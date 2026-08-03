"""
Automated tests using fake /proc and /sys trees — none of these touch the
real machine. Run with:

    python3 -m unittest discover -s tests -v

Covers the scenarios required by the spec: feature present/absent,
valid/invalid values, permission denied, bracketed current-value parsing,
multiple disks, excluded pseudo-devices, device removed mid-operation,
rollback, and external-change detection.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.kernel_features.base import SupportStatus
from core.kernel_features.monitoring import PSIFeature, PSIHysteresis
from core.kernel_features.memory import SwappinessFeature
from core.kernel_features.storage import IOSchedulerFeature, list_real_disks
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
        # in case a permission test forgot to restore perms, force it so
        # rmtree can actually clean up
        for root, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(root, name), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# ── PSI ───────────────────────────────────────────────────────────────
class PSITests(FakeRootTestCase):
    def _write_pressure(self, resource, content):
        d = os.path.join(self.proc_root, "pressure")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, resource), "w") as f:
            f.write(content)

    def test_probe_unsupported_kernel_when_missing(self):
        f = PSIFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_probe_supported_when_present(self):
        self._write_pressure("cpu", "some avg10=0.00 avg60=0.00 avg300=0.00 total=1\n")
        f = PSIFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.SUPPORTED_READ_ONLY)

    def test_probe_unavailable_on_permission_denied(self):
        self._write_pressure("cpu", "some avg10=0.00 avg60=0.00 avg300=0.00 total=1\n")
        path = os.path.join(self.proc_root, "pressure", "cpu")
        os.chmod(path, 0o000)
        f = PSIFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.UNAVAILABLE)

    def test_read_current_parses_all_resources_and_buckets(self):
        self._write_pressure("cpu", "some avg10=0.50 avg60=0.10 avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        self._write_pressure("memory", "some avg10=0.00 avg60=0.00 avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=1\n")
        self._write_pressure("io", "some avg10=15.00 avg60=10.00 avg300=5.00 total=1\nfull avg10=15.00 avg60=10.00 avg300=5.00 total=1\n")
        f = PSIFeature(proc_root=self.proc_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertAlmostEqual(r.value["cpu"]["some"]["avg10"], 0.5)
        self.assertEqual(f.to_friendly(r.value["cpu"]), "low")
        self.assertEqual(f.to_friendly(r.value["io"]), "high")

    def test_read_current_fails_cleanly_if_one_resource_missing(self):
        self._write_pressure("cpu", "some avg10=0.00 avg60=0.00 avg300=0.00 total=1\n")
        # memory/io missing entirely
        f = PSIFeature(proc_root=self.proc_root)
        r = f.read_current()
        self.assertFalse(r.ok)
        self.assertEqual(r.friendly_message, "kf_unsupported_kernel")


# ── PSI hysteresis (2026-08-03 fix) ─────────────────────────────────────
# Reproduces the real spike from the bug report: some avg10=77.52
# avg60=72.65 avg300=61.16, later avg10=0.00 avg60=0.16 avg300=18.27 —
# the Panoramica kept showing red purely because it never refreshed at
# all, and even once it did, a naive re-read would still need to not be
# fooled by a lingering avg300. PSIHysteresis never reads avg300, and
# needs two consecutive samples in each direction before it flips.
class PSIHysteresisTests(unittest.TestCase):
    def test_all_values_low(self):
        h = PSIHysteresis()
        self.assertEqual(h.update(0.0, 0.0), "low")
        self.assertFalse(h.critical)

    def test_high_avg10_but_isolated_sample_does_not_turn_critical(self):
        h = PSIHysteresis()
        bucket = h.update(77.52, 72.65)
        self.assertFalse(h.critical)
        self.assertEqual(bucket, "low")  # every visible transition needs confirmation

    def test_two_consecutive_high_samples_enter_critical(self):
        h = PSIHysteresis()
        h.update(77.52, 72.65)
        bucket = h.update(77.52, 72.65)
        self.assertTrue(h.critical)
        self.assertEqual(bucket, "high")

    def test_avg10_back_to_zero_alone_does_not_exit_critical(self):
        h = PSIHysteresis()
        h.update(77.52, 72.65)
        h.update(77.52, 72.65)
        self.assertTrue(h.critical)
        # avg10 has dropped, but avg60 is still elevated — the
        # situation "continues" per avg60, so it must stay critical.
        bucket = h.update(0.0, 72.62)
        self.assertTrue(h.critical)
        self.assertEqual(bucket, "high")

    def test_avg60_back_to_low_is_what_actually_exits_critical(self):
        h = PSIHysteresis()
        h.update(77.52, 72.65)
        h.update(77.52, 72.65)
        h.update(0.00, 72.62)   # avg60 still confirms — stays critical
        h.update(0.00, 0.16)    # 1st sample with both low
        self.assertTrue(h.critical)  # still needs a 2nd confirming sample
        bucket = h.update(0.00, 0.16)  # 2nd consecutive low sample
        self.assertFalse(h.critical)
        self.assertEqual(bucket, "low")

    def test_avg300_still_high_never_keeps_it_critical(self):
        # Exact numbers from the bug report: avg300=18.27 stays well
        # above THRESHOLD_HIGH, but PSIHysteresis.update() doesn't even
        # accept avg300 as a parameter — it structurally cannot see it.
        h = PSIHysteresis()
        h.update(77.52, 72.65)
        h.update(77.52, 72.65)
        h.update(0.00, 0.16)
        bucket = h.update(0.00, 0.16)
        self.assertFalse(h.critical)
        self.assertEqual(bucket, "low")

    def test_single_low_sample_after_critical_does_not_exit_early(self):
        h = PSIHysteresis()
        h.update(77.52, 72.65)
        h.update(77.52, 72.65)
        bucket = h.update(0.0, 0.0)  # only one low sample so far
        self.assertTrue(h.critical)
        self.assertEqual(bucket, "high")

    def test_moderate_transition_also_requires_two_samples(self):
        h = PSIHysteresis()
        self.assertEqual(h.update(2.0, 1.5), "low")
        self.assertEqual(h.update(2.0, 1.5), "moderate")
        self.assertEqual(h.update(0.5, 0.4), "moderate")
        self.assertEqual(h.update(0.5, 0.4), "low")

    def test_avg60_must_confirm_a_high_avg10_before_red(self):
        h = PSIHysteresis()
        self.assertEqual(h.update(77.52, 5.0), "low")
        self.assertEqual(h.update(77.52, 5.0), "moderate")
        self.assertFalse(h.critical)


# ── Swappiness (read-side, via KernelFeature) ──────────────────────────
class SwappinessReadTests(FakeRootTestCase):
    def _write(self, value):
        d = os.path.join(self.proc_root, "sys", "vm")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "swappiness"), "w") as f:
            f.write(str(value))

    def test_probe_missing(self):
        f = SwappinessFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_probe_present(self):
        self._write(60)
        f = SwappinessFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.SUPPORTED_PERSISTENT)

    def test_read_and_bucket_presets(self):
        self._write(60)
        f = SwappinessFeature(proc_root=self.proc_root)
        r = f.read_current()
        self.assertEqual(r.value, 60)
        self.assertEqual(f.to_friendly(60), "kf_swappiness_preset_balanced")
        self.assertEqual(f.to_friendly(10), "kf_swappiness_preset_rare")
        self.assertEqual(f.to_friendly(100), "kf_swappiness_preset_low_ram")
        self.assertEqual(f.to_friendly(37), "kf_swappiness_preset_custom")

    def test_validate_range_and_type(self):
        f = SwappinessFeature(proc_root=self.proc_root)
        self.assertTrue(f.validate(0))
        self.assertTrue(f.validate(200))
        self.assertFalse(f.validate(201))
        self.assertFalse(f.validate(-1))
        self.assertFalse(f.validate("abc"))
        self.assertFalse(f.validate(None))

    def test_permission_denied(self):
        self._write(60)
        path = os.path.join(self.proc_root, "sys", "vm", "swappiness")
        os.chmod(path, 0o000)
        f = SwappinessFeature(proc_root=self.proc_root)
        self.assertEqual(f.probe(), SupportStatus.UNAVAILABLE)


# ── I/O scheduler: disk discovery/exclusion ────────────────────────────
class DiskListingTests(FakeRootTestCase):
    def _make_block_dev(self, name, with_scheduler=True, model=None, rotational="0"):
        d = os.path.join(self.sys_root, "block", name, "queue")
        os.makedirs(d, exist_ok=True)
        if with_scheduler:
            with open(os.path.join(d, "scheduler"), "w") as f:
                f.write("none mq-deadline [kyber]\n")
        with open(os.path.join(d, "rotational"), "w") as f:
            f.write(rotational)
        if model:
            dev_dir = os.path.join(self.sys_root, "block", name, "device")
            os.makedirs(dev_dir, exist_ok=True)
            with open(os.path.join(dev_dir, "model"), "w") as f:
                f.write(model)

    def test_excludes_loop_ram_zram_devicemapper(self):
        self._make_block_dev("loop0")
        self._make_block_dev("ram0")
        self._make_block_dev("zram0")
        self._make_block_dev("dm-0")
        self._make_block_dev("nvme0n1", model="Test SSD 500")
        names = [d for d, _ in list_real_disks(sys_root=self.sys_root)]
        self.assertEqual(names, ["nvme0n1"])

    def test_excludes_devices_without_scheduler_file(self):
        self._make_block_dev("sda", with_scheduler=False)
        self.assertEqual(list_real_disks(sys_root=self.sys_root), [])

    def test_friendly_name_uses_model_and_kind(self):
        self._make_block_dev("nvme0n1", model="Samsung 970 EVO")
        self._make_block_dev("sdb", model="Old Spinner", rotational="1")
        disks = dict(list_real_disks(sys_root=self.sys_root))
        self.assertEqual(disks["nvme0n1"], "NVMe Samsung 970 EVO")
        self.assertEqual(disks["sdb"], "HDD Old Spinner")


class IOSchedulerFeatureReadTests(FakeRootTestCase):
    def test_parses_bracketed_current_value(self):
        d = os.path.join(self.sys_root, "block", "nvme0n1", "queue")
        os.makedirs(d)
        with open(os.path.join(d, "scheduler"), "w") as f:
            f.write("none [mq-deadline] kyber\n")
        f = IOSchedulerFeature("nvme0n1", sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertEqual(r.value["current"], "mq-deadline")
        self.assertEqual(r.value["available"], ["none", "mq-deadline", "kyber"])

    def test_device_missing(self):
        f = IOSchedulerFeature("doesnotexist", sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)


# ── Privileged writer logic (no real pkexec — the actual apply/restore/
#    persist code, tested against temp files standing in for the real
#    /proc, /sys and /etc/sysctl.d paths) ───────────────────────────────
class PrivWriterSwappinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake_swap_path = os.path.join(self.tmp, "swappiness")
        with open(self.fake_swap_path, "w") as f:
            f.write("60")
        self.fake_sysctl_path = os.path.join(self.tmp, "90-mg.conf")
        self._orig_sysctl_file = sysctl_store.SYSCTL_FILE
        sysctl_store.SYSCTL_FILE = self.fake_sysctl_path

        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.SwappinessWriter()
        self.writer.PATH = self.fake_swap_path  # instance attr shadows class attr

    def tearDown(self):
        sysctl_store.SYSCTL_FILE = self._orig_sysctl_file
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read_fake(self):
        with open(self.fake_swap_path) as f:
            return f.read().strip()

    def test_apply_temporary_valid_value(self):
        result = self.writer.apply_temporary("80", None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], 80)
        self.assertEqual(self._read_fake(), "80")
        rec = self.state.get("memory.swappiness")
        self.assertEqual(rec.initial_value, 60)
        self.assertEqual(rec.last_applied_value, 80)
        self.assertEqual(rec.mode, "temporary")

    def test_apply_temporary_invalid_value_rejected(self):
        result = self.writer.apply_temporary("999", None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_invalid_value")
        self.assertEqual(self._read_fake(), "60")  # untouched

    def test_restore_after_apply(self):
        self.writer.apply_temporary("80", None, False, self.state)
        result = self.writer.restore(None, None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], 60)
        self.assertEqual(self._read_fake(), "60")

    def test_restore_without_prior_apply_fails_cleanly(self):
        result = self.writer.restore(None, None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_nothing_to_restore")

    def test_restore_detects_external_change_and_does_not_overwrite(self):
        self.writer.apply_temporary("80", None, False, self.state)
        with open(self.fake_swap_path, "w") as f:
            f.write("42")  # something else changed it
        result = self.writer.restore(None, None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_external_change_detected")
        self.assertEqual(self._read_fake(), "42")  # left alone, not silently overwritten

    def test_restore_forced_overrides_external_change(self):
        self.writer.apply_temporary("80", None, False, self.state)
        with open(self.fake_swap_path, "w") as f:
            f.write("42")
        result = self.writer.restore(None, None, True, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(self._read_fake(), "60")

    def test_apply_persistent_writes_sysctl_key(self):
        result = self.writer.apply_persistent("100", None, False, self.state)
        self.assertTrue(result["ok"])
        with open(self.fake_sysctl_path) as f:
            self.assertIn("vm.swappiness = 100", f.read())

    def test_restore_removes_sysctl_key(self):
        self.writer.apply_persistent("100", None, False, self.state)
        self.writer.restore(None, None, False, self.state)
        if os.path.exists(self.fake_sysctl_path):
            with open(self.fake_sysctl_path) as f:
                self.assertNotIn("vm.swappiness", f.read())

    def test_sysctl_store_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            sysctl_store.write_key("kernel.totally_unmanaged_key", "1")


class PrivWriterIOSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake_sched_path = os.path.join(self.tmp, "scheduler")
        # Real sysfs scheduler files are special: you write a plain name
        # ("none"), and the kernel re-formats what you read back with
        # brackets around whichever is active ("none [mq-deadline] kyber").
        # A plain temp file can't replicate that kernel-side behaviour, so
        # we start it holding just the current value (no brackets) and
        # treat "whatever is in the file" as the current value directly —
        # this still genuinely exercises apply/validate/write/re-read/
        # record/restore, just not the bracket-parsing itself (that's
        # covered separately in IOSchedulerFeatureReadTests).
        with open(self.fake_sched_path, "w") as f:
            f.write("kyber")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.IOSchedulerWriter()
        fixed_available = ["none", "mq-deadline", "kyber"]

        def fake_read(path):
            with open(path) as f:
                raw = f.read().strip()
            return fixed_available, raw

        self.writer._read = fake_read
        # The real _validate_device() hardcodes /sys/block/<id>/queue/
        # scheduler by design (no GUI-supplied path). For this test we
        # substitute a fake path but keep the same "must really exist"
        # contract the real method has.
        self.writer._validate_device = lambda device_id: (
            self.fake_sched_path if device_id == "faketest" and os.path.isfile(self.fake_sched_path) else None
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_temporary_valid_choice(self):
        result = self.writer.apply_temporary("none", "faketest", False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "none")

    def test_apply_temporary_invalid_choice_rejected(self):
        result = self.writer.apply_temporary("bfq", "faketest", False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_invalid_value")

    def test_unknown_device_rejected(self):
        result = self.writer.apply_temporary("none", "doesnotexist", False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_device_not_found")

    def test_restore_after_apply(self):
        self.writer.apply_temporary("none", "faketest", False, self.state)
        result = self.writer.restore(None, "faketest", False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "kyber")

    def test_device_removed_mid_operation(self):
        """Simulates a USB disk being unplugged between listing it and
        restoring its scheduler — must fail cleanly, never crash."""
        self.writer.apply_temporary("none", "faketest", False, self.state)
        os.remove(self.fake_sched_path)
        result = self.writer.restore(None, "faketest", False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_device_not_found")

    def test_no_persistence_in_this_phase(self):
        from core.kernel_features.storage import IOSchedulerFeature
        f = IOSchedulerFeature("faketest")
        result = f.apply_persistent("none")
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "kf_err_no_persistence")


if __name__ == "__main__":
    unittest.main()
