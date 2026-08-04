"""
Tests for core.firewall_detect — fixes the Peppermint/GUFW report
(GUFW installed, UFW enabled with the "home" profile, Toolbox still
said "not installed" because `ufw status` needs root and failed
silently as a normal user). Covers the ID_LIKE-based Debian-like case
the spec explicitly asks for.

Every check is fully hermetic: `which` and `is_installed` are injected
so nothing here ever touches the real system's PATH or package
database, and `core.firewall_detect.run_command` is patched so no test
can accidentally shell out for real.
"""
import os
import tempfile
import unittest
from unittest import mock

from core.firewall_detect import (
    detect_firewall, STATE_UFW_ACTIVE, STATE_UFW_INACTIVE,
    STATE_UFW_INSTALLED_NOT_CONFIGURED, STATE_FIREWALLD_ACTIVE,
    STATE_FIREWALLD_INACTIVE, STATE_NFTABLES_RULES, STATE_NONE_DETECTED,
    STATE_UNDETERMINED,
)


def _which(present: set):
    return lambda name: (f"/usr/sbin/{name}" if name in present else None)


def _is_installed(installed_packages: set):
    """installed_packages: e.g. {"ufw"} — matches the 'default' key of
    the packages dict detect_firewall() passes in."""
    def fn(packages: dict) -> bool:
        return packages.get("default") in installed_packages
    return fn


class FirewallDetectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._conf_path = os.path.join(self._tmp.name, "ufw.conf")
        self._missing_conf = os.path.join(self._tmp.name, "does-not-exist.conf")

    def _write_conf(self, enabled: str):
        with open(self._conf_path, "w") as f:
            f.write(f"# comment\nENABLED={enabled}\nLOGLEVEL=low\n")

    def test_ufw_binary_and_conf_enabled_yes_is_active_without_root(self):
        # The core bug: no privileged call is made anywhere in this path.
        self._write_conf("yes")
        with mock.patch("core.firewall_detect.run_command", return_value=(True, "active", "")):
            status = detect_firewall(which=_which({"ufw", "systemctl"}), ufw_conf_path=self._conf_path,
                                      is_installed=_is_installed({"ufw"}))
        self.assertEqual(status.state, STATE_UFW_ACTIVE)

    def test_peppermint_gufw_report_reproduction(self):
        """GUFW installed (pulls in ufw), UFW enabled with 'home'
        profile, Toolbox restarted — ufw.conf says ENABLED=yes even
        though an unprivileged `ufw status` would fail/refuse."""
        self._write_conf("yes")
        with mock.patch("core.firewall_detect.run_command", return_value=(False, "", "You need to be root")):
            status = detect_firewall(which=_which({"ufw"}), ufw_conf_path=self._conf_path,
                                      is_installed=_is_installed({"ufw"}))
        self.assertEqual(status.state, STATE_UFW_ACTIVE)
        self.assertTrue(status.ufw_installed)

    def test_ufw_conf_enabled_no_is_inactive(self):
        self._write_conf("no")
        with mock.patch("core.firewall_detect.run_command", return_value=(False, "", "")):
            status = detect_firewall(which=_which({"ufw"}), ufw_conf_path=self._conf_path,
                                      is_installed=_is_installed({"ufw"}))
        self.assertEqual(status.state, STATE_UFW_INACTIVE)

    def test_ufw_installed_but_conf_missing_is_not_configured(self):
        with mock.patch("core.firewall_detect.run_command", return_value=(False, "", "")):
            status = detect_firewall(which=_which({"ufw"}), ufw_conf_path=self._missing_conf,
                                      is_installed=_is_installed({"ufw"}))
        self.assertEqual(status.state, STATE_UFW_INSTALLED_NOT_CONFIGURED)

    def test_firewalld_active_on_fedora_like(self):
        with mock.patch("core.firewall_detect.run_command", return_value=(True, "active", "")):
            status = detect_firewall(which=_which({"firewall-cmd", "systemctl"}), ufw_conf_path=self._missing_conf,
                                      is_installed=_is_installed({"firewalld"}))
        self.assertEqual(status.state, STATE_FIREWALLD_ACTIVE)

    def test_firewalld_inactive(self):
        with mock.patch("core.firewall_detect.run_command", return_value=(True, "inactive", "")):
            status = detect_firewall(which=_which({"firewall-cmd", "systemctl"}), ufw_conf_path=self._missing_conf,
                                      is_installed=_is_installed({"firewalld"}))
        self.assertEqual(status.state, STATE_FIREWALLD_INACTIVE)

    def test_none_detected_when_nothing_present(self):
        status = detect_firewall(which=_which(set()), ufw_conf_path=self._missing_conf,
                                  is_installed=_is_installed(set()))
        self.assertEqual(status.state, STATE_NONE_DETECTED)

    def test_nftables_rules_detected_when_neither_ufw_nor_firewalld_present(self):
        with mock.patch("core.firewall_detect.run_command", return_value=(True, "table inet filter { }", "")):
            status = detect_firewall(which=_which({"nft"}), ufw_conf_path=self._missing_conf,
                                      is_installed=_is_installed(set()))
        self.assertEqual(status.state, STATE_NFTABLES_RULES)

    def test_nft_present_but_unreadable_is_undetermined_not_none(self):
        with mock.patch("core.firewall_detect.run_command", return_value=(False, "", "Operation not permitted")):
            status = detect_firewall(which=_which({"nft"}), ufw_conf_path=self._missing_conf,
                                      is_installed=_is_installed(set()))
        self.assertEqual(status.state, STATE_UNDETERMINED)

    def test_never_calls_ufw_status_directly(self):
        """Regression guard for the actual bug: this module must never
        shell out to `ufw status` at all."""
        self._write_conf("yes")
        calls = []

        def fake_run(cmd, *a, **kw):
            calls.append(cmd)
            return (True, "active", "")

        with mock.patch("core.firewall_detect.run_command", side_effect=fake_run):
            detect_firewall(which=_which({"ufw"}), ufw_conf_path=self._conf_path,
                             is_installed=_is_installed({"ufw"}))
        for cmd in calls:
            self.assertFalse(cmd[:2] == ["ufw", "status"], f"unexpected privileged-only call: {cmd}")


if __name__ == "__main__":
    unittest.main()
