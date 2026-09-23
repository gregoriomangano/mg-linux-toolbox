use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::Command,
};

pub const HELPER_PATH: &str = "/usr/lib/mg-linux-toolbox/mg-linux-toolbox-performance-helper";
pub const STATE_DIR: &str = "/var/lib/mg-linux-toolbox/performance";
pub const PROFILE_PATH: &str = "/var/lib/mg-linux-toolbox/performance/profile.json";

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Profile {
    Performance,
    Balanced,
    Saving,
}

impl Profile {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "performance" => Some(Self::Performance),
            "balanced" => Some(Self::Balanced),
            "saving" => Some(Self::Saving),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PolicyState {
    pub name: String,
    pub driver: String,
    pub governor: String,
    pub available_governors: Vec<String>,
    pub epp: Option<String>,
    pub available_epp: Vec<String>,
    pub current_min: u64,
    pub current_max: u64,
    pub hardware_min: u64,
    pub hardware_max: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Change {
    pub policy: String,
    pub governor: Option<String>,
    pub epp: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Plan {
    pub profile: Profile,
    pub changes: Vec<Change>,
    pub available: bool,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Snapshot {
    version: u8,
    uid: u32,
    before: Vec<PolicyState>,
    applied: Vec<PolicyState>,
    profile: Profile,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct DesiredProfile {
    schema_version: u8,
    profile: Profile,
}

fn line(path: &Path) -> Result<String, String> {
    fs::read_to_string(path)
        .map(|v| v.trim().to_owned())
        .map_err(|e| format!("read_failed:{e}"))
}
fn number(path: &Path) -> Result<u64, String> {
    line(path)?.parse().map_err(|_| "invalid_number".into())
}
fn words(path: &Path) -> Vec<String> {
    line(path)
        .map(|v| v.split_whitespace().map(str::to_owned).collect())
        .unwrap_or_default()
}
fn policy_dirs(root: &Path) -> Result<Vec<PathBuf>, String> {
    let mut out = fs::read_dir(root)
        .map_err(|_| "cpufreq_unavailable")?
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .is_some_and(|n| n.to_string_lossy().starts_with("policy"))
        })
        .collect::<Vec<_>>();
    out.sort();
    if out.is_empty() {
        Err("cpufreq_unavailable".into())
    } else {
        Ok(out)
    }
}
pub fn scan(root: &Path) -> Result<Vec<PolicyState>, String> {
    policy_dirs(root)?
        .into_iter()
        .map(|p| {
            let name = p
                .file_name()
                .ok_or("cpufreq_unavailable")?
                .to_string_lossy()
                .into_owned();
            let current_min = number(&p.join("scaling_min_freq"))?;
            let current_max = number(&p.join("scaling_max_freq"))?;
            let hardware_min = number(&p.join("cpuinfo_min_freq"))?;
            let hardware_max = number(&p.join("cpuinfo_max_freq"))?;
            if current_min < hardware_min || current_max > hardware_max || current_min > current_max
            {
                return Err("unsafe_frequency_bounds".into());
            }
            Ok(PolicyState {
                name,
                driver: line(&p.join("scaling_driver"))?,
                governor: line(&p.join("scaling_governor"))?,
                available_governors: words(&p.join("scaling_available_governors")),
                epp: line(&p.join("energy_performance_preference")).ok(),
                available_epp: words(&p.join("energy_performance_available_preferences")),
                current_min,
                current_max,
                hardware_min,
                hardware_max,
            })
        })
        .collect()
}

fn select(available: &[String], candidates: &[&str]) -> Option<String> {
    candidates
        .iter()
        .find(|v| available.iter().any(|x| x == **v))
        .map(|v| (*v).to_owned())
}
pub fn plan(profile: Profile, policies: &[PolicyState], external_conflict: bool) -> Plan {
    if external_conflict {
        return Plan {
            profile,
            changes: vec![],
            available: false,
            reason: Some("external_manager".into()),
        };
    }
    let mut changes = Vec::new();
    for p in policies {
        let epp_driver = p.driver.contains("pstate") && !p.available_epp.is_empty();
        let (governor, epp) = if epp_driver {
            let e = match profile {
                Profile::Performance => select(&p.available_epp, &["performance"]),
                Profile::Balanced => select(
                    &p.available_epp,
                    &["balance_performance", "default", "balance_power"],
                ),
                Profile::Saving => select(&p.available_epp, &["power", "balance_power"]),
            };
            let g = select(&p.available_governors, &["powersave"]).filter(|v| v != &p.governor);
            (g, e.filter(|v| p.epp.as_ref() != Some(v)))
        } else {
            let candidates: &[&str] = match profile {
                Profile::Performance => &["performance"],
                Profile::Balanced => &["schedutil", "ondemand", "conservative", "powersave"],
                Profile::Saving => &["powersave", "conservative"],
            };
            (
                select(&p.available_governors, candidates).filter(|v| v != &p.governor),
                None,
            )
        };
        if governor.is_none() && epp.is_none() {
            continue;
        }
        changes.push(Change {
            policy: p.name.clone(),
            governor,
            epp,
        });
    }
    let supported = policies.iter().all(|p| {
        !p.available_governors.is_empty()
            && (p.driver.contains("pstate")
                || matches!(p.driver.as_str(), "acpi-cpufreq" | "cppc_cpufreq"))
    }) && policies.iter().all(|policy| {
        let unchanged = changes.iter().all(|change| change.policy != policy.name);
        let epp_capable = policy.driver.contains("pstate") && !policy.available_epp.is_empty();
        unchanged && !epp_capable
            || !unchanged
            || match profile {
                Profile::Performance => {
                    policy.governor == "performance" || policy.epp.as_deref() == Some("performance")
                }
                Profile::Balanced => {
                    ["schedutil", "ondemand", "conservative", "powersave"]
                        .contains(&policy.governor.as_str())
                        || ["balance_performance", "default", "balance_power"]
                            .contains(&policy.epp.as_deref().unwrap_or_default())
                }
                Profile::Saving => {
                    ["powersave", "conservative"].contains(&policy.governor.as_str())
                        || ["power", "balance_power"]
                            .contains(&policy.epp.as_deref().unwrap_or_default())
                }
            }
    });
    Plan {
        profile,
        changes,
        available: supported,
        reason: (!supported).then(|| "unsupported_driver".into()),
    }
}

pub fn active_external_manager() -> Option<String> {
    [
        ("power-profiles-daemon", "power-profiles-daemon.service"),
        ("tuned", "tuned.service"),
        ("tuned-ppd", "tuned-ppd.service"),
        ("TLP", "tlp.service"),
        ("system76-power", "system76-power.service"),
    ]
    .into_iter()
    .find_map(|(name, unit)| {
        Command::new("systemctl")
            .args(["is-active", "--quiet", unit])
            .status()
            .ok()
            .filter(|s| s.success())
            .map(|_| name.into())
    })
}
fn write_value(path: &Path, value: &str) -> Result<(), String> {
    fs::write(path, value).map_err(|e| format!("write_failed:{e}"))
}
fn apply_changes(root: &Path, changes: &[Change]) -> Result<(), String> {
    for c in changes {
        let base = root.join(&c.policy);
        if let Some(v) = &c.governor {
            write_value(&base.join("scaling_governor"), v)?
        }
        if let Some(v) = &c.epp {
            write_value(&base.join("energy_performance_preference"), v)?
        }
    }
    Ok(())
}
fn state_path(uid: u32) -> PathBuf {
    Path::new(STATE_DIR).join(format!("{uid}.json"))
}
fn atomic_snapshot(snapshot: &Snapshot) -> Result<(), String> {
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    fs::create_dir_all(STATE_DIR).map_err(|e| e.to_string())?;
    fs::set_permissions(STATE_DIR, fs::Permissions::from_mode(0o711)).map_err(|e| e.to_string())?;
    let target = state_path(snapshot.uid);
    let temp = target.with_extension("tmp");
    let bytes = serde_json::to_vec_pretty(snapshot).map_err(|e| e.to_string())?;
    let mut f = fs::OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .mode(0o600)
        .open(&temp)
        .map_err(|e| e.to_string())?;
    f.write_all(&bytes)
        .and_then(|_| f.sync_all())
        .map_err(|e| e.to_string())?;
    fs::rename(temp, target).map_err(|e| e.to_string())
}
fn atomic_desired(profile: Profile) -> Result<(), String> {
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    fs::create_dir_all(STATE_DIR).map_err(|e| e.to_string())?;
    fs::set_permissions(STATE_DIR, fs::Permissions::from_mode(0o711)).map_err(|e| e.to_string())?;
    let target = Path::new(PROFILE_PATH);
    let temp = target.with_extension("tmp");
    let bytes = serde_json::to_vec(&DesiredProfile {
        schema_version: 1,
        profile,
    })
    .map_err(|e| e.to_string())?;
    let mut f = fs::OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .mode(0o644)
        .open(&temp)
        .map_err(|e| e.to_string())?;
    f.write_all(&bytes)
        .and_then(|_| f.sync_all())
        .map_err(|e| e.to_string())?;
    fs::rename(temp, target).map_err(|e| e.to_string())
}
fn read_desired() -> Result<Profile, String> {
    let data = fs::read(PROFILE_PATH).map_err(|_| "persistent_profile_missing".to_string())?;
    let desired: DesiredProfile =
        serde_json::from_slice(&data).map_err(|_| "persistent_profile_corrupt".to_string())?;
    (desired.schema_version == 1)
        .then_some(desired.profile)
        .ok_or_else(|| "persistent_profile_incompatible".into())
}
fn clear_desired() -> Result<(), String> {
    match fs::remove_file(PROFILE_PATH) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}
fn rollback_to(root: &Path, before: &[PolicyState]) -> Result<(), String> {
    let rollback = before
        .iter()
        .map(|p| Change {
            policy: p.name.clone(),
            governor: Some(p.governor.clone()),
            epp: p.epp.clone(),
        })
        .collect::<Vec<_>>();
    apply_changes(root, &rollback).map_err(|_| "rollback_failed".into())
}
pub fn apply(profile: Profile, uid: u32) -> Result<(), String> {
    let root = Path::new("/sys/devices/system/cpu/cpufreq");
    let before = scan(root)?;
    let plan = plan(profile, &before, active_external_manager().is_some());
    if !plan.available {
        return Err(plan.reason.unwrap_or("unavailable".into()));
    }
    let mut expected = before.clone();
    for change in &plan.changes {
        if let Some(policy) = expected.iter_mut().find(|p| p.name == change.policy) {
            if let Some(value) = &change.governor {
                policy.governor = value.clone()
            }
            if let Some(value) = &change.epp {
                policy.epp = Some(value.clone())
            }
        }
    }
    atomic_snapshot(&Snapshot {
        version: 1,
        uid,
        before: before.clone(),
        applied: expected,
        profile,
    })?;
    if let Err(error) = apply_changes(root, &plan.changes) {
        if rollback_to(root, &before).is_err() {
            return Err("rollback_failed".into());
        }
        let _ = fs::remove_file(state_path(uid));
        return Err(error);
    }
    let after = scan(root)?;
    if plan.changes.iter().any(|c| {
        after.iter().find(|p| p.name == c.policy).is_none_or(|p| {
            c.governor.as_ref().is_some_and(|v| &p.governor != v)
                || c.epp.as_ref().is_some_and(|v| p.epp.as_ref() != Some(v))
        })
    }) {
        if rollback_to(root, &before).is_err() {
            return Err("rollback_failed".into());
        }
        let _ = fs::remove_file(state_path(uid));
        return Err("verification_failed".into());
    }
    if let Err(error) = atomic_desired(profile) {
        let rollback = rollback_to(root, &before);
        let _ = fs::remove_file(state_path(uid));
        return if rollback.is_err() {
            Err("rollback_failed".into())
        } else {
            Err(format!("persistent_profile_failed:{error}"))
        };
    }
    atomic_snapshot(&Snapshot {
        version: 1,
        uid,
        before,
        applied: after,
        profile,
    })
}
pub fn apply_persisted() -> Result<(), String> {
    let profile = read_desired()?;
    let root = Path::new("/sys/devices/system/cpu/cpufreq");
    let before = scan(root)?;
    let plan = plan(profile, &before, active_external_manager().is_some());
    if !plan.available {
        return Err(plan.reason.unwrap_or("unavailable".into()));
    }
    if let Err(error) = apply_changes(root, &plan.changes) {
        if rollback_to(root, &before).is_err() {
            return Err("rollback_failed".into());
        }
        return Err(error);
    }
    let after = scan(root)?;
    let verified = plan.changes.iter().all(|c| {
        after.iter().find(|p| p.name == c.policy).is_some_and(|p| {
            c.governor.as_ref().is_none_or(|v| &p.governor == v)
                && c.epp.as_ref().is_none_or(|v| p.epp.as_ref() == Some(v))
        })
    });
    if !verified {
        if rollback_to(root, &before).is_err() {
            return Err("rollback_failed".into());
        }
        return Err("verification_failed".into());
    }
    Ok(())
}
pub fn restore(uid: u32) -> Result<(), String> {
    let data = fs::read(state_path(uid)).map_err(|_| "snapshot_missing")?;
    let snapshot: Snapshot = serde_json::from_slice(&data).map_err(|_| "snapshot_corrupt")?;
    if snapshot.version != 1 || snapshot.uid != uid {
        return Err("snapshot_incompatible".into());
    }
    let root = Path::new("/sys/devices/system/cpu/cpufreq");
    if scan(root)? != snapshot.applied {
        return Err("external_change_detected".into());
    }
    let changes = snapshot
        .before
        .iter()
        .map(|p| Change {
            policy: p.name.clone(),
            governor: Some(p.governor.clone()),
            epp: p.epp.clone(),
        })
        .collect::<Vec<_>>();
    apply_changes(root, &changes)?;
    if scan(root)? != snapshot.before {
        return Err("rollback_verification_failed".into());
    }
    fs::remove_file(state_path(uid)).map_err(|e| e.to_string())?;
    clear_desired()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn persistent_profile_schema_is_allowlisted_and_versioned() {
        let encoded = serde_json::to_string(&DesiredProfile {
            schema_version: 1,
            profile: Profile::Balanced,
        })
        .unwrap();
        let decoded: DesiredProfile = serde_json::from_str(&encoded).unwrap();
        assert_eq!(decoded.profile, Profile::Balanced);
        assert!(serde_json::from_str::<DesiredProfile>(
            r#"{"schema_version":2,"profile":"balanced"}"#
        )
        .is_ok());
    }
    fn p(driver: &str, gov: &str, govs: &[&str], epp: Option<&str>, epps: &[&str]) -> PolicyState {
        PolicyState {
            name: "policy0".into(),
            driver: driver.into(),
            governor: gov.into(),
            available_governors: govs.iter().map(|v| (*v).into()).collect(),
            epp: epp.map(Into::into),
            available_epp: epps.iter().map(|v| (*v).into()).collect(),
            current_min: 100,
            current_max: 200,
            hardware_min: 100,
            hardware_max: 200,
        }
    }
    #[test]
    fn amd_uses_epp_without_inventing_frequency_limits() {
        let x = p(
            "amd-pstate-epp",
            "powersave",
            &["performance", "powersave"],
            Some("performance"),
            &["performance", "balance_performance", "power"],
        );
        let b = plan(Profile::Balanced, &[x], false);
        assert_eq!(b.changes[0].epp.as_deref(), Some("balance_performance"));
        assert!(b.changes[0].governor.is_none())
    }
    #[test]
    fn intel_uses_available_epp() {
        let x = p(
            "intel_pstate",
            "powersave",
            &["performance", "powersave"],
            Some("balance_power"),
            &["performance", "balance_performance", "balance_power"],
        );
        assert_eq!(
            plan(Profile::Performance, &[x], false).changes[0]
                .epp
                .as_deref(),
            Some("performance")
        )
    }
    #[test]
    fn generic_uses_governors() {
        let x = p(
            "acpi-cpufreq",
            "ondemand",
            &["performance", "ondemand", "powersave"],
            None,
            &[],
        );
        assert_eq!(
            plan(Profile::Saving, &[x], false).changes[0]
                .governor
                .as_deref(),
            Some("powersave")
        )
    }
    #[test]
    fn external_manager_blocks_plan() {
        let x = p(
            "amd-pstate-epp",
            "powersave",
            &["powersave"],
            Some("performance"),
            &["performance", "power"],
        );
        assert!(!plan(Profile::Saving, &[x], true).available)
    }
    #[test]
    fn unknown_profile_is_rejected() {
        assert_eq!(Profile::parse("/sys/x"), None)
    }
}
