"""Generic PackInstaller engine: only ever installs packages a real
scan() already reported as available, and only for explicitly selected
components. Exercised through video_editing_pack, the first real
consumer besides gaming_pack (which keeps its own, pre-existing
gaming_pack_installer.py)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import video_editing_pack as vep
from core.package_pack_installer import PackInstaller
from core.executor import CommandResult

installer = PackInstaller(vep, "test_video_editing_pack", "video_pack")


def _preview(component_id, state, suggested=None, installed=None):
    return vep.ComponentPreview(
        component_id, False, state, ["pkg"],
        suggested_packages=suggested or [], installed_packages=installed or [],
    )


def _profile(family="opensuse"):
    return vep.SystemProfile(
        family=family, distro_pretty_name="Test", package_manager=family, architecture="x86_64",
    )


class InstallSelectedTests(unittest.TestCase):
    def test_only_available_components_are_installed(self):
        previews = [
            _preview("obs_studio", vep.AVAILABLE, ["obs-studio"]),
            _preview("kdenlive", vep.NOT_AVAILABLE, ["kdenlive"]),
        ]
        ok_result = CommandResult(["zypper"], True, 0, "", "", 0.1)
        with mock.patch("core.package_pack_installer.run_pkexec_full", return_value=ok_result) as m, \
             mock.patch("core.video_editing_pack._is_installed", return_value=True), \
             mock.patch("core.package_pack_installer._preflight_availability", return_value=(True, "ok")), \
             mock.patch("core.package_pack_installer.pps.record_install") as record_mock:
            result = installer.install_selected(["obs_studio", "kdenlive"], _profile(), previews)
        self.assertTrue(result.ok)
        self.assertEqual(result.installed_packages, ["obs-studio"])
        self.assertEqual(result.verified_packages, ["obs-studio"])
        self.assertEqual(result.skipped_component_ids, ["kdenlive"])
        m.assert_called_once()
        self.assertEqual(m.call_args[0][0], ["zypper", "--non-interactive", "install", "obs-studio"])
        record_mock.assert_called_once()
        self.assertEqual(record_mock.call_args[0][0], "test_video_editing_pack")

    def test_nothing_selected_is_available_does_not_call_pkexec(self):
        previews = [_preview("kdenlive", vep.NOT_AVAILABLE, ["kdenlive"])]
        with mock.patch("core.package_pack_installer.run_pkexec_full") as m:
            result = installer.install_selected(["kdenlive"], _profile(), previews)
        m.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "video_pack_install_nothing_selected")
        self.assertEqual(result.skipped_component_ids, ["kdenlive"])

    def test_unknown_family_never_calls_pkexec(self):
        previews = [_preview("obs_studio", vep.AVAILABLE, ["obs-studio"])]
        with mock.patch("core.package_pack_installer.run_pkexec_full") as m:
            result = installer.install_selected(["obs_studio"], _profile("solaris"), previews)
        m.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "video_pack_install_unsupported_family")

    def test_failed_install_reports_technical_detail_without_installed_packages(self):
        previews = [_preview("obs_studio", vep.AVAILABLE, ["obs-studio"])]
        fail_result = CommandResult(["zypper"], False, 1, "", "not found", 0.1)
        with mock.patch("core.package_pack_installer.run_pkexec_full", return_value=fail_result), \
             mock.patch("core.video_editing_pack._is_installed", return_value=False), \
             mock.patch("core.package_pack_installer._preflight_availability", return_value=(True, "ok")):
            result = installer.install_selected(["obs_studio"], _profile(), previews)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "video_pack_install_failed")
        self.assertEqual(result.installed_packages, [])
        self.assertIn("not found", result.technical_detail)
        self.assertIn("exit code", result.technical_detail)

    def test_nonzero_exit_code_with_package_really_installed_is_success_with_warning(self):
        previews = [_preview("obs_studio", vep.AVAILABLE, ["obs-studio"])]
        warn_result = CommandResult(["zypper"], False, 106, "", "Warning: Skipping repository.", 0.4)
        with mock.patch("core.package_pack_installer.run_pkexec_full", return_value=warn_result), \
             mock.patch("core.video_editing_pack._is_installed", return_value=True), \
             mock.patch("core.package_pack_installer._preflight_availability", return_value=(True, "ok")), \
             mock.patch("core.package_pack_installer.pps.record_install") as record_mock:
            result = installer.install_selected(["obs_studio"], _profile(), previews)
        self.assertTrue(result.ok)
        self.assertEqual(result.friendly_message, "video_pack_install_done_with_warning")
        self.assertEqual(result.verified_packages, ["obs-studio"])
        record_mock.assert_called_once()

    def test_partial_install_registers_only_the_fully_verified_component(self):
        previews = [
            _preview("obs_studio", vep.AVAILABLE, ["obs-studio"]),
            _preview("kdenlive", vep.AVAILABLE, ["kdenlive"]),
        ]
        ok_result = CommandResult(["zypper"], True, 0, "", "", 0.2)

        def fake_is_installed(family, package, job=None):
            return package == "obs-studio"

        with mock.patch("core.package_pack_installer.run_pkexec_full", return_value=ok_result), \
             mock.patch("core.video_editing_pack._is_installed", side_effect=fake_is_installed), \
             mock.patch("core.package_pack_installer._preflight_availability", return_value=(True, "ok")), \
             mock.patch("core.package_pack_installer.pps.record_install") as record_mock:
            result = installer.install_selected(["obs_studio", "kdenlive"], _profile(), previews)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "video_pack_install_partial")
        self.assertEqual(result.verified_packages, ["obs-studio"])
        record_mock.assert_called_once_with(
            "test_video_editing_pack", mock.ANY, "obs_studio", ["obs-studio"], [],
            ["zypper", "--non-interactive", "install", "obs-studio", "kdenlive"],
        )

    def test_preflight_blocks_install_when_package_disappears(self):
        previews = [_preview("obs_studio", vep.AVAILABLE, ["obs-studio"])]
        with mock.patch("core.package_pack_installer._preflight_availability", return_value=(False, "blocking package: obs-studio")), \
             mock.patch("core.package_pack_installer.run_pkexec_full") as m:
            result = installer.install_selected(["obs_studio"], _profile(), previews)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "video_pack_install_precheck_failed")
        self.assertIn("blocking package: obs-studio", result.technical_detail)
        m.assert_not_called()

    def test_remove_only_recorded_packages(self):
        previews = [_preview("obs_studio", vep.ALREADY_INSTALLED, installed=["obs-studio"])]
        ok_result = CommandResult(["zypper"], True, 0, "", "", 0.1)
        with mock.patch("core.package_pack_installer.pps.get_record",
                         return_value={"family": "opensuse", "installed_packages": ["obs-studio"]}), \
             mock.patch("core.video_editing_pack._is_installed", side_effect=[True, False]), \
             mock.patch("core.package_pack_installer.run_pkexec_full", return_value=ok_result) as remove_mock, \
             mock.patch("core.package_pack_installer.pps.clear_records") as clear_mock:
            result = installer.remove_selected(["obs_studio"], _profile(), previews)
        self.assertTrue(result.ok)
        self.assertEqual(result.friendly_message, "video_pack_remove_done")
        self.assertEqual(remove_mock.call_args[0][0], ["zypper", "--non-interactive", "remove", "obs-studio"])
        clear_mock.assert_called_once_with("test_video_editing_pack", ["obs_studio"])

    def test_removable_component_ids_requires_matching_family_and_real_packages(self):
        previews = [_preview("obs_studio", vep.ALREADY_INSTALLED, installed=["obs-studio"])]
        with mock.patch("core.package_pack_installer.pps.get_record",
                         return_value={"family": "opensuse", "installed_packages": ["obs-studio"]}), \
             mock.patch("core.video_editing_pack._is_installed", return_value=True):
            removable = installer.removable_component_ids(_profile("opensuse"), previews)
        self.assertEqual(removable, {"obs_studio"})

        with mock.patch("core.package_pack_installer.pps.get_record",
                         return_value={"family": "debian", "installed_packages": ["obs-studio"]}):
            removable = installer.removable_component_ids(_profile("opensuse"), previews)
        self.assertEqual(removable, set())


if __name__ == "__main__":
    unittest.main()
