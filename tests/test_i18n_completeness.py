"""
Automated i18n completeness checks (2026-08-04 block, Phase 8).

Imports every page module so each one's page-local _strings dict (the
`_xxx_ds_strings` / `_page_strings` pattern used throughout ui/pages/)
registers into core.i18n._strings exactly like the running app does,
then verifies:

- every key has a non-empty it/en/fr value (the three languages this
  block is required to complete — es is a bonus, not required);
- T() never silently falls back to the bare key for it/en/fr;
- format-placeholders (the "{n}"-style spots used by
  sr_updates_available / sr_orphans_preview) match across languages,
  so a translator can't accidentally drop or rename one.
"""
import re
import unittest

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw
Adw.init()

import core.i18n as i18n
from core.i18n import T, set_lang

# Import every page (and the window shell, which registers a couple of
# its own keys too) so all module-level string registrations run —
# mirrors what ui/window.py does when the app actually starts. Doing
# this via ui.window itself pulls in the full PAGES list in one go and
# can't silently drift out of sync with it the way a hand-maintained
# import list here could.
import ui.sidebar          # noqa: F401
import ui.window           # noqa: F401
import ui.pages.page_disk_activity  # noqa: F401 (reached only via in-page links, not PAGES-visible)

_REQUIRED_LANGS = ("it", "en", "fr")
_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")


class RequiredLanguagesPresentTests(unittest.TestCase):
    def test_every_key_has_it_en_fr(self):
        missing = []
        for key, entry in i18n._strings.items():
            for lang in _REQUIRED_LANGS:
                if not entry.get(lang):
                    missing.append(f"{key}[{lang}]")
        self.assertEqual(missing, [], f"{len(missing)} missing translations: {missing[:30]}")

    def test_T_never_falls_back_to_bare_key_for_required_langs(self):
        bare_key_leaks = []
        for lang in _REQUIRED_LANGS:
            set_lang(lang)
            for key in i18n._strings:
                if T(key) == key:
                    bare_key_leaks.append(f"{key}[{lang}]")
        set_lang("it")
        self.assertEqual(bare_key_leaks, [], f"T() returned the bare key for: {bare_key_leaks[:30]}")


class SoftwareReposPageStringsTests(unittest.TestCase):
    """The strings this specific 2026-08-04 block introduced."""

    def test_new_page_keys_present_in_all_required_langs(self):
        new_keys = [k for k in i18n._strings if k.startswith("sr_") or k.startswith("recipe_")
                    or k.startswith("health_") or k.startswith("flatpak_") or k.startswith("flathub_")
                    or k.startswith("flatseal_") or k.startswith("fw_state_") or k.startswith("rootssh_state_")
                    or k == "tab_software_repos" or k == "engine_operation_unknown"]
        self.assertGreater(len(new_keys), 40, "expected the new block's keys to be registered")
        for key in new_keys:
            for lang in _REQUIRED_LANGS:
                self.assertTrue(i18n._strings[key].get(lang), f"{key} missing {lang}")

    # Note: a bare "&" in a markup-parsed group title (the exact bug
    # caught during manual smoke testing of Section B — "Flatpak &
    # Flathub" crashed Adw.PreferencesGroup.set_title()'s markup
    # parser) and "every T(...) call site resolves to a real key" are
    # both already covered, repo-wide, by
    # tests/test_ui_consistency.py::MarkupSafeGroupTitleTests and
    # ::NoRawI18nKeyLeakTests — not duplicated here.


class PlaceholderConsistencyTests(unittest.TestCase):
    def test_format_placeholders_match_across_languages(self):
        mismatches = []
        for key, entry in i18n._strings.items():
            langs_present = [l for l in _REQUIRED_LANGS if entry.get(l)]
            if len(langs_present) < 2:
                continue
            placeholder_sets = {l: set(_PLACEHOLDER_RE.findall(entry[l])) for l in langs_present}
            reference = placeholder_sets[langs_present[0]]
            for lang, placeholders in placeholder_sets.items():
                if placeholders != reference:
                    mismatches.append((key, lang, placeholders, reference))
        self.assertEqual(mismatches, [], f"placeholder mismatch: {mismatches[:10]}")

    def test_known_placeholder_keys_actually_have_one(self):
        for key in ("sr_updates_available", "sr_orphans_preview"):
            self.assertIn("{n}", i18n._strings[key]["it"])
            self.assertIn("{n}", i18n._strings[key]["en"])
            self.assertIn("{n}", i18n._strings[key]["fr"])


if __name__ == "__main__":
    unittest.main()
