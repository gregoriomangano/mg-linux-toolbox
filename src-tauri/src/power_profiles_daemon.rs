//! Capability-based client for `power-profiles-daemon` (PPD) over the system D-Bus.
//!
//! PPD is detected and driven purely through its D-Bus API (`net.hadess.PowerProfiles`),
//! never by matching a distro name or a systemd unit string, so this works on any
//! distribution that ships a running PPD. Profile switches are written directly to the
//! `ActiveProfile` property: PPD's own polkit policy already allows an active session
//! user to change it without a prompt, so no pkexec/privileged helper is involved here.
use serde::Serialize;
use std::collections::HashMap;
use zbus::{
    blocking::{Connection, Proxy},
    zvariant::OwnedValue,
};

const BUS_NAME: &str = "net.hadess.PowerProfiles";
const OBJECT_PATH: &str = "/net/hadess/PowerProfiles";
const INTERFACE: &str = "net.hadess.PowerProfiles";

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PpdProfile {
    pub name: String,
    pub cpu_driver: Option<String>,
    pub platform_driver: Option<String>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PpdStatus {
    pub active_profile: String,
    pub profiles: Vec<PpdProfile>,
    pub performance_degraded: Option<String>,
}

fn connect() -> Result<Connection, String> {
    Connection::system().map_err(|_| "ppd_unavailable".to_string())
}

fn proxy(connection: &Connection) -> Result<Proxy<'_>, String> {
    Proxy::new(connection, BUS_NAME, OBJECT_PATH, INTERFACE)
        .map_err(|_| "ppd_unavailable".to_string())
}

fn string_field(map: &HashMap<String, OwnedValue>, key: &str) -> Option<String> {
    String::try_from(map.get(key)?.clone()).ok()
}

/// Reads the live profile list and active profile straight from PPD. Returns `Err` when
/// PPD is not running, not reachable on the system bus, or does not implement the
/// expected interface -- callers treat any error here as "PPD is not a usable backend".
pub fn status() -> Result<PpdStatus, String> {
    let connection = connect()?;
    let p = proxy(&connection)?;
    let active_profile: String = p
        .get_property("ActiveProfile")
        .map_err(|_| "ppd_unavailable".to_string())?;
    let raw_profiles: Vec<HashMap<String, OwnedValue>> = p
        .get_property("Profiles")
        .map_err(|_| "ppd_unavailable".to_string())?;
    let profiles = raw_profiles
        .iter()
        .filter_map(|entry| {
            Some(PpdProfile {
                name: string_field(entry, "Profile")?,
                cpu_driver: string_field(entry, "CpuDriver"),
                platform_driver: string_field(entry, "PlatformDriver"),
            })
        })
        .collect();
    let performance_degraded: String = p.get_property("PerformanceDegraded").unwrap_or_default();
    Ok(PpdStatus {
        active_profile,
        profiles,
        performance_degraded: (!performance_degraded.is_empty()).then_some(performance_degraded),
    })
}

/// Capability check: is a usable PPD available on this machine right now?
pub fn detect() -> bool {
    status().is_ok()
}

/// Sets `ActiveProfile` directly over D-Bus. No privileged helper is involved: PPD is
/// already the privileged component here, and its own polkit action
/// (`net.hadess.PowerProfilesDaemon.switch-profile`) permits the active session user to
/// switch profiles without authentication, the same way GNOME Settings does it.
pub fn set_active_profile(name: &str) -> Result<(), String> {
    let connection = connect()?;
    let p = proxy(&connection)?;
    p.set_property("ActiveProfile", name)
        .map_err(|error| format!("ppd_set_failed:{error}"))
}

/// Maps our user-facing profile intent onto the technical PPD profile name. PPD's
/// power-saving profile is called "power-saver", everything else matches our own names.
pub fn profile_name(profile: crate::performance_profiles::Profile) -> &'static str {
    match profile {
        crate::performance_profiles::Profile::Performance => "performance",
        crate::performance_profiles::Profile::Balanced => "balanced",
        crate::performance_profiles::Profile::Saving => "power-saver",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profile_name_maps_saving_to_ppd_power_saver() {
        assert_eq!(
            profile_name(crate::performance_profiles::Profile::Saving),
            "power-saver"
        );
        assert_eq!(
            profile_name(crate::performance_profiles::Profile::Performance),
            "performance"
        );
        assert_eq!(
            profile_name(crate::performance_profiles::Profile::Balanced),
            "balanced"
        );
    }

    #[test]
    fn string_field_reads_declared_keys_and_ignores_missing_ones() {
        let mut map = HashMap::new();
        map.insert(
            "Profile".to_string(),
            OwnedValue::try_from(zbus::zvariant::Value::from("power-saver")).unwrap(),
        );
        assert_eq!(
            string_field(&map, "Profile"),
            Some("power-saver".to_string())
        );
        assert_eq!(string_field(&map, "Missing"), None);
    }

    #[test]
    #[ignore = "requires a real power-profiles-daemon on the system bus"]
    fn status_round_trip_against_a_real_daemon() {
        let status = status().expect("power-profiles-daemon should be reachable");
        assert!(!status.profiles.is_empty());
    }

    #[test]
    #[ignore = "mutates the live system power profile; run manually, never in CI"]
    fn set_active_profile_round_trip_leaves_the_machine_as_it_found_it() {
        let before = status().expect("power-profiles-daemon should be reachable");
        let other = before
            .profiles
            .iter()
            .map(|p| p.name.as_str())
            .find(|name| *name != before.active_profile)
            .expect("a real machine should expose at least two profiles to switch between");

        set_active_profile(other).expect("switching to a different real profile should succeed");
        assert_eq!(status().unwrap().active_profile, other);

        set_active_profile(&before.active_profile)
            .expect("restoring the original profile should succeed");
        assert_eq!(status().unwrap().active_profile, before.active_profile);
    }
}
