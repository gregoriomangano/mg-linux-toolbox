import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

const FORM_URL =
  "https://docs.google.com/forms/d/e/1FAIpQLSfknkVci9taZrfQi4e6nqQhKGRXZ-Hv0VnAdIKoNtKEzN1A1Q/viewform?embedded=true";

test("the sidebar hosts the new 1-on-1 support entry, before the final destinations", () => {
  const html = read("../src/index.html");
  assert.match(html, /id="assistance-nav"/);
  assert.match(html, /data-i18n="nav\.assistance"/);
  assert.match(html, /pen-icons\.svg#help/);
  // The sidebar label is the short one; the full name only lives in the page.
  assert.match(html, /<span class="assistance-label" data-i18n="nav\.assistance">Assistenza<\/span>/);
  assert.match(html, /data-i18n="assistance\.title">Assistenza 1 a 1</);
  // Order inside the bottom group: support, then Settings, then About.
  const order = ['id="assistance-nav"', 'id="settings-button"', 'id="about-nav"'].map((needle) =>
    html.indexOf(needle),
  );
  assert.ok(order.every((index) => index >= 0), "all three entries must exist");
  assert.deepEqual(order, [...order].sort((a, b) => a - b), "order must be support → settings → about");
  // The other destinations are untouched.
  for (const id of ["overview-nav", "performance-nav", "software-nav", "programs-nav"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
});

test("the new page follows the existing page shell and is hidden until selected", () => {
  const html = read("../src/index.html");
  assert.match(html, /<main class="performance" id="assistance-page" hidden>/);
  assert.match(html, /data-i18n="assistance\.title"/);
  assert.match(html, /data-i18n="assistance\.intro"/);
  assert.match(html, /data-i18n="assistance\.intro2"/);
  assert.match(html, /id="assistance-form"/);
  // No hand-built form fields: everything lives in the Google Form.
  const page = html.slice(html.indexOf('id="assistance-page"'), html.indexOf('id="about-page"'));
  assert.ok(!/<input|<textarea|<select/.test(page), "the page must not duplicate Google Form fields");
  // The redundant "Modulo di richiesta" section is gone: header, then form.
  assert.ok(!/Modulo di richiesta/.test(page), "the redundant section must be removed");
  assert.ok(!/assistance\.formTitle|assistance\.formHint/.test(page), "its keys must be gone too");
});

test("the intro never renders a literal <br>: two plain text lines instead", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  const html = read("../src/index.html");
  const page = html.slice(html.indexOf('id="assistance-page"'), html.indexOf('id="about-page"'));
  assert.ok(!page.includes("<br>"), "no <br> may appear in the assistance page markup");
  for (const language of ["it", "en"]) {
    for (const key of ["assistance.intro", "assistance.intro2"]) {
      const value = dictionaries[language][key];
      assert.ok(value, `${language} is missing ${key}`);
      assert.ok(!value.includes("<br>"), `${key} must not contain HTML`);
      assert.ok(!/[<>]/.test(value.replace(/&[a-z]+;/g, "")), `${key} must be plain text`);
    }
  }
  assert.match(dictionaries.it["assistance.intro"], /^Hai bisogno di aiuto con Linux, Windows o il tuo computer\?$/);
  assert.match(dictionaries.it["assistance.intro2"], /Gregorio/);
});

test("the sidebar label is a single line without shrinking the other entries", () => {
  const css = read("../src/styles/refinement.css");
  // nowrap is scoped to this one label only...
  assert.match(css, /#assistance-nav \.assistance-label \{white-space:nowrap\}/);
  // ...and no global rule was added for the other nav items.
  const navRules = [...css.matchAll(/\.nav-item[^{]*\{([^}]*)\}/g)].map((m) => m[1]);
  for (const rule of navRules) {
    assert.ok(!rule.includes("nowrap"), "the other nav items must not be restyled");
  }
});

test("the form is a long borderless embed and the page owns the only intended scroll", () => {
  const css = read("../src/styles/refinement.css");
  const frame = css.match(/\.assistance-frame \{([^}]*)\}/)[1];
  assert.match(frame, /width:100%/);
  assert.match(frame, /height:1500px/);
  assert.match(frame, /min-height:1500px/);
  assert.match(frame, /border:0/);
  assert.doesNotMatch(frame,/height:100%|100vh|70vh|calc\(/);
  assert.doesNotMatch(css,/\.assistance-frame-wrap/);
  assert.doesNotMatch(frame,/overflow:hidden/);
  assert.ok(!/body[^{]*\{[^}]*overflow:hidden/.test(css), "the shell scrolling must stay untouched");
});

test("the router registers the page exactly like every other destination", () => {
  const main = read("../src/main.js");
  assert.match(
    main,
    /assistance: \{content:"assistance-page", nav:"assistance-nav", breadcrumb:"breadcrumb\.assistance", onEnter:enterAssistance\}/,
  );
  assert.match(main, /\$\("assistance-nav"\)\.addEventListener\("click",\(\)=>setPage\("assistance"\)\)/);
  assert.match(main, /function enterAssistance\(\)/);
});

test("the embedded form uses the exact public URL, and nothing is fetched until the page is opened", () => {
  const html = read("../src/index.html");
  const frame = html.slice(html.indexOf('id="assistance-form"'), html.indexOf("</iframe>"));
  assert.ok(frame.includes(`data-src="${FORM_URL}"`), "the iframe must point at the exact form URL");
  assert.ok(!/\ssrc="/.test(frame), "the iframe must have no src until the page is opened");
  assert.match(frame, /referrerpolicy="no-referrer"/);
  assert.match(frame, /title="Modulo di assistenza 1 a 1"|data-i18n-title="assistance\.frameTitle"/);
  // The URL appears once, only there.
  const occurrences = read("../src/index.html").split(FORM_URL).length - 1;
  assert.equal(occurrences, 1, "the form URL must be declared in exactly one place");
  const main = read("../src/main.js");
  assert.match(main, /if\(!frame\.src&&frame\.dataset\.src\)frame\.src=frame\.dataset\.src/);
  assert.match(main, /frame\.dataset\.loaded==="true"/, "the form must only be loaded once");
});

test("the form is filled inside the Toolbox: nothing opens a browser or a mail client", () => {
  const main = read("../src/main.js");
  const block = main.slice(main.indexOf("function enterAssistance"), main.indexOf("function enterOverview"));
  for (const forbidden of ["open_external_url", "window.open", "externalLink", "mailto:", "opener"]) {
    assert.ok(!block.includes(forbidden), `enterAssistance must not use ${forbidden}`);
  }
  const html = read("../src/index.html");
  const page = html.slice(html.indexOf('id="assistance-page"'), html.indexOf('id="about-page"'));
  assert.ok(!/target="_blank"/.test(page), "no link out of the app belongs on this page");
});

test("the CSP allows exactly the Google Forms host for frames, with no wildcard", () => {
  const config = JSON.parse(read("../src-tauri/tauri.conf.json"));
  const csp = config.app.security.csp;
  assert.equal(csp["frame-src"], "https://docs.google.com");
  for (const [directive, value] of Object.entries(csp)) {
    assert.ok(!value.includes("*"), `${directive} must not contain a wildcard (got "${value}")`);
  }
  // The frame permission must not have leaked into a broader directive.
  assert.equal(csp["default-src"], "'self' customprotocol: asset:");
  assert.equal(csp["object-src"], "'none'");
  assert.equal(csp["base-uri"], "'none'");
});

test("no secret of any kind is needed for a public form", () => {
  const html = read("../src/index.html");
  const main = read("../src/main.js");
  // Only the assistance surface is inspected: elsewhere the app legitimately
  // talks about passwords (the KeePassXC entry) and similar words.
  const page = html.slice(html.indexOf('id="assistance-page"'), html.indexOf('id="about-page"'));
  const logic = main.slice(main.indexOf("function enterAssistance"), main.indexOf("function enterOverview"));
  const surfaces = [page, logic, read("../src-tauri/tauri.conf.json")].join("\n");
  for (const needle of ["apiKey", "api_key", "token", "password", "client_secret", "Bearer ", "Authorization"]) {
    assert.ok(!surfaces.includes(needle), `no "${needle}" may appear for a public form`);
  }
  // The only place the form is addressed is the one public URL.
  assert.match(page, /data-src="https:\/\/docs\.google\.com\/forms\//);
});

test("the new texts exist in both Italian and English", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  const keys = [
    "nav.assistance",
    "breadcrumb.assistance",
    "assistance.title",
    "assistance.intro",
    "assistance.intro2",
    "assistance.loading",
    "assistance.frameTitle",
  ];
  for (const key of keys) {
    assert.ok(dictionaries.it[key], `Italian is missing ${key}`);
    assert.ok(dictionaries.en[key], `English is missing ${key}`);
  }
  assert.match(dictionaries.it["assistance.intro2"], /Gregorio/);
  assert.match(dictionaries.en["assistance.intro2"], /Gregorio/);
});

test("the help glyph was added to the project's own icon set, in the same pen style", () => {
  const svg = read("../src/assets/pen-icons.svg");
  const symbol = svg.slice(svg.indexOf('<symbol id="help"'), svg.indexOf("</symbol>", svg.indexOf('<symbol id="help"')));
  assert.ok(symbol.length > 0, "the help symbol must exist");
  assert.match(symbol, /viewBox="0 0 34 34"/);
  assert.match(symbol, /stroke="currentColor"/);
  assert.match(symbol, /stroke-width="1\.8"/);
});
