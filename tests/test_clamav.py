"""
Tests for core.clamav: distro-family package mapping, install-command
safety (never touches repository configuration, never a shell string),
signature-database detection, and scan-argument safety (path always
its own argv element, never concatenated into a shell command).
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.distro import distro
import core.clamav as clamav
from core.executor import CommandResult


def _fake_distro(identifier, id_like=""):
    return mock.patch.multiple(distro, id=identifier, id_like=id_like)


class PackageMappingTests(unittest.TestCase):
    def test_debian_family_packages(self):
        with _fake_distro("debian"):
            self.assertEqual(clamav.packages_for_this_distro(), ["clamav", "clamav-daemon"])

    def test_ubuntu_uses_debian_mapping(self):
        with _fake_distro("ubuntu", "debian"):
            self.assertEqual(clamav.packages_for_this_distro(), ["clamav", "clamav-daemon"])

    def test_fedora_packages(self):
        with _fake_distro("fedora"):
            self.assertEqual(clamav.packages_for_this_distro(), ["clamav", "clamd", "clamav-update"])

    def test_opensuse_packages(self):
        with _fake_distro("opensuse-tumbleweed", "opensuse suse"):
            self.assertEqual(clamav.packages_for_this_distro(), ["clamav"])

    def test_arch_packages(self):
        with _fake_distro("arch"):
            self.assertEqual(clamav.packages_for_this_distro(), ["clamav"])


class InstalledDetectionTests(unittest.TestCase):
    def test_package_present(self):
        with _fake_distro("debian"), mock.patch.object(distro, "is_installed", return_value=True) as m:
            self.assertTrue(clamav.is_installed())
            m.assert_called_once_with({"debian": "clamav"})

    def test_package_absent(self):
        with _fake_distro("debian"), mock.patch.object(distro, "is_installed", return_value=False):
            self.assertFalse(clamav.is_installed())

    def test_already_installed_state_is_never_not_installed(self):
        with _fake_distro("debian"), \
             mock.patch.object(distro, "is_installed", return_value=True), \
             mock.patch.object(clamav, "signatures_status", return_value="ready"):
            self.assertEqual(clamav.state(), clamav.STATE_READY)
            self.assertNotEqual(clamav.state(), clamav.STATE_NOT_INSTALLED)

    def test_installed_packages_only_lists_what_is_really_confirmed(self):
        """Fedora: clamav + clamd present, clamav-update NOT — must
        report exactly the two, never guess the third is there too."""
        def fake_is_installed(pkg_map):
            pkg = pkg_map.get("fedora")
            return pkg in ("clamav", "clamd")
        with _fake_distro("fedora"), mock.patch.object(distro, "is_installed", side_effect=fake_is_installed):
            self.assertEqual(clamav.installed_packages(), ["clamav", "clamd"])

    def test_installed_packages_empty_when_nothing_installed(self):
        with _fake_distro("debian"), mock.patch.object(distro, "is_installed", return_value=False):
            self.assertEqual(clamav.installed_packages(), [])


class RepoAvailabilityTests(unittest.TestCase):
    def test_available_only_when_every_required_package_is_available(self):
        with _fake_distro("fedora"), \
             mock.patch("core.repo_check.is_available", side_effect=[True, True, False]):
            self.assertFalse(clamav.is_available_in_repos())

    def test_available_when_all_packages_available(self):
        with _fake_distro("fedora"), \
             mock.patch("core.repo_check.is_available", return_value=True):
            self.assertTrue(clamav.is_available_in_repos())


class NoExternalRepoTests(unittest.TestCase):
    """install() must only ever invoke the distro's own already-configured
    package manager on the exact package names — never add/enable a
    repository, never AUR/OBS/Packman/RPM Fusion/PPA."""

    _FORBIDDEN_SUBSTRINGS = (
        "addrepo", "add-apt-repository", "aur", "yay", "paru", "rpmfusion",
        "packman", "ppa:", "http://", "https://", "obs.opensuse", "software-properties",
    )

    def _assert_cmd_is_clean(self, cmd):
        joined = " ".join(cmd).lower()
        for bad in self._FORBIDDEN_SUBSTRINGS:
            self.assertNotIn(bad, joined, f"install command touched something external: {cmd}")

    def test_debian_install_command(self):
        with _fake_distro("debian"), mock.patch("core.clamav.run_pkexec_full") as m:
            m.return_value = CommandResult(["apt-get"], True, 0, "", "", 0.1)
            clamav.install()
            cmd = m.call_args.args[0]
            self.assertEqual(cmd, ["apt-get", "install", "-y", "clamav", "clamav-daemon"])
            self._assert_cmd_is_clean(cmd)

    def test_fedora_install_command(self):
        with _fake_distro("fedora"), mock.patch("core.clamav.run_pkexec_full") as m:
            m.return_value = CommandResult(["dnf"], True, 0, "", "", 0.1)
            clamav.install()
            cmd = m.call_args.args[0]
            self.assertEqual(cmd, ["dnf", "install", "-y", "clamav", "clamd", "clamav-update"])
            self._assert_cmd_is_clean(cmd)

    def test_opensuse_install_command(self):
        with _fake_distro("opensuse-tumbleweed", "opensuse suse"), mock.patch("core.clamav.run_pkexec_full") as m:
            m.return_value = CommandResult(["zypper"], True, 0, "", "", 0.1)
            clamav.install()
            cmd = m.call_args.args[0]
            self.assertEqual(cmd, ["zypper", "--non-interactive", "install", "clamav"])
            self._assert_cmd_is_clean(cmd)

    def test_arch_install_command(self):
        with _fake_distro("arch"), mock.patch("core.clamav.run_pkexec_full") as m:
            m.return_value = CommandResult(["pacman"], True, 0, "", "", 0.1)
            clamav.install()
            cmd = m.call_args.args[0]
            self.assertEqual(cmd, ["pacman", "-S", "--noconfirm", "clamav"])
            self._assert_cmd_is_clean(cmd)

    def test_installation_failure_is_reported_not_silently_swallowed(self):
        with _fake_distro("debian"), mock.patch("core.clamav.run_pkexec_full") as m:
            m.return_value = CommandResult(["apt-get"], False, 1, "", "permission denied", 0.1, error="")
            result = clamav.install()
            self.assertFalse(result.ok)

    def test_update_definitions_never_shells_out_with_a_path(self):
        with mock.patch("core.clamav.run_pkexec_full") as m:
            m.return_value = CommandResult(["freshclam"], True, 0, "", "", 0.1)
            clamav.update_definitions()
            m.assert_called_once()
            self.assertEqual(m.call_args.args[0], ["freshclam"])

    def test_debian_uninstall_command(self):
        with _fake_distro("debian"), \
             mock.patch.object(clamav, "installed_packages", return_value=["clamav", "clamav-daemon"]), \
             mock.patch.object(clamav, "clamd_service_name", return_value=None), \
             mock.patch.object(clamav, "freshclam_service_name", return_value=None), \
             mock.patch("core.clamav.run_pkexec_full") as m:
            m.return_value = CommandResult(["apt-get"], True, 0, "", "", 0.1)
            clamav.uninstall()
            cmd = m.call_args.args[0]
            self.assertEqual(cmd, ["apt-get", "remove", "-y", "clamav", "clamav-daemon"])
            self._assert_cmd_is_clean(cmd)
            joined = " ".join(cmd).lower()
            self.assertNotIn("purge", joined)
            self.assertNotIn("autoremove", joined)

    def test_fedora_uninstall_only_removes_packages_really_installed(self):
        """clamav-update can be a provides/alias of freshclam on Fedora
        — installed_packages() must only report what rpm -q actually
        confirms, never the full family list blindly."""
        with _fake_distro("fedora"), \
             mock.patch.object(clamav, "installed_packages", return_value=["clamav", "clamd"]), \
             mock.patch.object(clamav, "clamd_service_name", return_value=None), \
             mock.patch.object(clamav, "freshclam_service_name", return_value=None), \
             mock.patch("core.clamav.run_pkexec_full") as m:
            m.return_value = CommandResult(["dnf"], True, 0, "", "", 0.1)
            clamav.uninstall()
            cmd = m.call_args.args[0]
            self.assertEqual(cmd, ["dnf", "remove", "-y", "clamav", "clamd"])
            self.assertNotIn("clamav-update", cmd)

    def test_opensuse_uninstall_command(self):
        with _fake_distro("opensuse-tumbleweed", "opensuse suse"), \
             mock.patch.object(clamav, "installed_packages", return_value=["clamav"]), \
             mock.patch.object(clamav, "clamd_service_name", return_value=None), \
             mock.patch.object(clamav, "freshclam_service_name", return_value=None), \
             mock.patch("core.clamav.run_pkexec_full") as m:
            m.return_value = CommandResult(["zypper"], True, 0, "", "", 0.1)
            clamav.uninstall()
            cmd = m.call_args.args[0]
            self.assertEqual(cmd, ["zypper", "--non-interactive", "remove", "clamav"])

    def test_arch_uninstall_command_never_uses_rns(self):
        with _fake_distro("arch"), \
             mock.patch.object(clamav, "installed_packages", return_value=["clamav"]), \
             mock.patch.object(clamav, "clamd_service_name", return_value=None), \
             mock.patch.object(clamav, "freshclam_service_name", return_value=None), \
             mock.patch("core.clamav.run_pkexec_full") as m:
            m.return_value = CommandResult(["pacman"], True, 0, "", "", 0.1)
            clamav.uninstall()
            cmd = m.call_args.args[0]
            self.assertEqual(cmd, ["pacman", "-R", "--noconfirm", "clamav"])
            self.assertNotIn("-Rns", cmd)
            self.assertNotIn("-Rsn", cmd)

    def test_uninstall_does_nothing_when_nothing_is_installed(self):
        with mock.patch.object(clamav, "installed_packages", return_value=[]), \
             mock.patch("core.clamav.run_pkexec_full") as m:
            result = clamav.uninstall()
            self.assertFalse(result.ok)
            m.assert_not_called()

    def test_uninstall_stops_detected_services_before_removing(self):
        calls = []
        with _fake_distro("debian"), \
             mock.patch.object(clamav, "installed_packages", return_value=["clamav", "clamav-daemon"]), \
             mock.patch.object(clamav, "clamd_service_name", return_value="clamav-daemon"), \
             mock.patch.object(clamav, "freshclam_service_name", return_value="clamav-freshclam"), \
             mock.patch("core.clamav.run_pkexec_full", side_effect=lambda cmd, **kw: calls.append(cmd) or CommandResult(cmd, True, 0, "", "", 0.1)):
            clamav.uninstall()
        self.assertIn(["systemctl", "stop", "clamav-daemon"], calls)
        self.assertIn(["systemctl", "stop", "clamav-freshclam"], calls)
        self.assertEqual(calls[-1], ["apt-get", "remove", "-y", "clamav", "clamav-daemon"])

    def test_uninstall_stop_failure_does_not_block_removal(self):
        with _fake_distro("debian"), \
             mock.patch.object(clamav, "installed_packages", return_value=["clamav"]), \
             mock.patch.object(clamav, "clamd_service_name", return_value="clamav-daemon"), \
             mock.patch.object(clamav, "freshclam_service_name", return_value=None):
            def fake_pkexec(cmd, **kw):
                if cmd[:2] == ["systemctl", "stop"]:
                    return CommandResult(cmd, False, 1, "", "failed to stop", 0.1)
                return CommandResult(cmd, True, 0, "", "", 0.1)
            with mock.patch("core.clamav.run_pkexec_full", side_effect=fake_pkexec):
                result = clamav.uninstall()
        self.assertTrue(result.ok)


class ServiceLifecycleTests(unittest.TestCase):
    def test_clamd_manageable_false_when_no_unit_detected(self):
        with mock.patch.object(clamav, "clamd_service_name", return_value=None):
            self.assertFalse(clamav.clamd_manageable())

    def test_clamd_start_targets_the_detected_unit(self):
        with mock.patch.object(clamav, "clamd_service_name", return_value="clamd@scan"), \
             mock.patch("core.clamav.run_pkexec_full") as m:
            m.return_value = CommandResult(["systemctl"], True, 0, "", "", 0.1)
            clamav.clamd_start()
            self.assertEqual(m.call_args.args[0], ["systemctl", "start", "clamd@scan"])

    def test_clamd_stop_targets_the_detected_unit(self):
        with mock.patch.object(clamav, "clamd_service_name", return_value="clamav-daemon"), \
             mock.patch("core.clamav.run_pkexec_full") as m:
            m.return_value = CommandResult(["systemctl"], True, 0, "", "", 0.1)
            clamav.clamd_stop()
            self.assertEqual(m.call_args.args[0], ["systemctl", "stop", "clamav-daemon"])

    def test_clamd_start_fails_cleanly_with_no_unit_never_invents_one(self):
        with mock.patch.object(clamav, "clamd_service_name", return_value=None), \
             mock.patch("core.clamav.run_pkexec_full") as m:
            result = clamav.clamd_start()
            self.assertFalse(result.ok)
            m.assert_not_called()

    def test_clamd_stop_fails_cleanly_with_no_unit_never_invents_one(self):
        with mock.patch.object(clamav, "clamd_service_name", return_value=None), \
             mock.patch("core.clamav.run_pkexec_full") as m:
            result = clamav.clamd_stop()
            self.assertFalse(result.ok)
            m.assert_not_called()


class SignatureDatabaseTests(unittest.TestCase):
    def test_missing_db_dir_is_unknown(self):
        with mock.patch.object(clamav, "_SIGNATURE_DB_DIRS", ["/no/such/dir/xyz"]):
            self.assertEqual(clamav.signatures_status(), "unknown")

    def test_no_signature_files_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(clamav, "_SIGNATURE_DB_DIRS", [d]):
                self.assertEqual(clamav.signatures_status(), "missing")

    def test_fresh_signatures_are_ready(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "main.cvd"), "w").close()
            open(os.path.join(d, "daily.cvd"), "w").close()
            with mock.patch.object(clamav, "_SIGNATURE_DB_DIRS", [d]):
                self.assertEqual(clamav.signatures_status(), "ready")

    def test_stale_daily_signature_is_outdated(self):
        with tempfile.TemporaryDirectory() as d:
            main_path = os.path.join(d, "main.cvd")
            daily_path = os.path.join(d, "daily.cvd")
            open(main_path, "w").close()
            open(daily_path, "w").close()
            old = time.time() - clamav._STALE_SECONDS - 3600
            os.utime(daily_path, (old, old))
            with mock.patch.object(clamav, "_SIGNATURE_DB_DIRS", [d]):
                self.assertEqual(clamav.signatures_status(), "outdated")

    def test_state_installed_but_no_db_is_not_ready(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(clamav, "is_installed", return_value=True), \
             mock.patch.object(clamav, "_SIGNATURE_DB_DIRS", [d]):
            self.assertEqual(clamav.state(), clamav.STATE_INSTALLED)


class ServiceDetectionTests(unittest.TestCase):
    def test_no_assumed_service_name_when_none_exist(self):
        with mock.patch.object(clamav, "_service_exists", return_value=False):
            self.assertIsNone(clamav.clamd_service_name())
            self.assertIsNone(clamav.clamd_active())

    def test_uses_first_real_candidate_found(self):
        def fake_exists(name):
            return name == "clamd@scan"
        with mock.patch.object(clamav, "_service_exists", side_effect=fake_exists), \
             mock.patch.object(clamav, "_service_active", return_value=True):
            self.assertEqual(clamav.clamd_service_name(), "clamd@scan")
            self.assertTrue(clamav.clamd_active())


class ScanArgumentSafetyTests(unittest.TestCase):
    def test_nonexistent_path_never_invokes_the_scanner(self):
        with mock.patch("core.clamav.run_command_full") as m:
            result = clamav.scan_path("/definitely/not/a/real/path/abc123")
            self.assertFalse(result.ok)
            self.assertEqual(result.error, "path_not_found")
            m.assert_not_called()

    def test_empty_path_never_invokes_the_scanner(self):
        with mock.patch("core.clamav.run_command_full") as m:
            result = clamav.scan_path("")
            self.assertFalse(result.ok)
            m.assert_not_called()

    def test_path_is_its_own_argv_element_never_shell_joined(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "some file.txt")
            with open(target, "w") as f:
                f.write("x")
            with mock.patch("core.clamav.run_command_full") as m:
                m.return_value = CommandResult(["clamscan"], True, 0, "", "", 0.1)
                clamav.scan_path(target)
                cmd = m.call_args.args[0]
                self.assertIn(target, cmd)
                self.assertEqual(cmd[-1], target)
                for arg in cmd:
                    self.assertNotIn(";", arg)
                    self.assertNotIn("&&", arg)

    def test_path_with_spaces_is_preserved_as_one_argument(self):
        with tempfile.TemporaryDirectory() as base:
            spaced_dir = os.path.join(base, "my folder with spaces")
            os.makedirs(spaced_dir)
            with mock.patch("core.clamav.run_command_full") as m:
                m.return_value = CommandResult(["clamscan"], True, 0, "", "", 0.1)
                clamav.scan_path(spaced_dir)
                cmd = m.call_args.args[0]
                self.assertIn(spaced_dir, cmd)
                self.assertEqual(cmd.count(spaced_dir), 1)

    def test_directory_scan_uses_recursive_flag(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("core.clamav.run_command_full") as m:
            m.return_value = CommandResult(["clamscan"], True, 0, "", "", 0.1)
            clamav.scan_path(d)
            cmd = m.call_args.args[0]
            self.assertIn("-r", cmd)

    def test_clean_scan_reports_zero_infected(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("core.clamav.run_command_full") as m:
            m.return_value = CommandResult(["clamscan"], True, 0, "no infections", "", 0.1)
            result = clamav.scan_path(d)
            self.assertTrue(result.ok)
            self.assertEqual(result.infected_count, 0)

    def test_infected_scan_is_reported_with_count(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("core.clamav.run_command_full") as m:
            stdout = (
                f"{d}/evil.exe: Win.Test.EICAR_HDB-1 FOUND\n"
                f"{d}/sub/bad.js: Some.Other.Malware FOUND\n"
            )
            m.return_value = CommandResult(["clamscan"], False, 1, stdout, "", 0.1)
            result = clamav.scan_path(d)
            self.assertTrue(result.ok)  # the SCAN itself completed successfully
            self.assertEqual(result.infected_count, 2)
            self.assertEqual(len(result.infected_files), 2)

    def test_scanner_error_is_reported_not_confused_with_a_clean_result(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("core.clamav.run_command_full") as m:
            m.return_value = CommandResult(["clamscan"], False, 2, "", "database load error", 0.1)
            result = clamav.scan_path(d)
            self.assertFalse(result.ok)
            self.assertEqual(result.error, "scanner_error")

    def test_scanner_not_installed_is_reported_as_scanner_error(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("core.clamav.run_command_full") as m:
            m.return_value = CommandResult(["clamscan"], False, None, "", "", 0.0, error="No such file or directory")
            result = clamav.scan_path(d)
            self.assertFalse(result.ok)
            self.assertEqual(result.error, "scanner_error")


if __name__ == "__main__":
    unittest.main()
