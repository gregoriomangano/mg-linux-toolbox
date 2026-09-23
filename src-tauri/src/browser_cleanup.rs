//! Browser cache and cookie detection for Firefox and the common
//! Chromium-family browsers (Chrome, Chromium, Brave, Edge, Opera).
//! Capability-based: every browser/profile is only ever reported when its
//! real directory is found on disk, never assumed from the distro.
//!
//! Cache and cookies are kept in two entirely separate result sets by
//! design (see `cleanup.rs`'s `CleanupScan`): cache feeds the generic
//! "Cache browser" cleanup category, cookies are only ever exposed through
//! `cookie_groups`/`delete_cookie_group` and are never part of a default
//! selection. A cookie database is only ever deleted for a browser
//! [`CookieGroup::is_running`] reports as closed, and only the literal
//! `Cookies`/`cookies.sqlite` file (plus its `-wal`/`-shm`/`-journal`
//! siblings) is ever touched -- never `Login Data`, `logins.json`,
//! `key4.db`/`key3.db`, or any other credential store.
use crate::cleanup::{make_item, CleanupCategory, CleanupRoots, DiscoveredItem};
use serde::Serialize;
use std::{
    fs,
    path::{Path, PathBuf},
};

struct ChromiumVendor {
    key: &'static str,
    display: &'static str,
    /// Relative to `$HOME`.
    config_subpath: &'static str,
    /// Relative to `$XDG_CACHE_HOME`.
    cache_subpath: &'static str,
}

const CHROMIUM_VENDORS: &[ChromiumVendor] = &[
    ChromiumVendor {
        key: "chrome",
        display: "Google Chrome",
        config_subpath: ".config/google-chrome",
        cache_subpath: "google-chrome",
    },
    ChromiumVendor {
        key: "chromium",
        display: "Chromium",
        config_subpath: ".config/chromium",
        cache_subpath: "chromium",
    },
    ChromiumVendor {
        key: "brave",
        display: "Brave",
        config_subpath: ".config/BraveSoftware/Brave-Browser",
        cache_subpath: "BraveSoftware/Brave-Browser",
    },
    ChromiumVendor {
        key: "edge",
        display: "Microsoft Edge",
        config_subpath: ".config/microsoft-edge",
        cache_subpath: "microsoft-edge",
    },
    ChromiumVendor {
        key: "opera",
        display: "Opera",
        config_subpath: ".config/opera",
        cache_subpath: "opera",
    },
];

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CookieGroup {
    pub id: String,
    pub browser: String,
    pub profile_label: Option<String>,
    pub size_bytes: u64,
    pub is_running: bool,
}

fn cookie_group_id(vendor_key: &str, path_label: &str) -> String {
    crate::apt_ops::sha256_hex(format!("cookie\0{vendor_key}\0{path_label}").as_bytes())[..16]
        .to_string()
}

fn process_running(proc_root: &Path, names: &[&str]) -> bool {
    if names.is_empty() {
        return false;
    }
    let Ok(entries) = fs::read_dir(proc_root) else {
        return false;
    };
    for entry in entries.flatten() {
        if entry.file_name().to_string_lossy().parse::<u32>().is_err() {
            continue;
        }
        let Ok(comm) = fs::read_to_string(entry.path().join("comm")) else {
            continue;
        };
        let comm = comm.trim().to_ascii_lowercase();
        if names.iter().any(|n| comm.contains(&n.to_ascii_lowercase())) {
            return true;
        }
    }
    false
}

fn chromium_profiles(vendor_config_dir: &Path) -> Vec<String> {
    let mut profiles = Vec::new();
    let Ok(entries) = fs::read_dir(vendor_config_dir) else {
        return profiles;
    };
    for entry in entries.flatten() {
        let Ok(meta) = entry.metadata() else {
            continue;
        };
        if !meta.is_dir() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        let is_default = name == "Default";
        let is_numbered_profile = name
            .strip_prefix("Profile ")
            .is_some_and(|rest| !rest.is_empty() && rest.chars().all(|c| c.is_ascii_digit()));
        if is_default || is_numbered_profile {
            profiles.push(name);
        }
    }
    profiles.sort();
    profiles
}

fn chromium_running(vendor: &ChromiumVendor, vendor_config_dir: &Path, proc_root: &Path) -> bool {
    if fs::symlink_metadata(vendor_config_dir.join("SingletonLock")).is_ok() {
        return true;
    }
    let process_names: &[&str] = match vendor.key {
        "chrome" => &["chrome"],
        "chromium" => &["chromium", "chromium-browser"],
        "brave" => &["brave"],
        "edge" => &["msedge"],
        "opera" => &["opera"],
        _ => &[],
    };
    process_running(proc_root, process_names)
}

/// Parses `~/.mozilla/firefox/profiles.ini` (a plain INI file) for every
/// `[Profile*]` section, returning `(display label, absolute profile
/// directory)` pairs for profiles whose directory actually exists on disk.
fn firefox_profiles(home: &Path) -> Vec<(String, PathBuf)> {
    let mozilla_dir = home.join(".mozilla/firefox");
    let Ok(text) = fs::read_to_string(mozilla_dir.join("profiles.ini")) else {
        return Vec::new();
    };
    let mut profiles = Vec::new();
    let mut current_path: Option<String> = None;
    let mut current_name: Option<String> = None;
    let mut current_is_relative = true;
    let mut in_profile_section = false;

    fn flush(
        profiles: &mut Vec<(String, PathBuf)>,
        name: &Option<String>,
        path: &Option<String>,
        is_relative: bool,
        mozilla_dir: &Path,
    ) {
        let Some(p) = path else { return };
        let dir = if is_relative {
            mozilla_dir.join(p)
        } else {
            PathBuf::from(p)
        };
        if dir.is_dir() {
            profiles.push((name.clone().unwrap_or_else(|| "Firefox".to_string()), dir));
        }
    }

    for line in text.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed.strip_prefix('[') {
            flush(
                &mut profiles,
                &current_name,
                &current_path,
                current_is_relative,
                &mozilla_dir,
            );
            current_path = None;
            current_name = None;
            current_is_relative = true;
            in_profile_section = rest.starts_with("Profile");
            continue;
        }
        if !in_profile_section {
            continue;
        }
        if let Some((key, value)) = trimmed.split_once('=') {
            match key.trim() {
                "Path" => current_path = Some(value.trim().to_string()),
                "Name" => current_name = Some(value.trim().to_string()),
                "IsRelative" => current_is_relative = value.trim() == "1",
                _ => {}
            }
        }
    }
    flush(
        &mut profiles,
        &current_name,
        &current_path,
        current_is_relative,
        &mozilla_dir,
    );
    profiles
}

fn firefox_running(profile_dir: &Path, proc_root: &Path) -> bool {
    if fs::symlink_metadata(profile_dir.join("lock")).is_ok() {
        return true;
    }
    process_running(proc_root, &["firefox"])
}

fn firefox_cache_dir(cache_home: &Path, profile_dir: &Path) -> Option<PathBuf> {
    let profile_name = profile_dir.file_name()?.to_string_lossy().to_string();
    let candidate = cache_home
        .join("mozilla/firefox")
        .join(&profile_name)
        .join("cache2");
    if candidate.is_dir() {
        return Some(candidate);
    }
    let legacy = profile_dir.join("cache2");
    if legacy.is_dir() {
        return Some(legacy);
    }
    None
}

/// Every base directory a supported browser could keep files in, used by
/// `cleanup.rs` to build its allow-listed roots for the pre-deletion
/// symlink-escape check.
pub fn vendor_dirs(roots: &CleanupRoots) -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = CHROMIUM_VENDORS
        .iter()
        .flat_map(|v| {
            [
                roots.home.join(v.config_subpath),
                roots.cache_home.join(v.cache_subpath),
            ]
        })
        .collect();
    dirs.push(roots.home.join(".mozilla"));
    dirs.push(roots.cache_home.join("mozilla"));
    dirs
}

pub fn cache_category(
    roots: &CleanupRoots,
    discovered: &mut Vec<DiscoveredItem>,
) -> CleanupCategory {
    let mut items = Vec::new();
    for (label, profile_dir) in firefox_profiles(&roots.home) {
        if let Some(cache_dir) = firefox_cache_dir(&roots.cache_home, &profile_dir) {
            if let Some(item) = make_item(
                discovered,
                "browser_cache",
                format!("Firefox — {label}"),
                cache_dir,
                false,
            ) {
                items.push(item);
            }
        }
    }
    for vendor in CHROMIUM_VENDORS {
        let config_dir = roots.home.join(vendor.config_subpath);
        for profile in chromium_profiles(&config_dir) {
            let in_cache_home = roots
                .cache_home
                .join(vendor.cache_subpath)
                .join(&profile)
                .join("Cache");
            let in_config_dir = config_dir.join(&profile).join("Cache");
            let cache_dir = if in_cache_home.is_dir() {
                Some(in_cache_home)
            } else if in_config_dir.is_dir() {
                Some(in_config_dir)
            } else {
                None
            };
            if let Some(cache_dir) = cache_dir {
                if let Some(item) = make_item(
                    discovered,
                    "browser_cache",
                    format!("{} — {profile}", vendor.display),
                    cache_dir,
                    false,
                ) {
                    items.push(item);
                }
            }
        }
    }
    let total = items.iter().map(|i| i.size_bytes).sum();
    CleanupCategory {
        id: "browser_cache",
        size_bytes: total,
        selected_by_default: true,
        items,
    }
}

fn cookie_groups_internal(roots: &CleanupRoots) -> Vec<(CookieGroup, PathBuf)> {
    let mut groups = Vec::new();
    for (label, profile_dir) in firefox_profiles(&roots.home) {
        let cookies = profile_dir.join("cookies.sqlite");
        if cookies.is_file() {
            let size = fs::metadata(&cookies).map(|m| m.len()).unwrap_or(0);
            let running = firefox_running(&profile_dir, &roots.proc_root);
            let id = cookie_group_id("firefox", &profile_dir.to_string_lossy());
            groups.push((
                CookieGroup {
                    id,
                    browser: "Firefox".to_string(),
                    profile_label: Some(label),
                    size_bytes: size,
                    is_running: running,
                },
                cookies,
            ));
        }
    }
    for vendor in CHROMIUM_VENDORS {
        let config_dir = roots.home.join(vendor.config_subpath);
        let vendor_running = chromium_running(vendor, &config_dir, &roots.proc_root);
        for profile in chromium_profiles(&config_dir) {
            let cookies = config_dir.join(&profile).join("Cookies");
            if cookies.is_file() {
                let size = fs::metadata(&cookies).map(|m| m.len()).unwrap_or(0);
                let id = cookie_group_id(vendor.key, &cookies.to_string_lossy());
                groups.push((
                    CookieGroup {
                        id,
                        browser: vendor.display.to_string(),
                        profile_label: Some(profile),
                        size_bytes: size,
                        is_running: vendor_running,
                    },
                    cookies,
                ));
            }
        }
    }
    groups
}

pub fn cookie_groups(roots: &CleanupRoots) -> Vec<CookieGroup> {
    cookie_groups_internal(roots)
        .into_iter()
        .map(|(g, _)| g)
        .collect()
}

fn append_suffix(path: &Path, suffix: &str) -> PathBuf {
    let mut s = path.as_os_str().to_os_string();
    s.push(suffix);
    PathBuf::from(s)
}

/// Deletes one cookie database by its opaque id, re-resolved fresh from a
/// new scan. Refuses when the owning browser is currently running, and
/// refuses (structurally, not just by convention) to touch any file whose
/// name is not the literal cookie database filename -- so this can never be
/// repurposed to delete `Login Data` or any other store even if the
/// discovery logic above is changed incorrectly in the future.
pub fn delete_cookie_group(roots: &CleanupRoots, id: &str) -> Result<u64, String> {
    let (group, cookies_path) = cookie_groups_internal(roots)
        .into_iter()
        .find(|(g, _)| g.id == id)
        .ok_or_else(|| "cookie_group_not_found".to_string())?;
    if group.is_running {
        return Err("browser_running".into());
    }
    let file_name = cookies_path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("");
    if file_name != "Cookies" && file_name != "cookies.sqlite" {
        return Err("unexpected_cookie_file".into());
    }
    let mut freed = 0u64;
    for candidate in [
        cookies_path.clone(),
        append_suffix(&cookies_path, "-wal"),
        append_suffix(&cookies_path, "-shm"),
        append_suffix(&cookies_path, "-journal"),
    ] {
        if let Ok(meta) = fs::metadata(&candidate) {
            freed = freed.saturating_add(meta.len());
            let _ = fs::remove_file(&candidate);
        }
    }
    Ok(freed)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unique_root(label: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "mg-toolbox-browser-{label}-{}-{stamp}",
            std::process::id()
        ))
    }

    fn roots_for(root: &Path) -> CleanupRoots {
        CleanupRoots {
            home: root.join("home"),
            cache_home: root.join("home/.cache"),
            data_home: root.join("home/.local/share"),
            tmp_dirs: vec![root.join("tmp")],
            // An empty, dedicated directory: these tests must never depend on
            // which real browser processes happen to be running on the
            // machine that executes them.
            proc_root: root.join("proc-empty"),
        }
    }

    fn write_file(path: &Path, bytes: &[u8]) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, bytes).unwrap();
    }

    #[test]
    fn a_chromium_profile_is_detected_only_when_its_directory_really_exists() {
        let root = unique_root("chromium-detect");
        let roots = roots_for(&root);
        write_file(
            &roots.home.join(".config/google-chrome/Default/Cookies"),
            b"sqlite-bytes",
        );
        write_file(
            &roots
                .cache_home
                .join("google-chrome/Default/Cache/data.bin"),
            &[0u8; 1024],
        );
        let mut discovered = Vec::new();
        let category = cache_category(&roots, &mut discovered);
        assert!(category
            .items
            .iter()
            .any(|i| i.label.contains("Google Chrome")));
        let groups = cookie_groups(&roots);
        assert_eq!(groups.len(), 1);
        assert_eq!(groups[0].browser, "Google Chrome");
        assert!(!groups[0].is_running);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_running_chromium_browser_is_detected_via_singleton_lock() {
        let root = unique_root("chromium-running");
        let roots = roots_for(&root);
        write_file(
            &roots.home.join(".config/google-chrome/Default/Cookies"),
            b"x",
        );
        fs::create_dir_all(roots.home.join(".config/google-chrome")).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(
            "bogus-target",
            roots.home.join(".config/google-chrome/SingletonLock"),
        )
        .unwrap();
        let groups = cookie_groups(&roots);
        assert_eq!(groups.len(), 1);
        assert!(groups[0].is_running);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_firefox_profile_is_read_from_profiles_ini_with_isrelative() {
        let root = unique_root("firefox-profile");
        let roots = roots_for(&root);
        fs::create_dir_all(roots.home.join(".mozilla/firefox/abc.default-release")).unwrap();
        write_file(
            &roots.home.join(".mozilla/firefox/profiles.ini"),
            b"[Profile0]\nName=default\nIsRelative=1\nPath=abc.default-release\nDefault=1\n",
        );
        write_file(
            &roots
                .home
                .join(".mozilla/firefox/abc.default-release/cookies.sqlite"),
            b"sqlite-bytes",
        );
        let groups = cookie_groups(&roots);
        assert_eq!(groups.len(), 1);
        assert_eq!(groups[0].browser, "Firefox");
        assert_eq!(groups[0].profile_label.as_deref(), Some("default"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_running_firefox_profile_is_detected_via_the_lock_file() {
        let root = unique_root("firefox-running");
        let roots = roots_for(&root);
        fs::create_dir_all(roots.home.join(".mozilla/firefox/abc.default-release")).unwrap();
        write_file(
            &roots.home.join(".mozilla/firefox/profiles.ini"),
            b"[Profile0]\nName=default\nIsRelative=1\nPath=abc.default-release\n",
        );
        write_file(
            &roots
                .home
                .join(".mozilla/firefox/abc.default-release/cookies.sqlite"),
            b"x",
        );
        #[cfg(unix)]
        std::os::unix::fs::symlink(
            "12345",
            roots.home.join(".mozilla/firefox/abc.default-release/lock"),
        )
        .unwrap();
        let groups = cookie_groups(&roots);
        assert!(groups[0].is_running);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn deleting_a_cookie_group_while_the_browser_is_running_is_refused() {
        let root = unique_root("delete-while-running");
        let roots = roots_for(&root);
        fs::create_dir_all(roots.home.join(".config/google-chrome")).unwrap();
        write_file(
            &roots.home.join(".config/google-chrome/Default/Cookies"),
            b"x",
        );
        #[cfg(unix)]
        std::os::unix::fs::symlink(
            "bogus",
            roots.home.join(".config/google-chrome/SingletonLock"),
        )
        .unwrap();
        let groups = cookie_groups(&roots);
        let result = delete_cookie_group(&roots, &groups[0].id);
        assert_eq!(result, Err("browser_running".to_string()));
        assert!(roots
            .home
            .join(".config/google-chrome/Default/Cookies")
            .exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn deleting_a_cookie_group_when_closed_removes_only_the_cookie_files() {
        let root = unique_root("delete-closed");
        let roots = roots_for(&root);
        let profile_dir = roots.home.join(".config/google-chrome/Default");
        write_file(&profile_dir.join("Cookies"), b"cookie-bytes");
        write_file(&profile_dir.join("Cookies-wal"), b"wal-bytes");
        write_file(&profile_dir.join("Login Data"), b"never-touch-this");
        let groups = cookie_groups(&roots);
        let freed = delete_cookie_group(&roots, &groups[0].id).unwrap();
        assert!(freed > 0);
        assert!(!profile_dir.join("Cookies").exists());
        assert!(
            profile_dir.join("Login Data").exists(),
            "the password store must never be touched by cookie cleanup"
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn numbered_chromium_profiles_are_recognised_alongside_default() {
        let root = unique_root("numbered-profiles");
        let roots = roots_for(&root);
        write_file(&roots.home.join(".config/chromium/Profile 1/Cookies"), b"x");
        write_file(
            &roots.home.join(".config/chromium/NotAProfile/Cookies"),
            b"y",
        );
        let groups = cookie_groups(&roots);
        assert_eq!(groups.len(), 1);
        assert_eq!(groups[0].profile_label.as_deref(), Some("Profile 1"));
        let _ = fs::remove_dir_all(&root);
    }
}
