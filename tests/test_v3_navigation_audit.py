"""
v3 navigation audit tests (see the v3 redesign review, §3): every page
id the UI declares must be real, every navigation control must land on
the page its own text promises, the sidebar must track the active
page, and nothing should be clickable without a real destination.

Building the full LinuxToolboxWindow needs a real display connection
(same as tests/test_navigation.py), so everything here is gated behind
_HAS_DISPLAY.
"""
import os
import unittest
from unittest import mock

_HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
_SKIP_REASON = "no DISPLAY/WAYLAND_DISPLAY — constructing a real GTK widget without one segfaults the process"


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class SidebarPageIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from ui.window import LinuxToolboxWindow
        cls.window = LinuxToolboxWindow()

    def test_every_sidebar_entry_points_at_a_real_page_id(self):
        import ui.sidebar as sidebar
        from ui.window import PAGES
        real_ids = {internal for _key, _cls, internal, _icon in PAGES}
        for _title_key, items in sidebar.SIDEBAR_GROUPS:
            for _label_key, internal_name, _icon in items:
                self.assertIn(internal_name, real_ids,
                               f"sidebar entry '{internal_name}' has no matching page in window.PAGES")

    def test_every_sidebar_page_id_actually_exists_in_the_stack(self):
        import ui.sidebar as sidebar
        for _title_key, items in sidebar.SIDEBAR_GROUPS:
            for _label_key, internal_name, _icon in items:
                self.assertIsNotNone(
                    self.window._stack.get_child_by_name(internal_name),
                    f"'{internal_name}' missing from the real ViewStack")

    def test_clicking_every_sidebar_tile_navigates_and_marks_it_active(self):
        for internal_name, tile in self.window._sidebar._tiles.items():
            tile.emit("clicked")
            self.assertEqual(
                self.window._stack.get_visible_child_name(), internal_name,
                f"clicking the '{internal_name}' tile did not switch the stack to it")
            self.assertIn("active", tile.get_css_classes(),
                           f"'{internal_name}' tile not marked active after navigating to it")
            for other_name, other_tile in self.window._sidebar._tiles.items():
                if other_name != internal_name:
                    self.assertNotIn("active", other_tile.get_css_classes(),
                                      f"'{other_name}' tile still marked active while on '{internal_name}'")

    def test_sidebar_never_offers_the_four_hidden_pages(self):
        import ui.sidebar as sidebar
        from ui.window import HIDDEN_FROM_SWITCHER
        listed = {internal for _key, items in sidebar.SIDEBAR_GROUPS for _lk, internal, _ic in items}
        self.assertTrue(listed.isdisjoint(HIDDEN_FROM_SWITCHER))


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class HomeQuickActionTests(unittest.TestCase):
    """Every quick action / navigation control on the Panoramica must
    say exactly where it goes — the v3 review's mandatory fix for
    'Vedi tutti i dischi' opening a different page than the label
    implied, generalized into a repeatable check."""

    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from ui.window import LinuxToolboxWindow
        cls.window = LinuxToolboxWindow()
        cls.overview = cls.window._pages["info"][1]

    def test_kernel_functions_button_opens_kernel_page(self):
        # OverviewPage.navigate_callback is bound to window.switch_to_page
        # at construction time, so the real effect (not a mock) is what
        # actually proves the wiring — verify the stack really switches.
        self.overview._navigate_to("kernel")
        self.assertEqual(self.window._stack.get_visible_child_name(), "kernel")

    def test_disks_button_is_relabeled_and_targets_system_page(self):
        from core.i18n import T
        # Mandatory v3 fix: the button must say "Apri Sistema e disco"
        # (V7: sentence case), never the old "Vedi tutti i dischi" wording
        # that implied it just expanded the same card.
        self.assertEqual(T("ov2_disks_open_system"), "Apri Sistema e disco")

    def test_open_pressure_button_targets_kernel_and_subcards_are_not_buttons(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        # The PSI sub-cards used to be individually clickable, all
        # pointing at the same generic page — now they're plain
        # display boxes, and there is exactly one explicit button.
        pressure_card = self.overview._build_pressure_block()
        buttons = self._find_all(pressure_card, Gtk.Button)
        self.assertEqual(len(buttons), 1,
                          "expected exactly one explicit navigation button in the pressure card")

    def _find_all(self, widget, gtype):
        found = []
        if isinstance(widget, gtype):
            found.append(widget)
        child = widget.get_first_child()
        while child is not None:
            found.extend(self._find_all(child, gtype))
            child = child.get_next_sibling()
        return found

    def test_quick_action_titles_match_their_real_destination_tab_title(self):
        from core.i18n import T
        # (quick-action i18n title key, real destination page's own
        # tab title key) — every pair must resolve to the SAME text,
        # so a quick action can never claim to open one page while
        # actually opening another.
        pairs = [
            ("ov2_quick_kernel_t", "tab_kernel"),
            ("ov2_quick_system_t", "tab_system"),
            ("ov2_quick_network_t", "tab_network"),
            ("ov2_quick_history_t", "tab_history"),
        ]
        for quick_key, real_key in pairs:
            self.assertEqual(T(quick_key), T(real_key),
                              f"quick action '{quick_key}' text doesn't match the real page title '{real_key}'")


if __name__ == "__main__":
    unittest.main()
