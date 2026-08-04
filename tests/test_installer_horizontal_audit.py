"""
Horizontal audit tests (2026-08-05 block, Fase 4 + Fase 2): the same
"already installed" class of bug found in Flatseal, checked across one
representative installer per named page (Audio, Gaming, Stampanti,
Virtualizzazione), plus regression coverage for the two confirmed
openSUSE gaps (easyeffects_install/docker_install used to run NO
command at all on that family and still report failure) and the three
hardcoded-Italian DepBanner strings found during the same audit.
"""
import unittest
from unittest import mock

import backend.all as B


class AlreadyInstalledIsNeverAnErrorTests(unittest.TestCase):
    """run_install_in_background (ui/widgets.py) always re-verifies via
    a real check after the install command — these confirm each
    representative backend function's *_installed() reflects reality
    on its own, independent of whatever the install command did."""

    def test_easyeffects_already_installed_short_circuits_cleanly(self):
        with mock.patch("backend.all._cmd_exists", return_value=True):
            self.assertTrue(B.easyeffects_installed())

    def test_gamemode_already_installed_short_circuits_cleanly(self):
        with mock.patch("core.distro.distro.is_installed", return_value=True):
            self.assertTrue(B.gamemode_installed())

    def test_docker_already_installed_short_circuits_cleanly(self):
        with mock.patch("backend.all._cmd_exists", return_value=True):
            self.assertTrue(B.docker_installed())


class OpenSuseGapRegressionTests(unittest.TestCase):
    """Real bug found during the audit: on openSUSE, these two used to
    match NONE of their if/elif branches and run no command at all,
    yet still report "installation failed" afterwards."""

    def test_easyeffects_install_runs_zypper_on_opensuse(self):
        # Real package installs go through _install_pkg -> run_pkexec_full
        # (INSTALL_TIMEOUT=180s), not the plain run_pkexec (10s default) —
        # see the 2026-08-04 fix for the P1 SIGKILL-mid-install bug.
        with mock.patch("backend.all.distro") as distro_mock, \
             mock.patch("backend.all.run_pkexec_full") as pk_mock, \
             mock.patch("backend.all.easyeffects_installed", return_value=True):
            distro_mock.is_arch = False
            distro_mock.is_fedora = False
            distro_mock.is_opensuse = True
            distro_mock.install_cmd.side_effect = lambda pkgs: (
                ["zypper", "--non-interactive", "install", pkgs["opensuse"]]
            )
            B.easyeffects_install()
        called_cmd = pk_mock.call_args[0][0]
        self.assertEqual(called_cmd[0], "zypper")
        self.assertIn("easyeffects", called_cmd)

    def test_docker_install_runs_zypper_on_opensuse(self):
        # docker_install() makes two calls: the package install (now
        # via run_pkexec_full/_install_pkg) and a separate
        # `systemctl enable --now docker` (still plain run_pkexec) —
        # mock both and check the install command on the right one.
        with mock.patch("backend.all.distro") as distro_mock, \
             mock.patch("backend.all.run_pkexec_full") as pk_full_mock, \
             mock.patch("backend.all.run_pkexec") as pk_mock, \
             mock.patch("backend.all.docker_installed", return_value=True):
            distro_mock.is_arch = False
            distro_mock.is_fedora = False
            distro_mock.is_opensuse = True
            distro_mock.install_cmd.side_effect = lambda pkgs: (
                ["zypper", "--non-interactive", "install", pkgs["opensuse"]]
            )
            B.docker_install()
        install_cmd = pk_full_mock.call_args[0][0]
        self.assertEqual(install_cmd[0], "zypper")
        self.assertIn("docker", install_cmd)
        pk_mock.assert_called_once()  # the systemctl enable --now call

    def test_easyeffects_install_never_silently_does_nothing(self):
        """Regression guard for the actual defect: install_cmd() must
        be consulted for EVERY family, never skipped for one."""
        with mock.patch("backend.all.distro") as distro_mock, \
             mock.patch("backend.all.run_pkexec_full") as pk_mock, \
             mock.patch("backend.all.easyeffects_installed", return_value=False):
            distro_mock.is_arch = False
            distro_mock.is_fedora = False
            distro_mock.is_opensuse = True
            distro_mock.install_cmd.return_value = ["zypper", "--non-interactive", "install", "easyeffects"]
            B.easyeffects_install()
        pk_mock.assert_called_once()


class DepBannerTranslationTests(unittest.TestCase):
    """Regression guard for the three DepBanner strings found hardcoded
    in Italian only — used by every install banner across the app
    (Network, System, Security, Virt, Gaming, Audio, ...)."""

    def test_requires_text_translates(self):
        from core.i18n import T, set_lang
        set_lang("fr")
        text = T("dep_banner_requires").format(pkg="docker")
        self.assertIn("Nécessite", text)
        self.assertNotIn("Richiede", text)
        set_lang("it")

    def test_install_now_button_fully_translates(self):
        from core.i18n import T, set_lang
        set_lang("en")
        self.assertEqual(T("dep_banner_install_now_btn"), "Install now")
        self.assertNotIn("ora", T("dep_banner_install_now_btn"))
        set_lang("it")

    def test_installed_restart_note_translates(self):
        from core.i18n import T, set_lang
        set_lang("es")
        text = T("dep_banner_installed_restart_note")
        self.assertIn("reinicia", text)
        self.assertNotIn("riavvia", text)
        set_lang("it")


if __name__ == "__main__":
    unittest.main()
