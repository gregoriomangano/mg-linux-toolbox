import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
// Strips /* ... */ comments before any test matches against real
// declarations, so a comment that happens to mention a token/selector in
// prose (e.g. explaining the --font-ui stack) can never be mistaken for
// the actual CSS rule.
const readCss = (path) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");

// Regression coverage for this session's typography/contrast rewrite: the
// handwritten face must be an ACCENT, never the app's default reading
// font, and the only stylesheet Tauri actually loads must be the single
// source of truth for every token this file checks.

test("index.html loads exactly one stylesheet, and it is refinement.css", () => {
  const html = read("../src/index.html");
  const links = [...html.matchAll(/<link[^>]*rel="stylesheet"[^>]*>/g)];
  assert.equal(links.length, 1, "exactly one <link rel=stylesheet> is expected");
  assert.match(links[0][0], /href="styles\/refinement\.css"/);
  assert.ok(!existsSync(new URL("../src/styles/styles.css", import.meta.url)), "the old, never-loaded styles.css must not linger and drift out of sync again");
});

test("--font-ui is a real local/system sans-serif stack, with no remote font and no Google Fonts dependency", () => {
  const css = readCss("../src/styles/refinement.css");
  const match = css.match(/--font-ui:([^;]+);/);
  assert.ok(match, "--font-ui must be defined in :root");
  const stack = match[1];
  assert.match(stack, /system-ui/);
  assert.ok(!/googleapis|fonts\.google|@import/i.test(css), "no remote font source is ever allowed");
});

test("--data resolves to the UI sans-serif font, not the handwritten one (regression: --data used to be Kalam by mistake)", () => {
  const css = readCss("../src/styles/refinement.css");
  const dataMatch = css.match(/--data:([^;]+);/);
  assert.ok(dataMatch, "--data must be defined");
  assert.match(dataMatch[1].trim(), /^var\(--font-ui\)$/);
});

test("the handwritten face is reserved for the page title and a few identity accents, never the app's reading font", () => {
  const css = readCss("../src/styles/refinement.css");
  // body (the default inherited font for anything that does not specify
  // its own font-family) must never be the handwritten face again.
  const bodyRule = css.match(/\bbody\s*\{[^}]*\}/s)[0];
  assert.ok(!/var\(--hand\)/.test(bodyRule), "body must not default to the handwritten font");
  assert.match(bodyRule, /var\(--font-ui\)/);
  // Only this small, deliberate set of selectors may use the handwritten
  // face (via --hand or --font-display): logo, page titles, the hero
  // identity block, the sidebar signature and the big section titles.
  const handSelectors = [...css.matchAll(/([^{}]+)\{[^}]*var\(--(?:hand|font-display)\)[^}]*\}/g)]
    .map((m) => m[1].trim())
    .filter((selector) => selector !== ":root"); // :root only defines the token alias
  const allowed = new Set([
    "h1",
    ".performance-section-title h2",
    ".top-brand strong",
    ".welcome",
    ".tagline",
    ".linux-sign p",
  ]);
  for (const selector of handSelectors) {
    assert.ok(allowed.has(selector), `unexpected handwritten-font selector: "${selector}" -- either add it to the identity-accent allowlist deliberately, or convert it to the UI font`);
  }
});

test("card and panel titles are UI font, never handwritten (a desktop tool, not a school notebook)", () => {
  const css = readCss("../src/styles/refinement.css");
  const defaultH2 = css.match(/^h2\s*\{([^}]*)\}/m)[1];
  assert.match(defaultH2, /var\(--font-ui\)/);
  for (const selector of [".panel-title h2", ".metric-title h2", ".performance-card .panel-title h3", ".performance-adv .panel-title h3", ".performance-profile h3", ".programs-card .panel-title h3"]) {
    const rule = css.match(new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*\\{([^}]*)\\}"));
    if (rule) assert.ok(!/var\(--(?:hand|font-display)\)/.test(rule[1]), `${selector} must not use the handwritten font`);
  }
});

test("the type scale is centralized in a few tokens, and 100% is compact (13-15px body range)", () => {
  const css = readCss("../src/styles/refinement.css");
  const tokens = {
    "--text-xs": [10, 12],
    "--text-sm": [11, 13],
    "--text-md": [13, 14],
    "--text-lg": [14, 15.5],
    "--text-xl": [15, 17],
    "--title-section": [17, 19],
    "--title-panel": [18, 22],
    "--title-value": [20, 26],
  };
  for (const [token, [min, max]] of Object.entries(tokens)) {
    const match = css.match(new RegExp(`${token}:calc\\(([\\d.]+)px \\* var\\(--ui-scale\\)\\)`));
    assert.ok(match, `${token} must be defined as calc(<px> * var(--ui-scale))`);
    const px = Number(match[1]);
    assert.ok(px >= min && px <= max, `${token} is ${px}px, expected ${min}-${max}px for a compact 100% baseline`);
  }
  // Page titles stay prominent but are no longer enormous.
  assert.match(css, /--title-page:calc\(clamp\(24px,2\.2vw,30px\) \* var\(--ui-scale\)\)/);
  // Scattered raw values must be gone: everything typographic goes through
  // the tokens (the only remaining calc(px*scale) are the token definitions).
  const raw = [...css.matchAll(/calc\([\d.]+px \* var\(--ui-scale\)\)/g)];
  assert.equal(raw.length, Object.keys(tokens).length, `expected only the ${Object.keys(tokens).length} token definitions to use a raw px calc, found ${raw.length}`);
});

test("dense surfaces stay compact: no leftover giant min-heights on the main cards", () => {
  const css = readCss("../src/styles/refinement.css");
  for (const [selector, limit] of [["\\.performance-adv", 0], ["\\.software-tool-card", 0], ["\\.performance-profile", 160], ["\\.metric-card", 160]]) {
    const rule = css.match(new RegExp(selector + "\\s*\\{([^}]*)\\}"));
    assert.ok(rule, `${selector} rule not found`);
    const min = rule[1].match(/min-height:(\d+)px/);
    if (min) assert.ok(Number(min[1]) <= limit, `${selector} min-height ${min[1]}px is too tall (limit ${limit}px)`);
  }
});

test("buttons never use the handwritten font (section 14: evita micro-pulsanti con font manoscritto)", () => {
  const css = readCss("../src/styles/refinement.css");
  const buttonRules = [...css.matchAll(/([^{}]*button[^{}]*)\{([^}]*)\}/g)];
  for (const [, selector, body] of buttonRules) {
    assert.ok(!/var\(--hand\)/.test(body), `button selector "${selector.trim()}" must not use the handwritten font`);
  }
});

test("readable status/accent colors have a higher-contrast '-strong' variant, and it is actually used for the green 'Attivo' states", () => {
  const css = readCss("../src/styles/refinement.css");
  for (const token of ["--green-strong", "--orange-strong", "--purple-strong", "--red-strong"]) {
    assert.match(css, new RegExp(`${token}:`), `${token} must be defined in :root`);
  }
  assert.match(css, /\.repo-status\.on\s*\{color:var\(--green-strong\)\}/);
  assert.match(css, /\.autostart-row-state span\.on\s*\{color:var\(--green-strong\)\}/);
});

test("the five fixed app cards stay compact", () => {
  const html = read("../src/index.html");
  const css = readCss("../src/styles/refinement.css");
  assert.equal((html.match(/class="[^"]*app-card" data-app=/g)||[]).length,5);
  assert.match(css,/\.programs-card \{min-height:0/);
});

test("the Programs grid targets three columns on desktop width and degrades responsively", () => {
  const css = readCss("../src/styles/refinement.css");
  assert.match(css, /\.programs-grid\s*\{[^}]*repeat\(3,/);
  assert.match(css, /@media\(max-width:1050px\)[^}]*\.programs-grid\{grid-template-columns:repeat\(2,/);
  assert.match(css, /@media\(max-width:620px\)[^}]*\.programs-grid\{grid-template-columns:1fr\}/);
});
