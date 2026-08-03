"""
Disk Activity backend (2026-08-03) — read-only, no extra tools installed.

Deliberately separate from any GUI code: this module only takes samples
and returns plain dataclasses. The GUI layer (ui/pages/page_disk_activity.py)
is responsible for requesting sample() periodically from a worker and
rendering the returned snapshot on GTK's main thread. The sampler never
sleeps, but a full /proc walk can still take time and must not run in the
GTK thread.

Sources used, all already-present kernel interfaces (see master spec):
- /sys/block/<device>/stat      — per-disk cumulative read/write sectors
- /proc/<pid>/io                — per-process cumulative read/write bytes
- /proc/<pid>/comm              — process name (cmdline is deliberately
  NOT read here — comm is enough for a friendly name in this first
  version, and never risks showing another user's command-line
  arguments)

Rates are derived by keeping the previous cumulative counters and
diffing against the next sample() call — never by sleeping between two
reads. The very first sample() call after construction (or after a
device/process reappears) has nothing to diff against yet, so it
reports a zero rate rather than guessing.
"""
import os
import time
from dataclasses import dataclass, field

from core.kernel_features.storage import list_real_disks

SECTOR_BYTES = 512


@dataclass
class DiskSample:
    device_id: str
    friendly_name: str
    kind: str  # "NVMe" | "SSD" | "HDD" | other
    read_bps: float
    write_bps: float
    ops_in_progress: int


@dataclass
class ProcessSample:
    pid: int
    name: str
    read_bps: float
    write_bps: float


@dataclass
class DiskActivitySnapshot:
    disks: list = field(default_factory=list)
    processes: list = field(default_factory=list)
    unreadable_process_count: int = 0
    disk_source_available: bool = True
    process_source_available: bool = True


def _disk_kind(friendly_name: str) -> str:
    # _friendly_disk_name() already returns "<Kind> <model>" — reuse
    # that instead of re-deriving NVMe/SSD/HDD a second way (same trick
    # ui/pages/page_overview.py already uses for the same reason).
    return friendly_name.split(" ", 1)[0] if friendly_name else ""


class DiskActivitySampler:
    """Stateful sampler. Call sample() from one worker at a time; each
    call diffs against the previous call's counters."""

    def __init__(self, proc_root: str = "/proc", sys_root: str = "/sys", time_source=time.monotonic):
        self.proc_root = proc_root
        self.sys_root = sys_root
        self._time_source = time_source
        self._last_time = None
        self._last_disk_bytes = {}   # device_id -> (read_bytes, write_bytes)
        self._last_proc_io = {}      # pid -> (name, read_bytes, write_bytes)

    def sample(self) -> DiskActivitySnapshot:
        now = self._time_source()
        dt = None if self._last_time is None else (now - self._last_time)
        if dt is not None and dt <= 0:
            # Clock didn't advance (or somehow went backwards) between
            # two calls — never divide by zero/negative, just report
            # no movement this tick instead of a bogus spike.
            dt = None
        disks, disk_source_available = self._sample_disks(dt)
        processes, unreadable, process_source_available = self._sample_processes(dt)
        self._last_time = now
        return DiskActivitySnapshot(
            disks=disks,
            processes=processes,
            unreadable_process_count=unreadable,
            disk_source_available=disk_source_available,
            process_source_available=process_source_available,
        )

    # ── Per-disk ──────────────────────────────────────────────────
    def _sample_disks(self, dt):
        results = []
        seen_ids = set()
        block_dir = os.path.join(self.sys_root, "block")
        if not os.path.isdir(block_dir):
            self._last_disk_bytes.clear()
            return results, False
        for device_id, friendly_name in list_real_disks(self.sys_root):
            seen_ids.add(device_id)
            stat_path = os.path.join(self.sys_root, "block", device_id, "stat")
            try:
                with open(stat_path) as f:
                    fields = f.read().split()
            except OSError:
                # Disk removed between listing and reading it, or
                # otherwise unreadable right now — skip it, don't crash.
                self._last_disk_bytes.pop(device_id, None)
                continue
            try:
                sectors_read = int(fields[2])
                sectors_written = int(fields[6])
                ops_in_progress = int(fields[8]) if len(fields) > 8 else 0
            except (IndexError, ValueError):
                self._last_disk_bytes.pop(device_id, None)
                continue

            read_bytes = sectors_read * SECTOR_BYTES
            write_bytes = sectors_written * SECTOR_BYTES
            prev = self._last_disk_bytes.get(device_id)
            self._last_disk_bytes[device_id] = (read_bytes, write_bytes)
            read_bps, write_bps = _rate(prev, (read_bytes, write_bytes), dt)

            results.append(DiskSample(
                device_id=device_id,
                friendly_name=friendly_name,
                kind=_disk_kind(friendly_name),
                read_bps=read_bps,
                write_bps=write_bps,
                ops_in_progress=ops_in_progress,
            ))

        # Forget devices that disappeared since the last sample, so a
        # reinserted device with the same name never diffs against a
        # stale, unrelated counter value.
        for stale in list(self._last_disk_bytes):
            if stale not in seen_ids:
                del self._last_disk_bytes[stale]

        return results, True

    # ── Per-process ───────────────────────────────────────────────
    def _sample_processes(self, dt):
        results = []
        unreadable = 0
        current_pids = set()
        try:
            entries = os.listdir(self.proc_root)
        except OSError:
            self._last_proc_io.clear()
            return results, 0, False

        for entry in entries:
            if not entry.isdigit():
                continue
            pid = int(entry)
            current_pids.add(pid)

            io_path = os.path.join(self.proc_root, entry, "io")
            try:
                with open(io_path) as f:
                    io_text = f.read()
            except FileNotFoundError:
                # Normal race: the process exited after listdir().
                self._last_proc_io.pop(pid, None)
                continue
            except PermissionError:
                # Other users' or protected system processes are often
                # unreadable without elevated privileges. Never request
                # those privileges for this read-only page.
                unreadable += 1
                self._last_proc_io.pop(pid, None)
                continue
            except OSError:
                unreadable += 1
                self._last_proc_io.pop(pid, None)
                continue

            read_bytes, write_bytes = _parse_proc_io(io_text)
            if read_bytes is None:
                unreadable += 1
                self._last_proc_io.pop(pid, None)
                continue

            name = self._read_comm(entry)
            prev_entry = self._last_proc_io.get(pid)
            prev = None
            if prev_entry is not None and prev_entry[0] == name:
                prev = prev_entry[1:]
            self._last_proc_io[pid] = (name, read_bytes, write_bytes)
            read_bps, write_bps = _rate(prev, (read_bytes, write_bytes), dt)

            if read_bps > 0.0 or write_bps > 0.0:
                results.append(ProcessSample(pid=pid, name=name, read_bps=read_bps, write_bps=write_bps))

        for stale in list(self._last_proc_io):
            if stale not in current_pids:
                del self._last_proc_io[stale]

        results.sort(key=lambda p: p.read_bps + p.write_bps, reverse=True)
        return results, unreadable, True

    def _read_comm(self, pid_entry: str) -> str:
        try:
            with open(os.path.join(self.proc_root, pid_entry, "comm")) as f:
                name = f.read().strip()
        except OSError:
            return ""
        return name


def _parse_proc_io(text: str):
    """Returns (read_bytes, write_bytes) or (None, None) if the file
    doesn't have the fields this needs (unexpected format, empty)."""
    values = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        values[key.strip()] = raw_value.strip()
    try:
        return int(values["read_bytes"]), int(values["write_bytes"])
    except (KeyError, ValueError):
        return None, None


def _rate(prev, current, dt):
    if dt is None or prev is None:
        return 0.0, 0.0
    prev_read, prev_write = prev
    cur_read, cur_write = current
    # Counters are cumulative and monotonic except across a device
    # reset — never surface a negative rate from a counter rollback.
    read_bps = max(cur_read - prev_read, 0) / dt
    write_bps = max(cur_write - prev_write, 0) / dt
    return read_bps, write_bps
