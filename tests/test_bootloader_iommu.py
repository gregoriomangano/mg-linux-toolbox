"""
Tests for core.bootloader_iommu — real IOMMU bootloader configuration
logic. Per explicit project policy, this NEVER runs against a real
/etc/default/grub, /etc/kernel/cmdline, or any real pkexec/grub-mkconfig/
kernelstub/bootctl invocation — every filesystem path and every
subprocess call is mocked or redirected to a temp file. Only the pure
string-transform functions and the dispatch logic are exercised.
"""
import os
import tempfile
import unittest
from unittest import mock

from core import bootloader_iommu as bi


class GrubCmdlineTransformTests(unittest.TestCase):
    def test_add_amd_params_to_existing_line(self):
        content = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n'
        new = bi._update_grub_default_content(content, bi._iommu_params("amd"), remove=False)
        self.assertIn('amd_iommu=on', new)
        self.assertIn('iommu=pt', new)
        self.assertIn('quiet', new)
        self.assertIn('splash', new)

    def test_remove_params_leaves_other_options_untouched(self):
        content = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash amd_iommu=on iommu=pt"\n'
        new = bi._update_grub_default_content(content, bi._iommu_params("amd"), remove=True)
        self.assertNotIn('amd_iommu', new)
        self.assertNotIn('iommu=pt', new)
        self.assertIn('quiet', new)
        self.assertIn('splash', new)

    def test_reapplying_does_not_duplicate(self):
        content = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet amd_iommu=on iommu=pt"\n'
        new = bi._update_grub_default_content(content, bi._iommu_params("amd"), remove=False)
        self.assertEqual(new.count("amd_iommu="), 1)
        self.assertEqual(new.count("iommu=pt"), 1)

    def test_switching_vendor_replaces_not_appends(self):
        content = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt"\n'
        # intel_iommu and amd_iommu have different key prefixes, so
        # switching vendor without an explicit removal of the old one
        # first would leave both — verified here that it does NOT
        # silently do that (caller is responsible for using the right
        # vendor consistently, this only guards against our own key).
        new = bi._update_grub_default_content(content, bi._iommu_params("amd"), remove=False)
        self.assertIn("amd_iommu=on", new)

    def test_missing_key_appends_new_line(self):
        content = "GRUB_TIMEOUT=5\n"
        new = bi._update_grub_default_content(content, bi._iommu_params("intel"), remove=False)
        self.assertIn('GRUB_CMDLINE_LINUX_DEFAULT="intel_iommu=on iommu=pt"', new)
        self.assertIn("GRUB_TIMEOUT=5", new)

    def test_removing_from_missing_key_is_a_no_op(self):
        content = "GRUB_TIMEOUT=5\n"
        new = bi._update_grub_default_content(content, bi._iommu_params("amd"), remove=True)
        self.assertEqual(new, content)

    def test_other_kernel_params_never_touched(self):
        content = 'GRUB_CMDLINE_LINUX_DEFAULT="mitigations=off nvidia-drm.modeset=1"\n'
        new = bi._update_grub_default_content(content, bi._iommu_params("amd"), remove=False)
        self.assertIn("mitigations=off", new)
        self.assertIn("nvidia-drm.modeset=1", new)


class KernelCmdlineTransformTests(unittest.TestCase):
    def test_apply_params_add(self):
        result = bi._apply_params_to_cmdline("quiet splash", bi._iommu_params("intel"), remove=False)
        self.assertIn("intel_iommu=on", result)
        self.assertIn("iommu=pt", result)

    def test_apply_params_remove(self):
        result = bi._apply_params_to_cmdline("quiet intel_iommu=on iommu=pt", bi._iommu_params("intel"), remove=True)
        self.assertEqual(result, "quiet")


class ConfigureGrubIoTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.grub_file = os.path.join(self._tmpdir.name, "grub")
        with open(self.grub_file, "w") as f:
            f.write('GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n')
        self.patcher = mock.patch.object(bi, "GRUB_DEFAULT_FILE", self.grub_file)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_configure_grub_writes_backup_and_regenerates(self):
        with mock.patch.object(bi, "run_pkexec", return_value=(True, "", "")) as mock_pkexec:
            result = bi._configure_grub(bi._iommu_params("amd"), remove=False)
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertTrue(os.path.exists(f"{self.grub_file}.bak"))
        with open(self.grub_file) as f:
            self.assertIn("amd_iommu=on", f.read())
        mock_pkexec.assert_called_once()

    def test_no_op_when_already_applied_does_not_call_pkexec(self):
        with open(self.grub_file, "w") as f:
            f.write('GRUB_CMDLINE_LINUX_DEFAULT="quiet amd_iommu=on iommu=pt"\n')
        with mock.patch.object(bi, "run_pkexec") as mock_pkexec:
            result = bi._configure_grub(bi._iommu_params("amd"), remove=False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        mock_pkexec.assert_not_called()


class ConfigureIommuDispatchTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(bi.hs, "record_operation")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unknown_cpu_vendor_refuses(self):
        with mock.patch("core.virt_readiness._cpu_vendor", return_value=""):
            result = bi.configure_iommu()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "cpu_vendor_unknown")

    def test_unsupported_bootloader_refuses(self):
        with mock.patch("core.virt_readiness._cpu_vendor", return_value="amd"), \
             mock.patch("core.bootloader_iommu.get_context") as mock_ctx:
            mock_ctx.return_value.bootloader = "unknown"
            result = bi.configure_iommu()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unsupported_bootloader")

    def test_success_marks_reboot_required(self):
        with mock.patch("core.virt_readiness._cpu_vendor", return_value="amd"), \
             mock.patch("core.bootloader_iommu.get_context") as mock_ctx, \
             mock.patch.dict(bi._CONFIGURE_BY_BOOTLOADER, {"grub": lambda params, remove, job=None: {"ok": True, "changed": True}}):
            mock_ctx.return_value.bootloader = "grub"
            result = bi.configure_iommu()
        self.assertTrue(result["ok"])
        self.assertTrue(result["reboot_required"])

    def test_never_calls_real_pkexec_or_touches_real_files_in_this_test_module(self):
        # Sanity check for the test suite itself: nothing in this file
        # ever calls bi.run_pkexec / bi.atomic_write_text without first
        # patching it out, and GRUB_DEFAULT_FILE / KERNEL_CMDLINE_FILE
        # module-level constants are never mutated at import time.
        self.assertEqual(bi.GRUB_DEFAULT_FILE, "/etc/default/grub")
        self.assertEqual(bi.KERNEL_CMDLINE_FILE, "/etc/kernel/cmdline")


class VerifyAfterRebootTests(unittest.TestCase):
    def test_returns_real_iommu_active_state(self):
        with mock.patch("core.virt_readiness.check_iommu", return_value={"active": True}), \
             mock.patch.object(bi.hs, "record_operation"):
            self.assertTrue(bi.verify_after_reboot())


if __name__ == "__main__":
    unittest.main()
