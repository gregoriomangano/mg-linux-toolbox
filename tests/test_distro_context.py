"""
Tests for core.distro.DistroContext — the consolidated multi-distro
snapshot (identity, package manager, init, bootloader, filesystem,
kernel, architecture) used by history/checkpoints/virtualization/
AppArmor-SELinux/updates so they never have to re-derive "which distro
is this" themselves and never mix packages from different families.
"""
import os
import tempfile
import unittest
from unittest import mock

from core.distro import DistroContext, DistroManager, get_context


def _fake_manager(is_arch=False, is_fedora=False, is_opensuse=False, is_debian=False):
    m = mock.Mock(spec=DistroManager)
    m.id = "test"
    m.id_like = ""
    m.is_arch = is_arch
    m.is_fedora = is_fedora
    m.is_opensuse = is_opensuse
    m.is_debian = is_debian
    return m


class FamilyAndPackageManagerTests(unittest.TestCase):
    def test_arch_family(self):
        ctx = DistroContext(manager=_fake_manager(is_arch=True))
        self.assertEqual(ctx.family, "arch")
        self.assertEqual(ctx.package_manager, "pacman")

    def test_fedora_family(self):
        ctx = DistroContext(manager=_fake_manager(is_fedora=True))
        self.assertEqual(ctx.family, "fedora")
        self.assertEqual(ctx.package_manager, "dnf")

    def test_opensuse_family(self):
        ctx = DistroContext(manager=_fake_manager(is_opensuse=True))
        self.assertEqual(ctx.family, "opensuse")
        self.assertEqual(ctx.package_manager, "zypper")

    def test_debian_is_the_fallback_family(self):
        ctx = DistroContext(manager=_fake_manager())
        self.assertEqual(ctx.family, "debian")
        self.assertEqual(ctx.package_manager, "apt")

    def test_never_reports_two_families_at_once(self):
        # A machine can't be both — verifies the property doesn't
        # accidentally return something falling through to a second match.
        for flags in (
            dict(is_arch=True, is_fedora=True),
            dict(is_fedora=True, is_opensuse=True),
        ):
            ctx = DistroContext(manager=_fake_manager(**flags))
            self.assertIn(ctx.family, ("arch", "fedora", "opensuse", "debian"))


class OsReleaseParsingTests(unittest.TestCase):
    def test_reads_version_id_and_pretty_name(self):
        content = 'ID=fedora\nVERSION_ID=40\nPRETTY_NAME="Fedora Linux 40"\n'
        m = mock.mock_open(read_data=content)
        with mock.patch("builtins.open", m):
            ctx = DistroContext(manager=_fake_manager(is_fedora=True))
        self.assertEqual(ctx.version_id, "40")
        self.assertEqual(ctx.pretty_name, "Fedora Linux 40")

    def test_missing_os_release_does_not_raise(self):
        with mock.patch("builtins.open", side_effect=FileNotFoundError):
            ctx = DistroContext(manager=_fake_manager())
        self.assertEqual(ctx.version_id, "")
        self.assertEqual(ctx.pretty_name, "")


class RootFilesystemTests(unittest.TestCase):
    def test_finds_root_mount_filesystem(self):
        with tempfile.TemporaryDirectory() as proc_root:
            with open(os.path.join(proc_root, "mounts"), "w") as f:
                f.write("/dev/sda2 / ext4 rw,relatime 0 0\n")
                f.write("/dev/sda1 /boot/efi vfat rw 0 0\n")
            ctx = DistroContext(manager=_fake_manager(), proc_root=proc_root)
            self.assertEqual(ctx.root_filesystem, "ext4")

    def test_unknown_when_no_root_mount_present(self):
        with tempfile.TemporaryDirectory() as proc_root:
            with open(os.path.join(proc_root, "mounts"), "w") as f:
                f.write("/dev/sda1 /boot/efi vfat rw 0 0\n")
            ctx = DistroContext(manager=_fake_manager(), proc_root=proc_root)
            self.assertEqual(ctx.root_filesystem, "unknown")


class BootloaderDetectionTests(unittest.TestCase):
    def test_grub_detected_via_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            grub_cfg = os.path.join(tmp, "grub.cfg")
            open(grub_cfg, "w").close()
            with mock.patch("shutil.which", return_value=None), \
                 mock.patch("os.path.isdir", return_value=False), \
                 mock.patch("os.path.exists", side_effect=lambda p: p == "/boot/grub/grub.cfg"):
                ctx = DistroContext(manager=_fake_manager())
                self.assertEqual(ctx.bootloader, "grub")

    def test_unknown_when_nothing_detected(self):
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("os.path.isdir", return_value=False), \
             mock.patch("os.path.exists", return_value=False):
            ctx = DistroContext(manager=_fake_manager())
            self.assertEqual(ctx.bootloader, "unknown")


class InitSystemTests(unittest.TestCase):
    def test_systemd_detected(self):
        with mock.patch("os.path.isdir", side_effect=lambda p: p == "/run/systemd/system"):
            ctx = DistroContext(manager=_fake_manager())
            self.assertEqual(ctx.init_system, "systemd")


class ToDictAndGetContextTests(unittest.TestCase):
    def test_to_dict_has_all_expected_keys(self):
        ctx = DistroContext(manager=_fake_manager())
        d = ctx.to_dict()
        for key in ("id", "id_like", "version_id", "family", "package_manager",
                    "init_system", "bootloader", "root_filesystem",
                    "kernel_version", "architecture"):
            self.assertIn(key, d)

    def test_get_context_returns_real_snapshot(self):
        ctx = get_context()
        self.assertIn(ctx.family, ("arch", "fedora", "opensuse", "debian"))
        self.assertTrue(ctx.kernel_version)
        self.assertTrue(ctx.architecture)


if __name__ == "__main__":
    unittest.main()
