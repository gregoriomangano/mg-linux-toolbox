import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("Ripristino is gone from the sidebar, while every internal rollback mechanism stays", () => {
  const html = read("../src/index.html");
  const css = readFileSync(new URL("../src/styles/refinement.css", import.meta.url), "utf8");
  const frontend = read("../src/main.js");
  // The navigation entry (a disabled placeholder with no page) is removed...
  assert.doesNotMatch(html, /data-i18n="nav\.restore"/);
  assert.doesNotMatch(html, /<span data-i18n="nav\.restore">/);
  // ...together with the accent rule that targeted it, so the red never
  // slides onto the next item (Impostazioni).
  assert.doesNotMatch(css, /\.sidebar-bottom>\.nav-item:first-child \.pen-icon/);
  // ...but the real restore/rollback features are untouched.
  assert.match(html, /id="performance-restore"/, "performance rollback button must stay");
  assert.match(frontend, /invoke\("restore_apt_repository"/, "repository rollback must stay");
  assert.match(html, /pen-icons\.svg#restore/, "the restore icon is still used by Pulizia");
});

test("removing Ripristino left every other sidebar destination exactly where it was", () => {
  const html = read("../src/index.html");
  // The remaining user-facing destinations must all still be there, in order.
  const order = [
    'id="overview-nav"',
    'id="performance-nav"',
    'id="software-nav"',
    'id="programs-nav"',
    'id="settings-button"',
    'id="about-nav"',
  ].map((needle) => html.indexOf(needle));
  assert.ok(order.every((index) => index >= 0), "every destination must still exist");
  assert.deepEqual(
    order,
    [...order].sort((a, b) => a - b),
    "the navigation order must be unchanged: Overview, Performance, Software, Programs, Settings, About",
  );
  // Settings and About are still real, wired destinations (not just markup).
  assert.match(html, /id="settings-button"[^>]*data-i18n="nav\.settings"|id="settings-button"/);
  const frontend = read("../src/main.js");
  assert.match(frontend, /\$\("settings-button"\)\.addEventListener\("click"/);
  assert.match(frontend, /\$\("about-nav"\)\.addEventListener\("click"/);
});

test("no orphaned Ripristino strings are left in the translations", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  for (const language of ["it", "en"]) {
    assert.equal(
      dictionaries[language]["nav.restore"],
      undefined,
      `${language} must not keep the removed navigation label`,
    );
  }
  // ...and the two dictionaries must still expose exactly the same keys.
  assert.deepEqual(
    Object.keys(dictionaries.it).sort(),
    Object.keys(dictionaries.en).sort(),
    "Italian and English must stay in sync after the removal",
  );
});

test("Memoria/Rete/Sicurezza are hidden from the sidebar, not deleted, and Gestione software is a real nav item", () => {
  const html = read("../src/index.html");
  for (const id of ["nav.memory", "nav.network", "nav.security"]) {
    assert.match(html, new RegExp(`data-i18n="${id}"`), `${id} button markup should still exist`);
  }
  assert.match(html, /<button class="nav-item" aria-disabled="true" hidden>[^]*?nav\.memory/);
  assert.match(html, /<button class="nav-item" aria-disabled="true" hidden>[^]*?nav\.network/);
  assert.match(html, /<button class="nav-item" aria-disabled="true" hidden>[^]*?nav\.security/);
  assert.match(html, /id="software-nav"/);
  assert.match(html, /data-i18n="nav.software"/);
  // Order: Panoramica, Prestazioni, Gestione software must appear in that order.
  const order = [html.indexOf('id="overview-nav"'), html.indexOf('id="performance-nav"'), html.indexOf('id="software-nav"')];
  assert.ok(order.every((i) => i >= 0), "all three nav ids must exist");
  assert.ok(order[0] < order[1] && order[1] < order[2], "nav order must be Panoramica, Prestazioni, Gestione software");
});

test("the Software page is only loaded when the user navigates to it, never at startup", () => {
  const frontend = read("../src/main.js");
  assert.match(frontend, /software: \{content:"software-page", nav:"software-nav", breadcrumb:"breadcrumb.software", onEnter:loadSoftware\}/);
  const bootstrapLine = frontend.split("\n").find((line) => line.includes("refreshSystem();refreshPing();"));
  assert.ok(bootstrapLine, "bootstrap line not found");
  assert.doesNotMatch(bootstrapLine, /loadSoftware/);
  assert.doesNotMatch(frontend, /setInterval\([^)]*[Ss]oftware/);
});

test("the frontend never sends a path, shell command, or raw content to the repository commands", () => {
  const frontend = read("../src/main.js");
  assert.match(frontend, /invoke\("list_apt_repositories"\)/);
  assert.match(frontend, /invoke\("set_apt_repository_enabled",\{id:repo\.id,enabled:!repo\.enabled,expectedRevision:repo\.revision\}\)/);
  assert.match(frontend, /invoke\("restore_apt_repository",\{id:repo\.id,expectedRevision:repo\.revision\}\)/);
  // Only an opaque id + a boolean + the revision the backend itself handed
  // out ever cross the IPC boundary -- never repo.file, a shell string, or
  // file content.
  assert.doesNotMatch(frontend, /set_apt_repository_enabled",\{[^}]*file/);
  assert.doesNotMatch(frontend, /restore_apt_repository",\{[^}]*file/);
});

test("the repository helper is a separate binary and polkit action from the performance helper", () => {
  const lib = read("../src-tauri/src/lib.rs");
  assert.match(lib, /mod apt_ops;/);
  assert.match(lib, /mod apt_sources;/);
  assert.match(lib, /fn privileged_repository_helper_args/);
  assert.match(lib, /apt_ops::REPOSITORY_HELPER_PATH/);
  assert.doesNotMatch(lib, /apt_ops::REPOSITORY_HELPER_PATH[^;]*performance_profiles::HELPER_PATH/);
  const policy = read("../packaging/polkit/com.mg.linuxtoolbox.repository.policy");
  assert.match(policy, /com\.mg\.linuxtoolbox\.repository/);
  assert.doesNotMatch(policy, /com\.mg\.linuxtoolbox\.performance/);
  const packageConfig = read("../packaging/tauri.deb.conf.json");
  assert.match(packageConfig, /mg-linux-toolbox-repository-helper/);
  assert.match(packageConfig, /com\.mg\.linuxtoolbox\.repository\.policy/);
});

test("the repository helper only accepts the two closed, allow-listed operations", () => {
  const ops = read("../src-tauri/src/apt_ops.rs");
  assert.match(ops, /"apt-repo-set" \| "apt-repo-restore"/);
  assert.match(ops, /fn is_allowed_source_file/);
  assert.match(ops, /fn apt_dirs/);
  assert.doesNotMatch(ops, /Command::new\("sh"/);
  assert.doesNotMatch(ops, /Command::new\("bash"/);
  const helper = read("../src-tauri/src/bin/mg-linux-toolbox-repository-helper.rs");
  assert.match(helper, /apt_ops::is_operation/);
  assert.match(helper, /apt_ops::apply_operation/);
  assert.match(helper, /geteuid/);
});

test("Italian and English expose every software.* key the page uses", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  const keys = [
    "software.title", "software.intro", "software.sourcesTitle", "software.sourcesHint", "software.backendApt",
    "software.loading", "software.empty", "software.conflict",
    "software.stateActive", "software.stateDisabled", "software.stateManual", "software.stateIncompatible",
    "software.enable", "software.disable", "software.details", "software.restore",
    "software.official", "software.security",
    "software.detailBackend", "software.detailFormat", "software.detailFile", "software.detailTypes",
    "software.detailUris", "software.detailSuites", "software.detailComponents", "software.detailArch", "software.detailSignedBy",
    "software.formatList", "software.formatSources", "software.signedByEmbedded",
    "nav.software", "breadcrumb.software",
  ];
  for (const key of keys) {
    assert.ok(dictionaries.it[key], `Italian dictionary is missing ${key}`);
    assert.ok(dictionaries.en[key], `English dictionary is missing ${key}`);
  }
});
