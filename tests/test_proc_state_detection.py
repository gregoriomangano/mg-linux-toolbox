"""
Regression test for the RC-validation openSUSE finding: ipv6_disabled()
and zram_active() used to shell out to bare `sysctl`/`swapon`. On any
distro that keeps /usr/sbin out of a regular user's $PATH (confirmed on
openSUSE Tumbleweed — /etc/profile only adds it for UID 0), those calls
silently failed with "command not found" and both functions defaulted
to False — showing the IPv6 switch as "enabled" even when it was
actually disabled, and hiding the ZRAM advisory note even when ZRAM was
active. Both now read straight from /proc, which every regular user can
do without needing either binary on PATH.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.all as B


class Ipv6DisabledProcReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "disable_ipv6")
        self._orig = B.IPV6_DISABLE_PATH
        B.IPV6_DISABLE_PATH = self.path
        self.addCleanup(setattr, B, "IPV6_DISABLE_PATH", self._orig)

    def _write(self, value):
        with open(self.path, "w") as f:
            f.write(value)

    def test_returns_true_when_kernel_reports_disabled(self):
        self._write("1\n")
        self.assertTrue(B.ipv6_disabled())

    def test_returns_false_when_kernel_reports_enabled(self):
        self._write("0\n")
        self.assertFalse(B.ipv6_disabled())

    def test_missing_proc_entry_does_not_raise(self):
        # simulates the "binary/path not found" failure mode this fix
        # replaces: must fail safe (False), never raise.
        self.assertFalse(B.ipv6_disabled())


class ZramActiveProcReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "swaps")
        self._orig = B.SWAPS_PATH
        B.SWAPS_PATH = self.path
        self.addCleanup(setattr, B, "SWAPS_PATH", self._orig)

    def _write(self, body):
        header = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
        with open(self.path, "w") as f:
            f.write(header + body)

    def test_true_when_a_zram_device_is_active(self):
        self._write("/dev/zram0                             partition\t8388604\t0\t100\n")
        self.assertTrue(B.zram_active())

    def test_false_when_only_a_disk_swap_is_active(self):
        self._write("/dev/sda2                               partition\t2097148\t0\t-2\n")
        self.assertFalse(B.zram_active())

    def test_false_when_no_swap_at_all(self):
        self._write("")
        self.assertFalse(B.zram_active())

    def test_missing_proc_entry_does_not_raise(self):
        self.assertFalse(B.zram_active())


if __name__ == "__main__":
    unittest.main()
