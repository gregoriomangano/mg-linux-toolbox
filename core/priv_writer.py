#!/usr/bin/env python3
"""
Privileged writer for M.G Linux Toolbox kernel features.

Invoked as:
    pkexec python3 priv_writer.py <feature_id> <action> <value> <device_id> [force]

SECURITY MODEL (this is the whole point of this file):
- The GUI never sends a filesystem path here — only a feature_id, which
  must be one of the keys in FEATURE_WRITERS below. If it isn't, nothing
  happens.
- Each writer class hardcodes its OWN real path(s)/pattern and validates
  every value itself before writing anything.
- For per-device features (I/O scheduler), device_id is validated against
  a strict identifier pattern AND checked to actually exist under
  /sys/block before it's used to build a path — the GUI cannot supply a
  path, only a short device name.
- "restore" never trusts a target value from the GUI: it re-reads the
  recorded initial value from our own state store.
- Every write is: read current -> record initial (once) -> validate ->
  write -> re-read -> compare -> record result. No shell=True, no string-
  built commands. Almost everything here is plain file I/O; ZramWriter is
  the one exception — enabling ZRAM without requiring zram-tools/
  zram-generator needs modprobe/mkswap/swapon, invoked as fixed argv
  lists (never shell=True, never built from GUI-supplied text).

Prints one JSON object to stdout: {"ok": bool, "value": ...,
"friendly_message": <i18n key>, "technical_detail": <raw text, hidden
behind "Show details" in the UI>}.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from core.persistence.rollback_store import JsonStateStore, FeatureRecord, DEFAULT_STATE_PATH
from core.persistence import sysctl_store
from core.persistence import tmpfiles_store
from core.persistence import selinux_config_store


def _parse_bool(raw_value) -> bool:
    """Values arrive as strings over the CLI (str(True) -> "True")."""
    return str(raw_value).strip().lower() in ("true", "1", "y", "yes")


def ok(value, friendly_message="", technical_detail=""):
    return {"ok": True, "value": value, "friendly_message": friendly_message, "technical_detail": technical_detail}


def err(friendly_message, technical_detail="", value=None):
    return {"ok": False, "value": value, "friendly_message": friendly_message, "technical_detail": technical_detail}


def _note_initial(state: JsonStateStore, key: str, current_value, device_id=None):
    if state.get(key) is None:
        state.put(FeatureRecord(feature_id=key, initial_value=current_value, device_id=device_id))


def _note_applied(state: JsonStateStore, key: str, value, mode: str, device_id=None,
                   size_bytes=None, algorithm=None, priority=None):
    rec = state.get(key) or FeatureRecord(feature_id=key, initial_value=value, device_id=device_id)
    rec.last_applied_value = value
    rec.mode = mode
    if device_id is not None:
        rec.device_id = device_id
    if size_bytes is not None:
        rec.size_bytes = size_bytes
    if algorithm is not None:
        rec.algorithm = algorithm
    if priority is not None:
        rec.priority = priority
    from core.kernel_features.base import utc_now_iso
    rec.last_applied_at = utc_now_iso()
    state.put(rec)


class SwappinessWriter:
    PATH = "/proc/sys/vm/swappiness"
    MIN, MAX = 0, 200
    KEY = "memory.swappiness"

    def _read(self) -> int:
        with open(self.PATH) as f:
            return int(f.read().strip())

    def _validate(self, raw_value):
        try:
            v = int(raw_value)
        except (TypeError, ValueError):
            return None
        return v if self.MIN <= v <= self.MAX else None

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        v = self._validate(raw_value)
        if v is None:
            return err("kf_err_invalid_value", f"value {raw_value!r} out of range 0-200")
        try:
            current = self._read()
            _note_initial(state, self.KEY, current)
            with open(self.PATH, "w") as f:
                f.write(str(v))
            reread = self._read()
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != v:
            return err("kf_err_write_mismatch", f"wrote {v}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def apply_persistent(self, raw_value, device_id, force, state: JsonStateStore):
        temp = self.apply_temporary(raw_value, device_id, force, state)
        if not temp["ok"]:
            return temp
        v = temp["value"]
        try:
            sysctl_store.write_key("vm.swappiness", str(v))
        except (OSError, ValueError) as e:
            return err("kf_err_persist_write", str(e))
        reread_runtime = self._read()
        saved = sysctl_store.read_key("vm.swappiness")
        if reread_runtime != v or saved != str(v):
            return err("kf_err_persist_mismatch", f"runtime={reread_runtime} saved={saved} expected={v}")
        _note_applied(state, self.KEY, v, "persistent")
        return ok(v)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            current = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        v = rec.initial_value
        try:
            with open(self.PATH, "w") as f:
                f.write(str(v))
            reread = self._read()
            sysctl_store.remove_key("vm.swappiness")
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class IOSchedulerWriter:
    KEY = "storage.io_scheduler"
    _DEVICE_RE = re.compile(r"^[a-zA-Z0-9_]+$")

    def _validate_device(self, device_id):
        if not device_id or not self._DEVICE_RE.fullmatch(device_id):
            return None
        if device_id.startswith(("loop", "ram", "zram", "dm-")):
            return None
        path = f"/sys/block/{device_id}/queue/scheduler"
        return path if os.path.isfile(path) else None

    def _read(self, path):
        with open(path) as f:
            content = f.read().strip()
        tokens = [t.strip("[]") for t in content.split()]
        current = next((t.strip("[]") for t in content.split() if t.startswith("[")), None)
        return tokens, current

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        path = self._validate_device(device_id)
        if not path:
            return err("kf_err_device_not_found", f"device_id={device_id!r}")
        try:
            available, current = self._read(path)
        except OSError as e:
            return err("kf_err_generic", str(e))
        if raw_value not in available:
            return err("kf_err_invalid_value", f"{raw_value!r} not in {available}")
        key = f"{self.KEY}:{device_id}"
        _note_initial(state, key, current, device_id=device_id)
        try:
            with open(path, "w") as f:
                f.write(raw_value)
            _, reread = self._read(path)
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != raw_value:
            return err("kf_err_write_mismatch", f"wrote {raw_value}, kernel reports {reread}")
        _note_applied(state, key, reread, "temporary", device_id=device_id)
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        path = self._validate_device(device_id)
        if not path:
            return err("kf_err_device_not_found", f"device_id={device_id!r}")
        key = f"{self.KEY}:{device_id}"
        rec = state.get(key)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            _, current = self._read(path)
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        try:
            with open(path, "w") as f:
                f.write(rec.initial_value)
            _, reread = self._read(path)
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, key, reread, "restored", device_id=device_id)
        return ok(reread)


class TurboBoostWriter:
    KEY = "cpu.turbo_boost"
    NO_TURBO_PATH = "/sys/devices/system/cpu/intel_pstate/no_turbo"
    BOOST_PATH = "/sys/devices/system/cpu/cpufreq/boost"

    def _mode_and_path(self):
        if os.path.isfile(self.NO_TURBO_PATH):
            return "no_turbo", self.NO_TURBO_PATH
        if os.path.isfile(self.BOOST_PATH):
            return "boost", self.BOOST_PATH
        return None, None

    def _read_enabled(self, mode, path) -> bool:
        with open(path) as f:
            raw = f.read().strip()
        return (raw == "0") if mode == "no_turbo" else (raw == "1")

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        mode, path = self._mode_and_path()
        if not path:
            return err("kf_unsupported_hardware")
        want_enabled = _parse_bool(raw_value)
        try:
            current = self._read_enabled(mode, path)
            _note_initial(state, self.KEY, current)
            write_val = ("0" if want_enabled else "1") if mode == "no_turbo" else ("1" if want_enabled else "0")
            with open(path, "w") as f:
                f.write(write_val)
            reread = self._read_enabled(mode, path)
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != want_enabled:
            return err("kf_err_write_mismatch", f"wanted turbo={want_enabled}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        mode, path = self._mode_and_path()
        if not path:
            return err("kf_unsupported_hardware")
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            current = self._read_enabled(mode, path)
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        want_enabled = rec.initial_value
        try:
            write_val = ("0" if want_enabled else "1") if mode == "no_turbo" else ("1" if want_enabled else "0")
            with open(path, "w") as f:
                f.write(write_val)
            reread = self._read_enabled(mode, path)
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class _PolicyBasedWriter:
    """
    Shared logic for anything applied per cpufreq POLICY (Governor, EPP)
    — not per cpuN. cpuN/cpufreq is normally just a symlink into the
    policy that core belongs to, so several cores can share one policy;
    writing "for every cpuN" can mean writing the same real file more
    than once. Enumerating /sys/devices/system/cpu/cpufreq/policy*
    directly means exactly one read/write per distinct hardware group.
    Subclasses set FILENAME, AVAILABLE_FILENAME and KEY.
    """
    FILENAME = ""
    AVAILABLE_FILENAME = ""
    KEY = ""
    BASE = "/sys/devices/system/cpu/cpufreq"

    def _dirs(self):
        try:
            entries = sorted(os.listdir(self.BASE))
        except OSError:
            return []
        dirs = []
        for name in entries:
            if not name.startswith("policy"):
                continue
            d = os.path.join(self.BASE, name)
            if os.path.isfile(os.path.join(d, self.FILENAME)):
                dirs.append(d)
        return dirs

    def _read_one(self, cpufreq_dir):
        with open(os.path.join(cpufreq_dir, self.FILENAME)) as f:
            return f.read().strip()

    def _read_available(self, cpufreq_dir, available_filename):
        try:
            with open(os.path.join(cpufreq_dir, available_filename)) as f:
                return f.read().split()
        except OSError:
            return []

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        dirs = self._dirs()
        if not dirs:
            return err("kf_unsupported_hardware")
        available = self._read_available(dirs[0], self.AVAILABLE_FILENAME)
        if raw_value not in available:
            return err("kf_err_invalid_value", f"{raw_value!r} not in {available}")

        current_values = {d: self._read_one(d) for d in dirs}
        # Record initial value once, from whichever core disagreed least —
        # in practice all cores normally agree at first run.
        initial = next(iter(current_values.values()))
        _note_initial(state, self.KEY, initial)

        written, failed = [], None
        try:
            for d in dirs:
                with open(os.path.join(d, self.FILENAME), "w") as f:
                    f.write(raw_value)
                written.append(d)
        except OSError as e:
            failed = str(e)

        reread = {d: self._read_one(d) for d in dirs}
        if failed or any(v != raw_value for v in reread.values()):
            detail = failed or f"wrote {raw_value!r}, kernel reports {reread}"
            return err("kf_err_write_mismatch", detail)
        _note_applied(state, self.KEY, raw_value, "temporary")
        return ok(raw_value)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        dirs = self._dirs()
        if not dirs:
            return err("kf_unsupported_hardware")
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        current_values = {d: self._read_one(d) for d in dirs}
        distinct = set(current_values.values())
        current = distinct.pop() if len(distinct) == 1 else "mixed"
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        want = rec.initial_value
        try:
            for d in dirs:
                with open(os.path.join(d, self.FILENAME), "w") as f:
                    f.write(want)
        except OSError as e:
            return err("kf_err_generic", str(e))
        reread = {d: self._read_one(d) for d in dirs}
        distinct = set(reread.values())
        final = distinct.pop() if len(distinct) == 1 else "mixed"
        _note_applied(state, self.KEY, final, "restored")
        return ok(final)


class GovernorWriter(_PolicyBasedWriter):
    FILENAME = "scaling_governor"
    AVAILABLE_FILENAME = "scaling_available_governors"
    KEY = "cpu.governor"


class EPPWriter(_PolicyBasedWriter):
    FILENAME = "energy_performance_preference"
    AVAILABLE_FILENAME = "energy_performance_available_preferences"
    KEY = "cpu.epp"


class THPWriter:
    KEY = "memory.thp"
    PATH = "/sys/kernel/mm/transparent_hugepage/enabled"

    def _read(self):
        with open(self.PATH) as f:
            content = f.read().strip()
        tokens = [t.strip("[]") for t in content.split()]
        current = next((t.strip("[]") for t in content.split() if t.startswith("[")), None)
        return tokens, current

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        try:
            available, current = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if raw_value not in available:
            return err("kf_err_invalid_value", f"{raw_value!r} not in {available}")
        _note_initial(state, self.KEY, current)
        try:
            with open(self.PATH, "w") as f:
                f.write(raw_value)
            _, reread = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != raw_value:
            return err("kf_err_write_mismatch", f"wrote {raw_value}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            _, current = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        try:
            with open(self.PATH, "w") as f:
                f.write(rec.initial_value)
            _, reread = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class ZswapWriter:
    KEY = "memory.zswap"
    PATH = "/sys/module/zswap/parameters/enabled"

    def _read_enabled(self) -> bool:
        with open(self.PATH) as f:
            raw = f.read().strip()
        return raw in ("Y", "1", "y", "true")

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        want_enabled = _parse_bool(raw_value)
        try:
            current = self._read_enabled()
            _note_initial(state, self.KEY, current)
            with open(self.PATH, "w") as f:
                f.write("Y" if want_enabled else "N")
            reread = self._read_enabled()
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != want_enabled:
            return err("kf_err_write_mismatch", f"wanted zswap={want_enabled}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            current = self._read_enabled()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        want_enabled = rec.initial_value
        try:
            with open(self.PATH, "w") as f:
                f.write("Y" if want_enabled else "N")
            reread = self._read_enabled()
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class ZramWriter:
    """
    The one writer here that runs real commands instead of plain file
    I/O — enabling ZRAM without a helper package needs modprobe (load the
    module), a disksize write, mkswap and swapon; all fixed argv lists,
    nothing built from GUI-supplied text.

    Absolute rule: never touch a zram device we didn't create ourselves.
    A new device is picked dynamically (via zram-control's hot_add, or a
    fallback scan) rather than assuming zram0 is free — something else
    (systemd-zram-generator, zram-tools, the distro) may already own it.
    Disabling/restoring only ever acts on the device_id our own state
    store recorded when WE created it.
    """
    KEY = "memory.zram"
    SIZE_FRACTION_OF_RAM = 0.5  # deliberately conservative default
    SWAPS_PATH = "/proc/swaps"
    MEMINFO_PATH = "/proc/meminfo"
    SYS_BLOCK_BASE = "/sys/block"  # overridden by tests to a tmp dir
    ZRAM_CONTROL_HOT_ADD = "/sys/class/zram-control/hot_add"  # overridden by tests
    MAX_FALLBACK_SCAN = 8

    def _mem_total_bytes(self) -> int:
        with open(self.MEMINFO_PATH) as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        return 0

    def _active_devices(self) -> list:
        with open(self.SWAPS_PATH) as f:
            lines = f.read().splitlines()
        devices = []
        for line in lines[1:]:
            parts = line.split()
            if parts and "zram" in parts[0]:
                devices.append(os.path.basename(parts[0]))
        return devices

    def _zram_active(self) -> bool:
        return bool(self._active_devices())

    def _pick_free_device(self) -> "str | None":
        active = set(self._active_devices())
        if os.path.isfile(self.ZRAM_CONTROL_HOT_ADD):
            try:
                with open(self.ZRAM_CONTROL_HOT_ADD) as f:
                    idx = f.read().strip()
                return f"zram{idx}"
            except OSError:
                pass
        # Fallback for kernels without dynamic zram-control: reuse an
        # existing-but-idle zram block device (module loaded with
        # num_devices > 1), never one already active as swap.
        for i in range(self.MAX_FALLBACK_SCAN):
            name = f"zram{i}"
            if name in active:
                continue
            if os.path.isdir(os.path.join(self.SYS_BLOCK_BASE, name)):
                return name
        return None

    def _enable(self):
        subprocess.run(["modprobe", "zram"], check=True, capture_output=True, timeout=10)
        device = self._pick_free_device()
        if device is None:
            raise RuntimeError("no free zram device available")
        size = max(int(self._mem_total_bytes() * self.SIZE_FRACTION_OF_RAM), 1)
        with open(os.path.join(self.SYS_BLOCK_BASE, device, "disksize"), "w") as f:
            f.write(str(size))
        subprocess.run(["mkswap", f"/dev/{device}"], check=True, capture_output=True, timeout=15)
        subprocess.run(["swapon", f"/dev/{device}"], check=True, capture_output=True, timeout=15)
        return device, size

    def _disable(self, device: str):
        subprocess.run(["swapoff", f"/dev/{device}"], check=True, capture_output=True, timeout=15)

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        want_on = _parse_bool(raw_value)
        try:
            active_devices = self._active_devices()
        except OSError as e:
            return err("kf_err_generic", str(e))
        current = bool(active_devices)
        rec = state.get(self.KEY)

        # Refuse outright if something is active that we didn't create —
        # this mirrors the unprivileged-side check but never trusts the
        # caller to have done it (defense in depth).
        if current and (rec is None or rec.device_id not in active_devices):
            return err("kf_zram_externally_owned")

        _note_initial(state, self.KEY, current)
        if want_on == current:
            _note_applied(state, self.KEY, current, "temporary")
            return ok(current)

        device, size = None, None
        try:
            if want_on:
                device, size = self._enable()
            else:
                self._disable(rec.device_id)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, RuntimeError) as e:
            return err("kf_err_generic", str(e))
        try:
            reread = self._zram_active()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != want_on:
            return err("kf_err_write_mismatch", f"wanted zram={want_on}, actual={reread}")
        _note_applied(state, self.KEY, reread, "temporary",
                      device_id=(device if want_on else None),
                      size_bytes=(size if want_on else None))
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            active_devices = self._active_devices()
        except OSError as e:
            return err("kf_err_generic", str(e))
        current = bool(active_devices)
        if current and rec.device_id not in active_devices and rec.last_applied_value:
            # Something is active but it isn't the device we created —
            # never touch it.
            return err("kf_zram_externally_owned")
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        want_on = rec.initial_value
        new_device, new_size = None, None
        if want_on != current:
            try:
                if want_on:
                    new_device, new_size = self._enable()
                else:
                    self._disable(rec.device_id)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, RuntimeError) as e:
                return err("kf_err_generic", str(e))
        try:
            reread = self._zram_active()
        except OSError as e:
            return err("kf_err_generic", str(e))
        # Restoring to "was on" may land on a different device index than
        # before (can't guarantee reusing the exact same one); record
        # whichever device is genuinely active now, or None once disabled.
        _note_applied(state, self.KEY, reread, "restored",
                      device_id=(new_device if want_on else None),
                      size_bytes=(new_size if want_on else None))
        return ok(reread)


class BatteryThresholdWriter:
    KEY = "battery.charge_threshold"
    _START_NAMES = ("charge_control_start_threshold", "charge_start_threshold")
    _END_NAMES = ("charge_control_end_threshold", "charge_stop_threshold")
    POWER_SUPPLY_BASE = "/sys/class/power_supply"

    def _battery_dir(self):
        try:
            names = sorted(os.listdir(self.POWER_SUPPLY_BASE))
        except OSError:
            return None
        for name in names:
            d = os.path.join(self.POWER_SUPPLY_BASE, name)
            try:
                with open(os.path.join(d, "type")) as f:
                    if f.read().strip() != "Battery":
                        continue
            except OSError:
                continue
            start, end = self._paths(d)
            if start and end:
                return d
        return None

    def _paths(self, batt_dir):
        start = next((os.path.join(batt_dir, n) for n in self._START_NAMES
                      if os.path.isfile(os.path.join(batt_dir, n))), None)
        end = next((os.path.join(batt_dir, n) for n in self._END_NAMES
                    if os.path.isfile(os.path.join(batt_dir, n))), None)
        return start, end

    def _read_both(self, start_path, end_path):
        with open(start_path) as f:
            start = int(f.read().strip())
        with open(end_path) as f:
            end = int(f.read().strip())
        return {"start": start, "end": end}

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        batt_dir = self._battery_dir()
        if not batt_dir:
            return err("kf_unsupported_hardware")
        start_path, end_path = self._paths(batt_dir)
        try:
            value = json.loads(raw_value)
            start, end = int(value["start"]), int(value["end"])
        except (ValueError, KeyError, TypeError):
            return err("kf_err_invalid_value", f"bad payload {raw_value!r}")
        if not (0 <= start < end <= 100):
            return err("kf_err_invalid_value", f"start={start} end={end}")
        try:
            current = self._read_both(start_path, end_path)
            _note_initial(state, self.KEY, current)
            # Write end first, then start — avoids a transient
            # start>=end rejection some EC firmwares enforce mid-write.
            with open(end_path, "w") as f:
                f.write(str(end))
            with open(start_path, "w") as f:
                f.write(str(start))
            reread = self._read_both(start_path, end_path)
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread["start"] != start or reread["end"] != end:
            return err("kf_err_write_mismatch", f"wanted {start}/{end}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        batt_dir = self._battery_dir()
        if not batt_dir:
            return err("kf_unsupported_hardware")
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        start_path, end_path = self._paths(batt_dir)
        try:
            current = self._read_both(start_path, end_path)
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        want = rec.initial_value
        try:
            with open(end_path, "w") as f:
                f.write(str(want["end"]))
            with open(start_path, "w") as f:
                f.write(str(want["start"]))
            reread = self._read_both(start_path, end_path)
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class PlatformProfileWriter:
    KEY = "battery.platform_profile"
    PATH = "/sys/firmware/acpi/platform_profile"
    CHOICES_PATH = "/sys/firmware/acpi/platform_profile_choices"

    def _available(self):
        try:
            with open(self.CHOICES_PATH) as f:
                return f.read().split()
        except OSError:
            return []

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        available = self._available()
        if raw_value not in available:
            return err("kf_err_invalid_value", f"{raw_value!r} not in {available}")
        try:
            with open(self.PATH) as f:
                current = f.read().strip()
            _note_initial(state, self.KEY, current)
            with open(self.PATH, "w") as f:
                f.write(raw_value)
            with open(self.PATH) as f:
                reread = f.read().strip()
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != raw_value:
            return err("kf_err_write_mismatch", f"wrote {raw_value}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            with open(self.PATH) as f:
                current = f.read().strip()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        try:
            with open(self.PATH, "w") as f:
                f.write(rec.initial_value)
            with open(self.PATH) as f:
                reread = f.read().strip()
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class SuspendModeWriter:
    KEY = "battery.suspend_mode"
    PATH = "/sys/power/mem_sleep"

    def _read(self):
        with open(self.PATH) as f:
            content = f.read().strip()
        tokens = [t.strip("[]") for t in content.split()]
        current = next((t.strip("[]") for t in content.split() if t.startswith("[")), None)
        return tokens, current

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        try:
            available, current = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if raw_value not in available:
            return err("kf_err_invalid_value", f"{raw_value!r} not in {available}")
        _note_initial(state, self.KEY, current)
        try:
            with open(self.PATH, "w") as f:
                f.write(raw_value)
            _, reread = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != raw_value:
            return err("kf_err_write_mismatch", f"wrote {raw_value}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            _, current = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        try:
            with open(self.PATH, "w") as f:
                f.write(rec.initial_value)
            _, reread = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class DevicePowerWriter:
    """
    Shared writer for both power/wakeup and power/control toggles.
    `raw_value` is "<bus>:<device_id>:<setting>" — the GUI never sends a
    path; this re-derives and re-validates the real sysfs path itself,
    including re-running the exact same classification the read side
    used, so a stale/forged device_id can't reach an unintended file.
    """
    KEY_PREFIX = "device_power"
    _ALLOWED_BUSES = ("usb", "pci", "acpi", "serio")
    _DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

    def _resolve(self, bus, device_id, filename):
        if bus not in self._ALLOWED_BUSES:
            return None
        if not device_id or not self._DEVICE_ID_RE.fullmatch(device_id):
            return None
        base = f"/sys/bus/{bus}/devices"
        dev_dir = os.path.join(base, device_id)
        # Refuse to follow a symlink outside the expected bus tree.
        real_base = os.path.realpath(base)
        real_dev = os.path.realpath(dev_dir)
        if not real_dev.startswith(real_base + os.sep):
            return None
        path = os.path.join(dev_dir, "power", filename)
        return path if os.path.isfile(path) else None

    def _parse(self, raw_value):
        parts = raw_value.split(":", 2)
        if len(parts) != 3:
            return None, None, None
        return parts[0], parts[1], parts[2]

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        bus, dev_id, setting = self._parse(raw_value)
        if bus is None:
            return err("kf_err_invalid_value", f"malformed payload {raw_value!r}")
        is_wakeup = setting in ("enabled", "disabled")
        filename = "wakeup" if is_wakeup else "control"
        if not is_wakeup and setting not in ("auto", "on"):
            return err("kf_err_invalid_value", f"unknown setting {setting!r}")
        path = self._resolve(bus, dev_id, filename)
        if path is None:
            return err("kf_err_device_not_found", f"{bus}:{dev_id}")
        key = f"{self.KEY_PREFIX}.{filename}:{bus}:{dev_id}"
        try:
            with open(path) as f:
                current = f.read().strip()
            _note_initial(state, key, current, device_id=f"{bus}:{dev_id}")
            with open(path, "w") as f:
                f.write(setting)
            with open(path) as f:
                reread = f.read().strip()
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != setting:
            return err("kf_err_write_mismatch", f"wrote {setting}, kernel reports {reread}")
        _note_applied(state, key, reread, "temporary", device_id=f"{bus}:{dev_id}")
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        bus, dev_id, setting = self._parse(raw_value)
        if bus is None:
            return err("kf_err_invalid_value", f"malformed payload {raw_value!r}")
        filename = "wakeup" if setting in ("enabled", "disabled") else "control"
        path = self._resolve(bus, dev_id, filename)
        if path is None:
            return err("kf_err_device_not_found", f"{bus}:{dev_id}")
        key = f"{self.KEY_PREFIX}.{filename}:{bus}:{dev_id}"
        rec = state.get(key)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            with open(path) as f:
                current = f.read().strip()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        try:
            with open(path, "w") as f:
                f.write(rec.initial_value)
            with open(path) as f:
                reread = f.read().strip()
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, key, reread, "restored", device_id=f"{bus}:{dev_id}")
        return ok(reread)


class AudioPowerSaveWriter:
    KEY = "audio.power_save"
    PATH = "/sys/module/snd_hda_intel/parameters/power_save"
    CONTROLLER_PATH = "/sys/module/snd_hda_intel/parameters/power_save_controller"
    MIN, MAX = 0, 3600

    def _read(self):
        with open(self.PATH) as f:
            seconds = int(f.read().strip())
        controller = None
        if os.path.isfile(self.CONTROLLER_PATH):
            with open(self.CONTROLLER_PATH) as f:
                controller = f.read().strip() in ("Y", "1", "y")
        return {"seconds": seconds, "controller": controller}

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        try:
            value = json.loads(raw_value)
            seconds = int(value["seconds"])
            controller = value.get("controller")
        except (ValueError, KeyError, TypeError):
            return err("kf_err_invalid_value", f"bad payload {raw_value!r}")
        if not (self.MIN <= seconds <= self.MAX):
            return err("kf_err_invalid_value", f"seconds={seconds}")
        try:
            current = self._read()
            _note_initial(state, self.KEY, current)
            with open(self.PATH, "w") as f:
                f.write(str(seconds))
            if controller is not None and os.path.isfile(self.CONTROLLER_PATH):
                with open(self.CONTROLLER_PATH, "w") as f:
                    f.write("Y" if controller else "N")
            reread = self._read()
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread["seconds"] != seconds:
            return err("kf_err_write_mismatch", f"wanted {seconds}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            current = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        want = rec.initial_value
        try:
            with open(self.PATH, "w") as f:
                f.write(str(want["seconds"]))
            if want.get("controller") is not None and os.path.isfile(self.CONTROLLER_PATH):
                with open(self.CONTROLLER_PATH, "w") as f:
                    f.write("Y" if want["controller"] else "N")
            reread = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class KsmWriter:
    KEY = "virt.ksm"
    PATH = "/sys/kernel/mm/ksm/run"

    def _read_enabled(self) -> bool:
        with open(self.PATH) as f:
            raw = f.read().strip()
        return raw in ("1", "2")

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        if not os.path.isfile(self.PATH):
            return err("kf_unsupported_kernel")
        want_enabled = _parse_bool(raw_value)
        try:
            current = self._read_enabled()
            _note_initial(state, self.KEY, current)
            with open(self.PATH, "w") as f:
                f.write("1" if want_enabled else "0")
            reread = self._read_enabled()
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != want_enabled:
            return err("kf_err_write_mismatch", f"wanted ksm={want_enabled}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def apply_persistent(self, raw_value, device_id, force, state: JsonStateStore):
        """Persists across reboot via a systemd-tmpfiles 'w' line — KSM's
        /sys/kernel/mm/ksm/run has no /proc/sys equivalent, so
        sysctl_store.py doesn't apply here (see tmpfiles_store.py)."""
        if not os.path.isfile(self.PATH):
            return err("kf_unsupported_kernel")
        want_enabled = _parse_bool(raw_value)
        try:
            current = self._read_enabled()
            _note_initial(state, self.KEY, current)
            with open(self.PATH, "w") as f:
                f.write("1" if want_enabled else "0")
            reread = self._read_enabled()
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != want_enabled:
            return err("kf_err_write_mismatch", f"wanted ksm={want_enabled}, kernel reports {reread}")
        try:
            tmpfiles_store.write_value(self.PATH, "1" if want_enabled else "0")
        except (OSError, ValueError) as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "persistent")
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        if not os.path.isfile(self.PATH):
            return err("kf_unsupported_kernel")
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            current = self._read_enabled()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        want_enabled = rec.initial_value
        try:
            with open(self.PATH, "w") as f:
                f.write("1" if want_enabled else "0")
            reread = self._read_enabled()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if rec.mode == "persistent":
            try:
                tmpfiles_store.remove_value(self.PATH)
            except OSError as e:
                return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class SELinuxWriter:
    """Uses the real `setenforce` userspace tool (the same one every
    other SELinux-aware tool on the system uses) rather than writing
    /sys/fs/selinux/enforce by hand, so any kernel-side edge case
    setenforce itself already handles (e.g. refusing 1 when SELinux was
    disabled at boot) is inherited for free."""
    KEY = "selinux.mode"
    ENFORCE_PATH = "/sys/fs/selinux/enforce"

    def _read(self) -> str:
        with open(self.ENFORCE_PATH) as f:
            return "enforcing" if f.read().strip() == "1" else "permissive"

    def _validate(self, raw_value):
        return raw_value if raw_value in ("enforcing", "permissive") else None

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        mode = self._validate(raw_value)
        if mode is None:
            return err("kf_err_invalid_value", f"unsupported mode {raw_value!r}")
        try:
            current = self._read()
            _note_initial(state, self.KEY, current)
            result = subprocess.run(["setenforce", "1" if mode == "enforcing" else "0"],
                                     capture_output=True, text=True, timeout=10)
            reread = self._read()
        except (OSError, subprocess.TimeoutExpired) as e:
            return err("kf_err_generic", str(e))
        if result.returncode != 0:
            return err("kf_err_permission", result.stderr.strip())
        if reread != mode:
            return err("kf_err_write_mismatch", f"wanted {mode}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def apply_persistent(self, raw_value, device_id, force, state: JsonStateStore):
        mode = self._validate(raw_value)
        if mode is None:
            return err("kf_err_invalid_value", f"unsupported mode {raw_value!r}")
        temp_result = self.apply_temporary(raw_value, device_id, force, state)
        if not temp_result.get("ok"):
            return temp_result
        try:
            selinux_config_store.write_mode(mode)
        except (OSError, ValueError) as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, temp_result["value"], "persistent")
        return ok(temp_result["value"])

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            current = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        mode = rec.initial_value
        try:
            result = subprocess.run(["setenforce", "1" if mode == "enforcing" else "0"],
                                     capture_output=True, text=True, timeout=10)
            reread = self._read()
        except (OSError, subprocess.TimeoutExpired) as e:
            return err("kf_err_generic", str(e))
        if result.returncode != 0:
            return err("kf_err_permission", result.stderr.strip())
        if rec.mode == "persistent":
            try:
                selinux_config_store.write_mode(mode)
            except (OSError, ValueError) as e:
                return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class MGLRUWriter:
    KEY = "memory.mglru"
    PATH = "/sys/kernel/mm/lru_gen/enabled"

    def _read(self) -> str:
        with open(self.PATH) as f:
            return f.read().strip()

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        if raw_value not in ("y", "n"):
            return err("kf_err_invalid_value", f"unsupported value {raw_value!r}")
        try:
            current = self._read()
            _note_initial(state, self.KEY, current)
            with open(self.PATH, "w") as f:
                f.write(raw_value)
            reread = self._read()
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            current = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        try:
            with open(self.PATH, "w") as f:
                f.write(rec.initial_value)
            reread = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class SwapReadaheadWriter:
    KEY = "memory.swap_readahead"
    PATH = "/proc/sys/vm/page-cluster"
    SYSCTL_KEY = "vm.page-cluster"
    MIN, MAX = 0, 3

    def _read(self) -> str:
        with open(self.PATH) as f:
            return f.read().strip()

    def _validate(self, raw_value):
        try:
            v = int(raw_value)
        except (TypeError, ValueError):
            return None
        return v if self.MIN <= v <= self.MAX else None

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        v = self._validate(raw_value)
        if v is None:
            return err("kf_err_invalid_value", f"value {raw_value!r} out of range 0-3")
        try:
            current = self._read()
            _note_initial(state, self.KEY, current)
            with open(self.PATH, "w") as f:
                f.write(str(v))
            reread = self._read()
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != str(v):
            return err("kf_err_write_mismatch", f"wrote {v}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def apply_persistent(self, raw_value, device_id, force, state: JsonStateStore):
        temp = self.apply_temporary(raw_value, device_id, force, state)
        if not temp["ok"]:
            return temp
        v = temp["value"]
        try:
            sysctl_store.write_key(self.SYSCTL_KEY, str(v))
        except (OSError, ValueError) as e:
            return err("kf_err_persist_write", str(e))
        reread_runtime = self._read()
        saved = sysctl_store.read_key(self.SYSCTL_KEY)
        if reread_runtime != str(v) or saved != str(v):
            return err("kf_err_persist_mismatch", f"runtime={reread_runtime} saved={saved} expected={v}")
        _note_applied(state, self.KEY, v, "persistent")
        return ok(v)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            current = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        v = rec.initial_value
        try:
            with open(self.PATH, "w") as f:
                f.write(str(v))
            reread = self._read()
            sysctl_store.remove_key(self.SYSCTL_KEY)
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class ReadAheadWriter:
    KEY = "storage.read_ahead"
    MIN_KB, MAX_KB = 0, 16384
    _DEVICE_RE = re.compile(r"^[a-zA-Z0-9_]+$")

    def _validate_device(self, device_id):
        if not device_id or not self._DEVICE_RE.fullmatch(device_id):
            return None
        if device_id.startswith(("loop", "ram", "zram", "dm-")):
            return None
        path = f"/sys/block/{device_id}/queue/read_ahead_kb"
        return path if os.path.isfile(path) else None

    def _read(self, path) -> int:
        with open(path) as f:
            return int(f.read().strip())

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        path = self._validate_device(device_id)
        if not path:
            return err("kf_err_device_not_found", f"device_id={device_id!r}")
        try:
            v = int(raw_value)
        except (TypeError, ValueError):
            return err("kf_err_invalid_value", f"bad value {raw_value!r}")
        if not (self.MIN_KB <= v <= self.MAX_KB):
            return err("kf_err_invalid_value", f"value {v} out of range")
        key = f"{self.KEY}:{device_id}"
        try:
            current = self._read(path)
            _note_initial(state, key, current, device_id=device_id)
            with open(path, "w") as f:
                f.write(str(v))
            reread = self._read(path)
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != v:
            return err("kf_err_write_mismatch", f"wrote {v}, kernel reports {reread}")
        _note_applied(state, key, reread, "temporary", device_id=device_id)
        return ok(reread)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        path = self._validate_device(device_id)
        if not path:
            return err("kf_err_device_not_found", f"device_id={device_id!r}")
        key = f"{self.KEY}:{device_id}"
        rec = state.get(key)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            current = self._read(path)
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        try:
            with open(path, "w") as f:
                f.write(str(rec.initial_value))
            reread = self._read(path)
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, key, reread, "restored", device_id=device_id)
        return ok(reread)


class TcpCongestionControlWriter:
    KEY = "network.tcp_congestion_control"
    PATH = "/proc/sys/net/ipv4/tcp_congestion_control"
    AVAILABLE_PATH = "/proc/sys/net/ipv4/tcp_available_congestion_control"
    SYSCTL_KEY = "net.ipv4.tcp_congestion_control"

    def _read(self) -> str:
        with open(self.PATH) as f:
            return f.read().strip()

    def _available(self) -> list:
        try:
            with open(self.AVAILABLE_PATH) as f:
                return f.read().split()
        except OSError:
            return []

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        if raw_value not in self._available():
            return err("kf_err_invalid_value", f"{raw_value!r} not in {self._available()}")
        try:
            current = self._read()
            _note_initial(state, self.KEY, current)
            with open(self.PATH, "w") as f:
                f.write(raw_value)
            reread = self._read()
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != raw_value:
            return err("kf_err_write_mismatch", f"wrote {raw_value}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def apply_persistent(self, raw_value, device_id, force, state: JsonStateStore):
        temp = self.apply_temporary(raw_value, device_id, force, state)
        if not temp["ok"]:
            return temp
        v = temp["value"]
        try:
            sysctl_store.write_key(self.SYSCTL_KEY, v)
        except (OSError, ValueError) as e:
            return err("kf_err_persist_write", str(e))
        reread_runtime = self._read()
        saved = sysctl_store.read_key(self.SYSCTL_KEY)
        if reread_runtime != v or saved != v:
            return err("kf_err_persist_mismatch", f"runtime={reread_runtime} saved={saved} expected={v}")
        _note_applied(state, self.KEY, v, "persistent")
        return ok(v)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            current = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        v = rec.initial_value
        try:
            with open(self.PATH, "w") as f:
                f.write(v)
            reread = self._read()
            sysctl_store.remove_key(self.SYSCTL_KEY)
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class CpuFrequencyLimitsWriter:
    """
    Atomic across every real cpufreq policy: if any policy fails to
    accept the new min/max, every policy already written in this same
    call is rolled back to what it had before — never left half-applied
    across cores. Per spec: never called "overclock", never allowed
    beyond each policy's own cpuinfo_min_freq/cpuinfo_max_freq.
    """
    KEY = "cpu.frequency_limits"
    BASE = "/sys/devices/system/cpu/cpufreq"
    MIN_FILE, MAX_FILE = "scaling_min_freq", "scaling_max_freq"
    HW_MIN_FILE, HW_MAX_FILE = "cpuinfo_min_freq", "cpuinfo_max_freq"

    def _dirs(self):
        try:
            entries = sorted(os.listdir(self.BASE))
        except OSError:
            return []
        dirs = []
        for name in entries:
            if not name.startswith("policy"):
                continue
            d = os.path.join(self.BASE, name)
            if os.path.isfile(os.path.join(d, self.MIN_FILE)) and os.path.isfile(os.path.join(d, self.MAX_FILE)):
                dirs.append(d)
        return dirs

    def _read_int(self, path) -> "int | None":
        try:
            with open(path) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def _read_pair(self, d):
        return self._read_int(os.path.join(d, self.MIN_FILE)), self._read_int(os.path.join(d, self.MAX_FILE))

    def _write_pair_safely(self, d, new_min, new_max, current_min, current_max):
        """Writes min/max in whichever order never produces a transient
        min > max — the kernel rejects that mid-write on some drivers."""
        min_path = os.path.join(d, self.MIN_FILE)
        max_path = os.path.join(d, self.MAX_FILE)
        if new_min <= current_max:
            with open(min_path, "w") as f:
                f.write(str(new_min))
            with open(max_path, "w") as f:
                f.write(str(new_max))
        else:
            with open(max_path, "w") as f:
                f.write(str(new_max))
            with open(min_path, "w") as f:
                f.write(str(new_min))

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        dirs = self._dirs()
        if not dirs:
            return err("kf_unsupported_hardware")
        try:
            payload = json.loads(raw_value)
            new_min, new_max = int(payload["min"]), int(payload["max"])
        except (ValueError, KeyError, TypeError):
            return err("kf_err_invalid_value", f"bad payload {raw_value!r}")
        if new_min > new_max:
            return err("kf_err_invalid_value", f"min={new_min} > max={new_max}")

        # Validate against the REAL intersection of every policy's own
        # hardware limits — never a value only some policies could take.
        hw_mins, hw_maxs = [], []
        initial = {}
        for d in dirs:
            hw_min = self._read_int(os.path.join(d, self.HW_MIN_FILE))
            hw_max = self._read_int(os.path.join(d, self.HW_MAX_FILE))
            cur_min, cur_max = self._read_pair(d)
            if None in (hw_min, hw_max, cur_min, cur_max):
                return err("kf_err_generic", f"could not read policy at {d}")
            hw_mins.append(hw_min)
            hw_maxs.append(hw_max)
            initial[os.path.basename(d)] = {"min": cur_min, "max": cur_max}
        hw_min, hw_max = max(hw_mins), min(hw_maxs)
        if not (hw_min <= new_min <= hw_max and hw_min <= new_max <= hw_max):
            return err("kf_err_invalid_value",
                        f"range {new_min}-{new_max} outside hardware limits {hw_min}-{hw_max}")

        _note_initial(state, self.KEY, initial)

        written = []
        failure = None
        for d in dirs:
            name = os.path.basename(d)
            cur_min, cur_max = initial[name]["min"], initial[name]["max"]
            try:
                self._write_pair_safely(d, new_min, new_max, cur_min, cur_max)
                written.append(d)
            except OSError as e:
                failure = str(e)
                break

        if failure is not None:
            # Roll back every policy already written in this call.
            for d in written:
                name = os.path.basename(d)
                cur_min, cur_max = initial[name]["min"], initial[name]["max"]
                try:
                    self._write_pair_safely(d, cur_min, cur_max, new_min, new_max)
                except OSError:
                    pass  # best-effort rollback; nothing more can be done here
            return err("kf_err_write_mismatch", failure)

        final = {}
        mismatch = False
        for d in dirs:
            name = os.path.basename(d)
            r_min, r_max = self._read_pair(d)
            final[name] = {"min": r_min, "max": r_max}
            if r_min != new_min or r_max != new_max:
                mismatch = True
        if mismatch:
            # Reported values don't match what we asked for on at least
            # one policy (driver silently clamped/rejected) — roll back
            # everything rather than leave a partially-honored request.
            for d in dirs:
                name = os.path.basename(d)
                cur_min, cur_max = initial[name]["min"], initial[name]["max"]
                try:
                    self._write_pair_safely(d, cur_min, cur_max, new_min, new_max)
                except OSError:
                    pass
            return err("kf_err_write_mismatch", f"wanted {new_min}-{new_max}, kernel reports {final}")

        _note_applied(state, self.KEY, final, "temporary")
        return ok(final)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        dirs = self._dirs()
        if not dirs:
            return err("kf_unsupported_hardware")
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        current = {}
        for d in dirs:
            cur_min, cur_max = self._read_pair(d)
            current[os.path.basename(d)] = {"min": cur_min, "max": cur_max}
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        initial = rec.initial_value
        for d in dirs:
            name = os.path.basename(d)
            want = initial.get(name)
            if want is None:
                continue
            cur_min, cur_max = current[name]["min"], current[name]["max"]
            try:
                self._write_pair_safely(d, want["min"], want["max"], cur_min, cur_max)
            except OSError as e:
                return err("kf_err_generic", str(e))
        final = {}
        for d in dirs:
            r_min, r_max = self._read_pair(d)
            final[os.path.basename(d)] = {"min": r_min, "max": r_max}
        _note_applied(state, self.KEY, final, "restored")
        return ok(final)


class _SimpleSysctlChoiceWriter:
    """Shared logic for dmesg_restrict/kptr_restrict/ptrace_scope — one
    /proc/sys/kernel/... file, a small fixed set of allowed values,
    optional persistence via the sysctl store. Subclasses set KEY, PATH,
    SYSCTL_KEY and CHOICES."""
    KEY = ""
    PATH = ""
    SYSCTL_KEY = ""
    CHOICES = []

    def _read(self) -> str:
        with open(self.PATH) as f:
            return f.read().strip()

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        if raw_value not in self.CHOICES:
            return err("kf_err_invalid_value", f"value {raw_value!r} not in {self.CHOICES}")
        try:
            current = self._read()
            _note_initial(state, self.KEY, current)
            with open(self.PATH, "w") as f:
                f.write(raw_value)
            reread = self._read()
        except PermissionError as e:
            return err("kf_err_permission", str(e))
        except OSError as e:
            return err("kf_err_generic", str(e))
        if reread != raw_value:
            return err("kf_err_write_mismatch", f"wrote {raw_value}, kernel reports {reread}")
        _note_applied(state, self.KEY, reread, "temporary")
        return ok(reread)

    def apply_persistent(self, raw_value, device_id, force, state: JsonStateStore):
        temp = self.apply_temporary(raw_value, device_id, force, state)
        if not temp["ok"]:
            return temp
        v = temp["value"]
        try:
            sysctl_store.write_key(self.SYSCTL_KEY, v)
        except (OSError, ValueError) as e:
            return err("kf_err_persist_write", str(e))
        reread_runtime = self._read()
        saved = sysctl_store.read_key(self.SYSCTL_KEY)
        if reread_runtime != v or saved != v:
            return err("kf_err_persist_mismatch", f"runtime={reread_runtime} saved={saved} expected={v}")
        _note_applied(state, self.KEY, v, "persistent")
        return ok(v)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            current = self._read()
        except OSError as e:
            return err("kf_err_generic", str(e))
        if not force and rec.last_applied_value is not None and current != rec.last_applied_value:
            return {"ok": False, "value": current, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        v = rec.initial_value
        try:
            with open(self.PATH, "w") as f:
                f.write(v)
            reread = self._read()
            if rec.mode == "persistent":
                sysctl_store.remove_key(self.SYSCTL_KEY)
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_applied(state, self.KEY, reread, "restored")
        return ok(reread)


class DmesgRestrictWriter(_SimpleSysctlChoiceWriter):
    KEY = "security.dmesg_restrict"
    PATH = "/proc/sys/kernel/dmesg_restrict"
    SYSCTL_KEY = "kernel.dmesg_restrict"
    CHOICES = ["0", "1"]


class KptrRestrictWriter(_SimpleSysctlChoiceWriter):
    KEY = "security.kptr_restrict"
    PATH = "/proc/sys/kernel/kptr_restrict"
    SYSCTL_KEY = "kernel.kptr_restrict"
    CHOICES = ["0", "1", "2"]


class PtraceScopeWriter(_SimpleSysctlChoiceWriter):
    KEY = "security.ptrace_scope"
    PATH = "/proc/sys/kernel/yama/ptrace_scope"
    SYSCTL_KEY = "kernel.yama.ptrace_scope"
    CHOICES = ["0", "1", "2"]  # 3 intentionally never accepted, even here


class ProtectedPathsWriter:
    """
    Applies fs.protected_symlinks/hardlinks/fifos/regular as ONE atomic
    group: every key is read and saved first, then all are written, then
    all re-read; if anything looks wrong every key already written in
    this call is rolled back to what it had before — never left with
    only some of the four changed. Logged as a single grouped state
    entry (self.KEY), never as four independent ones.
    """
    KEY = "security.protected_paths"
    KEYS = ("protected_symlinks", "protected_hardlinks", "protected_fifos", "protected_regular")
    BASE = "/proc/sys/fs"
    FULL_VALUE = "1"
    OFF_VALUE = "0"

    def _existing(self) -> list:
        return [k for k in self.KEYS if os.path.isfile(os.path.join(self.BASE, k))]

    def _read(self, key: str) -> str:
        with open(os.path.join(self.BASE, key)) as f:
            return f.read().strip()

    def _state(self, values: dict) -> str:
        if not values:
            return "unavailable"
        on_count = sum(1 for v in values.values() if v not in ("0", ""))
        if on_count == len(values):
            return "full"
        if on_count == 0:
            return "off"
        return "partial"

    def apply_temporary(self, raw_value, device_id, force, state: JsonStateStore):
        if raw_value not in ("full", "off"):
            return err("kf_err_invalid_value", f"unsupported target state {raw_value!r}")
        existing = self._existing()
        if not existing:
            return err("kf_unsupported_kernel")

        initial = {}
        try:
            for key in existing:
                initial[key] = self._read(key)
        except OSError as e:
            return err("kf_err_generic", str(e))
        _note_initial(state, self.KEY, initial)

        # Already exactly the requested state — per spec, no need to
        # rewrite anything, still records the (no-op) attempt cleanly.
        if self._state(initial) == raw_value:
            _note_applied(state, self.KEY, initial, "temporary")
            return ok(initial)

        target_value = self.FULL_VALUE if raw_value == "full" else self.OFF_VALUE
        written = []
        failure = None
        for key in existing:
            try:
                with open(os.path.join(self.BASE, key), "w") as f:
                    f.write(target_value)
                written.append(key)
            except OSError as e:
                failure = str(e)
                break

        if failure is not None:
            for key in written:
                try:
                    with open(os.path.join(self.BASE, key), "w") as f:
                        f.write(initial[key])
                except OSError:
                    pass
            return err("kf_err_write_mismatch", failure)

        final = {}
        try:
            for key in existing:
                final[key] = self._read(key)
        except OSError as e:
            return err("kf_err_generic", str(e))
        if self._state(final) != raw_value:
            for key in existing:
                try:
                    with open(os.path.join(self.BASE, key), "w") as f:
                        f.write(initial[key])
                except OSError:
                    pass
            return err("kf_err_write_mismatch", f"wanted {raw_value}, kernel reports {final}")

        _note_applied(state, self.KEY, final, "temporary")
        return ok(final)

    def restore(self, raw_value, device_id, force, state: JsonStateStore):
        existing = self._existing()
        if not existing:
            return err("kf_unsupported_kernel")
        rec = state.get(self.KEY)
        if rec is None:
            return err("kf_err_nothing_to_restore")
        try:
            current = {key: self._read(key) for key in existing}
        except OSError as e:
            return err("kf_err_generic", str(e))
        current_state = self._state(current)
        if not force and rec.last_applied_value is not None and current_state != self._state(rec.last_applied_value):
            return {"ok": False, "value": current_state, "friendly_message": "kf_external_change_detected", "technical_detail": ""}
        initial = rec.initial_value
        for key in existing:
            want = initial.get(key)
            if want is None:
                continue
            try:
                with open(os.path.join(self.BASE, key), "w") as f:
                    f.write(want)
            except OSError as e:
                return err("kf_err_generic", str(e))
        final = {key: self._read(key) for key in existing}
        _note_applied(state, self.KEY, final, "restored")
        return ok(final)


FEATURE_WRITERS = {
    "memory.swappiness": SwappinessWriter(),
    "storage.io_scheduler": IOSchedulerWriter(),
    "cpu.turbo_boost": TurboBoostWriter(),
    "cpu.governor": GovernorWriter(),
    "cpu.epp": EPPWriter(),
    "memory.thp": THPWriter(),
    "memory.zram": ZramWriter(),
    "memory.zswap": ZswapWriter(),
    "battery.charge_threshold": BatteryThresholdWriter(),
    "battery.platform_profile": PlatformProfileWriter(),
    "battery.suspend_mode": SuspendModeWriter(),
    "device_power": DevicePowerWriter(),
    "audio.power_save": AudioPowerSaveWriter(),
    "virt.ksm": KsmWriter(),
    "selinux.mode": SELinuxWriter(),
    "memory.mglru": MGLRUWriter(),
    "memory.swap_readahead": SwapReadaheadWriter(),
    "storage.read_ahead": ReadAheadWriter(),
    "network.tcp_congestion_control": TcpCongestionControlWriter(),
    "cpu.frequency_limits": CpuFrequencyLimitsWriter(),
    "security.dmesg_restrict": DmesgRestrictWriter(),
    "security.kptr_restrict": KptrRestrictWriter(),
    "security.ptrace_scope": PtraceScopeWriter(),
    "security.protected_paths": ProtectedPathsWriter(),
}


def main(argv):
    if len(argv) < 3:
        print(json.dumps(err("kf_err_generic", "missing arguments")))
        return 1

    feature_id = argv[0]
    action = argv[1]
    raw_value = argv[2] if len(argv) > 2 and argv[2] != "" else None
    device_id = argv[3] if len(argv) > 3 and argv[3] != "" else None
    force = len(argv) > 4 and argv[4] == "1"

    writer = FEATURE_WRITERS.get(feature_id)
    if writer is None:
        print(json.dumps(err("kf_err_generic", f"unknown feature_id {feature_id!r}")))
        return 1

    method = getattr(writer, action, None)
    if method is None:
        print(json.dumps(err("kf_err_generic", f"unknown action {action!r} for {feature_id!r}")))
        return 1

    os.makedirs(os.path.dirname(DEFAULT_STATE_PATH), exist_ok=True)
    state = JsonStateStore(DEFAULT_STATE_PATH)

    try:
        result = method(raw_value, device_id, force, state)
    except Exception as e:  # last-resort guard: never crash without a JSON reply
        result = err("kf_err_generic", f"{type(e).__name__}: {e}")

    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
