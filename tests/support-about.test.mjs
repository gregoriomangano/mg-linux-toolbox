import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dictionaries } from "../src/js/i18n.js";
import { AUTHOR } from "../src/js/author.js";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

// Strips line comments so "must not contain" checks look at real code, not
// at documentation that explains what the code deliberately avoids.
const codeOnly = (source) =>
  source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trimStart().startsWith("//"))
    .join("\n");

test("all author details live in one module and nowhere else", () => {
  const author = read("../src/js/author.js");
  const frontend = read("../src/main.js");
  const markup = read("../src/index.html");
  for (const value of [
    AUTHOR.youtube,
    AUTHOR.website,
    AUTHOR.github,
    AUTHOR.support,
  ]) {
    assert.ok(author.includes(value), `author.js must hold ${value}`);
    assert.ok(!frontend.includes(value), `main.js must not duplicate ${value}`);
    assert.ok(!markup.includes(value), `index.html must not duplicate ${value}`);
  }
});

test("the public page carries no email, phone or private contact cards", () => {
  for (const path of [
    "../src/js/author.js",
    "../src/main.js",
    "../src/index.html",
    "../src/js/i18n.js",
  ]) {
    const source = read(path);
    for (const gone of [
      "info@manganogregorio.it",
      "328 775 0633",
      "wa.me",
      "ultracert.it",
      "mg-avviatore",
      "about.cardEmail",
      "about.cardPhone",
      "about.cardPec",
      "about.writeMe",
      "about.contactMe",
    ]) {
      assert.ok(!source.includes(gone), `${path} must not mention ${gone}`);
    }
  }
  assert.equal(AUTHOR.email, undefined);
  assert.equal(AUTHOR.phone, undefined);
  assert.equal(AUTHOR.pec, undefined);
  assert.equal(AUTHOR.toolbox, undefined);
  assert.equal(AUTHOR.avviatore, undefined);
});

test("the nudge and the about page share the same centralized author data", () => {
  const frontend = read("../src/main.js");
  assert.match(frontend, /import \{ AUTHOR, externalLink \} from "\.\/js\/author\.js"/);
  // The popup channel link and the about page both read AUTHOR.
  assert.match(frontend, /link\.href=AUTHOR\.youtube/);
  assert.match(frontend, /externalLink\(AUTHOR\.youtube/);
  assert.match(frontend, /externalLink\(AUTHOR\.support/);
});

test("external addresses go through the single openExternalUrl function and the opener plugin", () => {
  const author = read("../src/js/author.js");
  const frontend = read("../src/main.js");
  // One centralized function, explicitly using the Tauri 2 opener plugin.
  assert.match(author, /export async function openExternalUrl\(url\)/);
  assert.match(author, /invoke\("plugin:opener\|open_url"/);
  // It validates scheme and exact allow-listed URL before opening anything.
  assert.match(author, /parsed\.protocol !== "https:"/);
  assert.match(author, /EXTERNAL_ALLOWLIST\.has\(parsed\.href\)/);
  // Clicks are always intercepted; no target=_blank as the mechanism.
  assert.match(author, /event\.preventDefault\(\)/);
  assert.doesNotMatch(codeOnly(author), /target\s*=\s*"_blank"/);
  for (const source of [author, frontend]) {
    assert.doesNotMatch(codeOnly(source), /window\.open\(|location\.href\s*=|Command::new/i);
  }
  // Every advertised address is https.
  for (const url of [AUTHOR.youtube, AUTHOR.website, AUTHOR.github, AUTHOR.support]) {
    assert.ok(url.startsWith("https://"), `${url} must be https`);
  }
  // The capability really grants the opener command to the main window.
  const capability = read("../src-tauri/capabilities/default.json");
  assert.match(capability, /opener:allow-open-url/);
  assert.match(capability, /opener:allow-default-urls/);
});

test("the reminder cadence lives in Rust and uses timestamps, not JS timers", () => {
  const support = read("../src-tauri/src/support.rs");
  const frontend = read("../src/main.js");
  assert.match(support, /10 \* 24 \* 60 \* 60/);
  assert.match(support, /30 \* 24 \* 60 \* 60/);
  assert.match(support, /now >= nudge\.next_show_at/);
  assert.match(support, /pub fn decide/);
  // The nudge code itself must not schedule anything long-lived: its only
  // delays are the modal-wait and the after-first-paint delay.
  const nudgeSection = frontend.slice(
    frontend.indexOf("let nudgeShown"),
    frontend.indexOf("const PAGES"),
  );
  assert.ok(nudgeSection.length > 0, "the nudge section must exist");
  for (const match of nudgeSection.matchAll(/setTimeout\([^,]+,\s*(\d+)\)/g)) {
    assert.ok(Number(match[1]) < 60_000, `unexpected nudge timer: ${match[1]}ms`);
  }
  assert.doesNotMatch(nudgeSection, /10 \* 24|30 \* 24|864000000|2592000000/);
});

test("the dismissal modes the frontend can send are exactly the allow-listed ones", () => {
  const support = read("../src-tauri/src/support.rs");
  const frontend = read("../src/main.js");
  for (const mode of ["remind", "close", "never"]) {
    assert.match(support, new RegExp(`"${mode}"`));
    assert.match(frontend, new RegExp(`"${mode}"`));
  }
  assert.match(support, /invalid_mode/);
  assert.match(frontend, /invoke\("dismiss_support_nudge",\{mode\}\)/);
  assert.match(frontend, /invoke\("get_support_nudge"\)/);
});

test("the popup never stacks on top of another open modal and can be closed with ESC", () => {
  const frontend = read("../src/main.js");
  const markup = read("../src/index.html");
  assert.match(frontend, /document\.querySelector\("dialog\[open\]"\)/);
  assert.match(frontend, /dialog\.showModal\(\)/);
  assert.match(markup, /<dialog id="support-nudge-dialog"/);
  // Native <dialog> gives ESC for free; the close event persists the choice.
  assert.match(frontend, /support-nudge-dialog"\)\.addEventListener\("close"/);
});

test("the about page is a real sidebar destination with the support section inside it", () => {
  const markup = read("../src/index.html");
  const frontend = read("../src/main.js");
  assert.match(markup, /id="about-nav"/);
  assert.match(markup, /id="about-page"/);
  assert.match(markup, /id="about-where-title"/);
  assert.match(markup, /id="about-support-title"/);
  assert.match(markup, /id="about-links"/);
  assert.match(frontend, /about: \{content:"about-page", nav:"about-nav", breadcrumb:"breadcrumb\.about", onEnter:renderAbout\}/);
  // No separate "Sostieni" nav entry.
  assert.doesNotMatch(markup, /id="support-nav"/);
});

test("support uses the official project page without direct payment details", () => {
  const markup = read("../src/index.html");
  const author = read("../src/js/author.js");
  assert.doesNotMatch(markup, /IBAN|SWIFT|Bonifico|PayPal/i);
  assert.doesNotMatch(author, /iban|swift|bank|paypal/i);
  const frontend = read("../src/main.js");
  assert.match(frontend, /externalLink\(AUTHOR\.support/);
});

test("Italian and English expose every about/nudge key the surfaces use", () => {
  const frontend = read("../src/main.js") + read("../src/index.html");
  const keys = new Set();
  for (const match of frontend.matchAll(/(?:about|nudge)\.[A-Za-z0-9_]+/g)) keys.add(match[0]);
  assert.ok(keys.size > 20, "the about/nudge surfaces must use the i18n system");
  for (const key of keys) {
    assert.ok(dictionaries.it[key] !== undefined, `${key} missing in Italian`);
    assert.ok(dictionaries.en[key] !== undefined, `${key} missing in English`);
  }
  // Spot-check a few real sentences, in both languages.
  assert.equal(dictionaries.it["nudge.title"], "Ciao, sono Gregorio 👋");
  assert.equal(dictionaries.en["nudge.title"], "Hi, I'm Gregorio 👋");
  assert.equal(dictionaries.it["nav.about"], "Chi sono");
  assert.equal(dictionaries.en["nav.about"], "About me");
  assert.ok(dictionaries.it["about.bio7"].includes("open source"));
  assert.ok(dictionaries.en["about.bio7"].includes("open source"));
});

test("the Rust commands are registered and persist in the app config directory", () => {
  const lib = read("../src-tauri/src/lib.rs");
  const support = read("../src-tauri/src/support.rs");
  assert.match(lib, /support::get_support_nudge/);
  assert.match(lib, /support::dismiss_support_nudge/);
  assert.match(support, /app_config_dir/);
  assert.match(support, /support-nudge\.json/);
  // No helper, no polkit, no telemetry: this module is plain file state.
  assert.doesNotMatch(support, /pkexec|polkit|Command::new|reqwest|http/);
});
