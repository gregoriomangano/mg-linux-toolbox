"""
Tests for core.software_repo.distro_profile — universal distro/family
detection for the "Software e repository" page. Covers the exact
examples called out in the spec (Pop!_OS, Peppermint, Fedora Atomic,
Arch derivatives, openSUSE transactional) plus the "don't trust ID
alone, don't invent a confident answer when signals disagree" rules.
"""
import os
import tempfile
import unittest

from core.software_repo.distro_profile import (
    detect_distro_profile, FAMILY_DEBIAN, FAMILY_FEDORA, FAMILY_ARCH, FAMILY_OPENSUSE,
    FAMILY_UNKNOWN, SYSTEM_TRADITIONAL, SYSTEM_IMMUTABLE, SYSTEM_TRANSACTIONAL,
)


def _write_os_release(tmp_dir: str, content: str) -> str:
    path = os.path.join(tmp_dir, "os-release")
    with open(path, "w") as f:
        f.write(content)
    return path


def _all_tools_present(present: set):
    return lambda name: (f"/usr/bin/{name}" if name in present else None)


class DistroProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_root = os.path.join(self._tmp.name, "run")
        os.makedirs(self.run_root, exist_ok=True)

    def _detect(self, os_release_text: str, tools: set):
        path = _write_os_release(self._tmp.name, os_release_text)
        return detect_distro_profile(os_release_path=path, run_root=self.run_root,
                                      which=_all_tools_present(tools))

    def test_pop_os_is_debian_family_via_id_like(self):
        profile = self._detect(
            'ID=pop\nID_LIKE="ubuntu debian"\nNAME="Pop!_OS"\nVERSION_ID="24.04"\n'
            'VERSION_CODENAME=noble\nPRETTY_NAME="Pop!_OS 24.04 LTS"\n',
            {"apt", "apt-get", "dpkg"},
        )
        self.assertEqual(profile.family, FAMILY_DEBIAN)
        self.assertEqual(profile.package_manager, "apt")
        self.assertEqual(profile.system_type, SYSTEM_TRADITIONAL)
        self.assertTrue(profile.confident)

    def test_peppermint_uses_id_like_for_family(self):
        profile = self._detect(
            'ID=peppermint\nID_LIKE="ubuntu debian"\nNAME=Peppermint\nVERSION_ID="2024-04"\n',
            {"apt", "apt-get", "dpkg"},
        )
        self.assertEqual(profile.family, FAMILY_DEBIAN)
        self.assertTrue(profile.confident)

    def test_linux_mint_is_debian_family(self):
        profile = self._detect('ID=linuxmint\nID_LIKE="ubuntu debian"\n', {"apt", "dpkg"})
        self.assertEqual(profile.family, FAMILY_DEBIAN)

    def test_fedora_workstation_is_traditional(self):
        profile = self._detect('ID=fedora\nVARIANT_ID=workstation\nVERSION_ID=40\n', {"dnf"})
        self.assertEqual(profile.family, FAMILY_FEDORA)
        self.assertEqual(profile.package_manager, "dnf")
        self.assertEqual(profile.system_type, SYSTEM_TRADITIONAL)

    def test_fedora_silverblue_is_immutable(self):
        profile = self._detect('ID=fedora\nVARIANT_ID=silverblue\nVERSION_ID=40\n', {"rpm-ostree"})
        self.assertEqual(profile.family, FAMILY_FEDORA)
        self.assertEqual(profile.system_type, SYSTEM_IMMUTABLE)

    def test_fedora_kinoite_is_immutable_via_ostree_booted_marker(self):
        open(os.path.join(self.run_root, "ostree-booted"), "w").close()
        profile = self._detect('ID=fedora\nVARIANT_ID=kinoite\nVERSION_ID=40\n', {"rpm-ostree"})
        self.assertEqual(profile.system_type, SYSTEM_IMMUTABLE)

    def test_arch_is_arch_family(self):
        profile = self._detect('ID=arch\n', {"pacman"})
        self.assertEqual(profile.family, FAMILY_ARCH)
        self.assertEqual(profile.package_manager, "pacman")

    def test_manjaro_is_arch_family(self):
        profile = self._detect('ID=manjaro\nID_LIKE=arch\n', {"pacman"})
        self.assertEqual(profile.family, FAMILY_ARCH)

    def test_endeavouros_is_arch_family(self):
        profile = self._detect('ID=endeavouros\nID_LIKE=arch\n', {"pacman"})
        self.assertEqual(profile.family, FAMILY_ARCH)

    def test_cachyos_is_arch_family(self):
        profile = self._detect('ID=cachyos\nID_LIKE="arch"\n', {"pacman"})
        self.assertEqual(profile.family, FAMILY_ARCH)

    def test_opensuse_leap_is_traditional(self):
        profile = self._detect('ID=opensuse-leap\nVERSION_ID="15.6"\n', {"zypper"})
        self.assertEqual(profile.family, FAMILY_OPENSUSE)
        self.assertEqual(profile.system_type, SYSTEM_TRADITIONAL)

    def test_opensuse_tumbleweed_is_traditional(self):
        profile = self._detect('ID=opensuse-tumbleweed\n', {"zypper"})
        self.assertEqual(profile.family, FAMILY_OPENSUSE)
        self.assertEqual(profile.system_type, SYSTEM_TRADITIONAL)

    def test_opensuse_microos_is_transactional(self):
        profile = self._detect('ID=opensuse-microos\n', {"zypper", "transactional-update"})
        self.assertEqual(profile.family, FAMILY_OPENSUSE)
        self.assertEqual(profile.system_type, SYSTEM_TRANSACTIONAL)

    def test_opensuse_aeon_is_transactional(self):
        profile = self._detect('ID=opensuse-aeon\n', {"transactional-update"})
        self.assertEqual(profile.system_type, SYSTEM_TRANSACTIONAL)

    def test_debian_plain(self):
        profile = self._detect('ID=debian\nVERSION_CODENAME=bookworm\n', {"apt", "dpkg"})
        self.assertEqual(profile.family, FAMILY_DEBIAN)

    def test_unresolved_family_is_not_confident(self):
        profile = self._detect('ID=someunknownthing\n', set())
        self.assertEqual(profile.family, FAMILY_UNKNOWN)
        self.assertFalse(profile.confident)
        self.assertEqual(profile.package_manager, "unknown")

    def test_contradictory_signals_are_not_confident(self):
        # os-release claims Debian, but none of apt/apt-get/dpkg exist —
        # never invent a confident "apt" answer from ID alone.
        profile = self._detect('ID=debian\n', {"pacman"})
        self.assertFalse(profile.confident)
        self.assertEqual(profile.package_manager, "unknown")
        self.assertEqual(profile.uncertainty_reason, "tools_missing_for_family")

    def test_missing_os_release_is_not_confident(self):
        profile = detect_distro_profile(
            os_release_path=os.path.join(self._tmp.name, "does-not-exist"),
            run_root=self.run_root, which=_all_tools_present(set()))
        self.assertFalse(profile.confident)

    def test_reads_full_field_set(self):
        profile = self._detect(
            'ID=ubuntu\nID_LIKE=debian\nNAME=Ubuntu\nPRETTY_NAME="Ubuntu 24.04.1 LTS"\n'
            'VERSION_ID="24.04"\nVERSION_CODENAME=noble\nUBUNTU_CODENAME=noble\n'
            'VARIANT="Desktop"\nVARIANT_ID=desktop\n',
            {"apt", "dpkg"},
        )
        self.assertEqual(profile.pretty_name, "Ubuntu 24.04.1 LTS")
        self.assertEqual(profile.version_codename, "noble")
        self.assertEqual(profile.ubuntu_codename, "noble")
        self.assertEqual(profile.variant, "Desktop")
        self.assertEqual(profile.variant_id, "desktop")

    def test_tools_present_dict_reflects_injected_which(self):
        profile = self._detect('ID=fedora\n', {"dnf", "flatpak"})
        self.assertTrue(profile.tools_present["dnf"])
        self.assertTrue(profile.tools_present["flatpak"])
        self.assertFalse(profile.tools_present["pacman"])

    def test_to_dict_is_json_shaped(self):
        profile = self._detect('ID=debian\n', {"apt"})
        d = profile.to_dict()
        self.assertIn("family", d)
        self.assertIn("tools_present", d)
        self.assertIsInstance(d["id_like"], list)


if __name__ == "__main__":
    unittest.main()
