"""
Tests for the fix to silent pkexec/systemctl toggle failures (Wi-Fi,
Bluetooth, IPv6, Firewall, SSH, Samba, CUPS, TRIM, SMART, system
services): before this fix, a failed pkexec call (cancelled auth,
denied, command not found, timeout, an accepted-looking exit that the
system didn't actually apply) silently reverted the switch with zero
feedback — no message, no history entry. Now every one of these
backend functions returns an OpResult carrying enough detail for the
UI layer (ui.widgets.report_toggle_result) to show a plain-language
message, keep the raw detail behind "Mostra dettagli", and record the
failure to history — never leaving the control's real state and the
displayed state out of sync.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.all as B
from core.executor import CommandResult
from core.kernel_features.base import OpResult


def _cmd_result(ok, stderr="", error="", timed_out=False, cancelled=False):
    return CommandResult(cmd=["x"], ok=ok, returncode=0 if ok else 1,
                         stdout="", stderr=stderr, duration=0.1,
                         timed_out=timed_out, cancelled=cancelled, error=error)


class ServiceSetTests(unittest.TestCase):
    """_service_set backs ssh_set/samba_set/cups_set/trim_set/smart_set —
    fixing it once fixes all five."""

    def test_success_returns_ok_with_no_technical_detail(self):
        with mock.patch.object(B, "run_pkexec_full", return_value=_cmd_result(True)), \
             mock.patch.object(B, "_service_active", return_value=True):
            result = B._service_set("sshd", True)
        self.assertTrue(result.ok)
        self.assertTrue(result.value)
        self.assertEqual(result.technical_detail, "")

    def test_polkit_auth_denied_is_reported(self):
        denied = _cmd_result(False, stderr="Not authorized")
        with mock.patch.object(B, "run_pkexec_full", return_value=denied), \
             mock.patch.object(B, "_service_active", return_value=False):
            result = B._service_set("sshd", True)
        self.assertFalse(result.ok)
        self.assertFalse(result.value)  # real state re-read, not the requested one
        self.assertIn("Not authorized", result.technical_detail)

    def test_polkit_auth_cancelled_is_reported(self):
        cancelled = _cmd_result(False, error="cancelled", cancelled=True)
        with mock.patch.object(B, "run_pkexec_full", return_value=cancelled), \
             mock.patch.object(B, "_service_active", return_value=False):
            result = B._service_set("sshd", True)
        self.assertFalse(result.ok)

    def test_pkexec_timeout_is_reported(self):
        timed_out = _cmd_result(False, timed_out=True)
        with mock.patch.object(B, "run_pkexec_full", return_value=timed_out), \
             mock.patch.object(B, "_service_active", return_value=False):
            result = B._service_set("sshd", True)
        self.assertFalse(result.ok)

    def test_command_not_found_is_reported(self):
        missing = _cmd_result(False, error="[Errno 2] No such file or directory: 'systemctl'")
        with mock.patch.object(B, "run_pkexec_full", return_value=missing), \
             mock.patch.object(B, "_service_active", return_value=False):
            result = B._service_set("sshd", True)
        self.assertFalse(result.ok)
        self.assertIn("No such file", result.technical_detail)

    def test_accepted_exit_but_system_did_not_change_is_still_a_failure(self):
        """pkexec/systemctl can return exit 0 while the service still
        isn't in the requested state (masked unit, conflicting unit,
        etc.) — the real re-read after the call is what decides ok/not
        ok, never the raw exit code alone."""
        with mock.patch.object(B, "run_pkexec_full", return_value=_cmd_result(True)), \
             mock.patch.object(B, "_service_active", return_value=False):
            result = B._service_set("sshd", True)
        self.assertFalse(result.ok)
        self.assertFalse(result.value)


class NetworkToggleTests(unittest.TestCase):
    def test_bluetooth_set_failure_collects_every_failed_step(self):
        results = [_cmd_result(True), _cmd_result(False, stderr="unit not found"), _cmd_result(True)]
        with mock.patch.object(B, "run_pkexec_full", side_effect=results), \
             mock.patch.object(B, "bluetooth_active", return_value=False):
            result = B.bluetooth_set(True)
        self.assertFalse(result.ok)
        self.assertIn("unit not found", result.technical_detail)

    def test_ipv6_set_disabled_failure_reported(self):
        with mock.patch.object(B, "run_pkexec_full", return_value=_cmd_result(False, stderr="denied")), \
             mock.patch.object(B, "ipv6_disabled", return_value=False):
            result = B.ipv6_set_disabled(True)
        self.assertFalse(result.ok)
        self.assertIn("denied", result.technical_detail)

    def test_firewall_set_failure_reported(self):
        with mock.patch.object(B, "run_pkexec_full", return_value=_cmd_result(False, stderr="ufw: command not found")), \
             mock.patch.object(B, "firewall_active", return_value=False), \
             mock.patch.object(type(B.distro), "is_fedora", new_callable=mock.PropertyMock, return_value=False), \
             mock.patch.object(type(B.distro), "is_opensuse", new_callable=mock.PropertyMock, return_value=False):
            result = B.firewall_set(True)
        self.assertFalse(result.ok)
        self.assertIn("command not found", result.technical_detail)

    def test_wifi_set_failure_reported_without_pkexec(self):
        with mock.patch.object(B, "run_command_full", return_value=_cmd_result(False, stderr="nmcli: not found")), \
             mock.patch.object(B, "wifi_active", return_value=False):
            result = B.wifi_set(True)
        self.assertFalse(result.ok)
        self.assertIn("not found", result.technical_detail)

    def test_wifi_set_success(self):
        with mock.patch.object(B, "run_command_full", return_value=_cmd_result(True)), \
             mock.patch.object(B, "wifi_active", return_value=True):
            result = B.wifi_set(True)
        self.assertTrue(result.ok)


class DnsAndRootSshHelperResultTests(unittest.TestCase):
    """dns_dot_set / root_ssh_set_disabled go through the real
    privileged helper — their OpResult (including the more specific
    friendly_message, e.g. helper missing) must be propagated, not
    silently discarded in favour of a plain re-read."""

    def test_dns_dot_set_propagates_helper_missing_message(self):
        helper_result = OpResult(False, friendly_message="kf_err_helper_missing",
                                  technical_detail="state=missing")
        fake_writer = mock.Mock()
        fake_writer.execute.return_value = helper_result
        with mock.patch("core.persistence.priv_client.default_privileged_writer", return_value=fake_writer), \
             mock.patch.object(B, "dns_dot_active", return_value=False):
            result = B.dns_dot_set(True)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "kf_err_helper_missing")

    def test_dns_dot_set_success(self):
        helper_result = OpResult(True, value="yes")
        fake_writer = mock.Mock()
        fake_writer.execute.return_value = helper_result
        with mock.patch("core.persistence.priv_client.default_privileged_writer", return_value=fake_writer), \
             mock.patch.object(B, "dns_dot_active", return_value=True):
            result = B.dns_dot_set(True)
        self.assertTrue(result.ok)

    def test_root_ssh_set_disabled_propagates_helper_missing_message(self):
        helper_result = OpResult(False, friendly_message="kf_err_helper_missing")
        fake_writer = mock.Mock()
        fake_writer.execute.return_value = helper_result
        with mock.patch("core.persistence.priv_client.default_privileged_writer", return_value=fake_writer), \
             mock.patch.object(B, "root_ssh_disabled", return_value=False):
            result = B.root_ssh_set_disabled(True)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "kf_err_helper_missing")


class ReportToggleResultTests(unittest.TestCase):
    """ui.widgets.report_toggle_result: the shared UI-facing half of the
    fix — shown state always matches what the caller already re-read
    (this function never touches the switch itself), a failure shows a
    friendly message with details hidden until asked, a success clears
    any previous error, and every failure is recorded to history."""

    def setUp(self):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from ui.widgets import SwitchRow
        self.row = SwitchRow("wifi", True)

    def test_failure_shows_generic_message_and_hidden_details(self):
        from ui.widgets import report_toggle_result
        with mock.patch("core.persistence.history_store.record_operation") as mock_record:
            report_toggle_result(self.row, "network", "network.wifi", False, "raw stderr here")
        self.assertTrue(self.row._lbl_op_error.get_visible())
        self.assertTrue(self.row._op_details_btn.get_visible())
        self.assertFalse(self.row._lbl_op_details.get_visible())
        self.assertEqual(self.row._lbl_op_details.get_text(), "raw stderr here")
        mock_record.assert_called_once()
        args, kwargs = mock_record.call_args
        self.assertEqual(args[0], "network")
        self.assertEqual(args[1], "network.wifi")
        self.assertFalse(args[3])
        self.assertEqual(kwargs.get("technical_detail"), "raw stderr here")

    def test_details_button_toggles_visibility(self):
        from ui.widgets import report_toggle_result
        with mock.patch("core.persistence.history_store.record_operation"):
            report_toggle_result(self.row, "network", "network.wifi", False, "detail")
        self.row._op_details_btn.emit("clicked")
        self.assertTrue(self.row._lbl_op_details.get_visible())

    def test_helper_missing_uses_the_specific_friendly_key(self):
        from ui.widgets import report_toggle_result
        with mock.patch("core.persistence.history_store.record_operation"):
            report_toggle_result(self.row, "network", "dns.dot", False, "",
                                 friendly_key="kf_err_helper_missing")
        self.assertIn("componente amministrativo", self.row._lbl_op_error.get_text().lower())

    def test_success_clears_any_previous_error(self):
        from ui.widgets import report_toggle_result
        with mock.patch("core.persistence.history_store.record_operation"):
            report_toggle_result(self.row, "network", "network.wifi", False, "detail")
        self.assertTrue(self.row._lbl_op_error.get_visible())
        report_toggle_result(self.row, "network", "network.wifi", True, "")
        self.assertFalse(self.row._lbl_op_error.get_visible())
        self.assertFalse(self.row._op_details_btn.get_visible())

    def test_no_technical_detail_means_no_details_button(self):
        from ui.widgets import report_toggle_result
        with mock.patch("core.persistence.history_store.record_operation"):
            report_toggle_result(self.row, "gaming", "gaming.gamemode_install", False, "")
        self.assertTrue(self.row._lbl_op_error.get_visible())
        self.assertFalse(self.row._op_details_btn.get_visible())

    def test_broken_history_store_never_breaks_the_ui_feedback(self):
        from ui.widgets import report_toggle_result
        with mock.patch("core.persistence.history_store.record_operation",
                        side_effect=RuntimeError("disk full")):
            report_toggle_result(self.row, "network", "network.wifi", False, "detail")
        self.assertTrue(self.row._lbl_op_error.get_visible())


if __name__ == "__main__":
    unittest.main()
