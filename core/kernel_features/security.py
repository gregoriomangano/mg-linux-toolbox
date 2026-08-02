"""
SELinux enforcing/permissive mode — /sys/fs/selinux/enforce (the real
kernel LSM interface `setenforce` itself uses). Only ever offers
"enforcing"/"permissive": switching SELinux fully off needs a reboot
and a very different code path, out of scope for a simple mode toggle.
"""
import os

from core.kernel_features.base import KernelFeature, SupportStatus, OpResult

ENFORCING = "enforcing"
PERMISSIVE = "permissive"


class SELinuxFeature(KernelFeature):
    id = "selinux.mode"
    category = "security"
    technical_name = "SELinux (/sys/fs/selinux/enforce)"
    risk = "high"
    supports_persistence = True

    def _path(self) -> str:
        return os.path.join(self.sys_root, "fs", "selinux", "enforce")

    def probe(self) -> SupportStatus:
        path = self._path()
        if not os.path.isfile(path):
            return SupportStatus.UNSUPPORTED_KERNEL
        try:
            with open(path):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        return SupportStatus.SUPPORTED_PERSISTENT

    def read_current(self) -> OpResult:
        try:
            with open(self._path()) as f:
                raw = f.read().strip()
        except FileNotFoundError:
            return OpResult(False, friendly_message="kf_unsupported_kernel")
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except OSError as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
        return OpResult(True, value=ENFORCING if raw == "1" else PERMISSIVE)

    def read_available(self):
        return [ENFORCING, PERMISSIVE]

    def to_friendly(self, raw_value) -> str:
        return "selinux_mode_enforcing" if raw_value == ENFORCING else "selinux_mode_permissive"

    def validate(self, value) -> bool:
        return value in (ENFORCING, PERMISSIVE)

    def apply_temporary(self, value) -> OpResult:
        if not self.validate(value):
            return OpResult(False, friendly_message="kf_err_invalid_value")
        return self._privileged_writer.execute(self.id, "apply_temporary", value)

    def apply_persistent(self, value) -> OpResult:
        if not self.validate(value):
            return OpResult(False, friendly_message="kf_err_invalid_value")
        return self._privileged_writer.execute(self.id, "apply_persistent", value)

    def restore(self, force: bool = False) -> OpResult:
        current = self.read_current()
        if not current.ok:
            return current
        rec = self.get_record()
        if rec is None:
            return OpResult(False, friendly_message="kf_err_nothing_to_restore")
        if not force and self.external_change_detected(current.value):
            return OpResult(False, friendly_message="kf_external_change_detected", value=current.value)
        return self._privileged_writer.execute(self.id, "restore", None, force=force)


class _SimpleSysctlChoiceFeature(KernelFeature):
    """Shared logic for a single-value /proc/sys/kernel/... hardening
    knob with a small, fixed set of allowed values — dmesg_restrict,
    kptr_restrict, ptrace_scope. Subclasses set REL_PATH and CHOICES."""
    REL_PATH = ()   # tuple of path components under proc_root/sys
    CHOICES = []    # allowed raw string values, in order

    def _path(self) -> str:
        return os.path.join(self.proc_root, "sys", *self.REL_PATH)

    def probe(self) -> SupportStatus:
        path = self._path()
        if not os.path.exists(path):
            return SupportStatus.UNSUPPORTED_KERNEL
        try:
            with open(path):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        return SupportStatus.SUPPORTED_PERSISTENT

    def read_current(self) -> OpResult:
        try:
            with open(self._path()) as f:
                return OpResult(True, value=f.read().strip())
        except FileNotFoundError:
            return OpResult(False, friendly_message="kf_unsupported_kernel")
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except (OSError, ValueError) as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))

    def read_available(self):
        return list(self.CHOICES)

    def validate(self, value) -> bool:
        return str(value) in self.CHOICES

    def apply_temporary(self, value) -> OpResult:
        if not self.validate(value):
            return OpResult(False, friendly_message="kf_err_invalid_value")
        return self._privileged_writer.execute(self.id, "apply_temporary", str(value))

    def apply_persistent(self, value) -> OpResult:
        if not self.validate(value):
            return OpResult(False, friendly_message="kf_err_invalid_value")
        return self._privileged_writer.execute(self.id, "apply_persistent", str(value))

    def restore(self, force: bool = False) -> OpResult:
        current = self.read_current()
        if not current.ok:
            return current
        rec = self.get_record()
        if rec is None:
            return OpResult(False, friendly_message="kf_err_nothing_to_restore")
        if not force and self.external_change_detected(current.value):
            return OpResult(False, friendly_message="kf_external_change_detected", value=current.value)
        return self._privileged_writer.execute(self.id, "restore", None, force=force)


class DmesgRestrictFeature(_SimpleSysctlChoiceFeature):
    """Whether unprivileged users can read the kernel ring buffer
    (dmesg) — kernel log lines can leak memory addresses and other
    details useful to an attacker probing for kernel exploits."""
    id = "security.dmesg_restrict"
    category = "security"
    technical_name = "kernel.dmesg_restrict"
    risk = "low"
    supports_persistence = True
    REL_PATH = ("kernel", "dmesg_restrict")
    CHOICES = ["0", "1"]

    def to_friendly(self, raw_value) -> str:
        return "ds_val_enabled" if str(raw_value) == "1" else "ds_val_disabled"


class KptrRestrictFeature(_SimpleSysctlChoiceFeature):
    """Whether kernel pointer addresses are hidden (shown as all-zero)
    in /proc and similar interfaces — makes several classes of kernel
    exploit meaningfully harder to write."""
    id = "security.kptr_restrict"
    category = "security"
    technical_name = "kernel.kptr_restrict"
    risk = "medium"
    supports_persistence = True
    REL_PATH = ("kernel", "kptr_restrict")
    CHOICES = ["0", "1", "2"]

    def to_friendly(self, raw_value) -> str:
        return {
            "0": "ds_val_disabled",
            "1": "kf_kptr_restrict_1",
            "2": "kf_kptr_restrict_2",
        }.get(str(raw_value), str(raw_value))


class PtraceScopeFeature(_SimpleSysctlChoiceFeature):
    """Yama's ptrace_scope — who is allowed to attach a debugger/tracer
    to another process. Value 3 ("no ptrace at all, not even root, until
    reboot") is deliberately never offered here — it can make legitimate
    debugging and crash-diagnostic tools unusable system-wide with no
    way back except a reboot, per spec."""
    id = "security.ptrace_scope"
    category = "security"
    technical_name = "kernel.yama.ptrace_scope"
    risk = "medium"
    supports_persistence = True
    REL_PATH = ("kernel", "yama", "ptrace_scope")
    CHOICES = ["0", "1", "2"]  # 3 intentionally excluded

    def to_friendly(self, raw_value) -> str:
        return {
            "0": "kf_ptrace_scope_0",
            "1": "kf_ptrace_scope_1",
            "2": "kf_ptrace_scope_2",
        }.get(str(raw_value), str(raw_value))


PROTECTED_PATH_KEYS = ("protected_symlinks", "protected_hardlinks", "protected_fifos", "protected_regular")


class ProtectedPathsFeature(KernelFeature):
    """
    fs.protected_symlinks/protected_hardlinks/protected_fifos/
    protected_regular — a group of 4 related hardening sysctls, always
    read/applied/rolled back TOGETHER as one atomic operation and logged
    as a single grouped entry (per spec), never as 4 independent writes.
    Only manages keys whose /proc/sys/fs/<key> path actually exists on
    this kernel — never invents a write to one that doesn't.
    """
    id = "security.protected_paths"
    category = "security"
    technical_name = "fs.protected_symlinks/hardlinks/fifos/regular"
    risk = "medium"
    supports_persistence = False

    FULL_VALUE = "1"
    OFF_VALUE = "0"

    def _path(self, key: str) -> str:
        return os.path.join(self.proc_root, "sys", "fs", key)

    def _existing_keys(self) -> list:
        return [k for k in PROTECTED_PATH_KEYS if os.path.exists(self._path(k))]

    def probe(self) -> SupportStatus:
        existing = self._existing_keys()
        if not existing:
            return SupportStatus.UNSUPPORTED_KERNEL
        try:
            with open(self._path(existing[0])):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        return SupportStatus.SUPPORTED_RUNTIME

    def read_current(self) -> OpResult:
        existing = self._existing_keys()
        if not existing:
            return OpResult(False, friendly_message="kf_unsupported_kernel")
        values = {}
        try:
            for key in existing:
                with open(self._path(key)) as f:
                    values[key] = f.read().strip()
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except OSError as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
        return OpResult(True, value=values)

    def state(self, values: dict) -> str:
        """One of full/partial/off/unavailable — never guessed, always
        computed from the real values just read."""
        if not values:
            return "unavailable"
        on_count = sum(1 for v in values.values() if v not in ("0", ""))
        if on_count == len(values):
            return "full"
        if on_count == 0:
            return "off"
        return "partial"

    def to_friendly(self, raw_value) -> str:
        return {
            "full": "kf_protected_paths_full",
            "partial": "kf_protected_paths_partial",
            "off": "kf_protected_paths_off",
            "unavailable": "kf_protected_paths_unavailable",
        }.get(raw_value, raw_value)

    def validate(self, value) -> bool:
        return value in ("full", "off")

    def apply_temporary(self, value) -> OpResult:
        if not self.validate(value):
            return OpResult(False, friendly_message="kf_err_invalid_value")
        return self._privileged_writer.execute(self.id, "apply_temporary", value)

    def restore(self, force: bool = False) -> OpResult:
        current = self.read_current()
        if not current.ok:
            return current
        rec = self.get_record()
        if rec is None:
            return OpResult(False, friendly_message="kf_err_nothing_to_restore")
        current_state = self.state(current.value)
        if not force and self.external_change_detected(current_state):
            return OpResult(False, friendly_message="kf_external_change_detected", value=current_state)
        return self._privileged_writer.execute(self.id, "restore", None, force=force)
