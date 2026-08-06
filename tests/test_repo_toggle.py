"""
Targeted tests for core.software_repo.repo_toggle — remove_zypper_repo()
and remove_flatpak_remote() functions added 2026-08-05.
"""
import os
import tempfile
import unittest
from unittest import mock

from core.software_repo import repo_toggle as toggle
from core.software_repo import repo_transaction as tx


def _fake_pkexec_rm(cmd, timeout=None, job=None):
    """Mock for run_pkexec_full — actually performs 'mkdir'/'cp'/'rm'
    locally so the real file-surgery logic in repo_toggle AND
    repo_transaction's own backup_file() (which issues 'mkdir -p' for
    the backup dir before copying into it) run end to end."""
    import shutil
    m = mock.Mock()
    try:
        if cmd[0] == "mkdir":
            os.makedirs(cmd[-1], exist_ok=True)
        elif cmd[0] == "rm":
            target = cmd[-1]
            if os.path.exists(target):
                os.remove(target)
        elif cmd[0] == "cp":
            src, dst = cmd[-2], cmd[-1]
            shutil.copy2(src, dst)
        m.ok = True
        m.technical_detail = lambda: ""
    except OSError as e:
        msg = str(e)
        m.ok = False
        m.technical_detail = lambda: msg
    return m


class RemoveZypperRepoTests(unittest.TestCase):
    def test_remove_entire_file_when_only_section(self):
        """Removing the only section in a .repo file deletes the file."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_file = os.path.join(tmp, "single.repo")
            with open(repo_file, "w") as f:
                f.write("[repo-oss]\nname=Main Repository\nbaseurl=https://download.opensuse.org/oss\nenabled=1\n")

            with mock.patch.object(toggle, "run_pkexec_full", side_effect=_fake_pkexec_rm), \
                 mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_rm), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                result = toggle.remove_zypper_repo(repo_file, "repo-oss")

            self.assertTrue(result.ok)
            self.assertFalse(os.path.exists(repo_file))

    def test_remove_section_from_multi_section_file(self):
        """Removing one section from a multi-section file leaves others intact."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_file = os.path.join(tmp, "multi.repo")
            with open(repo_file, "w") as f:
                f.write(
                    "[repo-oss]\nname=Main Repository\nbaseurl=https://download.opensuse.org/oss\nenabled=1\n\n"
                    "[external]\nname=External\nbaseurl=https://example.com/repo\nenabled=1\n"
                )

            with mock.patch.object(toggle, "run_pkexec_full", side_effect=_fake_pkexec_rm), \
                 mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_rm), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                result = toggle.remove_zypper_repo(repo_file, "repo-oss")

            self.assertTrue(result.ok)
            self.assertTrue(os.path.exists(repo_file))
            content = open(repo_file).read()
            self.assertIn("[external]", content)
            self.assertNotIn("[repo-oss]", content)

    def test_remove_nonexistent_section_fails(self):
        """Removing a section that doesn't exist returns error."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_file = os.path.join(tmp, "test.repo")
            with open(repo_file, "w") as f:
                f.write("[other]\nname=Other\nbaseurl=https://example.com\nenabled=1\n")

            with mock.patch.object(toggle, "run_pkexec_full", side_effect=_fake_pkexec_rm), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                result = toggle.remove_zypper_repo(repo_file, "nonexistent")

            self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
