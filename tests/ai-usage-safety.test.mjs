import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("the per-provider adapters are gone: CodexBar is the only engine", () => {
  for (const removed of [
    "../src-tauri/src/codex_usage.rs",
    "../src-tauri/src/codexbar_claude.rs",
    "../src-tauri/src/codexbar_provider.rs",
  ]) {
    assert.equal(existsSync(new URL(removed, import.meta.url)), false, `${removed} should be deleted`);
  }
  const lib = read("../src-tauri/src/lib.rs");
  assert.match(lib, /mod codexbar_engine;/);
  assert.doesNotMatch(lib, /mod codex_usage;|mod codexbar_claude;|mod codexbar_provider;/);
});

test("one engine snapshot for the whole section, no per-provider commands", () => {
  const lib = read("../src-tauri/src/lib.rs");
  const frontend = read("../src/main.js");
  assert.match(lib, /async fn get_ai_usage/);
  for (const gone of [
    "get_ai_tools",
    "refresh_claude_oauth_usage",
    "refresh_gemini_usage",
    "refresh_antigravity_usage",
    "refresh_opencode_go_usage",
  ]) {
    assert.doesNotMatch(lib, new RegExp(gone), `${gone} should be removed from lib.rs`);
    assert.doesNotMatch(frontend, new RegExp(gone), `${gone} should be removed from main.js`);
  }
  const invokes = frontend.match(/invoke\("get_ai_usage"\)/g) || [];
  assert.equal(invokes.length, 1);
});

test("the engine runs one dashboard snapshot and never pins a provider source", () => {
  const engine = read("../src-tauri/src/codexbar_engine.rs");
  assert.match(engine, /"dashboard"/);
  assert.match(engine, /--identity",\s*"redacted"/);
  // Upstream auto strategies are mandatory: pinning a source was verified to
  // break Antigravity (only its `app` strategy works) and to skip Claude's
  // real auto chain.
  const configBuilder = engine.split("fn engine_config")[1].split("fn ensure_engine_config")[0];
  assert.match(configBuilder, /"source": "auto"/);
  // cookieSource must never be overridden in the generated config: it would
  // disable upstream web strategies (verified difference in Claude's chain).
  assert.doesNotMatch(configBuilder, /cookieSource/);
  // No second engine: no provider-specific CLI arguments anywhere.
  assert.doesNotMatch(engine, /"--provider"/);
});

test("the UI allowlist is fixed, ordered, and filters everything else out", () => {
  const engine = read("../src-tauri/src/codexbar_engine.rs");
  const expected = [
    ["codex", "OpenAI Codex"],
    ["claude", "Claude Code"],
    ["gemini", "Google Gemini"],
    ["antigravity", "Google Antigravity"],
    ["cursor", "Cursor"],
    ["opencode", "OpenCode"],
    ["copilot", "GitHub Copilot"],
    ["grok", "Grok / xAI"],
    ["opencodego", "OpenCode Go"],
  ];
  for (const [id] of expected) {
    assert.match(engine, new RegExp(`\\("${id}",`), `allowlist should contain ${id}`);
  }
  assert.match(engine, /filter.*find.*allowlist|PROVIDER_ALLOWLIST/s);
});

test("used and remaining percentages are preserved, converted once, never in the frontend", () => {
  const engine = read("../src-tauri/src/codexbar_engine.rs");
  const frontend = read("../src/main.js");
  assert.match(engine, /pub used_percent: f64/);
  assert.match(engine, /pub remaining_percent: f64/);
  assert.match(engine, /100\.0 - used/);
  // The frontend must not contain any used->remaining conversion.
  assert.doesNotMatch(frontend, /100\s*-\s*window\.usedPercent/);
  assert.doesNotMatch(frontend, /100\s*-\s*used/);
  const utils = read("../src/js/ui-utils.js");
  assert.doesNotMatch(utils, /remainingQuota/);
});

test("confidence is preserved and estimated readings never render as real quotas", () => {
  const engine = read("../src-tauri/src/codexbar_engine.rs");
  const frontend = read("../src/main.js");
  assert.match(engine, /"estimated"/);
  assert.match(engine, /"authoritative"/);
  assert.match(frontend, /provider\.confidence!=="authoritative"/);
  assert.match(frontend, /ai\.estimatedNotice/);
});

test("the OpenCode Go bridge is the only credential reader, env-only, never logged", () => {
  const engine = read("../src-tauri/src/codexbar_engine.rs");
  const bridge = read("../src-tauri/src/opencode_go.rs");
  assert.match(engine, /opencode_go::api_key\(\)/);
  assert.match(engine, /OPENCODE_API_KEY/);
  // Production paths must never log; only the ignored live test (behind
  // #[cfg(test)]) is allowed to print diagnostics, and only status labels.
  const engineProduction = engine.split("#[cfg(test)]")[0];
  assert.doesNotMatch(engineProduction, /println!|eprintln!|dbg!|log::/);
  assert.doesNotMatch(bridge, /println!|eprintln!|dbg!|log::/);
  const lib = read("../src-tauri/src/lib.rs");
  assert.match(lib, /mod opencode_go;/);
  // No tool may read provider credential files other than this bridge.
  assert.doesNotMatch(engine, /auth\.json|\.credentials\.json|cookies\.sqlite/);
});

test("a failed run falls back to the last valid snapshot, clearly marked stale", () => {
  const engine = read("../src-tauri/src/codexbar_engine.rs");
  const frontend = read("../src/main.js");
  assert.match(engine, /fn snapshot\(config_dir: &Path, resource_dir: Option<&Path>\)/);
  assert.match(engine, /fn fallback_or_error/);
  assert.match(engine, /stale = true/);
  assert.match(frontend, /ai\.staleNotice/);
  assert.match(frontend, /snapshot\.stale/);
});

test("The engine version and schema stay visible and versioned", () => {
  const engine = read("../src-tauri/src/codexbar_engine.rs");
  assert.match(engine, /schema_version/);
  assert.match(engine, /engine_schema_unsupported/);
  assert.match(engine, /codex_bar_version/);
  const frontend = read("../src/main.js");
  assert.match(frontend, /CodexBar \$\{snapshot\.engineVersion\}/);
});

test("AI refresh is one freshness-aware single flight and pauses when hidden", () => {
  const frontend = read("../src/main.js");
  const policy = read("../src/js/ai-refresh-policy.js");
  const html = read("../src/index.html");
  assert.match(policy, /AI_DEFAULT_FRESHNESS_SECONDS = 180/);
  assert.match(policy, /AI_MAX_FRESHNESS_SECONDS = 300/);
  assert.match(policy, /staleAfterSeconds/);
  assert.match(policy, /Date\.parse\(snapshot\?\.generatedAt/);
  assert.doesNotMatch(frontend, /30\s*\*\s*60\s*\*\s*1000/);
  assert.match(frontend, /if\(aiRefreshFlight\)return aiRefreshFlight/);
  assert.match(frontend, /if\(document\.hidden\)\{clearTimeout\(aiTimer\);aiTimer=null;return;\}/);
  assert.match(frontend, /if\(page!=="overview"\)\{clearTimeout\(aiTimer\);aiTimer=null;\}/);
  assert.match(frontend, /if\(aiSnapshotIsStale\(state\.ai\)\)refreshAi\(\)/);
  assert.match(html, /id="ai-refresh-button"/);
  assert.match(html, /data-i18n-title="ai\.refreshNow"/);
  assert.equal((frontend.match(/invoke\("get_ai_usage"\)/g) || []).length, 1);
});

test("AI metadata wraps by whole fields and quota grids handle two or three windows", () => {
  const frontend = read("../src/main.js");
  const css = read("../src/styles/refinement.css");
  assert.match(frontend, /ai-metadata/);
  assert.match(css, /\.ai-metadata \{[^}]*display:flex[^}]*flex-wrap:wrap/s);
  assert.match(css, /\.ai-metadata small \{[^}]*white-space:nowrap[^}]*word-break:normal/s);
  assert.match(css, /grid-template-columns:repeat\(auto-fit,minmax\(180px,1fr\)\)/);
});

test("Claude reconnect delegates only to the official Claude Code login", () => {
  const bridge = read("../src-tauri/src/claude_auth.rs");
  const lib = read("../src-tauri/src/lib.rs");
  const frontend = read("../src/main.js");
  assert.match(bridge, /\["auth", "login", "--claudeai"\]/);
  assert.match(bridge, /\["auth", "status", "--json"\]/);
  assert.match(bridge, /xdg-terminal-exec/);
  assert.doesNotMatch(bridge, /--title|--hold/);
  assert.doesNotMatch(bridge, /read_to_string|credentials\.json|reqwest|https?:\/\//);
  assert.match(lib, /get_claude_auth_status/);
  assert.match(lib, /reconnect_claude_code/);
  assert.match(frontend, /ai\.claudeReconnectNeeded/);
  assert.match(frontend, /reconnectClaudeCode/);
});

test("Italian and English expose every ai.* key the page uses", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  const keys = [
    "ai.detecting", "ai.detected", "ai.notDetected", "ai.unauthenticated",
    "ai.usageUnavailable", "ai.notSupported", "ai.temporary",
    "ai.reasonUnsupportedOs", "ai.reasonNoFetchStrategy",
    "ai.readFailed", "ai.updated", "ai.refreshNow", "ai.refreshing",
    "ai.claudeReconnectNeeded", "ai.reconnect", "ai.reconnecting",
    "ai.estimatedNotice", "ai.source", "ai.plan", "ai.staleNotice",
    "quota.remaining", "quota.reset", "quota.fiveHours", "quota.week", "quota.month", "quota.day",
  ];
  for (const key of keys) {
    assert.ok(dictionaries.it[key], `Italian dictionary is missing ${key}`);
    assert.ok(dictionaries.en[key], `English dictionary is missing ${key}`);
  }
  const leftovers = [...Object.keys(dictionaries.it), ...Object.keys(dictionaries.en)].filter(
    (key) => /cookiePaste/i.test(key),
  );
  assert.deepEqual([...new Set(leftovers)], []);
});
