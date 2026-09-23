//! Claude Code reconnect bridge. Claude Code remains the sole owner of its
//! OAuth credentials; the Toolbox only asks its official CLI for a redacted
//! status and opens its documented interactive login in the user's terminal.
use serde::{Deserialize, Serialize};
use std::{path::PathBuf, process::Command};

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClaudeAuthStatus {
    pub cli_available: bool,
    pub login_required: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ClaudeAuthResponse {
    logged_in: bool,
}

fn claude_binary() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("CLAUDE_CLI_PATH").filter(|path| !path.is_empty()) {
        let path = PathBuf::from(path);
        if path.is_file() {
            return Some(path);
        }
    }
    let path = std::env::var_os("PATH")?;
    for directory in std::env::split_paths(&path) {
        let candidate = directory.join("claude");
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .map(|home| home.join(".local/bin/claude"))
        .filter(|path| path.is_file())
}

pub fn status() -> ClaudeAuthStatus {
    let Some(binary) = claude_binary() else {
        return ClaudeAuthStatus {
            cli_available: false,
            login_required: false,
        };
    };
    let logged_in = Command::new(binary)
        .args(["auth", "status", "--json"])
        .output()
        .ok()
        .and_then(|output| serde_json::from_slice::<ClaudeAuthResponse>(&output.stdout).ok())
        .is_some_and(|status| status.logged_in);
    ClaudeAuthStatus {
        cli_available: true,
        login_required: !logged_in,
    }
}

pub fn start_reconnect() -> Result<(), String> {
    let binary = claude_binary().ok_or("claude_cli_missing")?;
    // xdg-terminal-exec is the freedesktop terminal launcher. Arguments stay
    // separate: no shell, token, cookie, config file or user-provided input is
    // ever passed to the spawned process.
    Command::new("xdg-terminal-exec")
        // Keep to the portable terminal-spec argument form. Some terminals
        // treat optional title/hold flags as part of the launched command.
        .arg("--")
        .arg(binary)
        .args(["auth", "login", "--claudeai"])
        .spawn()
        .map(|_| ())
        .map_err(|_| "claude_login_terminal_failed".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn public_status_has_no_credential_fields() {
        let encoded = serde_json::to_string(&ClaudeAuthStatus {
            cli_available: true,
            login_required: true,
        })
        .unwrap();
        assert_eq!(encoded, r#"{"cliAvailable":true,"loginRequired":true}"#);
        assert!(!encoded.contains("token"));
        assert!(!encoded.contains("cookie"));
    }
}
