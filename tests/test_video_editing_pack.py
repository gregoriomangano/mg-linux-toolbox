"""Video Editing Pack V1 tests: analysis/preview only, no system mutation."""
import inspect
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import video_editing_pack as vp
from core.distro import distro
from core.executor import CommandResult


def _fake_distro(identifier, id_like=""):
    return mock.patch.multiple(distro, id=identifier, id_like=id_like)


def _which_without_ffmpeg(tool):
    """Package-manager query tools resolve to a fake path; ffmpeg itself
    does not — keeps FamilyDetectionTests focused on family detection
    instead of also depending on whether ffmpeg is really installed on
    whatever machine runs the test suite."""
    return None if tool == "ffmpeg" else "/usr/bin/tool"


def _result(stdout="", ok=True, error=""):
    return CommandResult(cmd=[], ok=ok, returncode=0 if ok else 1, stdout=stdout, stderr="", duration=0.0, error=error)


class FamilyDetectionTests(unittest.TestCase):
    def _detect(self, identifier, id_like=""):
        with _fake_distro(identifier, id_like), \
             mock.patch("core.video_editing_pack.shutil.which", side_effect=_which_without_ffmpeg):
            return vp.detect_system()

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
            ("arch", ""), ("manjaro", "arch"), ("endeavouros", "arch"), ("cachyos", "arch"),
        ):
            with self.subTest(identifier=identifier):
                profile = self._detect(identifier, id_like)
                self.assertEqual((profile.family, profile.package_manager), ("arch", "pacman"))

    def test_opensuse(self):
        profile = self._detect("opensuse-tumbleweed", "opensuse suse")
        self.assertEqual((profile.family, profile.package_manager), ("opensuse", "zypper"))

    def test_unknown_distribution_is_not_silently_treated_as_debian(self):
        profile = self._detect("futurelinux")
        self.assertEqual(profile.family, "unknown")
        self.assertEqual(profile.package_manager, "")
        self.assertFalse(profile.package_manager_available)

    def test_ffmpeg_absent_means_no_hwaccel_probe(self):
        with mock.patch("core.video_editing_pack.run_command_full") as run_full:
            profile = self._detect("debian", "")
        self.assertFalse(profile.ffmpeg_present)
        self.assertEqual(profile.hwaccels, [])
        run_full.assert_not_called()


class HwaccelProbeTests(unittest.TestCase):
    def test_parses_real_ffmpeg_output(self):
        stdout = "Hardware acceleration methods:\nvdpau\ncuda\nvaapi\n"
        with mock.patch("core.video_editing_pack.shutil.which", return_value="/usr/bin/ffmpeg"), \
             mock.patch("core.video_editing_pack.run_command_full", return_value=_result(stdout=stdout)):
            self.assertEqual(vp._probe_hwaccels(), ["vdpau", "cuda", "vaapi"])

    def test_failed_probe_command_returns_empty(self):
        with mock.patch("core.video_editing_pack.shutil.which", return_value="/usr/bin/ffmpeg"), \
             mock.patch("core.video_editing_pack.run_command_full", return_value=_result(ok=False, error="failed")):
            self.assertEqual(vp._probe_hwaccels(), [])

    def test_no_ffmpeg_binary_never_runs_a_command(self):
        with mock.patch("core.video_editing_pack.shutil.which", return_value=None), \
             mock.patch("core.video_editing_pack.run_command_full") as run_full:
            self.assertEqual(vp._probe_hwaccels(), [])
        run_full.assert_not_called()


class ComponentStateTests(unittest.TestCase):
    def _profile(self, family="debian", architecture="x86_64", manager_available=True):
        manager = {"debian": "apt", "fedora": "dnf", "arch": "pacman", "opensuse": "zypper"}.get(family, "")
        return vp.SystemProfile(
            family=family, distro_pretty_name="Test", package_manager=manager,
            architecture=architecture, package_manager_available=manager_available,
        )

    def _scan(self, profile=None, installed=False, available=True):
        with mock.patch("core.video_editing_pack._is_installed", return_value=installed), \
             mock.patch("core.video_editing_pack._availability_probe", return_value=(available, "repo-test")):
            return vp.scan(profile or self._profile())

    def test_package_already_present(self):
        preview = next(p for p in self._scan(installed=True) if p.component_id == "ffmpeg")
        self.assertEqual(preview.state, vp.ALREADY_INSTALLED)
        self.assertEqual(preview.installed_packages, ["ffmpeg"])
        self.assertEqual(preview.suggested_packages, [])

    def test_package_available_is_only_a_suggestion(self):
        preview = next(p for p in self._scan() if p.component_id == "kdenlive")
        self.assertEqual(preview.state, vp.AVAILABLE)
        self.assertEqual(preview.suggested_packages, ["kdenlive"])
        self.assertEqual(preview.installed_packages, [])

    def test_package_not_available(self):
        preview = next(p for p in self._scan(available=False) if p.component_id == "obs_studio")
        self.assertEqual(preview.state, vp.NOT_AVAILABLE)
        self.assertEqual(preview.unavailable_packages, ["obs-studio"])

    def test_missing_package_manager_is_not_verifiable(self):
        previews = self._scan(self._profile(manager_available=False))
        self.assertTrue(previews)
        self.assertTrue(all(p.state == vp.NOT_VERIFIABLE for p in previews))

    def test_package_query_exception_does_not_crash(self):
        with mock.patch("core.video_editing_pack._is_installed", side_effect=PermissionError("denied")):
            previews = vp.scan(self._profile())
        self.assertTrue(all(p.state == vp.NOT_VERIFIABLE for p in previews))

    def test_availability_probe_returning_unverifiable_does_not_crash(self):
        with mock.patch("core.video_editing_pack._is_installed", return_value=False), \
             mock.patch("core.video_editing_pack._availability_probe", return_value=(None, "")):
            previews = vp.scan(self._profile())
        self.assertTrue(all(p.state == vp.NOT_VERIFIABLE for p in previews))

    def test_scan_cancelled_stops_before_queries(self):
        with mock.patch("core.video_editing_pack._is_installed") as installed:
            self.assertEqual(vp.scan(self._profile(), cancel_check=lambda: True), [])
        installed.assert_not_called()

    def test_scan_forwards_job_so_an_active_query_can_be_cancelled(self):
        job = vp.Job()
        with mock.patch("core.video_editing_pack._is_installed", return_value=True) as installed:
            vp.scan(self._profile(), job=job)
        self.assertTrue(installed.called)
        self.assertTrue(all(call.kwargs.get("job") is job for call in installed.call_args_list))


class MappingTests(unittest.TestCase):
    def test_all_families_have_a_mapping_entry_for_every_component(self):
        for component_id, per_family in vp.COMPONENTS.items():
            with self.subTest(component_id=component_id):
                self.assertEqual(set(per_family), set(vp.FAMILIES))

    def test_common_component_list_is_just_ffmpeg(self):
        self.assertEqual(vp.COMMON_COMPONENTS, ("ffmpeg",))

    def test_debian_mapping_uses_expected_names(self):
        self.assertEqual(vp.COMPONENTS["ffmpeg"]["debian"], ["ffmpeg"])
        self.assertEqual(vp.COMPONENTS["obs_studio"]["debian"], ["obs-studio"])
        self.assertEqual(vp.COMPONENTS["kdenlive"]["debian"], ["kdenlive"])


class ReadOnlySafetyTests(unittest.TestCase):
    def test_backend_exposes_no_install_or_removal_api(self):
        for name in ("install", "remove", "build_install_plan", "build_removal_plan"):
            self.assertFalse(hasattr(vp, name), name)

    def test_backend_source_contains_no_privileged_or_mutating_command(self):
        source = inspect.getsource(vp)
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
             mock.patch("core.video_editing_pack._is_installed", return_value=False), \
             mock.patch("core.video_editing_pack._availability_probe", return_value=(True, "repo-test")):
            previews = vp.scan(profile)
        self.assertEqual(len(previews), len(vp.COMPONENTS))


if __name__ == "__main__":
    unittest.main()
