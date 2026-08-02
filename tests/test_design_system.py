"""
v4 design-system tests: technical-value -> translated-text mapping,
StatusPill correctness, the new "Stampanti e driver" page (no printer
duplication left in Sicurezza), Secure Boot's read-only presentation,
and that every original callback/backend call is still wired exactly
as before (no behavior change, only presentation).
"""
import os
import unittest
from unittest import mock

_HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
_SKIP_REASON = "no DISPLAY/WAYLAND_DISPLAY — constructing a real GTK widget without one segfaults the process"


class ValueTranslationTests(unittest.TestCase):
    """No GTK construction needed — pure mapping logic."""

    def test_power_saver_maps_to_italian_by_default_locale(self):
        from ui.design_system.value_translation import translated_value
        import core.i18n as i18n
        self.assertEqual(i18n._lang, "it")
        self.assertEqual(translated_value("power-saver"), "Risparmio energetico")

    def test_balanced_maps_to_bilanciato(self):
        from ui.design_system.value_translation import translated_value
        self.assertEqual(translated_value("balanced"), "Bilanciato")

    def test_performance_maps_to_prestazioni(self):
        from ui.design_system.value_translation import translated_value
        self.assertEqual(translated_value("performance"), "Prestazioni")

    def test_disabled_maps_to_disattivato(self):
        from ui.design_system.value_translation import translated_value
        self.assertEqual(translated_value("disabled"), "Disattivato")

    def test_installed_maps_to_installato(self):
        from ui.design_system.value_translation import translated_value
        self.assertEqual(translated_value("installed"), "Installato")

    def test_unmapped_value_passes_through_unchanged(self):
        """Never invents a translation for a value it doesn't know —
        same rule ChoiceKernelFeatureRow already follows for governor
        values it can't honestly rename."""
        from ui.design_system.value_translation import translated_value
        self.assertEqual(translated_value("schedutil"), "schedutil")

    def test_technical_values_used_by_the_backend_are_unchanged(self):
        """The mapping is presentation-only — the technical strings on
        the LEFT side of the table (what gets sent to the backend) must
        stay exactly what backend.all / power_providers expect."""
        from ui.design_system.value_translation import TECH_VALUE_KEYS
        for real_value in ("power-saver", "balanced", "performance"):
            self.assertIn(real_value, TECH_VALUE_KEYS)


class StatusPillTests(unittest.TestCase):
    def test_five_canonical_states_have_distinct_css_variants(self):
        from ui.design_system.status_pill import VARIANT_CSS
        for variant in ("success", "neutral", "warning", "absent", "danger"):
            self.assertIn(variant, VARIANT_CSS)
        # each of the 5 real variants must be visually distinct
        real = {VARIANT_CSS[v] for v in ("success", "neutral", "warning", "absent", "danger")}
        self.assertEqual(len(real), 5)

    def test_state_pill_success_states_get_a_check_icon(self):
        from ui.design_system.status_pill import _CANONICAL_STATES
        for state in ("active", "installed", "always_on"):
            variant, show_check = _CANONICAL_STATES[state]
            self.assertEqual(variant, "success")
            self.assertTrue(show_check)

    def test_state_pill_absent_states_are_not_success_or_danger(self):
        from ui.design_system.status_pill import _CANONICAL_STATES
        for state in ("not_installed", "not_available", "not_supported"):
            variant, _show_check = _CANONICAL_STATES[state]
            self.assertEqual(variant, "absent")


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class NoBackendChangeTests(unittest.TestCase):
    """Confirms the v4 restyle didn't change what actually gets called
    — same real backend.all functions, same real KernelFeature
    instances, just new CSS/prefix widgets around them."""

    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from ui.window import LinuxToolboxWindow
        cls.window = LinuxToolboxWindow()

    def test_secureboot_row_is_read_only_no_switch_no_button(self):
        # Note: every Adw.ExpanderRow (every FeatureRow in this app)
        # already has libadwaita's OWN internal disclosure-arrow Switch
        # for expand/collapse — that's Adwaita chrome present on every
        # single row in the app, not something this app adds, and not
        # something the user can use to change Secure Boot. What must
        # be verified is that THIS row's own *control* widget (what
        # FeatureRow/SwitchRow/InstallRow adds on top of that chrome)
        # is neither a switch nor a button — i.e. this is a plain
        # FeatureRow, not a SwitchRow or InstallRow.
        from ui.widgets import SwitchRow, InstallRow
        security_page = self.window._pages["security"][1]
        secureboot_row = self._find_row_titled(security_page, "Secure Boot")
        self.assertIsNotNone(secureboot_row, "Secure Boot row not found")
        self.assertNotIsInstance(secureboot_row, SwitchRow)
        self.assertNotIsInstance(secureboot_row, InstallRow)
        self.assertFalse(hasattr(secureboot_row, "switch"))
        self.assertFalse(hasattr(secureboot_row, "button"))

    def _find_row_titled(self, widget, title):
        import gi
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        if isinstance(widget, Adw.PreferencesRow) and widget.get_title() == title:
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = self._find_row_titled(child, title)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None

    def _find_all(self, widget, gtype):
        found = []
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        if isinstance(widget, gtype):
            found.append(widget)
        child = widget.get_first_child()
        while child is not None:
            found.extend(self._find_all(child, gtype))
            child = child.get_next_sibling()
        return found

    def test_printers_page_id_exists(self):
        self.assertIsNotNone(self.window._stack.get_child_by_name("printers"))

    def test_printers_page_reachable_via_sidebar(self):
        self.assertIn("printers", self.window._sidebar._tiles)
        self.window._sidebar._tiles["printers"].emit("clicked")
        self.assertEqual(self.window._stack.get_visible_child_name(), "printers")

    def test_no_printer_controls_remain_on_security_page(self):
        """The mandatory move: CUPS/driver rows must exist on exactly
        one page, never both."""
        security_page = self.window._pages["security"][1]
        printers_page = self.window._pages["printers"][1]
        self.assertFalse(hasattr(security_page, "cups"))
        self.assertFalse(hasattr(security_page, "printer_base"))
        self.assertTrue(hasattr(printers_page, "cups"))
        self.assertTrue(hasattr(printers_page, "printer_base"))

    def test_printers_cups_switch_calls_the_real_backend_function(self):
        printers_page = self.window._pages["printers"][1]
        toggled = not printers_page.cups.switch.get_active()
        with mock.patch("ui.pages.page_printers.B.cups_set", return_value=toggled) as mock_set:
            printers_page.cups.switch.set_active(toggled)
        mock_set.assert_called_once_with(toggled)

    def test_power_profile_segmented_control_calls_real_backend_when_present(self):
        performance_page = self.window._pages["performance"][1]
        if not hasattr(performance_page, "pp_segmented"):
            self.skipTest("this system's active power provider isn't power-profiles-daemon")
        with mock.patch("ui.pages.page_performance.B.set_power_profile") as mock_set:
            performance_page._on_pprofile_segmented("balanced")
        mock_set.assert_called_once_with("balanced")

    def test_ksm_row_still_uses_the_real_ksm_feature(self):
        virt_page = self.window._pages["virt"][1]
        # Find the KSM row by its real, unmodified KernelFeature id.
        found = self._find_ksm_row(virt_page)
        self.assertIsNotNone(found)
        self.assertEqual(found.feature.id, "virt.ksm")

    def _find_ksm_row(self, widget):
        from ui.pages.page_kernel import BooleanKernelFeatureRow
        if isinstance(widget, BooleanKernelFeatureRow):
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = self._find_ksm_row(child)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None

    def test_every_page_has_a_page_header(self):
        # "info" (the Home) keeps its own richer hero card, approved
        # separately in v2/v3 — not a generic ui.design_system.PageHeader
        # instance, so it's intentionally excluded here.
        operational = ("kernel", "network", "system", "performance",
                       "gaming", "audio", "printers", "virt", "security",
                       "services", "history")
        for internal in operational:
            page = self.window._pages[internal][1]
            self.assertIsNotNone(self._find_page_header(page),
                                  f"'{internal}' has no PageHeader")

    def _find_page_header(self, widget):
        from ui.design_system.page_header import PageHeader
        if isinstance(widget, PageHeader):
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = self._find_page_header(child)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None


if __name__ == "__main__":
    unittest.main()
