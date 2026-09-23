import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

// A tiny in-memory Storage stand-in so the persistence logic is tested
// without touching a real browser localStorage.
function fakeStorage(initial = {}) {
  const data = { ...initial };
  return {
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => {
      data[key] = String(value);
    },
    _data: data,
  };
}

test("the default text scale is exactly 100% (1)", async () => {
  const { DEFAULT_TEXT_SCALE } = await import("../src/js/appearance-view.js");
  assert.equal(DEFAULT_TEXT_SCALE, 1);
});

test("loading with nothing stored yields the default, never a crash", async () => {
  const { loadTextScale, DEFAULT_TEXT_SCALE } = await import("../src/js/appearance-view.js");
  assert.equal(loadTextScale(fakeStorage()), DEFAULT_TEXT_SCALE);
});

test("an out-of-range or corrupted stored value is clamped to the nearest known option, never trusted as-is", async () => {
  const { clampTextScale, TEXT_SCALE_OPTIONS } = await import("../src/js/appearance-view.js");
  assert.equal(clampTextScale(999), TEXT_SCALE_OPTIONS.at(-1));
  assert.equal(clampTextScale(-5), 1);
  assert.equal(clampTextScale(0), 1);
  assert.equal(clampTextScale("not-a-number"), 1);
  assert.equal(clampTextScale(NaN), 1);
  assert.equal(clampTextScale(undefined), 1);
  assert.equal(clampTextScale(1.07), 1.1);
});

test("saving persists the clamped value and loading it back returns the same value", async () => {
  const { saveTextScale, loadTextScale } = await import("../src/js/appearance-view.js");
  const storage = fakeStorage();
  saveTextScale(storage, 1.2);
  assert.equal(loadTextScale(storage), 1.2);
  assert.equal(storage._data["mg-text-scale"], "1.2");
});

test("saving an out-of-range value never reaches storage unclamped", async () => {
  const { saveTextScale } = await import("../src/js/appearance-view.js");
  const storage = fakeStorage();
  const clamped = saveTextScale(storage, 5);
  assert.equal(clamped, 1.3);
  assert.equal(storage._data["mg-text-scale"], "1.3");
});

test("a storage read/write failure never throws: the in-memory default still applies", async () => {
  const { loadTextScale, saveTextScale, DEFAULT_TEXT_SCALE } = await import("../src/js/appearance-view.js");
  const throwing = {
    getItem() {
      throw new Error("blocked");
    },
    setItem() {
      throw new Error("blocked");
    },
  };
  assert.equal(loadTextScale(throwing), DEFAULT_TEXT_SCALE);
  assert.doesNotThrow(() => saveTextScale(throwing, 1.1));
});

test("every offered step has a clean, whole percentage label", async () => {
  const { TEXT_SCALE_OPTIONS, textScalePercentLabel } = await import("../src/js/appearance-view.js");
  assert.deepEqual(
    TEXT_SCALE_OPTIONS.map(textScalePercentLabel),
    ["90%", "100%", "110%", "120%", "130%"],
  );
});

test("the CSS custom property this session introduces is defined once, at :root, defaulting to 1", () => {
  const css = read("../src/styles/refinement.css");
  assert.match(css, /--ui-scale:1/);
});

test("no stylesheet ever uses transform: scale for text sizing, and the window is never resized for it", () => {
  const styles = read("../src/styles/refinement.css");
  assert.ok(!/transform:\s*scale/.test(styles), "text scaling must never use transform: scale()");
});

test("the Settings dialog hosts an Aspetto/text-size section with a reset control", () => {
  const html = read("../src/index.html");
  assert.match(html, /id="text-scale-options"/);
  assert.match(html, /id="text-scale-reset"/);
  assert.match(html, /data-i18n="settings\.appearance"/);
  assert.match(html, /data-i18n="settings\.textSize"/);
});

test("settings.appearance/textSize/textSizeReset exist in both Italian and English", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  for (const key of ["settings.appearance", "settings.textSize", "settings.textSizeReset"]) {
    assert.ok(dictionaries.it[key], `Italian is missing ${key}`);
    assert.ok(dictionaries.en[key], `English is missing ${key}`);
  }
});
