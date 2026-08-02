"""
Tests for core.snapshot_tools — read-only detection of Timeshift,
Snapper, Btrfs-as-root, transactional-update, and rpm-ostree. Never
installs anything; every check here is mocked since we can't assume
any of these tools exist on the machine running the test suite.
"""
import os
import tempfile
import unittest
from unittest import mock

from core import snapshot_tools as st


class TimeshiftTests(unittest.TestCase):
    def test_not_installed(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(st._timeshift_status(), {"tool": st.TIMESHIFT, "installed": False, "configured": False})

    def test_installed_but_not_configured(self):
        with mock.patch("shutil.which", return_value="/usr/bin/timeshift"), \
             mock.patch("os.path.isfile", return_value=False):
            status = st._timeshift_status()
        self.assertTrue(status["installed"])
        self.assertFalse(status["configured"])

    def test_installed_and_configured(self):
        with mock.patch("shutil.which", return_value="/usr/bin/timeshift"), \
             mock.patch("os.path.isfile", side_effect=lambda p: p == "/etc/timeshift/timeshift.json"):
            status = st._timeshift_status()
        self.assertTrue(status["configured"])

    def test_list_snapshots_empty_when_not_installed(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(st.list_timeshift_snapshots(), [])

    def test_list_snapshots_parses_real_output(self):
        sample = "Num\tName\n---\t----\n0\t2026-01-01_00-00-00\tO\n"
        with mock.patch("shutil.which", return_value="/usr/bin/timeshift"), \
             mock.patch.object(st, "run_command", return_value=(True, sample, "")):
            snaps = st.list_timeshift_snapshots()
        self.assertEqual(len(snaps), 1)


class SnapperTests(unittest.TestCase):
    def test_not_installed(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(st._snapper_status()["installed"])

    def test_configured_when_configs_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "root"))
            with mock.patch("shutil.which", return_value="/usr/bin/snapper"), \
                 mock.patch.object(st, "_snapper_status", wraps=st._snapper_status):
                pass
            # Exercise the real function against a real temp dir by
            # patching the hardcoded path indirectly via os.listdir/isdir.
            with mock.patch("shutil.which", return_value="/usr/bin/snapper"), \
                 mock.patch("os.path.isdir", side_effect=lambda p: p == "/etc/snapper/configs"), \
                 mock.patch("os.listdir", return_value=["root"]):
                status = st._snapper_status()
        self.assertTrue(status["configured"])

    def test_list_snapshots_skips_header_lines(self):
        sample = "Config | Snapshot\n-------|--------\n0 | pre-update\n1 | post-update\n"
        with mock.patch("shutil.which", return_value="/usr/bin/snapper"), \
             mock.patch.object(st, "run_command", return_value=(True, sample, "")):
            snaps = st.list_snapper_snapshots()
        self.assertEqual(len(snaps), 2)


class BtrfsTests(unittest.TestCase):
    def test_root_on_ext4_is_not_btrfs(self):
        with tempfile.TemporaryDirectory() as proc_root:
            with open(os.path.join(proc_root, "mounts"), "w") as f:
                f.write("/dev/sda2 / ext4 rw 0 0\n")
            status = st._btrfs_status(proc_root)
        self.assertFalse(status["configured"])

    def test_root_on_btrfs_detected(self):
        with tempfile.TemporaryDirectory() as proc_root:
            with open(os.path.join(proc_root, "mounts"), "w") as f:
                f.write("/dev/sda2 / btrfs rw,subvol=@ 0 0\n")
            with mock.patch("shutil.which", return_value="/usr/bin/btrfs"):
                status = st._btrfs_status(proc_root)
        self.assertTrue(status["configured"])
        self.assertTrue(status["installed"])


class TransactionalUpdateAndRpmOstreeTests(unittest.TestCase):
    def test_transactional_update_not_installed(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(st._transactional_update_status()["installed"])

    def test_transactional_update_installed_implies_configured(self):
        with mock.patch("shutil.which", return_value="/usr/sbin/transactional-update"):
            status = st._transactional_update_status()
        self.assertTrue(status["installed"])
        self.assertTrue(status["configured"])

    def test_rpm_ostree_not_installed(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(st._rpm_ostree_status()["installed"])

    def test_rpm_ostree_installed_and_status_succeeds(self):
        with mock.patch("shutil.which", return_value="/usr/bin/rpm-ostree"), \
             mock.patch.object(st, "run_command", return_value=(True, "State: idle", "")):
            status = st._rpm_ostree_status()
        self.assertTrue(status["configured"])


class DetectToolsTests(unittest.TestCase):
    def test_returns_one_entry_per_tool(self):
        with mock.patch("shutil.which", return_value=None):
            tools = st.detect_tools()
        names = {t["tool"] for t in tools}
        self.assertEqual(names, {st.TIMESHIFT, st.SNAPPER, st.BTRFS, st.TRANSACTIONAL_UPDATE, st.RPM_OSTREE})
        self.assertTrue(all(not t["installed"] for t in tools))


if __name__ == "__main__":
    unittest.main()
