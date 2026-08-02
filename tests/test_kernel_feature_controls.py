"""
Tests for kernel-expansion-v1 (Fase B, primo blocco): MGLRU, vm.page-
cluster, CPU frequency limits, disk read-ahead and TCP congestion
control. Same fake-/proc-and-/sys approach as test_kernel_features.py
and test_kernel_features_phase2.py — nothing here touches the real
machine except the PrivWriter tests, which use tempfile-based path
overrides exactly like the existing writer tests do. No real write
happens against the actual host in any test in this file.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.kernel_features.base import SupportStatus
from core.kernel_features.memory import MGLRUFeature, SwapReadaheadFeature, _classify_mglru
from core.kernel_features.cpu import CpuFrequencyLimitsFeature, compute_profile_range
from core.kernel_features.storage import ReadAheadFeature, list_real_disks
from core.kernel_features.network import TcpCongestionControlFeature
from core import priv_writer
from core.persistence.rollback_store import JsonStateStore
from core.persistence import sysctl_store


class FakeRootTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proc_root = os.path.join(self.tmp, "proc")
        self.sys_root = os.path.join(self.tmp, "sys")
        os.makedirs(self.proc_root)
        os.makedirs(self.sys_root)

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(root, name), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# ═══════════════════════════ MGLRU ═══════════════════════════════════
class MGLRUClassifyTests(unittest.TestCase):
    """Pure logic — the numeric/hex/y-n interpretation, no fixed 7."""

    def test_absent_value_treated_as_disabled(self):
        self.assertEqual(_classify_mglru(""), "disabled")

    def test_hex_all_bits_set_is_fully_active(self):
        self.assertEqual(_classify_mglru("0x0007"), "fully_active")

    def test_hex_more_bits_still_fully_active_never_hardcoded_to_7(self):
        # 0x000f = 0b1111 — a different bit count than 7, still a
        # contiguous run from bit 0, so still "fully active".
        self.assertEqual(_classify_mglru("0x000f"), "fully_active")

    def test_hex_with_gap_is_partially_active(self):
        # 0b101 (5) has bit 1 unset — a gap, not a clean 2^n-1 mask.
        self.assertEqual(_classify_mglru("0x0005"), "partially_active")

    def test_plain_decimal_disabled(self):
        self.assertEqual(_classify_mglru("0"), "disabled")

    def test_y_n_interpreted(self):
        self.assertEqual(_classify_mglru("y"), "fully_active")
        self.assertEqual(_classify_mglru("Y"), "fully_active")
        self.assertEqual(_classify_mglru("n"), "disabled")
        self.assertEqual(_classify_mglru("N"), "disabled")

    def test_garbage_value_never_crashes(self):
        self.assertEqual(_classify_mglru("not-a-number"), "disabled")


class MGLRUFeatureTests(FakeRootTestCase):
    def _make(self, content):
        d = os.path.join(self.sys_root, "kernel", "mm", "lru_gen")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "enabled"), "w") as f:
            f.write(content)

    def test_absent_is_unsupported_kernel(self):
        f = MGLRUFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_fully_active_hex(self):
        self._make("0x0007")
        f = MGLRUFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.SUPPORTED_RUNTIME)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertEqual(f.to_friendly(r.value), "kf_mglru_fully_active")

    def test_partially_active(self):
        self._make("0x0005")
        f = MGLRUFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertEqual(f.to_friendly(r.value), "kf_mglru_partially_active")

    def test_disabled(self):
        self._make("0x0000")
        f = MGLRUFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertEqual(f.to_friendly(r.value), "kf_mglru_disabled")

    def test_y_n_read_forms(self):
        self._make("y")
        f = MGLRUFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertEqual(f.to_friendly(r.value), "kf_mglru_fully_active")

    def test_offered_choices_are_y_and_n_never_a_raw_bitmask(self):
        self._make("0x0007")
        f = MGLRUFeature(sys_root=self.sys_root)
        self.assertEqual(f.read_available(), ["y", "n"])

    def test_validate_rejects_anything_but_y_n(self):
        f = MGLRUFeature(sys_root=self.sys_root)
        self.assertFalse(f.validate("0x0007"))
        self.assertFalse(f.validate("7"))
        self.assertTrue(f.validate("y"))
        self.assertTrue(f.validate("n"))

    def test_permission_denied(self):
        self._make("y")
        path = os.path.join(self.sys_root, "kernel", "mm", "lru_gen", "enabled")
        os.chmod(path, 0o000)
        f = MGLRUFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNAVAILABLE)


class PrivWriterMGLRUTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake_path = os.path.join(self.tmp, "enabled")
        with open(self.fake_path, "w") as f:
            f.write("n")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.MGLRUWriter()
        self.writer.PATH = self.fake_path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_rejected_for_anything_but_y_n(self):
        result = self.writer.apply_temporary("0x0007", None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_invalid_value")

    def test_apply_and_restore(self):
        result = self.writer.apply_temporary("y", None, False, self.state)
        self.assertTrue(result["ok"])
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "y")
        restored = self.writer.restore(None, None, False, self.state)
        self.assertTrue(restored["ok"])
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "n")

    def test_restore_without_prior_apply_fails_cleanly(self):
        result = self.writer.restore(None, None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_nothing_to_restore")


# ═══════════════════════ vm.page-cluster ══════════════════════════════
class SwapReadaheadFeatureTests(FakeRootTestCase):
    def _make(self, value):
        d = os.path.join(self.proc_root, "sys", "vm")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "page-cluster"), "w") as f:
            f.write(value)

    def test_absent_is_unsupported_kernel(self):
        f = SwapReadaheadFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_values_0_1_2_3_all_translate(self):
        for v in ("0", "1", "2", "3"):
            self._make(v)
            f = SwapReadaheadFeature(proc_root=self.proc_root)
            self.assertEqual(f.probe(), SupportStatus.SUPPORTED_PERSISTENT)
            r = f.read_current()
            self.assertTrue(r.ok)
            self.assertEqual(r.value, v)
            self.assertNotEqual(f.to_friendly(v), v)  # a real translation, not passthrough

    def test_validate_rejects_out_of_range(self):
        f = SwapReadaheadFeature(proc_root=self.proc_root)
        self.assertFalse(f.validate(4))
        self.assertFalse(f.validate(-1))
        self.assertFalse(f.validate("not-a-number"))
        self.assertTrue(f.validate(2))

    def test_never_forces_zero_just_because_zram_could_be_present(self):
        """Feature-level: nothing in this class reads ZRAM state or
        auto-picks 0 — it only ever reflects/validates what's asked."""
        self._make("2")
        f = SwapReadaheadFeature(proc_root=self.proc_root)
        r = f.read_current()
        self.assertEqual(r.value, "2")


class PrivWriterSwapReadaheadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake_path = os.path.join(self.tmp, "page-cluster")
        with open(self.fake_path, "w") as f:
            f.write("3")
        self.fake_sysctl = os.path.join(self.tmp, "90-mg-linux-toolbox.conf")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.SwapReadaheadWriter()
        self.writer.PATH = self.fake_path
        self._sysctl_patch = mock.patch.object(sysctl_store, "SYSCTL_FILE", self.fake_sysctl)
        self._sysctl_patch.start()

    def tearDown(self):
        self._sysctl_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_temporary_each_value(self):
        for v in (0, 1, 2, 3):
            result = self.writer.apply_temporary(v, None, False, self.state)
            self.assertTrue(result["ok"])
            with open(self.fake_path) as f:
                self.assertEqual(f.read().strip(), str(v))

    def test_apply_temporary_rejects_invalid(self):
        result = self.writer.apply_temporary(9, None, False, self.state)
        self.assertFalse(result["ok"])

    def test_apply_persistent_writes_sysctl_file(self):
        result = self.writer.apply_persistent(1, None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(sysctl_store.read_key("vm.page-cluster"), "1")

    def test_restore_removes_sysctl_entry(self):
        self.writer.apply_persistent(0, None, False, self.state)
        result = self.writer.restore(None, None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertIsNone(sysctl_store.read_key("vm.page-cluster"))
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "3")  # back to the original


# ═══════════════════════ CPU frequency limits ═════════════════════════
class CpuFrequencyLimitsFeatureTests(FakeRootTestCase):
    def _make_policy(self, n, min_khz, max_khz, hw_min=800000, hw_max=4800000):
        d = os.path.join(self.sys_root, "devices", "system", "cpu", "cpufreq", f"policy{n}")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "scaling_min_freq"), "w") as f:
            f.write(str(min_khz))
        with open(os.path.join(d, "scaling_max_freq"), "w") as f:
            f.write(str(max_khz))
        with open(os.path.join(d, "cpuinfo_min_freq"), "w") as f:
            f.write(str(hw_min))
        with open(os.path.join(d, "cpuinfo_max_freq"), "w") as f:
            f.write(str(hw_max))
        with open(os.path.join(d, "affected_cpus"), "w") as f:
            f.write(str(n))
        with open(os.path.join(d, "related_cpus"), "w") as f:
            f.write(str(n))
        return d

    def test_no_policies_is_unsupported_hardware(self):
        f = CpuFrequencyLimitsFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_HARDWARE)

    def test_single_policy(self):
        self._make_policy(0, 800000, 4800000)
        f = CpuFrequencyLimitsFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.SUPPORTED_RUNTIME)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertEqual(len(r.value["policies"]), 1)
        self.assertEqual(r.value["policies"][0]["min"], 800000)

    def test_multiple_policies_all_read(self):
        self._make_policy(0, 800000, 4800000)
        self._make_policy(1, 800000, 4800000)
        f = CpuFrequencyLimitsFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertEqual(len(r.value["policies"]), 2)

    def test_differing_hardware_limits_across_policies_take_intersection(self):
        # A big.LITTLE-style machine: one policy tops out lower than
        # the other. hw_bounds() must never claim the higher ceiling
        # applies to a policy that can't actually reach it.
        self._make_policy(0, 800000, 2400000, hw_min=800000, hw_max=2400000)
        self._make_policy(1, 800000, 4800000, hw_min=1000000, hw_max=4800000)
        f = CpuFrequencyLimitsFeature(sys_root=self.sys_root)
        r = f.read_current()
        bounds = f.hw_bounds(r.value["policies"])
        self.assertEqual(bounds, (1000000, 2400000))

    def test_validate_rejects_min_above_max(self):
        self._make_policy(0, 800000, 4800000)
        f = CpuFrequencyLimitsFeature(sys_root=self.sys_root)
        self.assertFalse(f.validate({"min": 3000000, "max": 2000000}))

    def test_validate_rejects_beyond_hardware_limits(self):
        self._make_policy(0, 800000, 4800000)
        f = CpuFrequencyLimitsFeature(sys_root=self.sys_root)
        self.assertFalse(f.validate({"min": 100000, "max": 2000000}))  # below hw_min
        self.assertFalse(f.validate({"min": 1000000, "max": 6000000}))  # above hw_max
        self.assertTrue(f.validate({"min": 1000000, "max": 2000000}))

    def test_compute_profile_range_never_exceeds_hw_bounds(self):
        for profile in ("power_saving", "balanced", "full_range"):
            lo, hi = compute_profile_range(profile, 800000, 4800000)
            self.assertGreaterEqual(lo, 800000)
            self.assertLessEqual(hi, 4800000)
            self.assertLessEqual(lo, hi)

    def test_compute_profile_range_unknown_profile_returns_none(self):
        self.assertIsNone(compute_profile_range("overclock", 800000, 4800000))


class PrivWriterCpuFrequencyLimitsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.CpuFrequencyLimitsWriter()
        self.dirs = []

    def _make_policy(self, n, min_khz, max_khz, hw_min=800000, hw_max=4800000):
        d = os.path.join(self.tmp, f"policy{n}")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "scaling_min_freq"), "w") as f:
            f.write(str(min_khz))
        with open(os.path.join(d, "scaling_max_freq"), "w") as f:
            f.write(str(max_khz))
        with open(os.path.join(d, "cpuinfo_min_freq"), "w") as f:
            f.write(str(hw_min))
        with open(os.path.join(d, "cpuinfo_max_freq"), "w") as f:
            f.write(str(hw_max))
        self.dirs.append(d)
        self.writer._dirs = lambda: self.dirs
        return d

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_policy_apply_and_restore(self):
        self._make_policy(0, 800000, 4800000)
        payload = json.dumps({"min": 1000000, "max": 3000000})
        result = self.writer.apply_temporary(payload, None, False, self.state)
        self.assertTrue(result["ok"])
        with open(os.path.join(self.dirs[0], "scaling_min_freq")) as f:
            self.assertEqual(f.read().strip(), "1000000")
        restored = self.writer.restore(None, None, False, self.state)
        self.assertTrue(restored["ok"])
        with open(os.path.join(self.dirs[0], "scaling_min_freq")) as f:
            self.assertEqual(f.read().strip(), "800000")

    def test_multiple_policies_all_written(self):
        self._make_policy(0, 800000, 4800000)
        self._make_policy(1, 800000, 4800000)
        payload = json.dumps({"min": 1200000, "max": 3600000})
        result = self.writer.apply_temporary(payload, None, False, self.state)
        self.assertTrue(result["ok"])
        for d in self.dirs:
            with open(os.path.join(d, "scaling_min_freq")) as f:
                self.assertEqual(f.read().strip(), "1200000")
            with open(os.path.join(d, "scaling_max_freq")) as f:
                self.assertEqual(f.read().strip(), "3600000")

    def test_rejects_min_above_max(self):
        self._make_policy(0, 800000, 4800000)
        payload = json.dumps({"min": 3000000, "max": 1000000})
        result = self.writer.apply_temporary(payload, None, False, self.state)
        self.assertFalse(result["ok"])

    def test_rejects_beyond_intersection_of_hardware_limits(self):
        self._make_policy(0, 800000, 2400000, hw_min=800000, hw_max=2400000)
        self._make_policy(1, 800000, 4800000, hw_min=1000000, hw_max=4800000)
        payload = json.dumps({"min": 800000, "max": 3000000})  # beyond policy0's hw_max
        result = self.writer.apply_temporary(payload, None, False, self.state)
        self.assertFalse(result["ok"])

    def test_partial_failure_rolls_back_every_policy_atomically(self):
        self._make_policy(0, 800000, 4800000)
        d1 = self._make_policy(1, 800000, 4800000)
        # Make the second policy's min file read-only so its write fails
        # partway through, after policy0 has already been written.
        os.chmod(os.path.join(d1, "scaling_min_freq"), 0o444)
        payload = json.dumps({"min": 1200000, "max": 3600000})
        try:
            result = self.writer.apply_temporary(payload, None, False, self.state)
            self.assertFalse(result["ok"])
            # policy0 must have been rolled back to its ORIGINAL value,
            # never left holding the new one while policy1 failed.
            with open(os.path.join(self.dirs[0], "scaling_min_freq")) as f:
                self.assertEqual(f.read().strip(), "800000")
        finally:
            os.chmod(os.path.join(d1, "scaling_min_freq"), 0o644)

    def test_restore_without_prior_apply_fails_cleanly(self):
        self._make_policy(0, 800000, 4800000)
        result = self.writer.restore(None, None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_nothing_to_restore")


# ═══════════════════════ Disk read-ahead ══════════════════════════════
class ReadAheadFeatureTests(FakeRootTestCase):
    def _make_disk(self, name, kb="128"):
        d = os.path.join(self.sys_root, "block", name, "queue")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "read_ahead_kb"), "w") as f:
            f.write(kb)
        # Also give it a "scheduler" file so it counts as a real disk for
        # list_real_disks(), the exact reuse this feature relies on.
        with open(os.path.join(d, "scheduler"), "w") as f:
            f.write("[none] mq-deadline")

    def test_multiple_real_disks_each_get_their_own_feature(self):
        self._make_disk("sda", "128")
        self._make_disk("nvme0n1", "512")
        disks = list_real_disks(sys_root=self.sys_root)
        names = {d for d, _friendly in disks}
        self.assertEqual(names, {"sda", "nvme0n1"})
        for device_id, _friendly in disks:
            f = ReadAheadFeature(device_id, sys_root=self.sys_root)
            self.assertEqual(f.probe(), SupportStatus.SUPPORTED_RUNTIME)

    def test_excluded_devices_never_listed(self):
        self._make_disk("loop0")
        self._make_disk("zram0")
        disks = list_real_disks(sys_root=self.sys_root)
        self.assertEqual(disks, [])

    def test_validate_rejects_out_of_range(self):
        self._make_disk("sda")
        f = ReadAheadFeature("sda", sys_root=self.sys_root)
        self.assertFalse(f.validate(-1))
        self.assertFalse(f.validate(999999))
        self.assertTrue(f.validate(256))

    def test_disk_removed_is_unsupported_kernel(self):
        f = ReadAheadFeature("nonexistent_disk", sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_to_friendly_matches_curated_presets(self):
        self._make_disk("sda", "1024")
        f = ReadAheadFeature("sda", sys_root=self.sys_root)
        r = f.read_current()
        self.assertEqual(f.to_friendly(r.value), "kf_read_ahead_high_sequential")

    def test_to_friendly_uncurated_value_is_custom(self):
        self._make_disk("sda", "777")
        f = ReadAheadFeature("sda", sys_root=self.sys_root)
        r = f.read_current()
        self.assertEqual(f.to_friendly(r.value), "kf_read_ahead_custom")


class PrivWriterReadAheadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.block_dir = os.path.join(self.tmp, "block", "faketest", "queue")
        os.makedirs(self.block_dir)
        self.fake_path = os.path.join(self.block_dir, "read_ahead_kb")
        with open(self.fake_path, "w") as f:
            f.write("128")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.ReadAheadWriter()
        self.writer._validate_device = lambda device_id: (
            self.fake_path if device_id == "faketest" and os.path.isfile(self.fake_path) else None
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_temporary_valid_value(self):
        result = self.writer.apply_temporary("512", "faketest", False, self.state)
        self.assertTrue(result["ok"])
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "512")

    def test_apply_temporary_rejects_invalid_value(self):
        result = self.writer.apply_temporary("not-a-number", "faketest", False, self.state)
        self.assertFalse(result["ok"])

    def test_disk_removed_reports_device_not_found(self):
        result = self.writer.apply_temporary("512", "some_other_disk", False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_device_not_found")

    def test_restore_back_to_original(self):
        self.writer.apply_temporary("1024", "faketest", False, self.state)
        result = self.writer.restore(None, "faketest", False, self.state)
        self.assertTrue(result["ok"])
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "128")


# ═══════════════════════ TCP congestion control ═══════════════════════
class TcpCongestionControlFeatureTests(FakeRootTestCase):
    def _make(self, current, available):
        d = os.path.join(self.proc_root, "sys", "net", "ipv4")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "tcp_congestion_control"), "w") as f:
            f.write(current)
        with open(os.path.join(d, "tcp_available_congestion_control"), "w") as f:
            f.write(available)

    def test_absent_is_unsupported_kernel(self):
        f = TcpCongestionControlFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_reno_only(self):
        self._make("reno", "reno")
        f = TcpCongestionControlFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.SUPPORTED_PERSISTENT)
        self.assertEqual(f.read_available(), ["reno"])
        r = f.read_current()
        self.assertEqual(r.value, "reno")
        self.assertEqual(f.description_key("reno"), "kf_tcp_cc_reno")

    def test_multiple_algorithms_only_kernel_reported_ones_shown(self):
        self._make("cubic", "reno cubic bbr")
        f = TcpCongestionControlFeature(proc_root=self.proc_root)
        self.assertEqual(f.read_available(), ["reno", "cubic", "bbr"])

    def test_bbr_absent_never_shown_or_promised(self):
        self._make("cubic", "reno cubic")
        f = TcpCongestionControlFeature(proc_root=self.proc_root)
        available = f.read_available()
        self.assertNotIn("bbr", available)
        self.assertFalse(f.validate("bbr"))

    def test_unknown_algorithm_gets_the_honest_no_description_text(self):
        self._make("cubic", "reno cubic totally_made_up_algo")
        f = TcpCongestionControlFeature(proc_root=self.proc_root)
        self.assertEqual(f.description_key("totally_made_up_algo"), "kf_tcp_cc_unknown_algorithm")

    def test_validate_rejects_unavailable_algorithm(self):
        self._make("cubic", "reno cubic")
        f = TcpCongestionControlFeature(proc_root=self.proc_root)
        self.assertFalse(f.validate("bbr"))
        self.assertTrue(f.validate("reno"))


class PrivWriterTcpCongestionControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake_path = os.path.join(self.tmp, "tcp_congestion_control")
        self.fake_available_path = os.path.join(self.tmp, "tcp_available_congestion_control")
        with open(self.fake_path, "w") as f:
            f.write("cubic")
        with open(self.fake_available_path, "w") as f:
            f.write("reno cubic")
        self.fake_sysctl = os.path.join(self.tmp, "90-mg-linux-toolbox.conf")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.TcpCongestionControlWriter()
        self.writer.PATH = self.fake_path
        self.writer.AVAILABLE_PATH = self.fake_available_path
        self._sysctl_patch = mock.patch.object(sysctl_store, "SYSCTL_FILE", self.fake_sysctl)
        self._sysctl_patch.start()

    def tearDown(self):
        self._sysctl_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_temporary_rejected_for_unavailable_algorithm(self):
        result = self.writer.apply_temporary("bbr", None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_invalid_value")

    def test_apply_temporary_valid_algorithm(self):
        result = self.writer.apply_temporary("reno", None, False, self.state)
        self.assertTrue(result["ok"])
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "reno")

    def test_apply_persistent_writes_sysctl_file(self):
        result = self.writer.apply_persistent("reno", None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(sysctl_store.read_key("net.ipv4.tcp_congestion_control"), "reno")

    def test_restore_removes_sysctl_entry_and_reverts_value(self):
        self.writer.apply_persistent("reno", None, False, self.state)
        result = self.writer.restore(None, None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertIsNone(sysctl_store.read_key("net.ipv4.tcp_congestion_control"))
        with open(self.fake_path) as f:
            self.assertEqual(f.read().strip(), "cubic")


# ═══════ Real machine: detection/read only, never a write ═════════════
class RealMachineDoesNotCrashTests(unittest.TestCase):
    """Exercises probe()/read_current() against the REAL host — never
    apply_temporary/apply_persistent/restore. Per spec: only detection,
    reading, display and navigation are allowed on the real computer in
    this phase; no real write happens in any test in this file."""

    def test_real_machine_mglru_probe_does_not_crash(self):
        MGLRUFeature().probe()

    def test_real_machine_swap_readahead_probe_does_not_crash(self):
        SwapReadaheadFeature().probe()

    def test_real_machine_cpu_frequency_limits_probe_does_not_crash(self):
        CpuFrequencyLimitsFeature().probe()

    def test_real_machine_tcp_congestion_control_probe_does_not_crash(self):
        TcpCongestionControlFeature().probe()

    def test_real_machine_read_ahead_probe_does_not_crash_for_every_real_disk(self):
        for device_id, _friendly in list_real_disks():
            ReadAheadFeature(device_id).probe()


if __name__ == "__main__":
    unittest.main()
