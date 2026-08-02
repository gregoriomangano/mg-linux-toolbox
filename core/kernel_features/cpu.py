"""
CPU-level kernel tunables: Turbo Boost, the CPU frequency governor and
Energy Performance Preference (EPP).

Governor and EPP operate on /sys/devices/system/cpu/cpufreq/policy* —
NOT by walking every cpuN — because cpuN/cpufreq is normally just a
symlink into the policy that CPU belongs to (verified: cpu0/cpufreq ->
../cpufreq/policy0 on a real machine). Several cores commonly share one
policy, so writing "for every cpuN" can mean writing the *same*
underlying file several times over. Enumerating policies directly means
exactly one read/write per real, distinct hardware group.
"""
import os

from core.kernel_features.base import KernelFeature, SupportStatus, OpResult


class TurboBoostFeature(KernelFeature):
    """
    Two mutually-exclusive sysfs knobs depending on the CPU driver, with
    OPPOSITE polarity:
      - intel_pstate/no_turbo: 1 = turbo DISABLED, 0 = turbo enabled
      - cpufreq/boost:         0 = turbo DISABLED, 1 = turbo enabled
    Only one of the two normally exists on a given machine. This class
    picks whichever is present and always exposes a single, positive
    concept to the rest of the app: "is turbo enabled?" (bool).
    """
    id = "cpu.turbo_boost"
    category = "cpu"
    technical_name = "CPU Turbo Boost"
    risk = "low"
    supports_persistence = False

    def _mode_and_path(self):
        no_turbo = os.path.join(self.sys_root, "devices", "system", "cpu", "intel_pstate", "no_turbo")
        if os.path.exists(no_turbo):
            return "no_turbo", no_turbo
        boost = os.path.join(self.sys_root, "devices", "system", "cpu", "cpufreq", "boost")
        if os.path.exists(boost):
            return "boost", boost
        return None, None

    def probe(self) -> SupportStatus:
        _, path = self._mode_and_path()
        if not path:
            return SupportStatus.UNSUPPORTED_HARDWARE
        try:
            with open(path):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        except OSError:
            return SupportStatus.UNKNOWN
        return SupportStatus.SUPPORTED_RUNTIME

    def read_current(self) -> OpResult:
        mode, path = self._mode_and_path()
        if not path:
            return OpResult(False, friendly_message="kf_unsupported_hardware")
        try:
            with open(path) as f:
                raw = f.read().strip()
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except OSError as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
        enabled = (raw == "0") if mode == "no_turbo" else (raw == "1")
        return OpResult(True, value=enabled)

    def to_friendly(self, raw_value) -> str:
        return "kf_turbo_on" if raw_value else "kf_turbo_off"

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


class _PolicyBasedFeature(KernelFeature):
    """
    Shared logic for a per-policy cpufreq sysfs value (Governor, EPP).
    Subclasses set FILENAME (the value file) and AVAILABLE_FILENAME (the
    kernel-reported list of valid values for that same file).
    """
    FILENAME = ""
    AVAILABLE_FILENAME = ""

    def _policy_dirs(self):
        base = os.path.join(self.sys_root, "devices", "system", "cpu", "cpufreq")
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            return []
        dirs = []
        for name in entries:
            if not name.startswith("policy"):
                continue
            d = os.path.join(base, name)
            if os.path.isfile(os.path.join(d, self.FILENAME)):
                dirs.append(d)
        return dirs

    def probe(self) -> SupportStatus:
        dirs = self._policy_dirs()
        if not dirs:
            return SupportStatus.UNSUPPORTED_HARDWARE
        try:
            with open(os.path.join(dirs[0], self.FILENAME)):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        except OSError:
            return SupportStatus.UNKNOWN
        return SupportStatus.SUPPORTED_RUNTIME

    def read_current(self) -> OpResult:
        dirs = self._policy_dirs()
        if not dirs:
            return OpResult(False, friendly_message="kf_unsupported_hardware")
        values = set()
        try:
            for d in dirs:
                with open(os.path.join(d, self.FILENAME)) as f:
                    values.add(f.read().strip())
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except OSError as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
        if len(values) == 1:
            return OpResult(True, value=values.pop())
        # Policies disagree — real state, not an error; the UI shows this
        # plainly ("Impostazioni diverse tra i gruppi di core") rather
        # than silently picking one policy's value.
        return OpResult(True, value="mixed")

    def read_available(self):
        dirs = self._policy_dirs()
        if not dirs:
            return None
        try:
            with open(os.path.join(dirs[0], self.AVAILABLE_FILENAME)) as f:
                return f.read().split()
        except OSError:
            return None

    def driver_name(self) -> str:
        """Best-effort scaling_driver of the first policy, for optional
        technical-detail display — never required for correctness."""
        dirs = self._policy_dirs()
        if not dirs:
            return ""
        try:
            with open(os.path.join(dirs[0], "scaling_driver")) as f:
                return f.read().strip()
        except OSError:
            return ""

    # Governor/EPP values that have an honest, universal plain-language
    # translation. Anything else (e.g. "schedutil", "ondemand", the EPP
    # value "power") passes through unchanged — never an invented name.
    _FRIENDLY_KEYS = {
        "performance": "cpu_val_performance",
        "powersave": "cpu_val_powersave",
        "balance_performance": "cpu_val_balance_performance",
        "balance_power": "cpu_val_balance_power",
    }

    def to_friendly(self, raw_value) -> str:
        return self._FRIENDLY_KEYS.get(raw_value, raw_value)

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
        if not force and self.external_change_detected(current.value):
            return OpResult(False, friendly_message="kf_external_change_detected", value=current.value)
        return self._privileged_writer.execute(self.id, "restore", None, force=force)


class GovernorFeature(_PolicyBasedFeature):
    id = "cpu.governor"
    category = "cpu"
    technical_name = "CPU scaling governor"
    risk = "medium"
    supports_persistence = False
    FILENAME = "scaling_governor"
    AVAILABLE_FILENAME = "scaling_available_governors"


class EPPFeature(_PolicyBasedFeature):
    """
    Energy Performance Preference — only meaningful (and only exposed by
    the kernel at all) when the active cpufreq driver supports it
    (intel_pstate in "active" mode, or amd-pstate). No fixed value list:
    whatever the driver actually reports via
    energy_performance_available_preferences is what gets offered. If
    that list has exactly one entry, the UI treats this as read-only —
    there is nothing a user could meaningfully choose.
    """
    id = "cpu.epp"
    category = "cpu"
    technical_name = "Energy Performance Preference (EPP)"
    risk = "low"
    supports_persistence = False
    FILENAME = "energy_performance_preference"
    AVAILABLE_FILENAME = "energy_performance_available_preferences"


# Fraction of the real, detected hardware [hw_min, hw_max] range used by
# each curated profile — never a hardcoded MHz number, always computed
# from what THIS cpu actually reports. "custom" is handled entirely by
# the UI (the user's own min/max, still validated against hw limits).
CPU_FREQ_PROFILES = {
    "power_saving": (0.0, 0.4),
    "balanced": (0.0, 0.75),
    "full_range": (0.0, 1.0),
}


def compute_profile_range(profile: str, hw_min: int, hw_max: int) -> "tuple[int, int] | None":
    """Returns (min, max) in the same units as hw_min/hw_max (kHz, per
    the real sysfs files), or None for an unknown profile name. Never
    claims one profile is universally best — just derives concrete
    numbers from this machine's own real limits."""
    fractions = CPU_FREQ_PROFILES.get(profile)
    if fractions is None or hw_max <= hw_min:
        return None
    lo_frac, hi_frac = fractions
    span = hw_max - hw_min
    lo = hw_min + int(span * lo_frac)
    hi = hw_min + int(span * hi_frac)
    return (min(lo, hi), max(lo, hi))


class CpuFrequencyLimitsFeature(KernelFeature):
    """
    scaling_min_freq/scaling_max_freq across every real cpufreq policy —
    never just cpu0 (several cores commonly share one policy; see the
    module docstring). Deliberately NOT called "overclock": this only
    ever narrows or widens the range within what cpuinfo_min_freq/
    cpuinfo_max_freq already say the hardware supports, never beyond it.
    """
    id = "cpu.frequency_limits"
    category = "cpu"
    technical_name = "CPU frequency minimum and maximum limits"
    risk = "medium"
    supports_persistence = False

    BASE_REL = os.path.join("devices", "system", "cpu", "cpufreq")

    def _base(self) -> str:
        return os.path.join(self.sys_root, self.BASE_REL)

    def _policy_dirs(self):
        try:
            entries = sorted(os.listdir(self._base()))
        except OSError:
            return []
        dirs = []
        for name in entries:
            if not name.startswith("policy"):
                continue
            d = os.path.join(self._base(), name)
            if (os.path.isfile(os.path.join(d, "scaling_min_freq"))
                    and os.path.isfile(os.path.join(d, "scaling_max_freq"))):
                dirs.append((name, d))
        return dirs

    def _read_int(self, path) -> "int | None":
        try:
            with open(path) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def _read_list(self, path) -> list:
        try:
            with open(path) as f:
                return f.read().split()
        except OSError:
            return []

    def probe(self) -> SupportStatus:
        dirs = self._policy_dirs()
        if not dirs:
            return SupportStatus.UNSUPPORTED_HARDWARE
        try:
            with open(os.path.join(dirs[0][1], "scaling_min_freq")):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        except OSError:
            return SupportStatus.UNKNOWN
        return SupportStatus.SUPPORTED_RUNTIME

    def read_current(self) -> OpResult:
        dirs = self._policy_dirs()
        if not dirs:
            return OpResult(False, friendly_message="kf_unsupported_hardware")
        policies = []
        for name, d in dirs:
            entry = {
                "name": name,
                "min": self._read_int(os.path.join(d, "scaling_min_freq")),
                "max": self._read_int(os.path.join(d, "scaling_max_freq")),
                "hw_min": self._read_int(os.path.join(d, "cpuinfo_min_freq")),
                "hw_max": self._read_int(os.path.join(d, "cpuinfo_max_freq")),
                "affected_cpus": self._read_list(os.path.join(d, "affected_cpus")),
                "related_cpus": self._read_list(os.path.join(d, "related_cpus")),
            }
            if entry["min"] is None or entry["max"] is None:
                return OpResult(False, friendly_message="kf_err_generic",
                                 technical_detail=f"could not read {name}")
            policies.append(entry)
        return OpResult(True, value={"policies": policies})

    def hw_bounds(self, policies: list) -> "tuple[int, int] | None":
        """The intersection every policy can honestly support: the
        highest of all hw_min values and the lowest of all hw_max
        values — never assuming every policy shares identical hardware
        limits (heterogeneous/big.LITTLE-style CPUs don't)."""
        mins = [p["hw_min"] for p in policies if p["hw_min"] is not None]
        maxs = [p["hw_max"] for p in policies if p["hw_max"] is not None]
        if not mins or not maxs:
            return None
        return (max(mins), min(maxs))

    def to_friendly(self, raw_value) -> str:
        return str(raw_value)

    def validate_range(self, min_khz: int, max_khz: int, policies: list) -> bool:
        if min_khz > max_khz:
            return False
        bounds = self.hw_bounds(policies)
        if bounds is None:
            return False
        hw_min, hw_max = bounds
        return hw_min <= min_khz <= hw_max and hw_min <= max_khz <= hw_max

    def validate(self, value) -> bool:
        try:
            min_khz, max_khz = int(value["min"]), int(value["max"])
        except (TypeError, KeyError, ValueError):
            return False
        current = self.read_current()
        if not current.ok:
            return False
        return self.validate_range(min_khz, max_khz, current.value["policies"])

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
        # "Current" for external-change purposes: the set of (min, max)
        # pairs across policies, order-independent.
        current_pairs = sorted((p["min"], p["max"]) for p in current.value["policies"])
        if not force and self.external_change_detected(current_pairs):
            return OpResult(False, friendly_message="kf_external_change_detected", value=current_pairs)
        return self._privileged_writer.execute(self.id, "restore", None, force=force)
