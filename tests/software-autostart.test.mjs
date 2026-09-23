import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("the autostart section lives inside the existing software page, alongside Sorgenti software", () => {
  const html = read("../src/index.html");
  const softwarePageMatch = html.match(/<main class="performance" id="software-page" hidden>[^]*?<\/main>/);
  assert.ok(softwarePageMatch, "software-page main element not found");
  const page = softwarePageMatch[0];
  assert.match(page, /id="repo-list"/, "the existing Sorgenti software block must stay untouched");
  assert.match(page, /data-control="autostart"/, "a summary card for autostart must exist");
  assert.match(page, /id="autostart-panel-title"/);
  assert.match(page, /id="autostart-list"/);
  assert.match(page, /id="autostart-search"/);
  assert.match(page, /data-filter="all"/);
  assert.match(page, /data-filter="active"/);
  assert.match(page, /data-filter="disabled"/);
  // The list must appear after Sorgenti software, never before or replacing it.
  assert.ok(page.indexOf('id="repo-list"') < page.indexOf('id="autostart-list"'));
});

test("loadSoftware is still the only onEnter for the software page, and it now also drives autostart", () => {
  const frontend = read("../src/main.js");
  assert.match(frontend, /software: \{content:"software-page", nav:"software-nav", breadcrumb:"breadcrumb.software", onEnter:loadSoftware\}/);
  assert.match(frontend, /loadAutostart\(\);\s*scanCleanup\(\);\s*if\(state\.softwareLoading\)return;/);
  const bootstrapLine = frontend.split("\n").find((line) => line.includes("refreshSystem();refreshPing();"));
  assert.ok(bootstrapLine, "bootstrap line not found");
  assert.doesNotMatch(bootstrapLine, /loadAutostart/);
  assert.doesNotMatch(frontend, /setInterval\([^)]*[Aa]utostart/);
});

test("the frontend only ever sends an opaque id and a boolean to the autostart command, never a path", () => {
  const frontend = read("../src/main.js");
  assert.match(frontend, /invoke\("list_autostart_entries"\)/);
  assert.match(frontend, /invoke\("set_autostart_entry_enabled",\{id:entry\.id,enabled:!entry\.enabled\}\)/);
  assert.doesNotMatch(frontend, /set_autostart_entry_enabled",\{[^}]*file/);
  assert.doesNotMatch(frontend, /set_autostart_entry_enabled",\{[^}]*path/i);
});

test("autostart is XDG-standard, cross-desktop, and requires no root", () => {
  const source = read("../src-tauri/src/xdg_autostart.rs");
  assert.match(source, /XDG_CONFIG_HOME/);
  assert.match(source, /XDG_CONFIG_DIRS/);
  assert.match(source, /PathBuf::from\("\/etc\/xdg"\)/, "the documented /etc/xdg fallback must be present");
  assert.match(source, /XDG_CURRENT_DESKTOP/);
  assert.match(source, /OnlyShowIn|only_show_in/);
  assert.match(source, /NotShowIn|not_show_in/);
  assert.doesNotMatch(source, /Command::new\("pkexec"\)/);
  assert.doesNotMatch(source, /Command::new\("sudo"\)/);
  assert.doesNotMatch(source, /GNOME_DESKTOP_SESSION_ID/, "detection must not be GNOME-only");
});

test("the id passed back to the backend is re-resolved fresh, never trusted as a path", () => {
  const source = read("../src-tauri/src/xdg_autostart.rs");
  assert.match(source, /fn set_entry_enabled_with_dirs/);
  assert.match(source, /entry_id\(name\) == id/);
  assert.match(source, /autostart_entry_not_found/);
});

test("lib.rs registers the autostart commands", () => {
  const lib = read("../src-tauri/src/lib.rs");
  assert.match(lib, /mod xdg_autostart;/);
  assert.match(lib, /xdg_autostart::list_autostart_entries/);
  assert.match(lib, /xdg_autostart::set_autostart_entry_enabled/);
});

test("Italian and English expose every software.autostart* key the page uses", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  const keys = [
    "software.autostartTitle", "software.autostartHint", "software.autostartBadge",
    "software.autostartLoading", "software.autostartEmpty",
    "software.autostartActiveCount", "software.autostartDisabledCount", "software.autostartManage",
    "software.autostartSearchPlaceholder", "software.autostartFilterAll", "software.autostartFilterActive", "software.autostartFilterDisabled",
    "software.autostartNoMatch", "software.autostartOn", "software.autostartOff",
    "software.autostartEnable", "software.autostartDisable",
    "software.autostartOriginUser", "software.autostartOriginSystem",
    "software.autostartDetailOrigin", "software.autostartDetailFile", "software.autostartDetailExec", "software.autostartDetailIcon", "software.autostartDetailDesktop",
    "software.autostartNotForThisDesktop", "software.autostartCardText", "software.autostartHintLong",
  ];
  for (const key of keys) {
    assert.ok(dictionaries.it[key], `Italian dictionary is missing ${key}`);
    assert.ok(dictionaries.en[key], `English dictionary is missing ${key}`);
  }
});
