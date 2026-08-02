"""
Tests for the "Chi sono"/"Guida"/"Crediti"/"Supporta il progetto" pages
added in this session, plus the license file and AppImage packaging.

GTK widget CONSTRUCTION needs a real display connection — attempting it
with none crashes the whole Python process with a segfault (verified:
this is not a catchable Python exception), unlike everything else in
this suite which is pure-Python and headless-safe. So this file is
split in two:
  - module-level constants, i18n keys, LICENSE file, packaging script
    content: plain Python, always run, no gi/Gtk construction at all;
  - actual page construction / image loading: gated behind
    _HAS_DISPLAY, skipped with a clear reason on a real headless CI
    box instead of taking the whole suite down.
"""
import os
import re
import unittest
from unittest import mock

from core.i18n import _strings, T
from core.uri_launcher import is_safe_https_url
import ui.window as window

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
_SKIP_REASON = "no DISPLAY/WAYLAND_DISPLAY — constructing a real GTK widget without one segfaults the process"


class MenuPresenceTests(unittest.TestCase):
    """"presenza delle quattro pagine nel menu" — checked at the class/
    PAGES-list level, which importing ui.window already builds without
    ever instantiating a page (construction only happens inside
    LinuxToolboxWindow.__init__, never at import time)."""

    def test_four_new_pages_registered(self):
        internal_names = {internal for _, _, internal, _ in window.PAGES}
        self.assertTrue({"author", "guide", "credits", "donate"}.issubset(internal_names))

    def test_new_pages_use_the_right_i18n_key_and_class(self):
        by_internal = {internal: (key, cls) for key, cls, internal, _ in window.PAGES}
        self.assertEqual(by_internal["author"], ("tab_author", window.AuthorPage))
        self.assertEqual(by_internal["guide"], ("tab_guide", window.GuidePage))
        self.assertEqual(by_internal["credits"], ("tab_credits", window.CreditsPage))
        self.assertEqual(by_internal["donate"], ("tab_donate", window.DonatePage))

    def test_no_duplicate_internal_names(self):
        internal_names = [internal for _, _, internal, _ in window.PAGES]
        self.assertEqual(len(internal_names), len(set(internal_names)))


class TranslationTests(unittest.TestCase):
    def test_all_new_keys_present_in_all_four_languages(self):
        keys = [
            "tab_author", "tab_guide", "tab_credits", "tab_donate",
            "author_bio", "author_guide_btn", "author_donate_btn", "author_credits_btn",
            "guide_intro_title", "guide_intro_body", "guide_open_online_btn",
            "guide_checkpoint_vs_snapshot_title", "guide_checkpoint_vs_snapshot_body",
            "credits_group_technologies", "credits_group_tools", "credits_disclaimer",
            "donate_intro", "donate_paypal_btn", "donate_iban_label", "donate_holder_label",
            "donate_swift_label", "donate_copy_iban_btn", "donate_copy_swift_btn",
            "donate_copied_iban", "donate_copied_swift", "donate_contact_btn",
            "donate_project_page_btn", "license_read_btn", "license_window_title",
        ]
        for key in keys:
            self.assertIn(key, _strings, f"missing i18n key: {key}")
            for lang in ("it", "en", "es", "fr"):
                self.assertIn(lang, _strings[key], f"{key} missing {lang}")
                self.assertTrue(_strings[key][lang], f"{key}/{lang} is empty")

    def test_tab_names_are_genuinely_translated_not_copy_pasted(self):
        for key in ("tab_author", "tab_guide", "tab_credits", "tab_donate"):
            values = _strings[key]
            # Not every language must differ from every other (short
            # words like "Crediti"/"Credits" legitimately look close),
            # but at least IT and EN must not be byte-identical for all
            # four — that would indicate a forgotten translation pass.
            distinct = len(set(values.values()))
            self.assertGreaterEqual(distinct, 2, f"{key} looks untranslated: {values}")

    def test_donate_intro_is_a_real_paragraph_not_a_placeholder(self):
        for lang in ("it", "en", "es", "fr"):
            self.assertGreater(len(_strings["donate_intro"][lang]), 100)


class HttpsLinkTests(unittest.TestCase):
    """"collegamenti HTTPS" + "blocco di URI non sicuri" — every
    external URL constant used by the new pages must itself be a safe
    https:// URL (core.uri_launcher's own unsafe-scheme rejection is
    covered exhaustively in tests/test_uri_launcher.py already)."""

    def test_every_external_url_constant_is_https(self):
        import ui.pages.page_donate as donate
        import ui.pages.page_guide as guide
        from core import release_config

        urls = [
            donate.PAYPAL_URL, donate.PROJECT_PAGE_URL, donate.CONTACT_URL,
            guide.GUIDE_URL, release_config.WEBSITE_URL, release_config.PROJECT_PAGE_URL,
            release_config.CONTACT_URL,
        ]
        for url in urls:
            self.assertTrue(is_safe_https_url(url), f"not a safe https URL: {url!r}")

    def test_paypal_url_matches_the_real_hosted_button_id(self):
        import ui.pages.page_donate as donate
        self.assertIn("hosted_button_id=7LCEUTKBTB6HW", donate.PAYPAL_URL)


class BankDetailsTests(unittest.TestCase):
    def test_iban_holder_swift_constants_are_exactly_as_provided(self):
        import ui.pages.page_donate as donate
        self.assertEqual(donate.IBAN, "IT16 S035 7601 6010 1000 9121 601")
        self.assertEqual(donate.ACCOUNT_HOLDER, "Mangano Gregorio")
        self.assertEqual(donate.SWIFT, "BBVAITM2XXX")

    def test_donate_page_module_never_imports_history_store(self):
        """"Non registrare IBAN o dati di donazione nella cronologia" —
        structurally enforced: the donate page module never imports the
        history store as a usable name, so it cannot log to it (its own
        docstring mentions the module name in prose, which is fine —
        this checks for an actual import/call, not the string anywhere)."""
        import ui.pages.page_donate as donate
        self.assertFalse(hasattr(donate, "history_store"))
        self.assertFalse(hasattr(donate, "record_operation"))
        self.assertFalse(hasattr(donate, "default_history_store"))


class LicenseFileTests(unittest.TestCase):
    def test_license_file_exists_at_repo_root(self):
        path = os.path.join(_REPO_ROOT, "LICENSE")
        self.assertTrue(os.path.isfile(path))

    def test_license_is_the_real_full_gplv3_text_not_a_stub(self):
        path = os.path.join(_REPO_ROOT, "LICENSE")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("GNU GENERAL PUBLIC LICENSE", content)
        self.assertIn("Version 3, 29 June 2007", content)
        self.assertIn("TERMS AND CONDITIONS", content)
        # The real text is 674 lines; anything drastically shorter
        # would be a hand-written summary, not the actual license.
        self.assertGreater(content.count("\n"), 500)

    def test_release_config_reports_the_chosen_license(self):
        from core import release_config
        self.assertEqual(release_config.LICENSE_NAME, "GNU General Public License v3.0 or later")
        self.assertEqual(release_config.LICENSE_SPDX, "GPL-3.0-or-later")

    def test_license_file_path_resolves_to_the_real_file(self):
        from core import release_config
        self.assertEqual(os.path.realpath(release_config.license_file_path()),
                          os.path.realpath(os.path.join(_REPO_ROOT, "LICENSE")))


class AppImagePackagingTests(unittest.TestCase):
    def test_build_script_copies_the_assets_directory(self):
        script_path = os.path.join(_REPO_ROOT, "packaging", "appimage", "build_appimage.sh")
        with open(script_path) as f:
            content = f.read()
        self.assertIn("assets", content)

    def test_new_image_files_exist_under_assets_images(self):
        for name in ("gregorio-profilo.jpg", "qr-donazione.png"):
            path = os.path.join(_REPO_ROOT, "assets", "images", name)
            self.assertTrue(os.path.isfile(path), f"missing {path}")
            self.assertGreater(os.path.getsize(path), 0)

    def test_build_script_also_copies_the_license_file(self):
        """Found via a real AppImage build in this session: the rsync
        file list only had main.py/core/backend/ui/assets — LICENSE
        was missing, so "Leggi la licenza" would have shown a "file
        not found" message inside the packaged app. Fixed in the same
        commit as this test."""
        script_path = os.path.join(_REPO_ROOT, "packaging", "appimage", "build_appimage.sh")
        with open(script_path) as f:
            content = f.read()
        self.assertIn("LICENSE", content)


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class ImageLoadingTests(unittest.TestCase):
    def test_existing_image_loads_as_a_real_picture(self):
        from ui.widgets import load_image_or_placeholder
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        path = os.path.join(_REPO_ROOT, "assets", "images", "gregorio-profilo.jpg")
        widget = load_image_or_placeholder(path, "avatar-default-symbolic", "author_photo_placeholder")
        self.assertIsInstance(widget, Gtk.Picture)
        self.assertIsNotNone(widget.get_paintable())

    def test_missing_image_falls_back_to_placeholder_without_crashing(self):
        from ui.widgets import load_image_or_placeholder
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        widget = load_image_or_placeholder("/definitely/not/a/real/file.jpg",
                                            "avatar-default-symbolic", "author_photo_placeholder")
        self.assertNotIsInstance(widget, Gtk.Picture)
        self.assertIsInstance(widget, Gtk.Box)


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class InternalNavigationTests(unittest.TestCase):
    def test_author_page_navigates_to_guide_and_donate_via_switch_to_page(self):
        from ui.pages.page_author import AuthorPage
        page = AuthorPage()
        fake_root = mock.Mock()
        with mock.patch.object(page, "get_root", return_value=fake_root):
            page._navigate("guide")
            page._navigate("donate")
        fake_root.switch_to_page.assert_has_calls([mock.call("guide"), mock.call("donate")])

    def test_navigation_without_a_root_does_not_crash(self):
        from ui.pages.page_author import AuthorPage
        page = AuthorPage()
        with mock.patch.object(page, "get_root", return_value=None):
            page._navigate("guide")  # must not raise


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class CopyToClipboardTests(unittest.TestCase):
    def test_copy_iban_sets_clipboard_and_shows_confirmation(self):
        from ui.pages.page_donate import DonatePage, IBAN
        page = DonatePage()
        btn = page._iban_row["btn"]
        fake_clipboard = mock.Mock()
        with mock.patch.object(page, "get_clipboard", return_value=fake_clipboard), \
             mock.patch("gi.repository.GLib.timeout_add_seconds"):
            btn.emit("clicked")
        fake_clipboard.set.assert_called_once_with(IBAN)
        self.assertEqual(btn.get_label(), T("donate_copied_iban"))

    def test_copy_swift_sets_clipboard(self):
        from ui.pages.page_donate import DonatePage, SWIFT
        page = DonatePage()
        btn = page._swift_row["btn"]
        fake_clipboard = mock.Mock()
        with mock.patch.object(page, "get_clipboard", return_value=fake_clipboard), \
             mock.patch("gi.repository.GLib.timeout_add_seconds"):
            btn.emit("clicked")
        fake_clipboard.set.assert_called_once_with(SWIFT)


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class NoBlockingCallTests(unittest.TestCase):
    """"nessun blocco del thread GTK" — every action on these four pages
    is local (clipboard, opening the default browser, reading a local
    image file) and therefore runs synchronously by design, unlike the
    install flows elsewhere in the app that explicitly use a background
    thread. This asserts the handlers actually return quickly instead
    of silently having grown a slow/network call some day."""

    def test_copy_handler_returns_quickly(self):
        import time
        from ui.pages.page_donate import DonatePage
        page = DonatePage()
        btn = page._holder_row["btn"]
        with mock.patch.object(page, "get_clipboard", return_value=mock.Mock()), \
             mock.patch("gi.repository.GLib.timeout_add_seconds"):
            start = time.monotonic()
            btn.emit("clicked")
            elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.5)

    def test_open_external_link_handler_does_not_spawn_a_thread(self):
        from ui.pages.page_guide import GuidePage
        page = GuidePage()
        with mock.patch("core.uri_launcher.Gio.AppInfo.launch_default_for_uri", return_value=True), \
             mock.patch("threading.Thread") as mock_thread:
            page._online_btn.emit("clicked")
        mock_thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
