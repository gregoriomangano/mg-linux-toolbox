"""
Tests for core.persistence.tmpfiles_store — the systemd-tmpfiles-backed
persistence mechanism for a plain sysfs value with no /proc/sys
equivalent (currently only KSM's /sys/kernel/mm/ksm/run).
"""
import os
import tempfile
import unittest
from unittest import mock

from core.persistence import tmpfiles_store as ts

KSM_PATH = "/sys/kernel/mm/ksm/run"


class TmpfilesStoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.fake_file = os.path.join(self._tmpdir.name, "90-mg-linux-toolbox.conf")
        patcher = mock.patch.object(ts, "TMPFILES_FILE", self.fake_file)
        patcher.start()
        self.addCleanup(patcher.stop)


class WriteReadRemoveTests(TmpfilesStoreTestCase):
    def test_write_then_read(self):
        ts.write_value(KSM_PATH, "1")
        self.assertEqual(ts.read_value(KSM_PATH), "1")

    def test_write_rejects_unknown_path(self):
        with self.assertRaises(ValueError):
            ts.write_value("/sys/some/other/path", "1")

    def test_overwrite_replaces_not_duplicates(self):
        ts.write_value(KSM_PATH, "1")
        ts.write_value(KSM_PATH, "0")
        with open(self.fake_file) as f:
            content = f.read()
        self.assertEqual(content.count(KSM_PATH), 1)
        self.assertEqual(ts.read_value(KSM_PATH), "0")

    def test_read_missing_returns_none(self):
        self.assertIsNone(ts.read_value(KSM_PATH))

    def test_remove_deletes_file_when_nothing_left(self):
        ts.write_value(KSM_PATH, "1")
        ts.remove_value(KSM_PATH)
        self.assertFalse(os.path.exists(self.fake_file))

    def test_remove_missing_key_is_a_no_op(self):
        ts.remove_value(KSM_PATH)  # file doesn't exist yet
        self.assertFalse(os.path.exists(self.fake_file))

    def test_written_file_uses_real_tmpfiles_w_syntax(self):
        ts.write_value(KSM_PATH, "1")
        with open(self.fake_file) as f:
            content = f.read()
        self.assertIn(f"w {KSM_PATH} - - - - 1", content)


if __name__ == "__main__":
    unittest.main()
