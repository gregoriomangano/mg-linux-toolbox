"""Gaming Pack V1 tests: analysis/preview only, no system mutation."""
import inspect
import os
import sys
import tempfile
import shutil
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import gaming_pack as gp
from core import gaming_readiness as gr
from core.distro import distro


def _fake_distro(identifier, id_like=""):
    return mock.patch.multiple(distro, id=identifier, id_like=id_like)


class FamilyDetectionTests(unittest.TestCase):
    def _detect(self, identifier, id_like=""):
        with _fake_distro(identifier, id_like), \
             mock.patch("core.gaming_pack.shutil.which", return_value="/usr/bin/tool"):
            return gp.detect_system()

    def test_debian_family_variants(self):
        for identifier, id_like in (
            ("debian", ""), ("ubuntu", "debian"), ("linuxmint", "ubuntu debian"),
            ("pop", "ubuntu debian"),
        ):
            with self.subTest(identifier=identifier):
                profile = self._detect(identifier, id_like)
                self.assertEqual((profile.family, profile.package_manager), ("debian", "apt"))

    def test_fedora(self):
        profile = self._detect("fedora")
        self.assertEqual((profile.family, profile.package_manager), ("fedora", "dnf"))

    def test_arch_family_variants(self):
        for identifier, id_like in (
            ("arch", ""), ("manjaro", "arch"), ("endeavouros", "arch"),
            ("cachyos", "arch"),
        ):
            with self.subTest(identifier=identifier):
                profile = self._detect(identifier, id_like)
                self.assertEqual((profile.family, profile.package_manager), ("arch", "pacman"))

    def test_opensuse_tumbleweed_variant(self):
        profile = self._detect("opensuse-tumbleweed", "opensuse suse")
        self.assertEqual((profile.family, profile.package_manager), ("opensuse", "zypper"))
        self.assertEqual(profile.distro_variant, "tumbleweed")

    def test_opensuse_leap_variant(self):
        profile = self._detect("opensuse-leap", "suse opensuse")
        self.assertEqual(profile.distro_variant, "leap")

    def test_unknown_distribution_is_not_silently_treated_as_debian(self):
        profile = self._detect("futurelinux")
        self.assertEqual(profile.family, "unknown")
        self.assertEqual(profile.package_manager, "")
        self.assertFalse(profile.package_manager_available)


class GpuDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _uevent(self, driver):
        path = os.path.join(self.tmp, f"card-{driver}-uevent")
        with open(path, "w") as handle:
            handle.write(f"DRIVER={driver}\n")
        return path

    def test_gpu_amd(self):
        with mock.patch("core.gaming_readiness.glob.glob", return_value=[self._uevent("amdgpu")]):
            item = gr.check_gpu_driver()
        self.assertEqual((item.detail, item.state), ("amdgpu", gr.READY))

    def test_gpu_intel(self):
        with mock.patch("core.gaming_readiness.glob.glob", return_value=[self._uevent("i915")]):
            item = gr.check_gpu_driver()
        self.assertEqual((item.detail, item.state), ("i915", gr.READY))

    def test_gpu_nvidia(self):
        with mock.patch("core.gaming_readiness.glob.glob", return_value=[self._uevent("nvidia")]):
            item = gr.check_gpu_driver()
        self.assertEqual((item.detail, item.state), ("nvidia", gr.READY))

    def test_gpu_root_is_injectable(self):
        with mock.patch("core.gaming_readiness.glob.glob", return_value=[]) as glob_call:
            gr.check_gpu_driver(sys_root="/fake-sys")
        self.assertTrue(glob_call.call_args.args[0].startswith("/fake-sys/"))


class ComponentStateTests(unittest.TestCase):
    def _profile(
        self, family="debian", lib32_active=True, architecture="x86_64",
        variant="", manager_available=True,
    ):
        manager = {"debian": "apt", "fedora": "dnf", "arch": "pacman", "opensuse": "zypper"}.get(family, "")
        return gp.SystemProfile(
            family=family, distro_pretty_name="Test", package_manager=manager,
            architecture=architecture, gpu_driver="amdgpu", gpu_driver_known_good=True,
            vulkan_ok=True, lib32_active=lib32_active, lib32_repo_hint="multilib-test",
            distro_variant=variant, package_manager_available=manager_available,
        )

    def _scan(self, profile=None, installed=False, available=True):
        with mock.patch("core.gaming_pack._is_installed", return_value=installed), \
             mock.patch("core.gaming_pack._availability_probe", return_value=(available, "repo-test")):
            return gp.scan(profile or self._profile())

    def test_package_already_present(self):
        preview = next(p for p in self._scan(installed=True) if p.component_id == "gamemode")
        self.assertEqual(preview.state, gp.ALREADY_INSTALLED)
        self.assertEqual(preview.installed_packages, ["gamemode"])
        self.assertEqual(preview.suggested_packages, [])

    def test_package_available_is_only_a_suggestion(self):
        preview = next(p for p in self._scan() if p.component_id == "gamemode")
        self.assertEqual(preview.state, gp.AVAILABLE)
        self.assertEqual(preview.suggested_packages, ["gamemode"])
        self.assertEqual(preview.installed_packages, [])

    def test_package_not_available(self):
        preview = next(p for p in self._scan(available=False) if p.component_id == "gamemode")
        self.assertEqual(preview.state, gp.NOT_AVAILABLE)
        self.assertEqual(preview.unavailable_packages, ["gamemode"])

    def test_partial_component_distinguishes_present_and_suggested_packages(self):
        def installed(family, package, job=None):
            return family == "debian" and package == "libvulkan1"

        with mock.patch("core.gaming_pack._is_installed", side_effect=installed), \
             mock.patch("core.gaming_pack._availability_probe", return_value=(True, "repo-test")):
            previews = gp.scan(self._profile())
        vulkan = next(p for p in previews if p.component_id == "vulkan_64")
        self.assertEqual(vulkan.state, gp.AVAILABLE)
        self.assertEqual(vulkan.installed_packages, ["libvulkan1"])
        self.assertEqual(vulkan.suggested_packages, ["vulkan-tools"])

    def test_steam_missing_on_fedora_reports_manual_repository_hint(self):
        steam = next(p for p in self._scan(self._profile("fedora"), available=False)
                     if p.component_id == "steam")
        self.assertEqual(steam.state, gp.NOT_AVAILABLE)

    def test_lib32_requires_existing_multilib_configuration(self):
        preview = next(p for p in self._scan(self._profile("arch", lib32_active=False))
                       if p.component_id == "vulkan_32")
        self.assertEqual(preview.state, gp.NOT_AVAILABLE)
        self.assertEqual(preview.repo_hint, "multilib-test")

    def test_lib32_not_suitable_on_non_x86(self):
        preview = next(p for p in self._scan(self._profile(architecture="aarch64"))
                       if p.component_id == "vulkan_32")
        self.assertEqual(preview.state, gp.NOT_SUITABLE)

    def test_missing_package_manager_is_not_verifiable(self):
        previews = self._scan(self._profile(manager_available=False))
        self.assertTrue(previews)
        self.assertTrue(all(p.state == gp.NOT_VERIFIABLE for p in previews))

    def test_package_query_exception_does_not_crash(self):
        with mock.patch("core.gaming_pack._is_installed", side_effect=PermissionError("denied")):
            previews = gp.scan(self._profile())
        self.assertTrue(all(p.state == gp.NOT_VERIFIABLE for p in previews))

    def test_scan_cancelled_stops_before_queries(self):
        with mock.patch("core.gaming_pack._is_installed") as installed:
            self.assertEqual(gp.scan(self._profile(), cancel_check=lambda: True), [])
        installed.assert_not_called()

    def test_scan_forwards_job_so_an_active_query_can_be_cancelled(self):
        job = gp.Job()
        with mock.patch("core.gaming_pack._is_installed", return_value=True) as installed:
            gp.scan(self._profile(), job=job)
        self.assertTrue(installed.called)
        self.assertTrue(all(call.kwargs.get("job") is job for call in installed.call_args_list))


class MappingTests(unittest.TestCase):
    def test_debian_mapping_uses_real_checked_names(self):
        self.assertEqual(gp.COMPONENTS["gamescope"]["debian"], ["gamescope"])
        self.assertEqual(gp.COMPONENTS["vulkan_64"]["debian"], ["libvulkan1", "vulkan-tools"])
        self.assertEqual(gp.COMPONENTS["steam_devices"]["debian"], ["steam-devices"])

    def test_fedora_mapping_uses_rpm_names(self):
        self.assertEqual(gp.COMPONENTS["vulkan_32"]["fedora"], ["vulkan-loader.i686"])
        self.assertEqual(gp.COMPONENTS["gamescope"]["fedora"], ["gamescope"])
        self.assertEqual(gp.COMPONENTS["steam_devices"]["fedora"], ["steam-devices"])

    def test_arch_mapping_includes_official_steam_device_rules(self):
        self.assertEqual(gp.COMPONENTS["steam_devices"]["arch"], ["steam-devices"])
        self.assertEqual(gp.COMPONENTS["vulkan_32"]["arch"], ["lib32-vulkan-icd-loader"])

    def test_common_component_list_matches_verified_cross_distro_subset(self):
        self.assertEqual(gp.COMMON_COMPONENTS, ("gamemode", "mangohud", "goverlay", "vulkan_64"))

    def test_opensuse_leap_does_not_inherit_tumbleweed_guesses(self):
        profile = ComponentStateTests()._profile("opensuse", variant="leap")
        previews = ComponentStateTests()._scan(profile)
        self.assertEqual(next(p for p in previews if p.component_id == "steam").state, gp.NOT_VERIFIABLE)
        self.assertEqual(next(p for p in previews if p.component_id == "mangohud").state, gp.NOT_VERIFIABLE)

    def test_opensuse_tumbleweed_uses_only_confirmed_names(self):
        profile = ComponentStateTests()._profile("opensuse", variant="tumbleweed")
        previews = ComponentStateTests()._scan(profile)
        mangohud = next(p for p in previews if p.component_id == "mangohud")
        self.assertEqual(mangohud.packages, ["mangohud"])
        self.assertNotIn("mangohud-32bit", mangohud.packages)


class ReadOnlySafetyTests(unittest.TestCase):
    def test_backend_exposes_no_install_or_removal_api(self):
        for name in ("install", "remove", "build_install_plan", "build_removal_plan"):
            self.assertFalse(hasattr(gp, name), name)

    def test_backend_source_contains_no_privileged_or_mutating_command(self):
        source = inspect.getsource(gp)
        for forbidden in (
            "run_pkexec", "apt-get", "dnf install", "pacman -S", "zypper install",
            "curl | bash", "full-upgrade", "dist-upgrade",
        ):
            self.assertNotIn(forbidden, source)

    def test_scan_never_calls_privileged_executor(self):
        def fail(*_args, **_kwargs):
            raise AssertionError("read-only scan attempted a privileged command")

        profile = ComponentStateTests()._profile()
        with mock.patch("core.executor.run_pkexec_full", side_effect=fail), \
             mock.patch("core.executor.run_pkexec", side_effect=fail), \
             mock.patch("core.gaming_pack._is_installed", return_value=False), \
             mock.patch("core.gaming_pack._availability_probe", return_value=(True, "repo-test")):
            previews = gp.scan(profile)
        self.assertEqual(len(previews), len(gp.COMPONENTS))

    def test_scan_never_proposes_removal_of_preexisting_packages(self):
        source = inspect.getsource(gp)
        self.assertNotIn("remove", source.lower())
        self.assertFalse(any(hasattr(p, "remove") for p in self._all_previews()))

    @staticmethod
    def _all_previews():
        profile = ComponentStateTests()._profile()
        with mock.patch("core.gaming_pack._is_installed", return_value=True):
            return gp.scan(profile)


if __name__ == "__main__":
    unittest.main()
