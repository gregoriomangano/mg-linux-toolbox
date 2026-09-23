//! Detection and safe ownership decisions for advanced kernel controls.
use serde::{Deserialize, Serialize};
use std::{fs, process::Command};

pub const ID_AUTOGROUP: &str = "autogroup";
pub const ID_THP: &str = "thp";
pub const ID_KSM: &str = "ksm";
pub const ID_ZSWAP: &str = "zswap";
pub const ID_FSTRIM: &str = "fstrim";

const AUTOGROUP: &str = "/proc/sys/kernel/sched_autogroup_enabled";
const THP: &str = "/sys/kernel/mm/transparent_hugepage/enabled";
const KSM_RUN: &str = "/sys/kernel/mm/ksm/run";
const ZSWAP_ENABLED: &str = "/sys/module/zswap/parameters/enabled";
const SWAPS: &str = "/proc/swaps";
const MOUNTINFO: &str = "/proc/self/mountinfo";
const FSTRIM_TIMER: &str = "fstrim.timer";

#[derive(Default, Deserialize)]
struct BlockDevices {
    blockdevices: Vec<BlockDevice>,
}

#[derive(Default, Deserialize)]
struct BlockDevice {
    name: String,
    #[serde(rename = "type")]
    kind: String,
    rota: Option<bool>,
    #[serde(rename = "disc-max")]
    disc_max: Option<u64>,
    #[serde(default)]
    mountpoints: Vec<Option<String>>,
    #[serde(default)]
    children: Vec<BlockDevice>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AdvancedControl {
    pub id: String,
    pub enabled: bool,
    /// active, disabled, managed, system-managed, unavailable or incompatible.
    pub state: String,
    pub note: Option<String>,
    pub writable: bool,
    pub managed: bool,
    pub owner: Option<String>,
}

fn first_line(path: &str) -> Option<String> {
    fs::read_to_string(path)
        .ok()?
        .lines()
        .next()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn is_active(unit: &str) -> bool {
    Command::new("systemctl")
        .args(["is-active", "--quiet", unit])
        .status()
        .is_ok_and(|status| status.success())
}

fn fstrim_timer_enabled() -> bool {
    Command::new("systemctl")
        .args(["is-enabled", "--quiet", FSTRIM_TIMER])
        .status()
        .is_ok_and(|status| status.success())
}

fn fstrim_timer_active() -> bool {
    Command::new("systemctl")
        .args(["is-active", "--quiet", FSTRIM_TIMER])
        .status()
        .is_ok_and(|status| status.success())
}

fn fstrim_timer_present() -> bool {
    Command::new("systemctl")
        .args(["show", "--property=LoadState", "--value", FSTRIM_TIMER])
        .output()
        .is_ok_and(|output| output.status.success() && output.stdout == b"loaded\n")
}

fn relevant_mount(path: &str) -> bool {
    path == "/" || path == "/home" || path == "/var" || path.starts_with("/home/")
}

fn continuous_discard() -> bool {
    fs::read_to_string(MOUNTINFO)
        .unwrap_or_default()
        .lines()
        .any(|line| {
            let Some((before, after)) = line.split_once(" - ") else {
                return false;
            };
            let fields = before.split_whitespace().collect::<Vec<_>>();
            let mountpoint = fields.get(4).copied().unwrap_or_default();
            let mount_options = fields.get(5).copied().unwrap_or_default();
            let super_options = after.split_whitespace().nth(2).unwrap_or_default();
            relevant_mount(mountpoint)
                && (mount_options.split(',').any(|option| option == "discard")
                    || super_options.split(',').any(|option| option == "discard"))
        })
}

fn storage_capabilities() -> Option<(bool, bool)> {
    fn inspect(devices: &[BlockDevice], has_ssd: &mut bool, has_discard: &mut bool) {
        for device in devices {
            let mounted = device
                .mountpoints
                .iter()
                .flatten()
                .any(|mountpoint| relevant_mount(mountpoint));
            if mounted && matches!(device.kind.as_str(), "disk" | "part" | "crypt" | "lvm") {
                *has_ssd |= device.rota == Some(false) || device.name.starts_with("nvme");
                *has_discard |= device.disc_max.unwrap_or(0) > 0;
            }
            inspect(&device.children, has_ssd, has_discard);
        }
    }

    let output = Command::new("lsblk")
        .args([
            "--json",
            "--bytes",
            "--output",
            "NAME,TYPE,ROTA,DISC-MAX,MOUNTPOINTS",
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let devices: BlockDevices = serde_json::from_slice(&output.stdout).ok()?;
    let (mut has_ssd, mut has_discard) = (false, false);
    inspect(&devices.blockdevices, &mut has_ssd, &mut has_discard);
    Some((has_ssd, has_discard))
}

fn swap_configuration() -> (bool, bool) {
    let mut any = false;
    let mut zram = false;
    for path in fs::read_to_string(SWAPS)
        .unwrap_or_default()
        .lines()
        .skip(1)
        .filter_map(|line| line.split_whitespace().next())
    {
        any = true;
        zram |= path.starts_with("/dev/zram");
    }
    (any, zram)
}

fn parse_thp_current(contents: &str) -> Option<String> {
    contents
        .split_whitespace()
        .find(|token| token.starts_with('[') && token.ends_with(']'))
        .map(|token| token.trim_matches(['[', ']']).to_owned())
}

fn unavailable(id: &str) -> AdvancedControl {
    AdvancedControl {
        id: id.into(),
        enabled: false,
        state: "unavailable".into(),
        note: Some("unavailable".into()),
        writable: false,
        managed: false,
        owner: None,
    }
}

fn normal(id: &str, enabled: bool, owner: Option<String>) -> AdvancedControl {
    let managed = owner.is_some();
    AdvancedControl {
        id: id.into(),
        enabled,
        state: if managed {
            "managed"
        } else if enabled {
            "active"
        } else {
            "disabled"
        }
        .into(),
        note: None,
        writable: !managed,
        managed,
        owner,
    }
}

fn detect_autogroup() -> AdvancedControl {
    match first_line(AUTOGROUP).as_deref() {
        Some("0") => normal(ID_AUTOGROUP, false, None),
        Some("1") => normal(ID_AUTOGROUP, true, None),
        _ => unavailable(ID_AUTOGROUP),
    }
}

fn detect_thp() -> AdvancedControl {
    match first_line(THP)
        .as_deref()
        .and_then(parse_thp_current)
        .as_deref()
    {
        Some("madvise") => normal(ID_THP, true, None),
        Some("never") => normal(ID_THP, false, None),
        // `always` is a distinct ABI state. Do not replace a distro's choice with madvise.
        Some("always") => AdvancedControl {
            id: ID_THP.into(),
            enabled: true,
            state: "system-managed".into(),
            note: Some("thp-always".into()),
            writable: false,
            managed: true,
            owner: Some("system".into()),
        },
        _ => unavailable(ID_THP),
    }
}

fn detect_ksm(ksmtuned: bool) -> AdvancedControl {
    let owner = ksmtuned.then(|| "ksmtuned".to_string());
    match first_line(KSM_RUN).as_deref() {
        Some("0") => normal(ID_KSM, false, owner),
        Some("1") => normal(ID_KSM, true, owner),
        // Kernel ABI: 2 unmerges pages then stops. This is not an ON state.
        Some("2") => AdvancedControl {
            id: ID_KSM.into(),
            enabled: false,
            state: "incompatible".into(),
            note: Some("ksm-unmerge".into()),
            writable: false,
            managed: false,
            owner: None,
        },
        _ => unavailable(ID_KSM),
    }
}

fn detect_zswap() -> AdvancedControl {
    let (has_swap, has_zram_swap) = swap_configuration();
    if has_zram_swap {
        return AdvancedControl {
            id: ID_ZSWAP.into(),
            enabled: false,
            state: "incompatible".into(),
            note: Some("zswap-zram".into()),
            writable: false,
            managed: false,
            owner: None,
        };
    }
    if !has_swap {
        return AdvancedControl {
            id: ID_ZSWAP.into(),
            enabled: false,
            state: "incompatible".into(),
            note: Some("zswap-no-swap".into()),
            writable: false,
            managed: false,
            owner: None,
        };
    }
    match first_line(ZSWAP_ENABLED).as_deref() {
        Some("Y" | "y" | "1") => normal(ID_ZSWAP, true, None),
        Some("N" | "n" | "0") => normal(ID_ZSWAP, false, None),
        _ => unavailable(ID_ZSWAP),
    }
}

fn detect_fstrim() -> AdvancedControl {
    if !fstrim_timer_present() {
        return unavailable(ID_FSTRIM);
    }
    let enabled = fstrim_timer_enabled();
    let active = fstrim_timer_active();
    if continuous_discard() {
        return AdvancedControl {
            id: ID_FSTRIM.into(),
            enabled,
            state: "system-managed".into(),
            note: Some("fstrim-continuous-discard".into()),
            writable: false,
            managed: true,
            owner: Some("mount-options".into()),
        };
    }
    match storage_capabilities() {
        Some((has_ssd, has_discard)) if !has_ssd || !has_discard => AdvancedControl {
            id: ID_FSTRIM.into(),
            enabled,
            state: "incompatible".into(),
            note: Some("fstrim-not-needed".into()),
            writable: false,
            managed: false,
            owner: None,
        },
        Some(_) => AdvancedControl {
            id: ID_FSTRIM.into(),
            enabled,
            state: if enabled && active {
                "active"
            } else {
                "recommended"
            }
            .into(),
            note: (!enabled || !active).then(|| "fstrim-recommended".into()),
            writable: true,
            managed: false,
            owner: None,
        },
        None => unavailable(ID_FSTRIM),
    }
}

/// Detection is called only when the performance page is opened. ksmtuned is the only
/// service whose active state is itself evidence that it owns this exact knob.
pub fn detect_all() -> Vec<AdvancedControl> {
    let ksmtuned = is_active("ksmtuned.service");
    vec![
        detect_autogroup(),
        detect_thp(),
        detect_ksm(ksmtuned),
        detect_zswap(),
        detect_fstrim(),
    ]
}

fn bool_arg(enabled: bool) -> String {
    if enabled { "1" } else { "0" }.into()
}

pub fn set_control(id: &str, enabled: bool) -> Result<Vec<AdvancedControl>, String> {
    let controls = detect_all();
    let control = controls
        .iter()
        .find(|control| control.id == id)
        .ok_or("control_unavailable")?;
    if !control.writable {
        return Err("control_managed".into());
    }
    let args: Vec<String> = match id {
        ID_AUTOGROUP => vec!["desktop-autogroup-set".into(), bool_arg(enabled)],
        ID_THP => vec![
            "thp-set".into(),
            if enabled { "madvise" } else { "never" }.into(),
        ],
        ID_KSM => vec!["ksm-set".into(), bool_arg(enabled)],
        ID_ZSWAP => vec!["zswap-set".into(), bool_arg(enabled)],
        ID_FSTRIM => vec!["fstrim-timer-set".into(), bool_arg(enabled)],
        _ => return Err("invalid_control".into()),
    };
    crate::privileged_helper_args(&args)?;
    Ok(detect_all())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn thp_parser_preserves_the_kernel_selected_mode() {
        assert_eq!(
            parse_thp_current("always [madvise] never"),
            Some("madvise".into())
        );
        assert_eq!(
            parse_thp_current("[always] madvise never"),
            Some("always".into())
        );
    }
}
