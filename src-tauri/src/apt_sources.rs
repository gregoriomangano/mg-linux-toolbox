//! APT repository discovery and display model (unprivileged side).
//!
//! This module never needs root: every file under the paths `apt-config`
//! reports is world-readable, so detection/parsing/listing all run as the
//! normal user. Only mutation goes through [`crate::apt_ops`] and the
//! privileged repository helper, which independently re-parses and
//! re-validates everything -- this module's output is a display
//! convenience, never a trusted instruction to the helper.
//!
//! This is the first `RepositoryBackend`. The frontend only ever sees the
//! generic [`AptRepository`] shape below; a future DNF5/Pacman backend would
//! plug in beside this module without the page changing.
use crate::apt_ops::{self, StanzaField};
use serde::Serialize;
use std::path::Path;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AptRepository {
    pub id: String,
    pub backend: &'static str,
    pub format: &'static str,
    pub file: String,
    pub index: usize,
    pub enabled: bool,
    pub writable: bool,
    pub note: Option<&'static str>,
    pub name: String,
    pub subtitle: Option<String>,
    pub kind: String,
    pub uris: Vec<String>,
    pub suites: Vec<String>,
    pub components: Vec<String>,
    pub architectures: Vec<String>,
    pub signed_by: Option<String>,
    pub revision: String,
    pub has_backup: bool,
}

fn short_id(file: &str, kind: &str, index: usize) -> String {
    apt_ops::sha256_hex(format!("{file}\0{kind}\0{index}").as_bytes())[..16].to_string()
}

fn host_of(uri: &str) -> Option<String> {
    let without_scheme = uri.split_once("://").map(|(_, rest)| rest).unwrap_or(uri);
    let host = without_scheme.split(['/', '@']).next()?;
    (!host.is_empty()).then(|| host.to_ascii_lowercase())
}

fn friendly_name_for_host(host: &str) -> Option<&'static str> {
    if host == "deb.debian.org"
        || host == "security.debian.org"
        || host.ends_with(".deb.debian.org")
    {
        return Some("Debian");
    }
    if host == "download.docker.com" {
        return Some("Docker");
    }
    if host == "downloads.claude.ai" {
        return Some("Claude");
    }
    if host == "dl.google.com" {
        return Some("Google");
    }
    if host.ends_with("ppa.launchpad.net") || host.ends_with("ppa.launchpadcontent.net") {
        return Some("PPA (Launchpad)");
    }
    if host == "archive.ubuntu.com"
        || host == "security.ubuntu.com"
        || host == "old-releases.ubuntu.com"
        || host.ends_with(".archive.ubuntu.com")
    {
        return Some("Ubuntu");
    }
    None
}

fn is_official_archive_host(host: &str) -> bool {
    host.ends_with("archive.ubuntu.com")
        || host == "security.ubuntu.com"
        || host == "old-releases.ubuntu.com"
        || host == "deb.debian.org"
        || host == "security.debian.org"
}

/// A short, translated-in-the-frontend key when the host is a well-known
/// official archive, otherwise the raw host itself (always truthful, never
/// invented) -- matching the V1 UX rule: a real value beats no value, and a
/// guess is never presented as a confirmed fact.
fn describe(host: Option<&str>, suites: &[String]) -> Option<String> {
    let host = host?;
    if is_official_archive_host(host) {
        let is_security =
            host.contains("security") || suites.iter().any(|s| s.contains("security"));
        return Some(if is_security {
            "apt.security".to_string()
        } else {
            "apt.official".to_string()
        });
    }
    Some(host.to_string())
}

struct DebFields {
    kind: String,
    uri: String,
    suite: String,
    components: Vec<String>,
    signed_by: Option<String>,
    architectures: Vec<String>,
}

fn parse_deb_fields(content: &str) -> DebFields {
    let content = content.trim();
    let (kind, rest) = if let Some(r) = content.strip_prefix("deb-src") {
        ("deb-src", r)
    } else if let Some(r) = content.strip_prefix("deb") {
        ("deb", r)
    } else {
        ("deb", content)
    };
    let mut rest = rest.trim_start();
    let mut signed_by = None;
    let mut architectures = Vec::new();
    if rest.starts_with('[') {
        if let Some(end) = rest.find(']') {
            for opt in rest[1..end].split_whitespace() {
                if let Some((key, value)) = opt.split_once('=') {
                    match key {
                        "signed-by" => signed_by = Some(value.to_string()),
                        "arch" | "arch+" => {
                            architectures.extend(value.split(',').map(str::to_string))
                        }
                        _ => {}
                    }
                }
            }
            rest = rest[end + 1..].trim_start();
        }
    }
    let mut tokens = rest.split_whitespace();
    let uri = tokens.next().unwrap_or("").to_string();
    let suite = tokens.next().unwrap_or("").to_string();
    let components = tokens.map(str::to_string).collect();
    DebFields {
        kind: kind.to_string(),
        uri,
        suite,
        components,
        signed_by,
        architectures,
    }
}

fn file_label(file: &Path) -> String {
    file.file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| "Repository".into())
}

fn parse_list_file(
    file: &Path,
    text: &str,
    revision: &str,
    has_backup: bool,
) -> Vec<AptRepository> {
    let mut repos = Vec::new();
    let mut index = 0usize;
    for (_start, _end, line) in apt_ops::lines_with_offsets(text) {
        let Some((enabled, writable, note, content_start)) = apt_ops::classify_list_line(line)
        else {
            continue;
        };
        let fields = parse_deb_fields(&line[content_start..]);
        let host = host_of(&fields.uri);
        let name = host
            .as_deref()
            .and_then(friendly_name_for_host)
            .map(str::to_string)
            .unwrap_or_else(|| host.clone().unwrap_or_else(|| file_label(file)));
        let subtitle = describe(host.as_deref(), std::slice::from_ref(&fields.suite));
        let file_str = file.to_string_lossy().to_string();
        repos.push(AptRepository {
            id: short_id(&file_str, "list-line", index),
            backend: "apt",
            format: "list",
            file: file_str,
            index,
            enabled,
            writable,
            note,
            name,
            subtitle,
            kind: fields.kind,
            uris: vec![fields.uri],
            suites: vec![fields.suite],
            components: fields.components,
            architectures: fields.architectures,
            signed_by: fields.signed_by,
            revision: revision.to_string(),
            has_backup,
        });
        index += 1;
    }
    repos
}

fn split_values(fields: &[StanzaField], key: &str) -> Vec<String> {
    apt_ops::split_values(fields, key)
}

fn parse_sources_file(
    file: &Path,
    text: &str,
    revision: &str,
    has_backup: bool,
) -> Vec<AptRepository> {
    let all_lines = apt_ops::lines_with_offsets(text);
    let mut repos = Vec::new();
    let mut index = 0usize;
    for chunk in apt_ops::stanza_chunks(&all_lines) {
        if chunk
            .iter()
            .all(|(_, _, line)| line.trim().is_empty() || line.trim_start().starts_with('#'))
        {
            continue;
        }
        let fields = apt_ops::parse_stanza_fields(chunk);
        let Some(types_field) = apt_ops::field(&fields, "types") else {
            continue;
        };
        let Some(uris_field) =
            apt_ops::field(&fields, "uris").or_else(|| apt_ops::field(&fields, "uri"))
        else {
            continue;
        };

        let enabled_field = apt_ops::field(&fields, "enabled");
        let (enabled, writable, note) = match enabled_field.map(|f| f.value_first_line.as_str()) {
            None => (true, true, None),
            Some("yes") => (true, true, None),
            Some("no") => (false, true, None),
            Some(_) => (false, false, Some("incompatible")),
        };

        let suites = split_values(&fields, "suites");
        let components = split_values(&fields, "components");
        let architectures = split_values(&fields, "architectures");
        let signed_by = apt_ops::field(&fields, "signed-by").map(|f| {
            if f.multiline {
                "embedded".to_string()
            } else {
                f.value_first_line.clone()
            }
        });
        let uris: Vec<String> = uris_field
            .value_first_line
            .split_whitespace()
            .map(str::to_string)
            .collect();
        let host = uris.first().and_then(|u| host_of(u));
        let repolib_name = apt_ops::field(&fields, "x-repolib-name")
            .map(|f| f.value_first_line.clone())
            .filter(|s| !s.is_empty());
        let name = repolib_name
            .or_else(|| {
                host.as_deref()
                    .and_then(friendly_name_for_host)
                    .map(str::to_string)
            })
            .unwrap_or_else(|| file_label(file));
        let subtitle = describe(host.as_deref(), &suites);

        let file_str = file.to_string_lossy().to_string();
        repos.push(AptRepository {
            id: short_id(&file_str, "sources-stanza", index),
            backend: "apt",
            format: "sources",
            file: file_str,
            index,
            enabled,
            writable,
            note,
            name,
            subtitle,
            kind: types_field.value_first_line.clone(),
            uris,
            suites,
            components,
            architectures,
            signed_by,
            revision: revision.to_string(),
            has_backup,
        });
        index += 1;
    }
    repos
}

/// Every repository this Toolbox currently finds across every discovered
/// APT source file. Read-only; never touches disk beyond reading.
pub fn list_repositories() -> Vec<AptRepository> {
    let mut repos = Vec::new();
    for file in apt_ops::discover_source_files() {
        let Ok(bytes) = std::fs::read(&file) else {
            continue;
        };
        let Ok(text) = String::from_utf8(bytes) else {
            continue;
        };
        let revision = apt_ops::sha256_hex(text.as_bytes());
        let has_backup = apt_ops::has_backup(&file);
        let format = file.extension().and_then(|e| e.to_str());
        match format {
            Some("sources") => {
                repos.extend(parse_sources_file(&file, &text, &revision, has_backup))
            }
            _ => repos.extend(parse_list_file(&file, &text, &revision, has_backup)),
        }
    }
    repos
}

/// Re-locates one repository by the stable id the frontend was given, by
/// re-scanning fresh from disk. Never trusts a cached index across calls.
fn find_repository(id: &str) -> Option<AptRepository> {
    list_repositories().into_iter().find(|repo| repo.id == id)
}

/// Re-verifies the repository against the caller's expected revision, then
/// asks the privileged helper -- which independently re-resolves and
/// re-verifies everything again -- to apply the minimal edit, and returns
/// the freshly re-read repository list.
pub fn set_enabled(
    id: &str,
    enabled: bool,
    expected_revision: &str,
) -> Result<Vec<AptRepository>, String> {
    let repo = find_repository(id).ok_or("repository_not_found")?;
    if repo.revision != expected_revision {
        return Err("revision_mismatch".into());
    }
    if !repo.writable {
        return Err("control_managed".into());
    }
    let args = [
        "apt-repo-set".to_string(),
        repo.file.clone(),
        repo.format.to_string(),
        repo.index.to_string(),
        if enabled { "enable" } else { "disable" }.to_string(),
        repo.revision.clone(),
    ];
    crate::privileged_repository_helper_args(&args)?;
    Ok(list_repositories())
}

pub fn restore_last_backup(
    id: &str,
    expected_revision: &str,
) -> Result<Vec<AptRepository>, String> {
    let repo = find_repository(id).ok_or("repository_not_found")?;
    if repo.revision != expected_revision {
        return Err("revision_mismatch".into());
    }
    if !repo.has_backup {
        return Err("no_backup_available".into());
    }
    let args = [
        "apt-repo-restore".to_string(),
        repo.file.clone(),
        repo.revision.clone(),
    ];
    crate::privileged_repository_helper_args(&args)?;
    Ok(list_repositories())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_bracketed_options_from_a_real_world_list_line() {
        let fields = parse_deb_fields(
            "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/claude-desktop-archive-keyring.asc] https://downloads.claude.ai/claude-desktop/apt/stable stable main",
        );
        assert_eq!(fields.kind, "deb");
        assert_eq!(
            fields.uri,
            "https://downloads.claude.ai/claude-desktop/apt/stable"
        );
        assert_eq!(fields.suite, "stable");
        assert_eq!(fields.components, vec!["main".to_string()]);
        assert_eq!(
            fields.signed_by,
            Some("/usr/share/keyrings/claude-desktop-archive-keyring.asc".to_string())
        );
        assert_eq!(
            fields.architectures,
            vec!["amd64".to_string(), "arm64".to_string()]
        );
    }

    #[test]
    fn parses_a_two_stanza_deb822_file_and_preserves_a_multiline_signed_by() {
        let text = "Types: deb\nURIs: http://it.archive.ubuntu.com/ubuntu/\nSuites: stonking stonking-updates\nComponents: main restricted\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n\nTypes: deb\nURIs: http://security.ubuntu.com/ubuntu/\nSuites: stonking-security\nComponents: main restricted\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n";
        let repos = parse_sources_file(
            Path::new("/etc/apt/sources.list.d/ubuntu.sources"),
            text,
            "rev",
            false,
        );
        assert_eq!(repos.len(), 2);
        assert_eq!(repos[0].name, "Ubuntu");
        assert_eq!(repos[0].subtitle.as_deref(), Some("apt.official"));
        assert_eq!(repos[1].subtitle.as_deref(), Some("apt.security"));
        assert!(repos.iter().all(|r| r.enabled && r.writable));

        let multiline = "Types: deb\nURIs: https://ppa.launchpadcontent.net/x/y/ubuntu/\nSuites: stonking\nComponents: main\nSigned-By:\n .\n -----BEGIN PGP PUBLIC KEY BLOCK-----\n abcd\n -----END PGP PUBLIC KEY BLOCK-----\n";
        let repos = parse_sources_file(
            Path::new("/etc/apt/sources.list.d/x.sources"),
            multiline,
            "rev",
            false,
        );
        assert_eq!(repos.len(), 1);
        assert_eq!(repos[0].signed_by.as_deref(), Some("embedded"));
    }

    #[test]
    fn honours_x_repolib_name_over_the_host_heuristic() {
        let text = "X-Repolib-Name: ChatGPT\nTypes: deb\nURIs: https://persistent.oaistatic.com/codex-app-prod/linux/deb\nSuites: stable\nComponents: main\nSigned-By: /usr/share/keyrings/chatgpt-archive-keyring.gpg\n";
        let repos = parse_sources_file(
            Path::new("/etc/apt/sources.list.d/chatgpt.sources"),
            text,
            "rev",
            false,
        );
        assert_eq!(repos[0].name, "ChatGPT");
    }

    #[test]
    fn deb822_enabled_no_is_read_as_disabled_and_stays_writable() {
        let text =
            "Types: deb\nURIs: http://example/\nSuites: stable\nComponents: main\nEnabled: no\n";
        let repos = parse_sources_file(
            Path::new("/etc/apt/sources.list.d/x.sources"),
            text,
            "rev",
            false,
        );
        assert!(!repos[0].enabled);
        assert!(repos[0].writable);
    }

    #[test]
    #[ignore = "reads this machine's real /etc/apt configuration; run manually with -- --ignored --nocapture"]
    fn live_list_repositories_against_the_real_system() {
        let repos = list_repositories();
        eprintln!("found {} real repositories:", repos.len());
        for repo in &repos {
            eprintln!(
                "  [{:>4}] idx={:<2} {:<9} enabled={:<5} writable={:<5} note={:<12} name={:<20} subtitle={:<16} file={} rev={}",
                &repo.id[..4],
                repo.index,
                repo.format,
                repo.enabled,
                repo.writable,
                repo.note.unwrap_or("-"),
                repo.name,
                repo.subtitle.as_deref().unwrap_or("-"),
                repo.file,
                repo.revision,
            );
        }
        assert!(
            !repos.is_empty(),
            "expected at least one real APT source on this machine"
        );
        assert!(
            repos.iter().all(|r| r.revision.len() == 64),
            "every revision must be a full sha256 hex string"
        );

        // Pure dry run: computes the exact disable-edit for every real,
        // writable repository found above and shows only the touched span,
        // without writing anything back. Proves the edit is minimal and
        // correct against real file bytes, not just synthetic fixtures.
        for repo in repos.iter().filter(|r| r.writable) {
            let bytes = std::fs::read(&repo.file).expect("real file must still be readable");
            let edit = if repo.format == "list" {
                apt_ops::build_list_toggle_edit(&bytes, repo.index, !repo.enabled)
            } else {
                apt_ops::build_sources_toggle_edit(&bytes, repo.index, !repo.enabled)
            }
            .expect("dry-run edit must be computable for a real writable repository");
            let touched = String::from_utf8_lossy(&bytes[edit.start..edit.end]);
            eprintln!(
                "  dry-run {} [{}]: replace {:?} ({} bytes) with {:?}",
                repo.name,
                &repo.id[..4],
                touched,
                edit.end - edit.start,
                String::from_utf8_lossy(&edit.replacement)
            );
        }
    }

    #[test]
    fn manually_commented_list_lines_are_shown_but_never_writable() {
        let text = "deb http://a/ suite main\n# deb http://b/ other main\n";
        let repos = parse_list_file(
            Path::new("/etc/apt/sources.list.d/x.list"),
            text,
            "rev",
            false,
        );
        assert_eq!(repos.len(), 2);
        assert!(repos[0].enabled && repos[0].writable);
        assert!(!repos[1].enabled && !repos[1].writable);
        assert_eq!(repos[1].note, Some("manual"));
    }
}
