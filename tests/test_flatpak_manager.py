"""
Tests for core.software_repo.flatpak_manager — Flatpak/Flathub
detection and guided setup. Detection is fully mocked (no real
`flatpak` invocation); the point is verifying the parsing, the
system/user remote distinction, and that system-scope Flathub is never
mistaken for "not configured" just because the user scope is empty.
"""
import unittest
from unittest import mock

from core.software_repo import flatpak_manager as fp
from core.software_repo.distro_profile import DistroProfile, FAMILY_DEBIAN, SYSTEM_TRADITIONAL, \
    SYSTEM_IMMUTABLE, FAMILY_UNKNOWN


class ParseRemotesTests(unittest.TestCase):
    def test_parses_name_url_options(self):
        output = "flathub\thttps://dl.flathub.org/repo/\tsystem\nfedora\thttps://example.com/\tsystem\n"
        remotes = fp._parse_remotes(output, "system")
        self.assertEqual(len(remotes), 2)
        self.assertEqual(remotes[0].name, "flathub")
        self.assertTrue(remotes[0].enabled)

    def test_disabled_option_is_respected(self):
        output = "flathub\thttps://dl.flathub.org/repo/\tdisabled\n"
        remotes = fp._parse_remotes(output, "user")
        self.assertFalse(remotes[0].enabled)

    def test_empty_output_is_empty_list(self):
        self.assertEqual(fp._parse_remotes("", "system"), [])

    def test_is_flathub_by_name_or_host(self):
        r1 = fp.FlatpakRemote("flathub", "https://dl.flathub.org/repo/", True, "system")
        r2 = fp.FlatpakRemote("myflathubmirror", "https://dl.flathub.org/repo/", True, "system")
        r3 = fp.FlatpakRemote("gnome-nightly", "https://nightly.gnome.org/repo/", True, "system")
        self.assertTrue(r1.is_flathub)
        self.assertTrue(r2.is_flathub)
        self.assertFalse(r3.is_flathub)


class DetectFlatpakStateTests(unittest.TestCase):
    def test_not_installed_returns_early(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=False):
            state = fp.detect_flatpak_state()
        self.assertFalse(state.installed)
        self.assertFalse(state.flathub_system)

    def test_system_flathub_configured_without_user_remote_is_not_an_error(self):
        """Absence of a *user* remote must never be read as 'Flathub
        not configured' when the system-wide remote already covers it."""
        system_remotes = [fp.FlatpakRemote("flathub", "https://dl.flathub.org/repo/", True, "system")]
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "flatpak_version", return_value="1.15.6"), \
             mock.patch.object(fp, "list_remotes", side_effect=lambda scope: system_remotes if scope == "system" else []), \
             mock.patch.object(fp, "_detect_portal", return_value=(True, "gnome")):
            state = fp.detect_flatpak_state()
        self.assertTrue(state.flathub_system)
        self.assertFalse(state.flathub_user)
        self.assertTrue(state.integration_complete)

    def test_other_remotes_lists_non_flathub_entries_from_both_scopes(self):
        system_remotes = [fp.FlatpakRemote("flathub", "https://dl.flathub.org/repo/", True, "system")]
        user_remotes = [fp.FlatpakRemote("gnome-nightly", "https://nightly.gnome.org/repo/", True, "user")]
        with mock.patch.object(fp, "flatpak_installed", return_value=True), \
             mock.patch.object(fp, "flatpak_version", return_value="1.15.6"), \
             mock.patch.object(fp, "list_remotes", side_effect=lambda scope: system_remotes if scope == "system" else user_remotes), \
             mock.patch.object(fp, "_detect_portal", return_value=(False, "")):
            state = fp.detect_flatpak_state()
        self.assertEqual(len(state.other_remotes), 1)
        self.assertEqual(state.other_remotes[0].name, "gnome-nightly")
        self.assertFalse(state.integration_complete)  # no portal


class InstallFlatpakTests(unittest.TestCase):
    def _profile(self, family=FAMILY_DEBIAN, system_type=SYSTEM_TRADITIONAL):
        return DistroProfile(family=family, system_type=system_type)

    def test_already_installed_is_a_noop_success(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=True):
            result = fp.install_flatpak(self._profile())
        self.assertTrue(result.ok)
        self.assertEqual(result.friendly_message, "flatpak_already_installed")

    def test_immutable_system_never_runs_a_package_manager(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=False), \
             mock.patch.object(fp, "run_pkexec_full") as run_mock:
            result = fp.install_flatpak(self._profile(system_type=SYSTEM_IMMUTABLE))
        self.assertFalse(result.ok)
        run_mock.assert_not_called()

    def test_unresolved_family_refuses_to_guess_a_command(self):
        with mock.patch.object(fp, "flatpak_installed", return_value=False), \
             mock.patch.object(fp, "run_pkexec_full") as run_mock:
            result = fp.install_flatpak(self._profile(family=FAMILY_UNKNOWN))
        self.assertFalse(result.ok)
        run_mock.assert_not_called()

    def test_traditional_debian_runs_install_cmd(self):
        with mock.patch.object(fp, "flatpak_installed", side_effect=[False, True]), \
             mock.patch("core.distro.distro.install_cmd", return_value=["apt-get", "install", "-y", "flatpak"]), \
             mock.patch.object(fp, "run_pkexec_full") as run_mock:
            run_mock.return_value = mock.Mock(ok=True, technical_detail=lambda: "")
            result = fp.install_flatpak(self._profile())
        run_mock.assert_called_once()
        self.assertTrue(result.ok)


class AddFlathubRemoteTests(unittest.TestCase):
    def test_system_scope_uses_pkexec(self):
        with mock.patch.object(fp, "run_pkexec_full") as pk_mock, \
             mock.patch.object(fp, "run_command_full") as run_mock, \
             mock.patch.object(fp, "list_remotes", return_value=[
                 fp.FlatpakRemote("flathub", fp.FLATHUB_REPO_URL, True, "system")]):
            pk_mock.return_value = mock.Mock(ok=True, technical_detail=lambda: "")
            result = fp.add_flathub_remote(fp.SCOPE_SYSTEM)
        pk_mock.assert_called_once()
        run_mock.assert_not_called()
        self.assertTrue(result.ok)
        self.assertTrue(result.logout_recommended)

    def test_user_scope_never_needs_pkexec(self):
        with mock.patch.object(fp, "run_pkexec_full") as pk_mock, \
             mock.patch.object(fp, "run_command_full") as run_mock, \
             mock.patch.object(fp, "list_remotes", return_value=[
                 fp.FlatpakRemote("flathub", fp.FLATHUB_REPO_URL, True, "user")]):
            run_mock.return_value = mock.Mock(ok=True, technical_detail=lambda: "")
            fp.add_flathub_remote(fp.SCOPE_USER)
        pk_mock.assert_not_called()
        run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
