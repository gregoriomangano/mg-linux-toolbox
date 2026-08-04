"""
Tests for core.ssh_detect — fixes the Beta 4 report where "Sicurezza"
still let the user toggle "Accesso root tramite SSH" even though SSH
wasn't installed (root_ssh_disabled() silently treated a missing
sshd_config as "not disabled" == indistinguishable from "installed and
allowed"). This module must never read the config file when the
server isn't installed, and must say so distinctly from a config that
simply couldn't be read.
"""
import os
import tempfile
import unittest
from unittest import mock

from core.ssh_detect import (
    root_ssh_state, openssh_server_installed,
    STATE_NOT_INSTALLED, STATE_DISABLED, STATE_ALLOWED, STATE_UNDETERMINED,
)


class SshServerInstalledTests(unittest.TestCase):
    def test_installed_via_package_db(self):
        with mock.patch("core.distro.distro.is_installed", return_value=True):
            self.assertTrue(openssh_server_installed())

    def test_not_installed_when_no_signal_present(self):
        with mock.patch("core.distro.distro.is_installed", return_value=False), \
             mock.patch("core.ssh_detect._sshd_binary_present", return_value=False), \
             mock.patch("core.ssh_detect._service_unit_known", return_value=False):
            self.assertFalse(openssh_server_installed())

    def test_installed_via_binary_even_if_package_db_check_fails(self):
        with mock.patch("core.distro.distro.is_installed", return_value=False), \
             mock.patch("core.ssh_detect._sshd_binary_present", return_value=True):
            self.assertTrue(openssh_server_installed())


class RootSshStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._config_path = os.path.join(self._tmp.name, "sshd_config")

    def _write(self, content: str):
        with open(self._config_path, "w") as f:
            f.write(content)

    def test_not_installed_never_reads_config(self):
        """The exact bug: a missing config on a machine WITHOUT sshd
        must report not_installed, never 'allowed'."""
        with mock.patch("core.ssh_detect.openssh_server_installed", return_value=False):
            state = root_ssh_state(sshd_config_path=os.path.join(self._tmp.name, "does-not-exist"))
        self.assertEqual(state, STATE_NOT_INSTALLED)

    def test_installed_permit_root_login_no_is_disabled(self):
        self._write("Port 22\nPermitRootLogin no\n")
        with mock.patch("core.ssh_detect.openssh_server_installed", return_value=True):
            state = root_ssh_state(sshd_config_path=self._config_path)
        self.assertEqual(state, STATE_DISABLED)

    def test_installed_permit_root_login_yes_is_allowed(self):
        self._write("Port 22\nPermitRootLogin yes\n")
        with mock.patch("core.ssh_detect.openssh_server_installed", return_value=True):
            state = root_ssh_state(sshd_config_path=self._config_path)
        self.assertEqual(state, STATE_ALLOWED)

    def test_installed_directive_absent_defaults_to_allowed(self):
        self._write("Port 22\n")
        with mock.patch("core.ssh_detect.openssh_server_installed", return_value=True):
            state = root_ssh_state(sshd_config_path=self._config_path)
        self.assertEqual(state, STATE_ALLOWED)

    def test_installed_but_config_missing_is_undetermined_not_allowed(self):
        with mock.patch("core.ssh_detect.openssh_server_installed", return_value=True):
            state = root_ssh_state(sshd_config_path=os.path.join(self._tmp.name, "does-not-exist"))
        self.assertEqual(state, STATE_UNDETERMINED)

    def test_prohibit_password_counts_as_allowed_not_disabled(self):
        self._write("PermitRootLogin prohibit-password\n")
        with mock.patch("core.ssh_detect.openssh_server_installed", return_value=True):
            state = root_ssh_state(sshd_config_path=self._config_path)
        self.assertEqual(state, STATE_ALLOWED)


if __name__ == "__main__":
    unittest.main()
