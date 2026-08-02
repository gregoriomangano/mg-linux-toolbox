"""
V5 tests: text/state polish and consistency fixes on top of the V4
design system — no backend/logic change, only presentation. Covers:
  - honest translation of governor/EPP technical values (A1)
  - grp_audio_power no longer a raw untranslated i18n key (A5)
  - Home kernel-functions counters wired to real state, no "da
    collegare" placeholder text anywhere (A3)
  - explicit SSH/Samba/Firewall/DNS state pills, never switch-position-only (A4)
  - AppArmor's 5 real detected states (A7)
  - Services page: one row per service, no Adw.ActionRow suffix pile-up (A6)
  - no raw (undefined) i18n key is ever handed to T() from static code
  - no unescaped "&" in a i18n string used as an Adw.PreferencesGroup title
    (regression test for the "Disk & I/O" GTK markup-parse crash found
    during the V5 audit)
"""
import os
import re
import unittest

_HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
_SKIP_REASON = "no DISPLAY/WAYLAND_DISPLAY — constructing a real GTK widget without one segfaults the process"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GovernorEppTranslationTests(unittest.TestCase):
    """Pure logic — no GTK needed."""

    def test_governor_performance_translates(self):
        from core.kernel_features.cpu import GovernorFeature
        from core.i18n import T
        f = GovernorFeature()
        self.assertEqual(T(f.to_friendly("performance")), "Prestazioni")

    def test_governor_powersave_translates(self):
        from core.kernel_features.cpu import GovernorFeature
        from core.i18n import T
        f = GovernorFeature()
        self.assertEqual(T(f.to_friendly("powersave")), "Risparmio energetico")

    def test_governor_unknown_value_passes_through_unchanged(self):
        """schedutil/ondemand/conservative never get an invented name."""
        from core.kernel_features.cpu import GovernorFeature
        f = GovernorFeature()
        self.assertEqual(f.to_friendly("schedutil"), "schedutil")

    def test_epp_balance_performance_translates(self):
        from core.kernel_features.cpu import EPPFeature
        from core.i18n import T
        f = EPPFeature()
        self.assertEqual(T(f.to_friendly("balance_performance")), "Bilanciato verso le prestazioni")

    def test_epp_balance_power_translates(self):
        from core.kernel_features.cpu import EPPFeature
        from core.i18n import T
        f = EPPFeature()
        self.assertEqual(T(f.to_friendly("balance_power")), "Bilanciato verso il risparmio")

    def test_epp_power_stays_untranslated_no_honest_mapping(self):
        from core.kernel_features.cpu import EPPFeature
        f = EPPFeature()
        self.assertEqual(f.to_friendly("power"), "power")

    def test_to_friendly_returns_a_key_never_invented_text(self):
        """to_friendly() only changes what's DISPLAYED (via T() at the
        UI layer) — the raw value itself ("performance") is still what
        validate()/apply_temporary() receive and send to the backend."""
        from core.kernel_features.cpu import GovernorFeature
        f = GovernorFeature()
        self.assertEqual(f.to_friendly("performance"), "cpu_val_performance")


class AudioGroupKeyTests(unittest.TestCase):
    def test_grp_audio_power_is_a_real_translated_key_not_a_raw_leak(self):
        from core.i18n import T, _strings
        self.assertIn("grp_audio_power", _strings)
        self.assertEqual(T("grp_audio_power"), "Risparmio energetico audio")
        self.assertNotEqual(T("grp_audio_power"), "grp_audio_power")

    def test_audio_power_seconds_are_spelled_out(self):
        from core.i18n import T
        self.assertEqual(T("audio_power_seconds_one"), "1 secondo")
        self.assertEqual(T("audio_power_seconds_many").format(n=5), "5 secondi")


class HomeCountersTests(unittest.TestCase):
    def test_no_pending_placeholder_key_survives_in_i18n(self):
        from core.i18n import _strings
        self.assertNotIn("ov2_kernel_pending", _strings)

    def test_source_never_shows_the_literal_da_collegare_phrase(self):
        path = os.path.join(_REPO_ROOT, "ui", "pages", "page_overview.py")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("da collegare", text)

    def test_count_feature_state_returns_real_ints_never_a_dash_placeholder(self):
        from ui.pages.page_overview import _count_feature_state
        active, temporary, permanent = _count_feature_state()
        for n in (active, temporary, permanent):
            self.assertIsInstance(n, int)
            self.assertGreaterEqual(n, 0)

    def test_count_feature_state_reads_the_real_rollback_store_mode_field(self):
        from unittest import mock
        from core.persistence.rollback_store import FeatureRecord
        fake_records = {
            "cpu.turbo_boost": FeatureRecord(feature_id="cpu.turbo_boost", initial_value=True, mode="temporary"),
            "memory.swappiness": FeatureRecord(feature_id="memory.swappiness", initial_value=60, mode="persistent"),
        }
        class FakeStore:
            def all(self):
                return fake_records
        with mock.patch("core.persistence.rollback_store.default_state_store", return_value=FakeStore()):
            from ui.pages.page_overview import _count_feature_state
            active, temporary, permanent = _count_feature_state()
        self.assertEqual((active, temporary, permanent), (2, 1, 1))


class AppArmorStateTests(unittest.TestCase):
    def test_not_available_when_not_installed(self):
        from unittest import mock
        from ui.pages.page_security import _apparmor_state
        with mock.patch("ui.pages.page_security.aa.is_installed", return_value=False):
            self.assertEqual(_apparmor_state(), "not_available")

    def test_inactive_when_installed_but_service_down(self):
        from unittest import mock
        from ui.pages.page_security import _apparmor_state
        with mock.patch("ui.pages.page_security.aa.is_installed", return_value=True), \
             mock.patch("ui.pages.page_security.aa.service_active", return_value=False):
            self.assertEqual(_apparmor_state(), "inactive")

    def test_supported_not_configured_when_active_with_zero_profiles(self):
        from unittest import mock
        from ui.pages.page_security import _apparmor_state
        with mock.patch("ui.pages.page_security.aa.is_installed", return_value=True), \
             mock.patch("ui.pages.page_security.aa.service_active", return_value=True), \
             mock.patch("ui.pages.page_security.aa.list_profiles", return_value=[]):
            self.assertEqual(_apparmor_state(), "supported_not_configured")

    def test_active_configured_when_active_with_real_profiles(self):
        from unittest import mock
        from ui.pages.page_security import _apparmor_state
        with mock.patch("ui.pages.page_security.aa.is_installed", return_value=True), \
             mock.patch("ui.pages.page_security.aa.service_active", return_value=True), \
             mock.patch("ui.pages.page_security.aa.list_profiles", return_value=[{"path": "/usr/bin/foo", "mode": "enforce"}]):
            self.assertEqual(_apparmor_state(), "active_configured")

    def test_unknown_state_never_crashes_never_guesses(self):
        from unittest import mock
        from ui.pages.page_security import _apparmor_state
        with mock.patch("ui.pages.page_security.aa.is_installed", side_effect=RuntimeError("boom")):
            self.assertEqual(_apparmor_state(), "unknown")

    def test_all_five_states_have_a_distinct_translated_pill_text(self):
        from ui.pages.page_security import _APPARMOR_PILL
        from core.i18n import T
        self.assertEqual(set(_APPARMOR_PILL), {
            "active_configured", "supported_not_configured", "inactive",
            "not_available", "unknown",
        })
        texts = {T(text_key) for _variant, _check, text_key in _APPARMOR_PILL.values()}
        self.assertEqual(len(texts), 5)


class SecureBootTriStateTests(unittest.TestCase):
    def test_unknown_when_the_underlying_command_fails(self):
        from unittest import mock
        import backend.all as B
        with mock.patch("backend.all.run_command", return_value=(False, "", "")):
            self.assertEqual(B.secureboot_state(), "unknown")

    def test_active_when_enabled_reported(self):
        from unittest import mock
        import backend.all as B
        with mock.patch("backend.all.run_command", return_value=(True, "SecureBoot enabled", "")):
            self.assertEqual(B.secureboot_state(), "active")

    def test_inactive_when_disabled_reported(self):
        from unittest import mock
        import backend.all as B
        with mock.patch("backend.all.run_command", return_value=(True, "SecureBoot disabled", "")):
            self.assertEqual(B.secureboot_state(), "inactive")


class ServicesRowStructureTests(unittest.TestCase):
    def test_service_row_is_not_a_bare_action_row_with_crammed_suffix(self):
        """The redesigned row is a plain Gtk.Box (header + reflowing
        actions), not an Adw.ActionRow with everything crammed as one
        suffix — see ui/pages/page_services.py ServiceRow."""
        import gi
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        from ui.pages.page_services import ServiceRow
        self.assertFalse(issubclass(ServiceRow, Adw.ActionRow))

    def test_not_installed_status_pill_text_is_short_not_the_full_sentence(self):
        from core.i18n import T
        self.assertEqual(T("svc_status_not_installed"), "Non installato")
        # the full explanatory sentence still exists, but only for the
        # secondary description text, never duplicated onto the pill
        self.assertNotEqual(T("svc_not_found"), T("svc_status_not_installed"))


class NoRawI18nKeyLeakTests(unittest.TestCase):
    """Static-analysis regression test: every literal T("...") call in
    the source must resolve to a real, defined i18n key — this is
    exactly the class of bug grp_audio_power was (used, never
    defined, so the UI showed the raw key text itself)."""

    def test_every_static_T_call_resolves_to_a_defined_key(self):
        import core.i18n as i18n_mod
        # import every UI module so any module-level side-effect string
        # registration (the ui.design_system.value_translation pattern)
        # has already happened, exactly like it has by the time the real
        # app renders anything.
        import importlib
        import pkgutil
        import ui.pages
        for _finder, name, _ispkg in pkgutil.iter_modules(ui.pages.__path__, "ui.pages."):
            importlib.import_module(name)
        import ui.window  # noqa: F401 — pulls in sidebar/widgets/etc.

        pattern = re.compile(r'T\("([a-zA-Z0-9_]+)"\)')
        missing = {}
        for root, _dirs, files in os.walk(_REPO_ROOT):
            if "__pycache__" in root or f"{os.sep}.git" in root:
                continue
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(root, fname)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                for m in pattern.finditer(text):
                    key = m.group(1)
                    if key == "key":
                        continue  # docstring example in core/i18n.py itself
                    if key not in i18n_mod._strings:
                        missing.setdefault(key, os.path.relpath(path, _REPO_ROOT))
        self.assertEqual(missing, {}, f"undefined i18n keys referenced via T(...): {missing}")


class MarkupSafeGroupTitleTests(unittest.TestCase):
    """Regression test for the real GTK markup-parse crash found during
    the V5 audit: Adw.PreferencesGroup.set_title() (what make_group()/
    make_section() feed) DOES parse Pango markup, so a bare "&" in an
    i18n string used as a group title breaks rendering — it must be
    "&amp;" there. (Page/sidebar titles are plain Gtk.Label text and
    are NOT affected — this check is scoped to group-title keys only.)
    """

    def _group_title_keys(self):
        keys = set()
        pattern = re.compile(r'make_(?:group|section)\("([a-zA-Z0-9_]+)"')
        for root, _dirs, files in os.walk(os.path.join(_REPO_ROOT, "ui")):
            if "__pycache__" in root:
                continue
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                with open(os.path.join(root, fname), encoding="utf-8") as f:
                    text = f.read()
                keys.update(pattern.findall(text))
        return keys

    def test_no_bare_ampersand_in_any_group_title_key(self):
        from core.i18n import _strings
        bad = []
        bare_amp = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)")
        for key in self._group_title_keys():
            entry = _strings.get(key, {})
            for lang, text in entry.items():
                if bare_amp.search(text):
                    bad.append((key, lang, text))
        self.assertEqual(bad, [], f"bare '&' in a markup-parsed group title: {bad}")


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class NetworkStatusPillTests(unittest.TestCase):
    """SSH/Samba/Firewall/DNS switches must never rely on switch
    position alone — each row gets an explicit StatusPill next to it."""

    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from ui.pages.page_network import NetworkPage
        cls.page = NetworkPage()

    def _has_status_pill(self, row):
        from ui.design_system.status_pill import StatusPill
        child = row.get_first_child()
        # ExpanderRow internals are nested; walk the whole subtree.
        stack = [row]
        while stack:
            w = stack.pop()
            if isinstance(w, StatusPill):
                return True
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        return False

    def test_ssh_row_has_an_explicit_status_pill(self):
        self.assertTrue(self._has_status_pill(self.page.ssh))

    def test_samba_row_has_an_explicit_status_pill(self):
        self.assertTrue(self._has_status_pill(self.page.samba))

    def test_firewall_row_has_an_explicit_status_pill(self):
        self.assertTrue(self._has_status_pill(self.page.fw))

    def test_dns_over_tls_row_has_an_explicit_status_pill(self):
        self.assertTrue(self._has_status_pill(self.page.dns))


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class AllPagesConstructTests(unittest.TestCase):
    """Every page must build without raising — the broad V5 audit net."""

    def test_all_sixteen_pages_plus_home_construct(self):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from ui.window import PAGES
        from ui.pages.page_overview import OverviewPage
        built = 0
        for _key, PageClass, _internal, _icon in PAGES:
            if PageClass is OverviewPage:
                PageClass(navigate_callback=lambda t: None)
            else:
                PageClass()
            built += 1
        self.assertEqual(built, len(PAGES))


if __name__ == "__main__":
    unittest.main()
