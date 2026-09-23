//! XDG Desktop Entry Autostart discovery and management, following the
//! freedesktop.org "Desktop Application Autostart Specification".
//!
//! Cross-distro, cross-desktop by construction: paths are always resolved
//! from `XDG_CONFIG_HOME`/`XDG_CONFIG_DIRS` (with the documented fallbacks),
//! never hardcoded to `~/.config/autostart`/`/etc/xdg/autostart`, and
//! `OnlyShowIn`/`NotShowIn` are read against `XDG_CURRENT_DESKTOP` rather
//! than assuming GNOME.
//!
//! The general shape of "scan `.desktop` files, respect `Hidden=`, create a
//! user override instead of editing a system file" is a well known XDG
//! pattern; this module's structure was informed by reading the equivalent
//! feature in Super Linux Utility
//! (<https://github.com/sviluppoarte1-lang/superlinuxutility>,
//! GPL-3.0-or-later, `lib/services/startup_apps_analyzer.dart`). No Dart
//! source was copied -- this is an independent Rust implementation with a
//! different safety model: opaque ids instead of frontend-supplied paths,
//! no root/pkexec (autostart entries are always user-writable resources),
//! and an explicit ownership marker so re-enabling only ever removes an
//! override this Toolbox created itself.
use serde::Serialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::Write,
    path::{Path, PathBuf},
};

/// Written into an override file this Toolbox creates in the user autostart
/// directory to hide a system-provided entry, so that re-enabling later can
/// tell "an override M.G itself owns" apart from "a real user file that
/// happens to also disable the same app" and only ever remove the former.
const MANAGED_OVERRIDE_KEY: &str = "X-MGLT-Managed-Override";

fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/"))
}

/// The resolved XDG config locations this module scans, with the documented
/// fallbacks applied. Kept as a small injectable struct (rather than reading
/// environment variables deep inside the scanning code) so tests can exercise
/// custom `XDG_CONFIG_HOME`/`XDG_CONFIG_DIRS` values without mutating the
/// process environment, which would be racy under `cargo test`'s parallel
/// threads.
pub struct XdgDirs {
    pub config_home: PathBuf,
    pub config_dirs: Vec<PathBuf>,
}

impl XdgDirs {
    pub fn from_env() -> Self {
        let config_home = std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .unwrap_or_else(|| home_dir().join(".config"));
        let config_dirs = std::env::var("XDG_CONFIG_DIRS")
            .ok()
            .filter(|v| !v.is_empty())
            .map(|v| {
                v.split(':')
                    .filter(|s| !s.is_empty())
                    .map(PathBuf::from)
                    .collect::<Vec<_>>()
            })
            .filter(|dirs: &Vec<PathBuf>| !dirs.is_empty())
            .unwrap_or_else(|| vec![PathBuf::from("/etc/xdg")]);
        Self {
            config_home,
            config_dirs,
        }
    }

    fn user_autostart_dir(&self) -> PathBuf {
        self.config_home.join("autostart")
    }

    fn system_autostart_dirs(&self) -> Vec<PathBuf> {
        self.config_dirs
            .iter()
            .map(|d| d.join("autostart"))
            .collect()
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AutostartEntry {
    /// Opaque, stable id derived from the `.desktop` filename -- the
    /// frontend only ever sends this back, never a path.
    pub id: String,
    pub name: String,
    pub comment: Option<String>,
    pub enabled: bool,
    /// "user" | "system" -- where the entry's real definition lives, even
    /// while a M.G-managed override currently hides a system entry.
    pub origin: &'static str,
    pub file: String,
    pub exec: String,
    pub icon_name: Option<String>,
    pub desktop_file_name: String,
    pub applies_to_current_desktop: bool,
}

#[derive(Default, Clone)]
struct DesktopEntryRaw {
    values: BTreeMap<String, String>,
}

/// Parses only the `[Desktop Entry]` group of a `.desktop` file, ignoring
/// every other group (e.g. `[Desktop Action ...]`) and every comment line.
/// Returns `None` when the file never declares a `[Desktop Entry]` group at
/// all, which means it is not a valid desktop entry.
fn parse_desktop_entry(text: &str) -> Option<DesktopEntryRaw> {
    let mut in_target_group = false;
    let mut seen_target_group = false;
    let mut values = BTreeMap::new();
    for raw_line in text.lines() {
        let trimmed = raw_line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix('[') {
            if let Some(end) = rest.find(']') {
                let group = &rest[..end];
                in_target_group = group == "Desktop Entry";
                if in_target_group {
                    seen_target_group = true;
                }
            } else {
                in_target_group = false;
            }
            continue;
        }
        if !in_target_group {
            continue;
        }
        if let Some((key, value)) = trimmed.split_once('=') {
            values.insert(key.trim().to_string(), value.trim().to_string());
        }
    }
    seen_target_group.then_some(DesktopEntryRaw { values })
}

fn localized(values: &BTreeMap<String, String>, base: &str) -> Option<String> {
    values
        .get(&format!("{base}[it_IT]"))
        .or_else(|| values.get(&format!("{base}[it]")))
        .or_else(|| values.get(base))
        .cloned()
        .filter(|s| !s.is_empty())
}

fn bool_field(values: &BTreeMap<String, String>, key: &str, default: bool) -> bool {
    values
        .get(key)
        .map(|v| v.eq_ignore_ascii_case("true"))
        .unwrap_or(default)
}

fn list_field(values: &BTreeMap<String, String>, key: &str) -> Vec<String> {
    values
        .get(key)
        .map(|v| {
            v.split(';')
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn current_desktop_names() -> Vec<String> {
    std::env::var("XDG_CURRENT_DESKTOP")
        .ok()
        .map(|v| {
            v.split(':')
                .filter(|s| !s.is_empty())
                .map(|s| s.to_ascii_uppercase())
                .collect()
        })
        .unwrap_or_default()
}

/// `OnlyShowIn`/`NotShowIn` evaluated against the desktops named in
/// `current`. When the current desktop is unknown (`current` empty), this
/// stays permissive: it only drives an informational flag, never whether an
/// entry is administratively enabled, so a management tool never hides
/// entries just because it cannot tell which desktop is running.
fn applies_to_current_desktop(
    only_show_in: &[String],
    not_show_in: &[String],
    current: &[String],
) -> bool {
    if current.is_empty() {
        return true;
    }
    let not: Vec<String> = not_show_in.iter().map(|s| s.to_ascii_uppercase()).collect();
    if !not.is_empty() && not.iter().any(|d| current.contains(d)) {
        return false;
    }
    let only: Vec<String> = only_show_in
        .iter()
        .map(|s| s.to_ascii_uppercase())
        .collect();
    if !only.is_empty() && !only.iter().any(|d| current.contains(d)) {
        return false;
    }
    true
}

/// Strips XDG field codes (`%f`, `%U`, ...) from an `Exec=` value for a
/// clean, informational display string. `%%` is kept as a literal `%`.
fn clean_exec(raw: &str) -> String {
    let mut out = String::new();
    let mut chars = raw.chars();
    while let Some(c) = chars.next() {
        if c == '%' {
            match chars.next() {
                Some('%') => out.push('%'),
                Some(_) => {}
                None => {}
            }
            continue;
        }
        out.push(c);
    }
    out.trim().to_string()
}

fn is_desktop_file(path: &Path) -> bool {
    path.is_file() && path.extension().and_then(|e| e.to_str()) == Some("desktop")
}

fn filename(path: &Path) -> Option<String> {
    path.file_name().map(|n| n.to_string_lossy().to_string())
}

/// Scans the user autostart directory and every `XDG_CONFIG_DIRS` autostart
/// directory (in priority order), and returns the filename -> path maps
/// needed to compute XDG override precedence. Never recurses.
fn discover_desktop_files(
    dirs: &XdgDirs,
) -> (BTreeMap<String, PathBuf>, BTreeMap<String, PathBuf>) {
    let mut system_by_name: BTreeMap<String, PathBuf> = BTreeMap::new();
    for system_dir in dirs.system_autostart_dirs() {
        let Ok(entries) = fs::read_dir(&system_dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if !is_desktop_file(&path) {
                continue;
            }
            if let Some(name) = filename(&path) {
                // First directory in XDG_CONFIG_DIRS order wins, matching the
                // spec's "the first directory listed is the most important".
                system_by_name.entry(name).or_insert(path);
            }
        }
    }
    let mut user_by_name: BTreeMap<String, PathBuf> = BTreeMap::new();
    if let Ok(entries) = fs::read_dir(dirs.user_autostart_dir()) {
        for entry in entries.flatten() {
            let path = entry.path();
            if !is_desktop_file(&path) {
                continue;
            }
            if let Some(name) = filename(&path) {
                user_by_name.insert(name, path);
            }
        }
    }
    (user_by_name, system_by_name)
}

fn entry_id(desktop_file_name: &str) -> String {
    crate::apt_ops::sha256_hex(desktop_file_name.as_bytes())[..16].to_string()
}

/// Builds the full, deduplicated entry list from an injected [`XdgDirs`].
/// The user directory always takes precedence over every system directory
/// for the same filename, so an app never appears twice.
pub fn list_entries_with_dirs(dirs: &XdgDirs) -> Vec<AutostartEntry> {
    let (user_by_name, system_by_name) = discover_desktop_files(dirs);
    let mut names: BTreeSet<String> = user_by_name.keys().cloned().collect();
    names.extend(system_by_name.keys().cloned());
    let current_desktop = current_desktop_names();
    let mut entries = Vec::new();
    for name in names {
        let user_path = user_by_name.get(&name);
        let system_path = system_by_name.get(&name);
        let (effective_path, is_user_file) = match (user_path, system_path) {
            (Some(u), _) => (u.clone(), true),
            (None, Some(s)) => (s.clone(), false),
            (None, None) => continue,
        };
        let Ok(text) = fs::read_to_string(&effective_path) else {
            continue;
        };
        let Some(raw) = parse_desktop_entry(&text) else {
            continue;
        };
        if let Some(kind) = raw.values.get("Type") {
            if kind != "Application" {
                continue;
            }
        }
        let Some(exec_raw) = raw.values.get("Exec").cloned() else {
            // No Exec= means this is not a real autostart entry (e.g. a stray
            // file); nothing meaningful to show or toggle.
            continue;
        };
        let name_display = localized(&raw.values, "Name").unwrap_or_else(|| name.clone());
        let comment = localized(&raw.values, "Comment");
        let hidden = bool_field(&raw.values, "Hidden", false);
        let gnome_enabled = bool_field(&raw.values, "X-GNOME-Autostart-enabled", true);
        let enabled = !hidden && gnome_enabled;
        let only_show_in = list_field(&raw.values, "OnlyShowIn");
        let not_show_in = list_field(&raw.values, "NotShowIn");
        let applies = applies_to_current_desktop(&only_show_in, &not_show_in, &current_desktop);
        let is_managed_override = raw
            .values
            .get(MANAGED_OVERRIDE_KEY)
            .map(|v| v == "true")
            .unwrap_or(false);
        let origin = if is_user_file {
            if is_managed_override && system_path.is_some() {
                "system"
            } else {
                "user"
            }
        } else {
            "system"
        };
        entries.push(AutostartEntry {
            id: entry_id(&name),
            name: name_display,
            comment,
            enabled,
            origin,
            file: effective_path.to_string_lossy().to_string(),
            exec: clean_exec(&exec_raw),
            icon_name: raw.values.get("Icon").cloned().filter(|s| !s.is_empty()),
            desktop_file_name: name,
            applies_to_current_desktop: applies,
        });
    }
    entries.sort_by(|a, b| {
        a.name
            .to_ascii_lowercase()
            .cmp(&b.name.to_ascii_lowercase())
    });
    entries
}

pub fn list_entries() -> Vec<AutostartEntry> {
    list_entries_with_dirs(&XdgDirs::from_env())
}

/// Line-oriented, in-place edit of one key inside the `[Desktop Entry]`
/// group: replaces the key if present, otherwise appends it at the end of
/// the group. Every other line -- including groups other than `[Desktop
/// Entry]` -- is preserved byte-for-byte, so this is lossless for anything
/// this Toolbox does not itself need to change.
fn set_field(text: &str, key: &str, value: &str) -> String {
    let mut in_target = false;
    let mut seen_target = false;
    let mut replaced = false;
    let mut out_lines: Vec<String> = Vec::new();
    for line in text.lines() {
        let trimmed = line.trim_start();
        if let Some(rest) = trimmed.strip_prefix('[') {
            if in_target && !replaced {
                out_lines.push(format!("{key}={value}"));
                replaced = true;
            }
            if let Some(end) = rest.find(']') {
                in_target = &rest[..end] == "Desktop Entry";
                if in_target {
                    seen_target = true;
                }
            } else {
                in_target = false;
            }
            out_lines.push(line.to_string());
            continue;
        }
        if in_target && !replaced {
            if let Some((k, _)) = trimmed.split_once('=') {
                if k.trim() == key {
                    out_lines.push(format!("{key}={value}"));
                    replaced = true;
                    continue;
                }
            }
        }
        out_lines.push(line.to_string());
    }
    if in_target && !replaced {
        out_lines.push(format!("{key}={value}"));
    }
    if !seen_target {
        out_lines.push("[Desktop Entry]".to_string());
        out_lines.push(format!("{key}={value}"));
    }
    let mut result = out_lines.join("\n");
    if text.ends_with('\n') {
        result.push('\n');
    }
    result
}

fn set_hidden_field(text: &str, hidden: bool) -> String {
    set_field(text, "Hidden", if hidden { "true" } else { "false" })
}

/// Atomic write (temp file in the same directory, fsynced, renamed over the
/// target) matching the pattern already used for APT source edits, so an
/// interrupted write can never leave a half-written `.desktop` file behind.
fn write_desktop_file(path: &Path, contents: &str) -> Result<(), String> {
    let dir = path.parent().ok_or("no_parent_directory")?;
    fs::create_dir_all(dir).map_err(|e| format!("mkdir_failed:{e}"))?;
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let temp_path = dir.join(format!(".mgtoolbox-autostart-tmp-{stamp}"));
    {
        let mut file =
            fs::File::create(&temp_path).map_err(|e| format!("temp_write_failed:{e}"))?;
        file.write_all(contents.as_bytes())
            .map_err(|e| format!("temp_write_failed:{e}"))?;
        file.sync_all()
            .map_err(|e| format!("temp_sync_failed:{e}"))?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(path)
            .map(|m| m.permissions().mode())
            .unwrap_or(0o644);
        let _ = fs::set_permissions(&temp_path, fs::Permissions::from_mode(mode));
    }
    fs::rename(&temp_path, path).map_err(|e| format!("rename_failed:{e}"))?;
    if let Ok(dir_handle) = fs::File::open(dir) {
        let _ = dir_handle.sync_all();
    }
    Ok(())
}

/// Enables or disables one autostart entry, identified only by its opaque
/// id. The id is re-resolved to a real filename by re-scanning fresh --
/// never trusted as, or converted from, a path the frontend could have sent.
///
/// * A genuine user-owned file (no M.G marker, or no system file behind it)
///   is edited in place: minimal, lossless, no new file created.
/// * A system-only entry is never written to directly; disabling it copies
///   its content into a new file in the user autostart directory with
///   `Hidden=true` and the M.G ownership marker added.
/// * Re-enabling a M.G-managed override removes only that override file,
///   restoring the system entry's own default state -- never touching the
///   system file itself, and never removing a file this Toolbox did not
///   create.
pub fn set_entry_enabled_with_dirs(
    dirs: &XdgDirs,
    id: &str,
    enabled: bool,
) -> Result<Vec<AutostartEntry>, String> {
    let (user_by_name, system_by_name) = discover_desktop_files(dirs);
    let mut names: BTreeSet<String> = user_by_name.keys().cloned().collect();
    names.extend(system_by_name.keys().cloned());
    let target_name = names
        .into_iter()
        .find(|name| entry_id(name) == id)
        .ok_or_else(|| "autostart_entry_not_found".to_string())?;

    let user_path = user_by_name.get(&target_name);
    let system_path = system_by_name.get(&target_name);

    match (user_path, system_path) {
        (Some(user_file), maybe_system) => {
            let text = fs::read_to_string(user_file).map_err(|e| format!("read_failed:{e}"))?;
            let Some(raw) = parse_desktop_entry(&text) else {
                return Err("invalid_desktop_file".into());
            };
            let is_managed = raw
                .values
                .get(MANAGED_OVERRIDE_KEY)
                .map(|v| v == "true")
                .unwrap_or(false);
            if is_managed && maybe_system.is_some() && enabled {
                fs::remove_file(user_file).map_err(|e| format!("remove_failed:{e}"))?;
            } else {
                let updated = set_hidden_field(&text, !enabled);
                write_desktop_file(user_file, &updated)?;
            }
        }
        (None, Some(system_file)) => {
            if enabled {
                // Already in its default (enabled) state: nothing to do.
                return Ok(list_entries_with_dirs(dirs));
            }
            let original =
                fs::read_to_string(system_file).map_err(|e| format!("read_failed:{e}"))?;
            let mut updated = set_hidden_field(&original, true);
            updated = set_field(&updated, MANAGED_OVERRIDE_KEY, "true");
            let target_dir = dirs.user_autostart_dir();
            fs::create_dir_all(&target_dir).map_err(|e| format!("mkdir_failed:{e}"))?;
            let target_path = target_dir.join(&target_name);
            write_desktop_file(&target_path, &updated)?;
        }
        (None, None) => return Err("autostart_entry_not_found".into()),
    }
    Ok(list_entries_with_dirs(dirs))
}

pub fn set_entry_enabled(id: &str, enabled: bool) -> Result<Vec<AutostartEntry>, String> {
    set_entry_enabled_with_dirs(&XdgDirs::from_env(), id, enabled)
}

#[tauri::command]
pub async fn list_autostart_entries() -> Result<Vec<AutostartEntry>, String> {
    tauri::async_runtime::spawn_blocking(list_entries)
        .await
        .map_err(|_| "autostart_list_failed".to_string())
}

#[tauri::command]
pub async fn set_autostart_entry_enabled(
    id: String,
    enabled: bool,
) -> Result<Vec<AutostartEntry>, String> {
    tauri::async_runtime::spawn_blocking(move || set_entry_enabled(&id, enabled))
        .await
        .map_err(|_| "autostart_set_failed".to_string())?
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
            "mg-toolbox-autostart-{label}-{}-{stamp}",
            std::process::id()
        ))
    }

    fn write_desktop(dir: &Path, filename: &str, contents: &str) {
        fs::create_dir_all(dir).unwrap();
        fs::write(dir.join(filename), contents).unwrap();
    }

    fn dirs_for(root: &Path) -> XdgDirs {
        XdgDirs {
            config_home: root.join("home/.config"),
            config_dirs: vec![root.join("etc-xdg")],
        }
    }

    const SYSTEM_FOO: &str =
        "[Desktop Entry]\nType=Application\nName=System Foo\nExec=system-foo\n";
    const USER_FOO: &str = "[Desktop Entry]\nType=Application\nName=User Foo\nExec=user-foo\n";

    #[test]
    fn a_user_override_takes_precedence_over_a_system_entry_with_the_same_filename() {
        let root = unique_root("precedence");
        let dirs = dirs_for(&root);
        write_desktop(&root.join("etc-xdg/autostart"), "foo.desktop", SYSTEM_FOO);
        write_desktop(
            &root.join("home/.config/autostart"),
            "foo.desktop",
            USER_FOO,
        );
        let entries = list_entries_with_dirs(&dirs);
        assert_eq!(
            entries.len(),
            1,
            "duplicates are never shown as two entries"
        );
        assert_eq!(entries[0].name, "User Foo");
        assert_eq!(entries[0].origin, "user");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn multiple_xdg_config_dirs_are_honoured_in_priority_order() {
        let root = unique_root("multi-dirs");
        let dirs = XdgDirs {
            config_home: root.join("home/.config"),
            config_dirs: vec![root.join("sys1"), root.join("sys2")],
        };
        write_desktop(
            &root.join("sys1/autostart"),
            "bar.desktop",
            "[Desktop Entry]\nType=Application\nName=From sys1\nExec=bar1\n",
        );
        write_desktop(
            &root.join("sys2/autostart"),
            "bar.desktop",
            "[Desktop Entry]\nType=Application\nName=From sys2\nExec=bar2\n",
        );
        let entries = list_entries_with_dirs(&dirs);
        assert_eq!(entries.len(), 1);
        assert_eq!(
            entries[0].name, "From sys1",
            "the first XDG_CONFIG_DIRS entry wins"
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn hidden_true_marks_the_entry_disabled() {
        let root = unique_root("hidden-disabled");
        let dirs = dirs_for(&root);
        write_desktop(
            &root.join("home/.config/autostart"),
            "app.desktop",
            "[Desktop Entry]\nType=Application\nName=App\nExec=app\nHidden=true\n",
        );
        let entries = list_entries_with_dirs(&dirs);
        assert_eq!(entries.len(), 1);
        assert!(!entries[0].enabled);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn x_gnome_autostart_enabled_false_marks_the_entry_disabled() {
        let root = unique_root("gnome-disabled");
        let dirs = dirs_for(&root);
        write_desktop(
            &root.join("home/.config/autostart"),
            "app.desktop",
            "[Desktop Entry]\nType=Application\nName=App\nExec=app\nX-GNOME-Autostart-enabled=false\n",
        );
        let entries = list_entries_with_dirs(&dirs);
        assert_eq!(entries.len(), 1);
        assert!(!entries[0].enabled);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn only_show_in_excludes_a_non_matching_current_desktop() {
        let only = vec!["GNOME".to_string()];
        let not = Vec::new();
        assert!(!applies_to_current_desktop(
            &only,
            &not,
            &["KDE".to_string()]
        ));
        assert!(applies_to_current_desktop(
            &only,
            &not,
            &["GNOME".to_string()]
        ));
    }

    #[test]
    fn not_show_in_excludes_a_matching_current_desktop() {
        let only = Vec::new();
        let not = vec!["GNOME".to_string()];
        assert!(!applies_to_current_desktop(
            &only,
            &not,
            &["GNOME".to_string()]
        ));
        assert!(applies_to_current_desktop(
            &only,
            &not,
            &["KDE".to_string()]
        ));
    }

    #[test]
    fn unknown_current_desktop_stays_permissive() {
        let only = vec!["GNOME".to_string()];
        assert!(applies_to_current_desktop(&only, &[], &[]));
    }

    #[test]
    fn disabling_a_user_owned_entry_edits_it_in_place_without_creating_an_override() {
        let root = unique_root("user-disable");
        let dirs = dirs_for(&root);
        write_desktop(
            &root.join("home/.config/autostart"),
            "user.desktop",
            USER_FOO,
        );
        let entries = list_entries_with_dirs(&dirs);
        let id = entries[0].id.clone();
        let updated = set_entry_enabled_with_dirs(&dirs, &id, false).unwrap();
        assert!(!updated[0].enabled);
        let files: Vec<_> = fs::read_dir(root.join("home/.config/autostart"))
            .unwrap()
            .flatten()
            .collect();
        assert_eq!(
            files.len(),
            1,
            "no extra override file is created for a user entry"
        );
        let contents =
            fs::read_to_string(root.join("home/.config/autostart/user.desktop")).unwrap();
        assert!(contents.contains("Hidden=true"));
        assert!(
            contents.contains("Name=User Foo"),
            "every other key is preserved"
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn disabling_a_system_entry_creates_a_managed_override_without_touching_the_system_file() {
        let root = unique_root("system-disable");
        let dirs = dirs_for(&root);
        write_desktop(&root.join("etc-xdg/autostart"), "sys.desktop", SYSTEM_FOO);
        let before = fs::read_to_string(root.join("etc-xdg/autostart/sys.desktop")).unwrap();
        let entries = list_entries_with_dirs(&dirs);
        assert_eq!(entries[0].origin, "system");
        let id = entries[0].id.clone();
        let updated = set_entry_enabled_with_dirs(&dirs, &id, false).unwrap();
        assert!(!updated[0].enabled);
        assert_eq!(updated[0].origin, "system");
        let after = fs::read_to_string(root.join("etc-xdg/autostart/sys.desktop")).unwrap();
        assert_eq!(before, after, "the system file is never modified");
        let override_contents =
            fs::read_to_string(root.join("home/.config/autostart/sys.desktop")).unwrap();
        assert!(override_contents.contains("Hidden=true"));
        assert!(override_contents.contains(MANAGED_OVERRIDE_KEY));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn enabling_a_managed_override_removes_only_that_override_file() {
        let root = unique_root("system-reenable");
        let dirs = dirs_for(&root);
        write_desktop(&root.join("etc-xdg/autostart"), "sys.desktop", SYSTEM_FOO);
        let entries = list_entries_with_dirs(&dirs);
        let id = entries[0].id.clone();
        set_entry_enabled_with_dirs(&dirs, &id, false).unwrap();
        assert!(root.join("home/.config/autostart/sys.desktop").exists());
        let updated = set_entry_enabled_with_dirs(&dirs, &id, true).unwrap();
        assert!(updated[0].enabled);
        assert_eq!(updated[0].origin, "system");
        assert!(
            !root.join("home/.config/autostart/sys.desktop").exists(),
            "the M.G-owned override is removed"
        );
        assert!(
            root.join("etc-xdg/autostart/sys.desktop").exists(),
            "the system file itself is untouched and still present"
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_real_user_file_that_happens_to_disable_a_system_app_is_never_deleted_on_enable() {
        let root = unique_root("real-user-override");
        let dirs = dirs_for(&root);
        write_desktop(&root.join("etc-xdg/autostart"), "sys.desktop", SYSTEM_FOO);
        // A real user file, without the M.G marker (e.g. hand-edited by the user).
        write_desktop(
            &root.join("home/.config/autostart"),
            "sys.desktop",
            "[Desktop Entry]\nType=Application\nName=System Foo\nExec=system-foo\nHidden=true\n",
        );
        let entries = list_entries_with_dirs(&dirs);
        assert_eq!(entries[0].origin, "user");
        let id = entries[0].id.clone();
        let updated = set_entry_enabled_with_dirs(&dirs, &id, true).unwrap();
        assert!(updated[0].enabled);
        assert!(
            root.join("home/.config/autostart/sys.desktop").exists(),
            "a real (non-M.G-owned) user file is edited, never deleted"
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn enable_disable_round_trip_reflects_in_a_fresh_scan() {
        let root = unique_root("round-trip");
        let dirs = dirs_for(&root);
        write_desktop(&root.join("etc-xdg/autostart"), "app.desktop", SYSTEM_FOO);
        let id = list_entries_with_dirs(&dirs)[0].id.clone();
        assert!(list_entries_with_dirs(&dirs)[0].enabled);
        set_entry_enabled_with_dirs(&dirs, &id, false).unwrap();
        assert!(!list_entries_with_dirs(&dirs)[0].enabled);
        set_entry_enabled_with_dirs(&dirs, &id, true).unwrap();
        assert!(list_entries_with_dirs(&dirs)[0].enabled);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn set_entry_enabled_never_accepts_a_path_only_an_opaque_id() {
        let root = unique_root("no-path");
        let dirs = dirs_for(&root);
        write_desktop(
            &root.join("home/.config/autostart"),
            "app.desktop",
            USER_FOO,
        );
        let result = set_entry_enabled_with_dirs(&dirs, "not-a-real-id", false);
        match result {
            Err(reason) => assert_eq!(reason, "autostart_entry_not_found"),
            Ok(_) => panic!("expected an error for an unknown id"),
        }
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn filesystem_scan_ignores_non_desktop_files() {
        let root = unique_root("ignore-non-desktop");
        let dirs = dirs_for(&root);
        fs::create_dir_all(root.join("home/.config/autostart")).unwrap();
        fs::write(
            root.join("home/.config/autostart/notes.txt"),
            "not a desktop file",
        )
        .unwrap();
        let entries = list_entries_with_dirs(&dirs);
        assert!(entries.is_empty());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn an_entry_without_exec_is_skipped() {
        let root = unique_root("no-exec");
        let dirs = dirs_for(&root);
        write_desktop(
            &root.join("home/.config/autostart"),
            "broken.desktop",
            "[Desktop Entry]\nType=Application\nName=Broken\n",
        );
        let entries = list_entries_with_dirs(&dirs);
        assert!(entries.is_empty());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn italian_localized_name_is_preferred_when_present() {
        let root = unique_root("localized-name");
        let dirs = dirs_for(&root);
        write_desktop(
            &root.join("home/.config/autostart"),
            "app.desktop",
            "[Desktop Entry]\nType=Application\nName=App\nName[it]=Applicazione\nExec=app\n",
        );
        let entries = list_entries_with_dirs(&dirs);
        assert_eq!(entries[0].name, "Applicazione");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn exec_field_codes_are_stripped_for_display() {
        assert_eq!(
            clean_exec("app --flag %f --other %U"),
            "app --flag  --other"
        );
        assert_eq!(clean_exec("app %%literal"), "app %literal");
    }

    #[test]
    #[ignore = "reads this machine's real XDG autostart configuration; run manually with -- --ignored --nocapture"]
    fn live_list_autostart_entries_against_the_real_system() {
        let entries = list_entries();
        eprintln!("found {} real autostart entries:", entries.len());
        for entry in &entries {
            eprintln!(
                "  [{}] {:<28} enabled={:<5} origin={:<6} applies_to_this_desktop={:<5} file={}",
                &entry.id[..4],
                entry.name,
                entry.enabled,
                entry.origin,
                entry.applies_to_current_desktop,
                entry.file,
            );
        }
    }

    #[test]
    #[ignore = "mutates this machine's real ~/.config/autostart; run manually with -- --ignored --nocapture"]
    fn live_disable_then_enable_round_trip_on_a_real_safe_entry() {
        // A deliberately low-risk, well-known system entry (just a periodic
        // update-notification popup, never a login/session-critical
        // service) -- present on stock Ubuntu/GNOME installs. Skips
        // cleanly if this machine does not have it, rather than picking an
        // arbitrary real app.
        let dirs = XdgDirs::from_env();
        let before = list_entries_with_dirs(&dirs);
        let Some(target) = before
            .iter()
            .find(|e| e.desktop_file_name == "update-notifier.desktop")
        else {
            eprintln!(
                "update-notifier.desktop not present on this machine; skipping live round trip"
            );
            return;
        };
        let original_enabled = target.enabled;
        let id = target.id.clone();
        eprintln!(
            "before: name={} enabled={} origin={}",
            target.name, target.enabled, target.origin
        );

        let disabled = set_entry_enabled_with_dirs(&dirs, &id, false).unwrap();
        let disabled_entry = disabled.iter().find(|e| e.id == id).unwrap();
        assert!(
            !disabled_entry.enabled,
            "the entry must report disabled right after disabling it"
        );
        eprintln!(
            "after disable: enabled={} origin={} file={}",
            disabled_entry.enabled, disabled_entry.origin, disabled_entry.file
        );

        let restored = set_entry_enabled_with_dirs(&dirs, &id, true).unwrap();
        let restored_entry = restored.iter().find(|e| e.id == id).unwrap();
        assert!(
            restored_entry.enabled,
            "the entry must report enabled again after re-enabling it"
        );
        eprintln!(
            "after enable: enabled={} origin={} file={}",
            restored_entry.enabled, restored_entry.origin, restored_entry.file
        );

        assert_eq!(
            restored_entry.enabled, original_enabled,
            "the machine must end in exactly the state it started in"
        );
    }
}
