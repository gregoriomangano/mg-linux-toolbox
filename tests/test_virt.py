"""
Tests for core.virt_readiness, core.container_engines and
core.kernel_features.ksm. This dev machine has real KVM (AMD, kvm_amd
loaded, /dev/kvm present, user in kvm group), real IOMMU (AMD-Vi, 25
groups), and real Docker/Podman/Distrobox installations, so a handful of
assertions are genuine real-machine checks (kept minimal, clearly
labeled); everything else uses fakes/mocks for determinism.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import virt_readiness as vr
from core import container_engines as ce
from core.kernel_features.base import SupportStatus
from core.kernel_features.ksm import KsmFeature
from core import priv_writer
from core.persistence.rollback_store import JsonStateStore


class KvmReadinessTests(unittest.TestCase):
    @mock.patch.object(vr, "_user_in_group")
    @mock.patch.object(vr, "kvm_module_loaded")
    @mock.patch.object(vr, "_cpu_virt_flag_present")
    @mock.patch.object(vr, "_cpu_vendor")
    @mock.patch("os.path.exists")
    @mock.patch("os.access")
    def test_ready_state(self, mock_access, mock_exists, mock_vendor, mock_flag, mock_module, mock_group):
        mock_vendor.return_value = "amd"
        mock_flag.return_value = True
        mock_module.return_value = "kvm_amd"
        mock_exists.return_value = True
        mock_access.return_value = True
        mock_group.return_value = True
        status = vr.check_kvm()
        self.assertEqual(status["state"], "ready")

    @mock.patch.object(vr, "_cpu_virt_flag_present")
    @mock.patch.object(vr, "_cpu_vendor")
    def test_unavailable_when_cpu_unsupported(self, mock_vendor, mock_flag):
        mock_vendor.return_value = ""
        mock_flag.return_value = False
        status = vr.check_kvm()
        self.assertEqual(status["state"], "unavailable")

    @mock.patch.object(vr, "kvm_module_loaded")
    @mock.patch.object(vr, "_cpu_virt_flag_present")
    @mock.patch.object(vr, "_cpu_vendor")
    def test_missing_components_when_module_not_loaded(self, mock_vendor, mock_flag, mock_module):
        mock_vendor.return_value = "intel"
        mock_flag.return_value = True
        mock_module.return_value = ""
        status = vr.check_kvm()
        self.assertEqual(status["state"], "missing_components")

    @mock.patch.object(vr, "_user_in_group")
    @mock.patch.object(vr, "kvm_module_loaded")
    @mock.patch.object(vr, "_cpu_virt_flag_present")
    @mock.patch.object(vr, "_cpu_vendor")
    @mock.patch("os.path.exists")
    @mock.patch("os.access")
    def test_missing_permissions_when_not_in_group_and_not_writable(
            self, mock_access, mock_exists, mock_vendor, mock_flag, mock_module, mock_group):
        mock_vendor.return_value = "amd"
        mock_flag.return_value = True
        mock_module.return_value = "kvm_amd"
        mock_exists.return_value = True
        mock_access.return_value = False
        mock_group.return_value = False
        status = vr.check_kvm()
        self.assertEqual(status["state"], "missing_permissions")

    def test_real_machine_kvm_check_does_not_crash(self):
        status = vr.check_kvm()
        self.assertIn(status["state"], ("ready", "missing_components", "missing_permissions", "unavailable"))


class IommuVfioTests(unittest.TestCase):
    def test_iommu_inactive_when_no_groups_dir(self):
        with mock.patch("os.path.isdir", return_value=False):
            status = vr.check_iommu()
        self.assertFalse(status["active"])
        self.assertEqual(status["group_count"], 0)

    @mock.patch("os.listdir")
    @mock.patch("os.path.isdir")
    @mock.patch.object(vr, "_cpu_vendor")
    def test_iommu_active_amd(self, mock_vendor, mock_isdir, mock_listdir):
        mock_vendor.return_value = "amd"
        mock_isdir.return_value = True
        mock_listdir.side_effect = lambda p: ["0", "1", "2"] if "iommu_groups" in p else ["ivhd0"]
        status = vr.check_iommu()
        self.assertTrue(status["active"])
        self.assertEqual(status["technology"], "AMD-Vi")
        self.assertEqual(status["group_count"], 3)

    @mock.patch.object(vr, "check_iommu")
    @mock.patch.object(vr, "run_command")
    def test_vfio_reports_loaded_modules(self, mock_run, mock_iommu):
        mock_run.return_value = (True, "vfio 100 0\nvfio_pci 200 0\nother_mod 50 0\n", "")
        mock_iommu.return_value = {"active": True, "technology": "AMD-Vi", "group_count": 5}
        status = vr.check_vfio()
        self.assertEqual(set(status["modules_loaded"]), {"vfio", "vfio_pci"})
        self.assertTrue(status["iommu_active"])

    def test_real_machine_iommu_check_does_not_crash(self):
        status = vr.check_iommu()
        self.assertIsInstance(status["active"], bool)


class ContainerEnginesTests(unittest.TestCase):
    @mock.patch.object(ce, "shutil")
    def test_docker_not_installed(self, mock_shutil):
        mock_shutil.which.return_value = None
        status = ce.docker_status()
        self.assertEqual(status["state"], ce.DOCKER_STATE_NOT_INSTALLED)

    @mock.patch.object(ce, "run_command")
    @mock.patch.object(ce, "shutil")
    def test_docker_not_started(self, mock_shutil, mock_run):
        mock_shutil.which.return_value = "/usr/bin/docker"
        mock_run.side_effect = [(True, "inactive", ""), (False, "", "")]
        with mock.patch("os.path.exists", return_value=False):
            status = ce.docker_status()
        self.assertEqual(status["state"], ce.DOCKER_STATE_NOT_STARTED)

    @mock.patch.object(ce, "run_command")
    @mock.patch.object(ce, "shutil")
    def test_docker_ready(self, mock_shutil, mock_run):
        mock_shutil.which.return_value = "/usr/bin/docker"
        mock_run.side_effect = [(True, "active", ""), (True, "...", "")]
        with mock.patch("os.path.exists", return_value=True), mock.patch("os.access", return_value=True):
            status = ce.docker_status()
        self.assertEqual(status["state"], ce.DOCKER_STATE_READY)

    @mock.patch.object(ce, "shutil")
    def test_podman_not_installed(self, mock_shutil):
        mock_shutil.which.return_value = None
        status = ce.podman_status()
        self.assertEqual(status["state"], ce.PODMAN_STATE_NOT_INSTALLED)

    @mock.patch.object(ce, "run_command")
    @mock.patch.object(ce, "shutil")
    def test_podman_not_ready_missing_subuid(self, mock_shutil, mock_run):
        mock_shutil.which.side_effect = lambda c: "/usr/bin/podman" if c == "podman" else None
        mock_run.return_value = (True, "true", "")
        with mock.patch.object(ce, "_has_subid_entry", return_value=False):
            status = ce.podman_status()
        self.assertEqual(status["state"], ce.PODMAN_STATE_NOT_READY)
        self.assertTrue(status["rootless"])

    @mock.patch.object(ce, "shutil")
    def test_distrobox_not_installed(self, mock_shutil):
        mock_shutil.which.return_value = None
        status = ce.distrobox_status()
        self.assertEqual(status["state"], ce.DISTROBOX_STATE_NOT_INSTALLED)

    @mock.patch.object(ce, "docker_status")
    @mock.patch.object(ce, "podman_status")
    @mock.patch.object(ce, "run_command")
    @mock.patch.object(ce, "shutil")
    def test_distrobox_prefers_podman_backend(self, mock_shutil, mock_run, mock_podman, mock_docker):
        mock_shutil.which.return_value = "/usr/bin/distrobox"
        mock_run.return_value = (True, "distrobox: 1.8.0", "")
        mock_podman.return_value = {"state": ce.PODMAN_STATE_READY, "rootless": True}
        mock_docker.return_value = {"state": ce.DOCKER_STATE_READY}
        status = ce.distrobox_status()
        self.assertEqual(status["state"], ce.DISTROBOX_STATE_READY)
        self.assertEqual(status["backend"], "podman")

    @mock.patch.object(ce, "docker_status")
    @mock.patch.object(ce, "podman_status")
    @mock.patch.object(ce, "shutil")
    def test_distrobox_no_backend(self, mock_shutil, mock_podman, mock_docker):
        mock_shutil.which.return_value = "/usr/bin/distrobox"
        mock_podman.return_value = {"state": ce.PODMAN_STATE_NOT_INSTALLED, "rootless": None}
        mock_docker.return_value = {"state": ce.DOCKER_STATE_NOT_INSTALLED}
        with mock.patch.object(ce, "run_command", return_value=(True, "distrobox: 1.8.0", "")):
            status = ce.distrobox_status()
        self.assertEqual(status["state"], ce.DISTROBOX_STATE_NO_BACKEND)
        self.assertIsNone(status["backend"])

    def test_real_docker_status_does_not_crash(self):
        status = ce.docker_status()
        self.assertIn(status["state"], (ce.DOCKER_STATE_NOT_INSTALLED, ce.DOCKER_STATE_NOT_STARTED,
                                         ce.DOCKER_STATE_MISSING_PERMISSIONS, ce.DOCKER_STATE_READY))

    def test_real_podman_status_does_not_crash(self):
        status = ce.podman_status()
        self.assertIn(status["state"], (ce.PODMAN_STATE_NOT_INSTALLED, ce.PODMAN_STATE_NOT_READY,
                                         ce.PODMAN_STATE_READY))

    def test_real_distrobox_status_does_not_crash(self):
        status = ce.distrobox_status()
        self.assertIn(status["state"], (ce.DISTROBOX_STATE_NOT_INSTALLED, ce.DISTROBOX_STATE_NO_BACKEND,
                                         ce.DISTROBOX_STATE_READY))


class KsmFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sys_root = os.path.join(self.tmp, "sys")
        self.ksm_dir = os.path.join(self.sys_root, "kernel", "mm", "ksm")
        os.makedirs(self.ksm_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_run(self, value):
        with open(os.path.join(self.ksm_dir, "run"), "w") as f:
            f.write(value)

    def test_probe_unsupported_when_absent(self):
        feature = KsmFeature(sys_root=self.sys_root)
        self.assertEqual(feature.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_probe_supported_when_present(self):
        self._write_run("0")
        feature = KsmFeature(sys_root=self.sys_root)
        self.assertEqual(feature.probe(), SupportStatus.SUPPORTED_PERSISTENT)

    def test_apply_persistent_delegates_to_privileged_writer(self):
        self._write_run("0")
        fake_writer = mock.Mock()
        fake_writer.execute.return_value = "sentinel"
        feature = KsmFeature(sys_root=self.sys_root, privileged_writer=fake_writer)
        result = feature.apply_persistent(True)
        fake_writer.execute.assert_called_once_with("virt.ksm", "apply_persistent", True)
        self.assertEqual(result, "sentinel")

    def test_apply_persistent_rejects_invalid_value(self):
        self._write_run("0")
        feature = KsmFeature(sys_root=self.sys_root)
        result = feature.apply_persistent("on")
        self.assertFalse(result.ok)

    def test_read_current_off(self):
        self._write_run("0")
        feature = KsmFeature(sys_root=self.sys_root)
        result = feature.read_current()
        self.assertTrue(result.ok)
        self.assertFalse(result.value)

    def test_read_current_on(self):
        self._write_run("1")
        feature = KsmFeature(sys_root=self.sys_root)
        result = feature.read_current()
        self.assertTrue(result.ok)
        self.assertTrue(result.value)

    def test_to_friendly(self):
        feature = KsmFeature(sys_root=self.sys_root)
        self.assertEqual(feature.to_friendly(True), "ksm_on")
        self.assertEqual(feature.to_friendly(False), "ksm_off")

    def test_validate(self):
        feature = KsmFeature(sys_root=self.sys_root)
        self.assertTrue(feature.validate(True))
        self.assertFalse(feature.validate("on"))

    def test_real_machine_ksm_probe_does_not_crash(self):
        feature = KsmFeature()
        self.assertIn(feature.probe(), (SupportStatus.SUPPORTED_PERSISTENT, SupportStatus.UNAVAILABLE,
                                         SupportStatus.UNSUPPORTED_KERNEL))


class PrivWriterKsmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "run")
        with open(self.path, "w") as f:
            f.write("0")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.KsmWriter()
        self.writer.PATH = self.path
        self.tmpfiles_patch = mock.patch.object(
            priv_writer.tmpfiles_store, "TMPFILES_FILE", os.path.join(self.tmp, "tmpfiles.conf"))
        self.tmpfiles_patch.start()
        self.tmpfiles_known_paths_patch = mock.patch.object(
            priv_writer.tmpfiles_store, "KNOWN_PATHS", {self.path})
        self.tmpfiles_known_paths_patch.start()

    def tearDown(self):
        self.tmpfiles_patch.stop()
        self.tmpfiles_known_paths_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read_path(self) -> str:
        with open(self.path) as f:
            return f.read().strip()

    def test_apply_and_restore(self):
        result = self.writer.apply_temporary("1", None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertTrue(result["value"])

        restored = self.writer.restore(None, None, False, self.state)
        self.assertTrue(restored["ok"])
        self.assertFalse(restored["value"])

    def test_restore_without_prior_apply_fails_cleanly(self):
        result = self.writer.restore(None, None, False, self.state)
        self.assertFalse(result["ok"])

    def test_restore_detects_external_change(self):
        self.writer.apply_temporary("1", None, False, self.state)
        with open(self.path, "w") as f:
            f.write("0")
        result = self.writer.restore(None, None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_external_change_detected")

    def test_apply_persistent_writes_tmpfiles_entry(self):
        result = self.writer.apply_persistent("1", None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(priv_writer.tmpfiles_store.read_value(self.path), "1")

    def test_restore_after_persistent_removes_tmpfiles_entry(self):
        self.writer.apply_persistent("1", None, False, self.state)
        restored = self.writer.restore(None, None, False, self.state)
        self.assertTrue(restored["ok"])
        self.assertIsNone(priv_writer.tmpfiles_store.read_value(self.path))

    def test_restore_after_temporary_leaves_no_tmpfiles_entry_to_remove(self):
        self.writer.apply_temporary("1", None, False, self.state)
        restored = self.writer.restore(None, None, False, self.state)
        self.assertTrue(restored["ok"])
        self.assertIsNone(priv_writer.tmpfiles_store.read_value(self.path))

    def test_run_value_2_reads_as_enabled(self):
        # 2 = "stop merging and unmerge" — KSM machinery still engaged,
        # read as enabled just like the read side does.
        with open(self.path, "w") as f:
            f.write("2")
        self.assertTrue(self.writer._read_enabled())

    # ── Regression: real Beta 4 restore bug ─────────────────────────
    # Reported from a real machine: KSM 0 -> "Prova fino al riavvio" (1,
    # verified on the real file) -> "Ripristina" recorded result=ok and
    # verified_value=true in the history, but /sys/kernel/mm/ksm/run
    # stayed at 1 — restore() wrote and re-read the file but never
    # actually compared the two before declaring success.
    def test_regression_ksm_0_to_1_to_restore_returns_to_0(self):
        self.assertEqual(self._read_path(), "0")

        applied = self.writer.apply_temporary("1", None, False, self.state)
        self.assertTrue(applied["ok"])
        self.assertTrue(applied["value"])
        self.assertEqual(self._read_path(), "1")

        restored = self.writer.restore(None, None, False, self.state)
        self.assertTrue(restored["ok"])
        self.assertFalse(restored["value"])
        self.assertEqual(self._read_path(), "0",
                         "il file reale deve tornare a 0, non solo il valore riportato dall'API")

    def test_restore_reports_failure_when_the_write_does_not_take_effect(self):
        """The exact defect: if the on-disk value doesn't actually change
        to match what restore() wrote, this must NEVER return ok=True —
        simulated here by making the real file read-only after the
        temporary apply, so restore()'s write is silently a no-op at the
        OS level for the *content* the writer itself controls (the write
        call still succeeds against the file being openable, but a
        mismatched value written via a stub _read_enabled proves the
        comparison itself, independent of how the mismatch happens in
        practice on real hardware)."""
        applied = self.writer.apply_temporary("1", None, False, self.state)
        self.assertTrue(applied["ok"])

        # Force the post-write re-read to report a value that never
        # actually changed, exactly like the real KSM bug — write
        # happens, but the re-read disagrees with what was requested.
        with mock.patch.object(priv_writer.KsmWriter, "_read_enabled", return_value=True):
            result = self.writer.restore(None, None, False, self.state)

        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_write_mismatch")
        # No entry recorded as a successful restore for a write that
        # never really happened.
        rec = self.state.get(self.writer.KEY)
        self.assertNotEqual(rec.mode, "restored")

    def test_restore_never_reuses_a_stale_initial_value_from_an_earlier_trial(self):
        """The other half of the real bug: a value recorded during some
        earlier, already-finished trial must never be what a brand new
        "Prova fino al riavvio" restores to — each apply captures the
        real value right before itself."""
        # Trial 1: 0 -> 1 -> restore -> 0 (leaves a record behind).
        self.writer.apply_temporary("1", None, False, self.state)
        self.writer.restore(None, None, False, self.state)
        self.assertEqual(self._read_path(), "0")

        # Something else (or the user, outside the app) sets it to 1
        # before the NEXT trial even starts.
        with open(self.path, "w") as f:
            f.write("1")

        # Trial 2: a fresh "Prova fino al riavvio" toggling it OFF this
        # time. The pre-trial value (1) must be what gets restored to —
        # never the stale initial_value (0) left over from trial 1.
        applied = self.writer.apply_temporary("0", None, False, self.state)
        self.assertTrue(applied["ok"])
        self.assertEqual(self._read_path(), "0")

        restored = self.writer.restore(None, None, False, self.state)
        self.assertTrue(restored["ok"])
        self.assertEqual(self._read_path(), "1",
                         "deve tornare al valore vero prima della prova 2 (1), non allo stale initial_value (0) della prova 1")


class KsmAutostartStateTests(unittest.TestCase):
    """Beta 4: /sys/.../ksm/run = 1 only means 'active NOW'. The
    'autostart configured' fact must come from the real tmpfiles entry,
    never be inferred from the runtime value."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.tmpfiles_file = os.path.join(self.tmp, "tmpfiles.conf")
        patcher = mock.patch(
            "core.persistence.tmpfiles_store.TMPFILES_FILE", self.tmpfiles_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_runtime_active_but_not_configured_reads_as_not_configured(self):
        feature = KsmFeature()
        self.assertFalse(feature.autostart_state())

    def test_configured_entry_reads_as_configured(self):
        with open(self.tmpfiles_file, "w") as f:
            f.write("w /sys/kernel/mm/ksm/run - - - - 1\n")
        feature = KsmFeature()
        self.assertTrue(feature.autostart_state())


if __name__ == "__main__":
    unittest.main()
