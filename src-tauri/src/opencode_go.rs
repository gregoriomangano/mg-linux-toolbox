//! OpenCode Go bridge: OpenCode's own CLI already stores its Go plan API key
//! in `auth.json` next to its local usage database. CodexBar can read the
//! authoritative `https://opencode.ai/zen/go/v1/usage` endpoint on its own,
//! but only when `OPENCODE_API_KEY` is present in *its* environment -- it
//! does not import OpenCode's `auth.json` itself.
//!
//! This module closes that gap without re-implementing CodexBar's HTTP
//! client: it reads the credential once, keeps it only on this process's
//! stack, and hands it to the CodexBar child process's environment alone.
//! The credential is never logged, never placed in argv, never written to
//! any Toolbox config file, and never sent to the frontend.
use serde_json::Value;
use std::path::PathBuf;

fn opencode_data_dir() -> Option<PathBuf> {
    if let Ok(dir) = std::env::var("XDG_DATA_HOME") {
        if !dir.is_empty() {
            return Some(PathBuf::from(dir).join("opencode"));
        }
    }
    let home = std::env::var("HOME").ok()?;
    (!home.is_empty()).then(|| PathBuf::from(home).join(".local/share/opencode"))
}

/// Reads OpenCode's `auth.json` and returns the OpenCode Go API key only
/// when a `type: "api"` credential is stored under the `opencode-go`
/// provider id. Returns `None` on any I/O, parse, or shape mismatch -- this
/// function never returns a partial or best-guess credential.
pub fn api_key() -> Option<String> {
    let path = opencode_data_dir()?.join("auth.json");
    let contents = std::fs::read_to_string(path).ok()?;
    let value: Value = serde_json::from_str(&contents).ok()?;
    let entry = value.get("opencode-go")?;
    if entry.get("type").and_then(Value::as_str) != Some("api") {
        return None;
    }
    let key = entry.get("key").and_then(Value::as_str)?;
    (!key.is_empty()).then(|| key.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    // These tests mutate process-wide XDG_DATA_HOME/HOME env vars, which are
    // read by nothing else in this crate; the lock only protects the three
    // tests below from each other when cargo runs them on separate threads.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    fn with_isolated_data_dir<T>(run: impl FnOnce(&std::path::Path) -> T) -> T {
        let _guard = ENV_LOCK.lock().unwrap();
        let dir = std::env::temp_dir().join(format!(
            "mg-toolbox-opencode-go-test-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let _ = std::fs::create_dir_all(dir.join("opencode"));
        let previous = std::env::var("XDG_DATA_HOME").ok();
        std::env::set_var("XDG_DATA_HOME", &dir);
        let result = run(&dir.join("opencode"));
        match previous {
            Some(value) => std::env::set_var("XDG_DATA_HOME", value),
            None => std::env::remove_var("XDG_DATA_HOME"),
        }
        let _ = std::fs::remove_dir_all(&dir);
        result
    }

    #[test]
    fn returns_the_key_only_for_a_real_api_credential() {
        with_isolated_data_dir(|opencode_dir| {
            std::fs::write(
                opencode_dir.join("auth.json"),
                r#"{"opencode-go":{"type":"api","key":"test-only-not-a-real-secret"}}"#,
            )
            .unwrap();
            assert_eq!(api_key(), Some("test-only-not-a-real-secret".to_string()));
        });
    }

    #[test]
    fn ignores_a_non_api_credential_type() {
        with_isolated_data_dir(|opencode_dir| {
            std::fs::write(
                opencode_dir.join("auth.json"),
                r#"{"opencode-go":{"type":"oauth","access":"whatever"}}"#,
            )
            .unwrap();
            assert_eq!(api_key(), None);
        });
    }

    #[test]
    fn returns_none_when_there_is_no_auth_file_at_all() {
        with_isolated_data_dir(|_| {
            assert_eq!(api_key(), None);
        });
    }
}
