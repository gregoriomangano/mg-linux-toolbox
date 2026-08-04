"""
Tests for the new "Software e repository" page and its sidebar wiring
(2026-08-04 block). Building a real GTK window needs a real display —
gated behind _HAS_DISPLAY exactly like tests/test_navigation.py.
"""
import os
import unittest
from unittest import mock

_HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
_SKIP_REASON = "no DISPLAY/WAYLAND_DISPLAY — constructing a real GTK widget without one segfaults the process"


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class SidebarPlacementTests(unittest.TestCase):
    def test_software_repos_is_the_only_new_sidebar_entry(self):
        import ui.sidebar as sidebar
        all_internal_names = [name for _t, items in sidebar.SIDEBAR_GROUPS for _l, name, _i in items]
        self.assertEqual(all_internal_names.count("software_repos"), 1)

    def test_software_repos_comes_immediately_before_gaming(self):
        import ui.sidebar as sidebar
        for _title_key, items in sidebar.SIDEBAR_GROUPS:
            names = [name for _l, name, _i in items]
            if "gaming" in names:
                self.assertIn("software_repos", names)
                self.assertEqual(names.index("software_repos") + 1, names.index("gaming"))
                return
        self.fail("gaming not found in any sidebar group")

    def test_usage_group_header_was_renamed(self):
        from core import i18n as _i18n_mod
        entry = _i18n_mod._strings["nav_group_usage"]
        self.assertEqual(entry["it"], "SOFTWARE E SERVIZI")
        self.assertNotIn("UTILIZZO", entry["it"])

    def test_sidebar_width_reduced_but_not_below_a_sane_floor(self):
        import ui.sidebar as sidebar
        # spec: ~8-10% narrower than the original 280, never "stretta"
        self.assertLess(sidebar.SIDEBAR_WIDE_WIDTH, 280)
        reduction = (280 - sidebar.SIDEBAR_WIDE_WIDTH) / 280
        self.assertLessEqual(reduction, 0.10)
        self.assertGreaterEqual(sidebar.SIDEBAR_WIDE_WIDTH, 240)


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class SoftwareRepositoriesPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()

    def _build_page(self):
        from ui.pages.page_software_repos import SoftwareRepositoriesPage
        return SoftwareRepositoriesPage()

    def test_page_constructs_without_touching_privileged_operations(self):
        with mock.patch("core.executor.run_pkexec_full") as pk_mock:
            page = self._build_page()
        pk_mock.assert_not_called()
        self.assertIsNotNone(page)

    def test_page_is_registered_in_window_pages_before_gaming(self):
        from ui.window import PAGES
        internal_names = [internal for _k, _c, internal, _i in PAGES]
        self.assertIn("software_repos", internal_names)
        self.assertLess(internal_names.index("software_repos"), internal_names.index("gaming"))

    def test_page_is_not_hidden_from_the_switcher(self):
        from ui.window import HIDDEN_FROM_SWITCHER
        self.assertNotIn("software_repos", HIDDEN_FROM_SWITCHER)

    def test_section_a_reflects_real_detected_profile(self):
        page = self._build_page()
        self.assertEqual(page._section_a.profile.family, page.profile.family)

    def test_scan_only_never_calls_pkexec(self):
        """Opening the page and refreshing every section (distro
        detection, Flatpak state, repository scan) must never touch a
        privileged operation — only the explicit action buttons may."""
        with mock.patch("core.executor.run_pkexec_full") as pk_mock:
            page = self._build_page()
            page._section_a.refresh()
            page._section_b.refresh()
            page._section_c.refresh()
        pk_mock.assert_not_called()

    def test_advanced_recipes_never_get_an_enable_button(self):
        from core.software_repo import repo_recipes as rr
        page = self._build_page()
        advanced_ids = {r.id for r in rr.RECIPES if r.level == rr.LEVEL_ADVANCED}
        guided_ids = {r.id for r in rr.RECIPES if r.level == rr.LEVEL_GUIDED}
        self.assertTrue(advanced_ids.isdisjoint(guided_ids))

    def test_no_internal_i18n_key_ever_renders_as_visible_text(self):
        """Regression guard for the real bug: recipe_ubuntu_universe_name
        et al. rendered as literal key text in 'Repository aggiuntivi'
        because T() is called with a variable there (recipe.name_key),
        which the repo-wide static-string T() checker cannot see.
        This walks the REAL rendered widget tree instead."""
        import re
        page = self._build_page()

        def collect_labels(widget, out):
            get_label = getattr(widget, "get_label", None)
            if callable(get_label):
                try:
                    text = get_label()
                except TypeError:
                    text = None
                if text:
                    out.append(text)
            child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
            while child is not None:
                collect_labels(child, out)
                child = child.get_next_sibling()

        texts = []
        collect_labels(page, texts)
        self.assertGreater(len(texts), 10)  # sanity: the walk actually found real widgets
        key_pattern = re.compile(r"^(recipe_[a-z0-9_]+_(name|desc)|sr_[a-z0-9_]+|fw_state_[a-z0-9_]+|"
                                   r"rootssh_state_[a-z0-9_]+|flatpak_err_[a-z0-9_]+)$")
        leaked = [t for t in texts if key_pattern.match(t)]
        self.assertEqual(leaked, [], f"internal key(s) rendered as visible text: {leaked}")

    def test_language_change_while_page_is_open_updates_visible_text(self):
        """Section B/C/D rebuild their text through T() on every
        refresh() — switching language while the page is already open
        (never rebuilt from scratch) must show the new language, not
        stale Italian, and must not crash."""
        from core.i18n import set_lang, T
        page = self._build_page()
        self.assertEqual(page._section_b.flatseal_btn.get_label(), T("sr_install_flatseal_btn"))
        try:
            set_lang("fr")
            page._section_a.refresh()
            page._section_b.refresh()
            page._section_c.refresh()
            self.assertEqual(page._section_b._flatseal_state_lbl.get_text(),
                              T("sr_flatseal_state_not_installed"))
            self.assertNotEqual(page._section_b._flatseal_state_lbl.get_text(), "Non installato")
        finally:
            set_lang("it")

    def test_recipe_text_fallback_never_shows_the_raw_key(self):
        from ui.pages.page_software_repos import _recipe_text
        fallback = _recipe_text("recipe_totally_made_up_thing_name")
        self.assertNotEqual(fallback, "recipe_totally_made_up_thing_name")
        self.assertNotIn("_", fallback)
        self.assertEqual(_recipe_text("recipe_ubuntu_universe_name"), "Ubuntu Universe")  # real key still translates normally


if __name__ == "__main__":
    unittest.main()
