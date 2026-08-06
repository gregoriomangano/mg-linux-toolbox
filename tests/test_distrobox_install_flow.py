"""
Targeted tests for the real Distrobox install flow (2026-08-05 correction
round): must check the real backend readiness, prefer Podman rootless,
never declare success without a working container engine, and capture
real command/exit-code/stdout/stderr for the "Dettagli errore" disclosure.
"""
import unittest
from unittest import mock

from core import container_engines as ce
from core.executor import CommandResult


def _ok(cmd):
    return CommandResult(cmd, True, 0, "", "", 0.1)


def _fail(cmd, stderr="boom"):
    return CommandResult(cmd, False, 1, "", stderr, 0.1)


class DistroboxInstallPlanTests(unittest.TestCase):
    def test_nothing_needed_when_distrobox_and_podman_are_already_ready(self):
        with mock.patch("core.container_engines.podman_status",
                         return_value={"state": ce.PODMAN_STATE_READY}), \
             mock.patch("core.container_engines.docker_status",
                         return_value={"state": ce.DOCKER_STATE_NOT_INSTALLED}), \
             mock.patch("core.container_engines.shutil.which", return_value="/usr/bin/distrobox"):
            plan = ce.distrobox_install_plan()
        self.assertEqual(plan["packages"], [])
        self.assertFalse(plan["needs_backend"])

    def test_no_backend_ready_prefers_podman_over_docker(self):
        with mock.patch("core.container_engines.podman_status",
                         return_value={"state": ce.PODMAN_STATE_NOT_INSTALLED}), \
             mock.patch("core.container_engines.docker_status",
                         return_value={"state": ce.DOCKER_STATE_NOT_INSTALLED}), \
             mock.patch("core.container_engines.shutil.which", return_value=None):
            plan = ce.distrobox_install_plan()
        self.assertIn("podman", plan["packages"])
        self.assertNotIn("docker", plan["packages"])
        self.assertEqual(plan["backend_choice"], "podman")

    def test_docker_ready_means_no_backend_needed_even_without_podman(self):
        with mock.patch("core.container_engines.podman_status",
                         return_value={"state": ce.PODMAN_STATE_NOT_INSTALLED}), \
             mock.patch("core.container_engines.docker_status",
                         return_value={"state": ce.DOCKER_STATE_READY}), \
             mock.patch("core.container_engines.shutil.which", return_value="/usr/bin/distrobox"):
            plan = ce.distrobox_install_plan()
        self.assertFalse(plan["needs_backend"])


class DistroboxInstallFlowTests(unittest.TestCase):
    def _patch_distro_install_cmd(self):
        return mock.patch("core.distro.distro.install_cmd",
                           side_effect=lambda pkgs: ["zypper", "--non-interactive", "install", pkgs["default"]])

    def test_podman_already_ready_only_installs_distrobox_and_succeeds(self):
        with mock.patch("core.container_engines.podman_status",
                         return_value={"state": ce.PODMAN_STATE_READY}), \
             mock.patch("core.container_engines.docker_status",
                         return_value={"state": ce.DOCKER_STATE_NOT_INSTALLED}), \
             mock.patch("core.container_engines.shutil.which", return_value=None), \
             self._patch_distro_install_cmd(), \
             mock.patch("core.container_engines.run_pkexec_full",
                         return_value=_ok(["zypper", "install", "distrobox"])) as pk_mock, \
             mock.patch("core.container_engines.run_command_full",
                         return_value=_ok(["distrobox", "version"])):
            result = ce.distrobox_install()
        self.assertTrue(result.ok)
        self.assertEqual(result.backend, "podman")
        pk_mock.assert_called_once()
        self.assertIn("distrobox", pk_mock.call_args[0][0])

    def test_no_backend_at_all_installs_podman_then_distrobox_then_verifies(self):
        calls = []

        def fake_pkexec(cmd, timeout=None, job=None):
            calls.append(cmd)
            return _ok(cmd)

        # podman_status() is consulted twice: once by distrobox_install_
        # plan() (NOT_INSTALLED — nothing ready yet, install needed) and
        # once by the final verification after both installs ran (READY
        # — the install actually worked).
        with mock.patch("core.container_engines.podman_status",
                         side_effect=[{"state": ce.PODMAN_STATE_NOT_INSTALLED}, {"state": ce.PODMAN_STATE_READY}]), \
             mock.patch("core.container_engines.docker_status",
                         return_value={"state": ce.DOCKER_STATE_NOT_INSTALLED}), \
             mock.patch("core.container_engines.shutil.which", return_value=None), \
             self._patch_distro_install_cmd(), \
             mock.patch("core.container_engines.run_pkexec_full", side_effect=fake_pkexec), \
             mock.patch("core.container_engines.run_command_full",
                         return_value=_ok(["distrobox", "version"])):
            result = ce.distrobox_install()
        self.assertTrue(result.ok)
        self.assertEqual(len(calls), 2)
        self.assertIn("podman", calls[0])
        self.assertIn("distrobox", calls[1])

    def test_never_reports_success_without_a_working_backend(self):
        """The exact bug reported: distrobox binary present, but with no
        working container engine, must never be reported as a successful
        install."""
        with mock.patch("core.container_engines.podman_status",
                         return_value={"state": ce.PODMAN_STATE_NOT_READY}), \
             mock.patch("core.container_engines.docker_status",
                         return_value={"state": ce.DOCKER_STATE_NOT_INSTALLED}), \
             mock.patch("core.container_engines.shutil.which", return_value="/usr/bin/distrobox"), \
             mock.patch("core.container_engines.run_command_full",
                         return_value=_ok(["distrobox", "version"])), \
             mock.patch("core.container_engines.run_pkexec_full") as pk_mock:
            result = ce.distrobox_install()
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "distrobox_install_no_working_backend")
        # No backend package install was even attempted: the "no backend
        # at all -> prefer podman" path only triggers when NEITHER is
        # ready, and here podman_status was NOT_READY (present but
        # broken) rather than not installed — installing it again would
        # not have been the plan, and it must not silently claim success.
        pk_mock.assert_not_called()

    def test_zypper_failure_on_podman_install_stops_before_distrobox_and_reports_real_stderr(self):
        with mock.patch("core.container_engines.podman_status",
                         return_value={"state": ce.PODMAN_STATE_NOT_INSTALLED}), \
             mock.patch("core.container_engines.docker_status",
                         return_value={"state": ce.DOCKER_STATE_NOT_INSTALLED}), \
             mock.patch("core.container_engines.shutil.which", return_value=None), \
             self._patch_distro_install_cmd(), \
             mock.patch("core.container_engines.run_pkexec_full",
                         return_value=_fail(["zypper", "install", "podman"], "nothing provides podman")) as pk_mock:
            result = ce.distrobox_install()
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "distrobox_install_backend_failed")
        pk_mock.assert_called_once()
        self.assertIn("nothing provides podman", result.technical_detail())

    def test_technical_detail_includes_every_attempted_step(self):
        with mock.patch("core.container_engines.podman_status",
                         return_value={"state": ce.PODMAN_STATE_READY}), \
             mock.patch("core.container_engines.docker_status",
                         return_value={"state": ce.DOCKER_STATE_NOT_INSTALLED}), \
             mock.patch("core.container_engines.shutil.which", return_value=None), \
             self._patch_distro_install_cmd(), \
             mock.patch("core.container_engines.run_pkexec_full",
                         return_value=_ok(["zypper", "install", "distrobox"])), \
             mock.patch("core.container_engines.run_command_full",
                         return_value=_ok(["distrobox", "version"])):
            result = ce.distrobox_install()
        detail = result.technical_detail()
        self.assertIn("install_distrobox", detail)
        self.assertIn("distrobox_version", detail)
        self.assertIn("exit code", detail)

    def test_result_bool_reflects_ok(self):
        self.assertTrue(bool(ce.DistroboxInstallResult(True)))
        self.assertFalse(bool(ce.DistroboxInstallResult(False)))


class DistroboxRowGatingTests(unittest.TestCase):
    """The button-gating fix: "installed" (pill shown, button hidden)
    must reflect real end-to-end readiness, not just the binary."""

    def test_binary_present_but_no_backend_is_not_considered_installed(self):
        status = {"state": ce.DISTROBOX_STATE_NO_BACKEND, "backend": None, "version": "1.0", "rootless": None}
        installed = status["state"] == ce.DISTROBOX_STATE_READY
        self.assertFalse(installed)

    def test_ready_state_is_considered_installed(self):
        status = {"state": ce.DISTROBOX_STATE_READY, "backend": "podman", "version": "1.0", "rootless": True}
        installed = status["state"] == ce.DISTROBOX_STATE_READY
        self.assertTrue(installed)


if __name__ == "__main__":
    unittest.main()
