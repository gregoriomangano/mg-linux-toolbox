import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync, lstatSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

// The first-run / welcome popup ("Ciao, sono Gregorio") already existed as
// the local support nudge: a small dialog with the project mark, a thank-you
// note and the YouTube channel invite, whose visibility is decided in Rust
// (support.rs) by a stored stage/nextShowAt pair. These tests lock in that
// mechanism plus this session's manual "show it again" button in Settings.

test("the welcome dialog still exists with project branding, texts, YouTube button and dismiss controls", () => {
  const html = read("../src/index.html");
  assert.match(html, /id="support-nudge-dialog"/);
  assert.match(html, /class="nudge-avatar" data-brand-logo src="assets\/branding\/logochiaro\.png" alt="M\.G Linux Toolbox"/);
  assert.match(html, /data-i18n="nudge\.title"/);
  for (const key of ["nudge.text1", "nudge.text2", "nudge.text3", "nudge.text4", "nudge.text5"]) {
    assert.match(html, new RegExp(`data-i18n="${key.replace(".", "\\.")}"`), `${key} must be rendered`);
  }
  assert.match(html, /id="support-nudge-channel"/);
  assert.match(html, /id="support-nudge-remind"/);
  assert.match(html, /id="support-nudge-later"/);
  assert.match(html, /id="support-nudge-never"/);
});

test("the project mark used by the popup is a local asset, present on disk", () => {
  const author = read("../src/js/author.js");
  assert.match(author, /avatar:\s*"assets\/branding\/logochiaro\.png"/);
  assert.ok(existsSync(new URL("../src/assets/branding/logochiaro.png", import.meta.url)), "project mark must exist as a real file");
  // A real file, not a symlink escaping the frontend root: that is exactly
  // what broke the logo in the dev build once.
  assert.ok(!lstatSync(new URL("../src/assets/branding/logochiaro.png", import.meta.url)).isSymbolicLink(), "project mark must not be a symlink");
});

test("the YouTube link comes from the centralized author data and is a real channel URL", async () => {
  const { AUTHOR } = await import("../src/js/author.js");
  assert.match(AUTHOR.youtube, /^https:\/\/www\.youtube\.com\/@[A-Za-z0-9._-]+$/);
  const main = read("../src/main.js");
  assert.match(main, /link\.href=AUTHOR\.youtube/, "the dialog link must be filled from AUTHOR.youtube, never hardcoded");
});

test("first run shows it automatically, later runs do not: the decision comes from Rust, never from localStorage", () => {
  const main = read("../src/main.js");
  assert.match(main, /invoke\("get_support_nudge"\)/);
  assert.match(main, /if\(!state_?\?\.show\)return;/);
  assert.match(main, /requestAnimationFrame\(\(\)=>setTimeout\(maybeShowSupportNudge,600\)\)/);
  // The "already seen" state lives in the Rust-owned JSON file, not in the
  // webview: no localStorage key may decide the popup's visibility.
  const nudgeBlock = main.slice(main.indexOf("let nudgeShown=false"), main.indexOf("const PAGES ="));
  assert.ok(!/localStorage/.test(nudgeBlock), "the welcome popup must never use localStorage for its state");
});

test("dismissing persists exactly one closed mode and the stored state is what hides it later", () => {
  const main = read("../src/main.js");
  assert.match(main, /invoke\("dismiss_support_nudge",\{mode\}\)/);
  assert.match(main, /const mode=nudgeMode\|\|\(\(!row\.hidden&&\$\("support-nudge-remind"\)\.checked\)\?"remind":"close"\)/);
});

test("Settings hosts a discrete Benvenuto section with a button that reopens the same dialog", () => {
  const html = read("../src/index.html");
  assert.match(html, /data-i18n="settings\.welcome"/);
  assert.match(html, /id="welcome-show-button"/);
  assert.match(html, /data-i18n="settings\.showWelcome"/);
  const main = read("../src/main.js");
  assert.match(main, /\$\("welcome-show-button"\)\.addEventListener\("click",openSupportNudgeManually\)/);
  assert.match(main, /function openSupportNudgeManually\(\)/);
  assert.match(main, /dialog\.showModal\(\)/, "the manual path must open the very same dialog");
});

test("a manual review is view-only: it hides the state-changing controls and never persists on close", () => {
  const main = read("../src/main.js");
  const manual = main.slice(main.indexOf("function openSupportNudgeManually"), main.indexOf("function refreshSupportNudgeLanguage"));
  assert.match(manual, /nudgeManual=true/);
  assert.match(manual, /support-nudge-remind-row"\)\.hidden=true/);
  assert.match(manual, /support-nudge-never"\)\.hidden=true/);
  // The close handler must return before ever invoking the dismiss command.
  const closeHandler = main.slice(main.indexOf('$("support-nudge-dialog").addEventListener("close"'), main.indexOf('$("welcome-show-button")'));
  const manualBranch = closeHandler.indexOf("if(nudgeManual)");
  const invoke = closeHandler.indexOf('invoke("dismiss_support_nudge"');
  assert.ok(manualBranch >= 0, "the manual branch must exist in the close handler");
  assert.ok(manualBranch < invoke, "the manual branch must return before the dismiss command runs");
  assert.match(closeHandler, /if\(nudgeManual\)\{[\s\S]*?return;/, "the manual branch must return early");
  // ...and it must restore the controls the automatic path needs.
  assert.match(closeHandler, /support-nudge-never"\)\.hidden=false/);
  assert.match(closeHandler, /configureSupportNudge\(nudgeStage\)/);
});

test("the welcome texts and the new Settings keys exist in both Italian and English", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  const keys = ["nudge.title", "nudge.text1", "nudge.text2", "nudge.text3", "nudge.text4", "nudge.text5", "nudge.goToChannel", "nudge.never", "nudge.remind10", "nudge.remind30", "settings.welcome", "settings.showWelcome"];
  for (const key of keys) {
    assert.ok(dictionaries.it[key], `Italian is missing ${key}`);
    assert.ok(dictionaries.en[key], `English is missing ${key}`);
  }
  // The welcome content still thanks the user and invites them to the channel.
  assert.match(dictionaries.it["nudge.text5"], /Grazie/i);
  assert.match(dictionaries.it["nudge.text2"], /canale YouTube/i);
  assert.match(dictionaries.en["nudge.text2"], /YouTube/i);
});
