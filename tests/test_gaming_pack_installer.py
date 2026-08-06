"""Gaming Pack installer: only ever installs packages a real scan()
already reported as available, and only for explicitly selected components."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import gaming_pack as gp
from core import gaming_pack_installer as installer
from core.executor import CommandResult


def _preview(component_id, state, suggested=None):
    return gp.ComponentPreview(component_id, False, state, ["pkg"], suggested_packages=suggested or [])


def _profile(family="opensuse"):
    return gp.SystemProfile(
        family=family,
        distro_pretty_name="Test",
        package_manager=family,
        architecture="x86_64",
        gpu_driver="amdgpu",
        gpu_driver_known_good=True,
        vulkan_ok=True,
        lib32_active=True,
        lib32_repo_hint="",
        gpu_vendor="amd",
    )


class InstallSelectedTests(unittest.TestCase):
    def test_only_available_components_are_installed(self):
        previews = [
            _preview("mangohud", gp.AVAILABLE, ["mangohud"]),
            _preview("lutris", gp.REPO_NEEDED, ["lutris"]),
        ]
        ok_result = CommandResult(["zypper"], True, 0, "", "", 0.1)
        with mock.patch("core.gaming_pack_installer.run_pkexec_full", return_value=ok_result) as m, \
             mock.patch("core.gaming_pack_installer.gp._is_installed", return_value=True), \
             mock.patch("core.gaming_pack_installer._preflight_availability", return_value=(True, "ok")), \
             mock.patch("core.gaming_pack_installer.gps.record_install") as record_mock:
            result = installer.install_selected(
                ["mangohud", "lutris"], _profile(), previews,
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.installed_packages, ["mangohud"])
        self.assertEqual(result.verified_packages, ["mangohud"])
        self.assertEqual(result.skipped_component_ids, ["lutris"])
        m.assert_called_once()
        called_cmd = m.call_args[0][0]
        self.assertEqual(called_cmd, ["zypper", "--non-interactive", "install", "mangohud"])
        record_mock.assert_called_once()

    def test_nothing_selected_is_available_does_not_call_pkexec(self):
        previews = [_preview("lutris", gp.REPO_NEEDED, ["lutris"])]
        with mock.patch("core.gaming_pack_installer.run_pkexec_full") as m:
            result = installer.install_selected(["lutris"], _profile(), previews)
        m.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.skipped_component_ids, ["lutris"])

    def test_unknown_family_never_calls_pkexec(self):
        previews = [_preview("mangohud", gp.AVAILABLE, ["mangohud"])]
        with mock.patch("core.gaming_pack_installer.run_pkexec_full") as m:
            result = installer.install_selected(["mangohud"], _profile("solaris"), previews)
        m.assert_not_called()
        self.assertFalse(result.ok)

    def test_failed_install_reports_technical_detail_without_installed_packages(self):
        previews = [_preview("mangohud", gp.AVAILABLE, ["mangohud"])]
        fail_result = CommandResult(["zypper"], False, 1, "", "not found", 0.1)
        with mock.patch("core.gaming_pack_installer.run_pkexec_full", return_value=fail_result), \
             mock.patch("core.gaming_pack_installer._preflight_availability", return_value=(True, "ok")):
            result = installer.install_selected(["mangohud"], _profile(), previews)
        self.assertFalse(result.ok)
        self.assertEqual(result.installed_packages, [])
        self.assertIn("not found", result.technical_detail)
        self.assertIn("exit code", result.technical_detail)
        self.assertIn("package manager: opensuse", result.technical_detail)

    def test_successful_transaction_fails_if_post_install_verification_misses_a_package(self):
        previews = [_preview("mangohud", gp.AVAILABLE, ["mangohud"])]
        ok_result = CommandResult(["zypper"], True, 0, "", "", 0.1)
        with mock.patch("core.gaming_pack_installer.run_pkexec_full", return_value=ok_result), \
             mock.patch("core.gaming_pack_installer.gp._is_installed", return_value=False), \
             mock.patch("core.gaming_pack_installer._preflight_availability", return_value=(True, "ok")):
            result = installer.install_selected(["mangohud"], _profile(), previews)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "gaming_pack_install_verification_failed")
        self.assertIn("missing after successful transaction", result.technical_detail)

    def test_preflight_blocks_install_when_package_disappears(self):
        previews = [_preview("mangohud", gp.AVAILABLE, ["mangohud"])]
        with mock.patch("core.gaming_pack_installer._preflight_availability", return_value=(False, "blocking package: mangohud")), \
             mock.patch("core.gaming_pack_installer.run_pkexec_full") as m:
            result = installer.install_selected(["mangohud"], _profile(), previews)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "gaming_pack_install_precheck_failed")
        self.assertIn("blocking package: mangohud", result.technical_detail)
        m.assert_not_called()

    def test_remove_only_recorded_packages(self):
        previews = [
            gp.ComponentPreview("mangohud", False, gp.ALREADY_INSTALLED, ["mangohud"], installed_packages=["mangohud"])
        ]
        ok_result = CommandResult(["zypper"], True, 0, "", "", 0.1)
        with mock.patch("core.gaming_pack_installer.gps.get_record", return_value={
            "family": "opensuse",
            "installed_packages": ["mangohud"],
        }), \
             mock.patch("core.gaming_pack_installer.gp._is_installed", side_effect=[True, False]), \
             mock.patch("core.gaming_pack_installer.run_pkexec_full", return_value=ok_result) as remove_mock, \
             mock.patch("core.gaming_pack_installer.gps.clear_records") as clear_mock:
            result = installer.remove_selected(["mangohud"], _profile(), previews)
        self.assertTrue(result.ok)
        self.assertEqual(result.friendly_message, "gaming_pack_remove_done")
        self.assertEqual(remove_mock.call_args[0][0], ["zypper", "--non-interactive", "remove", "mangohud"])
        clear_mock.assert_called_once_with(["mangohud"])


if __name__ == "__main__":
    unittest.main()
