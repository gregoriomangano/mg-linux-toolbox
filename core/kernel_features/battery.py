"""
Battery and platform power features: status (read-only), charge
thresholds, ACPI platform profile, and suspend mode. All read directly
from /sys — never assume a battery exists, never assume a specific
vendor's threshold file names.
"""
import os

from core.kernel_features.base import KernelFeature, SupportStatus, OpResult


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


class BatteryStatusFeature(KernelFeature):
    """
    Read-only battery snapshot. Handles both the energy_* (µWh, most
    common on modern ACPI batteries) and charge_* (µAh, older/some
    vendors) sysfs attribute families — a battery exposes one family or
    the other, never both, so every reading here checks first.
    """
    id = "battery.status"
    category = "battery"
    technical_name = "Battery status (/sys/class/power_supply)"
    risk = "low"
    read_only = True
    supports_persistence = False

    def _battery_dirs(self) -> list:
        base = os.path.join(self.sys_root, "class", "power_supply")
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return []
        dirs = []
        for name in names:
            if _read(os.path.join(base, name, "type")) == "Battery":
                dirs.append(os.path.join(base, name))
        return dirs

    def probe(self) -> SupportStatus:
        return SupportStatus.SUPPORTED_READ_ONLY if self._battery_dirs() else SupportStatus.UNSUPPORTED_HARDWARE

    def read_current(self) -> OpResult:
        dirs = self._battery_dirs()
        if not dirs:
            return OpResult(False, friendly_message="battery_not_present")
        d = dirs[0]  # first/primary battery
        data = {}

        percent = _read(os.path.join(d, "capacity"))
        if percent:
            data["percent"] = int(percent)

        status = _read(os.path.join(d, "status"))  # Charging/Discharging/Full/Not charging/Unknown
        if status:
            data["status"] = status

        full = _read(os.path.join(d, "energy_full")) or _read(os.path.join(d, "charge_full"))
        full_design = _read(os.path.join(d, "energy_full_design")) or _read(os.path.join(d, "charge_full_design"))
        now = _read(os.path.join(d, "energy_now")) or _read(os.path.join(d, "charge_now"))
        if full:
            data["capacity_now"] = int(full)
        if full_design:
            data["capacity_design"] = int(full_design)
        if now:
            data["energy_now"] = int(now)
        if full and full_design and int(full_design) > 0:
            data["health_percent"] = round(int(full) / int(full_design) * 100, 1)

        cycles = _read(os.path.join(d, "cycle_count"))
        if cycles and cycles != "0":
            data["cycle_count"] = int(cycles)

        temp = _read(os.path.join(d, "temp"))  # tenths of a degree C
        if temp:
            try:
                data["temperature_c"] = round(int(temp) / 10, 1)
            except ValueError:
                pass

        power_now = _read(os.path.join(d, "power_now")) or _read(os.path.join(d, "current_now"))
        if power_now:
            data["power_now_uw"] = int(power_now)

        # Rough remaining-time estimate when we have enough data —
        # never presented as exact, just "about".
        if now and power_now and int(power_now) > 0 and status == "Discharging":
            hours = int(now) / int(power_now)
            data["estimated_hours_remaining"] = round(hours, 1)

        return OpResult(True, value=data)


class BatteryThresholdFeature(KernelFeature):
    """
    Charge-stop/charge-start thresholds — real hardware/firmware feature
    on many laptops (exposed under different file names depending on the
    driver providing it: generic power_supply class extension,
    ideapad_laptop, thinkpad_acpi, etc.). Only shown if at least one such
    pair genuinely exists. Whatever we write is inherently what the
    firmware/EC already persists on its own — we never add extra
    persistence machinery on top.
    """
    id = "battery.charge_threshold"
    category = "battery"
    technical_name = "Battery charge thresholds"
    risk = "low"
    supports_persistence = False

    # Different vendors/drivers name these differently; try in order.
    _START_NAMES = ("charge_control_start_threshold", "charge_start_threshold")
    _END_NAMES = ("charge_control_end_threshold", "charge_stop_threshold")

    def _battery_dir(self) -> str:
        base = os.path.join(self.sys_root, "class", "power_supply")
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return ""
        for name in names:
            d = os.path.join(base, name)
            if _read(os.path.join(d, "type")) != "Battery":
                continue
            if self._threshold_paths(d) != (None, None):
                return d
        return ""

    def _threshold_paths(self, batt_dir: str):
        start = next((os.path.join(batt_dir, n) for n in self._START_NAMES
                      if os.path.isfile(os.path.join(batt_dir, n))), None)
        end = next((os.path.join(batt_dir, n) for n in self._END_NAMES
                    if os.path.isfile(os.path.join(batt_dir, n))), None)
        return start, end

    def probe(self) -> SupportStatus:
        d = self._battery_dir()
        if not d:
            return SupportStatus.UNSUPPORTED_HARDWARE
        start, end = self._threshold_paths(d)
        try:
            with open(start):
                pass
            with open(end):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        except OSError:
            return SupportStatus.UNKNOWN
        return SupportStatus.SUPPORTED_RUNTIME

    def read_current(self) -> OpResult:
        d = self._battery_dir()
        if not d:
            return OpResult(False, friendly_message="kf_unsupported_hardware")
        start_path, end_path = self._threshold_paths(d)
        start = _read(start_path)
        end = _read(end_path)
        if not start or not end:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail="threshold files unreadable")
        return OpResult(True, value={"start": int(start), "end": int(end)})

    def to_friendly(self, raw_value) -> str:
        return f"{raw_value['start']}% – {raw_value['end']}%"

    def validate(self, value) -> bool:
        if not isinstance(value, dict):
            return False
        start, end = value.get("start"), value.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            return False
        return 0 <= start < end <= 100

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


class PlatformProfileFeature(KernelFeature):
    """
    ACPI Platform Profile (/sys/firmware/acpi/platform_profile) — a
    firmware-level hint distinct from userspace power-profile daemons
    (power-profiles-daemon actually reads/writes this same file on
    supported hardware, but this row exists for machines/kernels where
    a daemon isn't managing it, and to show the raw kernel capability).
    """
    id = "battery.platform_profile"
    category = "battery"
    technical_name = "ACPI Platform Profile"
    risk = "low"
    supports_persistence = False

    _FRIENDLY_KEYS = {
        "low-power": "platform_profile_low_power",
        "quiet": "platform_profile_quiet",
        "cool": "platform_profile_cool",
        "balanced": "platform_profile_balanced",
        "balanced-performance": "platform_profile_balanced_performance",
        "performance": "platform_profile_performance",
    }

    def _path(self) -> str:
        return os.path.join(self.sys_root, "firmware", "acpi", "platform_profile")

    def _choices_path(self) -> str:
        return os.path.join(self.sys_root, "firmware", "acpi", "platform_profile_choices")

    def probe(self) -> SupportStatus:
        if not os.path.isfile(self._path()):
            return SupportStatus.UNSUPPORTED_HARDWARE
        try:
            with open(self._path()):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        return SupportStatus.SUPPORTED_RUNTIME

    def read_current(self) -> OpResult:
        if not os.path.isfile(self._path()):
            return OpResult(False, friendly_message="kf_unsupported_hardware")
        value = _read(self._path())
        if not value:
            return OpResult(False, friendly_message="kf_err_generic")
        return OpResult(True, value=value)

    def read_available(self):
        content = _read(self._choices_path())
        return content.split() if content else None

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


class SuspendModeFeature(KernelFeature):
    """
    /sys/power/mem_sleep — which suspend mode a subsequent "suspend"
    action will use. Writing here does NOT itself suspend the machine,
    it only changes the preference for next time.
    """
    id = "battery.suspend_mode"
    category = "battery"
    technical_name = "Suspend mode (/sys/power/mem_sleep)"
    risk = "medium"
    supports_persistence = False

    _FRIENDLY_KEYS = {
        "s2idle": "suspend_mode_s2idle",
        "shallow": "suspend_mode_shallow",
        "deep": "suspend_mode_deep",
    }

    def _path(self) -> str:
        return os.path.join(self.sys_root, "power", "mem_sleep")

    def probe(self) -> SupportStatus:
        if not os.path.isfile(self._path()):
            return SupportStatus.UNSUPPORTED_KERNEL
        try:
            with open(self._path()):
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

    def to_friendly(self, raw_value) -> str:
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
