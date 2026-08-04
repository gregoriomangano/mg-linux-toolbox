"""
Corroborating signals for the disk-activity PSI reading (2026-08-04).

PSI's avg10/avg60 alone can't distinguish "one background process
quietly waiting on disk while the machine is otherwise idle" from "the
whole system is bogged down waiting for disk I/O" — both can produce
the same "high" PSI bucket. This module adds the two cheap, always-
available corroborating signals the spec asks for (count of processes
in uninterruptible sleep, and how idle the CPU currently is) so the UI
can tell those two situations apart before ever showing red.

Deliberately NOT part of core.kernel_features.disk_activity's sampler:
that module's DiskActivitySnapshot shape is already relied on by
existing tests/UI, and this reads a different, smaller slice of /proc
independently so nothing there has to change.
"""
import os
from dataclasses import dataclass


def count_blocked_processes(proc_root: str = "/proc") -> "int | None":
    """Number of processes currently in uninterruptible sleep (state
    'D' in /proc/<pid>/stat) — the classic "waiting on disk" state.
    Returns None (not 0) when /proc itself can't even be listed, so an
    unreadable environment is never mistaken for "nothing blocked"."""
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return None
    count = 0
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, entry, "stat"), encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        idx = content.rfind(")")
        if idx == -1:
            continue
        rest = content[idx + 2:]
        state = rest.split(" ", 1)[0] if rest else ""
        if state == "D":
            count += 1
    return count


def _read_cpu_totals(stat_path: str) -> "tuple[float, float] | None":
    """Returns (idle_and_iowait, total) jiffies from the aggregate
    'cpu ' line, or None if /proc/stat couldn't be read/parsed."""
    try:
        with open(stat_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("cpu "):
                    parts = line.split()[1:]
                    nums = [float(p) for p in parts if p.lstrip("-").isdigit()]
                    if len(nums) < 4:
                        return None
                    # user nice system idle iowait irq softirq steal guest guest_nice
                    idle = nums[3] + (nums[4] if len(nums) > 4 else 0.0)
                    total = sum(nums)
                    return idle, total
    except OSError:
        return None
    return None


class CpuIdleTracker:
    """Stateful: each sample() diffs against the previous /proc/stat
    read, since the raw counters are cumulative since boot. The first
    call after construction (or after a read failure) has nothing to
    diff against yet and returns None rather than a bogus 0%/100%."""

    def __init__(self, proc_root: str = "/proc"):
        self._stat_path = os.path.join(proc_root, "stat")
        self._prev = None

    def sample(self) -> "float | None":
        curr = _read_cpu_totals(self._stat_path)
        if curr is None:
            self._prev = None
            return None
        if self._prev is None:
            self._prev = curr
            return None
        prev_idle, prev_total = self._prev
        idle, total = curr
        self._prev = curr
        d_total = total - prev_total
        if d_total <= 0:
            return None
        return max(0.0, min(100.0, (idle - prev_idle) / d_total * 100.0))


# Thresholds for "this high PSI reading looks like a lone, harmless
# blip" — deliberately conservative (both conditions must hold) so a
# real system-wide slowdown is never downgraded to yellow.
_LONE_BLIP_MAX_BLOCKED = 1
_LONE_BLIP_MIN_CPU_IDLE = 80.0

CRITICAL = "critical"
INFO = "info"
LOW = "low"


def classify_disk_pressure(psi_bucket: str, blocked_count: "int | None",
                             cpu_idle_pct: "float | None") -> str:
    """psi_bucket: 'low' | 'moderate' | 'high' (PSIHysteresis' output).
    Returns 'low' | 'info' (yellow) | 'critical' (red). A 'high' PSI
    bucket is only ever downgraded to 'info' when BOTH corroborating
    signals point at a single idle-system blip; missing data (None)
    never triggers a downgrade, since we can't corroborate what we
    can't read."""
    if psi_bucket == "low":
        return LOW
    if psi_bucket == "moderate":
        return INFO
    # psi_bucket == "high"
    if (blocked_count is not None and blocked_count <= _LONE_BLIP_MAX_BLOCKED
            and cpu_idle_pct is not None and cpu_idle_pct >= _LONE_BLIP_MIN_CPU_IDLE):
        return INFO
    return CRITICAL
