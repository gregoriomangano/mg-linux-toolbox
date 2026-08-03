"""
Tests for core.vfio_setup — real VFIO passthrough wizard logic. Per
explicit project policy, this NEVER runs lspci/pkexec/dracut/mkinitcpio/
update-initramfs for real, and never reads/writes real
/etc/modprobe.d or /etc/modules-load.d files or real /sys/bus/pci paths
— everything is mocked or redirected to temp files. Beta 4: the write
side lives in the privileged helper (see tests/test_privileged_helper.py
for the root-side transaction) — here the client orchestration is
exercised against a mocked PrivilegedWriter.
"""
import os
import unittest
from unittest import mock

from core import vfio_setup as vf
from core.kernel_features.base import OpResult


class _FakeWriter:
    def __init__(self, result: OpResult):
        self.result = result
        self.calls = []

    def execute(self, feature_id, action, value=None, device_id=None,
                force=False, record_history=True):
        self.calls.append((feature_id, action, value))
        return self.result


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
            {"address": "0000:01:00.0", "protected": False, "vendor_id": "10de",
             "device_id": "1234", "iommu_group": "14"},
            {"address": "0000:01:00.1", "protected": False, "vendor_id": "10de",
             "device_id": "5678", "iommu_group": "14"},
            {"address": "0000:00:17.0", "protected": True, "vendor_id": "8086",
             "device_id": "2822", "iommu_group": "3"},
        ]

    def test_never_trusts_caller_supplied_protected_flag(self):
        # Even though the passed-in dict claims protected=False is
        # irrelevant here — _validate_selection re-derives it itself.
        with mock.patch.object(vf, "_protection_reason", side_effect=lambda a: "storage_controller" if a == "0000:00:17.0" else None):
            reason = vf._validate_selection(["0000:00:17.0"], self._devices())
        self.assertEqual(reason, "storage_controller")

    def test_safe_whole_group_passes(self):
        with mock.patch.object(vf, "_protection_reason", return_value=None):
            self.assertIsNone(vf._validate_selection(
                ["0000:01:00.0", "0000:01:00.1"], self._devices()))

    def test_partial_group_selection_rejected(self):
        # A group is the smallest passthrough unit: picking only one of
        # two group-mates must be refused.
        with mock.patch.object(vf, "_protection_reason", return_value=None):
            reason = vf._validate_selection(["0000:01:00.0"], self._devices())
        self.assertEqual(reason, "incomplete_group")

    def test_empty_selection_rejected(self):
        self.assertEqual(vf._validate_selection([], self._devices()), "no_devices")

    def test_unknown_address_rejected(self):
        with mock.patch.object(vf, "_protection_reason", return_value=None):
            reason = vf._validate_selection(["0000:99:00.0"], self._devices())
        self.assertEqual(reason, "device_not_found")


class ConfigureVfioTests(unittest.TestCase):
    """Beta 4: configure_vfio() validates client-side, then hands the
    whole transaction to the privileged helper — no /etc write and no
    pkexec ever happens in this process."""

    def _devices(self):
        return [{"address": "0000:01:00.0", "vendor_id": "10de", "device_id": "1234",
                  "iommu_group": "14", "protected": False, "protection_reason": None}]

    def test_refuses_protected_device_without_calling_the_helper(self):
        writer = _FakeWriter(OpResult(True))
        with mock.patch.object(vf, "list_pci_devices", return_value=self._devices()), \
             mock.patch.object(vf, "_validate_selection", return_value="storage_controller"), \
             mock.patch.object(vf, "default_privileged_writer", return_value=writer):
            result = vf.configure_vfio(["0000:00:17.0"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "storage_controller")
        self.assertEqual(writer.calls, [])

    def test_sends_one_helper_transaction_with_the_addresses(self):
        writer = _FakeWriter(OpResult(True, value={"ids": "10de:1234"}, reboot_required=True))
        with mock.patch.object(vf, "list_pci_devices", return_value=self._devices()), \
             mock.patch.object(vf, "_protection_reason", return_value=None), \
             mock.patch.object(vf, "default_privileged_writer", return_value=writer):
            result = vf.configure_vfio(["0000:01:00.0"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["reboot_required"])
        self.assertEqual(writer.calls,
                         [("virt.vfio", "configure", {"addresses": ["0000:01:00.0"]})])

    def test_helper_failure_reported_with_friendly_reason(self):
        writer = _FakeWriter(OpResult(False, friendly_message="kf_err_helper_missing",
                                       technical_detail="helper state=missing"))
        with mock.patch.object(vf, "list_pci_devices", return_value=self._devices()), \
             mock.patch.object(vf, "_protection_reason", return_value=None), \
             mock.patch.object(vf, "default_privileged_writer", return_value=writer):
            result = vf.configure_vfio(["0000:01:00.0"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "kf_err_helper_missing")

    def test_no_etc_write_exists_in_the_client_module(self):
        # Structural regression guard for the Beta 3 bug: the client
        # module must not contain any code writing files (the helper
        # owns every write).
        import inspect
        source = inspect.getsource(vf)
        self.assertNotIn("atomic_write_text", source)
        self.assertNotIn('open(MODPROBE_FILE, "w"', source)


class RemoveAndRestoreTests(unittest.TestCase):
    def test_remove_goes_through_the_helper(self):
        writer = _FakeWriter(OpResult(True, value={"changed": True}, reboot_required=True))
        with mock.patch.object(vf, "default_privileged_writer", return_value=writer):
            result = vf.remove_vfio_configuration()
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(writer.calls[0][:2], ("virt.vfio", "disable"))

    def test_remove_when_nothing_configured_reports_unchanged(self):
        writer = _FakeWriter(OpResult(True, value={"changed": False}))
        with mock.patch.object(vf, "default_privileged_writer", return_value=writer):
            result = vf.remove_vfio_configuration()
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])

    def test_restore_original_driver_uses_the_restore_action(self):
        writer = _FakeWriter(OpResult(True, value={"changed": True}, reboot_required=True))
        with mock.patch.object(vf, "default_privileged_writer", return_value=writer):
            result = vf.restore_original_driver()
        self.assertTrue(result["ok"])
        self.assertEqual(writer.calls[0][:2], ("virt.vfio", "restore"))


class VerifyAfterRebootTests(unittest.TestCase):
    def test_all_bound_to_vfio_pci_is_ok(self):
        with mock.patch("os.readlink", return_value="/sys/bus/pci/drivers/vfio-pci"), \
             mock.patch("core.persistence.history_store.record_operation"):
            result = vf.verify_after_reboot(["0000:01:00.0"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["drivers"]["0000:01:00.0"], "vfio-pci")

    def test_still_bound_to_original_driver_is_not_ok(self):
        with mock.patch("os.readlink", return_value="/sys/bus/pci/drivers/nvidia"), \
             mock.patch("core.persistence.history_store.record_operation"):
            result = vf.verify_after_reboot(["0000:01:00.0"])
        self.assertFalse(result["ok"])

    def test_missing_driver_link_reported_as_none(self):
        with mock.patch("os.readlink", side_effect=OSError), \
             mock.patch("core.persistence.history_store.record_operation"):
            result = vf.verify_after_reboot(["0000:01:00.0"])
        self.assertIsNone(result["drivers"]["0000:01:00.0"])
        self.assertFalse(result["ok"])


class PassthroughGroupsTests(unittest.TestCase):
    """The Beta 4 group-based selection model the wizard is built on."""

    def _devices(self):
        return [
            {"address": "0000:00:01.0", "iommu_group": "0", "protected": True,
             "protection_reason": "essential_device"},
            {"address": "0000:01:00.0", "iommu_group": "14", "protected": False,
             "protection_reason": None},
            {"address": "0000:01:00.1", "iommu_group": "14", "protected": False,
             "protection_reason": None},
            {"address": "0000:02:00.0", "iommu_group": "15", "protected": True,
             "protection_reason": "primary_gpu"},
            {"address": "0000:03:00.0", "iommu_group": "15", "protected": False,
             "protection_reason": None},
            {"address": "0000:04:00.0", "iommu_group": "", "protected": False,
             "protection_reason": None},
        ]

    def test_safe_group_is_selectable_as_a_unit(self):
        groups = vf.passthrough_groups(self._devices(), iommu_active=True)
        by_id = {g["group"]: g for g in groups}
        self.assertTrue(by_id["14"]["selectable"])
        self.assertEqual(len(by_id["14"]["devices"]), 2)

    def test_group_with_any_protected_device_is_disabled_whole(self):
        groups = vf.passthrough_groups(self._devices(), iommu_active=True)
        by_id = {g["group"]: g for g in groups}
        self.assertFalse(by_id["0"]["selectable"])
        self.assertFalse(by_id["15"]["selectable"])
        self.assertEqual(by_id["15"]["reason"], "contains_protected")

    def test_device_without_group_is_never_selectable(self):
        groups = vf.passthrough_groups(self._devices(), iommu_active=True)
        by_id = {g["group"]: g for g in groups}
        self.assertFalse(by_id[""]["selectable"])
        self.assertEqual(by_id[""]["reason"], "no_group")

    def test_iommu_off_disables_every_group(self):
        groups = vf.passthrough_groups(self._devices(), iommu_active=False)
        self.assertTrue(all(not g["selectable"] for g in groups))
        self.assertTrue(all(g["reason"] == "no_iommu" for g in groups))

    def test_no_devices_means_no_candidates(self):
        groups = vf.passthrough_groups([], iommu_active=True)
        self.assertEqual(groups, [])
        self.assertFalse(vf.has_passthrough_candidates(groups))

    def test_only_protected_devices_means_no_candidates(self):
        devices = [d for d in self._devices() if d["protected"]]
        groups = vf.passthrough_groups(devices, iommu_active=True)
        self.assertFalse(vf.has_passthrough_candidates(groups))

    def test_has_candidates_true_with_one_safe_group(self):
        groups = vf.passthrough_groups(self._devices(), iommu_active=True)
        self.assertTrue(vf.has_passthrough_candidates(groups))

    def test_non_numeric_group_id_still_listed_and_disabled_only_by_rules(self):
        devices = [{"address": "0000:05:00.0", "iommu_group": "abc",
                     "protected": False, "protection_reason": None}]
        groups = vf.passthrough_groups(devices, iommu_active=True)
        self.assertEqual(groups[0]["group"], "abc")
        self.assertTrue(groups[0]["selectable"])


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
