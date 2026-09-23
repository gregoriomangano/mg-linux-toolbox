//! Shared, closed operations for the APT repository backend. Compiled into
//! both the application and the privileged repository helper, the same way
//! `advanced_ops.rs` is shared with the performance helper: the helper never
//! trusts a path or byte range handed to it -- it independently re-derives
//! the allow-listed APT source files and re-parses the target itself before
//! writing anything.
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Write,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    process::Command,
    time::{SystemTime, UNIX_EPOCH},
};

/// Root-writable location for pre-write backups. Root-owned and mode 0700:
/// only the helper (running as root through polkit) ever writes here.
pub const BACKUP_DIR: &str = "/var/lib/mg-linux-toolbox/backups/apt";

fn apt_config_value(key: &str, item: &str) -> Option<String> {
    let output = Command::new("apt-config")
        .args(["shell", key, item])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let prefix = format!("{key}='");
    let line = text.lines().find(|line| line.starts_with(&prefix))?;
    let rest = line.strip_prefix(&prefix)?;
    Some(
        rest.strip_suffix('\'')
            .unwrap_or(rest)
            .replace("'\\''", "'"),
    )
}

fn join(root: &str, parts: &[&str]) -> PathBuf {
    let mut path = PathBuf::from(if root.is_empty() { "/" } else { root });
    for part in parts {
        for segment in part.split('/').filter(|s| !s.is_empty()) {
            path.push(segment);
        }
    }
    path
}

/// The real, current APT configuration paths -- never hardcoded to
/// `/etc/apt`, always re-derived from `apt-config` so a distro or local
/// policy that points elsewhere is respected. Falls back to the documented
/// APT defaults only when `apt-config` itself is unavailable.
pub fn apt_dirs() -> (PathBuf, PathBuf) {
    let root = apt_config_value("RT", "Dir").unwrap_or_else(|| "/".into());
    let etc = apt_config_value("ET", "Dir::Etc").unwrap_or_else(|| "etc/apt".into());
    let sourcelist =
        apt_config_value("SL", "Dir::Etc::sourcelist").unwrap_or_else(|| "sources.list".into());
    let sourceparts =
        apt_config_value("SP", "Dir::Etc::sourceparts").unwrap_or_else(|| "sources.list.d".into());
    (
        join(&root, &[&etc, &sourcelist]),
        join(&root, &[&etc, &sourceparts]),
    )
}

/// Every file this backend may read or write: the single `sources.list` (if
/// it exists) plus every `*.list`/`*.sources` file directly inside the
/// sourceparts directory (no recursion).
pub fn discover_source_files() -> Vec<PathBuf> {
    let (sourcelist, sourceparts) = apt_dirs();
    let mut files = Vec::new();
    if sourcelist.is_file() {
        files.push(sourcelist);
    }
    if let Ok(entries) = fs::read_dir(&sourceparts) {
        for entry in entries.flatten() {
            let path = entry.path();
            let relevant = path
                .extension()
                .and_then(|ext| ext.to_str())
                .is_some_and(|ext| ext == "list" || ext == "sources");
            if relevant && path.is_file() {
                files.push(path);
            }
        }
    }
    files.sort();
    files
}

/// True only when `candidate` resolves, after following any symlinks, to a
/// path that is itself one of the currently discovered, allow-listed source
/// files. This is the one check every mutating operation performs before
/// touching anything on disk; it is never skipped.
pub fn is_allowed_source_file(candidate: &Path) -> bool {
    let Ok(real) = fs::canonicalize(candidate) else {
        return false;
    };
    discover_source_files()
        .iter()
        .filter_map(|allowed| fs::canonicalize(allowed).ok())
        .any(|allowed| allowed == real)
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hasher
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn backup_meta_path(backup_path: &Path) -> PathBuf {
    backup_path.with_extension("meta.json")
}

/// Copies `original_path`'s current bytes into `BACKUP_DIR` and writes a
/// small JSON sidecar describing the operation, before any modification is
/// made. Never overwrites a previous backup: every call creates a new,
/// timestamped pair.
fn write_backup(
    original_path: &Path,
    original_bytes: &[u8],
    operation: &str,
) -> Result<(), String> {
    fs::create_dir_all(BACKUP_DIR).map_err(|e| format!("backup_dir_failed:{e}"))?;
    let _ = fs::set_permissions(BACKUP_DIR, fs::Permissions::from_mode(0o700));
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let sanitized: String = original_path
        .to_string_lossy()
        .chars()
        .map(|c| if c == '/' { '_' } else { c })
        .collect();
    let backup_path = Path::new(BACKUP_DIR).join(format!("{sanitized}.{stamp}.bak"));
    fs::write(&backup_path, original_bytes).map_err(|e| format!("backup_write_failed:{e}"))?;
    let _ = fs::set_permissions(&backup_path, fs::Permissions::from_mode(0o600));
    let meta = serde_json::json!({
        "originalPath": original_path.to_string_lossy(),
        "timestamp": stamp,
        "sha256Before": sha256_hex(original_bytes),
        "operation": operation,
    });
    fs::write(backup_meta_path(&backup_path), meta.to_string())
        .map_err(|e| format!("backup_meta_failed:{e}"))?;
    Ok(())
}

/// The most recent backup recorded for `original_path`, if any, as
/// (bytes-before, backup file path).
fn latest_backup(original_path: &Path) -> Option<(Vec<u8>, PathBuf)> {
    let target = original_path.to_string_lossy().to_string();
    let mut candidates: Vec<(u128, PathBuf)> = fs::read_dir(BACKUP_DIR)
        .ok()?
        .flatten()
        .filter(|entry| entry.path().extension().and_then(|e| e.to_str()) == Some("bak"))
        .filter_map(|entry| {
            let meta_path = backup_meta_path(&entry.path());
            let meta: serde_json::Value =
                serde_json::from_str(&fs::read_to_string(&meta_path).ok()?).ok()?;
            if meta.get("originalPath").and_then(|v| v.as_str()) != Some(target.as_str()) {
                return None;
            }
            let stamp = meta.get("timestamp").and_then(|v| v.as_u64()).unwrap_or(0) as u128;
            Some((stamp, entry.path()))
        })
        .collect();
    candidates.sort_by_key(|(stamp, _)| *stamp);
    let (_, path) = candidates.pop()?;
    let bytes = fs::read(&path).ok()?;
    Some((bytes, path))
}

/// Writes `new_bytes` to `target` atomically: a temp file in the *same*
/// directory (so the rename cannot cross filesystems), fsynced, renamed over
/// the target, then the directory itself is fsynced. Preserves the
/// original's owner and mode. Never follows a symlink at `target` onto a
/// path outside the allow-listed set (the caller already verified that with
/// [`is_allowed_source_file`] against the pre-write canonical path).
fn atomic_write_preserving_metadata(target: &Path, new_bytes: &[u8]) -> Result<(), String> {
    let metadata = fs::metadata(target).map_err(|e| format!("stat_failed:{e}"))?;
    let dir = target.parent().ok_or("no_parent_directory")?;
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let temp_path = dir.join(format!(
        ".{}.mgtoolbox-tmp-{stamp}",
        target
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("apt-source")
    ));
    {
        let mut file =
            fs::File::create(&temp_path).map_err(|e| format!("temp_write_failed:{e}"))?;
        file.write_all(new_bytes)
            .map_err(|e| format!("temp_write_failed:{e}"))?;
        file.sync_all()
            .map_err(|e| format!("temp_sync_failed:{e}"))?;
    }
    let _ = fs::set_permissions(&temp_path, metadata.permissions());
    fs::rename(&temp_path, target).map_err(|e| format!("rename_failed:{e}"))?;
    if let Ok(dir_handle) = fs::File::open(dir) {
        let _ = dir_handle.sync_all();
    }
    Ok(())
}

/// One minimal, span-based edit: replace `original[start..end]` with
/// `replacement`. Never touches a single byte outside that span.
#[derive(Debug, PartialEq)]
pub struct TextEdit {
    pub start: usize,
    pub end: usize,
    pub replacement: Vec<u8>,
}

fn apply_edit(original: &[u8], edit: &TextEdit) -> Result<Vec<u8>, String> {
    if edit.start > edit.end || edit.end > original.len() {
        return Err("edit_out_of_bounds".into());
    }
    let mut result = Vec::with_capacity(original.len() + edit.replacement.len());
    result.extend_from_slice(&original[..edit.start]);
    result.extend_from_slice(&edit.replacement);
    result.extend_from_slice(&original[edit.end..]);
    Ok(result)
}

/// Applies one minimal edit to an allow-listed file: verifies the file is
/// still one of the discovered APT source files, re-reads it, checks the
/// caller's expected revision against the real current hash (rejecting any
/// external change instead of overwriting it), backs it up, writes the
/// edited bytes atomically, then re-reads and re-hashes to confirm the
/// write really landed. Returns the new revision on success.
pub fn apply_source_edit(
    file: &Path,
    expected_revision: &str,
    operation: &str,
    build_edit: impl FnOnce(&[u8]) -> Result<TextEdit, String>,
) -> Result<String, String> {
    if !is_allowed_source_file(file) {
        return Err("file_not_allowed".into());
    }
    let current = fs::read(file).map_err(|e| format!("read_failed:{e}"))?;
    if sha256_hex(&current) != expected_revision {
        return Err("revision_mismatch".into());
    }
    let edit = build_edit(&current)?;
    let updated = apply_edit(&current, &edit)?;
    write_backup(file, &current, operation)?;
    atomic_write_preserving_metadata(file, &updated)?;
    let verify = fs::read(file).map_err(|e| format!("post_write_read_failed:{e}"))?;
    if verify != updated {
        return Err("post_write_verification_failed".into());
    }
    Ok(sha256_hex(&verify))
}

/// Restores the most recent backup recorded for `file`, with the same
/// revision check and post-write verification as [`apply_source_edit`].
pub fn restore_latest_backup(file: &Path, expected_revision: &str) -> Result<String, String> {
    if !is_allowed_source_file(file) {
        return Err("file_not_allowed".into());
    }
    let current = fs::read(file).map_err(|e| format!("read_failed:{e}"))?;
    if sha256_hex(&current) != expected_revision {
        return Err("revision_mismatch".into());
    }
    let (backup_bytes, _backup_path) = latest_backup(file).ok_or("no_backup_available")?;
    write_backup(file, &current, "restore")?;
    atomic_write_preserving_metadata(file, &backup_bytes)?;
    let verify = fs::read(file).map_err(|e| format!("post_write_read_failed:{e}"))?;
    if verify != backup_bytes {
        return Err("post_write_verification_failed".into());
    }
    Ok(sha256_hex(&verify))
}

/// `true` when a restorable backup exists for `file` -- used to show/hide
/// "Ripristina ultima modifica" without exposing backup internals.
pub fn has_backup(file: &Path) -> bool {
    latest_backup(file).is_some()
}

/// The exact marker this Toolbox writes at the very start of a one-line
/// `.list` entry it has disabled. Chosen to be unmistakable and never
/// something a human would type by accident, so re-enabling only ever acts
/// on entries this Toolbox itself turned off (see `apt_sources.rs`).
pub const LIST_DISABLE_MARKER: &str = "#MGLT-DISABLED# ";

/// Installed path of the privileged repository helper, checked by the
/// application before invoking `pkexec` and matched by the polkit action's
/// `org.freedesktop.policykit.exec.path` annotation.
pub const REPOSITORY_HELPER_PATH: &str =
    "/usr/lib/mg-linux-toolbox/mg-linux-toolbox-repository-helper";

fn looks_like_deb_line(line: &str) -> bool {
    let s = line.trim_start();
    s.starts_with("deb ")
        || s.starts_with("deb\t")
        || s.starts_with("deb[")
        || s.starts_with("deb-src ")
        || s.starts_with("deb-src\t")
        || s.starts_with("deb-src[")
}

/// Classifies one physical line of a one-line `.list` file:
/// `(enabled, writable, note, content_start_byte_within_line)`. `writable`
/// is only true for lines this Toolbox may toggle: an active `deb`/`deb-src`
/// line, or one it has itself disabled with [`LIST_DISABLE_MARKER`]. A line
/// a human commented out by hand (any other `#` form) is reported as
/// disabled but never writable, so "Attiva" can never turn someone else's
/// intentional comment back on.
pub fn classify_list_line(line: &str) -> Option<(bool, bool, Option<&'static str>, usize)> {
    if let Some(rest) = line.strip_prefix(LIST_DISABLE_MARKER) {
        return looks_like_deb_line(rest).then_some((false, true, None, LIST_DISABLE_MARKER.len()));
    }
    if looks_like_deb_line(line) {
        return Some((true, true, None, 0));
    }
    let trimmed = line.trim_start();
    let hashes = trimmed.trim_start_matches('#');
    if trimmed.len() != hashes.len() {
        let after = hashes.trim_start();
        if looks_like_deb_line(after) {
            let content_start = line.len() - after.len();
            return Some((false, false, Some("manual"), content_start));
        }
    }
    None
}

/// `(line_start_byte, line_end_byte_excl_newline, line_text)` for every line
/// in `text`, so every later span computed from these offsets stays
/// byte-accurate against the original file.
pub fn lines_with_offsets(text: &str) -> Vec<(usize, usize, &str)> {
    let mut out = Vec::new();
    let mut start = 0usize;
    for part in text.split('\n') {
        let end = start + part.len();
        out.push((start, end, part));
        start = end + 1;
    }
    out
}

/// One Deb822 field: its lower-cased key, the trimmed value text of its
/// first line (for display), whether it had continuation lines (in which
/// case `value_byte_range` is not meaningful for editing), the exact byte
/// range of the value on its own first line, and the byte offset right
/// after this field's last line (its own or its last continuation).
pub struct StanzaField {
    pub key_lower: String,
    pub value_first_line: String,
    pub multiline: bool,
    pub value_byte_range: (usize, usize),
    pub field_end_byte: usize,
}

/// Splits one Deb822 stanza (a run of lines with no blank line inside it)
/// into its fields, folding indented continuation lines into the field
/// that precedes them exactly as RFC822/Deb822 requires -- so a multi-line
/// `Signed-By` PGP block is recognised as one field and never mistaken for
/// a stanza boundary or a second field.
pub fn parse_stanza_fields(lines: &[(usize, usize, &str)]) -> Vec<StanzaField> {
    let mut fields: Vec<StanzaField> = Vec::new();
    for &(line_start, line_end, line) in lines {
        if line.starts_with(' ') || line.starts_with('\t') {
            if let Some(last) = fields.last_mut() {
                last.multiline = true;
                last.field_end_byte = line_end;
            }
            continue;
        }
        if line.trim_start().starts_with('#') || line.trim().is_empty() {
            continue;
        }
        let Some((key, value)) = line.split_once(':') else {
            continue;
        };
        let value_trimmed = value.trim_start();
        let leading_ws = value.len() - value_trimmed.len();
        let value_start = line_start + key.len() + 1 + leading_ws;
        fields.push(StanzaField {
            key_lower: key.trim().to_ascii_lowercase(),
            value_first_line: value_trimmed.trim_end().to_string(),
            multiline: false,
            value_byte_range: (value_start, line_end),
            field_end_byte: line_end,
        });
    }
    fields
}

pub fn field<'a>(fields: &'a [StanzaField], key: &str) -> Option<&'a StanzaField> {
    fields.iter().find(|f| f.key_lower == key)
}

pub fn split_values(fields: &[StanzaField], key: &str) -> Vec<String> {
    field(fields, key)
        .filter(|f| !f.multiline)
        .map(|f| {
            f.value_first_line
                .split_whitespace()
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

/// Groups a file's lines into Deb822 stanzas: consecutive non-empty lines,
/// separated by one or more truly-empty lines (never by a whitespace-only
/// continuation line, which Deb822 uses to fold an empty value).
pub fn stanza_chunks<'a>(
    lines: &'a [(usize, usize, &'a str)],
) -> Vec<&'a [(usize, usize, &'a str)]> {
    let mut chunks = Vec::new();
    let mut start = 0usize;
    for (i, &(_, _, line)) in lines.iter().enumerate() {
        if line.is_empty() {
            if i > start {
                chunks.push(&lines[start..i]);
            }
            start = i + 1;
        }
    }
    if start < lines.len() {
        chunks.push(&lines[start..]);
    }
    chunks
}

fn is_real_stanza(chunk: &[(usize, usize, &str)]) -> bool {
    !chunk
        .iter()
        .all(|(_, _, line)| line.trim().is_empty() || line.trim_start().starts_with('#'))
}

/// Builds the minimal edit that flips one `.list` line's enabled state, by
/// re-parsing `text` fresh and counting only the recognised repository
/// lines (matching how `apt_sources::list_repositories` assigns `index`).
/// Refuses a manually-commented line instead of guessing intent.
pub fn build_list_toggle_edit(text: &[u8], index: usize, enable: bool) -> Result<TextEdit, String> {
    let text = std::str::from_utf8(text).map_err(|_| "invalid_utf8".to_string())?;
    let mut seen = 0usize;
    for (line_start, _line_end, line) in lines_with_offsets(text) {
        let Some((current_enabled, writable, _note, _content_start)) = classify_list_line(line)
        else {
            continue;
        };
        if seen != index {
            seen += 1;
            continue;
        }
        if !writable {
            return Err("control_managed".into());
        }
        return match (current_enabled, enable) {
            (true, false) => Ok(TextEdit {
                start: line_start,
                end: line_start,
                replacement: LIST_DISABLE_MARKER.as_bytes().to_vec(),
            }),
            (false, true) => Ok(TextEdit {
                start: line_start,
                end: line_start + LIST_DISABLE_MARKER.len(),
                replacement: Vec::new(),
            }),
            _ => Err("already_in_requested_state".into()),
        };
    }
    Err("repository_not_found".into())
}

/// Builds the minimal edit that flips one Deb822 stanza's `Enabled` state,
/// by re-parsing `text` fresh and counting only stanzas that declare
/// `Types` (matching `apt_sources::list_repositories`'s `index`).
pub fn build_sources_toggle_edit(
    text: &[u8],
    index: usize,
    enable: bool,
) -> Result<TextEdit, String> {
    let text = std::str::from_utf8(text).map_err(|_| "invalid_utf8".to_string())?;
    let all_lines = lines_with_offsets(text);
    let mut seen = 0usize;
    for chunk in stanza_chunks(&all_lines) {
        if !is_real_stanza(chunk) {
            continue;
        }
        let fields = parse_stanza_fields(chunk);
        if field(&fields, "types").is_none() {
            continue;
        }
        if seen != index {
            seen += 1;
            continue;
        }
        let enabled_field = field(&fields, "enabled");
        return match enabled_field.map(|f| f.value_first_line.as_str()) {
            Some("yes") if !enable => Ok(TextEdit {
                start: enabled_field.unwrap().value_byte_range.0,
                end: enabled_field.unwrap().value_byte_range.1,
                replacement: b"no".to_vec(),
            }),
            Some("no") if enable => Ok(TextEdit {
                start: enabled_field.unwrap().value_byte_range.0,
                end: enabled_field.unwrap().value_byte_range.1,
                replacement: b"yes".to_vec(),
            }),
            None if !enable => {
                let insert_at = field(&fields, "types")
                    .map(|f| f.field_end_byte)
                    .unwrap_or_else(|| chunk.last().map(|(_, end, _)| *end).unwrap_or(0));
                let insert_at = (insert_at + 1).min(text.len());
                Ok(TextEdit {
                    start: insert_at,
                    end: insert_at,
                    replacement: b"Enabled: no\n".to_vec(),
                })
            }
            Some("no") | Some("yes") | None => Err("already_in_requested_state".into()),
            Some(_) => Err("control_managed".into()),
        };
    }
    Err("repository_not_found".into())
}

/// The two allow-listed, closed repository operations. The frontend never
/// names a file, a byte offset, or a shell command -- only these two
/// operation names ever reach [`apply_operation`].
pub fn is_operation(name: &str) -> bool {
    matches!(name, "apt-repo-set" | "apt-repo-restore")
}

/// Dispatches one allow-listed operation. `format`/`direction` are matched
/// against fixed literals before anything is touched; `index` is parsed as
/// a bounds-checked integer, never used as a raw offset into the file.
pub fn apply_operation(args: &[String]) -> Result<(), String> {
    match args {
        [action, file, format, index, direction, revision] if action == "apt-repo-set" => {
            let file = Path::new(file);
            let index: usize = index.parse().map_err(|_| "invalid_index".to_string())?;
            let enable = match direction.as_str() {
                "enable" => true,
                "disable" => false,
                _ => return Err("invalid_direction".into()),
            };
            apply_source_edit(file, revision, direction, |bytes| match format.as_str() {
                "list" => build_list_toggle_edit(bytes, index, enable),
                "sources" => build_sources_toggle_edit(bytes, index, enable),
                _ => Err("invalid_format".into()),
            })
            .map(|_| ())
        }
        [action, file, revision] if action == "apt-repo-restore" => {
            restore_latest_backup(Path::new(file), revision).map(|_| ())
        }
        _ => Err("invalid_operation".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha256_matches_a_known_vector() {
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn apply_edit_only_touches_the_given_span() {
        let original = b"deb http://example/ suite main\n";
        let edit = TextEdit {
            start: 0,
            end: 0,
            replacement: LIST_DISABLE_MARKER.as_bytes().to_vec(),
        };
        let updated = apply_edit(original, &edit).unwrap();
        assert_eq!(
            updated,
            b"#MGLT-DISABLED# deb http://example/ suite main\n".to_vec()
        );
    }

    #[test]
    fn apply_edit_rejects_an_out_of_bounds_span() {
        let original = b"short";
        let edit = TextEdit {
            start: 2,
            end: 100,
            replacement: Vec::new(),
        };
        assert_eq!(
            apply_edit(original, &edit),
            Err("edit_out_of_bounds".into())
        );
    }

    #[test]
    fn classifies_active_disabled_and_manual_list_lines() {
        assert_eq!(
            classify_list_line("deb http://a/ suite main"),
            Some((true, true, None, 0))
        );
        assert_eq!(
            classify_list_line("#MGLT-DISABLED# deb http://a/ suite main"),
            Some((false, true, None, LIST_DISABLE_MARKER.len()))
        );
        let (enabled, writable, note, _) =
            classify_list_line("# deb http://a/ suite main").unwrap();
        assert!(!enabled && !writable);
        assert_eq!(note, Some("manual"));
        assert_eq!(
            classify_list_line("# just a human comment, not a repo"),
            None
        );
        assert_eq!(classify_list_line(""), None);
    }

    #[test]
    fn build_list_toggle_edit_only_touches_the_marker_span() {
        let text = b"deb http://a/ suite main\n";
        let edit = build_list_toggle_edit(text, 0, false).unwrap();
        assert_eq!(edit.start, 0);
        assert_eq!(edit.end, 0);
        assert_eq!(edit.replacement, LIST_DISABLE_MARKER.as_bytes());
    }

    #[test]
    fn build_list_toggle_edit_refuses_a_manually_commented_line() {
        let text = b"# deb http://a/ suite main\n";
        assert_eq!(
            build_list_toggle_edit(text, 0, true),
            Err("control_managed".into())
        );
    }

    #[test]
    fn build_sources_toggle_edit_inserts_enabled_no_right_after_types_when_absent() {
        let text = b"Types: deb\nURIs: http://a/\nSuites: stable\nComponents: main\n";
        let edit = build_sources_toggle_edit(text, 0, false).unwrap();
        let mut updated = text.to_vec();
        updated.splice(edit.start..edit.end, edit.replacement);
        let updated = String::from_utf8(updated).unwrap();
        assert_eq!(
            updated,
            "Types: deb\nEnabled: no\nURIs: http://a/\nSuites: stable\nComponents: main\n"
        );
    }

    #[test]
    fn build_sources_toggle_edit_flips_an_existing_value_in_place() {
        let text = b"Types: deb\nURIs: http://a/\nSuites: stable\nComponents: main\nEnabled: no\n";
        let edit = build_sources_toggle_edit(text, 0, true).unwrap();
        let mut updated = text.to_vec();
        updated.splice(edit.start..edit.end, edit.replacement);
        let updated = String::from_utf8(updated).unwrap();
        assert_eq!(
            updated,
            "Types: deb\nURIs: http://a/\nSuites: stable\nComponents: main\nEnabled: yes\n"
        );
    }

    #[test]
    fn discover_source_files_only_returns_list_and_sources_extensions() {
        // Structural check: whatever this machine has, every path returned
        // must end in .list or .sources or be exactly the sourcelist file.
        let (sourcelist, _) = apt_dirs();
        for file in discover_source_files() {
            let ext = file.extension().and_then(|e| e.to_str());
            assert!(
                file == sourcelist || ext == Some("list") || ext == Some("sources"),
                "unexpected file in discovery: {file:?}"
            );
        }
    }
}
