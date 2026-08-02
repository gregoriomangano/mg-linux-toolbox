"""
Tests for core.kernel_features.security.SELinuxFeature and
core.priv_writer.SELinuxWriter — fake /sys tree + tempfile-based
/etc/selinux/config override, same convention as every other
PrivWriter test in this suite. Never runs a real `setenforce` or
touches the real /etc/selinux/config on the machine running the suite.
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from core.kernel_features.base import SupportStatus
from core.kernel_features.security import SELinuxFeature, ENFORCING, PERMISSIVE
from core import priv_writer
from core.persistence.rollback_store import JsonStateStore
from core.persistence import selinux_config_store


class FakeRootTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sys_root = os.path.join(self.tmp, "sys")
        os.makedirs(os.path.join(self.sys_root, "fs", "selinux"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_enforce(self, value: str):
        with open(os.path.join(self.sys_root, "fs", "selinux", "enforce"), "w") as f:
            f.write(value)


class SELinuxFeatureTests(FakeRootTestCase):
    def test_probe_unsupported_when_absent(self):
        os.rmdir(os.path.join(self.sys_root, "fs", "selinux"))
        feature = SELinuxFeature(sys_root=self.sys_root)
        self.assertEqual(feature.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_probe_supported_persistent_when_present(self):
        self._write_enforce("1")
        feature = SELinuxFeature(sys_root=self.sys_root)
        self.assertEqual(feature.probe(), SupportStatus.SUPPORTED_PERSISTENT)

    def test_read_current_enforcing(self):
        self._write_enforce("1")
        feature = SELinuxFeature(sys_root=self.sys_root)
        result = feature.read_current()
        self.assertTrue(result.ok)
        self.assertEqual(result.value, ENFORCING)

    def test_read_current_permissive(self):
        self._write_enforce("0")
        feature = SELinuxFeature(sys_root=self.sys_root)
        result = feature.read_current()
        self.assertEqual(result.value, PERMISSIVE)

    def test_read_available_is_always_both_modes(self):
        feature = SELinuxFeature(sys_root=self.sys_root)
        self.assertEqual(feature.read_available(), [ENFORCING, PERMISSIVE])

    def test_validate_rejects_disabled(self):
        feature = SELinuxFeature(sys_root=self.sys_root)
        self.assertFalse(feature.validate("disabled"))
        self.assertTrue(feature.validate(ENFORCING))
        self.assertTrue(feature.validate(PERMISSIVE))

    def test_to_friendly_returns_i18n_keys(self):
        feature = SELinuxFeature(sys_root=self.sys_root)
        self.assertEqual(feature.to_friendly(ENFORCING), "selinux_mode_enforcing")
        self.assertEqual(feature.to_friendly(PERMISSIVE), "selinux_mode_permissive")

    def test_apply_temporary_delegates_to_privileged_writer(self):
        self._write_enforce("0")
        fake_writer = mock.Mock()
        fake_writer.execute.return_value = "sentinel"
        feature = SELinuxFeature(sys_root=self.sys_root, privileged_writer=fake_writer)
        result = feature.apply_temporary(ENFORCING)
        fake_writer.execute.assert_called_once_with("selinux.mode", "apply_temporary", ENFORCING)
        self.assertEqual(result, "sentinel")


class SELinuxWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.enforce_path = os.path.join(self.tmp, "enforce")
        with open(self.enforce_path, "w") as f:
            f.write("0")
        self.config_path = os.path.join(self.tmp, "selinux_config")
        with open(self.config_path, "w") as f:
            f.write("SELINUX=permissive\nSELINUXTYPE=targeted\n")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.SELinuxWriter()
        self.writer.ENFORCE_PATH = self.enforce_path
        self.config_patch = mock.patch.object(selinux_config_store, "SELINUX_CONFIG_FILE", self.config_path)
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_setenforce(self, want_value):
        def run(cmd, **kwargs):
            with open(self.enforce_path, "w") as f:
                f.write(cmd[1])
            return mock.Mock(returncode=0, stderr="")
        return run

    def test_apply_temporary_switches_mode(self):
        with mock.patch("subprocess.run", side_effect=self._fake_setenforce(1)):
            result = self.writer.apply_temporary("enforcing", None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "enforcing")

    def test_apply_temporary_rejects_invalid_mode(self):
        result = self.writer.apply_temporary("disabled", None, False, self.state)
        self.assertFalse(result["ok"])

    def test_apply_persistent_also_writes_config_file(self):
        with mock.patch("subprocess.run", side_effect=self._fake_setenforce(1)):
            result = self.writer.apply_persistent("enforcing", None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(selinux_config_store.read_mode(), "enforcing")

    def test_setenforce_failure_reported_as_permission_error(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stderr="Permission denied")):
            result = self.writer.apply_temporary("enforcing", None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_permission")

    def test_restore_without_prior_apply_fails_cleanly(self):
        result = self.writer.restore(None, None, False, self.state)
        self.assertFalse(result["ok"])

    def test_restore_reverts_to_initial_mode(self):
        with mock.patch("subprocess.run", side_effect=self._fake_setenforce(1)):
            self.writer.apply_temporary("enforcing", None, False, self.state)
        with mock.patch("subprocess.run", side_effect=self._fake_setenforce(0)):
            restored = self.writer.restore(None, None, False, self.state)
        self.assertTrue(restored["ok"])
        self.assertEqual(restored["value"], "permissive")

    def test_restore_after_persistent_updates_config_file_too(self):
        with mock.patch("subprocess.run", side_effect=self._fake_setenforce(1)):
            self.writer.apply_persistent("enforcing", None, False, self.state)
        with mock.patch("subprocess.run", side_effect=self._fake_setenforce(0)):
            self.writer.restore(None, None, False, self.state)
        self.assertEqual(selinux_config_store.read_mode(), "permissive")

    def test_restore_detects_external_change(self):
        with mock.patch("subprocess.run", side_effect=self._fake_setenforce(1)):
            self.writer.apply_temporary("enforcing", None, False, self.state)
        with open(self.enforce_path, "w") as f:
            f.write("0")
        result = self.writer.restore(None, None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_external_change_detected")


class SELinuxConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, "config")
        self.patcher = mock.patch.object(selinux_config_store, "SELINUX_CONFIG_FILE", self.config_path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_rejects_disabled(self):
        with self.assertRaises(ValueError):
            selinux_config_store.write_mode("disabled")

    def test_write_then_read(self):
        with open(self.config_path, "w") as f:
            f.write("SELINUX=enforcing\nSELINUXTYPE=targeted\n")
        selinux_config_store.write_mode("permissive")
        self.assertEqual(selinux_config_store.read_mode(), "permissive")

    def test_selinuxtype_line_never_touched(self):
        with open(self.config_path, "w") as f:
            f.write("SELINUX=enforcing\nSELINUXTYPE=targeted\n")
        selinux_config_store.write_mode("permissive")
        with open(self.config_path) as f:
            content = f.read()
        self.assertIn("SELINUXTYPE=targeted", content)


if __name__ == "__main__":
    unittest.main()
