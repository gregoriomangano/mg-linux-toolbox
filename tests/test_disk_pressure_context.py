"""
Tests for core.kernel_features.disk_pressure_context — the corroborating
signals (blocked-process count, CPU idle%) that keep a "high" PSI
reading from being shown as red when it's really just one background
process quietly waiting on disk while the system is otherwise idle.
Covers the exact contrast the spec asks for:

  PSI alto + molti processi bloccati + attività sostenuta = rosso
  PSI alto + un processo bloccato + CPU quasi inattiva     = giallo
"""
import os
import tempfile
import unittest

from core.kernel_features.disk_pressure_context import (
    count_blocked_processes, CpuIdleTracker, classify_disk_pressure,
    LOW, INFO, CRITICAL,
)


def _make_fake_proc(tmp_dir: str, pids_states: dict):
    for pid, state in pids_states.items():
        pid_dir = os.path.join(tmp_dir, str(pid))
        os.makedirs(pid_dir, exist_ok=True)
        with open(os.path.join(pid_dir, "stat"), "w") as f:
            f.write(f"{pid} (some proc) {state} 1 1 1 1 1 1\n")
    # a non-numeric entry must be skipped, not crash
    os.makedirs(os.path.join(tmp_dir, "self"), exist_ok=True)


class CountBlockedProcessesTests(unittest.TestCase):
    def test_counts_only_d_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_fake_proc(tmp, {1: "S", 2: "D", 3: "D", 4: "R"})
            self.assertEqual(count_blocked_processes(proc_root=tmp), 2)

    def test_zero_when_none_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_fake_proc(tmp, {1: "S", 2: "R"})
            self.assertEqual(count_blocked_processes(proc_root=tmp), 0)

    def test_none_when_proc_root_unreadable(self):
        self.assertIsNone(count_blocked_processes(proc_root="/this/does/not/exist"))

    def test_tolerates_a_pid_disappearing_mid_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_fake_proc(tmp, {1: "D"})
            os.makedirs(os.path.join(tmp, "9999"))  # dir with no stat file
            self.assertEqual(count_blocked_processes(proc_root=tmp), 1)


class CpuIdleTrackerTests(unittest.TestCase):
    def _write_stat(self, path, idle, total_minus_idle):
        # cpu user nice system idle iowait irq softirq steal
        user = total_minus_idle
        with open(path, "w") as f:
            f.write(f"cpu  {user} 0 0 {idle} 0 0 0 0 0 0\ncpu0 0 0 0 0 0 0 0 0 0 0\n")

    def test_first_sample_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_stat(os.path.join(tmp, "stat"), idle=1000, total_minus_idle=0)
            tracker = CpuIdleTracker(proc_root=tmp)
            self.assertIsNone(tracker.sample())

    def test_mostly_idle_reports_high_percentage(self):
        with tempfile.TemporaryDirectory() as tmp:
            stat_path = os.path.join(tmp, "stat")
            self._write_stat(stat_path, idle=1000, total_minus_idle=0)
            tracker = CpuIdleTracker(proc_root=tmp)
            tracker.sample()
            self._write_stat(stat_path, idle=1090, total_minus_idle=10)
            pct = tracker.sample()
            self.assertAlmostEqual(pct, 90.0, delta=0.1)

    def test_busy_reports_low_percentage(self):
        with tempfile.TemporaryDirectory() as tmp:
            stat_path = os.path.join(tmp, "stat")
            self._write_stat(stat_path, idle=1000, total_minus_idle=0)
            tracker = CpuIdleTracker(proc_root=tmp)
            tracker.sample()
            self._write_stat(stat_path, idle=1005, total_minus_idle=95)
            pct = tracker.sample()
            self.assertAlmostEqual(pct, 5.0, delta=0.1)

    def test_none_when_stat_unreadable(self):
        tracker = CpuIdleTracker(proc_root="/this/does/not/exist")
        self.assertIsNone(tracker.sample())


class ClassifyDiskPressureTests(unittest.TestCase):
    def test_low_bucket_is_always_low(self):
        self.assertEqual(classify_disk_pressure("low", 5, 10.0), LOW)

    def test_moderate_bucket_is_info(self):
        self.assertEqual(classify_disk_pressure("moderate", 5, 10.0), INFO)

    def test_high_with_many_blocked_is_critical(self):
        self.assertEqual(classify_disk_pressure("high", 5, 90.0), CRITICAL)

    def test_high_with_one_blocked_and_idle_cpu_is_info(self):
        self.assertEqual(classify_disk_pressure("high", 1, 95.0), INFO)

    def test_high_with_one_blocked_but_busy_cpu_stays_critical(self):
        self.assertEqual(classify_disk_pressure("high", 1, 30.0), CRITICAL)

    def test_high_with_missing_data_never_downgrades(self):
        self.assertEqual(classify_disk_pressure("high", None, None), CRITICAL)
        self.assertEqual(classify_disk_pressure("high", 0, None), CRITICAL)
        self.assertEqual(classify_disk_pressure("high", None, 99.0), CRITICAL)


if __name__ == "__main__":
    unittest.main()
