"""
Tests for core.repo_check — the "is this package actually available in
the repos configured on this system" gate that must run before ever
showing an Install button. Uses the real package manager on whichever
distro runs the suite for the "real package" / "fake package" cases
(this machine is Debian-family), and monkeypatches run_command_full for
the other three families, since we don't have Arch/Fedora/openSUSE
machines to verify against — see the project notes on not claiming to
have tested distros we don't have.
"""
import contextlib
import unittest
from unittest import mock

from core import repo_check
from core.executor import CommandResult


@unittest.skipUnless(repo_check.distro.is_debian,
                      "exercises the real apt-cache path — only meaningful on a Debian-family host "
                      "(found failing for the wrong reason in an Arch distrobox: is_available({'debian': ...}) "
                      "correctly took the Arch branch instead, where 'debian'-only dict has no arch/default key)")
class DebianFamilyLiveTests(unittest.TestCase):
    """These actually run apt-cache on whatever machine runs the suite."""

    def test_real_package_is_available(self):
        self.assertTrue(repo_check.is_available({"debian": "coreutils"}))

    def test_nonexistent_package_is_not_available(self):
        self.assertFalse(repo_check.is_available({"debian": "totally-fake-pkg-xyz-123"}))

    def test_missing_key_fails_open(self):
        self.assertTrue(repo_check.is_available({}))


class OtherFamiliesMockedTests(unittest.TestCase):
    """Not verified against real Arch/Fedora/openSUSE machines — resolver
    logic only, exercised via a mocked run_command_full."""

    def _distro_flags(self, stack, **flags):
        for name in ("is_arch", "is_fedora", "is_opensuse", "is_debian"):
            stack.enter_context(mock.patch.object(
                type(repo_check.distro), name,
                new_callable=mock.PropertyMock, return_value=flags.get(name, False)))

    def test_arch_package_found(self):
        with contextlib.ExitStack() as stack:
            self._distro_flags(stack, is_arch=True)
            stack.enter_context(mock.patch.object(
                repo_check, "run_command_full",
                return_value=CommandResult(["pacman"], True, 0, "some info", "", 0.1)))
            self.assertTrue(repo_check.is_available({"arch": "some-pkg"}))

    def test_arch_package_not_found(self):
        with contextlib.ExitStack() as stack:
            self._distro_flags(stack, is_arch=True)
            stack.enter_context(mock.patch.object(
                repo_check, "run_command_full",
                return_value=CommandResult(["pacman"], False, 1, "", "error: package 'x' was not found", 0.1)))
            self.assertFalse(repo_check.is_available({"arch": "some-pkg"}))

    def test_fedora_package_not_found(self):
        with contextlib.ExitStack() as stack:
            self._distro_flags(stack, is_fedora=True)
            stack.enter_context(mock.patch.object(
                repo_check, "run_command_full",
                return_value=CommandResult(["dnf"], False, 1, "", "No matching packages", 0.1)))
            self.assertFalse(repo_check.is_available({"fedora": "some-pkg"}))

    def test_opensuse_package_found(self):
        with contextlib.ExitStack() as stack:
            self._distro_flags(stack, is_opensuse=True)
            stack.enter_context(mock.patch.object(
                repo_check, "run_command_full",
                return_value=CommandResult(["zypper"], True, 0, "Information for package some-pkg", "", 0.1)))
            self.assertTrue(repo_check.is_available({"opensuse": "some-pkg"}))

    def test_tool_missing_fails_open(self):
        with contextlib.ExitStack() as stack:
            self._distro_flags(stack, is_fedora=True)
            stack.enter_context(mock.patch.object(
                repo_check, "run_command_full",
                return_value=CommandResult(["dnf"], False, None, "", "", 0.0, error="not found")))
            self.assertTrue(repo_check.is_available({"fedora": "some-pkg"}))


if __name__ == "__main__":
    unittest.main()
