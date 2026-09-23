use crate::power_profiles_daemon;
use serde::Serialize;
use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
    time::{Duration, Instant},
};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PerformanceSnapshot {
    pub governor: Option<ChoiceCapability>,
    pub epp: Option<ChoiceCapability>,
    pub frequency_limits: Option<FrequencyCapability>,
    pub platform_profile: Option<ChoiceCapability>,
    pub cpu_driver: Option<String>,
    pub power_manager: PowerManager,
    pub cpu_psi: Option<PsiInfo>,
    pub io_psi: Option<PsiInfo>,
    pub thermal: Option<ThermalInfo>,
    pub current_frequency: Option<CurrentFrequency>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ChoiceCapability {
    pub current: String,
    pub available: Vec<String>,
    pub policy_count: usize,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FrequencyCapability {
    pub minimum_mhz: u64,
    pub maximum_mhz: u64,
    pub policy_count: usize,
}
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PowerManager {
    pub name: Option<String>,
    pub detected: bool,
    /// True when the detected manager is power-profiles-daemon and it is actually
    /// reachable and usable over D-Bus, meaning M.G Linux Toolbox drives it directly
    /// instead of stepping back from it.
    pub integrated: bool,
    /// power-profiles-daemon's `PerformanceDegraded` reason, when it reports one.
    /// Empty/absent is normalized to `None`.
    pub degraded: Option<String>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PsiInfo {
    pub some_avg10: f64,
    pub full_avg10: Option<f64>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ThermalInfo {
    pub temperature_celsius: Option<f64>,
    pub throttle_events: Option<u64>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CurrentFrequency {
    pub minimum_mhz: u64,
    pub maximum_mhz: u64,
    pub average_mhz: u64,
    pub policy_count: usize,
}

fn first_line(path: impl AsRef<Path>) -> Option<String> {
    fs::read_to_string(path)
        .ok()?
        .lines()
        .next()
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .map(str::to_owned)
}
fn policy_directories() -> Vec<PathBuf> {
    let Ok(entries) = fs::read_dir("/sys/devices/system/cpu/cpufreq") else {
        return Vec::new();
    };
    let mut policies = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name()
                .is_some_and(|name| name.to_string_lossy().starts_with("policy"))
        })
        .collect::<Vec<_>>();
    policies.sort();
    policies
}
fn consolidate_values(values: Vec<String>) -> Option<String> {
    let values = values.into_iter().collect::<BTreeSet<_>>();
    match values.len() {
        0 => None,
        1 => values.into_iter().next(),
        _ => Some("<multiple>".into()),
    }
}
fn khz_to_mhz(raw: &str) -> Option<u64> {
    raw.trim().parse::<u64>().ok().map(|value| value / 1000)
}
fn collect_choice(
    file: &str,
    available_file: &str,
    policies: &[PathBuf],
) -> Option<ChoiceCapability> {
    let values = policies
        .iter()
        .filter_map(|policy| first_line(policy.join(file)))
        .collect::<Vec<_>>();
    let current = consolidate_values(values)?;
    let available = policies
        .iter()
        .find_map(|policy| first_line(policy.join(available_file)))
        .map(|line| line.split_whitespace().map(str::to_owned).collect())
        .unwrap_or_default();
    Some(ChoiceCapability {
        current,
        available,
        policy_count: policies.len(),
    })
}
fn collect_frequency_limits(policies: &[PathBuf]) -> Option<FrequencyCapability> {
    let mins = policies
        .iter()
        .filter_map(|policy| {
            first_line(policy.join("scaling_min_freq")).and_then(|value| khz_to_mhz(&value))
        })
        .collect::<Vec<_>>();
    let maxes = policies
        .iter()
        .filter_map(|policy| {
            first_line(policy.join("scaling_max_freq")).and_then(|value| khz_to_mhz(&value))
        })
        .collect::<Vec<_>>();
    Some(FrequencyCapability {
        minimum_mhz: *mins.iter().min()?,
        maximum_mhz: *maxes.iter().max()?,
        policy_count: policies.len(),
    })
}
fn collect_current_frequency(policies: &[PathBuf]) -> Option<CurrentFrequency> {
    let values = policies
        .iter()
        .filter_map(|policy| {
            first_line(policy.join("scaling_cur_freq")).and_then(|v| khz_to_mhz(&v))
        })
        .collect::<Vec<_>>();
    if values.is_empty() {
        return None;
    }
    let total = values
        .iter()
        .fold(0_u64, |sum, value| sum.saturating_add(*value));
    Some(CurrentFrequency {
        minimum_mhz: *values.iter().min()?,
        maximum_mhz: *values.iter().max()?,
        average_mhz: total / values.len() as u64,
        policy_count: values.len(),
    })
}
fn parse_psi(input: &str) -> Option<PsiInfo> {
    let mut some = None;
    let mut full = None;
    for line in input.lines() {
        let mut fields = line.split_whitespace();
        let kind = fields.next()?;
        let avg10 = fields.find_map(|field| field.strip_prefix("avg10=")?.parse::<f64>().ok())?;
        match kind {
            "some" => some = Some(avg10),
            "full" => full = Some(avg10),
            _ => {}
        }
    }
    Some(PsiInfo {
        some_avg10: some?,
        full_avg10: full,
    })
}
fn power_manager() -> PowerManager {
    static CACHE: OnceLock<Mutex<Option<(Instant, PowerManager)>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(None));
    if let Ok(guard) = cache.lock() {
        if let Some((checked_at, manager)) = &*guard {
            if checked_at.elapsed() < Duration::from_secs(30) {
                return manager.clone();
            }
        }
    }
    // power-profiles-daemon is detected by capability (a real, usable D-Bus endpoint),
    // not by matching a distro or systemd unit name, so this also works on systems where
    // PPD is reachable without being systemd-managed. Other managers have no supported
    // API we integrate with, so they still fall back to a unit-presence check purely for
    // informational display.
    let mut result = if let Ok(ppd) = power_profiles_daemon::status() {
        PowerManager {
            name: Some("Power Profiles Daemon".into()),
            detected: true,
            integrated: true,
            degraded: ppd.performance_degraded.clone(),
        }
    } else {
        PowerManager {
            name: None,
            detected: false,
            integrated: false,
            degraded: None,
        }
    };
    if !result.detected {
        let managers = [
            ("TuneD", "tuned.service"),
            ("TuneD", "tuned-ppd.service"),
            ("TLP", "tlp.service"),
            ("System76 Power", "system76-power.service"),
        ];
        for (name, unit) in managers {
            if std::process::Command::new("systemctl")
                .args(["is-active", "--quiet", unit])
                .status()
                .is_ok_and(|s| s.success())
            {
                result = PowerManager {
                    name: Some(name.into()),
                    detected: true,
                    integrated: false,
                    degraded: None,
                };
                break;
            }
        }
    }
    if let Ok(mut guard) = cache.lock() {
        *guard = Some((Instant::now(), result.clone()));
    }
    result
}
fn throttle_events() -> Option<u64> {
    let entries = fs::read_dir("/sys/devices/system/cpu").ok()?;
    let mut found = false;
    let mut total = 0_u64;
    for cpu in entries.flatten().map(|entry| entry.path()).filter(|path| {
        path.file_name()
            .is_some_and(|name| name.to_string_lossy().starts_with("cpu"))
    }) {
        let path = cpu.join("thermal_throttle");
        let Ok(files) = fs::read_dir(path) else {
            continue;
        };
        for file in files.flatten() {
            if let Some(value) = first_line(file.path()).and_then(|v| v.parse::<u64>().ok()) {
                found = true;
                total = total.saturating_add(value);
            }
        }
    }
    found.then_some(total)
}
pub fn collect_performance_snapshot() -> PerformanceSnapshot {
    let policies = policy_directories();
    let driver = consolidate_values(
        policies
            .iter()
            .filter_map(|policy| first_line(policy.join("scaling_driver")))
            .collect(),
    );
    let temperature_celsius = crate::hardware::cpu_temperature();
    let throttle_events = throttle_events();
    PerformanceSnapshot {
        governor: collect_choice("scaling_governor", "scaling_available_governors", &policies),
        epp: collect_choice(
            "energy_performance_preference",
            "energy_performance_available_preferences",
            &policies,
        ),
        frequency_limits: collect_frequency_limits(&policies),
        platform_profile: first_line("/sys/firmware/acpi/platform_profile").map(|current| {
            ChoiceCapability {
                available: first_line("/sys/firmware/acpi/platform_profile_choices")
                    .map(|line| {
                        line.split_whitespace()
                            .map(|value| value.trim_matches(['[', ']']).to_owned())
                            .collect()
                    })
                    .unwrap_or_default(),
                current,
                policy_count: 1,
            }
        }),
        cpu_driver: driver,
        power_manager: power_manager(),
        cpu_psi: psi("/proc/pressure/cpu"),
        io_psi: psi("/proc/pressure/io"),
        thermal: (temperature_celsius.is_some() || throttle_events.is_some()).then_some(
            ThermalInfo {
                temperature_celsius,
                throttle_events,
            },
        ),
        current_frequency: collect_current_frequency(&policies),
    }
}

/// Reads one kernel PSI file (`/proc/pressure/*`), returning `None` when the
/// interface is unavailable on this kernel.
fn psi(path: &str) -> Option<PsiInfo> {
    fs::read_to_string(path)
        .ok()
        .and_then(|input| parse_psi(&input))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn parses_psi_some_pressure_from_kernel_format() {
        let parsed=parse_psi("some avg10=1.25 avg60=0.75 avg300=0.40 total=123\nfull avg10=0.20 avg60=0.10 avg300=0.05 total=12\n").expect("PSI data should parse");
        assert_eq!(parsed.some_avg10, 1.25);
        assert_eq!(parsed.full_avg10, Some(0.20));
    }
    #[test]
    fn consolidates_matching_policy_values_and_marks_mixed_values() {
        assert_eq!(
            consolidate_values(vec!["powersave".into(), "powersave".into()]),
            Some("powersave".into())
        );
        assert_eq!(
            consolidate_values(vec!["powersave".into(), "performance".into()]),
            Some("<multiple>".into())
        );
    }
    #[test]
    fn parses_cpu_frequency_khz_as_mhz_without_guessing_units() {
        assert_eq!(khz_to_mhz("1746460"), Some(1746));
        assert_eq!(khz_to_mhz("not-a-frequency"), None);
    }
}
