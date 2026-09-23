//! The single AI integration: M.G Linux Toolbox drives the bundled CodexBar
//! CLI as its complete, upstream provider engine and only reads the one
//! structured snapshot it publishes.
//!
//! Ownership split (deliberate, verified live against CodexBar 0.60.4):
//! - CodexBar owns detection, authentication, quota sources, fallbacks,
//!   OAuth, CLI/local/web/API probing, window interpretation, resets,
//!   source/confidence and per-provider errors.
//! - M.G owns exactly four things: locating the sidecar, preparing a correct
//!   Linux environment for it (plus the one credential bridge that upstream
//!   cannot do itself -- see `opencode_go.rs`), reading `dashboard` JSON into
//!   a tolerant versioned model, and rendering that through our own UI.
//!
//! There is deliberately no per-provider code here. The UI allowlist below
//! is a *display* filter, not a second engine: every allowlisted id is turned
//! on in the engine config with upstream `source: "auto"` so CodexBar runs
//! its own full strategy chain (probed live: pinning a source broke
//! Antigravity, which works only through its `app` strategy).
use crate::opencode_go;
use serde::{Deserialize, Serialize};
use std::{
    io::Read,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{mpsc, Mutex, OnceLock},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

/// The fixed UI allowlist, in the exact display order of our page. CodexBar
/// knows ~60 providers; anything not listed here is ignored by the frontend
/// even if the engine returns it (e.g. Codex is default-on upstream).
pub const PROVIDER_ALLOWLIST: &[(&str, &str)] = &[
    ("codex", "OpenAI Codex"),
    ("claude", "Claude Code"),
    ("gemini", "Google Gemini"),
    ("antigravity", "Google Antigravity"),
    ("cursor", "Cursor"),
    ("opencode", "OpenCode"),
    ("copilot", "GitHub Copilot"),
    ("grok", "Grok / xAI"),
    ("opencodego", "OpenCode Go"),
];

/// Dashboard snapshots older than this are surfaced as stale by the engine
/// itself; kept for the view model so the UI can say "last valid reading".
const DASHBOARD_TIMEOUT_SECS: u64 = 45;
const PROCESS_DEADLINE_SECS: u64 = 75;
const MAX_OUTPUT_BYTES: u64 = 4 * 1024 * 1024;

// ---------------------------------------------------------------------------
// Dashboard-v1 schema (tolerant: unknown fields ignored, optionals optional)
// ---------------------------------------------------------------------------

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Dashboard {
    schema_version: Option<u32>,
    generated_at: Option<String>,
    stale_after_seconds: Option<u64>,
    host: Option<HostInfo>,
    #[serde(default)]
    providers: Vec<Row>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct HostInfo {
    codex_bar_version: Option<String>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Row {
    id: Option<String>,
    #[allow(dead_code)]
    name: Option<String>,
    source: Option<String>,
    identity: Option<Identity>,
    updated_at: Option<String>,
    error: Option<RowError>,
    #[serde(default)]
    windows: Vec<Window>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Identity {
    plan: Option<String>,
    account_email: Option<String>,
}

#[derive(Debug, Default, Deserialize)]
struct RowError {
    kind: Option<String>,
    message: Option<String>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Window {
    used_percent: Option<f64>,
    remaining_percent: Option<f64>,
    kind: Option<String>,
    label: Option<String>,
    reset_at: Option<String>,
}

// ---------------------------------------------------------------------------
// M.G view model (the only thing the frontend ever receives)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AiWindow {
    /// Preserved semantically exactly as upstream reports them; the UI never
    /// inverts anything itself. `remaining = 100 - used` is applied once,
    /// here, and only when upstream omitted `remainingPercent`.
    pub used_percent: f64,
    pub remaining_percent: f64,
    pub kind: Option<String>,
    pub label: Option<String>,
    pub resets_at: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AiProviderView {
    pub id: String,
    /// Display name from the fixed allowlist (never upstream's own naming,
    /// so the UI stays stable even if upstream renames a provider).
    pub name: String,
    /// A row for this provider came back from the engine.
    pub installed: bool,
    /// No authentication-type error was reported for this provider.
    pub authenticated: bool,
    /// Real quota windows are present.
    pub available: bool,
    /// Engine status, one of: detected, unauthenticated, unsupportedOs,
    /// noFetchStrategy, temporary, unavailable, notDetected.
    pub status: String,
    pub source: Option<String>,
    /// authoritative | estimated | unknown (see `confidence_for`).
    pub confidence: String,
    pub plan: Option<String>,
    pub account: Option<String>,
    /// Upstream's own measurement time for this row, when it reported one.
    pub updated_at: Option<String>,
    pub windows: Vec<AiWindow>,
    /// Upstream's own error text, when it reported one (already redacted at
    /// the source: only the provider's own message is kept, never creds).
    pub diagnostic: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AiUsageSnapshot {
    pub engine_version: Option<String>,
    /// Set when the whole engine run failed (spawn/timeout/schema). The
    /// provider list may then carry the last valid cached snapshot instead.
    pub engine_error: Option<String>,
    pub stale: bool,
    pub age_seconds: Option<u64>,
    pub stale_after_seconds: Option<u64>,
    pub generated_at: Option<String>,
    pub duration_ms: u64,
    pub providers: Vec<AiProviderView>,
}

// ---------------------------------------------------------------------------
// Sidecar discovery and version
// ---------------------------------------------------------------------------

fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    path.metadata()
        .is_ok_and(|metadata| metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
}

/// Locates the bundled CodexBar sidecar, in the same order as before:
/// Tauri's resource dir first, then paths relative to the running binary.
pub fn find_binary(resource_dir: Option<&Path>) -> Option<PathBuf> {
    let directory = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf))?;
    let mut candidates = resource_dir
        .into_iter()
        .flat_map(|path| {
            [
                path.join("codexbar-linux-x86_64/CodexBarCLI"),
                path.join("resources/codexbar-linux-x86_64/CodexBarCLI"),
            ]
        })
        .collect::<Vec<_>>();
    candidates.extend([
        directory.join("resources/codexbar-linux-x86_64/CodexBarCLI"),
        directory.join("../resources/codexbar-linux-x86_64/CodexBarCLI"),
        PathBuf::from("resources/codexbar-linux-x86_64/CodexBarCLI"),
    ]);
    candidates.into_iter().find(|path| is_executable(path))
}

// ---------------------------------------------------------------------------
// Environment (section 15): the engine must see the same user profile as the
// CLIs the user runs themselves, and nothing more.
// ---------------------------------------------------------------------------

fn home_dir() -> Option<PathBuf> {
    if let Ok(home) = std::env::var("HOME") {
        if !home.is_empty() {
            return Some(PathBuf::from(home));
        }
    }
    // GUI sessions occasionally lack HOME entirely; derive it from the
    // passwd database instead of guessing.
    let uid = unsafe { libc::getuid() };
    let entry = unsafe { libc::getpwuid(uid) };
    if entry.is_null() {
        return None;
    }
    let dir = unsafe { (*entry).pw_dir };
    if dir.is_null() {
        return None;
    }
    let cstr = unsafe { std::ffi::CStr::from_ptr(dir) };
    cstr.to_str()
        .ok()
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
}

/// Extra environment for the CodexBar child only. Inherits what the app
/// already has (the normal Linux contract: HOME/PATH are already correct in
/// a desktop session), and explicitly *fills the gaps* the engine cannot
/// work without. The only secret ever added is the OpenCode Go bridge key,
/// and it goes into this child's environment alone -- never argv, never our
/// own process, never a log, never the frontend.
fn child_environment() -> Vec<(String, String)> {
    let mut env = Vec::new();
    if let Some(home) = home_dir() {
        let home = home.to_string_lossy().to_string();
        if std::env::var_os("HOME").is_none() {
            env.push(("HOME".into(), home.clone()));
        }
        for (key, suffix) in [
            ("XDG_CONFIG_HOME", ".config"),
            ("XDG_DATA_HOME", ".local/share"),
            ("XDG_CACHE_HOME", ".cache"),
        ] {
            if std::env::var_os(key).is_none() {
                env.push((key.into(), format!("{home}/{suffix}")));
            }
        }
    }
    let path_ok = std::env::var("PATH")
        .map(|value| !value.is_empty())
        .unwrap_or(false);
    if !path_ok {
        env.push((
            "PATH".into(),
            "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin".into(),
        ));
    }
    if let Some(key) = opencode_go::api_key() {
        env.push(("OPENCODE_API_KEY".into(), key));
    }
    env
}

// ---------------------------------------------------------------------------
// Engine config: our allowlist, upstream auto strategies
// ---------------------------------------------------------------------------

/// Builds the engine config: every allowlisted provider enabled with
/// upstream `source: "auto"`, and deliberately no `cookieSource` override,
/// so CodexBar runs its own full strategy chain for each provider.
///
/// This replaced one config-per-provider with pinned sources. Probed live on
/// 2026-09-17: pinning `--source oauth` made Antigravity report "Google auth
/// not found" while `auto` reads its `app` source successfully; pinning also
/// skipped Claude's real auto chain (cookie store -> `claude auth-status`
/// CLI probe -> fallback). Both differences were reproduced before changing
/// this, and the parity test in `tests/` pins the new behaviour.
fn engine_config() -> Vec<u8> {
    let providers: Vec<serde_json::Value> = PROVIDER_ALLOWLIST
        .iter()
        .map(|(id, _)| serde_json::json!({ "id": id, "enabled": true, "source": "auto" }))
        .collect();
    serde_json::to_vec(&serde_json::json!({ "version": 1, "providers": providers }))
        .unwrap_or_default()
}

fn ensure_engine_config(config_dir: &Path) -> Option<PathBuf> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::create_dir_all(config_dir).ok()?;
    let path = config_dir.join("codexbar-engine.json");
    let temporary = config_dir.join(".codexbar-engine.json.tmp");
    std::fs::write(&temporary, engine_config()).ok()?;
    let _ = std::fs::set_permissions(&temporary, std::fs::Permissions::from_mode(0o600));
    std::fs::rename(&temporary, &path).ok()?;
    Some(path)
}

// ---------------------------------------------------------------------------
// Snapshot execution
// ---------------------------------------------------------------------------

/// Runs `CodexBarCLI dashboard` once and returns its raw JSON bytes.
fn run_dashboard(binary: &Path, config_path: &Path) -> Result<Vec<u8>, String> {
    let mut command = Command::new(binary);
    command
        .args([
            "dashboard",
            "--identity",
            "redacted",
            "--timeout",
            &DASHBOARD_TIMEOUT_SECS.to_string(),
            "--json-output",
        ])
        .env("CODEXBAR_CONFIG", config_path)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    for (key, value) in child_environment() {
        command.env(key, value);
    }
    let mut child = command
        .spawn()
        .map_err(|_| "engine_spawn_failed".to_string())?;
    let Some(stdout) = child.stdout.take() else {
        return Err("engine_spawn_failed".into());
    };
    let (sender, receiver) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = stdout.take(MAX_OUTPUT_BYTES).read_to_end(&mut bytes);
        let _ = sender.send(bytes);
    });
    let deadline = Instant::now() + Duration::from_secs(PROCESS_DEADLINE_SECS);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => {
                return receiver
                    .recv_timeout(Duration::from_secs(2))
                    .map_err(|_| "engine_read_failed".to_string());
            }
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(50)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("engine_timeout".into());
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Mapping: engine row -> view model (single centralised translation)
// ---------------------------------------------------------------------------

/// Confidence is derived from the engine's own `source` provenance, because
/// dashboard-v1 does not carry `dataConfidence` (the per-provider `usage`
/// output does). Verified live: OpenCode Go reports `local` when reading its
/// local estimate and `local+api` once the API bridge is supplied, so a
/// source that is exactly `local` is the estimated case.
fn confidence_for(source: Option<&str>) -> &'static str {
    match source.map(str::trim) {
        None | Some("") => "unknown",
        Some("local") => "estimated",
        Some(_) => "authoritative",
    }
}

/// Centralised, provider-agnostic classification of an engine row. This is a
/// translation of the engine's own state -- never a second opinion: every
/// branch keys off upstream's `error.kind` and its own message text, and the
/// upstream message itself is always preserved in `diagnostic`.
fn classify(kind: Option<&str>, message: Option<&str>, has_windows: bool) -> &'static str {
    if has_windows {
        return "detected";
    }
    let message = message.unwrap_or("").to_ascii_lowercase();
    let kind = kind.unwrap_or("");
    if message.contains("macos") || message.contains("not supported on linux") {
        return "unsupportedOs";
    }
    if message.contains("no available fetch strategy") {
        return "noFetchStrategy";
    }
    if message.contains("timed out") || message.contains("timeout") {
        return "temporary";
    }
    if kind == "provider"
        && (message.contains("not logged in")
            || message.contains("authenticate")
            || message.contains("token")
            || message.contains("credentials")
            || message.contains("session")
            || message.contains("login"))
    {
        return "unauthenticated";
    }
    if kind == "provider" {
        return "unauthenticated";
    }
    "unavailable"
}

fn sanitize_percent(value: f64) -> f64 {
    if value.is_finite() {
        value.clamp(0.0, 100.0)
    } else {
        0.0
    }
}

fn map_window(window: &Window) -> Option<AiWindow> {
    let used = window.used_percent;
    let remaining = window.remaining_percent;
    // The single centralised conversion: only ever applied when upstream
    // omitted the field, and only from a real usedPercent.
    let (used_percent, remaining_percent) = match (used, remaining) {
        (Some(used), Some(remaining)) => (sanitize_percent(used), sanitize_percent(remaining)),
        (Some(used), None) => {
            let used = sanitize_percent(used);
            (used, sanitize_percent(100.0 - used))
        }
        (None, Some(remaining)) => {
            let remaining = sanitize_percent(remaining);
            (sanitize_percent(100.0 - remaining), remaining)
        }
        (None, None) => return None,
    };
    Some(AiWindow {
        used_percent,
        remaining_percent,
        kind: window.kind.clone(),
        label: window.label.clone(),
        resets_at: window.reset_at.clone(),
    })
}

fn view_for_row(id: &str, name: &str, row: Option<&Row>) -> AiProviderView {
    let Some(row) = row else {
        return AiProviderView {
            id: id.into(),
            name: name.into(),
            installed: false,
            authenticated: false,
            available: false,
            status: "notDetected".into(),
            source: None,
            confidence: "unknown".into(),
            plan: None,
            account: None,
            updated_at: None,
            windows: Vec::new(),
            diagnostic: None,
        };
    };
    let windows: Vec<AiWindow> = row.windows.iter().filter_map(map_window).collect();
    let error_kind = row.error.as_ref().and_then(|error| error.kind.as_deref());
    let error_message = row
        .error
        .as_ref()
        .and_then(|error| error.message.as_deref());
    let status = classify(error_kind, error_message, !windows.is_empty());
    let identity = row.identity.as_ref();
    AiProviderView {
        id: id.into(),
        name: name.into(),
        installed: true,
        authenticated: !matches!(status, "unauthenticated"),
        available: !windows.is_empty(),
        status: status.into(),
        source: row.source.clone(),
        // Confidence only means something when there is a reading to trust.
        confidence: if windows.is_empty() {
            "unknown".to_owned()
        } else {
            confidence_for(row.source.as_deref()).to_owned()
        },
        plan: identity.and_then(|identity| identity.plan.clone()),
        account: identity.and_then(|identity| identity.account_email.clone()),
        updated_at: row.updated_at.clone(),
        windows,
        diagnostic: error_message.map(str::to_owned),
    }
}

/// Converts a parsed dashboard snapshot into the fixed allowlist order.
/// Providers the engine knows but we do not display are dropped here -- the
/// UI never grows a card on its own.
fn map_snapshot(dashboard: &Dashboard, stale: bool, age_seconds: Option<u64>) -> AiUsageSnapshot {
    let providers = PROVIDER_ALLOWLIST
        .iter()
        .map(|(id, name)| {
            let row = dashboard
                .providers
                .iter()
                .find(|row| row.id.as_deref() == Some(*id));
            view_for_row(id, name, row)
        })
        .collect();
    AiUsageSnapshot {
        engine_version: dashboard
            .host
            .as_ref()
            .and_then(|host| host.codex_bar_version.clone()),
        engine_error: None,
        stale,
        age_seconds,
        stale_after_seconds: dashboard.stale_after_seconds,
        generated_at: dashboard.generated_at.clone(),
        duration_ms: 0,
        providers,
    }
}

fn parse_dashboard(bytes: &[u8]) -> Result<Dashboard, String> {
    let dashboard: Dashboard =
        serde_json::from_slice(bytes).map_err(|_| "engine_schema_invalid".to_string())?;
    match dashboard.schema_version {
        Some(1) => Ok(dashboard),
        Some(other) => Err(format!("engine_schema_unsupported:{other}")),
        None => Err("engine_schema_missing".into()),
    }
}

// ---------------------------------------------------------------------------
// Cache (last valid snapshot only)
// ---------------------------------------------------------------------------

const CACHE_SCHEMA: u32 = 1;

struct Cached {
    schema: u32,
    engine_version: Option<String>,
    fetched_at: SystemTime,
    snapshot: AiUsageSnapshot,
}

static CACHE: OnceLock<Mutex<Option<Cached>>> = OnceLock::new();

fn cache() -> &'static Mutex<Option<Cached>> {
    CACHE.get_or_init(|| Mutex::new(None))
}

fn now_epoch() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_secs()
}

/// Cheap `CodexBarCLI --version` probe, cached for the process lifetime.
/// Used only to decide whether a cached snapshot still belongs to the same
/// engine generation after a failed dashboard run.
fn binary_version(binary: &Path) -> Option<String> {
    static VERSION: OnceLock<Option<String>> = OnceLock::new();
    VERSION
        .get_or_init(|| {
            let output = Command::new(binary).arg("--version").output().ok()?;
            if !output.status.success() {
                return None;
            }
            let text = String::from_utf8_lossy(&output.stdout);
            text.split_whitespace().last().map(str::to_owned)
        })
        .clone()
}

/// Public entry point: run the engine, or fall back to the last valid
/// snapshot (clearly marked stale) when the run itself fails. A schema
/// change or an engine version change invalidates the cache instead of
/// showing numbers from a different engine generation.
pub fn snapshot(config_dir: &Path, resource_dir: Option<&Path>) -> AiUsageSnapshot {
    let started = Instant::now();
    let Some(binary) = find_binary(resource_dir) else {
        return fallback_or_error("engine_missing", None, started);
    };
    let Some(config_path) = ensure_engine_config(config_dir) else {
        return fallback_or_error("engine_config_failed", binary_version(&binary), started);
    };
    let bytes = match run_dashboard(&binary, &config_path) {
        Ok(bytes) => bytes,
        Err(error) => return fallback_or_error(&error, binary_version(&binary), started),
    };
    let dashboard = match parse_dashboard(&bytes) {
        Ok(dashboard) => dashboard,
        Err(error) => return fallback_or_error(&error, binary_version(&binary), started),
    };
    let engine_version = dashboard
        .host
        .as_ref()
        .and_then(|host| host.codex_bar_version.clone());
    let mut result = map_snapshot(&dashboard, false, None);
    result.duration_ms = started.elapsed().as_millis() as u64;
    if let Ok(mut guard) = cache().lock() {
        *guard = Some(Cached {
            schema: CACHE_SCHEMA,
            engine_version: engine_version.clone(),
            fetched_at: SystemTime::now(),
            snapshot: result.clone(),
        });
    }
    result
}

fn fallback_or_error(
    error: &str,
    engine_version: Option<String>,
    started: Instant,
) -> AiUsageSnapshot {
    if let Ok(guard) = cache().lock() {
        if let Some(cached) = guard.as_ref() {
            if cached.schema == CACHE_SCHEMA
                && (cached.engine_version == engine_version || engine_version.is_none())
            {
                let age = now_epoch().saturating_sub(
                    cached
                        .fetched_at
                        .duration_since(UNIX_EPOCH)
                        .unwrap_or(Duration::ZERO)
                        .as_secs(),
                );
                let mut stale = cached.snapshot.clone();
                stale.stale = true;
                stale.age_seconds = Some(age);
                stale.engine_error = Some(error.to_string());
                stale.duration_ms = started.elapsed().as_millis() as u64;
                return stale;
            }
        }
    }
    AiUsageSnapshot {
        engine_version,
        engine_error: Some(error.to_string()),
        stale: false,
        age_seconds: None,
        stale_after_seconds: None,
        generated_at: None,
        duration_ms: started.elapsed().as_millis() as u64,
        providers: PROVIDER_ALLOWLIST
            .iter()
            .map(|(id, name)| view_for_row(id, name, None))
            .collect(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(bytes: &[u8]) -> Dashboard {
        parse_dashboard(bytes).expect("valid dashboard fixture")
    }

    /// Shaped exactly like a real `CodexBarCLI dashboard` document captured
    /// from this machine on 2026-09-17 (identity redacted, values trimmed).
    const REAL_SHAPE: &[u8] = br##"{
      "generatedAt":"2026-09-17T15:35:17Z",
      "schemaVersion":1,
      "host":{"codexBarVersion":"0.60.4","usageBarsShowUsed":false},
      "staleAfterSeconds":180,
      "providers":[
        {"id":"codex","name":"Codex","enabled":true,"source":"oauth",
         "identity":{"plan":"Plus","accountEmail":"u@example.com"},
         "windows":[{"usedPercent":10,"remainingPercent":90,"kind":"session","label":"Session","resetAt":"2026-09-17T17:29:08Z"},
                    {"usedPercent":73,"remainingPercent":27,"kind":"weekly","label":"Weekly","resetAt":"2026-09-21T08:43:20Z"}],
         "display":{"priority":"normal","accentColor":"#49A3B0","sortKey":0},
         "cost":{"last30DaysUSD":2.3},"credits":{"remaining":0},"status":null,"error":null},
        {"id":"antigravity","name":"Antigravity","enabled":true,"source":"app",
         "identity":{"plan":"Google AI Plus"},
         "windows":[{"usedPercent":0,"remainingPercent":100,"kind":"antigravity-quota-summary-gemini-weekly","label":"Gemini weekly","resetAt":"2026-09-24T15:35:15Z"}]},
        {"id":"opencodego","name":"OpenCode Go","enabled":true,"source":"local+api",
         "windows":[{"usedPercent":56,"remainingPercent":44,"kind":"tertiary","label":"Monthly","resetAt":"2026-09-26T10:06:00Z"}]},
        {"id":"gemini","name":"Gemini","enabled":true,"source":"auto","windows":[],
         "error":{"kind":"provider","message":"Not logged in to Gemini. Run 'gemini' in Terminal to authenticate."}},
        {"id":"opencode","name":"OpenCode","enabled":true,"source":"auto","windows":[],
         "error":{"kind":"runtime","message":"Error: selected source requires web support and is only supported on macOS."}},
        {"id":"copilot","name":"Copilot","enabled":true,"source":"auto","windows":[],
         "error":{"kind":"provider","message":"No available fetch strategy for copilot."}},
        {"id":"kiro","name":"Kiro","enabled":true,"source":"auto","windows":[{"usedPercent":1,"remainingPercent":99}]}
      ]
    }"##;

    #[test]
    fn reads_a_real_shaped_dashboard_snapshot() {
        let dashboard = parse(REAL_SHAPE);
        assert_eq!(
            dashboard
                .host
                .as_ref()
                .and_then(|h| h.codex_bar_version.as_deref()),
            Some("0.60.4")
        );
        assert_eq!(dashboard.stale_after_seconds, Some(180));
        assert_eq!(dashboard.providers.len(), 7);
    }

    #[test]
    fn only_the_fixed_allowlist_is_exposed_and_in_order() {
        let view = map_snapshot(&parse(REAL_SHAPE), false, None);
        let ids: Vec<&str> = view.providers.iter().map(|p| p.id.as_str()).collect();
        assert_eq!(
            ids,
            [
                "codex",
                "claude",
                "gemini",
                "antigravity",
                "cursor",
                "opencode",
                "copilot",
                "grok",
                "opencodego"
            ]
        );
        // Kiro is in the engine output but must never surface in the UI.
        assert!(!ids.contains(&"kiro"));
    }

    #[test]
    fn windows_preserve_used_and_remaining_without_frontend_inversion() {
        let view = map_snapshot(&parse(REAL_SHAPE), false, None);
        let codex = view.providers.iter().find(|p| p.id == "codex").unwrap();
        assert_eq!(codex.windows[0].used_percent, 10.0);
        assert_eq!(codex.windows[0].remaining_percent, 90.0);
        assert_eq!(codex.windows[1].remaining_percent, 27.0);
        assert_eq!(codex.status, "detected");
        assert_eq!(codex.confidence, "authoritative");
        assert_eq!(codex.plan.as_deref(), Some("Plus"));
    }

    #[test]
    fn antigravity_is_read_through_its_own_app_source() {
        let view = map_snapshot(&parse(REAL_SHAPE), false, None);
        let antigravity = view
            .providers
            .iter()
            .find(|p| p.id == "antigravity")
            .unwrap();
        assert_eq!(antigravity.status, "detected");
        assert_eq!(antigravity.source.as_deref(), Some("app"));
        assert_eq!(
            antigravity.windows[0].label.as_deref(),
            Some("Gemini weekly")
        );
        assert_eq!(antigravity.plan.as_deref(), Some("Google AI Plus"));
    }

    #[test]
    fn local_only_sources_are_estimated_never_authoritative() {
        let view = map_snapshot(&parse(REAL_SHAPE), false, None);
        let opencodego = view
            .providers
            .iter()
            .find(|p| p.id == "opencodego")
            .unwrap();
        assert_eq!(opencodego.status, "detected");
        assert_eq!(opencodego.confidence, "authoritative");

        let local = view_for_row(
            "opencodego",
            "OpenCode Go",
            Some(&Row {
                id: Some("opencodego".into()),
                source: Some("local".into()),
                windows: vec![Window {
                    used_percent: Some(4.0),
                    remaining_percent: Some(96.0),
                    ..Window::default()
                }],
                ..Row::default()
            }),
        );
        assert_eq!(local.confidence, "estimated");
    }

    #[test]
    fn upstream_errors_are_translated_without_inventing_a_different_state() {
        let view = map_snapshot(&parse(REAL_SHAPE), false, None);
        let by = |id: &str| view.providers.iter().find(|p| p.id == id).unwrap().clone();
        assert_eq!(by("gemini").status, "unauthenticated");
        assert!(by("gemini")
            .diagnostic
            .as_deref()
            .unwrap()
            .contains("Not logged in"));
        assert_eq!(by("opencode").status, "unsupportedOs");
        assert_eq!(by("copilot").status, "noFetchStrategy");
        // Cursor and Grok are simply absent from this fixture: no row at all
        // means "not detected", never a fabricated state.
        assert_eq!(by("cursor").status, "notDetected");
        assert!(!by("cursor").installed);
    }

    #[test]
    fn missing_remaining_percent_is_derived_once_from_used() {
        let row = Row {
            id: Some("claude".into()),
            source: Some("oauth".into()),
            windows: vec![Window {
                used_percent: Some(37.5),
                remaining_percent: None,
                kind: Some("session".into()),
                ..Window::default()
            }],
            ..Row::default()
        };
        let view = view_for_row("claude", "Claude Code", Some(&row));
        assert_eq!(view.windows[0].used_percent, 37.5);
        assert_eq!(view.windows[0].remaining_percent, 62.5);
        assert_eq!(view.status, "detected");
    }

    #[test]
    fn out_of_range_percentages_are_clamped_as_a_final_guard() {
        let row = Row {
            windows: vec![Window {
                used_percent: Some(120.0),
                remaining_percent: Some(-5.0),
                ..Window::default()
            }],
            ..Row::default()
        };
        let view = view_for_row("cursor", "Cursor", Some(&row));
        assert_eq!(view.windows[0].used_percent, 100.0);
        assert_eq!(view.windows[0].remaining_percent, 0.0);
    }

    #[test]
    fn a_future_schema_version_is_a_readable_error_not_a_panic() {
        assert_eq!(
            parse_dashboard(br#"{"schemaVersion":99,"providers":[]}"#).unwrap_err(),
            "engine_schema_unsupported:99"
        );
        assert_eq!(
            parse_dashboard(b"not json").unwrap_err(),
            "engine_schema_invalid"
        );
    }

    #[test]
    fn the_engine_config_enables_the_allowlist_with_auto_sources() {
        let config: serde_json::Value = serde_json::from_slice(&engine_config()).unwrap();
        let providers = config["providers"].as_array().unwrap();
        assert_eq!(providers.len(), PROVIDER_ALLOWLIST.len());
        for (provider, (id, _)) in providers.iter().zip(PROVIDER_ALLOWLIST) {
            assert_eq!(provider["id"], *id);
            assert_eq!(provider["enabled"], true);
            // Never pin a source: upstream auto is the whole point.
            assert_eq!(provider["source"], "auto");
            // Never override cookieSource: it would disable upstream web
            // strategies (verified difference for Claude's auto chain).
            assert!(provider.get("cookieSource").is_none());
        }
    }

    #[test]
    #[ignore = "requires the bundled CodexBarCLI and the real user profile"]
    fn live_snapshot_matches_the_engine_directly() {
        let config_dir =
            std::env::temp_dir().join(format!("mg-engine-live-{}", std::process::id()));
        let resource_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("resources");
        let snapshot = snapshot(&config_dir, Some(&resource_dir));
        eprintln!(
            "engine={:?} duration={}ms error={:?} stale={}",
            snapshot.engine_version, snapshot.duration_ms, snapshot.engine_error, snapshot.stale
        );
        for provider in &snapshot.providers {
            eprintln!(
                "  {:<12} status={:<16} conf={:<13} src={:<10} windows={} plan={:?}",
                provider.id,
                provider.status,
                provider.confidence,
                provider.source.as_deref().unwrap_or("-"),
                provider.windows.len(),
                provider.plan
            );
        }
        assert!(snapshot.engine_error.is_none());
        assert!(snapshot
            .engine_version
            .as_deref()
            .is_some_and(|version| version.starts_with("0.60")));
        // The four providers verified live on this machine.
        let by = |id: &str| snapshot.providers.iter().find(|p| p.id == id).unwrap();
        assert_eq!(by("codex").status, "detected");
        assert_eq!(by("antigravity").status, "detected");
        assert_eq!(by("opencodego").status, "detected");
        assert_eq!(by("opencodego").confidence, "authoritative");
        let _ = std::fs::remove_dir_all(config_dir);
    }
}
