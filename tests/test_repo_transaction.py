"""
Tests for core.software_repo.repo_transaction / repo_toggle — the
backup -> apply -> verify -> rollback engine built for the RC
validation block. Every privileged step (backup_file/restore_file/
apply) is mocked to operate on real files in a tmp directory instead
of actually calling pkexec, so these stay fully offline and never
touch the real system, while still exercising the real file-copy /
text-surgery logic end to end.

Real-privilege-escalation guard (2026-08-04): a prior version of this
file mocked repo_transaction.run_pkexec_full but NOT
repo_toggle.run_pkexec_full — since repo_toggle.py imports its own
`run_pkexec_full` name into its own module namespace, patching the
first never touched the second, and repo_toggle's apply_fn (`cp
tmp_path repo_file`) silently reached a REAL pkexec, observed hanging
on a real authentication prompt during a live run. setUpModule/
tearDownModule below patch subprocess.Popen itself (the one place
every pkexec/sudo/su call in this codebase ultimately goes through) so
ANY test in this module that lets a forbidden binary through fails
immediately and loudly, instead of quietly blocking on — or
succeeding via — a real privileged prompt.
"""
import hashlib
import os
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock

from core.software_repo import repo_transaction as tx
from core.software_repo import repo_toggle as toggle

_FORBIDDEN_BINARIES = ("pkexec", "sudo", "su", "doas")
_real_popen = subprocess.Popen
_popen_patcher = None


def _guarded_popen(cmd, *args, **kwargs):
    if isinstance(cmd, (list, tuple)):
        prog = cmd[0] if cmd else ""
    else:
        parts = shlex.split(str(cmd))
        prog = parts[0] if parts else ""
    if os.path.basename(str(prog)) in _FORBIDDEN_BINARIES:
        raise AssertionError(
            f"Test attempted to spawn a REAL privileged process: {cmd!r}. "
            "Every pkexec/sudo/su/doas call in this module must be mocked "
            "(patch both repo_transaction.run_pkexec_full AND "
            "repo_toggle.run_pkexec_full — they are separate names)."
        )
    return _real_popen(cmd, *args, **kwargs)


def setUpModule():
    global _popen_patcher
    _popen_patcher = mock.patch("subprocess.Popen", side_effect=_guarded_popen)
    _popen_patcher.start()


def tearDownModule():
    _popen_patcher.stop()


def _fake_pkexec_cp(cmd, timeout=None, job=None):
    """Stands in for run_pkexec_full(["cp", "-a"/"", src, dst]) and
    ["mkdir", "-p", dir] — actually performs the operation locally so
    the rest of the transaction logic runs against real files."""
    import shutil
    m = mock.Mock()
    try:
        if cmd[0] == "mkdir":
            os.makedirs(cmd[2], exist_ok=True)
        elif cmd[0] == "cp":
            src, dst = cmd[-2], cmd[-1]
            shutil.copy2(src, dst)
        elif cmd[0] == "rm":
            target = cmd[-1]
            if os.path.exists(target):
                os.remove(target)
        m.ok = True
        m.technical_detail = lambda: ""
    except OSError as e:
        m.ok = False
        m.technical_detail = lambda: str(e)
    return m


class BackupFileTests(unittest.TestCase):
    def test_backup_creates_a_byte_identical_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "source.repo")
            with open(src, "w") as f:
                f.write("[repo]\nenabled=1\n")
            with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                backup_path = tx.backup_file(src)
            self.assertIsNotNone(backup_path)
            self.assertTrue(os.path.isfile(backup_path))
            src_hash = hashlib.sha256(open(src, "rb").read()).hexdigest()
            backup_hash = hashlib.sha256(open(backup_path, "rb").read()).hexdigest()
            self.assertEqual(src_hash, backup_hash)

    def test_backup_of_nonexistent_file_returns_none_not_an_error(self):
        backup_path = tx.backup_file("/definitely/does/not/exist/anywhere")
        self.assertIsNone(backup_path)

    def test_backup_of_existing_file_raises_when_privileged_copy_fails(self):
        """A file that DOES exist but whose privileged `cp` fails must
        never be silently treated the same as 'nothing to back up'."""
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "source.repo")
            with open(src, "w") as f:
                f.write("[repo]\nenabled=1\n")

            def failing_cp(cmd, timeout=None, job=None):
                m = mock.Mock()
                if cmd[0] == "cp":
                    m.ok = False
                    m.technical_detail = lambda: "simulated backup failure"
                    return m
                return _fake_pkexec_cp(cmd, timeout, job)

            with mock.patch.object(tx, "run_pkexec_full", side_effect=failing_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                with self.assertRaises(tx.BackupFailedError):
                    tx.backup_file(src)

    def test_backup_path_has_restrictive_directory_never_world_writable_by_construction(self):
        # The backup dir is created via `mkdir -p` under pkexec (root-
        # owned by default umask), never written to by the unprivileged
        # caller directly — this just documents/locks the path shape.
        self.assertTrue(tx._BACKUP_DIR.startswith("/var/lib/"))


class RunTransactionTests(unittest.TestCase):
    def test_successful_transaction_reaches_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "a.repo")
            open(f, "w").write("original")
            with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                result = tx.run_transaction([f], apply_fn=lambda: True, verify_fn=lambda: True)
        self.assertEqual(result.state, tx.VERIFIED)
        self.assertTrue(result.ok)
        self.assertTrue(result.rollback_available)

    def test_apply_failure_without_a_change_has_nothing_to_roll_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "a.repo")
            open(f, "w").write("original-content")
            with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                result = tx.run_transaction([f], apply_fn=lambda: False, verify_fn=lambda: True)
            self.assertEqual(result.state, tx.NOTHING_TO_ROLL_BACK)
            self.assertFalse(result.ok)
            self.assertEqual(open(f).read(), "original-content")

    def test_verification_failure_triggers_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "a.repo")
            open(f, "w").write("original-content")

            def apply_and_corrupt():
                open(f, "w").write("half-broken-write")
                return True

            with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                result = tx.run_transaction([f], apply_fn=apply_and_corrupt, verify_fn=lambda: False)
            self.assertEqual(result.state, tx.ROLLED_BACK)
            self.assertFalse(result.ok)
            self.assertEqual(open(f).read(), "original-content", "file was not restored after a failed verification")

    def test_permission_only_change_is_materially_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "a.repo")
            open(f, "w").write("original-content")
            os.chmod(f, 0o644)

            def apply_and_change_mode():
                os.chmod(f, 0o600)
                return True

            with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                result = tx.run_transaction(
                    [f], apply_fn=apply_and_change_mode,
                    verify_fn=lambda: False)
            self.assertEqual(result.state, tx.ROLLED_BACK)
            self.assertEqual(os.stat(f).st_mode & 0o777, 0o644)
            self.assertEqual(open(f).read(), "original-content")

    def test_rollback_failure_is_reported_distinctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "a.repo")
            open(f, "w").write("original")

            def failing_restore(cmd, timeout=None, job=None):
                m = mock.Mock()
                if cmd[0] == "cp" and cmd[-1] == f:
                    m.ok = False
                    m.technical_detail = lambda: "simulated restore failure"
                    return m
                return _fake_pkexec_cp(cmd, timeout, job)

            with mock.patch.object(tx, "run_pkexec_full", side_effect=failing_restore), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                def apply_and_corrupt():
                    open(f, "w").write("changed")
                    return True

                result = tx.run_transaction([f], apply_fn=apply_and_corrupt, verify_fn=lambda: False)
            self.assertEqual(result.state, tx.ROLLBACK_FAILED)
            self.assertFalse(result.ok)
            self.assertEqual(open(f).read(), "changed")

    def test_successful_restore_command_without_content_change_is_rollback_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "a.repo")
            open(f, "w").write("original")

            def no_op_restore(cmd, timeout=None, job=None):
                if cmd[0] == "cp" and cmd[-1] == f:
                    m = mock.Mock(ok=True)
                    m.technical_detail = lambda: ""
                    return m
                return _fake_pkexec_cp(cmd, timeout, job)

            def apply_and_corrupt():
                open(f, "w").write("changed")
                return True

            with mock.patch.object(tx, "run_pkexec_full", side_effect=no_op_restore), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                result = tx.run_transaction([f], apply_fn=apply_and_corrupt, verify_fn=lambda: False)
            self.assertEqual(result.state, tx.ROLLBACK_FAILED)
            self.assertEqual(open(f).read(), "changed")

    def test_unexpected_exception_in_apply_is_treated_as_failure_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "a.repo")
            open(f, "w").write("original")

            def boom():
                raise RuntimeError("boom")

            with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                result = tx.run_transaction([f], apply_fn=boom, verify_fn=lambda: True)
        self.assertFalse(result.ok)
        self.assertIn("boom", result.technical_detail)

    def test_created_file_is_deleted_on_rollback_not_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            new_file = os.path.join(tmp, "brand-new.repo")

            def apply_fn():
                open(new_file, "w").write("new")
                return True

            with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                result = tx.run_transaction([], apply_fn=apply_fn, verify_fn=lambda: False,
                                             created_files=[new_file])
        self.assertEqual(result.state, tx.ROLLED_BACK)
        self.assertFalse(os.path.exists(new_file))

    def test_successful_remove_command_that_leaves_file_is_rollback_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            new_file = os.path.join(tmp, "brand-new.repo")

            def apply_fn():
                open(new_file, "w").write("new")
                return True

            with mock.patch.object(tx, "remove_file", return_value=True):
                result = tx.run_transaction(
                    [], apply_fn=apply_fn, verify_fn=lambda: False,
                    created_files=[new_file])
            self.assertEqual(result.state, tx.ROLLBACK_FAILED)
            self.assertTrue(os.path.exists(new_file))

    def test_declared_created_file_still_absent_is_nothing_to_roll_back(self):
        """A failed apply before file creation must not turn `rm -f`'s
        no-op success into a false ROLLED_BACK state."""
        with tempfile.TemporaryDirectory() as tmp:
            never_created = os.path.join(tmp, "never-created.repo")
            with mock.patch.object(tx, "remove_file") as remove_mock:
                result = tx.run_transaction(
                    [], apply_fn=lambda: False, verify_fn=lambda: True,
                    created_files=[never_created])
        self.assertEqual(result.state, tx.NOTHING_TO_ROLL_BACK)
        self.assertFalse(result.ok)
        remove_mock.assert_not_called()

    def test_nonexistent_file_is_not_misreported_as_backup_failure(self):
        """A genuinely new path remains valid: no backup is required,
        and the apply step must still run."""
        with tempfile.TemporaryDirectory() as tmp:
            not_yet_existing = os.path.join(tmp, "future.repo")
            apply_mock = mock.Mock(return_value=True)
            result = tx.run_transaction(
                [not_yet_existing], apply_fn=apply_mock,
                verify_fn=lambda: True)
        self.assertEqual(result.state, tx.VERIFIED)
        self.assertTrue(result.ok)
        apply_mock.assert_called_once_with()

    def test_backup_failure_aborts_before_apply_is_ever_called(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "a.repo")
            open(f, "w").write("original-content")
            apply_calls = []

            def failing_backup(cmd, timeout=None, job=None):
                m = mock.Mock()
                if cmd[0] == "cp":
                    m.ok = False
                    m.technical_detail = lambda: "simulated backup failure"
                    return m
                return _fake_pkexec_cp(cmd, timeout, job)

            def apply_fn():
                apply_calls.append(1)
                return True

            with mock.patch.object(tx, "run_pkexec_full", side_effect=failing_backup), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")):
                result = tx.run_transaction([f], apply_fn=apply_fn, verify_fn=lambda: True)
            self.assertEqual(result.state, tx.BACKUP_FAILED)
            self.assertFalse(result.ok)
            self.assertEqual(apply_calls, [], "apply_fn must never run when a mandatory backup failed")
            self.assertEqual(open(f).read(), "original-content", "the untouched file must not have been modified")

    def test_rollback_with_nothing_backed_up_or_created_is_never_reported_as_rolled_back(self):
        """If files_involved is empty and apply_fn creates nothing,
        there is nothing to roll back to — the state must say so
        honestly instead of claiming ROLLED_BACK when zero files were
        actually restored."""
        with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp):
            result = tx.run_transaction([], apply_fn=lambda: False, verify_fn=lambda: True)
        self.assertEqual(result.state, tx.NOTHING_TO_ROLL_BACK)
        self.assertFalse(result.ok)


class DnfZypperToggleTests(unittest.TestCase):
    def _write_repo_file(self, tmp, content):
        path = os.path.join(tmp, "test.repo")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_enable_sets_enabled_1_leaving_rest_of_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_repo_file(tmp, "[myrepo]\nname=My Repo\nbaseurl=file:///tmp/x\nenabled=0\ngpgcheck=1\n")
            with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(toggle, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")), \
                 mock.patch.object(toggle, "run_command_full", return_value=mock.Mock(ok=True)):
                result = toggle.set_dnf_repo_enabled(path, "myrepo", True)
            self.assertTrue(result.ok)
            text = open(path).read()
            self.assertIn("enabled=1", text)
            self.assertIn("name=My Repo", text)
            self.assertIn("gpgcheck=1", text)

    def test_disable_then_re_enable_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_repo_file(tmp, "[myrepo]\nname=My Repo\nenabled=1\n")
            with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(toggle, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")), \
                 mock.patch.object(toggle, "run_command_full", return_value=mock.Mock(ok=True)):
                r1 = toggle.set_dnf_repo_enabled(path, "myrepo", False)
                self.assertTrue(r1.ok)
                self.assertIn("enabled=0", open(path).read())
                r2 = toggle.set_dnf_repo_enabled(path, "myrepo", True)
                self.assertTrue(r2.ok)
                self.assertIn("enabled=1", open(path).read())

    def test_zypper_toggle_uses_zypper_verify_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_repo_file(tmp, "[myrepo]\nname=My Repo\nenabled=1\n")
            with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(toggle, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")), \
                 mock.patch.object(toggle, "run_command_full", return_value=mock.Mock(ok=True)) as verify_mock:
                toggle.set_zypper_repo_enabled(path, "myrepo", False)
            verify_mock.assert_called()
            self.assertEqual(verify_mock.call_args[0][0][0], "zypper")

    def test_missing_section_is_unsupported_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_repo_file(tmp, "[other]\nenabled=1\n")
            result = toggle.set_dnf_repo_enabled(path, "myrepo", True)
        self.assertFalse(result.ok)
        self.assertEqual(result.state, tx.UNSUPPORTED)

    def test_verification_failure_rolls_back_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = "[myrepo]\nname=My Repo\nenabled=0\n"
            path = self._write_repo_file(tmp, original)
            with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(toggle, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")), \
                 mock.patch.object(toggle, "run_command_full", return_value=mock.Mock(ok=False)):
                result = toggle.set_dnf_repo_enabled(path, "myrepo", True)
            self.assertFalse(result.ok)
            self.assertEqual(open(path).read(), original)

    def test_no_password_ever_appears_in_a_technical_detail_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_repo_file(tmp, "[myrepo]\nenabled=0\n")
            with mock.patch.object(tx, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(toggle, "run_pkexec_full", side_effect=_fake_pkexec_cp), \
                 mock.patch.object(tx, "_BACKUP_DIR", os.path.join(tmp, "backups")), \
                 mock.patch.object(toggle, "run_command_full", return_value=mock.Mock(ok=True)):
                result = toggle.set_dnf_repo_enabled(path, "myrepo", True)
            self.assertNotIn("password", result.technical_detail.lower())
            self.assertNotIn("secret", result.technical_detail.lower())


class FlatpakRemoteToggleTests(unittest.TestCase):
    def test_enable_user_scope_never_uses_pkexec(self):
        from core.software_repo import flatpak_manager as fpm
        remote = fpm.FlatpakRemote(name="flathub", url="https://dl.flathub.org/repo/", enabled=True, scope="user")
        with mock.patch.object(toggle, "run_command_full", return_value=mock.Mock(ok=True, technical_detail=lambda: "")) as run_mock, \
             mock.patch.object(toggle, "run_pkexec_full") as pk_mock, \
             mock.patch("core.software_repo.flatpak_manager.list_remotes", return_value=[remote]):
            result = toggle.set_flatpak_remote_enabled("flathub", fpm.SCOPE_USER, True)
        pk_mock.assert_not_called()
        run_mock.assert_called_once()
        self.assertTrue(result.ok)

    def test_verification_mismatch_is_reported_as_failure(self):
        from core.software_repo import flatpak_manager as fpm
        # Remote still reports enabled=False after the "enable" call.
        remote = fpm.FlatpakRemote(name="flathub", url="https://dl.flathub.org/repo/", enabled=False, scope="user")
        with mock.patch.object(toggle, "run_command_full", return_value=mock.Mock(ok=True, technical_detail=lambda: "")), \
             mock.patch("core.software_repo.flatpak_manager.list_remotes", return_value=[remote]):
            result = toggle.set_flatpak_remote_enabled("flathub", fpm.SCOPE_USER, True)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
