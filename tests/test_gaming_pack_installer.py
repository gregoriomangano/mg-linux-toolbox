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
        """A hard failure: the transaction exits non-zero AND the
        package really never made it onto the system."""
        previews = [_preview("mangohud", gp.AVAILABLE, ["mangohud"])]
        fail_result = CommandResult(["zypper"], False, 1, "", "not found", 0.1)
        with mock.patch("core.gaming_pack_installer.run_pkexec_full", return_value=fail_result), \
             mock.patch("core.gaming_pack_installer.gp._is_installed", return_value=False), \
             mock.patch("core.gaming_pack_installer._preflight_availability", return_value=(True, "ok")):
            result = installer.install_selected(["mangohud"], _profile(), previews)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "gaming_pack_install_failed")
        self.assertEqual(result.installed_packages, [])
        self.assertIn("not found", result.technical_detail)
        self.assertIn("exit code", result.technical_detail)
        self.assertIn("package manager: opensuse", result.technical_detail)

    def test_successful_exit_code_but_package_missing_is_still_a_real_failure(self):
        """A clean exit code alone must never be trusted either: if the
        package genuinely isn't there afterwards, this is a failure."""
        previews = [_preview("mangohud", gp.AVAILABLE, ["mangohud"])]
        ok_result = CommandResult(["zypper"], True, 0, "", "", 0.1)
        with mock.patch("core.gaming_pack_installer.run_pkexec_full", return_value=ok_result), \
             mock.patch("core.gaming_pack_installer.gp._is_installed", return_value=False), \
             mock.patch("core.gaming_pack_installer._preflight_availability", return_value=(True, "ok")), \
             mock.patch("core.gaming_pack_installer.gps.record_install") as record_mock:
            result = installer.install_selected(["mangohud"], _profile(), previews)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "gaming_pack_install_failed")
        record_mock.assert_not_called()

    def test_nonzero_exit_code_with_package_really_installed_is_success_with_warning(self):
        """The real bug found on openSUSE Tumbleweed: Zypper exits 106
        because an unrelated third-party repository (e.g. warpdotdev)
        has a signature/metadata problem, while the requested package
        (mangohud-32bit in the real report) is genuinely installed. The
        Toolbox must report success with a warning, not a plain
        failure — and it must still remember the install so the
        package can be removed safely later."""
        previews = [_preview("mangohud", gp.AVAILABLE, ["mangohud"])]
        warn_result = CommandResult(
            ["zypper"], False, 106, "",
            "Warning: Skipping repository 'warpdotdev' because of the above error.",
            0.4,
        )
        with mock.patch("core.gaming_pack_installer.run_pkexec_full", return_value=warn_result), \
             mock.patch("core.gaming_pack_installer.gp._is_installed", return_value=True), \
             mock.patch("core.gaming_pack_installer._preflight_availability", return_value=(True, "ok")), \
             mock.patch("core.gaming_pack_installer.gps.record_install") as record_mock:
            result = installer.install_selected(["mangohud"], _profile(), previews)
        self.assertTrue(result.ok)
        self.assertEqual(result.friendly_message, "gaming_pack_install_done_with_warning")
        self.assertEqual(result.verified_packages, ["mangohud"])
        self.assertIn("warpdotdev", result.technical_detail)
        self.assertIn("exit code: 106", result.technical_detail)
        record_mock.assert_called_once()

    def test_partial_install_registers_only_the_fully_verified_component(self):
        """Two components requested, only one fully lands — the
        Toolbox must say "parziale", must not claim ownership of the
        component that didn't fully install, and must still record the
        one that did."""
        previews = [
            _preview("mangohud", gp.AVAILABLE, ["mangohud"]),
            _preview("gamemode", gp.AVAILABLE, ["gamemode"]),
        ]
        ok_result = CommandResult(["zypper"], True, 0, "", "", 0.2)

        def fake_is_installed(family, package, job=None):
            return package == "mangohud"

        with mock.patch("core.gaming_pack_installer.run_pkexec_full", return_value=ok_result), \
             mock.patch("core.gaming_pack_installer.gp._is_installed", side_effect=fake_is_installed), \
             mock.patch("core.gaming_pack_installer._preflight_availability", return_value=(True, "ok")), \
             mock.patch("core.gaming_pack_installer.gps.record_install") as record_mock:
            result = installer.install_selected(["mangohud", "gamemode"], _profile(), previews)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "gaming_pack_install_partial")
        self.assertEqual(result.verified_packages, ["mangohud"])
        record_mock.assert_called_once_with(
            mock.ANY, "mangohud", ["mangohud"], [], ["zypper", "--non-interactive", "install", "mangohud", "gamemode"],
        )

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

    def test_remove_nonzero_exit_code_with_package_really_gone_is_success_with_warning(self):
        """Symmetric to the install case: a removal transaction can
        also exit non-zero because of an unrelated repository problem
        while genuinely removing the requested package."""
        previews = [
            gp.ComponentPreview("mangohud", False, gp.ALREADY_INSTALLED, ["mangohud"], installed_packages=["mangohud"])
        ]
        warn_result = CommandResult(["zypper"], False, 106, "", "Warning: Skipping repository 'warpdotdev'.", 0.3)
        with mock.patch("core.gaming_pack_installer.gps.get_record", return_value={
            "family": "opensuse",
            "installed_packages": ["mangohud"],
        }), \
             mock.patch("core.gaming_pack_installer.gp._is_installed", side_effect=[True, False]), \
             mock.patch("core.gaming_pack_installer.run_pkexec_full", return_value=warn_result), \
             mock.patch("core.gaming_pack_installer.gps.clear_records") as clear_mock:
            result = installer.remove_selected(["mangohud"], _profile(), previews)
        self.assertTrue(result.ok)
        self.assertEqual(result.friendly_message, "gaming_pack_remove_done_with_warning")
        self.assertIn("warpdotdev", result.technical_detail)
        clear_mock.assert_called_once_with(["mangohud"])

    def test_partial_remove_keeps_the_record_for_the_still_installed_component(self):
        previews = [
            gp.ComponentPreview("mangohud", False, gp.ALREADY_INSTALLED, ["mangohud"], installed_packages=["mangohud"]),
            gp.ComponentPreview("gamemode", False, gp.ALREADY_INSTALLED, ["gamemode"], installed_packages=["gamemode"]),
        ]
        ok_result = CommandResult(["zypper"], True, 0, "", "", 0.2)
        records = {
            "mangohud": {"family": "opensuse", "installed_packages": ["mangohud"]},
            "gamemode": {"family": "opensuse", "installed_packages": ["gamemode"]},
        }
        # Call order is deterministic: precheck(mangohud), precheck(gamemode),
        # then post-removal check(mangohud), post-removal check(gamemode).
        # mangohud is really removed; gamemode's removal didn't take.
        is_installed_calls = [True, True, False, True]

        with mock.patch("core.gaming_pack_installer.gps.get_record", side_effect=lambda cid: records[cid]), \
             mock.patch("core.gaming_pack_installer.gp._is_installed", side_effect=is_installed_calls), \
             mock.patch("core.gaming_pack_installer.run_pkexec_full", return_value=ok_result), \
             mock.patch("core.gaming_pack_installer.gps.clear_records") as clear_mock:
            result = installer.remove_selected(["mangohud", "gamemode"], _profile(), previews)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "gaming_pack_remove_partial")
        clear_mock.assert_called_once_with(["mangohud"])


if __name__ == "__main__":
    unittest.main()
