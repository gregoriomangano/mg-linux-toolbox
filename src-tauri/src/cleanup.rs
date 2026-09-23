//! Cross-distro, capability-based cleanup of temporary files, application
//! caches, thumbnails and the trash -- never a "magic optimizer": this
//! module only ever reports and removes files it has just found on disk by
//! scanning real, standard locations (primarily `XDG_CACHE_HOME`), never a
//! guessed or hardcoded distro path, and it never touches the kernel page
//! cache (`/proc/sys/vm/drop_caches` is intentionally not implemented here).
//!
//! Browser cache/cookie detection lives in [`crate::browser_cleanup`];
//! this module owns the shared scan/selection/deletion plumbing both use.
//!
//! Security model, mirroring `apt_ops.rs`/`apt_sources.rs`: the frontend
//! only ever sees opaque item ids (no path is ever serialized to it), and
//! every deletion re-runs the *entire* discovery scan fresh, re-derives the
//! real path for the requested id, and re-verifies with
//! [`fs::canonicalize`] that the real path still resolves inside one of the
//! fixed, allow-listed roots before touching anything -- so a symlink placed
//! inside a cache directory can never cause a deletion outside it, and the
//! frontend can never smuggle in an arbitrary path.
//!
//! The general idea of recognising well-known per-app cache locations
//! (browsers, editors, package managers) was informed by reading Super
//! Linux Utility's cleanup feature
//! (<https://github.com/sviluppoarte1-lang/superlinuxutility>,
//! GPL-3.0-or-later, `lib/services/cleanup_service.dart`). No Dart source
//! was copied; the curated path list here is independently derived and
//! deliberately narrower (e.g. it never removes an editor's
//! workspace/history/config, never falls back to `sudo`, and never drops
//! the kernel page cache).
use crate::browser_cleanup;
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Path, PathBuf},
};

fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/"))
}

/// The resolved XDG base directories this module scans, injectable so tests
/// never need to touch the real `$HOME` or mutate process environment
/// variables.
pub struct CleanupRoots {
    pub home: PathBuf,
    pub cache_home: PathBuf,
    pub data_home: PathBuf,
    pub tmp_dirs: Vec<PathBuf>,
    /// Where to look for running-process detection (`/proc` in production).
    /// Injectable so browser-running tests never depend on which real
    /// processes happen to be running on the machine that executes them.
    pub proc_root: PathBuf,
}

impl CleanupRoots {
    pub fn from_env() -> Self {
        let home = home_dir();
        let cache_home = std::env::var_os("XDG_CACHE_HOME")
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .unwrap_or_else(|| home.join(".cache"));
        let data_home = std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .unwrap_or_else(|| home.join(".local/share"));
        Self {
            home,
            cache_home,
            data_home,
            tmp_dirs: vec![PathBuf::from("/tmp")],
            proc_root: PathBuf::from("/proc"),
        }
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CleanupItem {
    pub id: String,
    pub label: String,
    pub size_bytes: u64,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CleanupCategory {
    pub id: &'static str,
    pub size_bytes: u64,
    pub selected_by_default: bool,
    pub items: Vec<CleanupItem>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CleanupScan {
    pub total_reclaimable_bytes: u64,
    pub categories: Vec<CleanupCategory>,
    pub cookie_groups: Vec<browser_cleanup::CookieGroup>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CleanupSelectionInput {
    pub item_ids: Vec<String>,
    pub cookie_group_ids: Vec<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CleanupSkipped {
    pub id: String,
    pub label: String,
    pub reason: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CleanupOutcome {
    pub freed_bytes: u64,
    pub cleaned_count: u32,
    pub skipped: Vec<CleanupSkipped>,
}

/// One discovered, deletable item, kept internally alongside its real path
/// -- never serialized to the frontend, only ever re-derived fresh from a
/// scan and consumed inside this process.
pub(crate) struct DiscoveredItem {
    pub id: String,
    pub label: String,
    pub path: PathBuf,
    /// When true, only entries owned by the current uid inside `path` are
    /// ever touched (used for shared locations like `/tmp`); when false the
    /// whole directory is already known to be inside the user's own home.
    pub restrict_to_uid: bool,
}

fn item_id(category: &str, path_label: &str) -> String {
    crate::apt_ops::sha256_hex(format!("{category}\0{path_label}").as_bytes())[..16].to_string()
}

/// Recursive size of every regular file under `path`, in bytes. Symlinks are
/// never followed (matching `DirEntry::metadata`'s own symlink-safe
/// behaviour), so a symlink planted inside a cache directory can never make
/// this walk (or the deletion that follows the same shape) leave the
/// directory it started in.
fn dir_size(path: &Path) -> u64 {
    let mut total = 0u64;
    let mut stack = vec![path.to_path_buf()];
    while let Some(current) = stack.pop() {
        let Ok(entries) = fs::read_dir(&current) else {
            continue;
        };
        for entry in entries.flatten() {
            let Ok(meta) = entry.metadata() else {
                continue;
            };
            if meta.is_dir() {
                stack.push(entry.path());
            } else if meta.is_file() {
                total = total.saturating_add(meta.len());
            }
        }
    }
    total
}

fn owned_children_size(path: &Path) -> u64 {
    let uid = unsafe { libc::getuid() };
    let mut total = 0u64;
    let Ok(entries) = fs::read_dir(path) else {
        return 0;
    };
    for entry in entries.flatten() {
        let Ok(meta) = entry.metadata() else {
            continue;
        };
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            if meta.uid() != uid {
                continue;
            }
        }
        total = total.saturating_add(if meta.is_dir() {
            dir_size(&entry.path())
        } else {
            meta.len()
        });
    }
    total
}

/// Computes the item's size, and -- only if it is non-zero -- records it
/// both in the internal `discovered` list (used later to re-derive the real
/// path by id) and returns the public, path-free [`CleanupItem`] for the
/// scan response.
pub(crate) fn make_item(
    discovered: &mut Vec<DiscoveredItem>,
    category: &str,
    label: String,
    path: PathBuf,
    restrict_to_uid: bool,
) -> Option<CleanupItem> {
    if !path.is_dir() {
        return None;
    }
    let size_bytes = if restrict_to_uid {
        owned_children_size(&path)
    } else {
        dir_size(&path)
    };
    if size_bytes == 0 {
        return None;
    }
    let id = item_id(category, &path.to_string_lossy());
    discovered.push(DiscoveredItem {
        id: id.clone(),
        label: label.clone(),
        path,
        restrict_to_uid,
    });
    Some(CleanupItem {
        id,
        label,
        size_bytes,
    })
}

/// Directory names under `$XDG_CACHE_HOME` that are never offered, even
/// though they live under `.cache`: session/credential/input-method state
/// that a generic "it's called .cache" heuristic would otherwise wrongly
/// treat as disposable.
const CACHE_DENYLIST: &[&str] = &[
    "keyring",
    "gnome-keyring",
    "pkcs11",
    "ibus",
    "ibus-table",
    "fcitx",
    "fcitx5",
    "dconf",
    "gvfs",
    "session",
    "sessions",
];

/// `$XDG_CACHE_HOME` subdirectory names that belong to a browser and are
/// reported instead under the dedicated "Cache browser" category (see
/// `browser_cleanup.rs`), so nothing is ever double-counted.
const BROWSER_CACHE_DIR_NAMES: &[&str] = &[
    "google-chrome",
    "chromium",
    "mozilla",
    "BraveSoftware",
    "microsoft-edge",
    "opera",
    "vivaldi",
];

/// Narrow, individually-named safe subfolders outside `.cache` -- never a
/// whole `~/.config/<App>` directory, so workspace state, history and
/// settings for the same apps are structurally impossible to reach from
/// here.
const CURATED_APP_CACHE_PATHS: &[(&str, &str)] = &[
    (".config/Code/Cache", "VS Code — Cache"),
    (".config/Code/CachedData", "VS Code — CachedData"),
    (".config/Code/Code Cache", "VS Code — Code Cache"),
    (".config/Code/GPUCache", "VS Code — GPUCache"),
    (".config/Cursor/Cache", "Cursor — Cache"),
    (".config/Cursor/CachedData", "Cursor — CachedData"),
    (".config/Cursor/Code Cache", "Cursor — Code Cache"),
    (".config/Cursor/GPUCache", "Cursor — GPUCache"),
    (".cargo/registry/cache", "Cargo — pacchetti scaricati"),
    (".cargo/registry/src", "Cargo — sorgenti scaricati"),
    (".npm", "npm — cache pacchetti"),
    (".gradle/caches", "Gradle — cache moduli"),
    ("go/pkg/mod/cache", "Go — cache moduli"),
];

fn temp_category(roots: &CleanupRoots, discovered: &mut Vec<DiscoveredItem>) -> CleanupCategory {
    let mut items = Vec::new();
    for tmp_dir in &roots.tmp_dirs {
        if let Some(item) = make_item(
            discovered,
            "temp",
            tmp_dir.to_string_lossy().to_string(),
            tmp_dir.clone(),
            true,
        ) {
            items.push(item);
        }
    }
    let total = items.iter().map(|i| i.size_bytes).sum();
    CleanupCategory {
        id: "temp",
        size_bytes: total,
        selected_by_default: true,
        items,
    }
}

fn app_cache_category(
    roots: &CleanupRoots,
    discovered: &mut Vec<DiscoveredItem>,
) -> CleanupCategory {
    let mut items = Vec::new();
    if let Ok(entries) = fs::read_dir(&roots.cache_home) {
        for entry in entries.flatten() {
            let Ok(meta) = entry.metadata() else {
                continue;
            };
            if !meta.is_dir() {
                continue;
            }
            let name = entry.file_name().to_string_lossy().to_string();
            if name.eq_ignore_ascii_case("thumbnails") {
                continue;
            }
            if CACHE_DENYLIST.iter().any(|d| d.eq_ignore_ascii_case(&name)) {
                continue;
            }
            if BROWSER_CACHE_DIR_NAMES
                .iter()
                .any(|d| d.eq_ignore_ascii_case(&name))
            {
                continue;
            }
            if let Some(item) = make_item(discovered, "app_cache", name, entry.path(), false) {
                items.push(item);
            }
        }
    }
    for (rel, label) in CURATED_APP_CACHE_PATHS {
        let path = roots.home.join(rel);
        if let Some(item) = make_item(discovered, "app_cache", (*label).to_string(), path, false) {
            items.push(item);
        }
    }
    let total = items.iter().map(|i| i.size_bytes).sum();
    CleanupCategory {
        id: "app_cache",
        size_bytes: total,
        selected_by_default: true,
        items,
    }
}

fn thumbnails_category(
    roots: &CleanupRoots,
    discovered: &mut Vec<DiscoveredItem>,
) -> CleanupCategory {
    let path = roots.cache_home.join("thumbnails");
    let mut items = Vec::new();
    if let Some(item) = make_item(
        discovered,
        "thumbnails",
        "Miniature".to_string(),
        path,
        false,
    ) {
        items.push(item);
    }
    let total = items.iter().map(|i| i.size_bytes).sum();
    CleanupCategory {
        id: "thumbnails",
        size_bytes: total,
        selected_by_default: true,
        items,
    }
}

fn trash_category(roots: &CleanupRoots, discovered: &mut Vec<DiscoveredItem>) -> CleanupCategory {
    let path = roots.data_home.join("Trash");
    let mut items = Vec::new();
    if let Some(item) = make_item(discovered, "trash", "Cestino".to_string(), path, false) {
        items.push(item);
    }
    let total = items.iter().map(|i| i.size_bytes).sum();
    CleanupCategory {
        id: "trash",
        size_bytes: total,
        // The trash is real user data (deleted files, still recoverable);
        // never pre-selected, matching the requirement that it is offered
        // but not swept up by a default "clean everything" action.
        selected_by_default: false,
        items,
    }
}

fn discover_all(
    roots: &CleanupRoots,
) -> (
    Vec<CleanupCategory>,
    Vec<browser_cleanup::CookieGroup>,
    Vec<DiscoveredItem>,
) {
    let mut discovered = Vec::new();
    let temp = temp_category(roots, &mut discovered);
    let app_cache = app_cache_category(roots, &mut discovered);
    let browser_cache = browser_cleanup::cache_category(roots, &mut discovered);
    let thumbnails = thumbnails_category(roots, &mut discovered);
    let trash = trash_category(roots, &mut discovered);
    let cookie_groups = browser_cleanup::cookie_groups(roots);
    (
        vec![temp, app_cache, browser_cache, thumbnails, trash],
        cookie_groups,
        discovered,
    )
}

fn allowed_roots(roots: &CleanupRoots) -> Vec<PathBuf> {
    let mut list = vec![roots.cache_home.clone(), roots.data_home.join("Trash")];
    list.extend(roots.tmp_dirs.clone());
    for (rel, _) in CURATED_APP_CACHE_PATHS {
        list.push(roots.home.join(rel));
    }
    list.extend(browser_cleanup::vendor_dirs(roots));
    list
}

/// `true` only when `candidate` resolves, after following any symlinks, to
/// a path that is itself inside one of the currently allow-listed roots.
/// Re-checked immediately before every deletion, exactly like
/// `apt_ops::is_allowed_source_file`.
fn is_allowed(candidate: &Path, roots: &[PathBuf]) -> bool {
    let Ok(real_candidate) = fs::canonicalize(candidate) else {
        return false;
    };
    roots.iter().any(|root| {
        fs::canonicalize(root)
            .map(|real_root| real_candidate == real_root || real_candidate.starts_with(&real_root))
            .unwrap_or(false)
    })
}

/// Removes every immediate child of `path`, individually, catching each
/// failure on its own so one locked or permission-denied file never blocks
/// the rest. `path` itself is always kept: some apps expect their cache
/// directory to still exist, and for `/tmp` removing it is simply not ours
/// to do. Returns `(bytes_freed, items_cleaned, items_skipped)`.
fn clear_directory_contents(path: &Path, restrict_to_uid: bool) -> (u64, u32, u32) {
    let uid = unsafe { libc::getuid() };
    let mut freed = 0u64;
    let mut cleaned = 0u32;
    let mut skipped = 0u32;
    let Ok(entries) = fs::read_dir(path) else {
        return (0, 0, 0);
    };
    for entry in entries.flatten() {
        let Ok(meta) = entry.metadata() else {
            skipped += 1;
            continue;
        };
        #[cfg(unix)]
        if restrict_to_uid {
            use std::os::unix::fs::MetadataExt;
            if meta.uid() != uid {
                continue;
            }
        }
        let entry_path = entry.path();
        let size = if meta.is_dir() {
            dir_size(&entry_path)
        } else {
            meta.len()
        };
        let removed = if meta.is_dir() {
            fs::remove_dir_all(&entry_path).is_ok()
        } else {
            fs::remove_file(&entry_path).is_ok()
        };
        if removed {
            freed = freed.saturating_add(size);
            cleaned += 1;
        } else {
            skipped += 1;
        }
    }
    (freed, cleaned, skipped)
}

pub fn scan_with_roots(roots: &CleanupRoots) -> CleanupScan {
    let (categories, cookie_groups, _discovered) = discover_all(roots);
    let total_reclaimable_bytes = categories.iter().map(|c| c.size_bytes).sum();
    CleanupScan {
        total_reclaimable_bytes,
        categories,
        cookie_groups,
    }
}

/// Re-scans fresh, re-derives every selected id's real path, re-verifies it
/// against the allow-listed roots, and only then deletes. Cookies are only
/// ever touched when their id is explicitly present in `cookie_group_ids`
/// (never inferred from `item_ids`), and only when the owning browser is
/// not currently running.
pub fn clean_selected_with_roots(
    roots: &CleanupRoots,
    selection: &CleanupSelectionInput,
) -> CleanupOutcome {
    let (_categories, cookie_groups, discovered) = discover_all(roots);
    let roots_list = allowed_roots(roots);
    let mut freed_total = 0u64;
    let mut cleaned_total = 0u32;
    let mut skipped = Vec::new();

    for id in &selection.item_ids {
        let Some(item) = discovered.iter().find(|d| &d.id == id) else {
            skipped.push(CleanupSkipped {
                id: id.clone(),
                label: id.clone(),
                reason: "not_found",
            });
            continue;
        };
        if !is_allowed(&item.path, &roots_list) {
            skipped.push(CleanupSkipped {
                id: item.id.clone(),
                label: item.label.clone(),
                reason: "not_allowed",
            });
            continue;
        }
        let (freed, cleaned, item_skipped) =
            clear_directory_contents(&item.path, item.restrict_to_uid);
        freed_total = freed_total.saturating_add(freed);
        cleaned_total += cleaned;
        if item_skipped > 0 && cleaned == 0 {
            skipped.push(CleanupSkipped {
                id: item.id.clone(),
                label: item.label.clone(),
                reason: "in_use",
            });
        } else if item_skipped > 0 {
            skipped.push(CleanupSkipped {
                id: item.id.clone(),
                label: item.label.clone(),
                reason: "partially_in_use",
            });
        }
    }

    for cookie_id in &selection.cookie_group_ids {
        let Some(group) = cookie_groups.iter().find(|g| &g.id == cookie_id) else {
            skipped.push(CleanupSkipped {
                id: cookie_id.clone(),
                label: cookie_id.clone(),
                reason: "not_found",
            });
            continue;
        };
        if group.is_running {
            skipped.push(CleanupSkipped {
                id: group.id.clone(),
                label: group.browser.clone(),
                reason: "browser_running",
            });
            continue;
        }
        match browser_cleanup::delete_cookie_group(roots, &group.id) {
            Ok(freed) => {
                freed_total = freed_total.saturating_add(freed);
                cleaned_total += 1;
            }
            Err(_) => skipped.push(CleanupSkipped {
                id: group.id.clone(),
                label: group.browser.clone(),
                reason: "delete_failed",
            }),
        }
    }

    CleanupOutcome {
        freed_bytes: freed_total,
        cleaned_count: cleaned_total,
        skipped,
    }
}

#[tauri::command]
pub async fn scan_cleanup_targets() -> Result<CleanupScan, String> {
    tauri::async_runtime::spawn_blocking(|| scan_with_roots(&CleanupRoots::from_env()))
        .await
        .map_err(|_| "cleanup_scan_failed".to_string())
}

#[tauri::command]
pub async fn clean_selected_cleanup_targets(
    selection: CleanupSelectionInput,
) -> Result<CleanupOutcome, String> {
    tauri::async_runtime::spawn_blocking(move || {
        clean_selected_with_roots(&CleanupRoots::from_env(), &selection)
    })
    .await
    .map_err(|_| "cleanup_run_failed".to_string())
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
            "mg-toolbox-cleanup-{label}-{}-{stamp}",
            std::process::id()
        ))
    }

    fn roots_for(root: &Path) -> CleanupRoots {
        CleanupRoots {
            home: root.join("home"),
            cache_home: root.join("home/.cache"),
            data_home: root.join("home/.local/share"),
            tmp_dirs: vec![root.join("tmp")],
            proc_root: root.join("proc-empty"),
        }
    }

    fn write_file(path: &Path, bytes: &[u8]) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, bytes).unwrap();
    }

    #[test]
    fn xdg_cache_home_custom_is_scanned_instead_of_a_hardcoded_path() {
        let root = unique_root("custom-cache-home");
        let roots = roots_for(&root);
        write_file(&roots.cache_home.join("pip/wheel.bin"), &[0u8; 2048]);
        let scan = scan_with_roots(&roots);
        let app_cache = scan
            .categories
            .iter()
            .find(|c| c.id == "app_cache")
            .unwrap();
        assert!(app_cache
            .items
            .iter()
            .any(|i| i.label == "pip" && i.size_bytes == 2048));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn size_calculation_sums_every_regular_file_recursively() {
        let root = unique_root("size-calc");
        let dir = root.join("home/.cache/demo");
        write_file(&dir.join("a.bin"), &[0u8; 100]);
        write_file(&dir.join("sub/b.bin"), &[0u8; 250]);
        assert_eq!(dir_size(&dir), 350);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_symlink_inside_a_cache_directory_is_never_followed() {
        let root = unique_root("symlink-escape");
        let roots = roots_for(&root);
        let outside = root.join("outside-secret");
        write_file(&outside.join("secret.bin"), &[0u8; 999]);
        let cache_item_dir = roots.cache_home.join("evil");
        fs::create_dir_all(&cache_item_dir).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(&outside, cache_item_dir.join("escape")).unwrap();
        // The symlinked-to bytes must never be counted...
        assert_eq!(dir_size(&cache_item_dir), 0);
        // ...and cleaning the category must never delete anything outside it.
        let (_freed, _cleaned, _skipped) = clear_directory_contents(&cache_item_dir, false);
        assert!(
            outside.join("secret.bin").exists(),
            "the symlink target must survive untouched"
        );
        let _ = fs::remove_dir_all(&root);
        let _ = fs::remove_dir_all(&outside);
    }

    #[test]
    fn a_missing_item_id_is_reported_as_not_found_and_never_panics() {
        let root = unique_root("missing-item");
        let roots = roots_for(&root);
        fs::create_dir_all(&roots.cache_home).unwrap();
        let outcome = clean_selected_with_roots(
            &roots,
            &CleanupSelectionInput {
                item_ids: vec!["does-not-exist".to_string()],
                cookie_group_ids: vec![],
            },
        );
        assert_eq!(outcome.cleaned_count, 0);
        assert_eq!(outcome.skipped.len(), 1);
        assert_eq!(outcome.skipped[0].reason, "not_found");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_permission_denied_directory_is_skipped_without_aborting_the_scan() {
        let root = unique_root("permission-denied");
        let roots = roots_for(&root);
        write_file(&roots.cache_home.join("readable/file.bin"), &[0u8; 64]);
        let locked_dir = roots.cache_home.join("locked");
        fs::create_dir_all(&locked_dir).unwrap();
        write_file(&locked_dir.join("inner.bin"), &[0u8; 32]);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&locked_dir, fs::Permissions::from_mode(0o000));
        }
        let scan = scan_with_roots(&roots);
        let app_cache = scan
            .categories
            .iter()
            .find(|c| c.id == "app_cache")
            .unwrap();
        assert!(app_cache.items.iter().any(|i| i.label == "readable"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&locked_dir, fs::Permissions::from_mode(0o755));
        }
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_selected_cache_item_is_cleaned_and_reported_as_freed() {
        let root = unique_root("clean-selected");
        let roots = roots_for(&root);
        write_file(&roots.cache_home.join("pip/big.bin"), &[0u8; 4096]);
        let scan = scan_with_roots(&roots);
        let item = scan
            .categories
            .iter()
            .find(|c| c.id == "app_cache")
            .unwrap()
            .items
            .iter()
            .find(|i| i.label == "pip")
            .unwrap();
        let outcome = clean_selected_with_roots(
            &roots,
            &CleanupSelectionInput {
                item_ids: vec![item.id.clone()],
                cookie_group_ids: vec![],
            },
        );
        assert_eq!(outcome.freed_bytes, 4096);
        assert_eq!(outcome.cleaned_count, 1);
        assert!(outcome.skipped.is_empty());
        assert!(
            roots.cache_home.join("pip").is_dir(),
            "the directory itself is kept, only its contents are cleared"
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn cookies_are_never_selected_unless_their_id_is_explicitly_requested() {
        let root = unique_root("cookies-explicit");
        let roots = roots_for(&root);
        write_file(&roots.cache_home.join("pip/x.bin"), &[0u8; 10]);
        let scan = scan_with_roots(&roots);
        let item_ids: Vec<String> = scan
            .categories
            .iter()
            .flat_map(|c| c.items.iter().map(|i| i.id.clone()))
            .collect();
        // Cleaning only the discovered cache items must never remove a
        // cookie group id at the same time -- cookie ids live in a
        // completely separate namespace and list.
        let outcome = clean_selected_with_roots(
            &roots,
            &CleanupSelectionInput {
                item_ids,
                cookie_group_ids: vec![],
            },
        );
        assert!(outcome
            .skipped
            .iter()
            .all(|s| s.reason != "browser_running"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn the_trash_category_is_never_selected_by_default() {
        let root = unique_root("trash-default");
        let roots = roots_for(&root);
        write_file(&roots.data_home.join("Trash/files/deleted.bin"), &[0u8; 10]);
        let scan = scan_with_roots(&roots);
        let trash = scan.categories.iter().find(|c| c.id == "trash").unwrap();
        assert!(!trash.selected_by_default);
        assert!(trash.size_bytes > 0);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_failed_item_never_blocks_the_others_in_the_same_selection() {
        let root = unique_root("partial-failure");
        let roots = roots_for(&root);
        write_file(&roots.cache_home.join("pip/a.bin"), &[0u8; 100]);
        write_file(&roots.cache_home.join("npm-like/b.bin"), &[0u8; 200]);
        let scan = scan_with_roots(&roots);
        let mut ids: Vec<String> = vec!["bogus-id-1".to_string()];
        ids.extend(
            scan.categories
                .iter()
                .flat_map(|c| c.items.iter().map(|i| i.id.clone())),
        );
        let outcome = clean_selected_with_roots(
            &roots,
            &CleanupSelectionInput {
                item_ids: ids,
                cookie_group_ids: vec![],
            },
        );
        assert_eq!(outcome.freed_bytes, 300);
        assert_eq!(outcome.cleaned_count, 2);
        assert_eq!(outcome.skipped.len(), 1);
        assert_eq!(outcome.skipped[0].reason, "not_found");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_denylisted_cache_subdirectory_is_never_offered() {
        let root = unique_root("denylist");
        let roots = roots_for(&root);
        write_file(&roots.cache_home.join("keyring/secret.bin"), &[0u8; 10]);
        let scan = scan_with_roots(&roots);
        let app_cache = scan
            .categories
            .iter()
            .find(|c| c.id == "app_cache")
            .unwrap();
        assert!(!app_cache.items.iter().any(|i| i.label == "keyring"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    #[ignore = "reads this machine's real cache/temp/trash/browser data; run manually with -- --ignored --nocapture"]
    fn live_scan_against_the_real_system() {
        let scan = scan_with_roots(&CleanupRoots::from_env());
        eprintln!(
            "total reclaimable: {} bytes across {} categories, {} cookie groups",
            scan.total_reclaimable_bytes,
            scan.categories.len(),
            scan.cookie_groups.len()
        );
        let mut recomputed_total = 0u64;
        for category in &scan.categories {
            let category_total: u64 = category.items.iter().map(|i| i.size_bytes).sum();
            assert_eq!(
                category_total, category.size_bytes,
                "a category's reported size must equal the sum of its own real items"
            );
            recomputed_total += category.size_bytes;
            eprintln!(
                "  [{}] size={} selected_by_default={} items={}",
                category.id,
                category.size_bytes,
                category.selected_by_default,
                category.items.len()
            );
            for item in &category.items {
                eprintln!("      {:<40} {} bytes", item.label, item.size_bytes);
            }
        }
        assert_eq!(
            recomputed_total, scan.total_reclaimable_bytes,
            "the scan total must equal the sum of every category's own real size"
        );
        for group in &scan.cookie_groups {
            eprintln!(
                "  cookie group: {} ({}) running={} size={}",
                group.browser,
                group.profile_label.as_deref().unwrap_or("-"),
                group.is_running,
                group.size_bytes
            );
        }
    }
}
