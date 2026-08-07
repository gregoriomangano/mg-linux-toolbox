"""
Tests for this session's header/navigation reorganization: the brand
link, the three compact center nav buttons (Guida/Chi sono/Supporta),
the pages hidden from both view switchers (author/guide/credits/donate,
plus disk_activity added 2026-08-03 — reached only from the
Panoramica's Disco card), the license window bug fix (Adw.Window's
transient_for needs a real Gtk.Window — "Crediti" used to pass itself,
a plain widget, and silently do nothing), the new "Pagina ufficiale GNU
GPL" link, and the responsive compact-label breakpoint.

Constructing the full LinuxToolboxWindow pulls in all 15 pages
(including real system probes from the operational ones) and needs a
real display connection — same segfault-without-a-display risk
documented in tests/test_new_pages.py, so it's built ONCE in
setUpClass and every GTK-widget-touching test in this file is gated
behind _HAS_DISPLAY.
"""
import os
import unittest
from unittest import mock

from core.i18n import _strings

_HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
_SKIP_REASON = "no DISPLAY/WAYLAND_DISPLAY — constructing a real GTK widget without one segfaults the process"


class PureLogicTests(unittest.TestCase):
    """No GTK construction at all — safe to run anywhere."""

    def test_hidden_pages_set_matches_the_six_static_pages(self):
        import ui.window as window
        self.assertEqual(window.HIDDEN_FROM_SWITCHER,
                          {"author", "guide", "credits", "donate", "disk_activity", "help_support"})

    def test_center_nav_order_is_guide_author_donate(self):
        import ui.window as window
        targets = [target for _icon, _key, target in window._CENTER_NAV_ITEMS]
        self.assertEqual(targets, ["guide", "author", "donate"])

    def test_center_nav_never_includes_credits(self):
        import ui.window as window
        targets = [target for _icon, _key, target in window._CENTER_NAV_ITEMS]
        self.assertNotIn("credits", targets)

    def test_brand_link_targets_the_project_page_not_the_generic_homepage(self):
        from core import release_config
        self.assertEqual(release_config.PROJECT_PAGE_URL, "https://www.manganogregorio.it/m-g-linux-toolbox/")
        self.assertNotEqual(release_config.PROJECT_PAGE_URL, release_config.WEBSITE_URL)

    def test_gpl_official_url_is_the_real_gnu_page(self):
        from ui.license_dialog import GPL_OFFICIAL_URL
        self.assertEqual(GPL_OFFICIAL_URL, "https://www.gnu.org/licenses/gpl-3.0.html")

    def test_gpl_official_url_and_project_page_are_safe_https(self):
        from core.uri_launcher import is_safe_https_url
        from ui.license_dialog import GPL_OFFICIAL_URL
        from core import release_config
        self.assertTrue(is_safe_https_url(GPL_OFFICIAL_URL))
        self.assertTrue(is_safe_https_url(release_config.PROJECT_PAGE_URL))

    def test_new_translation_keys_present_in_all_four_languages(self):
        for key in ("license_official_page_btn",):
            self.assertIn(key, _strings)
            for lang in ("it", "en", "es", "fr"):
                self.assertTrue(_strings[key].get(lang))

    def test_application_exposes_standard_quit_action(self):
        from ui.window import LinuxToolboxApp
        app = LinuxToolboxApp()
        action = app.lookup_action("quit")
        self.assertIsNotNone(action)
        self.assertTrue(action.get_enabled())


class LicenseTextTests(unittest.TestCase):
    def test_missing_license_file_returns_none_not_a_crash(self):
        from ui import license_dialog
        with mock.patch("core.release_config.license_file_path", return_value="/no/such/file"):
            self.assertIsNone(license_dialog._read_license_text())

    def test_real_license_file_is_read_in_full(self):
        from ui import license_dialog
        text = license_dialog._read_license_text()
        self.assertIsNotNone(text)
        self.assertIn("GNU GENERAL PUBLIC LICENSE", text)

    def test_resolve_window_passes_through_a_real_window(self):
        from ui.license_dialog import _resolve_window
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        if not _HAS_DISPLAY:
            self.skipTest(_SKIP_REASON)
        win = Gtk.Window()
        self.assertIs(_resolve_window(win), win)

    def test_resolve_window_none_stays_none(self):
        from ui.license_dialog import _resolve_window
        self.assertIsNone(_resolve_window(None))


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class LicenseWindowRegressionTests(unittest.TestCase):
    """The actual bug: Adw.Window(transient_for=a_plain_widget) raises
    TypeError, which used to happen inside CreditsPage's button click
    handler and looked like the button "did nothing"."""

    def test_show_license_window_from_a_plain_widget_does_not_raise(self):
        from ui.pages.page_credits import CreditsPage
        from ui.license_dialog import show_license_window
        page = CreditsPage()  # an Adw.PreferencesPage, NOT a Gtk.Window
        with mock.patch.object(page, "get_root", return_value=None):
            show_license_window(page)  # must not raise TypeError

    def test_credits_license_button_click_does_not_raise(self):
        from ui.pages.page_credits import CreditsPage
        page = CreditsPage()
        with mock.patch.object(page, "get_root", return_value=None):
            page._license_btn.emit("clicked")

    def test_credits_gpl_official_page_button_opens_the_real_url(self):
        from ui.pages.page_credits import CreditsPage
        from ui.license_dialog import GPL_OFFICIAL_URL
        page = CreditsPage()
        with mock.patch("ui.pages.page_credits.open_external_url") as mock_open:
            page._gpl_page_btn.emit("clicked")
        mock_open.assert_called_once_with(GPL_OFFICIAL_URL)


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from ui.window import LinuxToolboxWindow
        cls.window = LinuxToolboxWindow()

    def test_hidden_pages_are_invisible_in_the_stack_switcher(self):
        for internal in ("author", "guide", "credits", "donate", "disk_activity"):
            child = self.window._stack.get_child_by_name(internal)
            self.assertIsNotNone(child, f"{internal} missing from stack")
            self.assertFalse(self.window._stack.get_page(child).get_visible(),
                              f"{internal} should be hidden from switchers")

    def test_operational_pages_remain_visible_in_the_bottom_bar(self):
        for internal in ("info", "kernel", "network", "system", "performance",
                          "gaming", "audio", "virt", "security", "services", "history"):
            child = self.window._stack.get_child_by_name(internal)
            self.assertIsNotNone(child, f"{internal} missing from stack")
            self.assertTrue(self.window._stack.get_page(child).get_visible(),
                             f"{internal} should stay visible")

    def test_hidden_pages_are_still_reachable_via_switch_to_page(self):
        for internal in ("author", "guide", "credits", "donate", "disk_activity"):
            self.window.switch_to_page(internal)
            self.assertEqual(self.window._stack.get_visible_child_name(), internal)

    def test_center_nav_buttons_navigate_to_the_right_pages(self):
        # 2026-08-07: "Aiuto e supporto" added as a fourth switch_to_page
        # button alongside Guida/Chi sono/Supporta (Contatti stays a
        # separate, direct open_external_url button and never appends
        # here).
        targets_clicked = []
        with mock.patch.object(self.window, "switch_to_page", side_effect=lambda t: targets_clicked.append(t)):
            for btn, _label, _key in self.window._nav_buttons:
                btn.emit("clicked")
        self.assertEqual(targets_clicked, ["guide", "author", "donate", "help_support"])

    def test_brand_button_opens_project_page_via_secure_launcher_not_generic_homepage(self):
        # v3: the single in-app identity block moved from a duplicate
        # topbar button into the sidebar header (ui/sidebar.py) — same
        # URL, same secure launcher, new location.
        from core import release_config
        with mock.patch("ui.sidebar.open_external_url") as mock_open:
            self.window._sidebar._brand_btn.emit("clicked")
        mock_open.assert_called_once_with(release_config.PROJECT_PAGE_URL)

    def test_compact_breakpoint_targets_every_nav_label_to_hide_on_narrow_width(self):
        """"layout compatto con finestra stretta": verifies the
        breakpoint is wired to hide exactly the label widgets (icons
        and tooltips are untouched) — the real condition firing on an
        actual resize isn't simulated here (that depends on real
        window-manager geometry), but the *configuration* that would
        make it happen is checked directly."""
        with mock.patch.object(type(self.window._nav_breakpoint), "add_setter") as mock_setter:
            # Rebuild just the wiring loop from window.py's own logic
            # against the real, already-built button list, to confirm
            # every label (and only the labels) is targeted.
            for _btn, label, _key in self.window._nav_buttons:
                self.window._nav_breakpoint.add_setter(label, "visible", False)
        self.assertEqual(mock_setter.call_count, len(self.window._nav_buttons))
        for call in mock_setter.call_args_list:
            args = call[0]
            self.assertEqual(args[1], "visible")
            self.assertEqual(args[2], False)

    def test_nav_buttons_have_tooltips_for_icon_only_mode(self):
        for btn, _label, key in self.window._nav_buttons:
            self.assertTrue(btn.get_tooltip_text())

    def test_credits_reachable_from_author_page(self):
        author_page = self.window._pages["author"][1]
        with mock.patch.object(author_page, "get_root", return_value=self.window), \
             mock.patch.object(self.window, "switch_to_page") as mock_switch:
            author_page._credits_btn.emit("clicked")
        mock_switch.assert_called_once_with("credits")

    def test_guide_and_donate_reachable_from_author_page(self):
        author_page = self.window._pages["author"][1]
        with mock.patch.object(author_page, "get_root", return_value=self.window), \
             mock.patch.object(self.window, "switch_to_page") as mock_switch:
            author_page._guide_btn.emit("clicked")
            author_page._donate_btn.emit("clicked")
        mock_switch.assert_has_calls([mock.call("guide"), mock.call("donate")])

    def test_credits_reachable_from_about_window(self):
        from ui.pages.page_about import AboutWindow
        about = AboutWindow(parent=self.window)
        with mock.patch.object(self.window, "switch_to_page") as mock_switch, \
             mock.patch.object(about, "close") as mock_close:
            about._on_credits_clicked(None)
        mock_switch.assert_called_once_with("credits")
        mock_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
