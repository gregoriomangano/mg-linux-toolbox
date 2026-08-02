"""
I/O scheduler — the order in which Linux serves read/write requests for
one real disk. Per-device (each disk gets its own IOSchedulerFeature
instance), temporary-only in this phase (no persistence yet).
"""
import os

from core.kernel_features.base import KernelFeature, SupportStatus, OpResult

EXCLUDED_PREFIXES = ("loop", "ram", "zram", "dm-")


def _read_file(path: str, fallback: str = "") -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return fallback


def list_real_disks(sys_root: str = "/sys") -> list:
    """
    Returns [(device_id, friendly_name), ...] for block devices that are
    real disks with a usable I/O scheduler — not loop/ram/zram/device-mapper,
    and only if they actually expose queue/scheduler (some virtual devices
    don't).
    """
    disks = []
    block_dir = os.path.join(sys_root, "block")
    try:
        entries = sorted(os.listdir(block_dir))
    except OSError:
        return disks

    for dev in entries:
        if dev.startswith(EXCLUDED_PREFIXES):
            continue
        sched_path = os.path.join(block_dir, dev, "queue", "scheduler")
        if not os.path.isfile(sched_path):
            continue
        disks.append((dev, _friendly_disk_name(dev, sys_root)))
    return disks


def _friendly_disk_name(dev: str, sys_root: str) -> str:
    model = _read_file(os.path.join(sys_root, "block", dev, "device", "model"))
    rotational = _read_file(os.path.join(sys_root, "block", dev, "queue", "rotational"), "1")
    if dev.startswith("nvme"):
        kind = "NVMe"
    elif rotational == "0":
        kind = "SSD"
    else:
        kind = "HDD"
    return f"{kind} {model}".strip() if model else f"{kind} ({dev})"


class IOSchedulerFeature(KernelFeature):
    id = "storage.io_scheduler"
    category = "storage"
    technical_name = "I/O scheduler"
    risk = "medium"
    supports_persistence = False

    def __init__(self, device_id: str, **kwargs):
        super().__init__(**kwargs)
        self.device_id = device_id

    def _path(self) -> str:
        return os.path.join(self.sys_root, "block", self.device_id, "queue", "scheduler")

    def probe(self) -> SupportStatus:
        path = self._path()
        if not os.path.isfile(path):
            return SupportStatus.UNSUPPORTED_KERNEL
        try:
            with open(path):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        return SupportStatus.SUPPORTED_RUNTIME

    def _parse(self, content: str) -> dict:
        tokens = [t.strip("[]") for t in content.split()]
        current = next((t.strip("[]") for t in content.split() if t.startswith("[")), None)
        return {"available": tokens, "current": current}

    def read_current(self) -> OpResult:
        try:
            with open(self._path()) as f:
                content = f.read().strip()
        except FileNotFoundError:
            return OpResult(False, friendly_message="kf_err_device_not_found")
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except OSError as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
        return OpResult(True, value=self._parse(content))

    def read_available(self):
        r = self.read_current()
        return r.value["available"] if r.ok else None

    def to_friendly(self, raw_value) -> str:
        # Scheduler algorithm names (bfq/mq-deadline/kyber/none) are technical
        # identifiers without a natural-language translation — shown as-is,
        # per spec ("non suggerire BFQ/kyber/mq-deadline se non compaiono").
        return raw_value or "—"

    def validate(self, value) -> bool:
        available = self.read_available()
        return bool(available) and value in available

    def apply_temporary(self, value) -> OpResult:
        if not self.validate(value):
            return OpResult(False, friendly_message="kf_err_invalid_value")
        return self._privileged_writer.execute(self.id, "apply_temporary", value, device_id=self.device_id)

    def apply_persistent(self, value) -> OpResult:
        return OpResult(False, friendly_message="kf_err_no_persistence")

    def restore(self, force: bool = False) -> OpResult:
        current = self.read_current()
        if not current.ok:
            return current
        rec = self.get_record(device_id=self.device_id)
        if rec is None:
            return OpResult(False, friendly_message="kf_err_nothing_to_restore")
        current_value = current.value["current"]
        if not force and self.external_change_detected(current_value, device_id=self.device_id):
            return OpResult(False, friendly_message="kf_external_change_detected", value=current_value)
        return self._privileged_writer.execute(self.id, "restore", None, device_id=self.device_id, force=force)


# Curated KB presets — never applied automatically to every disk at
# once (per spec: a value that helps one disk's workload can hurt
# another's). "custom" is any other value the kernel reports.
READ_AHEAD_PRESETS = {"reduced": 32, "medium": 128, "high_sequential": 1024}
_READ_AHEAD_FRIENDLY_BY_KB = {v: f"kf_read_ahead_{k}" for k, v in READ_AHEAD_PRESETS.items()}


class ReadAheadFeature(KernelFeature):
    """
    Block-device read-ahead (queue/read_ahead_kb) — how much extra data
    Linux speculatively reads past what was actually requested. One
    instance per real disk, reusing the exact same device enumeration
    as the I/O scheduler (list_real_disks): no loop/ram/zram/unsuitable
    device-mapper devices, and only disks that really expose this file.
    """
    id = "storage.read_ahead"
    category = "storage"
    technical_name = "Block device read-ahead"
    risk = "low"
    supports_persistence = False

    MIN_KB, MAX_KB = 0, 16384  # sanity bounds only — no "best" value implied

    def __init__(self, device_id: str, **kwargs):
        super().__init__(**kwargs)
        self.device_id = device_id

    def _path(self) -> str:
        return os.path.join(self.sys_root, "block", self.device_id, "queue", "read_ahead_kb")

    def probe(self) -> SupportStatus:
        path = self._path()
        if not os.path.isfile(path):
            return SupportStatus.UNSUPPORTED_KERNEL
        try:
            with open(path):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        return SupportStatus.SUPPORTED_RUNTIME

    def read_current(self) -> OpResult:
        try:
            with open(self._path()) as f:
                return OpResult(True, value=int(f.read().strip()))
        except FileNotFoundError:
            return OpResult(False, friendly_message="kf_err_device_not_found")
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except (OSError, ValueError) as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))

    def to_friendly(self, raw_value) -> str:
        try:
            kb = int(raw_value)
        except (TypeError, ValueError):
            return str(raw_value)
        return _READ_AHEAD_FRIENDLY_BY_KB.get(kb, "kf_read_ahead_custom")

    def validate(self, value) -> bool:
        try:
            v = int(value)
        except (TypeError, ValueError):
            return False
        return self.MIN_KB <= v <= self.MAX_KB

    def apply_temporary(self, value) -> OpResult:
        if not self.validate(value):
            return OpResult(False, friendly_message="kf_err_invalid_value")
        return self._privileged_writer.execute(self.id, "apply_temporary", str(value), device_id=self.device_id)

    def restore(self, force: bool = False) -> OpResult:
        current = self.read_current()
        if not current.ok:
            return current
        rec = self.get_record(device_id=self.device_id)
        if rec is None:
            return OpResult(False, friendly_message="kf_err_nothing_to_restore")
        if not force and self.external_change_detected(current.value, device_id=self.device_id):
            return OpResult(False, friendly_message="kf_external_change_detected", value=current.value)
        return self._privileged_writer.execute(self.id, "restore", None, device_id=self.device_id, force=force)
