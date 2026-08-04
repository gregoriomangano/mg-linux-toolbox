"""
Tests for core.software_repo.package_health — "Salute pacchetti".
Scans are read-only/dry-run only; repair/orphan-removal/cache actions
must go through run_pkexec_full (never a plain run_command that could
silently no-op) and must never be triggered by a scan alone.
"""
import unittest
from unittest import mock

from core.software_repo import package_health as ph


def _fake_result(ok=True, stdout="", error=""):
    m = mock.Mock()
    m.ok = ok
    m.stdout = stdout
    m.error = error
    m.technical_detail = lambda: ""
    return m


class ScanDebianTests(unittest.TestCase):
    def test_scan_never_calls_pkexec(self):
        with mock.patch.object(ph, "run_command_full", return_value=_fake_result()) as run_mock, \
             mock.patch.object(ph, "run_pkexec_full") as pk_mock, \
             mock.patch("backend.all.cache_size_human", return_value="120 MB"):
            ph.scan_system_health("debian")
        pk_mock.assert_not_called()
        self.assertTrue(run_mock.called)

    def test_broken_packages_parsed_from_dpkg_dash_c(self):
        def fake_run(cmd, *a, **kw):
            if cmd[:2] == ["dpkg", "-C"]:
                return _fake_result(ok=False, stdout="pkg1 is broken\n")
            return _fake_result(ok=True, stdout="")
        with mock.patch.object(ph, "run_command_full", side_effect=fake_run), \
             mock.patch("backend.all.cache_size_human", return_value="0 B"):
            report = ph.scan_system_health("debian")
        self.assertIn("pkg1 is broken", report.broken_packages)

    def test_orphans_parsed_from_autoremove_dry_run(self):
        autoremove_output = (
            "The following packages will be REMOVED:\n"
            "  foo bar\n"
            "0 upgraded, 0 newly installed, 2 to remove\n"
        )

        def fake_run(cmd, *a, **kw):
            if cmd[:2] == ["apt-get", "-s"]:
                return _fake_result(ok=True, stdout=autoremove_output)
            return _fake_result(ok=True, stdout="")
        with mock.patch.object(ph, "run_command_full", side_effect=fake_run), \
             mock.patch("backend.all.cache_size_human", return_value="0 B"):
            report = ph.scan_system_health("debian")
        self.assertIn("foo", report.orphan_packages)
        self.assertIn("bar", report.orphan_packages)

    def test_dry_run_never_mutates_anything(self):
        """The apt-get call in a scan must always carry -s (simulate)."""
        seen_cmds = []

        def fake_run(cmd, *a, **kw):
            seen_cmds.append(cmd)
            return _fake_result(ok=True, stdout="")
        with mock.patch.object(ph, "run_command_full", side_effect=fake_run), \
             mock.patch("backend.all.cache_size_human", return_value="0 B"):
            ph.scan_system_health("debian")
        for cmd in seen_cmds:
            if cmd[0] == "apt-get":
                self.assertIn("-s", cmd)


class RepairActionsRequirePrivilegeTests(unittest.TestCase):
    def test_repair_dependencies_uses_pkexec(self):
        with mock.patch.object(ph, "run_pkexec_full", return_value=_fake_result()) as pk_mock:
            ph.repair_dependencies("debian")
        pk_mock.assert_called_once()

    def test_remove_orphans_uses_pkexec(self):
        with mock.patch.object(ph, "run_pkexec_full", return_value=_fake_result()) as pk_mock:
            ph.remove_orphans("fedora")
        pk_mock.assert_called_once()

    def test_unsupported_family_refuses_cleanly(self):
        result = ph.repair_dependencies("unknown")
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "health_action_family_unsupported")

    def test_remove_orphans_opensuse_never_calls_pkexec_and_is_honest(self):
        """`zypper packages --orphaned` only lists candidates — it must
        never be run and reported as a successful removal."""
        with mock.patch.object(ph, "run_pkexec_full") as pk_mock:
            result = ph.remove_orphans("opensuse")
        pk_mock.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "health_orphans_opensuse_not_supported")

    def test_clean_cache_dispatches_by_family(self):
        with mock.patch("core.distro.distro") as distro_mock:
            distro_mock.is_arch = True
            distro_mock.is_fedora = False
            distro_mock.is_opensuse = False
            with mock.patch.object(ph, "run_pkexec_full", return_value=_fake_result()) as pk_mock:
                ph.clean_package_cache("arch")
            called_cmd = pk_mock.call_args[0][0]
            self.assertEqual(called_cmd[0], "pacman")


if __name__ == "__main__":
    unittest.main()
