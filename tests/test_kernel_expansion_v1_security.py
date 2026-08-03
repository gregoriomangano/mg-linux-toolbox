"""
Tests for kernel-expansion-v1 (Fase B, secondo blocco — Sicurezza
kernel): dmesg_restrict, kptr_restrict, ptrace_scope and the
protected_symlinks/hardlinks/fifos/regular group. Same fake-proc
approach as test_kernel_expansion_v1.py — nothing here touches the
real machine except the PrivWriter tests, which use tempfile-based
path overrides. No real write happens against the actual host in any
test in this file, and no automatic application is exercised (every
apply_temporary call here is the test explicitly asking for it, never
triggered on construction/probe/read).
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.kernel_features.base import SupportStatus
from core.kernel_features.security import (
    DmesgRestrictFeature, KptrRestrictFeature, PtraceScopeFeature, ProtectedPathsFeature,
)
from core import priv_writer
from core.persistence.rollback_store import JsonStateStore
from core.persistence import sysctl_store


class FakeRootTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proc_root = os.path.join(self.tmp, "proc")
        os.makedirs(self.proc_root)

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(root, name), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# ═══════════════════════ dmesg_restrict ═══════════════════════════════
class DmesgRestrictFeatureTests(FakeRootTestCase):
    def _make(self, value):
        d = os.path.join(self.proc_root, "sys", "kernel")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "dmesg_restrict"), "w") as f:
            f.write(value)

    def test_absent_is_unsupported_kernel(self):
        f = DmesgRestrictFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_values_0_and_1(self):
        for v in ("0", "1"):
            self._make(v)
            f = DmesgRestrictFeature(proc_root=self.proc_root)
            r = f.read_current()
            self.assertTrue(r.ok)
            self.assertEqual(r.value, v)

    def test_validate_rejects_anything_else(self):
        f = DmesgRestrictFeature(proc_root=self.proc_root)
        self.assertTrue(f.validate("0"))
        self.assertTrue(f.validate("1"))
        self.assertFalse(f.validate("2"))
        self.assertFalse(f.validate("y"))


# ═══════════════════════ kptr_restrict ════════════════════════════════
class KptrRestrictFeatureTests(FakeRootTestCase):
    def _make(self, value):
        d = os.path.join(self.proc_root, "sys", "kernel")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "kptr_restrict"), "w") as f:
            f.write(value)

    def test_absent_is_unsupported_kernel(self):
        f = KptrRestrictFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_values_0_1_2_all_valid_and_translated(self):
        for v in ("0", "1", "2"):
            self._make(v)
            f = KptrRestrictFeature(proc_root=self.proc_root)
            self.assertTrue(f.validate(v))
            r = f.read_current()
            self.assertEqual(r.value, v)

    def test_validate_rejects_3(self):
        f = KptrRestrictFeature(proc_root=self.proc_root)
        self.assertFalse(f.validate("3"))


# ═══════════════════════ ptrace_scope ═════════════════════════════════
class PtraceScopeFeatureTests(FakeRootTestCase):
    def _make(self, value):
        d = os.path.join(self.proc_root, "sys", "kernel", "yama")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ptrace_scope"), "w") as f:
            f.write(value)

    def test_absent_is_unsupported_kernel(self):
        f = PtraceScopeFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_values_0_1_2_accepted(self):
        self._make("0")
        f = PtraceScopeFeature(proc_root=self.proc_root)
        for v in ("0", "1", "2"):
            self.assertTrue(f.validate(v))

    def test_value_3_never_offered_or_accepted(self):
        """Per spec: value 3 ('no ptrace at all, needs reboot to undo')
        must never be exposed in simple mode."""
        self._make("0")
        f = PtraceScopeFeature(proc_root=self.proc_root)
        self.assertFalse(f.validate("3"))
        self.assertNotIn("3", f.read_available())
        self.assertNotIn(3, f.read_available())

    def test_read_available_is_exactly_three_values(self):
        f = PtraceScopeFeature(proc_root=self.proc_root)
        self.assertEqual(f.read_available(), ["0", "1", "2"])


class PrivWriterPtraceScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake_path = os.path.join(self.tmp, "ptrace_scope")
        with open(self.fake_path, "w") as f:
            f.write("1")
        self.fake_sysctl = os.path.join(self.tmp, "90-mg-linux-toolbox.conf")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.PtraceScopeWriter()
        self.writer.PATH = self.fake_path
        self._sysctl_patch = mock.patch.object(sysctl_store, "SYSCTL_FILE", self.fake_sysctl)
        self._sysctl_patch.start()

    def tearDown(self):
        self._sysctl_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_rejected_for_value_3_even_at_the_writer_level(self):
        """Defense in depth: rejected here even if something upstream
        of the privileged helper ever tried to send 3."""
        result = self.writer.apply_temporary("3", None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_invalid_value")
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "1")  # untouched

    def test_apply_and_restore(self):
        result = self.writer.apply_temporary("2", None, False, self.state)
        self.assertTrue(result["ok"])
        restored = self.writer.restore(None, None, False, self.state)
        self.assertTrue(restored["ok"])
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "1")


# ═══════════════════════ protected_paths group ════════════════════════
class ProtectedPathsFeatureTests(FakeRootTestCase):
    def _make_all(self, symlinks="1", hardlinks="1", fifos="1", regular="1"):
        d = os.path.join(self.proc_root, "sys", "fs")
        os.makedirs(d, exist_ok=True)
        values = {"protected_symlinks": symlinks, "protected_hardlinks": hardlinks,
                  "protected_fifos": fifos, "protected_regular": regular}
        for key, v in values.items():
            with open(os.path.join(d, key), "w") as f:
                f.write(v)
        return values

    def test_no_keys_present_is_unsupported_kernel(self):
        f = ProtectedPathsFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_all_four_on_is_full(self):
        self._make_all("1", "1", "1", "1")
        f = ProtectedPathsFeature(proc_root=self.proc_root)
        r = f.read_current()
        self.assertEqual(f.state(r.value), "full")

    def test_all_four_off_is_off(self):
        self._make_all("0", "0", "0", "0")
        f = ProtectedPathsFeature(proc_root=self.proc_root)
        r = f.read_current()
        self.assertEqual(f.state(r.value), "off")

    def test_mixed_is_partial(self):
        self._make_all("1", "0", "1", "0")
        f = ProtectedPathsFeature(proc_root=self.proc_root)
        r = f.read_current()
        self.assertEqual(f.state(r.value), "partial")

    def test_only_some_keys_present_still_read_correctly(self):
        """Only manages keys that really exist — never invents a write
        to a missing one."""
        d = os.path.join(self.proc_root, "sys", "fs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "protected_symlinks"), "w") as f:
            f.write("1")
        with open(os.path.join(d, "protected_hardlinks"), "w") as f:
            f.write("1")
        # fifos/regular deliberately absent — an older kernel.
        f = ProtectedPathsFeature(proc_root=self.proc_root)
        r = f.read_current()
        self.assertEqual(set(r.value.keys()), {"protected_symlinks", "protected_hardlinks"})
        self.assertEqual(f.state(r.value), "full")

    def test_validate_only_accepts_full_or_off(self):
        f = ProtectedPathsFeature(proc_root=self.proc_root)
        self.assertTrue(f.validate("full"))
        self.assertTrue(f.validate("off"))
        self.assertFalse(f.validate("partial"))
        self.assertFalse(f.validate("on"))


class PrivWriterProtectedPathsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.ProtectedPathsWriter()
        self.writer.BASE = self.tmp
        self.keys = ("protected_symlinks", "protected_hardlinks", "protected_fifos", "protected_regular")

    def _write_all(self, value):
        for key in self.keys:
            with open(os.path.join(self.tmp, key), "w") as f:
                f.write(value)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_enable_full_protection_writes_all_four_keys(self):
        self._write_all("0")
        result = self.writer.apply_temporary("full", None, False, self.state)
        self.assertTrue(result["ok"])
        for key in self.keys:
            with open(os.path.join(self.tmp, key)) as f:
                self.assertEqual(f.read().strip(), "1")

    def test_disable_writes_all_four_keys_to_zero(self):
        self._write_all("1")
        result = self.writer.apply_temporary("off", None, False, self.state)
        self.assertTrue(result["ok"])
        for key in self.keys:
            with open(os.path.join(self.tmp, key)) as f:
                self.assertEqual(f.read().strip(), "0")

    def test_already_in_requested_state_is_a_clean_no_op(self):
        self._write_all("1")
        result = self.writer.apply_temporary("full", None, False, self.state)
        self.assertTrue(result["ok"])
        # A record is still created (so restore/history stay consistent)
        rec = self.state.get(self.writer.KEY)
        self.assertIsNotNone(rec)

    def test_partial_failure_rolls_back_every_key_atomically(self):
        self._write_all("0")
        # Make one key's file read-only so its write fails partway
        # through, after some of the others have already been written.
        os.chmod(os.path.join(self.tmp, "protected_regular"), 0o444)
        try:
            result = self.writer.apply_temporary("full", None, False, self.state)
            self.assertFalse(result["ok"])
            # Every key must be back to "0" — never left half-applied.
            for key in self.keys:
                if key == "protected_regular":
                    continue
                with open(os.path.join(self.tmp, key)) as f:
                    self.assertEqual(f.read().strip(), "0")
        finally:
            os.chmod(os.path.join(self.tmp, "protected_regular"), 0o644)

    def test_restore_reverts_all_four_together(self):
        self._write_all("0")
        self.writer.apply_temporary("full", None, False, self.state)
        result = self.writer.restore(None, None, False, self.state)
        self.assertTrue(result["ok"])
        for key in self.keys:
            with open(os.path.join(self.tmp, key)) as f:
                self.assertEqual(f.read().strip(), "0")

    def test_grouped_as_a_single_state_record_not_four(self):
        self._write_all("0")
        self.writer.apply_temporary("full", None, False, self.state)
        all_records = self.state.all()
        matching = [k for k in all_records if k.startswith("security.protected_")]
        self.assertEqual(matching, ["security.protected_paths"])

    def test_only_manages_keys_that_really_exist(self):
        # Only 2 of the 4 files exist on this "kernel".
        for key in ("protected_symlinks", "protected_hardlinks"):
            with open(os.path.join(self.tmp, key), "w") as f:
                f.write("0")
        result = self.writer.apply_temporary("full", None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "protected_fifos")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "protected_regular")))


# ═══════ Real machine: detection/read only, never a write ═════════════
class RealMachineDoesNotCrashTests(unittest.TestCase):
    """Never calls apply_temporary/apply_persistent/restore against the
    real host — only probe()/read_current(), per spec."""

    def test_real_machine_dmesg_restrict_probe_does_not_crash(self):
        DmesgRestrictFeature().probe()

    def test_real_machine_kptr_restrict_probe_does_not_crash(self):
        KptrRestrictFeature().probe()

    def test_real_machine_ptrace_scope_probe_does_not_crash(self):
        PtraceScopeFeature().probe()

    def test_real_machine_protected_paths_probe_does_not_crash(self):
        ProtectedPathsFeature().probe()


if __name__ == "__main__":
    unittest.main()
