//! Distribution detection from `/etc/os-release`, plus the family/variant
//! resolution the Programs page needs to pick the right package mappings.
//!
//! The model is "specific distro -> specific override -> family fallback":
//! `ID` and `ID_LIKE` are both honoured, a known derivative (CachyOS,
//! Nobara, Mint, ...) is kept as a variant so a specific mapping can be used
//! when one exists, and the family (Arch/Fedora/Debian/Ubuntu/SUSE) is only
//! the fallback. Immutable/atomic systems (rpm-ostree, the Universal Blue
//! family, SteamOS) are recognised and reported as unsupported for package
//! installation, exactly as this iteration's scope requires.
use serde::Serialize;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DistroFamily {
    Arch,
    Fedora,
    Debian,
    Ubuntu,
    Suse,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct OsRelease {
    pub id: String,
    pub id_like: Vec<String>,
    pub version_id: Option<String>,
    pub version_codename: Option<String>,
    pub ubuntu_codename: Option<String>,
    pub pretty_name: Option<String>,
}

pub fn parse_os_release(text: &str) -> OsRelease {
    let mut release = OsRelease::default();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let value = value
            .trim()
            .trim_matches('"')
            .trim_matches('\'')
            .to_string();
        match key.trim() {
            "ID" => release.id = value.to_ascii_lowercase(),
            "ID_LIKE" => {
                release.id_like = value
                    .split_whitespace()
                    .map(|v| v.to_ascii_lowercase())
                    .collect()
            }
            "VERSION_ID" => release.version_id = Some(value),
            "VERSION_CODENAME" => release.version_codename = Some(value),
            "UBUNTU_CODENAME" => release.ubuntu_codename = Some(value),
            "PRETTY_NAME" => release.pretty_name = Some(value),
            _ => {}
        }
    }
    release
}

/// `true` for systems that boot from an immutable/atomic image: package
/// installation is out of this iteration's scope there, and pretending
/// otherwise would risk writing to a read-only root.
pub fn is_immutable(release: &OsRelease, ostree_booted: bool, rpm_ostree_present: bool) -> bool {
    if ostree_booted && rpm_ostree_present {
        return true;
    }
    matches!(
        release.id.as_str(),
        "steamos" | "silverblue" | "kinoite" | "bazzite" | "bluefin" | "aurora" | "ublue-os"
    )
}

fn is_one_of(values: &[String], candidates: &[&str]) -> bool {
    values
        .iter()
        .any(|value| candidates.iter().any(|candidate| value == candidate))
}

/// Resolves one concrete distribution (variant name, lowercase `ID`-style)
/// from the release data, or `None` when the family itself is unknown.
pub fn variant_name(release: &OsRelease) -> Option<&'static str> {
    match release.id.as_str() {
        "cachyos" => Some("cachyos"),
        "endeavouros" => Some("endeavouros"),
        "manjaro" => Some("manjaro"),
        "garuda" => Some("garuda"),
        "nobara" => Some("nobara"),
        "linuxmint" => Some("linuxmint"),
        "pop" => Some("pop"),
        "zorin" => Some("zorin"),
        "elementary" => Some("elementary"),
        "kde-neon" | "neon" => Some("neon"),
        "opensuse-tumbleweed" => Some("opensuse-tumbleweed"),
        "opensuse-leap" => Some("opensuse-leap"),
        _ => None,
    }
}

pub fn family(release: &OsRelease) -> Option<DistroFamily> {
    // Direct IDs first, then ID_LIKE, so a derivative is classified by its
    // own declared family rather than by guessing from its name.
    let direct = match release.id.as_str() {
        "arch" | "archlinux" | "artix" | "cachyos" | "endeavouros" | "manjaro" | "garuda" => {
            Some(DistroFamily::Arch)
        }
        "fedora" | "nobara" | "rhel" | "centos" | "almalinux" | "rocky" => {
            Some(DistroFamily::Fedora)
        }
        "ubuntu" | "linuxmint" | "pop" | "zorin" | "elementary" | "kde-neon" | "neon" => {
            Some(DistroFamily::Ubuntu)
        }
        "debian" | "deepin" | "raspbian" => Some(DistroFamily::Debian),
        "opensuse" | "opensuse-tumbleweed" | "opensuse-leap" | "suse" | "sles" => {
            Some(DistroFamily::Suse)
        }
        _ => None,
    };
    if direct.is_some() {
        return direct;
    }
    let id_like = |candidates: &[&str]| is_one_of(&release.id_like, candidates);
    if id_like(&["arch", "archlinux"]) {
        return Some(DistroFamily::Arch);
    }
    if id_like(&["fedora", "rhel"]) {
        return Some(DistroFamily::Fedora);
    }
    if id_like(&["ubuntu"]) {
        return Some(DistroFamily::Ubuntu);
    }
    if id_like(&["debian"]) {
        return Some(DistroFamily::Debian);
    }
    if id_like(&["suse", "opensuse", "sles"]) {
        return Some(DistroFamily::Suse);
    }
    None
}

/// Human-readable label used by the UI: the vendor's own name when it is
/// known, otherwise the raw `ID`, never a made-up distribution name.
pub fn display_name(release: &OsRelease) -> String {
    release
        .pretty_name
        .clone()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| release.id.clone())
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Distribution {
    pub id: String,
    pub name: String,
    pub family: &'static str,
    pub variant: Option<String>,
    pub version: Option<String>,
    pub package_manager: Option<&'static str>,
    pub supported: bool,
    pub unsupported_reason: Option<&'static str>,
}

pub fn describe(
    release: &OsRelease,
    package_manager: Option<&'static str>,
    unsupported_reason: Option<&'static str>,
) -> Distribution {
    let family = family(release);
    let family_label = match family {
        Some(DistroFamily::Arch) => "arch",
        Some(DistroFamily::Fedora) => "fedora",
        Some(DistroFamily::Debian) => "debian",
        Some(DistroFamily::Ubuntu) => "ubuntu",
        Some(DistroFamily::Suse) => "suse",
        None => "unknown",
    };
    let supported = family.is_some() && package_manager.is_some() && unsupported_reason.is_none();
    Distribution {
        id: release.id.clone(),
        name: display_name(release),
        family: family_label,
        variant: variant_name(release).map(str::to_string),
        version: release
            .version_id
            .clone()
            .or_else(|| release.version_codename.clone()),
        package_manager,
        supported,
        unsupported_reason,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn os(id: &str, id_like: &str) -> OsRelease {
        parse_os_release(&format!("ID={id}\nID_LIKE=\"{id_like}\"\nVERSION_ID=1\n"))
    }

    #[test]
    fn parses_quoted_and_unquoted_os_release_fields() {
        let release = parse_os_release(
            "NAME=\"Ubuntu\"\nID=ubuntu\nID_LIKE=debian\nVERSION_ID=\"24.04\"\nVERSION_CODENAME=noble\nUBUNTU_CODENAME=noble\n",
        );
        assert_eq!(release.id, "ubuntu");
        assert_eq!(release.id_like, vec!["debian".to_string()]);
        assert_eq!(release.version_id.as_deref(), Some("24.04"));
        assert_eq!(release.version_codename.as_deref(), Some("noble"));
    }

    #[test]
    fn classifies_direct_ids_and_variants() {
        assert_eq!(family(&os("arch", "")), Some(DistroFamily::Arch));
        assert_eq!(family(&os("cachyos", "arch")), Some(DistroFamily::Arch));
        assert_eq!(variant_name(&os("cachyos", "arch")), Some("cachyos"));
        assert_eq!(family(&os("fedora", "")), Some(DistroFamily::Fedora));
        assert_eq!(family(&os("nobara", "fedora")), Some(DistroFamily::Fedora));
        assert_eq!(variant_name(&os("nobara", "fedora")), Some("nobara"));
        assert_eq!(family(&os("ubuntu", "debian")), Some(DistroFamily::Ubuntu));
        assert_eq!(
            family(&os("linuxmint", "ubuntu")),
            Some(DistroFamily::Ubuntu)
        );
        assert_eq!(variant_name(&os("linuxmint", "ubuntu")), Some("linuxmint"));
        assert_eq!(family(&os("debian", "")), Some(DistroFamily::Debian));
        assert_eq!(
            family(&os("opensuse-tumbleweed", "suse")),
            Some(DistroFamily::Suse)
        );
    }

    #[test]
    fn falls_back_to_id_like_for_unknown_derivatives() {
        assert_eq!(
            family(&os("ultramarine", "fedora rhel")),
            Some(DistroFamily::Fedora)
        );
        assert_eq!(
            family(&os("pop", "ubuntu debian")),
            Some(DistroFamily::Ubuntu)
        );
        assert_eq!(family(&os("archbang", "arch")), Some(DistroFamily::Arch));
        assert_eq!(family(&os("some-niche-distro", "")), None);
    }

    #[test]
    fn ubuntu_derivative_with_debian_in_id_like_is_still_ubuntu() {
        // Mint declares `ID_LIKE="ubuntu debian"`: the more specific family
        // must win, otherwise the Ubuntu-specific mappings would be skipped.
        let release = os("linuxmint", "ubuntu debian");
        assert_eq!(family(&release), Some(DistroFamily::Ubuntu));
    }

    #[test]
    fn immutable_systems_are_recognised() {
        assert!(is_immutable(&os("fedora", ""), true, true));
        assert!(is_immutable(&os("bazzite", "fedora"), false, false));
        assert!(is_immutable(&os("steamos", "arch"), false, false));
        assert!(!is_immutable(&os("fedora", ""), false, false));
        assert!(!is_immutable(&os("cachyos", "arch"), false, false));
    }

    #[test]
    fn describes_a_supported_and_an_unsupported_system() {
        let supported = describe(&os("cachyos", "arch"), Some("pacman"), None);
        assert!(supported.supported);
        assert_eq!(supported.family, "arch");
        assert_eq!(supported.variant.as_deref(), Some("cachyos"));

        let unsupported = describe(
            &os("bazzite", "fedora"),
            Some("rpm-ostree"),
            Some("immutable"),
        );
        assert!(!unsupported.supported);
        assert_eq!(unsupported.unsupported_reason, Some("immutable"));
    }
}
