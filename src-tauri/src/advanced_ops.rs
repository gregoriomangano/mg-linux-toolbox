//! Closed, validated write operations for the advanced controls.
//!
//! This module is compiled into both the application and the privileged helper. It
//! never accepts a path from the caller: every operation maps to one fixed kernel
//! attribute and one allow-listed value. The helper decides internally what to write,
//! so the frontend can only ever request one of these named operations.
use std::{fs, process::Command};

const AUTOGROUP: &str = "/proc/sys/kernel/sched_autogroup_enabled";
const THP: &str = "/sys/kernel/mm/transparent_hugepage/enabled";
const KSM_RUN: &str = "/sys/kernel/mm/ksm/run";
const ZSWAP_ENABLED: &str = "/sys/module/zswap/parameters/enabled";

pub fn is_operation(name: &str) -> bool {
    matches!(
        name,
        "desktop-autogroup-set" | "thp-set" | "ksm-set" | "zswap-set" | "fstrim-timer-set"
    )
}

fn fstrim_timer_command(value: &str) -> Result<Command, String> {
    let mut command = Command::new("systemctl");
    match value {
        "1" => command.args(["enable", "--now", "fstrim.timer"]),
        "0" => command.args(["disable", "--now", "fstrim.timer"]),
        _ => return Err("invalid_value".into()),
    };
    Ok(command)
}

fn set_fstrim_timer(value: &str) -> Result<(), String> {
    fstrim_timer_command(value)?
        .status()
        .map_err(|error| format!("systemctl_failed:{error}"))?
        .success()
        .then_some(())
        .ok_or_else(|| "systemctl_failed".into())
}

fn write_file(path: &str, value: &str) -> Result<(), String> {
    fs::write(path, value).map_err(|error| format!("write_failed:{error}"))?;
    fs::read_to_string(path)
        .map_err(|error| format!("post_write_read_failed:{error}"))
        .and_then(|current| {
            (!current.trim().is_empty())
                .then_some(())
                .ok_or_else(|| "post_write_invalid".into())
        })
}

fn write_allowed(path: &str, value: &str, allowed: &[&str]) -> Result<(), String> {
    if !allowed.contains(&value) {
        return Err("invalid_value".into());
    }
    write_file(path, value)
}

/// Applies one allow-listed operation. Only these argument shapes are accepted.
pub fn apply_operation(args: &[String]) -> Result<(), String> {
    match args {
        [action, value] if action == "desktop-autogroup-set" => {
            write_allowed(AUTOGROUP, value, &["0", "1"])
        }
        [action, value] if action == "thp-set" => write_allowed(THP, value, &["madvise", "never"]),
        [action, value] if action == "ksm-set" => write_allowed(KSM_RUN, value, &["0", "1"]),
        [action, value] if action == "zswap-set" => {
            let raw = match value.as_str() {
                "0" => "N",
                "1" => "Y",
                _ => return Err("invalid_value".into()),
            };
            write_file(ZSWAP_ENABLED, raw)
        }
        [action, value] if action == "fstrim-timer-set" => set_fstrim_timer(value),
        _ => Err("invalid_operation".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn op(parts: &[&str]) -> Result<(), String> {
        apply_operation(&parts.iter().map(|p| (*p).to_string()).collect::<Vec<_>>())
    }

    #[test]
    fn only_the_named_operations_are_recognised() {
        for name in [
            "desktop-autogroup-set",
            "thp-set",
            "ksm-set",
            "zswap-set",
            "fstrim-timer-set",
        ] {
            assert!(is_operation(name));
        }
        assert!(!is_operation("thp-set-arbitrary"));
        assert!(!is_operation("sh"));
    }

    #[test]
    fn invalid_values_are_rejected_before_any_write() {
        assert_eq!(op(&["thp-set", "always"]), Err("invalid_value".into()));
        assert_eq!(
            op(&["desktop-autogroup-set", "2"]),
            Err("invalid_value".into())
        );
        assert_eq!(op(&["ksm-set", "2"]), Err("invalid_value".into()));
        assert_eq!(op(&["ksm-set", "1; rm -rf /"]), Err("invalid_value".into()));
        assert_eq!(op(&["zswap-set", "yes"]), Err("invalid_value".into()));
        assert_eq!(
            op(&["fstrim-timer-set", "yes"]),
            Err("invalid_value".into())
        );
        assert_eq!(op(&["unknown-op"]), Err("invalid_operation".into()));
    }

    #[test]
    fn fstrim_uses_only_the_fixed_timer_argv() {
        let enabled = fstrim_timer_command("1").unwrap();
        assert_eq!(enabled.get_program(), "systemctl");
        assert_eq!(
            enabled.get_args().collect::<Vec<_>>(),
            ["enable", "--now", "fstrim.timer"]
        );

        let disabled = fstrim_timer_command("0").unwrap();
        assert_eq!(disabled.get_program(), "systemctl");
        assert_eq!(
            disabled.get_args().collect::<Vec<_>>(),
            ["disable", "--now", "fstrim.timer"]
        );
    }
}
