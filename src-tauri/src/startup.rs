//! User-level startup preferences for the resident application itself:
//! whether M.G starts at login, and whether those automatic starts stay in
//! the system area instead of opening the window.
//!
//! The "start at login" registration is delegated entirely to the official
//! Tauri autostart plugin, which on Linux writes the standard XDG
//! `~/.config/autostart` entry for the current binary -- no `~/.bashrc`,
//! `.profile`, cron or systemd unit is ever touched, and no root privilege
//! is ever requested. The "start hidden" preference is a plain user JSON
//! file next to the other M.G configuration.
use serde::{Deserialize, Serialize};
use std::{fs, path::Path};
use tauri::Manager;

/// Marker argument the autostart entry passes to the application, so a
/// login launch can be told apart from a manual one. A manual launch never
/// carries it, and therefore always shows the window.
pub const HIDDEN_ARG: &str = "--hidden";

const STARTUP_FILE: &str = "startup.json";

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StartupSettings {
    pub start_hidden: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StartupState {
    pub launch_at_login: bool,
    pub start_hidden: bool,
    /// False on desktops where the tray could not be created: with no tray
    /// there is no way back to a hidden window, so the UI hides the
    /// "start hidden" option instead of silently stranding the user.
    pub tray_available: bool,
}

pub fn load(config_dir: &Path) -> StartupSettings {
    fs::read_to_string(config_dir.join(STARTUP_FILE))
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

fn save(config_dir: &Path, settings: &StartupSettings) -> Result<(), String> {
    fs::create_dir_all(config_dir).map_err(|e| format!("startup_dir_failed:{e}"))?;
    let text =
        serde_json::to_string_pretty(settings).map_err(|e| format!("startup_encode_failed:{e}"))?;
    fs::write(config_dir.join(STARTUP_FILE), text).map_err(|e| format!("startup_write_failed:{e}"))
}

fn config_dir(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    app.path()
        .app_config_dir()
        .map_err(|_| "startup_config_unavailable".to_string())
}

fn state(app: &tauri::AppHandle) -> Result<StartupState, String> {
    use tauri_plugin_autostart::ManagerExt;
    let launch_at_login = app.autolaunch().is_enabled().unwrap_or(false);
    let start_hidden = load(&config_dir(app)?).start_hidden;
    let tray_available = app
        .try_state::<crate::tray::TrayState>()
        .map(|state| state.available)
        .unwrap_or(false);
    Ok(StartupState {
        launch_at_login,
        start_hidden,
        tray_available,
    })
}

#[tauri::command]
pub fn get_startup_settings(app: tauri::AppHandle) -> Result<StartupState, String> {
    state(&app)
}

#[tauri::command]
pub fn set_launch_at_login(app: tauri::AppHandle, enabled: bool) -> Result<StartupState, String> {
    use tauri_plugin_autostart::ManagerExt;
    let manager = app.autolaunch();
    if enabled {
        manager
            .enable()
            .map_err(|error| format!("autostart_enable_failed:{error}"))?;
    } else {
        manager
            .disable()
            .map_err(|error| format!("autostart_disable_failed:{error}"))?;
    }
    state(&app)
}

#[tauri::command]
pub fn set_start_hidden(app: tauri::AppHandle, enabled: bool) -> Result<StartupState, String> {
    let dir = config_dir(&app)?;
    let mut settings = load(&dir);
    settings.start_hidden = enabled;
    save(&dir, &settings)?;
    state(&app)
}
