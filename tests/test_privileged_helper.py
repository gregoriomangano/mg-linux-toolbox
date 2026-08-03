"""
Tests for the Beta 4 privileged-helper architecture — the fix for the
real AppImage bug ("/usr/bin/python3: can't open file
'/tmp/.mount_.../priv_writer.py': Permission denied"):

  1. the generated standalone helper (packaging/helper/) stays in sync
     with its sources and answers the read-only diagnose action;
  2. the unprivileged client verifies owner/permissions/version of the
     installed helper and NEVER passes a /tmp/.mount_* path to pkexec;
  3. the new root-side writers (nested virt, DNS-over-TLS, root SSH,
     VFIO transaction, IOMMU transaction, helper self-update) validate
     everything themselves, verify after writing, and roll back.

Per project policy nothing here touches the real system: every path is
a temp file, every regen command is /bin/true or /bin/false, and pkexec
is never actually invoked.
"""
import json
import os
import stat as stat_module
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATED_HELPER = os.path.join(REPO_ROOT, "packaging", "helper", "mg-privileged-helper")

from core import priv_writer
from core.persistence import priv_client
from core.persistence.rollback_store import JsonStateStore
from core.privileged import helper_meta


class _TmpState:
    """A JsonStateStore in a temp dir, one per test."""

    def __init__(self, testcase):
        self._tmpdir = tempfile.TemporaryDirectory()
        testcase.addCleanup(self._tmpdir.cleanup)
        self.store = JsonStateStore(os.path.join(self._tmpdir.name, "state.json"))
        self.dir = self._tmpdir.name


# ── 1. Generated helper ───────────────────────────────────────────────
class GeneratedHelperTests(unittest.TestCase):
    def test_generated_helper_exists_and_is_in_sync_with_sources(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        try:
            import build_privileged_helper as builder
        finally:
            sys.path.pop(0)
        expected = builder.build()
        with open(GENERATED_HELPER, encoding="utf-8") as f:
            actual = f.read()
        self.assertEqual(actual, expected,
                         "packaging/helper/mg-privileged-helper non è aggiornato: "
                         "esegui python3 scripts/build_privileged_helper.py")

    def test_markers_match_helper_meta(self):
        with open(GENERATED_HELPER, encoding="utf-8") as f:
            text = f.read(65536)
        self.assertEqual(helper_meta.parse_marker(text, helper_meta.VERSION_MARKER),
                         helper_meta.HELPER_VERSION)
        self.assertEqual(helper_meta.parse_marker(text, helper_meta.PROTOCOL_MARKER),
                         str(helper_meta.PROTOCOL_VERSION))

    def test_source_markers_match_helper_meta(self):
        self.assertEqual(priv_writer.MG_HELPER_VERSION, helper_meta.HELPER_VERSION)
        self.assertEqual(priv_writer.MG_HELPER_PROTOCOL, str(helper_meta.PROTOCOL_VERSION))

    def test_standalone_helper_answers_diagnose_without_root(self):
        proc = subprocess.run(
            [sys.executable, GENERATED_HELPER, "helper.ping", "diagnose", "", "", "0"],
            capture_output=True, text=True, timeout=30)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["value"]["helper_version"], helper_meta.HELPER_VERSION)
        self.assertFalse(payload["value"]["running_as_root"])

    def test_standalone_helper_refuses_unknown_feature(self):
        proc = subprocess.run(
            [sys.executable, GENERATED_HELPER, "not.a.feature", "diagnose", "", "", "0"],
            capture_output=True, text=True, timeout=30)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(proc.returncode, 1)

    def test_standalone_helper_refuses_unknown_action(self):
        proc = subprocess.run(
            [sys.executable, GENERATED_HELPER, "memory.swappiness", "__class__", "", "", "0"],
            capture_output=True, text=True, timeout=30)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])

    def test_standalone_helper_refuses_privileged_action_without_root(self):
        proc = subprocess.run(
            [sys.executable, GENERATED_HELPER, "memory.swappiness", "apply_temporary", "10", "", "0"],
            capture_output=True, text=True, timeout=30)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["friendly_message"], "kf_err_permission")

    def test_helper_never_imports_from_a_user_writable_location(self):
        with open(GENERATED_HELPER, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("/tmp/.mount_", text)
        self.assertNotIn('sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")', text)


# ── 2. Client-side verification ──────────────────────────────────────
def _fake_stat(uid=0, gid=0, mode=0o100755):
    st = mock.MagicMock()
    st.st_uid = uid
    st.st_gid = gid
    st.st_mode = mode
    return st


class InstalledHelperStatusTests(unittest.TestCase):
    def test_missing_helper_reported_as_missing(self):
        with mock.patch.object(priv_client.os, "lstat", side_effect=FileNotFoundError):
            status = priv_client.installed_helper_status()
        self.assertEqual(status.state, priv_client.HELPER_MISSING)

    def test_wrong_owner_is_refused(self):
        with mock.patch.object(priv_client.os, "lstat",
                               return_value=_fake_stat(uid=os.getuid() or 1000)):
            status = priv_client._check_installed_helper("/usr/libexec/x/helper")
        self.assertEqual(status.state, priv_client.HELPER_WRONG_OWNER)

    def test_group_or_other_writable_is_refused(self):
        with mock.patch.object(priv_client.os, "lstat",
                               return_value=_fake_stat(mode=0o100775)):
            status = priv_client._check_installed_helper("/usr/libexec/x/helper")
        self.assertEqual(status.state, priv_client.HELPER_USER_WRITABLE)

    def test_symlink_or_non_regular_file_is_refused(self):
        with mock.patch.object(priv_client.os, "lstat",
                               return_value=_fake_stat(mode=stat_module.S_IFLNK | 0o755)):
            status = priv_client._check_installed_helper("/usr/libexec/x/helper")
        self.assertEqual(status.state, priv_client.HELPER_WRONG_OWNER)

    def test_incompatible_protocol_is_reported(self):
        content = 'MG_HELPER_VERSION = "9.9.9"\nMG_HELPER_PROTOCOL = "999"\n'
        with mock.patch.object(priv_client.os, "lstat", return_value=_fake_stat()), \
             mock.patch("builtins.open", mock.mock_open(read_data=content)):
            status = priv_client._check_installed_helper("/usr/libexec/x/helper")
        self.assertEqual(status.state, priv_client.HELPER_INCOMPATIBLE)
        self.assertEqual(status.version, "9.9.9")

    def test_valid_helper_is_ready_with_version(self):
        content = (f'MG_HELPER_VERSION = "{helper_meta.HELPER_VERSION}"\n'
                   f'MG_HELPER_PROTOCOL = "{helper_meta.PROTOCOL_VERSION}"\n')
        with mock.patch.object(priv_client.os, "lstat", return_value=_fake_stat()), \
             mock.patch("builtins.open", mock.mock_open(read_data=content)):
            status = priv_client._check_installed_helper("/usr/libexec/x/helper")
        self.assertEqual(status.state, priv_client.HELPER_READY)
        self.assertTrue(status.usable)


class ResolvePrivilegedArgvTests(unittest.TestCase):
    def test_appimage_launch_without_helper_disables_privileged_ops(self):
        with mock.patch.object(priv_client, "installed_helper_status",
                               return_value=priv_client.HelperStatus(priv_client.HELPER_MISSING)), \
             mock.patch.dict(os.environ, {"APPIMAGE": "/home/user/Scaricati/MG.AppImage"}):
            argv, status = priv_client.resolve_privileged_argv()
        self.assertEqual(argv, [])
        self.assertEqual(status.state, priv_client.HELPER_MISSING)

    def test_source_checkout_falls_back_to_dev_writer(self):
        env = {k: v for k, v in os.environ.items() if k != "APPIMAGE"}
        with mock.patch.object(priv_client, "installed_helper_status",
                               return_value=priv_client.HelperStatus(priv_client.HELPER_MISSING)), \
             mock.patch.dict(os.environ, env, clear=True):
            argv, _ = priv_client.resolve_privileged_argv()
        self.assertEqual(argv[:2], ["pkexec", "python3"])
        self.assertTrue(argv[2].endswith("core/priv_writer.py"))
        self.assertFalse(argv[2].startswith("/tmp/"))

    def test_dev_writer_under_tmp_is_never_used(self):
        env = {k: v for k, v in os.environ.items() if k != "APPIMAGE"}
        with mock.patch.object(priv_client, "installed_helper_status",
                               return_value=priv_client.HelperStatus(priv_client.HELPER_MISSING)), \
             mock.patch.object(priv_client, "_PRIV_WRITER_PATH",
                               "/tmp/.mount_MGLinux/usr/share/mg-linux-toolbox/core/priv_writer.py"), \
             mock.patch.dict(os.environ, env, clear=True):
            argv, _ = priv_client.resolve_privileged_argv()
        self.assertEqual(argv, [])

    def test_ready_helper_is_used_via_its_stable_path(self):
        ready = priv_client.HelperStatus(priv_client.HELPER_READY,
                                          path="/usr/libexec/mg-linux-toolbox/mg-privileged-helper",
                                          version=helper_meta.HELPER_VERSION)
        with mock.patch.object(priv_client, "installed_helper_status", return_value=ready):
            argv, _ = priv_client.resolve_privileged_argv()
        self.assertEqual(argv, ["pkexec", "/usr/libexec/mg-linux-toolbox/mg-privileged-helper"])


class ExecuteRefusalTests(unittest.TestCase):
    def _writer(self, argv, state):
        return priv_client.PrivilegedWriter(
            history_store=mock.MagicMock(),
            argv_resolver=lambda: (argv, priv_client.HelperStatus(state)))

    def test_execute_refuses_when_helper_unavailable_without_running_anything(self):
        writer = self._writer([], priv_client.HELPER_MISSING)
        with mock.patch("subprocess.run") as mock_run:
            result = writer.execute("virt.ksm", "apply_temporary", True, record_history=False)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "kf_err_helper_missing")
        mock_run.assert_not_called()

    def test_untrusted_helper_gets_its_own_message(self):
        writer = self._writer([], priv_client.HELPER_USER_WRITABLE)
        result = writer.execute("virt.ksm", "apply_temporary", True, record_history=False)
        self.assertEqual(result.friendly_message, "kf_err_helper_untrusted")

    def test_incompatible_helper_gets_its_own_message(self):
        writer = self._writer([], priv_client.HELPER_INCOMPATIBLE)
        result = writer.execute("virt.ksm", "apply_temporary", True, record_history=False)
        self.assertEqual(result.friendly_message, "kf_err_helper_incompatible")

    def test_appimage_mount_path_is_never_passed_to_pkexec(self):
        # Defense in depth: even if a resolver bug produced a mount
        # path, execute() must refuse rather than escalate it.
        writer = self._writer(["pkexec", "python3", "/tmp/.mount_MG123/core/priv_writer.py"],
                               priv_client.HELPER_MISSING)
        with mock.patch("subprocess.run") as mock_run:
            result = writer.execute("virt.ksm", "apply_temporary", True, record_history=False)
        self.assertFalse(result.ok)
        mock_run.assert_not_called()

    def test_diagnostics_fail_cleanly_when_helper_missing(self):
        with mock.patch.object(priv_client, "installed_helper_status",
                               return_value=priv_client.HelperStatus(priv_client.HELPER_MISSING)):
            result = priv_client.run_helper_diagnostics()
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "kf_err_helper_missing")


# ── 3. Root-side writers ─────────────────────────────────────────────
class NestedVirtWriterTests(unittest.TestCase):
    def setUp(self):
        self.state = _TmpState(self)
        self.writer = priv_writer.NestedVirtWriter()
        self.nested_path = os.path.join(self.state.dir, "nested")
        with open(self.nested_path, "w") as f:
            f.write("0\n")
        self.writer.INTEL_PATH = self.nested_path
        self.writer.AMD_PATH = os.path.join(self.state.dir, "missing")

    def test_enable_writes_and_verifies(self):
        result = self.writer.apply_temporary("True", None, False, self.state.store)
        self.assertTrue(result["ok"])
        with open(self.nested_path) as f:
            self.assertEqual(f.read(), "1")

    def test_restore_returns_to_initial_value(self):
        self.writer.apply_temporary("True", None, False, self.state.store)
        result = self.writer.restore(None, None, False, self.state.store)
        self.assertTrue(result["ok"])
        with open(self.nested_path) as f:
            self.assertEqual(f.read(), "0")

    def test_unsupported_hardware_reported(self):
        self.writer.INTEL_PATH = os.path.join(self.state.dir, "no1")
        result = self.writer.apply_temporary("True", None, False, self.state.store)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_unsupported_hardware")


class ConfigLineWritersTests(unittest.TestCase):
    def _dns_writer(self, service_ok=True):
        state = _TmpState(self)
        writer = priv_writer.DnsOverTlsWriter()
        writer = type(writer)()  # fresh instance so attribute overrides stay local
        conf = os.path.join(state.dir, "resolved.conf")
        with open(conf, "w") as f:
            f.write("[Resolve]\n#DNSOverTLS=no\n")
        writer.FILE_PATH = conf
        writer.SERVICE_CMDS = (["true"] if service_ok else ["false"],)
        return writer, conf, state

    def test_dns_dot_enable_edits_only_the_directive(self):
        writer, conf, state = self._dns_writer()
        result = writer.apply_temporary("True", None, False, state.store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "yes")
        with open(conf) as f:
            content = f.read()
        self.assertIn("[Resolve]", content)
        self.assertIn("DNSOverTLS=yes", content)
        self.assertTrue(os.path.exists(f"{conf}.bak"))

    def test_dns_dot_invalid_value_refused(self):
        writer, _conf, state = self._dns_writer()
        result = writer.apply_temporary("maybe", None, False, state.store)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_invalid_value")

    def test_dns_dot_service_failure_is_reported(self):
        writer, _conf, state = self._dns_writer(service_ok=False)
        result = writer.apply_temporary("True", None, False, state.store)
        self.assertFalse(result["ok"])

    def test_root_ssh_disable_sets_permitrootlogin_no(self):
        state = _TmpState(self)
        writer = priv_writer.RootSshWriter()
        conf = os.path.join(state.dir, "sshd_config")
        with open(conf, "w") as f:
            f.write("#PermitRootLogin prohibit-password\nPort 22\n")
        writer.FILE_PATH = conf
        writer.SERVICE_CMDS = (["true"],)
        result = writer.apply_temporary("True", None, False, state.store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "no")
        with open(conf) as f:
            content = f.read()
        self.assertIn("PermitRootLogin no", content)
        self.assertIn("Port 22", content)

    def test_missing_config_file_is_a_clean_refusal(self):
        state = _TmpState(self)
        writer = priv_writer.RootSshWriter()
        writer.FILE_PATH = os.path.join(state.dir, "does-not-exist")
        writer.SERVICE_CMDS = (["true"],)
        result = writer.apply_temporary("True", None, False, state.store)
        self.assertFalse(result["ok"])


class VfioWriterTests(unittest.TestCase):
    ADDRESS = "0000:01:00.0"

    def setUp(self):
        self.state = _TmpState(self)
        self.writer = priv_writer.VfioWriter()
        self.pci_dir = os.path.join(self.state.dir, "pci")
        self.writer.PCI_DEVICES_DIR = self.pci_dir
        self.writer.MODPROBE_FILE = os.path.join(self.state.dir, "vfio.conf")
        self.writer.MODULES_LOAD_FILE = os.path.join(self.state.dir, "vfio-modules.conf")
        self._make_device(self.ADDRESS, pci_class="0x030000", boot_vga="0",
                          vendor="0x10de", device="0x1234")
        self.writer._initramfs_cmd = lambda: ["true"]

    def _make_device(self, address, pci_class, boot_vga, vendor, device):
        d = os.path.join(self.pci_dir, address)
        os.makedirs(d, exist_ok=True)
        for name, value in (("class", pci_class), ("boot_vga", boot_vga),
                            ("vendor", vendor), ("device", device)):
            with open(os.path.join(d, name), "w") as f:
                f.write(value + "\n")

    def _configure(self, addresses):
        return self.writer.configure(json.dumps({"addresses": addresses}),
                                      None, False, self.state.store)

    def test_configure_writes_both_files_with_ids_read_from_sys(self):
        result = self._configure([self.ADDRESS])
        self.assertTrue(result["ok"])
        self.assertTrue(result["reboot_required"])
        with open(self.writer.MODPROBE_FILE) as f:
            self.assertIn("options vfio-pci ids=10de:1234", f.read())
        with open(self.writer.MODULES_LOAD_FILE) as f:
            self.assertIn("vfio_pci", f.read())

    def test_malformed_address_refused(self):
        result = self._configure(["../../etc/passwd"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["technical_detail"], "bad_address_format")

    def test_unknown_device_refused(self):
        result = self._configure(["0000:99:00.0"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["technical_detail"], "device_not_found")

    def test_storage_controller_refused_server_side(self):
        self._make_device("0000:00:17.0", pci_class="0x010601", boot_vga="0",
                          vendor="0x8086", device="0x2822")
        result = self._configure(["0000:00:17.0"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["technical_detail"], "storage_controller")

    def test_boot_vga_refused_server_side(self):
        self._make_device("0000:02:00.0", pci_class="0x030000", boot_vga="1",
                          vendor="0x1002", device="0x9999")
        result = self._configure(["0000:02:00.0"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["technical_detail"], "primary_gpu")

    def test_bridge_class_refused_server_side(self):
        self._make_device("0000:00:01.0", pci_class="0x060400", boot_vga="0",
                          vendor="0x1022", device="0x1483")
        result = self._configure(["0000:00:01.0"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["technical_detail"], "essential_device")

    def test_empty_selection_refused(self):
        result = self._configure([])
        self.assertFalse(result["ok"])
        self.assertEqual(result["technical_detail"], "no_devices")

    def test_initramfs_failure_rolls_every_file_back(self):
        self.writer._initramfs_cmd = lambda: ["false"]
        result = self._configure([self.ADDRESS])
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "vfio_err_initramfs")
        # Files did not exist before, so the rollback removes them.
        self.assertFalse(os.path.exists(self.writer.MODPROBE_FILE))
        self.assertFalse(os.path.exists(self.writer.MODULES_LOAD_FILE))

    def test_initramfs_failure_restores_previous_content(self):
        with open(self.writer.MODPROBE_FILE, "w") as f:
            f.write("options vfio-pci ids=aaaa:bbbb\n")
        self.writer._initramfs_cmd = lambda: ["false"]
        self._configure([self.ADDRESS])
        with open(self.writer.MODPROBE_FILE) as f:
            self.assertIn("aaaa:bbbb", f.read())

    def test_disable_removes_files_and_regenerates(self):
        self._configure([self.ADDRESS])
        result = self.writer.disable(None, None, False, self.state.store)
        self.assertTrue(result["ok"])
        self.assertTrue(result["value"]["changed"])
        self.assertFalse(os.path.exists(self.writer.MODPROBE_FILE))

    def test_disable_with_nothing_configured_is_a_no_op(self):
        result = self.writer.disable(None, None, False, self.state.store)
        self.assertTrue(result["ok"])
        self.assertFalse(result["value"]["changed"])

    def test_disable_regen_failure_restores_the_files(self):
        self._configure([self.ADDRESS])
        self.writer._initramfs_cmd = lambda: ["false"]
        result = self.writer.disable(None, None, False, self.state.store)
        self.assertFalse(result["ok"])
        self.assertTrue(os.path.exists(self.writer.MODPROBE_FILE))


class IommuWriterTests(unittest.TestCase):
    def setUp(self):
        self.state = _TmpState(self)
        self.writer = priv_writer.IommuWriter()
        self.grub_file = os.path.join(self.state.dir, "grub")
        with open(self.grub_file, "w") as f:
            f.write('GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n')
        self.writer.GRUB_DEFAULT_FILE = self.grub_file
        self.cpuinfo = os.path.join(self.state.dir, "cpuinfo")
        with open(self.cpuinfo, "w") as f:
            f.write("vendor_id\t: AuthenticAMD\n")
        self.writer.CPUINFO_PATH = self.cpuinfo
        self.writer._bootloader = lambda: "grub"
        self.writer._grub_regen_cmd = lambda: ["true"]

    def test_enable_adds_params_with_backup_and_reboot_flag(self):
        result = self.writer.enable(None, None, False, self.state.store)
        self.assertTrue(result["ok"])
        self.assertTrue(result["reboot_required"])
        with open(self.grub_file) as f:
            content = f.read()
        self.assertIn("amd_iommu=on", content)
        self.assertIn("iommu=pt", content)
        self.assertTrue(os.path.exists(f"{self.grub_file}.bak"))

    def test_enable_twice_is_a_no_op_the_second_time(self):
        self.writer.enable(None, None, False, self.state.store)
        result = self.writer.enable(None, None, False, self.state.store)
        self.assertTrue(result["ok"])
        self.assertFalse(result["value"]["changed"])

    def test_regen_failure_rolls_the_file_back(self):
        self.writer._grub_regen_cmd = lambda: ["false"]
        result = self.writer.enable(None, None, False, self.state.store)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "iommu_err_regen")
        with open(self.grub_file) as f:
            self.assertNotIn("amd_iommu=on", f.read())

    def test_disable_removes_only_our_params(self):
        self.writer.enable(None, None, False, self.state.store)
        result = self.writer.disable(None, None, False, self.state.store)
        self.assertTrue(result["ok"])
        with open(self.grub_file) as f:
            content = f.read()
        self.assertNotIn("amd_iommu=on", content)
        self.assertIn("quiet splash", content)

    def test_restore_uses_the_backup_file(self):
        self.writer.enable(None, None, False, self.state.store)
        result = self.writer.restore(None, None, False, self.state.store)
        self.assertTrue(result["ok"])
        with open(self.grub_file) as f:
            self.assertEqual(f.read(), 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n')

    def test_unknown_cpu_vendor_refused(self):
        with open(self.cpuinfo, "w") as f:
            f.write("vendor_id\t: SomethingElse\n")
        result = self.writer.enable(None, None, False, self.state.store)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "iommu_err_cpu_vendor")


class HelperUpdateWriterTests(unittest.TestCase):
    def _candidate_text(self, version="9.9.9"):
        return (f'MG_HELPER_VERSION = "{version}"\n'
                f'MG_HELPER_PROTOCOL = "1"\n'
                "print('helper')\n")

    def setUp(self):
        self.state = _TmpState(self)
        self.writer = priv_writer.HelperUpdateWriter()
        self.target = os.path.join(self.state.dir, "installed-helper")
        self.writer.INSTALL_PATH = self.target
        self.writer.FALLBACK_INSTALL_PATH = os.path.join(self.state.dir, "fallback-helper")
        self.writer._chown_root = lambda path: None  # unprivileged test run

    def _stage(self, text):
        import hashlib
        path = os.path.join(self.state.dir, "candidate")
        with open(path, "w") as f:
            f.write(text)
        digest = hashlib.sha256(text.encode()).hexdigest()
        return path, digest

    def _run(self, path, digest):
        return self.writer.self_update(
            json.dumps({"source_path": path, "expected_sha256": digest}),
            None, False, self.state.store)

    def test_valid_candidate_is_installed(self):
        path, digest = self._stage(self._candidate_text())
        result = self._run(path, digest)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"]["installed_version"], "9.9.9")
        with open(self.target) as f:
            self.assertIn('MG_HELPER_VERSION = "9.9.9"', f.read())

    def test_checksum_mismatch_refused(self):
        path, _digest = self._stage(self._candidate_text())
        result = self._run(path, "0" * 64)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "helper_update_err_checksum")
        self.assertFalse(os.path.exists(self.target))

    def test_malformed_sha_refused(self):
        path, _ = self._stage(self._candidate_text())
        result = self._run(path, "nothex")
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_invalid_value")

    def test_symlink_candidate_refused(self):
        real, digest = self._stage(self._candidate_text())
        link = os.path.join(self.state.dir, "link")
        os.symlink(real, link)
        result = self._run(link, digest)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "helper_update_err_source")

    def test_non_python_candidate_refused(self):
        text = "this is ( not python\n"
        path, digest = self._stage(text)
        result = self._run(path, digest)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "helper_update_err_source")

    def test_candidate_without_markers_refused(self):
        text = "print('no markers')\n"
        path, digest = self._stage(text)
        result = self._run(path, digest)
        self.assertFalse(result["ok"])

    def test_downgrade_refused(self):
        path, digest = self._stage(self._candidate_text(version="0.0.1"))
        result = self._run(path, digest)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "helper_update_err_downgrade")

    def test_previous_helper_backed_up_before_replacement(self):
        with open(self.target, "w") as f:
            f.write("old helper\n")
        path, digest = self._stage(self._candidate_text())
        result = self._run(path, digest)
        self.assertTrue(result["ok"])
        with open(f"{self.target}.previous") as f:
            self.assertEqual(f.read(), "old helper\n")


class RestoreVerificationRegressionTests(unittest.TestCase):
    """The shared restore-verification bug, checked against categories
    other than KSM (sysfs) — a real fix in _note_initial() (always
    capture the value right before THIS trial, never reuse a stale one)
    plus a write/re-read mismatch check added to EVERY restore() method
    in core/priv_writer.py (24 writers, audited individually), not just
    the one originally reported. This covers a sysctl-backed writer
    (memory.swappiness, /proc + /etc/sysctl.d) as the second concrete
    category alongside the dedicated KSM regression suite in
    tests/test_virt.py::PrivWriterKsmTests."""

    def setUp(self):
        self.state = _TmpState(self)
        self.writer = priv_writer.SwappinessWriter()
        self.path = os.path.join(self.state.dir, "swappiness")
        with open(self.path, "w") as f:
            f.write("60")
        self.writer.PATH = self.path
        sysctl_patch = mock.patch.object(priv_writer.sysctl_store, "SYSCTL_FILE",
                                         os.path.join(self.state.dir, "90-mg.conf"))
        sysctl_patch.start()
        self.addCleanup(sysctl_patch.stop)
        known_patch = mock.patch.object(priv_writer.sysctl_store, "KNOWN_KEYS", {"vm.swappiness"})
        known_patch.start()
        self.addCleanup(known_patch.stop)

    def _read_path(self) -> str:
        with open(self.path) as f:
            return f.read().strip()

    def test_sysctl_restore_reports_failure_when_write_does_not_take_effect(self):
        self.writer.apply_temporary("120", None, False, self.state.store)
        with mock.patch.object(priv_writer.SwappinessWriter, "_read", return_value=120):
            result = self.writer.restore(None, None, False, self.state.store)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_err_write_mismatch")

    def test_sysctl_restore_never_reuses_stale_initial_value(self):
        # Trial 1
        self.writer.apply_temporary("120", None, False, self.state.store)
        self.writer.restore(None, None, False, self.state.store)
        self.assertEqual(self._read_path(), "60")
        # External change right before trial 2 starts
        with open(self.path, "w") as f:
            f.write("100")
        # Trial 2: pre-trial value (100) is what must come back, not the
        # stale initial_value (60) from trial 1.
        self.writer.apply_temporary("10", None, False, self.state.store)
        self.assertEqual(self._read_path(), "10")
        result = self.writer.restore(None, None, False, self.state.store)
        self.assertTrue(result["ok"])
        self.assertEqual(self._read_path(), "100")


if __name__ == "__main__":
    unittest.main()
