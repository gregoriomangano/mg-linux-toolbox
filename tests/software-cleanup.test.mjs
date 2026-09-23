import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("the cleanup section lives inside the existing software page, after Sorgenti software and Autostart", () => {
  const html = read("../src/index.html");
  const softwarePageMatch = html.match(/<main class="performance" id="software-page" hidden>[^]*?<\/main>/);
  assert.ok(softwarePageMatch, "software-page main element not found");
  const page = softwarePageMatch[0];
  assert.match(page, /data-control="cleanup"/, "a summary card for cleanup must exist");
  assert.match(page, /id="cleanup-panel-title"/);
  assert.match(page, /id="cleanup-scan-button"/);
  assert.match(page, /data-i18n="software\.cleanupScan">Analizza</, "the initial action must be Analizza, never an immediate clean");
  assert.doesNotMatch(page, />Pulisci subito</);
  assert.match(page, /id="cleanup-cookies" class="cleanup-cookies" hidden>/, "cookies must start hidden until an analysis actually finds them");
  assert.match(page, /data-i18n="software\.cleanupCookiesWarning"/);
  const order = [page.indexOf('id="repo-list"'), page.indexOf('data-control="autostart"'), page.indexOf('data-control="cleanup"')];
  assert.ok(order.every((i) => i >= 0));
  assert.ok(order[0] < order[1] && order[1] < order[2], "Sorgenti software, Autostart, Pulizia must appear in this order");
});

test("the frontend only ever sends opaque item/cookie ids to the cleanup commands, never a path or raw content", () => {
  const frontend = read("../src/main.js");
  assert.match(frontend, /invoke\("scan_cleanup_targets"\)/);
  assert.match(frontend, /invoke\("clean_selected_cleanup_targets",\{selection:\{itemIds,cookieGroupIds:cookieIds\}\}\)/);
  assert.doesNotMatch(frontend, /clean_selected_cleanup_targets",\{[^}]*path/i);
  assert.doesNotMatch(frontend, /clean_selected_cleanup_targets",\{[^}]*content/i);
});

test("cookies are never part of the default selection computed after a scan", () => {
  const frontend = read("../src/main.js");
  assert.match(
    frontend,
    /state\.cleanupSelection=new Set\(state\.cleanup\.categories\.filter\(c=>c\.selectedByDefault\)\.flatMap\(c=>c\.items\.map\(i=>i\.id\)\)\);\s*state\.cleanupCookieSelection=new Set\(\);/,
    "only categories' own selectedByDefault ever seeds the selection; cookieGroups never do",
  );
});

test("the trash category is never selected by default, matching the backend", () => {
  const source = read("../src-tauri/src/cleanup.rs");
  assert.match(source, /selected_by_default: false,\s*items,\s*\}\s*\}/, "trash_category must end with selected_by_default:false");
});

test("the kernel page cache is never dropped: no write to /proc/sys/vm/drop_caches", () => {
  const source = read("../src-tauri/src/cleanup.rs");
  // The module intentionally documents the decision in a comment, but must
  // never contain the write itself.
  assert.match(source, /never touches the kernel page[\s\S]{0,20}cache/);
  assert.doesNotMatch(source, /fs::write\([^)]*drop_caches/);
  assert.doesNotMatch(source, /File::create\([^)]*drop_caches/);
  assert.doesNotMatch(source, /Command::new/, "cleanup.rs must never shell out");
});

test("every deletion re-derives the real path fresh and re-verifies it against the allow-listed roots", () => {
  const source = read("../src-tauri/src/cleanup.rs");
  assert.match(source, /fn clean_selected_with_roots/);
  assert.match(source, /discover_all\(roots\)/);
  assert.match(source, /fn is_allowed/);
  assert.match(source, /fs::canonicalize/);
});

test("cookie cleanup only ever touches the literal cookie database file, never a password store", () => {
  const source = read("../src-tauri/src/browser_cleanup.rs");
  assert.match(source, /fn delete_cookie_group/);
  assert.match(source, /file_name != "Cookies" && file_name != "cookies\.sqlite"/);
  assert.doesNotMatch(source, /remove_file\([^)]*Login Data/);
  assert.doesNotMatch(source, /remove_file\([^)]*logins\.json/);
  assert.doesNotMatch(source, /remove_file\([^)]*key4\.db/);
  assert.match(source, /browser_running/, "a running browser must refuse cookie deletion");
});

test("lib.rs registers the cleanup commands", () => {
  const lib = read("../src-tauri/src/lib.rs");
  assert.match(lib, /mod cleanup;/);
  assert.match(lib, /mod browser_cleanup;/);
  assert.match(lib, /cleanup::scan_cleanup_targets/);
  assert.match(lib, /cleanup::clean_selected_cleanup_targets/);
});

test("Italian and English expose every software.cleanup* key the page uses", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  const keys = [
    "software.cleanupTitle", "software.cleanupHint",
    "software.cleanupTempPreview", "software.cleanupBrowserPreview", "software.cleanupAvailable", "software.cleanupNone",
    "software.cleanupScan", "software.cleanupScanning", "software.cleanupReclaimable",
    "software.cleanupCategoryTemp", "software.cleanupCategoryAppCache", "software.cleanupCategoryBrowserCache", "software.cleanupCategoryThumbnails", "software.cleanupCategoryTrash",
    "software.cleanupShowItems", "software.cleanupCookiesTitle", "software.cleanupCookiesWarning",
    "software.cleanupBrowserOpen", "software.cleanupCleanSelected",
    "software.cleanupConfirm", "software.cleanupCookieConfirm", "software.cleanupFreed", "software.cleanupSkipped",
    "software.cleanupCardText", "software.cleanupHintLong", "software.cleanupNotAnalyzed", "software.cleanupAnalyzed",
  ];
  for (const key of keys) {
    assert.ok(dictionaries.it[key], `Italian dictionary is missing ${key}`);
    assert.ok(dictionaries.en[key], `English dictionary is missing ${key}`);
  }
});
