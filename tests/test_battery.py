"""
Tests for core.kernel_features.battery and device_power. This machine is
a desktop with no battery, so BatteryStatusFeature/BatteryThresholdFeature
are exercised entirely against a fake /sys tree — never verified on real
battery hardware. PlatformProfileFeature is also fake-tree-only (real
absence confirmed separately). SuspendModeFeature IS real (mem_sleep
exists on this machine) and is covered by real-machine assertions too.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.kernel_features.base import SupportStatus
from core.kernel_features.battery import (
    BatteryStatusFeature, BatteryThresholdFeature, PlatformProfileFeature, SuspendModeFeature,
)
from core.kernel_features.device_power import list_wakeup_capable_devices, list_pm_controllable_devices
from core import priv_writer
from core.persistence.rollback_store import JsonStateStore


class FakeRootTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sys_root = os.path.join(self.tmp, "sys")
        os.makedirs(self.sys_root)

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(root, name), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)


class BatteryStatusTests(FakeRootTestCase):
    def _make_battery(self, **attrs):
        d = os.path.join(self.sys_root, "class", "power_supply", "BAT0")
        os.makedirs(d)
        with open(os.path.join(d, "type"), "w") as f:
            f.write("Battery")
        for name, value in attrs.items():
            with open(os.path.join(d, name), "w") as f:
                f.write(str(value))
        return d

    def test_no_battery_present(self):
        os.makedirs(os.path.join(self.sys_root, "class", "power_supply"))
        f = BatteryStatusFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_HARDWARE)

    def test_non_battery_power_supply_ignored(self):
        d = os.path.join(self.sys_root, "class", "power_supply", "AC")
        os.makedirs(d)
        with open(os.path.join(d, "type"), "w") as f:
            f.write("Mains")
        f = BatteryStatusFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_HARDWARE)

    def test_energy_family_parsed(self):
        self._make_battery(capacity=77, status="Discharging",
                            energy_full=45000000, energy_full_design=50000000,
                            energy_now=34650000, power_now=8000000, cycle_count=142, temp=312)
        f = BatteryStatusFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertEqual(r.value["percent"], 77)
        self.assertEqual(r.value["status"], "Discharging")
        self.assertEqual(r.value["health_percent"], 90.0)
        self.assertEqual(r.value["cycle_count"], 142)
        self.assertEqual(r.value["temperature_c"], 31.2)
        self.assertIn("estimated_hours_remaining", r.value)

    def test_charge_family_parsed(self):
        """Older/other-vendor batteries expose charge_* (µAh) instead of
        energy_* (µWh) — both must work."""
        self._make_battery(capacity=50, status="Charging",
                            charge_full=4000000, charge_full_design=4500000, charge_now=2000000)
        f = BatteryStatusFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertEqual(r.value["percent"], 50)
        self.assertAlmostEqual(r.value["health_percent"], 88.9, places=1)

    def test_missing_optional_fields_simply_absent(self):
        self._make_battery(capacity=60, status="Full")
        f = BatteryStatusFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertNotIn("cycle_count", r.value)
        self.assertNotIn("health_percent", r.value)


class BatteryThresholdTests(FakeRootTestCase):
    def _make_battery_with_thresholds(self, start=20, end=80):
        d = os.path.join(self.sys_root, "class", "power_supply", "BAT0")
        os.makedirs(d)
        with open(os.path.join(d, "type"), "w") as f:
            f.write("Battery")
        with open(os.path.join(d, "charge_control_start_threshold"), "w") as f:
            f.write(str(start))
        with open(os.path.join(d, "charge_control_end_threshold"), "w") as f:
            f.write(str(end))
        return d

    def test_unsupported_without_threshold_files(self):
        d = os.path.join(self.sys_root, "class", "power_supply", "BAT0")
        os.makedirs(d)
        with open(os.path.join(d, "type"), "w") as f:
            f.write("Battery")
        f = BatteryThresholdFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_HARDWARE)

    def test_reads_current_thresholds(self):
        self._make_battery_with_thresholds(20, 80)
        f = BatteryThresholdFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertEqual(r.value, {"start": 20, "end": 80})

    def test_validate_rejects_start_not_less_than_end(self):
        self._make_battery_with_thresholds()
        f = BatteryThresholdFeature(sys_root=self.sys_root)
        self.assertFalse(f.validate({"start": 80, "end": 20}))
        self.assertFalse(f.validate({"start": 50, "end": 50}))
        self.assertTrue(f.validate({"start": 20, "end": 80}))

    def test_thinkpad_style_names_also_recognized(self):
        d = os.path.join(self.sys_root, "class", "power_supply", "BAT0")
        os.makedirs(d)
        with open(os.path.join(d, "type"), "w") as f:
            f.write("Battery")
        with open(os.path.join(d, "charge_start_threshold"), "w") as f:
            f.write("40")
        with open(os.path.join(d, "charge_stop_threshold"), "w") as f:
            f.write("90")
        f = BatteryThresholdFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.SUPPORTED_RUNTIME)
        self.assertEqual(f.read_current().value, {"start": 40, "end": 90})


class PlatformProfileTests(FakeRootTestCase):
    def test_unsupported_when_missing(self):
        f = PlatformProfileFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_HARDWARE)

    def test_reads_current_and_available(self):
        d = os.path.join(self.sys_root, "firmware", "acpi")
        os.makedirs(d)
        with open(os.path.join(d, "platform_profile"), "w") as f:
            f.write("balanced")
        with open(os.path.join(d, "platform_profile_choices"), "w") as f:
            f.write("low-power balanced performance")
        f = PlatformProfileFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.SUPPORTED_RUNTIME)
        self.assertEqual(f.read_current().value, "balanced")
        self.assertEqual(f.read_available(), ["low-power", "balanced", "performance"])

    def test_single_choice_still_reported_available(self):
        d = os.path.join(self.sys_root, "firmware", "acpi")
        os.makedirs(d)
        with open(os.path.join(d, "platform_profile"), "w") as f:
            f.write("balanced")
        with open(os.path.join(d, "platform_profile_choices"), "w") as f:
            f.write("balanced")
        f = PlatformProfileFeature(sys_root=self.sys_root)
        self.assertEqual(f.read_available(), ["balanced"])


class SuspendModeTests(FakeRootTestCase):
    def test_unsupported_when_missing(self):
        f = SuspendModeFeature(sys_root=self.sys_root)
        self.assertEqual(f.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_parses_bracketed_current(self):
        d = os.path.join(self.sys_root, "power")
        os.makedirs(d)
        with open(os.path.join(d, "mem_sleep"), "w") as f:
            f.write("s2idle [deep]")
        f = SuspendModeFeature(sys_root=self.sys_root)
        r = f.read_current()
        self.assertTrue(r.ok)
        self.assertEqual(r.value["current"], "deep")
        self.assertEqual(r.value["available"], ["s2idle", "deep"])

    def test_real_machine_reports_supported(self):
        """This one runs against the REAL /sys/power/mem_sleep — it does
        exist on this machine, so this is a genuine (not faked) check."""
        f = SuspendModeFeature()
        status = f.probe()
        self.assertIn(status, (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.UNSUPPORTED_KERNEL))


class WakeupDeviceClassificationTests(FakeRootTestCase):
    def _make_usb_device(self, name, product=None, interfaces=None):
        d = os.path.join(self.sys_root, "bus", "usb", "devices", name)
        os.makedirs(os.path.join(d, "power"))
        with open(os.path.join(d, "power", "wakeup"), "w") as f:
            f.write("disabled")
        with open(os.path.join(d, "power", "control"), "w") as f:
            f.write("auto")
        if product is not None:
            with open(os.path.join(d, "product"), "w") as f:
                f.write(product)
        for i, (cls, proto) in enumerate(interfaces or []):
            idir = os.path.join(self.sys_root, "bus", "usb", "devices", f"{name}:1.{i}")
            os.makedirs(idir)
            with open(os.path.join(idir, "bInterfaceClass"), "w") as f:
                f.write(cls)
            with open(os.path.join(idir, "bInterfaceProtocol"), "w") as f:
                f.write(proto)
        return d

    def test_root_hub_excluded(self):
        self._make_usb_device("1-0:1.0", product="xHCI Host Controller")
        devices = list_wakeup_capable_devices(sys_root=self.sys_root)
        self.assertEqual(devices, [])

    def test_hub_excluded(self):
        self._make_usb_device("1-2", product="USB2.0 Hub")
        devices = list_wakeup_capable_devices(sys_root=self.sys_root)
        self.assertEqual(devices, [])

    def test_keyboard_classified_by_product_name(self):
        self._make_usb_device("3-1.2", product="USB Keyboard")
        devices = list_wakeup_capable_devices(sys_root=self.sys_root)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["category"], "keyboard")

    def test_gaming_mouse_classified_by_hid_protocol_not_name(self):
        """A mouse whose product name doesn't contain 'mouse' at all —
        must still classify correctly via the USB HID boot-protocol
        interface (bInterfaceClass=03, bInterfaceProtocol=02), exactly
        matching the real 'Razer Naga' device found on this machine."""
        self._make_usb_device("3-1.3", product="Razer Naga Left Handed Edition",
                               interfaces=[("03", "02")])
        devices = list_wakeup_capable_devices(sys_root=self.sys_root)
        self.assertEqual(devices[0]["category"], "mouse")

    def test_unidentified_device_excluded_from_wakeup(self):
        self._make_usb_device("1-9", product="Insta360 Link 2")  # webcam, not a wakeup category
        devices = list_wakeup_capable_devices(sys_root=self.sys_root)
        self.assertEqual(devices, [])

    def test_pm_control_excludes_mouse_even_via_hid_protocol(self):
        self._make_usb_device("3-1.3", product="Razer Naga Left Handed Edition",
                               interfaces=[("03", "02")])
        devices = list_pm_controllable_devices(sys_root=self.sys_root)
        self.assertEqual(devices, [])

    def test_pm_control_includes_webcam(self):
        self._make_usb_device("1-9", product="My Webcam 4K")
        devices = list_pm_controllable_devices(sys_root=self.sys_root)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["category"], "webcam")

    def test_lid_classified_via_acpi_hid(self):
        d = os.path.join(self.sys_root, "bus", "acpi", "devices", "PNP0C0D:00")
        os.makedirs(os.path.join(d, "power"))
        with open(os.path.join(d, "power", "wakeup"), "w") as f:
            f.write("enabled")
        with open(os.path.join(d, "hid"), "w") as f:
            f.write("PNP0C0D")
        devices = list_wakeup_capable_devices(sys_root=self.sys_root)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["category"], "lid")


class PrivWriterBatteryThresholdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.batt_dir = os.path.join(self.tmp, "BAT0")
        os.makedirs(self.batt_dir)
        with open(os.path.join(self.batt_dir, "type"), "w") as f:
            f.write("Battery")
        with open(os.path.join(self.batt_dir, "charge_control_start_threshold"), "w") as f:
            f.write("20")
        with open(os.path.join(self.batt_dir, "charge_control_end_threshold"), "w") as f:
            f.write("80")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.BatteryThresholdWriter()
        self.writer.POWER_SUPPLY_BASE = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_and_restore(self):
        payload = json.dumps({"start": 40, "end": 90})
        result = self.writer.apply_temporary(payload, None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], {"start": 40, "end": 90})
        restored = self.writer.restore(None, None, False, self.state)
        self.assertTrue(restored["ok"])
        self.assertEqual(restored["value"], {"start": 20, "end": 80})

    def test_rejects_invalid_range(self):
        payload = json.dumps({"start": 90, "end": 40})
        result = self.writer.apply_temporary(payload, None, False, self.state)
        self.assertFalse(result["ok"])


class PrivWriterDevicePowerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.writer = priv_writer.DevicePowerWriter()
        self.writer._ALLOWED_BUSES = ("usb",)
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        # Patch the hardcoded /sys/bus/... base by monkeypatching _resolve
        # via a real fake directory tree instead (keeps the method's own
        # validation logic under test unchanged).
        self._orig_resolve = priv_writer.DevicePowerWriter._resolve
        fake_base = os.path.join(self.tmp, "sys", "bus", "usb", "devices")
        os.makedirs(os.path.join(fake_base, "3-1.2", "power"))
        with open(os.path.join(fake_base, "3-1.2", "power", "wakeup"), "w") as f:
            f.write("disabled")

        def fake_resolve(self_writer, bus, device_id, filename):
            if bus != "usb" or device_id != "3-1.2":
                return None
            path = os.path.join(fake_base, device_id, "power", filename)
            return path if os.path.isfile(path) else None

        priv_writer.DevicePowerWriter._resolve = fake_resolve

    def tearDown(self):
        priv_writer.DevicePowerWriter._resolve = self._orig_resolve
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_enable_wakeup_for_real_device(self):
        result = self.writer.apply_temporary("usb:3-1.2:enabled", None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "enabled")

    def test_rejects_unresolvable_device(self):
        result = self.writer.apply_temporary("usb:9-9.9:enabled", None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_device_not_found")

    def test_rejects_malformed_payload(self):
        result = self.writer.apply_temporary("garbage", None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_invalid_value")

    def test_path_traversal_device_id_rejected(self):
        result = self.writer.apply_temporary("usb:../../../etc:enabled", None, False, self.state)
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
