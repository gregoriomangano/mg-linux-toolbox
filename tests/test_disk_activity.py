"""
Tests for the Disk Activity backend (core/kernel_features/disk_activity.py)
using fake /proc and /sys trees — none of these touch the real machine.
Run with: python3 -m unittest discover -s tests -v

DiskActivitySampler is stateful and rate/time-based, so most tests drive
it with an injected fake time_source instead of real time.monotonic(),
for determinism.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.kernel_features.disk_activity import DiskActivitySampler


class FakeClock:
    def __init__(self, start=0.0):
        self.t = start

    def advance(self, seconds):
        self.t += seconds

    def __call__(self):
        return self.t


class DiskActivityFakeRootTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proc_root = os.path.join(self.tmp, "proc")
        self.sys_root = os.path.join(self.tmp, "sys")
        os.makedirs(self.proc_root)
        os.makedirs(os.path.join(self.sys_root, "block"))
        self.clock = FakeClock()
        self.sampler = DiskActivitySampler(
            proc_root=self.proc_root, sys_root=self.sys_root, time_source=self.clock
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── disk fixture helpers ────────────────────────────────────
    def _add_disk(self, device_id, sectors_read=0, sectors_written=0, ops_in_progress=0, rotational="0"):
        dev_dir = os.path.join(self.sys_root, "block", device_id, "queue")
        os.makedirs(dev_dir, exist_ok=True)
        with open(os.path.join(dev_dir, "scheduler"), "w") as f:
            f.write("[none] mq-deadline\n")
        with open(os.path.join(dev_dir, "rotational"), "w") as f:
            f.write(rotational + "\n")
        self._write_disk_stat(device_id, sectors_read, sectors_written, ops_in_progress)

    def _write_disk_stat(self, device_id, sectors_read, sectors_written, ops_in_progress=0):
        # Real /sys/block/<dev>/stat has 11+ whitespace-separated fields;
        # only indices 2 (sectors_read), 6 (sectors_written) and 8
        # (ios_in_progress) matter to the sampler.
        fields = [0, 0, sectors_read, 0, 0, 0, sectors_written, 0, ops_in_progress, 0, 0]
        path = os.path.join(self.sys_root, "block", device_id, "stat")
        with open(path, "w") as f:
            f.write(" ".join(str(v) for v in fields) + "\n")

    def _remove_disk(self, device_id):
        shutil.rmtree(os.path.join(self.sys_root, "block", device_id))

    # ── process fixture helpers ──────────────────────────────────
    def _add_process(self, pid, read_bytes=0, write_bytes=0, comm=None):
        pid_dir = os.path.join(self.proc_root, str(pid))
        os.makedirs(pid_dir, exist_ok=True)
        with open(os.path.join(pid_dir, "io"), "w") as f:
            f.write(
                f"rchar: {read_bytes}\nwchar: {write_bytes}\n"
                f"syscr: 1\nsyscw: 1\n"
                f"read_bytes: {read_bytes}\nwrite_bytes: {write_bytes}\n"
                f"cancelled_write_bytes: 0\n"
            )
        if comm is not None:
            with open(os.path.join(pid_dir, "comm"), "w") as f:
                f.write(comm + "\n")

    def _remove_process(self, pid):
        shutil.rmtree(os.path.join(self.proc_root, str(pid)))

    def _make_unreadable(self, pid):
        os.chmod(os.path.join(self.proc_root, str(pid), "io"), 0o000)

    def _tick(self, seconds=2.0):
        self.clock.advance(seconds)
        return self.sampler.sample()


# ── Disks ────────────────────────────────────────────────────────────
class DiskSamplingTests(DiskActivityFakeRootTestCase):
    def test_missing_sys_block_source_is_reported_without_crashing(self):
        shutil.rmtree(os.path.join(self.sys_root, "block"))
        snap = self.sampler.sample()
        self.assertFalse(snap.disk_source_available)
        self.assertEqual(snap.disks, [])

    def test_first_sample_has_no_rate_yet(self):
        self._add_disk("sda", sectors_read=1000, sectors_written=1000)
        snap = self.sampler.sample()
        self.assertEqual(len(snap.disks), 1)
        self.assertEqual(snap.disks[0].read_bps, 0.0)
        self.assertEqual(snap.disks[0].write_bps, 0.0)

    def test_growing_counters_produce_a_positive_rate(self):
        self._add_disk("sda", sectors_read=1000, sectors_written=1000)
        self.sampler.sample()
        self._write_disk_stat("sda", sectors_read=3000, sectors_written=1000)  # +2000 sectors read
        snap = self._tick(2.0)
        d = snap.disks[0]
        self.assertAlmostEqual(d.read_bps, (2000 * 512) / 2.0)
        self.assertEqual(d.write_bps, 0.0)

    def test_unchanged_counters_produce_a_zero_rate(self):
        self._add_disk("sda", sectors_read=1000, sectors_written=1000)
        self.sampler.sample()
        snap = self._tick(2.0)  # stat file untouched
        self.assertEqual(snap.disks[0].read_bps, 0.0)
        self.assertEqual(snap.disks[0].write_bps, 0.0)

    def test_reset_counters_never_produce_a_negative_rate(self):
        self._add_disk("sda", sectors_read=5000, sectors_written=5000)
        self.sampler.sample()
        self._write_disk_stat("sda", sectors_read=10, sectors_written=10)  # device reset/replaced
        snap = self._tick(2.0)
        self.assertEqual(snap.disks[0].read_bps, 0.0)
        self.assertEqual(snap.disks[0].write_bps, 0.0)

    def test_multiple_real_disks_are_all_reported(self):
        self._add_disk("nvme0n1", sectors_read=100, sectors_written=100)
        self._add_disk("sda", sectors_read=100, sectors_written=100, rotational="1")
        snap = self.sampler.sample()
        ids = {d.device_id for d in snap.disks}
        self.assertEqual(ids, {"nvme0n1", "sda"})
        kinds = {d.device_id: d.kind for d in snap.disks}
        self.assertEqual(kinds["nvme0n1"], "NVMe")
        self.assertEqual(kinds["sda"], "HDD")

    def test_removed_device_disappears_without_crashing(self):
        self._add_disk("sda", sectors_read=100, sectors_written=100)
        self.sampler.sample()
        self._remove_disk("sda")
        snap = self._tick(2.0)  # must not raise
        self.assertEqual(snap.disks, [])
        # Re-adding it afterwards must not diff against the stale value.
        self._add_disk("sda", sectors_read=999999, sectors_written=999999)
        snap2 = self._tick(2.0)
        self.assertEqual(snap2.disks[0].read_bps, 0.0)


# ── Processes ────────────────────────────────────────────────────────
class ProcessSamplingTests(DiskActivityFakeRootTestCase):
    def test_missing_proc_source_is_reported_without_crashing(self):
        shutil.rmtree(self.proc_root)
        snap = self.sampler.sample()
        self.assertFalse(snap.process_source_available)
        self.assertEqual(snap.processes, [])

    def test_process_with_only_reads(self):
        self._add_process(111, read_bytes=1000, write_bytes=0, comm="reader")
        self.sampler.sample()
        self._add_process(111, read_bytes=5000, write_bytes=0, comm="reader")
        snap = self._tick(2.0)
        self.assertEqual(len(snap.processes), 1)
        p = snap.processes[0]
        self.assertAlmostEqual(p.read_bps, (5000 - 1000) / 2.0)
        self.assertEqual(p.write_bps, 0.0)

    def test_process_with_only_writes(self):
        self._add_process(222, read_bytes=0, write_bytes=1000, comm="writer")
        self.sampler.sample()
        self._add_process(222, read_bytes=0, write_bytes=9000, comm="writer")
        snap = self._tick(2.0)
        p = snap.processes[0]
        self.assertEqual(p.read_bps, 0.0)
        self.assertAlmostEqual(p.write_bps, (9000 - 1000) / 2.0)

    def test_idle_process_is_not_listed(self):
        self._add_process(333, read_bytes=1000, write_bytes=1000, comm="idle")
        self.sampler.sample()
        # counters unchanged on the 2nd sample
        snap = self._tick(2.0)
        self.assertEqual(snap.processes, [])

    def test_process_terminated_between_samples_does_not_crash(self):
        self._add_process(444, read_bytes=1000, write_bytes=1000, comm="short-lived")
        self.sampler.sample()
        self._remove_process(444)
        snap = self._tick(2.0)  # must not raise
        self.assertEqual(snap.processes, [])
        self.assertEqual(snap.unreadable_process_count, 0)  # gone, not "unreadable"

    def test_unreadable_proc_io_is_counted_not_crashed(self):
        self._add_process(555, read_bytes=1000, write_bytes=1000, comm="root-owned")
        self._make_unreadable(555)
        snap = self.sampler.sample()  # must not raise
        self.assertEqual(snap.processes, [])
        self.assertEqual(snap.unreadable_process_count, 1)

    def test_missing_process_name_falls_back_gracefully(self):
        self._add_process(666, read_bytes=1000, write_bytes=1000, comm=None)  # no comm file
        self.sampler.sample()
        self._add_process(666, read_bytes=5000, write_bytes=1000, comm=None)
        snap = self._tick(2.0)
        self.assertEqual(len(snap.processes), 1)
        self.assertEqual(snap.processes[0].name, "")  # GUI decides the placeholder text

    def test_most_active_process_sorted_first(self):
        self._add_process(1, read_bytes=0, write_bytes=0, comm="quiet")
        self._add_process(2, read_bytes=0, write_bytes=0, comm="busy")
        self.sampler.sample()
        self._add_process(1, read_bytes=100, write_bytes=0, comm="quiet")
        self._add_process(2, read_bytes=100000, write_bytes=0, comm="busy")
        snap = self._tick(2.0)
        self.assertEqual(snap.processes[0].name, "busy")

    def test_reused_pid_with_a_different_name_does_not_create_a_false_spike(self):
        self._add_process(777, read_bytes=10, write_bytes=10, comm="old-process")
        self.sampler.sample()
        self._remove_process(777)
        self._add_process(777, read_bytes=999999, write_bytes=999999, comm="new-process")
        snap = self._tick(2.0)
        self.assertEqual(snap.processes, [])


if __name__ == "__main__":
    unittest.main()
