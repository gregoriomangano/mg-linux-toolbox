"""
Tests for backend.all's automatic-updates functions — one real
mechanism per distro family, never mixed: unattended-upgrades (Debian/
Ubuntu/Pop/Mint), dnf-automatic or dnf5-plugin-automatic depending on
whether DNF5 is in use (Fedora), pacman-contrib/checkupdates as a
notify-only check that never runs `pacman -Sy` or installs anything on
its own (Arch), and transactional-update.timer when really present,
falling back to zypper-refresh.timer otherwise (openSUSE).
"""
import contextlib
import unittest
from unittest import mock

import backend.all as B


def _distro_flags(stack, **flags):
    for name in ("is_arch", "is_fedora", "is_opensuse", "is_debian"):
        stack.enter_context(mock.patch.object(
            type(B.distro), name,
            new_callable=mock.PropertyMock, return_value=flags.get(name, False)))


class FedoraDnfVersionTests(unittest.TestCase):
    def test_dnf5_active_when_binary_present(self):
        with mock.patch.object(B, "_cmd_exists", side_effect=lambda c: c == "dnf5"):
            self.assertTrue(B.dnf5_active())

    def test_dnf4_when_dnf5_binary_absent(self):
        with mock.patch.object(B, "_cmd_exists", return_value=False):
            self.assertFalse(B.dnf5_active())

    def test_dnf5_unit_and_package_names(self):
        with mock.patch.object(B, "dnf5_active", return_value=True):
            self.assertEqual(B._fedora_automatic_unit(), "dnf5-automatic.timer")
            self.assertEqual(B._fedora_automatic_package()["fedora"], "dnf5-plugin-automatic")

    def test_dnf4_unit_and_package_names(self):
        with mock.patch.object(B, "dnf5_active", return_value=False):
            self.assertEqual(B._fedora_automatic_unit(), "dnf-automatic.timer")
            self.assertEqual(B._fedora_automatic_package()["fedora"], "dnf-automatic")


class FedoraDispatchTests(unittest.TestCase):
    def test_auto_updates_active_uses_dnf5_unit_when_dnf5(self):
        with contextlib.ExitStack() as stack:
            _distro_flags(stack, is_fedora=True)
            stack.enter_context(mock.patch.object(B, "dnf5_active", return_value=True))
            stack.enter_context(mock.patch.object(B, "_service_active", return_value=True) )
            self.assertTrue(B.auto_updates_active())
            B._service_active.assert_called_with("dnf5-automatic.timer")

    def test_auto_updates_set_installs_dnf5_package_when_missing(self):
        with contextlib.ExitStack() as stack:
            _distro_flags(stack, is_fedora=True)
            stack.enter_context(mock.patch.object(B, "dnf5_active", return_value=True))
            stack.enter_context(mock.patch.object(B.distro, "is_installed", return_value=False))
            stack.enter_context(mock.patch.object(B.distro, "install_cmd", return_value=["dnf5", "install", "-y", "dnf5-plugin-automatic"]))
            mock_pkexec = stack.enter_context(mock.patch.object(B, "run_pkexec"))
            stack.enter_context(mock.patch.object(B, "_service_set", return_value=True))
            B.auto_updates_set(True)
            mock_pkexec.assert_called_once_with(["dnf5", "install", "-y", "dnf5-plugin-automatic"])


class OpenSuseTests(unittest.TestCase):
    def test_uses_transactional_update_when_present(self):
        with contextlib.ExitStack() as stack:
            _distro_flags(stack, is_opensuse=True)
            stack.enter_context(mock.patch.object(B, "_cmd_exists", return_value=True))
            mock_service_active = stack.enter_context(mock.patch.object(B, "_service_active", return_value=True))
            B.auto_updates_active()
            mock_service_active.assert_called_with("transactional-update.timer")

    def test_falls_back_to_zypper_refresh_when_transactional_update_absent(self):
        with contextlib.ExitStack() as stack:
            _distro_flags(stack, is_opensuse=True)
            stack.enter_context(mock.patch.object(B, "_cmd_exists", return_value=False))
            mock_service_active = stack.enter_context(mock.patch.object(B, "_service_active", return_value=False))
            B.auto_updates_active()
            mock_service_active.assert_called_with("zypper-refresh.timer")

    def test_set_toggles_the_real_mechanism_in_use(self):
        with contextlib.ExitStack() as stack:
            _distro_flags(stack, is_opensuse=True)
            stack.enter_context(mock.patch.object(B, "_cmd_exists", return_value=True))
            mock_service_set = stack.enter_context(mock.patch.object(B, "_service_set", return_value=True))
            B.auto_updates_set(True)
            mock_service_set.assert_called_once_with("transactional-update.timer", True)


class ArchNeverAutoInstallsTests(unittest.TestCase):
    def test_set_on_never_calls_pacman_sy_or_syu(self):
        with contextlib.ExitStack() as stack:
            _distro_flags(stack, is_arch=True)
            stack.enter_context(mock.patch.object(B.distro, "is_installed", return_value=True))
            mock_run = stack.enter_context(mock.patch.object(B, "run_command", return_value=(True, "active", "")))
            stack.enter_context(mock.patch.object(B, "_write_user_unit"))
            B.auto_updates_set(True)
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            self.assertNotIn("-Sy", cmd)
            self.assertNotIn("-Syu", cmd)

    def test_set_off_disables_the_user_timer_only(self):
        with contextlib.ExitStack() as stack:
            _distro_flags(stack, is_arch=True)
            mock_run = stack.enter_context(mock.patch.object(B, "run_command", return_value=(True, "inactive", "")))
            B.auto_updates_set(False)
        cmd = mock_run.call_args_list[0][0][0]
        self.assertEqual(cmd, ["systemctl", "--user", "disable", "--now", f"{B._ARCH_UPDATE_UNIT_NAME}.timer"])


class DebianFamilyTests(unittest.TestCase):
    def test_pop_os_only_ever_uses_unattended_upgrades(self):
        with contextlib.ExitStack() as stack:
            _distro_flags(stack, is_debian=True)
            stack.enter_context(mock.patch.object(B.distro, "is_installed", return_value=True))
            mock_service_set = stack.enter_context(mock.patch.object(B, "_service_set", return_value=True))
            B.auto_updates_set(True)
            mock_service_set.assert_called_once_with("unattended-upgrades", True)


if __name__ == "__main__":
    unittest.main()
