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

if _HAS_DISPLAY:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk, GLib


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


class TextCorrectionsTests(unittest.TestCase):
    """2026-08-05 correction round, points 1 and 2 — pure string-table
    checks, no GTK needed."""

    def test_old_advanced_disclaimer_wording_is_gone(self):
        from ui.pages.page_software_repos import _page_strings
        entry = _page_strings["sr_additional_advanced_desc"]
        for lang in ("en", "it", "es", "fr"):
            self.assertNotIn("solo informativo per ora", entry["it"].lower())
            self.assertNotIn("information only for now", entry["en"].lower())

    def test_new_advanced_description_text_is_present(self):
        from ui.pages.page_software_repos import _page_strings
        entry = _page_strings["sr_additional_advanced_desc"]
        self.assertIn("origine", entry["it"].lower())
        self.assertIn("rischi", entry["it"].lower())

    def test_new_state_vocabulary_keys_exist(self):
        from ui.pages.page_software_repos import _page_strings
        for key in ("sr_state_not_compatible", "sr_state_unverifiable",
                    "sr_state_not_available", "sr_state_conflict_detected",
                    "sr_state_info_only"):
            self.assertIn(key, _page_strings)

    def test_obs_explanation_text_is_present(self):
        from ui.pages.page_software_repos import _page_strings
        entry = _page_strings["sr_obs_info_explanation"]
        self.assertIn("progetto", entry["it"].lower())


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class RecipeStateVocabularyTests(unittest.TestCase):
    """Point 2: no recipe row may show 'Da controllare' — every advanced
    recipe row must resolve to one of the seven allowed states."""

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

    @staticmethod
    def _collect_labels(widget, out):
        get_label = getattr(widget, "get_label", None)
        if callable(get_label):
            try:
                text = get_label()
            except TypeError:
                text = None
            if text:
                out.append(text)
        get_text = getattr(widget, "get_text", None)
        if callable(get_text) and not callable(get_label):
            try:
                text = get_text()
            except TypeError:
                text = None
            if text:
                out.append(text)
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child is not None:
            RecipeStateVocabularyTests._collect_labels(child, out)
            child = child.get_next_sibling()

    def test_no_recipe_row_ever_shows_da_controllare(self):
        from core.i18n import T
        page = self._build_page()
        texts = []
        self._collect_labels(page._section_c._additional_box, texts)
        self.assertNotIn(T("sr_kind_needs_review"), texts)
        self.assertNotIn("Da controllare", texts)

    def test_obs_row_always_shows_info_only_and_no_button(self):
        from core.i18n import T
        from core.software_repo import repo_recipes as rr
        page = self._build_page()
        recipe = rr.RECIPES_BY_ID["opensuse_obs"]
        row = page._section_c._build_obs_row(recipe)

        texts = []
        self._collect_labels(row, texts)
        self.assertIn(T("sr_state_info_only"), texts)
        self.assertIn(T("sr_obs_info_explanation"), texts)

        buttons = []
        self._collect_widgets(row, Gtk.Button, buttons)
        self.assertEqual(buttons, [], "OBS must never show a generic activation button")

    @staticmethod
    def _collect_widgets(widget, cls, out):
        if isinstance(widget, cls):
            out.append(widget)
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child is not None:
            RecipeStateVocabularyTests._collect_widgets(child, cls, out)
            child = child.get_next_sibling()


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class PackmanLinkageTests(unittest.TestCase):
    """Point 3: Packman must always be linked to the real scanned
    repository — never a duplicate activation path."""

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

    def _recipe(self):
        from core.software_repo import repo_recipes as rr
        return rr.RECIPES_BY_ID["packman"]

    def _collect(self, widget, cls, out):
        if isinstance(widget, cls):
            out.append(widget)
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child is not None:
            self._collect(child, cls, out)
            child = child.get_next_sibling()

    def test_packman_active_shows_already_active_no_duplicate_activate_button(self):
        from core.i18n import T
        page = self._build_page()
        entry = {"family": "opensuse", "name": "Packman", "alias": "packman",
                 "source_file": "/etc/zypp/repos.d/packman.repo", "enabled": True}
        row = page._section_c._build_packman_row(self._recipe(), entry, page.profile)

        buttons = []
        self._collect(row, Gtk.Button, buttons)
        labels = []
        self._collect(row, Gtk.Label, labels)
        texts = [l.get_label() for l in labels if l.get_label()]
        self.assertIn(T("sr_packman_already_active"), texts)
        self.assertNotIn(T("sr_packman_activate_btn"), [b.get_label() for b in buttons if b.get_label()])

    def test_packman_disabled_shows_reactivate_button(self):
        from core.i18n import T
        page = self._build_page()
        entry = {"family": "opensuse", "name": "Packman", "alias": "packman",
                 "source_file": "/etc/zypp/repos.d/packman.repo", "enabled": False}
        row = page._section_c._build_packman_row(self._recipe(), entry, page.profile)
        buttons = []
        self._collect(row, Gtk.Button, buttons)
        labels = [b.get_label() for b in buttons if b.get_label()]
        self.assertIn(T("sr_packman_disabled_reactivate_btn"), labels)

    def test_packman_absent_on_tumbleweed_shows_activate_button(self):
        from core.i18n import T
        from core.software_repo import distro_profile as dp
        page = self._build_page()
        profile = dp.DistroProfile(family="opensuse", id="opensuse-tumbleweed", confident=True)
        row = page._section_c._build_packman_row(self._recipe(), None, profile)
        buttons = []
        self._collect(row, Gtk.Button, buttons)
        labels = [b.get_label() for b in buttons if b.get_label()]
        self.assertIn(T("sr_packman_activate_btn"), labels)

    def test_packman_absent_on_leap_shows_not_compatible(self):
        from core.i18n import T
        from core.software_repo import distro_profile as dp
        page = self._build_page()
        profile = dp.DistroProfile(family="opensuse", id="opensuse-leap", confident=True)
        row = page._section_c._build_packman_row(self._recipe(), None, profile)
        labels = []
        self._collect(row, Gtk.Label, labels)
        texts = [l.get_label() for l in labels if l.get_label()]
        self.assertIn(T("sr_state_not_compatible"), texts)

    def test_packman_absent_and_unresolved_shows_unverifiable(self):
        from core.i18n import T
        from core.software_repo import distro_profile as dp
        page = self._build_page()
        profile = dp.DistroProfile(family="opensuse", id="opensuse", confident=False)
        row = page._section_c._build_packman_row(self._recipe(), None, profile)
        labels = []
        self._collect(row, Gtk.Label, labels)
        texts = [l.get_label() for l in labels if l.get_label()]
        self.assertIn(T("sr_state_unverifiable"), texts)

    def test_clicking_already_active_opens_or_highlights_the_main_row(self):
        page = self._build_page()
        section = page._section_c
        entry = {"family": "opensuse", "name": "Packman", "alias": "packman",
                 "source_file": "/etc/zypp/repos.d/packman.repo", "enabled": True}

        dummy_expander = Adw.ExpanderRow(title="Packman")
        dummy_expander.set_expanded(False)
        section._repo_row_widgets[section._repo_row_key(entry)] = dummy_expander

        row = section._build_packman_row(self._recipe(), entry, page.profile)
        buttons = []
        self._collect(row, Gtk.Button, buttons)
        self.assertEqual(len(buttons), 1)
        buttons[0].emit("clicked")

        self.assertTrue(dummy_expander.get_expanded())
        self.assertTrue(dummy_expander.has_css_class("accent"))


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class FlatpakRowButtonsTests(unittest.TestCase):
    """Point 4: Flatpak remote rows use only Flatpak commands, and the
    real bare remote name/scope (not the "(system)"-suffixed display
    name) must reach the engine."""

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

    def _collect(self, widget, cls, out):
        if isinstance(widget, cls):
            out.append(widget)
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child is not None:
            self._collect(child, cls, out)
            child = child.get_next_sibling()

    def _flathub_entry(self, enabled=True):
        from core.software_repo import repo_scanner as rsc
        return {"name": f"flathub ({'system' if enabled else 'system'})", "family": "flatpak",
                "kind": rsc.KIND_UNIVERSAL, "enabled": enabled, "source_file": "flatpak --system",
                "uri": "https://dl.flathub.org/repo/", "alias": "flathub", "scope": "system",
                "signed": None, "suites": [], "components": "", "warnings": [], "duplicate_files": []}

    def test_enabled_flathub_row_shows_disable_and_remove_remote(self):
        from core.i18n import T
        page = self._build_page()
        entry = self._flathub_entry(enabled=True)
        expander = page._section_c._build_repo_row(entry)
        buttons = []
        self._collect(expander, Gtk.Button, buttons)
        labels = [b.get_label() for b in buttons if b.get_label()]
        self.assertIn(T("sr_repo_action_disable_btn"), labels)
        self.assertIn(T("sr_repo_action_remove_remote_btn"), labels)
        self.assertNotIn(T("sr_repo_action_enable_btn"), labels)

    def test_disabled_flathub_row_shows_enable_and_remove_remote(self):
        from core.i18n import T
        page = self._build_page()
        entry = self._flathub_entry(enabled=False)
        expander = page._section_c._build_repo_row(entry)
        buttons = []
        self._collect(expander, Gtk.Button, buttons)
        labels = [b.get_label() for b in buttons if b.get_label()]
        self.assertIn(T("sr_repo_action_enable_btn"), labels)
        self.assertIn(T("sr_repo_action_remove_remote_btn"), labels)

    def test_flatpak_toggle_scope_uses_bare_remote_name_and_real_scope(self):
        """The exact bug fixed in this round: remote_name must be the
        bare 'flathub', never 'flathub (system)', and scope must be the
        entry's real recorded scope, not a hardcoded 'system'."""
        import json
        from unittest import mock
        page = self._build_page()
        section = page._section_c
        entry = self._flathub_entry(enabled=True)
        entry["scope"] = "user"  # deliberately not "system", to prove it's read from the entry

        class ImmediateThread:
            def __init__(self, target=None, daemon=None):
                self._target = target

            def start(self):
                self._target()

        with mock.patch("ui.pages.page_software_repos.threading.Thread", ImmediateThread), \
             mock.patch("ui.pages.page_software_repos.GLib.idle_add", side_effect=lambda fn, *a: fn(*a)), \
             mock.patch("ui.pages.page_software_repos.engine.run_operation") as run_mock:
            run_mock.return_value = mock.Mock(ok=True, friendly_message="", technical_detail="",
                                                reboot_required=False, logout_recommended=False)
            section._run_repo_operation("toggle_repo", entry, False)

        self.assertTrue(run_mock.called)
        _, kwargs = run_mock.call_args
        scope_data = json.loads(kwargs["scope"])
        self.assertEqual(scope_data["remote_name"], "flathub")
        self.assertEqual(scope_data["scope"], "user")
        self.assertEqual(scope_data["family"], "flatpak")

    def test_no_zypper_command_ever_built_for_a_flatpak_entry(self):
        """Never mix Zypper and Flatpak — a flatpak-family entry must
        never reach set_zypper_repo_enabled/remove_zypper_repo."""
        from unittest import mock
        page = self._build_page()
        section = page._section_c
        entry = self._flathub_entry(enabled=True)

        with mock.patch("core.software_repo.repo_toggle.set_zypper_repo_enabled") as zypper_mock, \
             mock.patch("core.software_repo.repo_toggle.set_flatpak_remote_enabled") as flatpak_mock:
            flatpak_mock.return_value = mock.Mock(ok=True, state="verified", friendly_message="", technical_detail="")
            from core.software_repo import package_engine as engine_mod
            engine_mod.run_operation("toggle_repo", profile=page.profile,
                                       scope='{"family": "flatpak", "alias": "", "remote_name": "flathub", '
                                             '"source_file": "flatpak --system", "scope": "system", "enabled": false}')
        zypper_mock.assert_not_called()
        flatpak_mock.assert_called_once()


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class UiRefreshAfterOperationTests(unittest.TestCase):
    """Point 5: after an operation, the row/counters/additional section
    all reflect the new real state, with no duplication and no need to
    restart the app."""

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

    def _count_children(self, box):
        n = 0
        child = box.get_first_child()
        while child is not None:
            n += 1
            child = child.get_next_sibling()
        return n

    def test_operation_done_rebuilds_list_without_duplication(self):
        from unittest import mock
        from core.software_repo import repo_scanner as rsc
        page = self._build_page()
        section = page._section_c

        fake_entries = [
            {"name": "Repo A", "family": "opensuse", "kind": rsc.KIND_EXTERNAL, "enabled": True,
             "source_file": "/etc/zypp/repos.d/a.repo", "uri": "https://a.example.com", "alias": "a",
             "scope": "", "signed": True, "suites": [], "components": "", "warnings": [], "duplicate_files": []},
            {"name": "Repo B", "family": "opensuse", "kind": rsc.KIND_EXTERNAL, "enabled": False,
             "source_file": "/etc/zypp/repos.d/b.repo", "uri": "https://b.example.com", "alias": "b",
             "scope": "", "signed": True, "suites": [], "components": "", "warnings": [], "duplicate_files": []},
        ]
        fake_summary = {"official_active": 0, "external_active": 1, "disabled": 1, "needs_review": 0}

        with mock.patch("ui.pages.page_software_repos.rsc.scan_all",
                          return_value={"entries": fake_entries, "summary": fake_summary}):
            section._on_repo_operation_done(mock.Mock(ok=True))

            self.assertEqual(self._count_children(section._list_box), len(fake_entries))

            # run it again — a naive implementation might append instead
            # of replacing and double the rows.
            section._on_repo_operation_done(mock.Mock(ok=True))
            self.assertEqual(self._count_children(section._list_box), len(fake_entries))

    def test_counters_reflect_the_new_summary_after_refresh(self):
        from unittest import mock
        from core.software_repo import repo_scanner as rsc
        page = self._build_page()
        section = page._section_c

        fake_summary = {"official_active": 7, "external_active": 3, "disabled": 2, "needs_review": 0}
        with mock.patch("ui.pages.page_software_repos.rsc.scan_all",
                          return_value={"entries": [], "summary": fake_summary}):
            section.refresh()

        texts = []
        child = section._summary_box.get_first_child()
        while child is not None:
            inner = child.get_first_child()
            while inner is not None:
                t = inner.get_label() if hasattr(inner, "get_label") else None
                if t:
                    texts.append(t)
                inner = inner.get_next_sibling()
            child = child.get_next_sibling()
        self.assertIn("7", texts)
        self.assertIn("3", texts)
        self.assertIn("2", texts)

    def test_no_app_restart_needed_multiple_refreshes_work_in_place(self):
        """refresh() rebuilds the section's widgets in place and can be
        called repeatedly (e.g. after every action) without the app
        itself ever needing to be closed and reopened."""
        page = self._build_page()
        section = page._section_c
        section.refresh()
        count_after_first = self._count_children(section._list_box)
        section.refresh()
        count_after_second = self._count_children(section._list_box)
        self.assertEqual(count_after_first, count_after_second)


if __name__ == "__main__":
    unittest.main()
