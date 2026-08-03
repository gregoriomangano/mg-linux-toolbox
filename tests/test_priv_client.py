"""
Tests for core.persistence.priv_client.PrivilegedWriter.execute() — the
one place that serializes a Python value into the string argv the
privileged helper (core/priv_writer.py) parses back. Found via a real
end-to-end check on this machine: dict payloads (battery thresholds,
audio power-save) were being serialized with str(value) (Python repr —
single quotes, True/False) instead of json.dumps(value), so every
writer that does json.loads(raw_value) failed with "bad payload" the
moment it was driven through the real client instead of called
directly with a hand-built JSON string, which is how every prior test
for those writers exercised them.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.persistence.priv_client import PrivilegedWriter
from core.persistence.priv_client import HelperStatus, HELPER_MISSING, _PRIV_WRITER_PATH


def _dev_resolver():
    # Same argv shape the resolver produces for a source checkout —
    # injected so these tests never depend on this machine's real
    # /usr/libexec state.
    return (["pkexec", "python3", _PRIV_WRITER_PATH],
            HelperStatus(HELPER_MISSING))
from core.persistence.history_store import HistoryStore


class _FakeCompletedProcess:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class SerializationTests(unittest.TestCase):
    def setUp(self):
        # Real HistoryStore backed by a temp file — execute() records to
        # it automatically on every call, and this keeps that off the
        # real ~/.local/share/mg-linux-toolbox/history.db during tests.
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.history = HistoryStore(path=os.path.join(self._tmpdir.name, "history.db"))
        self.writer = PrivilegedWriter(history_store=self.history, argv_resolver=_dev_resolver)

    @mock.patch("subprocess.run")
    def test_dict_value_serialized_as_valid_json(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(stdout='{"ok": true, "value": null}')
        self.writer.execute("battery.charge_threshold", "apply_temporary", {"start": 40, "end": 90})
        args = mock_run.call_args[0][0]
        raw_value_arg = args[5]
        import json
        parsed = json.loads(raw_value_arg)  # must not raise
        self.assertEqual(parsed, {"start": 40, "end": 90})

    @mock.patch("subprocess.run")
    def test_dict_with_bool_and_none_serialized_as_valid_json(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(stdout='{"ok": true, "value": null}')
        self.writer.execute("audio.power_save", "apply_temporary", {"seconds": 5, "controller": True})
        args = mock_run.call_args[0][0]
        import json
        parsed = json.loads(args[5])
        self.assertEqual(parsed, {"seconds": 5, "controller": True})

    @mock.patch("subprocess.run")
    def test_bool_value_serialized_as_python_str(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(stdout='{"ok": true, "value": true}')
        self.writer.execute("cpu.turbo_boost", "apply_temporary", True)
        args = mock_run.call_args[0][0]
        self.assertEqual(args[5], "True")

    @mock.patch("subprocess.run")
    def test_string_value_passed_through(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(stdout='{"ok": true, "value": "performance"}')
        self.writer.execute("cpu.governor", "apply_temporary", "performance")
        args = mock_run.call_args[0][0]
        self.assertEqual(args[5], "performance")

    @mock.patch("subprocess.run")
    def test_none_value_becomes_empty_string(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(stdout='{"ok": true, "value": null}')
        self.writer.execute("cpu.governor", "restore", None)
        args = mock_run.call_args[0][0]
        self.assertEqual(args[5], "")

    @mock.patch("subprocess.run")
    def test_result_parsed_from_stdout(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(stdout='{"ok": true, "value": 42, "friendly_message": "", "technical_detail": ""}')
        result = self.writer.execute("cpu.governor", "apply_temporary", "performance")
        self.assertTrue(result.ok)
        self.assertEqual(result.value, 42)

    @mock.patch("subprocess.run")
    def test_nonzero_exit_without_json_reports_technical_detail(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(stdout="", stderr="pkexec: not authorized", returncode=127)
        result = self.writer.execute("cpu.governor", "apply_temporary", "performance")
        self.assertFalse(result.ok)
        self.assertIn("not authorized", result.technical_detail)


class HistoryRecordingTests(unittest.TestCase):
    """execute() must record every call to the injected HistoryStore
    automatically — this is the single choke point new features (KVM,
    IOMMU, VFIO, AppArmor, SELinux, updates) get "Cronologia" for free
    from, without any per-feature integration work."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.history = HistoryStore(path=os.path.join(self._tmpdir.name, "history.db"))
        self.writer = PrivilegedWriter(history_store=self.history, argv_resolver=_dev_resolver)

    @mock.patch("subprocess.run")
    def test_successful_apply_is_recorded(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(stdout='{"ok": true, "value": "performance"}')
        self.writer.execute("cpu.governor", "apply_temporary", "performance")
        entries = self.history.query()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["feature_id"], "cpu.governor")
        self.assertEqual(entry["page"], "kernel")
        self.assertEqual(entry["entry_type"], "temporary_change")
        self.assertEqual(entry["result"], "ok")
        self.assertEqual(entry["new_value"], "performance")
        self.assertEqual(entry["verified_value"], "performance")
        self.assertTrue(entry["rollback_available"])

    @mock.patch("subprocess.run")
    def test_failed_apply_is_recorded_as_error(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(
            stdout='{"ok": false, "friendly_message": "kf_err_permission"}')
        self.writer.execute("cpu.turbo_boost", "apply_temporary", False)
        entry = self.history.query()[0]
        self.assertEqual(entry["entry_type"], "error")
        self.assertEqual(entry["result"], "failed")
        self.assertFalse(entry["rollback_available"])

    @mock.patch("subprocess.run")
    def test_reboot_required_overrides_entry_type(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(
            stdout='{"ok": true, "value": "amd_iommu=on", "reboot_required": true}')
        result = self.writer.execute("virt.iommu", "configure", "amd_iommu=on")
        self.assertTrue(result.reboot_required)
        entry = self.history.query()[0]
        self.assertEqual(entry["entry_type"], "reboot_required")
        self.assertTrue(entry["reboot_required"])

    @mock.patch("subprocess.run")
    def test_restore_action_is_not_rollback_eligible(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(stdout='{"ok": true, "value": "performance"}')
        self.writer.execute("cpu.governor", "restore", None)
        entry = self.history.query()[0]
        self.assertEqual(entry["entry_type"], "restore")
        self.assertFalse(entry["rollback_available"])

    @mock.patch("subprocess.run")
    def test_sensitive_looking_keys_are_redacted(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(stdout='{"ok": true, "value": null}')
        self.writer.execute("network.wifi_hotspot", "apply_temporary",
                             {"ssid": "MyHomeWifi", "password": "hunter2", "enabled": True})
        entry = self.history.query()[0]
        self.assertEqual(entry["new_value"]["ssid"], "***")
        self.assertEqual(entry["new_value"]["password"], "***")
        self.assertEqual(entry["new_value"]["enabled"], True)

    @mock.patch("subprocess.run")
    def test_record_history_false_skips_logging(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(stdout='{"ok": true, "value": "performance"}')
        self.writer.execute("cpu.governor", "apply_temporary", "performance", record_history=False)
        self.assertEqual(self.history.query(), [])

    @mock.patch("subprocess.run")
    def test_a_broken_history_store_never_breaks_the_real_result(self, mock_run):
        mock_run.return_value = _FakeCompletedProcess(stdout='{"ok": true, "value": "performance"}')
        broken_writer = PrivilegedWriter(argv_resolver=_dev_resolver)
        broken_writer._history_store = mock.Mock()
        broken_writer._history_store.record.side_effect = RuntimeError("disk full")
        result = broken_writer.execute("cpu.governor", "apply_temporary", "performance")
        self.assertTrue(result.ok)
        self.assertEqual(result.value, "performance")


if __name__ == "__main__":
    unittest.main()
