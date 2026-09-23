import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("CPU Boost and temporary performance stay removed", () => {
  assert.equal(existsSync(new URL("../src-tauri/src/cpu_boost.rs", import.meta.url)), false);
  const ppd = read("../src-tauri/src/power_profiles_daemon.rs");
  assert.doesNotMatch(ppd, /HoldProfile|ReleaseProfile|HoldSession|ActiveProfileHolds/);
  for (const file of [
    "../src-tauri/src/lib.rs",
    "../src-tauri/src/performance.rs",
    "../src-tauri/src/advanced_controls.rs",
    "../src-tauri/src/advanced_ops.rs",
    "../src/main.js",
    "../src/index.html",
  ]) {
    assert.doesNotMatch(read(file), /boost/i, `${file} should not mention boost`);
  }
});

test("BBR is fully removed from code, UI and translations", () => {
  for (const file of [
    "../src-tauri/src/advanced_ops.rs",
    "../src-tauri/src/advanced_controls.rs",
    "../src/main.js",
    "../src/index.html",
    "../src/js/i18n.js",
    "../src/styles/refinement.css",
  ]) {
    assert.doesNotMatch(read(file), /bbr/i, `${file} should not mention bbr`);
  }
});

test("advanced operations are closed and include the safe allow-listed names", () => {
  const ops = read("../src-tauri/src/advanced_ops.rs");
  for (const name of [
    "desktop-autogroup-set",
    "thp-set",
    "ksm-set",
    "zswap-set",
    "fstrim-timer-set",
  ]) {
    assert.match(ops, new RegExp(name));
  }
  assert.match(ops, /write_allowed/);
  assert.match(ops, /invalid_operation/);
  // Fixed kernel attributes are chosen by the helper, never passed in.
  assert.match(ops, /const KSM_RUN: &str = "\/sys\/kernel\/mm\/ksm\/run"/);
});

test("fstrim is constrained to the fixed timer and does not mutate storage configuration", () => {
  const controls = read("../src-tauri/src/advanced_controls.rs");
  const ops = read("../src-tauri/src/advanced_ops.rs");
  assert.match(controls, /pub const ID_FSTRIM: &str = "fstrim"/);
  assert.match(controls, /\.args\(\["is-enabled", "--quiet", FSTRIM_TIMER\]\)/);
  assert.match(controls, /\.args\(\["is-active", "--quiet", FSTRIM_TIMER\]\)/);
  assert.match(controls, /NAME,TYPE,ROTA,DISC-MAX,MOUNTPOINTS/);
  assert.match(controls, /fstrim-continuous-discard/);
  assert.match(controls, /fstrim-not-needed/);
  assert.match(controls, /fstrim-recommended/);
  assert.match(ops, /\["enable", "--now", "fstrim\.timer"\]/);
  assert.match(ops, /\["disable", "--now", "fstrim\.timer"\]/);
  for (const source of [controls, ops]) {
    assert.doesNotMatch(source, /blkdiscard|\/etc\/fstab|mount\s+-o|umount|cryptsetup|lvchange/i);
  }
});

test("MGLRU is not exposed because its bitmask cannot be restored safely", () => {
  for (const file of ["../src-tauri/src/advanced_controls.rs", "../src-tauri/src/advanced_ops.rs", "../src/main.js"]) {
    assert.doesNotMatch(read(file), /mglru/i);
  }
});

test("the helper dispatches advanced operations and keeps the profile path", () => {
  const helper = read("../src-tauri/src/bin/mg-linux-toolbox-performance-helper.rs");
  assert.match(helper, /advanced_ops::is_operation/);
  assert.match(helper, /advanced_ops::apply_operation/);
  assert.match(helper, /"apply-persisted"/);
});

test("inhibit keeps using the existing portal mechanism", () => {
  const lib = read("../src-tauri/src/lib.rs");
  assert.match(lib, /get_inhibit_status/);
  assert.match(lib, /set_inhibit_active/);
  assert.match(lib, /inhibit::InhibitState/);
  const inhibit = read("../src-tauri/src/inhibit.rs");
  assert.match(inhibit, /org\.freedesktop\.portal\.Inhibit/);
  const main = read("../src/main.js");
  assert.match(main, /set_inhibit_active/);
  assert.match(main, /get_inhibit_status/);
});

test("the health frame is informational only and the advanced grid holds the safe cards", () => {
  const html = read("../src/index.html");
  for (const id of ["health-cpu", "health-io", "health-thermal", "health-speed", "advanced-grid"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.doesNotMatch(html, /health-inhibit|inhibit-card|cpu-speed-card|pressure-card|temperature-card/);
  const main = read("../src/main.js");
  // Backend controls and the inhibit card are all rendered into the same grid.
  assert.match(main, /advanced-grid/);
  assert.match(main, /function buildFeatureCard/);
  assert.match(main, /inhibit-toggle/);
  assert.doesNotMatch(main, /health-inhibit/);
});

test("Italian and English expose control + technical-label keys and no removed ones", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  const ids = ["autogroup", "thp", "ksm", "zswap", "inhibit", "fstrim"];
  const keys = [
    "performance.speedLabel",
    "performance.controlEnable", "performance.controlDisable", "performance.controlSystem",
    "performance.controlManagedHint", "performance.controlUnavailableHint",
    ...ids.flatMap((id) => [
      `performance.control.${id}.title`,
      `performance.control.${id}.text`,
      `performance.control.${id}.tech`,
    ]),
  ];
  for (const key of keys) {
    assert.ok(dictionaries.it[key], `Italian dictionary is missing ${key}`);
    assert.ok(dictionaries.en[key], `English dictionary is missing ${key}`);
  }
  const leftovers = [...Object.keys(dictionaries.it), ...Object.keys(dictionaries.en)].filter(
    (key) => /boost|bbr|performance\.temporary|inhibitShort|inhibitHint|inhibitOn|inhibitOff|performance\.details\b|performance\.recommendation(Recommended|Situational|NotNeeded|Managed)\b|performance\.controlOn\b|performance\.controlOff\b|performance\.controlManaged\b|performance\.controlSystemManaged\b|performance\.controlIncompatible\b|performance\.controlUnavailable\b/i.test(key),
  );
  assert.deepEqual([...new Set(leftovers)], []);
});

test("the section is renamed to 'Funzioni opzionali' and the old advanced-controls title/copy is gone", () => {
  const html=read("../src/index.html"),main=read("../src/main.js");
  assert.doesNotMatch(html,/Come vuoi far lavorare il computer|Scegli un risultato/);
  assert.doesNotMatch(html,/Regolazioni avanzate/);
  assert.match(html,/data-i18n="performance\.advancedTitle">Funzioni opzionali</);
  assert.match(main,/detect_fstrim|fstrim/);
  assert.match(main,/function renderAdvancedControls/);
});

test("every one of the six cards has exactly one status, at most one badge, one sentence and two actions -- no expandable content, no microscopic technology label", () => {
  const main = read("../src/main.js");
  const css = read("../src/styles/refinement.css");
  // No <details>/accordion and no "Quando conviene?"-style permanent guidance left in the card path.
  assert.doesNotMatch(main, /adv-details|adv-guidance|adv-recommendation|adv-tech/);
  assert.doesNotMatch(css, /\.adv-details|\.adv-guidance|\.adv-recommendation/);
  // The technology name/tech-label line and any <details> are never built inside buildFeatureCard.
  const cardBody = main.slice(main.indexOf("function buildFeatureCard"), main.indexOf("function renderAdvancedControls"));
  assert.doesNotMatch(cardBody, /\.tech`/);
  assert.doesNotMatch(cardBody, /element\("details"/);
  assert.match(cardBody, /perf-feature-head/);
  assert.match(cardBody, /perf-feature-status/);
  assert.match(cardBody, /perf-feature-badge/);
  assert.match(cardBody, /perf-feature-desc/);
  assert.match(cardBody, /perf-feature-actions/);
  assert.match(cardBody, /performance\.learnMore/);
  assert.match(css, /#advanced-grid \.perf-feature-card/);
});

test("status is tri-state (on/off/dash) and only one evaluative tone drives both the card badge and the dialog verdict", () => {
  const main = read("../src/main.js");
  assert.match(main, /function controlStatusKind/);
  assert.match(main, /function controlEvaluation/);
  assert.match(main, /control\.note==="zswap-zram"\|\|control\.note==="zswap-no-swap"/);
  assert.match(main, /EVALUATION_BADGE_KEY/);
  assert.match(main, /EVALUATION_VERDICT_KEY/);
  assert.match(main, /performance\.recommendationUnknown/);
});

test("the six titles use plain Italian names while the technical labels stay accurate and now live only in the help dialog", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  const expectedTitles = {
    autogroup: "Reattività del desktop",
    thp: "Memoria per carichi pesanti",
    ksm: "Risparmio RAM per macchine virtuali",
    zswap: "Swap compresso",
    fstrim: "Manutenzione SSD automatica",
    inhibit: "Evita la sospensione automatica",
  };
  for (const [id, title] of Object.entries(expectedTitles)) {
    assert.equal(dictionaries.it[`performance.control.${id}.title`], title);
  }
  const expectedTech = {
    thp: /Transparent Huge Pages/,
    ksm: /Kernel Samepage Merging/,
    zswap: /zswap/,
    fstrim: /fstrim\.timer/,
    inhibit: /Inhibit/,
  };
  for (const [id, pattern] of Object.entries(expectedTech)) {
    assert.match(dictionaries.it[`performance.control.${id}.tech`], pattern);
    assert.match(dictionaries.en[`performance.control.${id}.tech`], pattern);
  }
});

test("a single reusable help dialog exists, closes only by X/Escape/backdrop, and is never rebuilt or closed by a periodic refresh", () => {
  const html = read("../src/index.html");
  const main = read("../src/main.js");
  assert.match(html, /<dialog id="advanced-help-dialog"/);
  assert.match(html, /data-close="advanced-help-dialog"/);
  assert.match(html, /id="advanced-help-title"/);
  assert.match(html, /id="advanced-help-body"/);
  assert.match(html, /id="advanced-help-action"/);
  // Reuses the app's one generic close-button/backdrop-click system: no bespoke close wiring.
  assert.match(main, /for\(const button of document\.querySelectorAll\("\[data-close\]"\)\)/);
  assert.match(main, /for\(const dialog of document\.querySelectorAll\("dialog"\)\)/);
  // Opening builds the static content once; the periodic path only patches values.
  assert.match(main, /function openAdvancedHelp/);
  assert.match(main, /function updateAdvancedHelpDynamic/);
  const dynamicBody = main.slice(main.indexOf("function updateAdvancedHelpDynamic"), main.indexOf("async function toggleAdvanced"));
  assert.doesNotMatch(dynamicBody, /\.showModal\(\)|\.close\(\)|replaceChildren/);
  // renderAdvancedControls (called by the 3s poll) only patches the dialog, it never opens/closes it.
  const renderBody = main.slice(main.indexOf("function renderAdvancedControls()"), main.indexOf("function helpSection"));
  assert.match(renderBody, /updateAdvancedHelpDynamic\(\)/);
  assert.doesNotMatch(renderBody, /advanced-help-dialog/);
  // Focus returns to the button that opened the dialog when it closes.
  assert.match(main, /advancedHelpOpener\?\.focus\(\)/);
});

test("the help dialog content is built per control from the real detected state, includes what/useful/off/changes/recommend/technical sections, and the action button reuses the same toggle", () => {
  const main = read("../src/main.js");
  const body = main.slice(main.indexOf("function openAdvancedHelp"), main.indexOf("function updateAdvancedHelpDynamic"));
  assert.match(body, /performance\.help\.whatIsTitle/);
  assert.match(body, /performance\.help\.usefulTitle/);
  assert.match(body, /performance\.help\.offTitle/);
  assert.match(body, /performance\.help\.changesTitle/);
  assert.match(body, /performance\.help\.recommendTitle/);
  assert.match(body, /performance\.help\.techTitle/);
  assert.match(body, /toggleControl\(id,!resolveHelpControl\(id\)\.enabled\)/);
  assert.match(main, /function toggleControl/);
  assert.match(main, /function resolveHelpControl/);
});

test("every control's help content (what/useful/off/changes/reco) exists in both languages, matching this session's real backend behaviour", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  const HELP_USEFUL_ITEMS = {
    autogroup: ["desktop", "conversions", "compiling", "compression", "cpuBusy"],
    thp: ["vms", "memoryHeavy", "games"],
    ksm: ["multipleVms", "duplicatedMemory"],
    zswap: ["diskSwap", "ramPressure", "fewerWrites"],
    fstrim: ["ssdNvme", "compatibleFs", "noOtherMechanism"],
    inhibit: ["presentations", "downloads", "conversions", "processing", "video"],
  };
  const HELP_OFF_ITEMS = {
    autogroup: ["servers", "batch", "cgroupManaged"],
    thp: ["databases", "specificConfig", "noBenefit"],
    ksm: ["normalDesktop", "noVms", "notWorthCpu"],
    zswap: ["noSwap", "zramRedundant", "managedDifferently"],
    fstrim: ["hdd", "noDiscard", "alreadyManaged"],
    inhibit: ["normalUse"],
  };
  for (const id of Object.keys(HELP_USEFUL_ITEMS)) {
    for (const language of ["it", "en"]) {
      const dict = dictionaries[language];
      assert.ok(dict[`performance.help.${id}.what`], `${language} missing ${id} what`);
      assert.ok(dict[`performance.help.${id}.changes`], `${language} missing ${id} changes`);
      assert.ok(dict[`performance.reco.${id}`], `${language} missing ${id} reco`);
      for (const key of HELP_USEFUL_ITEMS[id]) assert.ok(dict[`performance.help.${id}.useful.${key}`], `${language} missing ${id} useful.${key}`);
      for (const key of HELP_OFF_ITEMS[id]) assert.ok(dict[`performance.help.${id}.off.${key}`], `${language} missing ${id} off.${key}`);
    }
  }
  // THP explicitly explains the madvise-only behaviour, never a bare contradiction.
  assert.match(dictionaries.it["performance.help.thp.changes"], /madvise/);
  // fstrim never touches fstab/LUKS/mount options, matching the backend.
  assert.match(dictionaries.it["performance.help.fstrim.changes"], /fstab/);
  // Inhibit is explicit that it never improves performance.
  assert.match(dictionaries.it["performance.help.inhibit.changes"], /non migliora le prestazioni/i);
});

test("toggling a control (from the card or the dialog) updates both the card and an open dialog, and never regresses the PPD profiles or the other Performance sections", () => {
  const main = read("../src/main.js");
  assert.match(main, /async function toggleAdvanced\(id,enabled\)/);
  assert.match(main, /async function toggleInhibit\(\)/);
  assert.doesNotMatch(main, /function renderInhibit/);
  // Both toggle paths re-render the six cards (which also patches the dialog if open).
  const toggleAdvancedBody = main.slice(main.indexOf("async function toggleAdvanced("), main.indexOf("async function loadAdvanced"));
  assert.match(toggleAdvancedBody, /renderAdvancedControls\(\)/);
  const toggleInhibitBody = main.slice(main.indexOf("async function toggleInhibit()"), main.indexOf("async function applyPerformanceProfile"));
  assert.match(toggleInhibitBody, /renderAdvancedControls\(\)/);
  // The three PPD profile cards and their apply/restore commands are untouched.
  assert.match(main, /async function applyPerformanceProfile/);
  assert.match(main, /async function restorePerformanceState/);
  assert.match(main, /apply_performance_profile/);
  assert.match(main, /restore_previous_performance_state/);
  // "Il tuo sistema" and "Come sta lavorando il computer" keep their own render path.
  assert.match(main, /function renderCpuDetails/);
  assert.match(main, /setHealth\("health-cpu"/);
});

test("the layout stays 3x2 on desktop, 2 columns on medium and 1 column on small, shared with the grid used elsewhere", () => {
  const css = read("../src/styles/refinement.css");
  assert.match(css, /\.performance-advanced-grid \{display:grid;grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
  assert.match(css, /@media\(max-width:1050px\) \{\.performance-advanced-grid\{grid-template-columns:1fr 1fr\}\}/);
  assert.match(css, /@media\(max-width:760px\) \{\.performance-advanced-grid\{grid-template-columns:1fr\}\}/);
});
