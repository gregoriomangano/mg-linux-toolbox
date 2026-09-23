//! Local support nudge ("Ciao, sono Gregorio") state for the startup popup.
//!
//! Fully offline: a small JSON file in the user's app config directory, no
//! helper, no Polkit, no telemetry and no network of any kind. The whole
//! 10/30-day cadence is decided by comparing the current timestamp with the
//! stored `nextShowAt` at every launch -- there is no runtime timer, and a
//! clock moved backwards can never make the popup appear early.
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

pub const NUDGE_FILE: &str = "support-nudge.json";

/// First reminder distance, exactly as the product asks: 10 days.
const FIRST_REMIND_SECONDS: u64 = 10 * 24 * 60 * 60;
/// Second reminder distance: 30 days.
const SECOND_REMIND_SECONDS: u64 = 30 * 24 * 60 * 60;

/// `stage` is the number of automatic appearances already completed:
/// 0 = never shown, 1 = snoozed once, 2 = snoozed twice, 3 = completed.
/// `nextShowAt` is a unix timestamp; 0 means "due now". `disabled` is the
/// permanent opt-out and always wins over everything else.
pub const COMPLETED_STAGE: u8 = 3;

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct SupportNudge {
    pub stage: u8,
    pub next_show_at: u64,
    pub disabled: bool,
}

/// What the frontend is told: whether to show it, and which stage the
/// dialog is representing (0/1/2), so the right "remind me in N days"
/// label can be shown without the frontend ever computing dates itself.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SupportNudgeState {
    pub show: bool,
    pub stage: u8,
}

pub fn now_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

/// Pure visibility rule: shown only when still due, never completed, never
/// opted out, and only once the stored instant has really arrived. Because
/// this compares timestamps (instead of counting elapsed runs), moving the
/// clock backwards simply keeps the popup hidden until the deadline is
/// really reached again.
pub fn visible(nudge: &SupportNudge, now: u64) -> bool {
    !nudge.disabled && nudge.stage < COMPLETED_STAGE && now >= nudge.next_show_at
}

/// Pure state transition for one dismissal. `mode` is the only input the
/// frontend may send and is a closed set:
/// - `"remind"`: the checkbox was left checked -> snooze 10 then 30 days;
/// - `"close"`: closed with the checkbox unchecked, or the third and final
///   appearance -> completed, no further automatic popups;
/// - `"never"`: explicit permanent opt-out.
pub fn decide(nudge: &SupportNudge, mode: &str, now: u64) -> Result<SupportNudge, String> {
    let mut next = nudge.clone();
    match mode {
        "never" => next.disabled = true,
        "remind" => match nudge.stage {
            0 => {
                next.stage = 1;
                next.next_show_at = now + FIRST_REMIND_SECONDS;
            }
            1 => {
                next.stage = 2;
                next.next_show_at = now + SECOND_REMIND_SECONDS;
            }
            // The third appearance carries no checkbox: nothing left to
            // snooze, so a stray "remind" can only mean "done".
            _ => next.stage = COMPLETED_STAGE,
        },
        "close" => next.stage = COMPLETED_STAGE,
        _ => return Err("invalid_mode".into()),
    }
    Ok(next)
}

pub fn load(config_dir: &Path) -> SupportNudge {
    fs::read_to_string(config_dir.join(NUDGE_FILE))
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

fn save(config_dir: &Path, nudge: &SupportNudge) -> Result<(), String> {
    fs::create_dir_all(config_dir).map_err(|e| format!("nudge_dir_failed:{e}"))?;
    let text =
        serde_json::to_string_pretty(nudge).map_err(|e| format!("nudge_encode_failed:{e}"))?;
    fs::write(config_dir.join(NUDGE_FILE), text).map_err(|e| format!("nudge_write_failed:{e}"))
}

fn config_dir(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    use tauri::Manager;
    app.path()
        .app_config_dir()
        .map_err(|_| "nudge_config_unavailable".to_string())
}

#[tauri::command]
pub fn get_support_nudge(app: tauri::AppHandle) -> Result<SupportNudgeState, String> {
    get_support_nudge_with(&config_dir(&app)?, now_seconds())
}

#[tauri::command]
pub fn dismiss_support_nudge(
    app: tauri::AppHandle,
    mode: String,
) -> Result<SupportNudgeState, String> {
    dismiss_support_nudge_with(&config_dir(&app)?, &mode, now_seconds())
}

/// The file-backed variants, split out so the whole cadence is unit-tested
/// against a temporary directory and an injected clock.
fn get_support_nudge_with(dir: &Path, now: u64) -> Result<SupportNudgeState, String> {
    let nudge = load(dir);
    Ok(SupportNudgeState {
        show: visible(&nudge, now),
        stage: nudge.stage,
    })
}

fn dismiss_support_nudge_with(
    dir: &Path,
    mode: &str,
    now: u64,
) -> Result<SupportNudgeState, String> {
    let nudge = load(dir);
    let next = decide(&nudge, mode, now)?;
    save(dir, &next)?;
    Ok(SupportNudgeState {
        show: visible(&next, now),
        stage: next.stage,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const DAY: u64 = 24 * 60 * 60;

    fn temp_dir(name: &str) -> std::path::PathBuf {
        let unique = now_seconds() as u128 * 1_000_000_000 + std::process::id() as u128;
        std::env::temp_dir().join(format!("mg-nudge-{name}-{unique}"))
    }

    #[test]
    fn first_launch_is_due_and_an_invalid_mode_is_rejected() {
        let nudge = SupportNudge::default();
        assert!(visible(&nudge, 0));
        assert!(visible(&nudge, 1_000_000_000));
        assert_eq!(decide(&nudge, "nonsense", 0), Err("invalid_mode".into()));
        assert_eq!(decide(&nudge, "", 0), Err("invalid_mode".into()));
    }

    #[test]
    fn the_full_ten_thirty_completed_cadence_matches_the_spec() {
        let t0 = 1_000_000_000u64;

        // First appearance: checkbox left checked -> +10 days, stage 1.
        let after_first = decide(&SupportNudge::default(), "remind", t0).unwrap();
        assert_eq!(after_first.stage, 1);
        assert_eq!(after_first.next_show_at, t0 + 10 * DAY);
        assert!(!visible(&after_first, t0));
        assert!(!visible(&after_first, t0 + 10 * DAY - 1));
        assert!(visible(&after_first, t0 + 10 * DAY));

        // Second appearance: checkbox left checked -> +30 days, stage 2.
        let t1 = t0 + 10 * DAY;
        let after_second = decide(&after_first, "remind", t1).unwrap();
        assert_eq!(after_second.stage, 2);
        assert_eq!(after_second.next_show_at, t1 + 30 * DAY);
        assert!(!visible(&after_second, t1 + 30 * DAY - 1));
        assert!(visible(&after_second, t1 + 30 * DAY));

        // Third appearance: closing completes it for good.
        let t2 = t1 + 30 * DAY;
        let completed = decide(&after_second, "close", t2).unwrap();
        assert_eq!(completed.stage, COMPLETED_STAGE);
        assert!(!visible(&completed, t2));
        assert!(!visible(&completed, t2 + 1000 * DAY));
    }

    #[test]
    fn closing_without_the_checkbox_completes_without_ever_reminding_again() {
        let completed = decide(&SupportNudge::default(), "close", 500).unwrap();
        assert_eq!(completed.stage, COMPLETED_STAGE);
        assert!(!visible(&completed, u64::MAX));
    }

    #[test]
    fn never_disables_immediately_and_permanently_even_before_the_deadline() {
        let snoozed = decide(&SupportNudge::default(), "remind", 100).unwrap();
        let disabled = decide(&snoozed, "never", 101).unwrap();
        assert!(disabled.disabled);
        assert!(!visible(&disabled, 101));
        assert!(!visible(&disabled, u64::MAX));
        // A later snooze can never resurrect an explicit opt-out.
        let after = decide(&disabled, "remind", 200).unwrap();
        assert!(after.disabled);
        assert!(!visible(&after, u64::MAX));
    }

    #[test]
    fn a_clock_moved_backwards_never_shows_the_popup_early() {
        let t0 = 2_000_000_000u64;
        let snoozed = decide(&SupportNudge::default(), "remind", t0).unwrap();
        // The clock is now far behind the moment the snooze was saved.
        assert!(!visible(&snoozed, t0 - 5 * DAY));
        assert!(!visible(&snoozed, 0));
        // And only the real deadline brings it back.
        assert!(visible(&snoozed, t0 + 10 * DAY));
    }

    #[test]
    fn state_persists_across_loads_exactly_as_saved() {
        let dir = temp_dir("persist");
        let t0 = 1_700_000_000u64;
        assert!(get_support_nudge_with(&dir, t0).unwrap().show);

        let state = dismiss_support_nudge_with(&dir, "remind", t0).unwrap();
        assert!(!state.show);
        assert_eq!(state.stage, 1);

        // A fresh read (the next launch) sees the same stored deadline.
        let reloaded = load(&dir);
        assert_eq!(reloaded.stage, 1);
        assert_eq!(reloaded.next_show_at, t0 + 10 * DAY);
        assert!(!get_support_nudge_with(&dir, t0 + DAY).unwrap().show);
        assert!(get_support_nudge_with(&dir, t0 + 10 * DAY).unwrap().show);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_corrupted_file_falls_back_to_the_first_appearance_instead_of_failing() {
        let dir = temp_dir("corrupt");
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join(NUDGE_FILE), "{not json").unwrap();
        let loaded = load(&dir);
        assert_eq!(loaded, SupportNudge::default());
        assert!(get_support_nudge_with(&dir, 1).unwrap().show);
        let _ = fs::remove_dir_all(&dir);
    }
}
