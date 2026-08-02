"""
Tests for the About window's non-interactive logic: diagnostic report
content (no personal data) and backup-file lookup. GTK is required to
construct the widget (it's a real Adw.Window), but nothing here performs
a real install/replace/network call.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw
Adw.init()

from ui.pages.page_about import AboutWindow
from core import version as app_version
from core.updater import installer
from core.updater.models import UpdateCheckResult, ReleaseInfo
from core.i18n import T


class DiagnosticReportTests(unittest.TestCase):
    def setUp(self):
        self.win = AboutWindow()

    def test_report_contains_version_and_no_personal_data(self):
        report = self.win._diagnostic_report_text()
        self.assertIn(app_version.APP_VERSION, report)
        self.assertIn("Kernel:", report)
        # No home directory path, no username-derived content expected.
        self.assertNotIn(os.path.expanduser("~"), report)


class BackupLookupTests(unittest.TestCase):
    def setUp(self):
        self.win = AboutWindow()
        self.tmp = tempfile.mkdtemp()
        self._orig_backup_dir = installer.BACKUP_DIR
        installer.BACKUP_DIR = self.tmp

    def tearDown(self):
        installer.BACKUP_DIR = self._orig_backup_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_backup_dir_returns_none(self):
        installer.BACKUP_DIR = os.path.join(self.tmp, "does-not-exist")
        self.assertIsNone(self.win._find_backup_file())

    def test_finds_versioned_backup(self):
        open(os.path.join(self.tmp, "previous-0.8.0.AppImage"), "wb").close()
        found = self.win._find_backup_file()
        self.assertEqual(os.path.basename(found), "previous-0.8.0.AppImage")

    def test_ignores_unrelated_files(self):
        open(os.path.join(self.tmp, "random.txt"), "wb").close()
        self.assertIsNone(self.win._find_backup_file())


class CheckUpdatesDisplayTests(unittest.TestCase):
    """The "messaggi semplici" flow — _on_check_updates_done() must
    show exactly one of three plain sentences, always in this order of
    priority: a real update, then any friendly_message (covers both a
    real GithubError and "no release for this channel yet"), then
    "you're up to date" as the true default."""

    def setUp(self):
        self.win = AboutWindow()

    def test_update_available_takes_priority(self):
        result = UpdateCheckResult(
            update_available=True,
            latest=ReleaseInfo(tag="v0.9.0-beta.2", version="0.9.0-beta.2", prerelease=True, channel="beta"),
            current_version="0.9.0-beta.1")
        self.win._on_check_updates_done(result)
        self.assertIn("0.9.0-beta.2", self.win._update_status_lbl.get_text())

    def test_no_releases_yet_shows_the_friendly_not_available_message(self):
        result = UpdateCheckResult(update_available=False, latest=None, current_version="0.9.0-beta.1",
                                    friendly_message="updater_no_releases_yet")
        self.win._on_check_updates_done(result)
        self.assertEqual(self.win._update_status_lbl.get_text(), T("updater_no_releases_yet"))

    def test_generic_error_shows_a_plain_sentence_not_the_exception(self):
        result = UpdateCheckResult(update_available=False, latest=None, current_version="0.9.0-beta.1",
                                    friendly_message="updater_check_failed",
                                    technical_detail="ConnectionResetError(104, 'Connection reset by peer')")
        self.win._on_check_updates_done(result)
        text = self.win._update_status_lbl.get_text()
        self.assertEqual(text, T("updater_check_failed"))
        self.assertNotIn("ConnectionResetError", text)

    def test_no_friendly_message_and_no_update_means_genuinely_up_to_date(self):
        result = UpdateCheckResult(update_available=False, latest=None, current_version="0.9.0-beta.1")
        self.win._on_check_updates_done(result)
        self.assertEqual(self.win._update_status_lbl.get_text(), T("updater_up_to_date"))

    def test_check_update_button_is_re_enabled_after_any_result(self):
        self.win._check_update_btn.set_sensitive(False)
        self.win._on_check_updates_done(UpdateCheckResult(update_available=False, current_version="0.9.0-beta.1"))
        self.assertTrue(self.win._check_update_btn.get_sensitive())


class CreditsNavigationTests(unittest.TestCase):
    def test_credits_click_navigates_and_closes(self):
        from unittest import mock
        # AboutWindow's transient_for requires a real Gtk.Window (see
        # the license-dialog bug fixed this session) — pass none, then
        # inject the mock into the plain-Python _main_window attribute
        # this test actually cares about, instead of the GObject property.
        win = AboutWindow(parent=None)
        win._main_window = mock.Mock()
        with mock.patch.object(win, "close") as mock_close:
            win._on_credits_clicked(None)
        win._main_window.switch_to_page.assert_called_once_with("credits")
        mock_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
