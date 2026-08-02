"""
Base classes for the KernelFeature registry.

Design rules (from the M.G Linux Toolbox master spec):
- Detect capability by probing /proc and /sys directly — never by branching
  on distro name.
- Every dependency (proc_root, sys_root, state_store, privileged_writer,
  clock) is injectable, so tests can point at a fake filesystem instead of
  the real machine.
- Never claim a value is "the" universally recommended one — features may
  offer optional presets, but the UI layer decides how to present them.
- No feature ever executes a GUI-supplied path or shell string. Writes go
  through PrivilegedWriter.execute(feature_id, action, value, device_id),
  which resolves the real path internally.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from core.persistence.rollback_store import FeatureRecord


class SupportStatus(Enum):
    """
    What the UI is allowed to offer for a given feature right now.
    Deliberately more granular than a plain "available/not available" flag.
    """
    SUPPORTED_READ_ONLY = "supported_read_only"    # can read, nothing to change
    SUPPORTED_RUNTIME = "supported_runtime"        # can read + apply temporarily
    SUPPORTED_PERSISTENT = "supported_persistent"  # can also make it survive reboot
    UNSUPPORTED_KERNEL = "unsupported_kernel"      # this kernel build lacks it
    UNSUPPORTED_HARDWARE = "unsupported_hardware"  # kernel has it, this hardware doesn't
    UNAVAILABLE = "unavailable"                    # present but not usable right now
                                                    # (permission denied, device just
                                                    # vanished, transient I/O error)
    UNKNOWN = "unknown"                            # probing itself failed unexpectedly
    ERROR = "error"                                # a real operation failed


@dataclass
class OpResult:
    """
    Result of any probe/read/apply/restore call. `friendly_message` is what
    the simple-mode UI shows; `technical_detail` only appears behind a
    "Show details" disclosure — never raw exceptions/stderr in the main UI.
    """
    ok: bool
    value: Any = None
    friendly_message: str = ""
    technical_detail: str = ""
    reboot_required: bool = False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class KernelFeature:
    """
    Base class for one kernel-level tunable/monitor. Concrete features
    (PSI, swappiness, I/O scheduler...) subclass this and implement the
    probe/read/apply/restore methods for their own /proc or /sys paths.

    Every constructor argument has a real-system default so normal app
    code can just do `SwappinessFeature()`, while tests pass in fakes:

        SwappinessFeature(proc_root="tests/fake_proc", ...)
    """

    id: str = ""
    category: str = ""
    simple_name_key: str = ""     # i18n key, e.g. "kf_swappiness_title"
    technical_name: str = ""      # shown small, never as the main title
    risk: str = "low"             # "low" | "medium" | "high"
    read_only: bool = False
    supports_persistence: bool = False

    def __init__(self, proc_root: str = "/proc", sys_root: str = "/sys",
                 state_store=None, privileged_writer=None, clock=None):
        self.proc_root = proc_root
        self.sys_root = sys_root
        self._state_store = state_store
        self._privileged_writer = privileged_writer
        self._clock = clock or utc_now_iso

        if self._state_store is None:
            from core.persistence.rollback_store import default_state_store
            self._state_store = default_state_store()
        if self._privileged_writer is None:
            from core.persistence.priv_client import default_privileged_writer
            self._privileged_writer = default_privileged_writer()

    # ── to be implemented by subclasses ────────────────────────────
    def probe(self) -> SupportStatus:
        raise NotImplementedError

    def read_current(self) -> OpResult:
        raise NotImplementedError

    def read_available(self) -> Optional[list]:
        """Discrete values the kernel really exposes right now, or None if
        the value is continuous / has no fixed set."""
        return None

    def to_friendly(self, raw_value) -> str:
        """Human-readable label for a raw value. Default: str() as-is."""
        return str(raw_value)

    def validate(self, value) -> bool:
        return True

    def apply_temporary(self, value) -> OpResult:
        raise NotImplementedError

    def apply_persistent(self, value) -> OpResult:
        return OpResult(False, friendly_message="kf_err_no_persistence")

    def restore(self, force: bool = False) -> OpResult:
        raise NotImplementedError

    # ── shared read-only helper around the record store ─────────────
    # NOTE: recording (put) only ever happens on the privileged side,
    # inside core/priv_writer.py while it runs as root — /var/lib is
    # root-owned. From here (the unprivileged GUI process) we only ever
    # read it, e.g. to detect an external change before restoring.
    def _record_key(self, device_id: Optional[str] = None) -> str:
        return f"{self.id}:{device_id}" if device_id else self.id

    def get_record(self, device_id: Optional[str] = None) -> Optional[FeatureRecord]:
        return self._state_store.get(self._record_key(device_id))

    def external_change_detected(self, current_value, device_id: Optional[str] = None) -> bool:
        """True if something outside M.G Linux Toolbox changed this value
        since our own last apply — used to decide whether to show the
        "this was changed elsewhere, restore anyway?" confirmation."""
        rec = self.get_record(device_id)
        if rec is None or rec.last_applied_value is None:
            return False
        return current_value != rec.last_applied_value
