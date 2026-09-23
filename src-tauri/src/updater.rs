use serde::Serialize;
use tauri::AppHandle;
use tauri_plugin_updater::UpdaterExt;
use url::Url;

#[derive(Clone, Debug, PartialEq)]
pub struct RuntimeConfig {
    endpoint: Url,
    pubkey: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum DistributionChannel {
    AppImage,
    Deb,
    Aur,
    Unknown,
}

/// Which system package manager owns the currently running binary, if any. This is a
/// capability probe (does dpkg/pacman actually know this exact executable path), not a
/// distro-name guess, so it works correctly even on a distro whose default package
/// manager differs from what installed this particular build.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PackageManagerKind {
    Dpkg,
    Pacman,
}

fn detect_package_manager(exe: &std::path::Path) -> Option<PackageManagerKind> {
    if std::process::Command::new("dpkg")
        .arg("-S")
        .arg(exe)
        .output()
        .is_ok_and(|output| output.status.success())
    {
        return Some(PackageManagerKind::Dpkg);
    }
    if std::process::Command::new("pacman")
        .arg("-Qo")
        .arg(exe)
        .output()
        .is_ok_and(|output| output.status.success())
    {
        return Some(PackageManagerKind::Pacman);
    }
    None
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateStatus {
    pub state: String,
    pub installed_version: String,
    pub channel: DistributionChannel,
    pub available_version: Option<String>,
}

pub fn runtime_config(
    endpoint: Option<&str>,
    pubkey: Option<&str>,
) -> Result<Option<RuntimeConfig>, String> {
    let (Some(endpoint), Some(pubkey)) = (
        endpoint.map(str::trim).filter(|v| !v.is_empty()),
        pubkey.map(str::trim).filter(|v| !v.is_empty()),
    ) else {
        return Ok(None);
    };
    let endpoint = Url::parse(endpoint).map_err(|_| "invalid updater endpoint")?;
    if endpoint.scheme() != "https" || endpoint.host().is_none() {
        return Err("updater endpoint must use HTTPS".into());
    }
    Ok(Some(RuntimeConfig {
        endpoint,
        pubkey: pubkey.to_string(),
    }))
}

pub fn distribution_channel(
    appimage: Option<&str>,
    package_manager: Option<PackageManagerKind>,
) -> DistributionChannel {
    if appimage.is_some_and(|value| !value.trim().is_empty()) {
        return DistributionChannel::AppImage;
    }
    match package_manager {
        Some(PackageManagerKind::Dpkg) => DistributionChannel::Deb,
        Some(PackageManagerKind::Pacman) => DistributionChannel::Aur,
        None => DistributionChannel::Unknown,
    }
}

fn current_channel() -> DistributionChannel {
    let package_manager = std::env::current_exe()
        .ok()
        .and_then(|exe| detect_package_manager(&exe));
    distribution_channel(std::env::var("APPIMAGE").ok().as_deref(), package_manager)
}

fn configured() -> Result<Option<RuntimeConfig>, String> {
    runtime_config(
        option_env!("MG_UPDATER_ENDPOINT"),
        option_env!("MG_UPDATER_PUBKEY"),
    )
}

fn base_status(app: &AppHandle, state: &str) -> UpdateStatus {
    UpdateStatus {
        state: state.into(),
        installed_version: app.package_info().version.to_string(),
        channel: current_channel(),
        available_version: None,
    }
}

#[tauri::command]
pub fn get_update_status(app: AppHandle) -> Result<UpdateStatus, String> {
    if current_channel() != DistributionChannel::AppImage {
        return Ok(base_status(&app, "packageManaged"));
    }
    Ok(base_status(
        &app,
        if configured()?.is_some() {
            "idle"
        } else {
            "notConfigured"
        },
    ))
}

#[tauri::command]
pub async fn check_for_update(app: AppHandle) -> Result<UpdateStatus, String> {
    if current_channel() != DistributionChannel::AppImage {
        return Ok(base_status(&app, "packageManaged"));
    }
    let Some(config) = configured()? else {
        return Ok(base_status(&app, "notConfigured"));
    };
    let update = app
        .updater_builder()
        .pubkey(config.pubkey)
        .endpoints(vec![config.endpoint])
        .map_err(|e| e.to_string())?
        .build()
        .map_err(|e| e.to_string())?
        .check()
        .await
        .map_err(|e| e.to_string())?;
    if let Some(update) = update {
        let mut status = base_status(&app, "available");
        status.available_version = Some(update.version);
        Ok(status)
    } else {
        Ok(base_status(&app, "current"))
    }
}

#[tauri::command]
pub async fn download_and_install_update(app: AppHandle) -> Result<(), String> {
    if current_channel() != DistributionChannel::AppImage {
        return Err("internal updates are only available for AppImage".into());
    }
    let config = configured()?.ok_or("updater is not configured")?;
    let update = app
        .updater_builder()
        .pubkey(config.pubkey)
        .endpoints(vec![config.endpoint])
        .map_err(|e| e.to_string())?
        .build()
        .map_err(|e| e.to_string())?
        .check()
        .await
        .map_err(|e| e.to_string())?
        .ok_or("no update is available")?;
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|e| e.to_string())?;
    app.restart();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn updater_requires_both_real_endpoint_and_public_key() {
        assert_eq!(runtime_config(None, None), Ok(None));
        assert_eq!(
            runtime_config(Some("https://updates.test/latest.json"), None),
            Ok(None)
        );
        assert_eq!(runtime_config(None, Some("PUBLIC")), Ok(None));
        assert!(
            runtime_config(Some("https://updates.test/latest.json"), Some("PUBLIC"))
                .unwrap()
                .is_some()
        );
    }

    #[test]
    fn updater_rejects_insecure_or_malformed_endpoints() {
        assert!(runtime_config(Some("http://updates.test/latest.json"), Some("PUBLIC")).is_err());
        assert!(runtime_config(Some("not a url"), Some("PUBLIC")).is_err());
    }

    #[test]
    fn internal_updates_are_only_for_appimage_channel() {
        assert_eq!(
            distribution_channel(Some("/tmp/MG.AppImage"), Some(PackageManagerKind::Dpkg)),
            DistributionChannel::AppImage,
            "an AppImage runtime marker always wins, even if a package manager also owns the path"
        );
        assert_eq!(
            distribution_channel(None, Some(PackageManagerKind::Dpkg)),
            DistributionChannel::Deb
        );
        assert_eq!(
            distribution_channel(None, Some(PackageManagerKind::Pacman)),
            DistributionChannel::Aur
        );
        assert_eq!(
            distribution_channel(None, None),
            DistributionChannel::Unknown
        );
    }
}
