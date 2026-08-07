"""
Tests for this session's four deliverables:
  1. the real cause of the "oval pill" complaint (KernelFeatureRow's
     collapsed suffix missing valign=CENTER) and its fix — the real
     shared StatusPill widget, valign=CENTER, never a stretched Label;
  2. Services page rows are real visible cards (a genuine "row" CSS
     node in the widget tree, not a bare Gtk.Box with no chrome);
  3. the new "Aiuto e supporto" page/nav button, using the internal
     photo asset (never a /home/*/Scaricati runtime path);
  4. the new ClamAV row on "Sicurezza" (moved there from "Rete e
     dispositivi" on 2026-08-07, along with Firewall/SSH).

GTK-constructing tests are gated behind _HAS_DISPLAY, same convention
as the rest of this test suite.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
_SKIP_REASON = "no DISPLAY/WAYLAND_DISPLAY — constructing a real GTK widget without one segfaults the process"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PureLogicTests(unittest.TestCase):
    def test_internal_photo_asset_exists(self):
        path = os.path.join(_REPO_ROOT, "assets", "images", "gregorio-supporto.jpg")
        self.assertTrue(os.path.isfile(path), path)

    def test_help_page_source_never_references_a_runtime_downloads_path(self):
        with open(os.path.join(_REPO_ROOT, "ui", "pages", "page_help_support.py")) as f:
            src = f.read()
        self.assertNotIn("Scaricati", src)
        self.assertNotIn("/home/", src)

    def test_youtube_url_is_the_verified_readme_channel(self):
        from core import release_config
        self.assertEqual(release_config.YOUTUBE_URL, "https://www.youtube.com/@GregorioMangano")

    def test_author_page_reuses_the_same_photo_as_help_support_no_second_copy(self):
        from ui.pages.page_author import _PHOTO_PATH as author_photo
        from ui.pages.page_help_support import _PHOTO_PATH as help_photo
        self.assertEqual(os.path.abspath(author_photo), os.path.abspath(help_photo))
        self.assertTrue(os.path.isfile(author_photo))

    def test_support_email_is_a_central_constant_never_scattered_placeholders(self):
        from core import release_config
        self.assertTrue(hasattr(release_config, "SUPPORT_EMAIL"))
        # Deliberately empty until a real "info@..." mailbox is verified —
        # never a guessed domain/local-part.
        self.assertEqual(release_config.SUPPORT_EMAIL, "")


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class KernelPillFixTests(unittest.TestCase):
    """The real bug: a bare Gtk.Label suffix with no valign=CENTER
    stretches to the row's full height under Adw.ExpanderRow, and the
    ds-pill border-radius:999px turns that stretched box into a big
    oval — visually different from the reference page's StatusPill
    (Rete e dispositivi / Sicurezza) even though both used the same
    CSS class names."""

    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()

    def test_status_pill_is_the_real_shared_statuspill_widget(self):
        from ui.kernel.feature_row import KernelFeatureRow
        from ui.design_system.status_pill import StatusPill
        from core.kernel_features.ksm import KsmFeature
        from core.kernel_features.registry import register
        row = KernelFeatureRow(register(KsmFeature()), "virt_ksm")
        self.assertIsInstance(row._status_pill, StatusPill)

    def test_status_pill_and_status_text_are_vertically_centered(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        from ui.kernel.feature_row import KernelFeatureRow
        from core.kernel_features.ksm import KsmFeature
        from core.kernel_features.registry import register
        row = KernelFeatureRow(register(KsmFeature()), "virt_ksm")
        self.assertEqual(row._status_pill.get_valign(), Gtk.Align.CENTER)
        self.assertEqual(row._status_text.get_valign(), Gtk.Align.CENTER)

    def test_multi_value_row_uses_plain_text_not_a_pill(self):
        from ui.pages.page_kernel import GovernorRow
        row = GovernorRow()
        self.assertFalse(row._status_is_pill)
        self.assertTrue(row._status_text.get_visible())
        self.assertFalse(row._status_pill.get_visible())

    def test_boolean_row_still_uses_a_short_pill(self):
        from ui.pages.page_kernel import ZramRow
        row = ZramRow()
        self.assertTrue(row._status_is_pill)
        self.assertTrue(row._status_pill.get_visible())
        self.assertFalse(row._status_text.get_visible())

    def test_audio_power_row_is_multivalue_not_a_pill(self):
        """Explicitly called out as a still-oval example — must be
        plain text like every other multi-value kernel row."""
        from ui.pages.page_audio import AudioPowerRow
        row = AudioPowerRow()
        self.assertFalse(row._status_is_pill)

    def test_ksm_row_pill_stays_short_autostart_is_a_separate_line(self):
        """The old bug: autostart info concatenated into the SAME text
        as the pill ("Disattivata · Avvio automatico: ...") — now two
        separate widgets, the pill stays a short state word."""
        from ui.pages.page_kernel import BooleanKernelFeatureRow
        from core.kernel_features.ksm import KsmFeature
        from core.kernel_features.registry import register
        row = BooleanKernelFeatureRow(register(KsmFeature()), "virt_ksm")
        pill_text = row._status_pill._label.get_label()
        self.assertNotIn("·", pill_text)
        self.assertLess(len(pill_text), 20)


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class ServicesRealCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()

    @staticmethod
    def _ancestor_css_names(widget, limit=12):
        names = []
        w = widget
        for _ in range(limit):
            if w is None:
                break
            names.append(w.get_css_name())
            w = w.get_parent()
        return names

    def test_every_service_row_sits_inside_a_real_row_css_node(self):
        """Adw.PreferencesGroup.add() on a bare Gtk.Box produces NO
        "row" CSS node at all (verified directly against a live
        Adw.PreferencesGroup) — this is the actual reason services
        looked "resting on the background". Each ServiceRow must now
        be wrapped so a real "row" node exists above it."""
        from ui.pages.page_services import ServicesPage, ServiceRow
        page = ServicesPage()
        found_any = False

        def walk(w):
            nonlocal found_any
            if isinstance(w, ServiceRow):
                found_any = True
                names = self._ancestor_css_names(w)
                self.assertIn("row", names, f"no 'row' CSS node above ServiceRow: {names}")
            child = w.get_first_child() if hasattr(w, "get_first_child") else None
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(page)
        self.assertTrue(found_any, "no ServiceRow instances found in ServicesPage")

    def test_service_row_itself_is_still_a_plain_box(self):
        """The fix wraps ServiceRow in an outer Adw.ActionRow — it must
        NOT turn ServiceRow itself into an Adw.ActionRow subclass
        (that was the earlier "crammed suffix" anti-pattern)."""
        import gi
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        from ui.pages.page_services import ServiceRow
        self.assertFalse(issubclass(ServiceRow, Adw.ActionRow))


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class HelpSupportPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()

    def test_page_constructs_and_has_a_title(self):
        from ui.pages.page_help_support import HelpSupportPage
        page = HelpSupportPage()
        self.assertTrue(page.get_title())

    def test_page_is_registered_and_hidden_from_switcher(self):
        from ui.window import LinuxToolboxWindow, HIDDEN_FROM_SWITCHER
        win = LinuxToolboxWindow()
        self.assertIn("help_support", win._pages)
        self.assertIn("help_support", HIDDEN_FROM_SWITCHER)
        child = win._stack.get_child_by_name("help_support")
        self.assertFalse(win._stack.get_page(child).get_visible())

    def test_nav_button_reaches_the_page_and_has_the_highlight_class(self):
        from ui.window import LinuxToolboxWindow
        win = LinuxToolboxWindow()
        matches = [(btn, key) for btn, _label, key in win._nav_buttons if key == "tab_help_support"]
        self.assertEqual(len(matches), 1)
        btn, _key = matches[0]
        self.assertIn("mgv2-topbar-nav-btn-highlight", btn.get_css_classes())
        targets = []
        with mock.patch.object(win, "switch_to_page", side_effect=lambda t: targets.append(t)):
            btn.emit("clicked")
        self.assertEqual(targets, ["help_support"])

    def test_email_row_hidden_while_support_email_is_not_configured(self):
        from ui.pages.page_help_support import HelpSupportPage
        from core import release_config
        self.assertEqual(release_config.SUPPORT_EMAIL, "")
        page = HelpSupportPage()
        self.assertFalse(page._email_row.get_visible())

    def test_website_and_youtube_buttons_use_the_secure_launcher(self):
        from ui.pages.page_help_support import HelpSupportPage
        from core import release_config
        page = HelpSupportPage()
        with mock.patch("ui.pages.page_help_support.open_external_url") as m:
            page._site_btn.emit("clicked")
            m.assert_called_once_with(release_config.WEBSITE_URL)
        with mock.patch("ui.pages.page_help_support.open_external_url") as m:
            page._write_btn.emit("clicked")
            m.assert_called_once_with(release_config.CONTACT_URL)


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class ClamAVRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()

    def test_security_page_has_a_clamav_row_under_its_own_group(self):
        from ui.pages.page_security import SecurityPage, ClamAVRow
        page = SecurityPage()
        self.assertIsInstance(page.clamav, ClamAVRow)

    def test_network_page_no_longer_has_a_clamav_row(self):
        from ui.pages.page_network import NetworkPage
        page = NetworkPage()
        self.assertFalse(hasattr(page, "clamav"))

    def test_network_page_no_longer_has_firewall_or_ssh(self):
        """Firewall/SSH moved to Sicurezza — must not remain duplicated
        on Rete e dispositivi."""
        from ui.pages.page_network import NetworkPage
        page = NetworkPage()
        self.assertFalse(hasattr(page, "fw"))
        self.assertFalse(hasattr(page, "ssh"))

    def test_closed_row_shows_a_compact_pill_never_a_bare_switch_only(self):
        import core.clamav as clamav
        from ui.pages.page_security import ClamAVRow
        with mock.patch.object(clamav, "is_installed", return_value=False), \
             mock.patch.object(clamav, "is_available_in_repos", return_value=True):
            row = ClamAVRow()
        self.assertTrue(row._install_btn.get_visible())
        self.assertTrue(row._status_pill.get_visible())

    def test_installed_row_hides_the_install_button(self):
        import core.clamav as clamav
        from ui.pages.page_security import ClamAVRow
        with mock.patch.object(clamav, "is_installed", return_value=True), \
             mock.patch.object(clamav, "signatures_status", return_value="ready"), \
             mock.patch.object(clamav, "clamd_active", return_value=True):
            row = ClamAVRow()
        self.assertFalse(row._install_btn.get_visible())

    def test_never_claims_real_time_protection_anywhere_in_its_strings(self):
        from core.i18n import _strings
        banned = ("tempo reale", "real time", "real-time", "tiempo real", "temps réel")
        for key, translations in _strings.items():
            if not key.startswith("clamav"):
                continue
            for lang, text in translations.items():
                lowered = text.lower()
                for phrase in banned:
                    self.assertNotIn(phrase, lowered,
                                      f"{key}[{lang}] implies always-on real-time protection: {text!r}")

    def test_never_calls_the_word_protetto_for_a_merely_installed_state(self):
        from core.i18n import T, set_lang
        set_lang("it")
        self.assertNotIn("protett", T("clamav_state_installed").lower())
        self.assertNotIn("protett", T("clamav_state_not_installed").lower())

    def test_expanded_body_explains_what_usefor_and_limitation(self):
        from ui.pages.page_security import ClamAVRow
        row = ClamAVRow()
        self.assertTrue(row._explain_lbl.get_text())
        self.assertTrue(row._what_lbl.get_text())
        self.assertTrue(row._usefor_lbl.get_text())
        self.assertTrue(row._limit_lbl.get_text())

    def test_scan_never_blocks_the_gtk_thread(self):
        """_start_scan must hand off to a background thread, never call
        clamav.scan_path() synchronously on the calling (GTK) thread."""
        from ui.pages.page_security import ClamAVRow
        row = ClamAVRow()
        with mock.patch("ui.pages.page_security.clamav.scan_path") as scan_mock, \
             mock.patch("ui.pages.page_security.threading.Thread") as thread_mock:
            row._start_scan("/tmp")
            scan_mock.assert_not_called()
            thread_mock.assert_called_once()
            self.assertTrue(thread_mock.return_value.start.called)

    def test_update_definitions_never_blocks_the_gtk_thread(self):
        import core.clamav as clamav
        from ui.pages.page_security import ClamAVRow
        # Button is only enabled when ClamAV + freshclam are really
        # present — force that state so the click isn't a no-op.
        with mock.patch.object(clamav, "is_installed", return_value=True), \
             mock.patch.object(clamav, "signatures_status", return_value="ready"), \
             mock.patch.object(clamav, "clamd_active", return_value=True), \
             mock.patch.object(clamav, "freshclam_present", return_value=True):
            row = ClamAVRow()
        with mock.patch("ui.pages.page_security.clamav.update_definitions") as upd_mock, \
             mock.patch("ui.pages.page_security.threading.Thread") as thread_mock:
            row._on_update_definitions(None)
            upd_mock.assert_not_called()
            thread_mock.assert_called_once()
            self.assertTrue(thread_mock.return_value.start.called)

    def test_service_toggle_hidden_when_no_clamd_unit_detected(self):
        """Installed via clamscan only (no daemon unit) is a normal,
        supported state — Avvia/Ferma must not appear as a fake control."""
        import core.clamav as clamav
        from ui.pages.page_security import ClamAVRow
        with mock.patch.object(clamav, "is_installed", return_value=True), \
             mock.patch.object(clamav, "signatures_status", return_value="ready"), \
             mock.patch.object(clamav, "clamd_service_name", return_value=None), \
             mock.patch.object(clamav, "clamd_active", return_value=None):
            row = ClamAVRow()
        self.assertFalse(row._service_toggle_btn.get_visible())

    def test_service_toggle_shows_start_when_inactive_and_stop_when_active(self):
        import core.clamav as clamav
        from ui.pages.page_security import ClamAVRow
        with mock.patch.object(clamav, "is_installed", return_value=True), \
             mock.patch.object(clamav, "signatures_status", return_value="ready"), \
             mock.patch.object(clamav, "clamd_service_name", return_value="clamav-daemon"), \
             mock.patch.object(clamav, "clamd_active", return_value=False):
            row = ClamAVRow()
        self.assertTrue(row._service_toggle_btn.get_visible())
        self.assertEqual(row._service_toggle_btn.get_label(), T_it("clamav_service_start_btn"))

    def test_uninstall_button_only_visible_when_installed(self):
        import core.clamav as clamav
        from ui.pages.page_security import ClamAVRow
        with mock.patch.object(clamav, "is_installed", return_value=False), \
             mock.patch.object(clamav, "is_available_in_repos", return_value=True):
            row = ClamAVRow()
        self.assertFalse(row._uninstall_btn.get_visible())

    def test_uninstall_cancel_does_nothing(self):
        import core.clamav as clamav
        from ui.pages.page_security import ClamAVRow
        with mock.patch.object(clamav, "is_installed", return_value=True), \
             mock.patch.object(clamav, "signatures_status", return_value="ready"):
            row = ClamAVRow()
        with mock.patch("ui.pages.page_security.clamav.uninstall") as uninstall_mock:
            row._on_uninstall_confirm_response(None, "cancel")
            uninstall_mock.assert_not_called()

    def test_uninstall_confirm_never_blocks_the_gtk_thread(self):
        import core.clamav as clamav
        from ui.pages.page_security import ClamAVRow
        with mock.patch.object(clamav, "is_installed", return_value=True), \
             mock.patch.object(clamav, "signatures_status", return_value="ready"):
            row = ClamAVRow()
        with mock.patch("ui.pages.page_security.clamav.uninstall") as uninstall_mock, \
             mock.patch("ui.pages.page_security.threading.Thread") as thread_mock:
            row._on_uninstall_confirm_response(None, "confirm")
            uninstall_mock.assert_not_called()
            thread_mock.assert_called_once()
            self.assertTrue(thread_mock.return_value.start.called)

    def test_service_toggle_never_blocks_the_gtk_thread(self):
        import core.clamav as clamav
        from ui.pages.page_security import ClamAVRow
        # clamd_manageable()/clamd_active() are re-queried live at click
        # time (not just cached from construction), so the mocks must
        # still be active when _on_service_toggle_clicked runs.
        with mock.patch.object(clamav, "is_installed", return_value=True), \
             mock.patch.object(clamav, "signatures_status", return_value="ready"), \
             mock.patch.object(clamav, "clamd_service_name", return_value="clamav-daemon"), \
             mock.patch.object(clamav, "clamd_active", return_value=False), \
             mock.patch("ui.pages.page_security.clamav.clamd_start") as start_mock, \
             mock.patch("ui.pages.page_security.threading.Thread") as thread_mock:
            row = ClamAVRow()
            row._on_service_toggle_clicked(None)
            start_mock.assert_not_called()
            thread_mock.assert_called_once()
            self.assertTrue(thread_mock.return_value.start.called)

    def test_uninstall_never_logs_a_filesystem_path(self):
        """log_uninstall/log_service_toggle must never be handed a
        scanned/installed file path — same 'no personal paths' rule as
        scan logging."""
        import inspect
        import core.clamav as clamav
        sig = inspect.signature(clamav.log_uninstall)
        self.assertNotIn("path", sig.parameters)


def T_it(key):
    from core.i18n import T, set_lang
    set_lang("it")
    return T(key)


if __name__ == "__main__":
    unittest.main()
