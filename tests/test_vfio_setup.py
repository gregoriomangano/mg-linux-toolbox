"""
Tests for core.vfio_setup — real VFIO passthrough wizard logic. Per
explicit project policy, this NEVER runs lspci/pkexec/dracut/mkinitcpio/
update-initramfs for real, and never reads/writes real
/etc/modprobe.d or /etc/modules-load.d files or real /sys/bus/pci paths
— everything is mocked or redirected to temp files.
"""
import contextlib
import os
import tempfile
import unittest
from unittest import mock

from core import vfio_setup as vf


def _distro_flags(stack, **flags):
    for name in ("is_arch", "is_fedora", "is_opensuse", "is_debian"):
        stack.enter_context(mock.patch.object(
            type(vf.distro), name,
            new_callable=mock.PropertyMock, return_value=flags.get(name, False)))


class ParseLspciLineTests(unittest.TestCase):
    def test_parses_a_real_shaped_line(self):
        line = '0000:01:00.0 "0300" "10de" "1234" -ra1 "10de" "5678"'
        result = vf._parse_lspci_mm_line(line)
        self.assertEqual(result[0], "0000:01:00.0")
        self.assertEqual(result[1], "10de")
        self.assertEqual(result[2], "1234")

    def test_malformed_line_returns_none(self):
        self.assertIsNone(vf._parse_lspci_mm_line("garbage"))


class ProtectionTests(unittest.TestCase):
    def test_storage_class_is_protected(self):
        with mock.patch.object(vf, "_read", return_value="0x010601"):
            self.assertEqual(vf._protection_reason("0000:00:17.0"), "storage_controller")

    def test_boot_vga_is_protected(self):
        def fake_read(path):
            if path.endswith("class"):
                return "0x030000"
            if path.endswith("boot_vga"):
                return "1"
            return ""
        with mock.patch.object(vf, "_read", side_effect=fake_read):
            self.assertEqual(vf._protection_reason("0000:01:00.0"), "primary_gpu")

    def test_secondary_gpu_not_boot_vga_is_not_protected(self):
        def fake_read(path):
            if path.endswith("class"):
                return "0x030000"
            if path.endswith("boot_vga"):
                return "0"
            return ""
        with mock.patch.object(vf, "_read", side_effect=fake_read):
            self.assertIsNone(vf._protection_reason("0000:02:00.0"))

    def test_network_card_is_not_protected(self):
        with mock.patch.object(vf, "_read", return_value="0x020000"):
            self.assertIsNone(vf._protection_reason("0000:03:00.0"))


class ValidateSelectionTests(unittest.TestCase):
    def _devices(self):
        return [
            {"address": "0000:01:00.0", "protected": False, "vendor_id": "10de", "device_id": "1234"},
            {"address": "0000:00:17.0", "protected": True, "vendor_id": "8086", "device_id": "2822"},
        ]

    def test_never_trusts_caller_supplied_protected_flag(self):
        # Even though the passed-in dict claims protected=False is
        # irrelevant here — _validate_selection re-derives it itself.
        with mock.patch.object(vf, "_protection_reason", side_effect=lambda a: "storage_controller" if a == "0000:00:17.0" else None):
            reason = vf._validate_selection(["0000:00:17.0"], self._devices())
        self.assertEqual(reason, "storage_controller")

    def test_safe_device_passes(self):
        with mock.patch.object(vf, "_protection_reason", return_value=None):
            self.assertIsNone(vf._validate_selection(["0000:01:00.0"], self._devices()))

    def test_unknown_address_rejected(self):
        with mock.patch.object(vf, "_protection_reason", return_value=None):
            reason = vf._validate_selection(["0000:99:00.0"], self._devices())
        self.assertEqual(reason, "device_not_found")


class ConfigureVfioTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.modprobe_file = os.path.join(self._tmpdir.name, "vfio.conf")
        self.modules_file = os.path.join(self._tmpdir.name, "vfio-modules.conf")
        mp = mock.patch.object(vf, "MODPROBE_FILE", self.modprobe_file)
        mp.start(); self.addCleanup(mp.stop)
        ml = mock.patch.object(vf, "MODULES_LOAD_FILE", self.modules_file)
        ml.start(); self.addCleanup(ml.stop)
        logp = mock.patch.object(vf.hs, "record_operation")
        logp.start(); self.addCleanup(logp.stop)

    def _devices(self):
        return [{"address": "0000:01:00.0", "vendor_id": "10de", "device_id": "1234",
                  "iommu_group": "14", "protected": False, "protection_reason": None}]

    def test_refuses_protected_device(self):
        with mock.patch.object(vf, "list_pci_devices", return_value=self._devices()), \
             mock.patch.object(vf, "_validate_selection", return_value="storage_controller"), \
             mock.patch.object(vf, "run_pkexec") as mock_pkexec:
            result = vf.configure_vfio(["0000:00:17.0"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "storage_controller")
        mock_pkexec.assert_not_called()

    def test_writes_modprobe_and_modules_load_files(self):
        with mock.patch.object(vf, "list_pci_devices", return_value=self._devices()), \
             mock.patch.object(vf, "_protection_reason", return_value=None), \
             mock.patch.object(vf, "run_pkexec", return_value=(True, "", "")) as mock_pkexec:
            result = vf.configure_vfio(["0000:01:00.0"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["reboot_required"])
        with open(self.modprobe_file) as f:
            self.assertIn("options vfio-pci ids=10de:1234", f.read())
        with open(self.modules_file) as f:
            content = f.read()
        self.assertIn("vfio_pci", content)
        mock_pkexec.assert_called_once()

    def test_never_mixes_initramfs_tools_across_distro_families(self):
        with contextlib.ExitStack() as stack:
            _distro_flags(stack, is_arch=True)
            self.assertEqual(vf._initramfs_regen_cmd(), ["mkinitcpio", "-P"])
        with contextlib.ExitStack() as stack:
            _distro_flags(stack, is_debian=True)
            self.assertEqual(vf._initramfs_regen_cmd(), ["update-initramfs", "-u"])


class RemoveAndRestoreTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.modprobe_file = os.path.join(self._tmpdir.name, "vfio.conf")
        self.modules_file = os.path.join(self._tmpdir.name, "vfio-modules.conf")
        mp = mock.patch.object(vf, "MODPROBE_FILE", self.modprobe_file)
        mp.start(); self.addCleanup(mp.stop)
        ml = mock.patch.object(vf, "MODULES_LOAD_FILE", self.modules_file)
        ml.start(); self.addCleanup(ml.stop)
        logp = mock.patch.object(vf.hs, "record_operation")
        logp.start(); self.addCleanup(logp.stop)
        with open(self.modprobe_file, "w") as f:
            f.write("options vfio-pci ids=10de:1234\n")
        with open(self.modules_file, "w") as f:
            f.write("vfio\nvfio_pci\n")

    def test_remove_deletes_both_files_and_regenerates(self):
        with mock.patch.object(vf, "run_pkexec", return_value=(True, "", "")) as mock_pkexec:
            result = vf.remove_vfio_configuration()
        self.assertTrue(result["ok"])
        self.assertFalse(os.path.exists(self.modprobe_file))
        self.assertFalse(os.path.exists(self.modules_file))
        mock_pkexec.assert_called_once()

    def test_remove_when_nothing_configured_is_a_no_op(self):
        os.remove(self.modprobe_file)
        os.remove(self.modules_file)
        with mock.patch.object(vf, "run_pkexec") as mock_pkexec:
            result = vf.remove_vfio_configuration()
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        mock_pkexec.assert_not_called()

    def test_restore_original_driver_is_mechanically_the_same_as_remove(self):
        with mock.patch.object(vf, "run_pkexec", return_value=(True, "", "")):
            result = vf.restore_original_driver()
        self.assertTrue(result["ok"])
        self.assertFalse(os.path.exists(self.modprobe_file))


class VerifyAfterRebootTests(unittest.TestCase):
    def test_all_bound_to_vfio_pci_is_ok(self):
        with mock.patch("os.readlink", return_value="/sys/bus/pci/drivers/vfio-pci"), \
             mock.patch.object(vf.hs, "record_operation"):
            result = vf.verify_after_reboot(["0000:01:00.0"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["drivers"]["0000:01:00.0"], "vfio-pci")

    def test_still_bound_to_original_driver_is_not_ok(self):
        with mock.patch("os.readlink", return_value="/sys/bus/pci/drivers/nvidia"), \
             mock.patch.object(vf.hs, "record_operation"):
            result = vf.verify_after_reboot(["0000:01:00.0"])
        self.assertFalse(result["ok"])

    def test_missing_driver_link_reported_as_none(self):
        with mock.patch("os.readlink", side_effect=OSError), \
             mock.patch.object(vf.hs, "record_operation"):
            result = vf.verify_after_reboot(["0000:01:00.0"])
        self.assertIsNone(result["drivers"]["0000:01:00.0"])
        self.assertFalse(result["ok"])


class ListPciDevicesTests(unittest.TestCase):
    def test_uses_lspci_dnmm_and_tags_protection(self):
        sample = '0000:01:00.0 "0300" "10de" "1234"\n0000:00:17.0 "0106" "8086" "2822"\n'
        with mock.patch.object(vf, "run_command", return_value=(True, sample, "")), \
             mock.patch.object(vf, "_iommu_group", return_value="1"), \
             mock.patch.object(vf, "_protection_reason", side_effect=lambda a: "storage_controller" if a == "0000:00:17.0" else None):
            devices = vf.list_pci_devices()
        self.assertEqual(len(devices), 2)
        by_addr = {d["address"]: d for d in devices}
        self.assertFalse(by_addr["0000:01:00.0"]["protected"])
        self.assertTrue(by_addr["0000:00:17.0"]["protected"])

    def test_lspci_failure_returns_empty_list(self):
        with mock.patch.object(vf, "run_command", return_value=(False, "", "not found")):
            self.assertEqual(vf.list_pci_devices(), [])


class ListIommuGroupsTests(unittest.TestCase):
    def test_groups_devices_by_iommu_group(self):
        devices = [
            {"address": "a", "iommu_group": "1"},
            {"address": "b", "iommu_group": "1"},
            {"address": "c", "iommu_group": "2"},
        ]
        groups = vf.list_iommu_groups(devices)
        self.assertEqual(len(groups["1"]), 2)
        self.assertEqual(len(groups["2"]), 1)


if __name__ == "__main__":
    unittest.main()
