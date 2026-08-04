"""
Tests for the Flatseal install fix (2026-08-05 block, Fase 3): the
real bug was "Installa Flatseal" always forcing --system regardless of
what was actually configured, so a user with only the personal
Flathub active always saw "Non è stato possibile installare Flatseal"
— even with Flatseal already installed. Covers every state named in
the spec: not_installed / installed_user / installed_system /
installed_both / flatpak_unavailable / flathub_user_unavailable /
flathub_system_unavailable / undetermined, plus the install flow's
error taxonomy (already installed, no connection, auth cancelled,
permission denied, verification failed).
"""
import unittest
from unittest import mock

from core.software_repo import flatpak_manager as fp


def _result(ok, stdout="", stderr="", returncode=0, cancelled=False, timed_out=False, error=""):
    m = mock.Mock()
    m.ok = ok
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    m.cancelled = cancelled
    m.timed_out = timed_out
    m.error = error
    m.technical_detail = lambda: f"{stdout}\n{stderr}"
    return m


class FlatpakAppStatusTests(unittest.TestCase):
    def test_flatpak_unavailable(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=False):
            status = fp.flatpak_app_status(fp.FLATSEAL_APP_ID)
        self.assertEqual(status.state, fp.APP_FLATPAK_UNAVAILABLE)
        self.assertFalse(status.installed)

    def test_not_installed_but_both_scopes_available(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "_flatpak_info_installed", return_value=False), \
             mock.patch.object(fp, "detect_flatpak_state", return_value=mock.Mock(
                 flathub_user=True, flathub_system=True)):
            status = fp.flatpak_app_status(fp.FLATSEAL_APP_ID)
        self.assertEqual(status.state, fp.APP_NOT_INSTALLED)

    def test_installed_user_only(self):
        def fake_info(app_id, scope, job=None):
            return scope == fp.SCOPE_USER
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "_flatpak_info_installed", side_effect=fake_info), \
             mock.patch.object(fp, "detect_flatpak_state", return_value=mock.Mock(
                 flathub_user=True, flathub_system=False)):
            status = fp.flatpak_app_status(fp.FLATSEAL_APP_ID)
        self.assertEqual(status.state, fp.APP_INSTALLED_USER)
        self.assertTrue(status.installed)

    def test_installed_system_only(self):
        def fake_info(app_id, scope, job=None):
            return scope == fp.SCOPE_SYSTEM
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "_flatpak_info_installed", side_effect=fake_info), \
             mock.patch.object(fp, "detect_flatpak_state", return_value=mock.Mock(
                 flathub_user=False, flathub_system=True)):
            status = fp.flatpak_app_status(fp.FLATSEAL_APP_ID)
        self.assertEqual(status.state, fp.APP_INSTALLED_SYSTEM)

    def test_installed_both(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "_flatpak_info_installed", return_value=True), \
             mock.patch.object(fp, "detect_flatpak_state", return_value=mock.Mock(
                 flathub_user=True, flathub_system=True)):
            status = fp.flatpak_app_status(fp.FLATSEAL_APP_ID)
        self.assertEqual(status.state, fp.APP_INSTALLED_BOTH)

    def test_flathub_user_unavailable_when_only_system_configured(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "_flatpak_info_installed", return_value=False), \
             mock.patch.object(fp, "detect_flatpak_state", return_value=mock.Mock(
                 flathub_user=False, flathub_system=True)):
            status = fp.flatpak_app_status(fp.FLATSEAL_APP_ID)
        self.assertEqual(status.state, fp.APP_FLATHUB_USER_UNAVAILABLE)

    def test_flathub_system_unavailable_when_only_user_configured(self):
        """The real machine's exact reported state."""
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "_flatpak_info_installed", return_value=False), \
             mock.patch.object(fp, "detect_flatpak_state", return_value=mock.Mock(
                 flathub_user=True, flathub_system=False)):
            status = fp.flatpak_app_status(fp.FLATSEAL_APP_ID)
        self.assertEqual(status.state, fp.APP_FLATHUB_SYSTEM_UNAVAILABLE)
        self.assertTrue(status.any_scope_available)

    def test_undetermined_when_flatpak_info_is_inconclusive(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "_flatpak_info_installed", return_value=None), \
             mock.patch.object(fp, "detect_flatpak_state", return_value=mock.Mock(
                 flathub_user=True, flathub_system=True)):
            status = fp.flatpak_app_status(fp.FLATSEAL_APP_ID)
        self.assertEqual(status.state, fp.APP_UNDETERMINED)


class FlatpakInfoInstalledParsingTests(unittest.TestCase):
    def test_ok_means_installed(self):
        with mock.patch.object(fp, "run_command_full", return_value=_result(True, stdout="Ref: ...")):
            self.assertTrue(fp._flatpak_info_installed("some.app", fp.SCOPE_USER))

    def test_not_installed_message_means_false(self):
        with mock.patch.object(fp, "run_command_full",
                                 return_value=_result(False, stderr="error: some.app not installed")):
            self.assertFalse(fp._flatpak_info_installed("some.app", fp.SCOPE_USER))

    def test_unexpected_error_is_undetermined(self):
        with mock.patch.object(fp, "run_command_full",
                                 return_value=_result(False, stderr="error: something else entirely")):
            self.assertIsNone(fp._flatpak_info_installed("some.app", fp.SCOPE_USER))

    def test_binary_missing_is_undetermined(self):
        with mock.patch.object(fp, "run_command_full", return_value=_result(False, error="not found")):
            self.assertIsNone(fp._flatpak_info_installed("some.app", fp.SCOPE_USER))


class InstallFlatpakAppTests(unittest.TestCase):
    def _status(self, **kwargs):
        defaults = dict(app_id=fp.FLATSEAL_APP_ID, flatpak_installed=True, determined=True,
                        installed_user=False, installed_system=False,
                        flathub_user_available=True, flathub_system_available=True)
        defaults.update(kwargs)
        return fp.FlatpakAppStatus(**defaults)

    def test_already_installed_is_never_reported_as_a_failure(self):
        """The exact bug: clicking Install on an already-installed
        Flatseal must never show 'installation failed'."""
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "flatpak_app_status", return_value=self._status(installed_user=True)), \
             mock.patch.object(fp, "run_pkexec_full") as pk_mock, \
             mock.patch.object(fp, "run_command_full") as run_mock:
            result = fp.install_flatpak_app(fp.FLATSEAL_APP_ID, fp.SCOPE_USER)
        self.assertTrue(result.ok)
        self.assertEqual(result.friendly_message, "app_already_installed")
        pk_mock.assert_not_called()
        run_mock.assert_not_called()

    def test_user_scope_never_calls_pkexec(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "flatpak_app_status", side_effect=[
                 self._status(), self._status(installed_user=True)]), \
             mock.patch.object(fp, "run_pkexec_full") as pk_mock, \
             mock.patch.object(fp, "run_command_full", return_value=_result(True)) as run_mock:
            result = fp.install_flatpak_app(fp.FLATSEAL_APP_ID, fp.SCOPE_USER)
        pk_mock.assert_not_called()
        run_mock.assert_called_once()
        self.assertTrue(result.ok)

    def test_system_scope_uses_pkexec(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "flatpak_app_status", side_effect=[
                 self._status(), self._status(installed_system=True)]), \
             mock.patch.object(fp, "run_pkexec_full", return_value=_result(True)) as pk_mock:
            result = fp.install_flatpak_app(fp.FLATSEAL_APP_ID, fp.SCOPE_SYSTEM)
        pk_mock.assert_called_once()
        self.assertTrue(result.ok)

    def test_requested_scope_has_no_flathub_refuses_without_a_command(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "flatpak_app_status", return_value=self._status(
                 flathub_system_available=False)), \
             mock.patch.object(fp, "run_pkexec_full") as pk_mock:
            result = fp.install_flatpak_app(fp.FLATSEAL_APP_ID, fp.SCOPE_SYSTEM)
        pk_mock.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "flatpak_err_flathub_not_configured")

    def test_verification_failure_is_distinct_from_install_failure(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "flatpak_app_status", side_effect=[
                 self._status(), self._status()]), \
             mock.patch.object(fp, "run_command_full", return_value=_result(True)):
            result = fp.install_flatpak_app(fp.FLATSEAL_APP_ID, fp.SCOPE_USER)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "flatpak_err_verification_failed")

    def test_install_flatseal_wraps_generic_messages_with_flatseal_naming(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "flatpak_app_status", return_value=self._status(installed_user=True)):
            result = fp.install_flatseal(fp.SCOPE_USER)
        self.assertEqual(result.friendly_message, "flatseal_already_installed")


class ClassifyFlatpakErrorTests(unittest.TestCase):
    def test_auth_cancelled_from_pkexec_dismiss_codes(self):
        r = _result(False, returncode=126)
        self.assertEqual(fp._classify_flatpak_error(r), "flatpak_err_auth_cancelled")

    def test_permission_denied(self):
        r = _result(False, stderr="Not authorized to perform this action")
        self.assertEqual(fp._classify_flatpak_error(r), "flatpak_err_permission_denied")

    def test_no_connection(self):
        r = _result(False, stderr="Unable to connect to dl.flathub.org")
        self.assertEqual(fp._classify_flatpak_error(r), "flatpak_err_no_connection")

    def test_package_not_found(self):
        r = _result(False, stderr="No remote refs found similar to 'foo'")
        self.assertEqual(fp._classify_flatpak_error(r), "flatpak_err_package_not_found")

    def test_operation_cancelled_via_job(self):
        job = mock.Mock(cancelled=True)
        r = _result(False)
        self.assertEqual(fp._classify_flatpak_error(r, job=job), "flatpak_err_operation_cancelled")

    def test_unclassified_failure_falls_back_to_generic(self):
        r = _result(False, stderr="some completely novel error string")
        self.assertEqual(fp._classify_flatpak_error(r), "flatpak_err_install_failed")


if __name__ == "__main__":
    unittest.main()
