"""
Targeted tests for the GPU-vendor-dependent 32-bit Vulkan mapping
(2026-08-05 correction round): a generic loader package is not enough
for real 32-bit Vulkan, and this app must never guess a GPU-specific
package when the vendor can't be determined.
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from core import gpu_vendor as gv


def _make_uevent(root: str, driver: str) -> None:
    card_dir = os.path.join(root, "class", "drm", "card0", "device")
    os.makedirs(card_dir, exist_ok=True)
    with open(os.path.join(card_dir, "uevent"), "w") as f:
        f.write(f"DRIVER={driver}\n")


class GpuVendorDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_amdgpu_driver_is_amd_vendor(self):
        _make_uevent(self.tmp, "amdgpu")
        self.assertEqual(gv.detect_gpu_vendor(sys_root=self.tmp), gv.AMD)

    def test_radeon_driver_is_amd_vendor(self):
        _make_uevent(self.tmp, "radeon")
        self.assertEqual(gv.detect_gpu_vendor(sys_root=self.tmp), gv.AMD)

    def test_i915_driver_is_intel_vendor(self):
        _make_uevent(self.tmp, "i915")
        self.assertEqual(gv.detect_gpu_vendor(sys_root=self.tmp), gv.INTEL)

    def test_nvidia_driver_is_proprietary_vendor(self):
        _make_uevent(self.tmp, "nvidia")
        self.assertEqual(gv.detect_gpu_vendor(sys_root=self.tmp), gv.NVIDIA_PROPRIETARY)

    def test_nouveau_driver_is_nouveau_vendor(self):
        _make_uevent(self.tmp, "nouveau")
        self.assertEqual(gv.detect_gpu_vendor(sys_root=self.tmp), gv.NOUVEAU)

    def test_no_driver_bound_is_unknown(self):
        os.makedirs(os.path.join(self.tmp, "class", "drm"), exist_ok=True)
        self.assertEqual(gv.detect_gpu_vendor(sys_root=self.tmp), gv.UNKNOWN)

    def test_unrecognized_driver_is_unknown_not_guessed(self):
        _make_uevent(self.tmp, "some_future_driver")
        self.assertEqual(gv.detect_gpu_vendor(sys_root=self.tmp), gv.UNKNOWN)


class Lib32BackendGpuMappingTests(unittest.TestCase):
    """backend.all.lib32_* — the real Vulkan ICD must match the real GPU,
    and an unrecognized/proprietary GPU must block, never guess."""

    @staticmethod
    def _opensuse(B):
        return mock.patch.multiple(B.distro, id="opensuse-tumbleweed", id_like="opensuse suse")

    def test_amd_gpu_install_command_includes_libvulkan_radeon_32bit(self):
        import backend.all as B
        with self._opensuse(B), \
             mock.patch("backend.all.gpu_vendor.detect_gpu_vendor", return_value=gv.AMD), \
             mock.patch("backend.all.run_pkexec_full") as pk_mock, \
             mock.patch("backend.all.lib32_installed", return_value=True):
            B.lib32_install()
        cmd = pk_mock.call_args[0][0]
        self.assertIn("libvulkan_radeon-32bit", cmd)
        self.assertIn("Mesa-libGL1-32bit", cmd)
        self.assertIn("libvulkan1-32bit", cmd)

    def test_unknown_gpu_blocks_install_without_calling_zypper(self):
        import backend.all as B
        with self._opensuse(B), \
             mock.patch("backend.all.gpu_vendor.detect_gpu_vendor", return_value=gv.UNKNOWN), \
             mock.patch("backend.all.run_pkexec_full") as pk_mock:
            result = B.lib32_install()
        pk_mock.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "lib32_blocked_unknown_gpu")

    def test_nvidia_proprietary_gpu_blocks_install_without_calling_zypper(self):
        import backend.all as B
        with self._opensuse(B), \
             mock.patch("backend.all.gpu_vendor.detect_gpu_vendor", return_value=gv.NVIDIA_PROPRIETARY), \
             mock.patch("backend.all.run_pkexec_full") as pk_mock:
            result = B.lib32_install()
        pk_mock.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "lib32_blocked_nvidia_proprietary")

    def test_lib32_blocked_reason_empty_for_known_vendor(self):
        import backend.all as B
        with self._opensuse(B), \
             mock.patch("backend.all.gpu_vendor.detect_gpu_vendor", return_value=gv.AMD):
            self.assertEqual(B.lib32_blocked_reason(), "")

    def test_lib32_installed_checks_the_real_icd_not_just_the_loader(self):
        """The exact defect described by the user: a loader-only check
        would report "installed" even with zero working 32-bit Vulkan
        drivers for this GPU."""
        import backend.all as B
        with self._opensuse(B), \
             mock.patch("backend.all.gpu_vendor.detect_gpu_vendor", return_value=gv.AMD), \
             mock.patch("backend.all.run_command") as run_mock:
            run_mock.return_value = (True, "", "")
            B.lib32_installed()
        queried_packages = run_mock.call_args[0][0]
        self.assertIn("libvulkan_radeon-32bit", queried_packages)


class GamingPackVulkan32GpuMappingTests(unittest.TestCase):
    """core.gaming_pack — same GPU-dependent rule, in the read-only
    Gaming Pack scan preview."""

    def _profile(self, vendor):
        from core import gaming_pack as gp
        return gp.SystemProfile(
            family="opensuse", distro_pretty_name="openSUSE Tumbleweed",
            package_manager="zypper", architecture="x86_64",
            gpu_driver="amdgpu" if vendor == gv.AMD else "unknown",
            gpu_driver_known_good=True, vulkan_ok=True,
            lib32_active=True, lib32_repo_hint="", distro_variant="tumbleweed",
            gpu_vendor=vendor,
        )

    def test_amd_profile_resolves_to_radeon_32bit_icd(self):
        from core import gaming_pack as gp
        packages = gp._packages_for("vulkan_32", self._profile(gv.AMD))
        self.assertEqual(packages, ["libvulkan1-32bit", "libvulkan_radeon-32bit"])

    def test_unknown_gpu_profile_is_not_verifiable_never_guessed(self):
        from core import gaming_pack as gp
        packages = gp._packages_for("vulkan_32", self._profile(gv.UNKNOWN))
        self.assertIsNone(packages)

    def test_nvidia_proprietary_profile_is_not_verifiable_never_guessed(self):
        from core import gaming_pack as gp
        packages = gp._packages_for("vulkan_32", self._profile(gv.NVIDIA_PROPRIETARY))
        self.assertIsNone(packages)


class ZypperFailureTests(unittest.TestCase):
    """A real Zypper failure (system locked, network error, ...) must
    degrade gracefully — never crash, never be reported as success."""

    def test_lib32_install_reports_real_zypper_stderr_on_failure(self):
        import backend.all as B
        from core.executor import CommandResult
        fail_result = CommandResult(
            ["zypper", "--non-interactive", "install", "Mesa-libGL1-32bit", "libvulkan1-32bit", "libvulkan_radeon-32bit"],
            False, 7, "", "System management is locked by the application with pid 1234", 0.2,
        )
        with mock.patch.multiple(B.distro, id="opensuse-tumbleweed", id_like="opensuse suse"), \
             mock.patch("backend.all.gpu_vendor.detect_gpu_vendor", return_value=gv.AMD), \
             mock.patch("backend.all.run_pkexec_full", return_value=fail_result), \
             mock.patch("backend.all.lib32_installed", return_value=False):
            result = B.lib32_install()
        self.assertFalse(result.ok)
        self.assertIn("locked", result.stderr)

    def test_gaming_pack_availability_check_survives_a_zypper_failure(self):
        from core import gaming_pack as gp
        fail_result = mock.Mock(error="System management is locked")
        with mock.patch("core.gaming_pack.run_command_full", return_value=fail_result):
            available = gp._is_available("opensuse", "mangohud")
        self.assertFalse(available)

    def test_gaming_pack_scan_does_not_crash_when_zypper_is_locked(self):
        from core import gaming_pack as gp
        profile = gp.SystemProfile(
            family="opensuse", distro_pretty_name="openSUSE Tumbleweed",
            package_manager="zypper", architecture="x86_64",
            gpu_driver="amdgpu", gpu_driver_known_good=True, vulkan_ok=True,
            lib32_active=True, lib32_repo_hint="", distro_variant="tumbleweed",
            gpu_vendor=gv.AMD,
        )
        with mock.patch("core.gaming_pack._is_installed", return_value=False), \
             mock.patch("core.gaming_pack.run_command_full", return_value=mock.Mock(error="System management is locked")):
            previews = gp.scan(profile)
        self.assertEqual(len(previews), len(gp.COMPONENTS))
        mangohud = next(p for p in previews if p.component_id == "mangohud")
        self.assertIn(mangohud.state, (gp.NOT_AVAILABLE, gp.NOT_VERIFIABLE))


if __name__ == "__main__":
    unittest.main()
