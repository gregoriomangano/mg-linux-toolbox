import test from "node:test";
import assert from "node:assert/strict";
import { createI18n, dictionaries, translate } from "../src/js/i18n.js";

test("Italian and English dictionaries expose identical keys", () => {
  assert.deepEqual(Object.keys(dictionaries.it).sort(), Object.keys(dictionaries.en).sort());
  assert.ok(Object.keys(dictionaries.it).length > 70);
});

test("translations preserve technical interpolation values", () => {
  assert.equal(translate("it", "network.connected"), "Connessa");
  assert.equal(translate("en", "network.connected"), "Connected");
  assert.equal(translate("en", "quota.remaining", { value: "75" }), "75% remaining");
  assert.equal(translate("it", "feed.source", { source: "Arch Linux" }), "Fonte: Arch Linux");
  assert.equal(translate("en", "missing.key"), "missing.key");
});

test("language preference changes live and persists", () => {
  const values = new Map([["mg-language", "en"]]);
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  const i18n = createI18n({ storage, initial: "it" });
  let observed = null;
  i18n.subscribe((language) => { observed = language; });
  assert.equal(i18n.getLanguage(), "en");
  i18n.setLanguage("it");
  assert.equal(values.get("mg-language"), "it");
  assert.equal(observed, "it");
  assert.equal(i18n.t("nav.overview"), "Panoramica");
});
