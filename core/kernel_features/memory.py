"""
Memory-related kernel tunables: vm.swappiness, Transparent Huge Pages,
ZRAM (compressed RAM-backed swap) and Zswap (compressed swap cache).
"""
import os

from core.kernel_features.base import KernelFeature, SupportStatus, OpResult

PRESETS = [("rare", 10), ("balanced", 60), ("low_ram", 100)]


class SwappinessFeature(KernelFeature):
    id = "memory.swappiness"
    category = "memory"
    technical_name = "vm.swappiness"
    risk = "low"
    supports_persistence = True

    MIN, MAX = 0, 200

    def _path(self) -> str:
        return os.path.join(self.proc_root, "sys", "vm", "swappiness")

    def probe(self) -> SupportStatus:
        path = self._path()
        if not os.path.exists(path):
            return SupportStatus.UNSUPPORTED_KERNEL
        try:
            with open(path):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        except OSError:
            return SupportStatus.UNKNOWN
        return SupportStatus.SUPPORTED_PERSISTENT

    def read_current(self) -> OpResult:
        try:
            with open(self._path()) as f:
                return OpResult(True, value=int(f.read().strip()))
        except FileNotFoundError:
            return OpResult(False, friendly_message="kf_unsupported_kernel")
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except (OSError, ValueError) as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))

    def read_available(self):
        return None  # continuous, not a discrete kernel-exposed set

    def preset_for(self, value) -> str:
        for key, v in PRESETS:
            if v == value:
                return key
        return "custom"

    def to_friendly(self, raw_value) -> str:
        return f"kf_swappiness_preset_{self.preset_for(raw_value)}"

    def validate(self, value) -> bool:
        try:
            v = int(value)
        except (TypeError, ValueError):
            return False
        return self.MIN <= v <= self.MAX

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


class THPFeature(KernelFeature):
    """Transparent Huge Pages — bracket-notation sysfs file, same shape as
    the I/O scheduler ("always [madvise] never")."""
    id = "memory.thp"
    category = "memory"
    technical_name = "Transparent Huge Pages"
    risk = "medium"
    supports_persistence = False

    def _path(self) -> str:
        return os.path.join(self.sys_root, "kernel", "mm", "transparent_hugepage", "enabled")

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
            return OpResult(False, friendly_message="kf_unsupported_kernel")
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except OSError as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
        return OpResult(True, value=self._parse(content))

    def read_available(self):
        r = self.read_current()
        return r.value["available"] if r.ok else None

    _FRIENDLY_KEYS = {"always": "thp_choice_always", "madvise": "thp_choice_madvise", "never": "thp_choice_never"}

    def to_friendly(self, raw_value) -> str:
        # Returns an i18n KEY (never invented text) — same convention as
        # every other feature's to_friendly(). Unlike Governor/EPP/I/O
        # scheduler, THP's three values genuinely have a plain-language
        # translation worth showing, with the real technical value kept
        # alongside in parentheses by the UI layer.
        return self._FRIENDLY_KEYS.get(raw_value, raw_value or "—")

    def validate(self, value) -> bool:
        available = self.read_available()
        return bool(available) and value in available

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
        current_value = current.value["current"]
        if not force and self.external_change_detected(current_value):
            return OpResult(False, friendly_message="kf_external_change_detected", value=current_value)
        return self._privileged_writer.execute(self.id, "restore", None, force=force)


class ZramFeature(KernelFeature):
    """
    ZRAM as swap — a real kernel feature (the zram module, always part of
    the kernel on every mainstream distro), deliberately implemented here
    WITHOUT requiring zram-tools/zram-generator or any package: enabling
    it means modprobe + writing a size to a freshly allocated zram device
    + mkswap + swapon, all real system utilities that ship with the base
    system everywhere (util-linux, kmod) — not something you'd install
    separately.

    Ownership is the whole point of this class: an active ZRAM device
    found on the system might have been set up by systemd-zram-generator,
    zram-tools, a distro service, or something else entirely — never by
    us. We only ever offer to modify (restore/disable) a device we
    genuinely created ourselves (proven by our own state store recording
    that exact device_id), never one we merely happened to notice.
    """
    id = "memory.zram"
    category = "memory"
    technical_name = "ZRAM"
    risk = "low"
    supports_persistence = False

    OWNER_TOOLBOX = "mg-linux-toolbox"
    OWNER_SYSTEMD_GENERATOR = "systemd-zram-generator"
    OWNER_ZRAM_TOOLS = "zram-tools"
    OWNER_EXTERNAL = "external"

    def _swaps_path(self) -> str:
        return os.path.join(self.proc_root, "swaps")

    def _module_available(self) -> bool:
        # Either already loaded (any zram block device or the dynamic
        # zram-control class dir present) or loadable (module present in
        # the running kernel's module tree).
        block_dir = os.path.join(self.sys_root, "block")
        try:
            if any(name.startswith("zram") for name in os.listdir(block_dir)):
                return True
        except OSError:
            pass
        if os.path.isdir(os.path.join(self.sys_root, "class", "zram-control")):
            return True
        import subprocess
        try:
            return subprocess.run(["modinfo", "zram"], capture_output=True, timeout=5).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _active_zram_devices(self) -> list:
        """Device names (e.g. ["zram0"]) currently active as swap, read
        straight from /proc/swaps — not assumed to be any particular
        fixed device."""
        try:
            with open(self._swaps_path()) as f:
                lines = f.read().splitlines()
        except OSError:
            return []
        devices = []
        for line in lines[1:]:  # first line is the header
            parts = line.split()
            if parts and "zram" in parts[0]:
                devices.append(os.path.basename(parts[0]))
        return devices

    def _service_active(self, name: str) -> bool:
        from core.executor import run_command
        ok, out, _ = run_command(["systemctl", "is-active", name])
        return ok and out.strip() == "active"

    def _service_enabled(self, name: str) -> bool:
        from core.executor import run_command
        ok, out, _ = run_command(["systemctl", "is-enabled", name])
        return ok and out.strip() == "enabled"

    def _owner_of(self, device: str) -> str:
        """Best-effort, evidence-based ownership of one active zram
        device. Only ever returns OWNER_TOOLBOX when our own state store
        actually recorded creating that exact device — never inferred."""
        rec = self.get_record()
        if rec is not None and rec.device_id == device:
            return self.OWNER_TOOLBOX
        if (self._service_active(f"systemd-zram-setup@{device}.service")
                or self._service_enabled(f"systemd-zram-setup@{device}.service")):
            return self.OWNER_SYSTEMD_GENERATOR
        if self._service_active("zramswap.service") or self._service_enabled("zramswap.service"):
            return self.OWNER_ZRAM_TOOLS
        return self.OWNER_EXTERNAL

    def owner(self) -> "str | None":
        """None if ZRAM isn't active as swap anywhere on the system right
        now; otherwise one of the OWNER_* constants."""
        devices = self._active_zram_devices()
        if not devices:
            return None
        return self._owner_of(devices[0])

    def probe(self) -> SupportStatus:
        if not os.path.isfile(self._swaps_path()):
            return SupportStatus.UNKNOWN
        owner = self.owner()
        if owner is not None and owner != self.OWNER_TOOLBOX:
            # Active, but not ours to touch — read-only.
            return SupportStatus.SUPPORTED_READ_ONLY
        if not self._module_available():
            return SupportStatus.UNSUPPORTED_KERNEL
        return SupportStatus.SUPPORTED_RUNTIME

    def read_current(self) -> OpResult:
        if not os.path.isfile(self._swaps_path()):
            return OpResult(False, friendly_message="kf_err_generic", technical_detail="/proc/swaps missing")
        active = bool(self._active_zram_devices())
        return OpResult(True, value=active)

    def to_friendly(self, raw_value) -> str:
        return "kf_zram_on" if raw_value else "kf_zram_off"

    def validate(self, value) -> bool:
        return isinstance(value, bool)

    def apply_temporary(self, value) -> OpResult:
        if not self.validate(value):
            return OpResult(False, friendly_message="kf_err_invalid_value")
        owner = self.owner()
        if owner is not None and owner != self.OWNER_TOOLBOX:
            # Defense in depth: the UI already hides all controls for an
            # externally-owned ZRAM, but never trust the UI alone.
            return OpResult(False, friendly_message="kf_zram_externally_owned")
        return self._privileged_writer.execute(self.id, "apply_temporary", value)

    def restore(self, force: bool = False) -> OpResult:
        owner = self.owner()
        if owner is not None and owner != self.OWNER_TOOLBOX:
            return OpResult(False, friendly_message="kf_zram_externally_owned")
        current = self.read_current()
        if not current.ok:
            return current
        rec = self.get_record()
        if rec is None:
            return OpResult(False, friendly_message="kf_err_nothing_to_restore")
        if not force and self.external_change_detected(current.value):
            return OpResult(False, friendly_message="kf_external_change_detected", value=current.value)
        return self._privileged_writer.execute(self.id, "restore", None, force=force)


def _classify_mglru(raw: str) -> str:
    """Interprets whatever lru_gen/enabled really returns — numeric,
    hex ("0x0007"), or y/n — without ever assuming a specific bit count
    is "the" full mask (kernels differ in how many feature bits they
    define). A value whose binary form is a contiguous run of 1s from
    bit 0 (1, 3, 7, 15, 31, ...) is treated as "fully active"; any other
    nonzero value has gaps, i.e. only some components are on."""
    raw = raw.strip()
    if not raw:
        return "disabled"
    low = raw.lower()
    if low in ("y", "1"):
        return "fully_active"
    if low in ("n", "0"):
        return "disabled"
    try:
        value = int(raw, 0)
    except ValueError:
        return "disabled"
    if value <= 0:
        return "disabled"
    return "fully_active" if (value & (value + 1)) == 0 else "partially_active"


class MGLRUFeature(KernelFeature):
    """
    Multi-Gen LRU (MGLRU) — organizes memory pages into generations by
    how recently they were used, for potentially more efficient reclaim
    under memory pressure. Only shown at all if the kernel really
    exposes /sys/kernel/mm/lru_gen/enabled (never deduced from the
    kernel version number alone).

    The UI offers exactly two safe actions ("enable everything
    supported" / "disable"), written as "y"/"n" — accepted by every
    kernel version with this interface — rather than a specific numeric
    bitmask, which would only be correct on some kernels.
    """
    id = "memory.mglru"
    category = "memory"
    technical_name = "Multi-Gen LRU (MGLRU)"
    risk = "medium"
    supports_persistence = False

    CHOICE_ENABLE_ALL = "y"
    CHOICE_DISABLE = "n"

    def _path(self) -> str:
        return os.path.join(self.sys_root, "kernel", "mm", "lru_gen", "enabled")

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
                raw = f.read().strip()
        except FileNotFoundError:
            return OpResult(False, friendly_message="kf_unsupported_kernel")
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except OSError as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
        return OpResult(True, value=raw)

    def read_available(self):
        # Not the kernel's own enumeration (there isn't one to read) —
        # these are the two safe actions this app offers, per spec.
        return [self.CHOICE_ENABLE_ALL, self.CHOICE_DISABLE]

    def to_friendly(self, raw_value) -> str:
        # Always a real state classification — "y"/"n" are accepted WRITE
        # syntax as well as values a kernel could conceivably read back,
        # so they classify the same way here as any numeric/hex reading
        # (fully_active/disabled), never a separate "action verb" label
        # that would read oddly as a *current status* description.
        return f"kf_mglru_{_classify_mglru(str(raw_value))}"

    def validate(self, value) -> bool:
        return value in (self.CHOICE_ENABLE_ALL, self.CHOICE_DISABLE)

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
        if not force and self.external_change_detected(current.value):
            return OpResult(False, friendly_message="kf_external_change_detected", value=current.value)
        return self._privileged_writer.execute(self.id, "restore", None, force=force)


SWAP_READAHEAD_CHOICES = ["0", "1", "2", "3"]
_SWAP_READAHEAD_FRIENDLY = {
    "0": "kf_swap_readahead_0",
    "1": "kf_swap_readahead_1",
    "2": "kf_swap_readahead_2",
    "3": "kf_swap_readahead_3",
}


class SwapReadaheadFeature(KernelFeature):
    """
    vm.page-cluster — how many neighboring pages Linux tries to fetch
    together when reading from swap. A small, fixed set of kernel-
    defined values (0-3, each doubling the page count) — never a
    continuous range, and 0 is never applied automatically just because
    ZRAM is present (per spec: no value is universally better).
    """
    id = "memory.swap_readahead"
    category = "memory"
    technical_name = "vm.page-cluster"
    risk = "low"
    supports_persistence = True

    MIN, MAX = 0, 3

    def _path(self) -> str:
        return os.path.join(self.proc_root, "sys", "vm", "page-cluster")

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
        return list(SWAP_READAHEAD_CHOICES)

    def to_friendly(self, raw_value) -> str:
        return _SWAP_READAHEAD_FRIENDLY.get(str(raw_value), str(raw_value))

    def validate(self, value) -> bool:
        try:
            v = int(value)
        except (TypeError, ValueError):
            return False
        return self.MIN <= v <= self.MAX

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


class ZswapFeature(KernelFeature):
    """
    Zswap — compresses pages in RAM before they'd otherwise go to swap
    (as opposed to ZRAM, which IS a compressed swap device). Purely a
    kernel module parameter, no package involved; only shown at all if
    the kernel actually has zswap compiled in.
    """
    id = "memory.zswap"
    category = "memory"
    technical_name = "Zswap"
    risk = "low"
    supports_persistence = False

    def _path(self) -> str:
        return os.path.join(self.sys_root, "module", "zswap", "parameters", "enabled")

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
                raw = f.read().strip()
        except FileNotFoundError:
            return OpResult(False, friendly_message="kf_unsupported_kernel")
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except OSError as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
        return OpResult(True, value=raw in ("Y", "1", "y", "true"))

    def to_friendly(self, raw_value) -> str:
        return "kf_zswap_on" if raw_value else "kf_zswap_off"

    def validate(self, value) -> bool:
        return isinstance(value, bool)

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
        if not force and self.external_change_detected(current.value):
            return OpResult(False, friendly_message="kf_external_change_detected", value=current.value)
        return self._privileged_writer.execute(self.id, "restore", None, force=force)
