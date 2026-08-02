"""
Tests for core.virt_setup — real KVM/libvirt setup actions ("Configura
KVM", "Disattiva servizi", "Ripristina configurazione Toolbox",
Virt-Manager install/launch). Every privileged call (run_pkexec) and
every real-system probe is mocked — this is orchestration-logic
coverage, not a real install on the machine running the suite.
"""
import contextlib
import os
import tempfile
import unittest
from unittest import mock

from core import virt_setup as vs


def _distro_flags(stack, **flags):
    for name in ("is_arch", "is_fedora", "is_opensuse", "is_debian"):
        stack.enter_context(mock.patch.object(
            type(vs.distro), name,
            new_callable=mock.PropertyMock, return_value=flags.get(name, False)))


class VirtSetupTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        patcher = mock.patch.object(vs, "state_path",
                                     return_value=os.path.join(self._tmpdir.name, "virt_setup.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        # _log() writes to the real ~/.local/share history.db unless
        # mocked — keep tests from touching real user data.
        log_patcher = mock.patch.object(vs.hs, "record_operation")
        log_patcher.start()
        self.addCleanup(log_patcher.stop)


class ConfigureKvmTests(VirtSetupTestCase):
    def test_refuses_when_cpu_unsupported(self):
        with mock.patch("core.virt_readiness.check_kvm", return_value={"cpu_supported": False}):
            result = vs.configure_kvm()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "cpu_unsupported")

    def test_installs_packages_when_missing_and_records_state(self):
        with mock.patch("core.virt_readiness.check_kvm",
                         side_effect=[{"cpu_supported": True}, {"state": "ready"}]), \
             mock.patch.object(vs, "qemu_installed", side_effect=[False, True]), \
             mock.patch.object(vs, "libvirt_installed", side_effect=[False, True]), \
             mock.patch.object(vs, "_service_active", return_value=False), \
             mock.patch.object(vs, "_service_enabled", return_value=False), \
             mock.patch.object(vs, "run_pkexec") as mock_pkexec, \
             mock.patch("backend.all.kvm_load"):
            result = vs.configure_kvm()
        self.assertTrue(result["ok"])
        # one call to install packages, one to enable+start libvirtd
        self.assertEqual(mock_pkexec.call_count, 2)
        install_cmd = mock_pkexec.call_args_list[0][0][0]
        self.assertIn("install", " ".join(install_cmd))

    def test_skips_install_when_already_present(self):
        with mock.patch("core.virt_readiness.check_kvm",
                         side_effect=[{"cpu_supported": True}, {"state": "ready"}]), \
             mock.patch.object(vs, "qemu_installed", return_value=True), \
             mock.patch.object(vs, "libvirt_installed", return_value=True), \
             mock.patch.object(vs, "_service_active", return_value=False), \
             mock.patch.object(vs, "_service_enabled", return_value=False), \
             mock.patch.object(vs, "run_pkexec") as mock_pkexec, \
             mock.patch("backend.all.kvm_load"):
            vs.configure_kvm()
        # only the enable+start libvirtd call, no install call
        self.assertEqual(mock_pkexec.call_count, 1)

    def test_records_a_history_entry(self):
        with mock.patch("core.virt_readiness.check_kvm",
                         side_effect=[{"cpu_supported": True}, {"state": "ready"}]), \
             mock.patch.object(vs, "qemu_installed", return_value=True), \
             mock.patch.object(vs, "libvirt_installed", return_value=True), \
             mock.patch.object(vs, "_service_active", return_value=False), \
             mock.patch.object(vs, "_service_enabled", return_value=False), \
             mock.patch.object(vs, "run_pkexec"), \
             mock.patch("backend.all.kvm_load"):
            vs.configure_kvm()
        vs.hs.record_operation.assert_called_once()
        args = vs.hs.record_operation.call_args[0]
        self.assertEqual(args[:3], ("virt", "virt.kvm", vs.hs.CONFIGURATION))
        self.assertTrue(args[3])

    def test_never_mixes_packages_across_distro_families(self):
        with contextlib.ExitStack() as stack:
            _distro_flags(stack, is_arch=True)
            cmd = vs._install_packages_cmd(vs._KVM_PACKAGES)
        self.assertEqual(cmd, ["pacman", "-S", "--noconfirm"] + vs._KVM_PACKAGES["arch"])
        for pkg in vs._KVM_PACKAGES["debian"] + vs._KVM_PACKAGES["fedora"] + vs._KVM_PACKAGES["opensuse"]:
            if pkg not in vs._KVM_PACKAGES["arch"]:
                self.assertNotIn(pkg, cmd)


class RestoreKvmConfigurationTests(VirtSetupTestCase):
    def test_nothing_to_restore_without_prior_configure(self):
        result = vs.restore_kvm_configuration()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "nothing_to_restore")

    def test_restores_service_that_was_inactive_before(self):
        os.makedirs(os.path.dirname(vs.state_path()), exist_ok=True)
        from core.persistence.atomic_io import write_json_atomic
        write_json_atomic(vs.state_path(), {
            "packages_installed_by_toolbox": True,
            "service_was_active_before": False,
            "service_was_enabled_before": False,
        })
        with mock.patch.object(vs, "_service_active", return_value=True), \
             mock.patch.object(vs, "_service_enabled", return_value=True), \
             mock.patch.object(vs, "run_pkexec") as mock_pkexec:
            result = vs.restore_kvm_configuration()
        self.assertTrue(result["ok"])
        self.assertEqual(mock_pkexec.call_count, 2)  # stop + disable
        self.assertFalse(os.path.exists(vs.state_path()))

    def test_leaves_service_alone_if_it_was_already_active_before(self):
        from core.persistence.atomic_io import write_json_atomic
        os.makedirs(os.path.dirname(vs.state_path()), exist_ok=True)
        write_json_atomic(vs.state_path(), {
            "packages_installed_by_toolbox": False,
            "service_was_active_before": True,
            "service_was_enabled_before": True,
        })
        with mock.patch.object(vs, "_service_active", return_value=True), \
             mock.patch.object(vs, "_service_enabled", return_value=True), \
             mock.patch.object(vs, "run_pkexec") as mock_pkexec:
            vs.restore_kvm_configuration()
        mock_pkexec.assert_not_called()

    def test_never_uninstalls_packages(self):
        from core.persistence.atomic_io import write_json_atomic
        os.makedirs(os.path.dirname(vs.state_path()), exist_ok=True)
        write_json_atomic(vs.state_path(), {
            "packages_installed_by_toolbox": True,
            "service_was_active_before": False,
            "service_was_enabled_before": False,
        })
        with mock.patch.object(vs, "_service_active", return_value=True), \
             mock.patch.object(vs, "_service_enabled", return_value=True), \
             mock.patch.object(vs, "run_pkexec") as mock_pkexec:
            vs.restore_kvm_configuration()
        for call in mock_pkexec.call_args_list:
            cmd = call[0][0]
            self.assertNotIn("remove", cmd)
            self.assertNotIn("uninstall", cmd)


class DeactivateAndVirtManagerTests(VirtSetupTestCase):
    def test_deactivate_services_stops_and_disables_unconditionally(self):
        with mock.patch.object(vs, "run_pkexec") as mock_pkexec, \
             mock.patch.object(vs, "_service_active", return_value=False):
            result = vs.deactivate_kvm_services()
        self.assertTrue(result["ok"])
        cmd = mock_pkexec.call_args[0][0]
        self.assertEqual(cmd, ["systemctl", "disable", "--now", vs.LIBVIRTD_SERVICE])

    def test_install_virt_manager_checks_result_after_install(self):
        with mock.patch.object(vs, "run_pkexec") as mock_pkexec, \
             mock.patch.object(vs, "virt_manager_installed", return_value=True):
            self.assertTrue(vs.install_virt_manager())
        mock_pkexec.assert_called_once()

    def test_open_virt_manager_refuses_when_not_installed(self):
        with mock.patch.object(vs, "virt_manager_installed", return_value=False), \
             mock.patch("subprocess.Popen") as mock_popen:
            self.assertFalse(vs.open_virt_manager())
        mock_popen.assert_not_called()

    def test_open_virt_manager_launches_detached_process(self):
        with mock.patch.object(vs, "virt_manager_installed", return_value=True), \
             mock.patch("subprocess.Popen") as mock_popen:
            self.assertTrue(vs.open_virt_manager())
        mock_popen.assert_called_once()
        self.assertTrue(mock_popen.call_args[1].get("start_new_session"))


if __name__ == "__main__":
    unittest.main()
