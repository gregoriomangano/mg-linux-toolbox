"""Focused tests for startup confirmation and external rollback supervision."""
import json
import os
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.updater import launch_helper, startup


class _LiveProcess:
    returncode = None

    def poll(self):
        return None

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class _ExitedProcess:
    returncode = 1

    def poll(self):
        return self.returncode


class LaunchHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backup_dir = os.path.join(self.tmp.name, "backup")
        os.makedirs(self.backup_dir)
        self.target = os.path.join(self.tmp.name, "MG-Linux-Toolbox.AppImage")
        self.pending = os.path.join(self.backup_dir, "pending-previous-0.9.0-beta.4.AppImage")
        self.confirmation = os.path.join(self.tmp.name, "confirmation.json")
        self.log = os.path.join(self.tmp.name, "update.log")
        self._write(self.target, b"new")
        self._write(self.pending, b"old")

    def _write(self, path, content):
        with open(path, "wb") as stream:
            stream.write(content)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    def _args(self, timeout=0.1, stabilize=0):
        return SimpleNamespace(target=self.target, pending=self.pending,
                               backup_dir=self.backup_dir, version="0.9.0-beta.5",
                               previous_version="0.9.0-beta.4",
                               confirmation=self.confirmation, token="token",
                               log=self.log, timeout=timeout, stabilize=stabilize)

    def test_confirmed_startup_finalizes_pending_backup(self):
        with open(self.confirmation, "w") as stream:
            json.dump({"token": "token", "version": "0.9.0-beta.5"}, stream)
        process = _LiveProcess()
        with mock.patch.object(launch_helper.subprocess, "Popen", return_value=process):
            result = launch_helper.supervise(self._args())
        self.assertEqual(result, 0)
        self.assertFalse(os.path.exists(self.pending))
        self.assertTrue(os.path.isfile(os.path.join(self.backup_dir, "previous-0.9.0-beta.4.AppImage")))

    def test_immediate_exit_rolls_back_and_keeps_user_message_path(self):
        with mock.patch.object(launch_helper.subprocess, "Popen", return_value=_ExitedProcess()), \
             mock.patch.object(launch_helper, "_launch_previous", return_value=True), \
             mock.patch.object(launch_helper, "_notify") as notify:
            result = launch_helper.supervise(self._args(timeout=0.1))
        self.assertEqual(result, 1)
        with open(self.target, "rb") as stream:
            self.assertEqual(stream.read(), b"old")
        notify.assert_called_once()
        self.assertTrue(os.path.exists(self.pending))

    def test_timeout_rolls_back_without_process_name_heuristics(self):
        with mock.patch.object(launch_helper.subprocess, "Popen", return_value=_LiveProcess()), \
             mock.patch.object(launch_helper, "_launch_previous", return_value=True), \
             mock.patch.object(launch_helper, "_notify"):
            result = launch_helper.supervise(self._args(timeout=0.01))
        self.assertEqual(result, 1)
        with open(self.target, "rb") as stream:
            self.assertEqual(stream.read(), b"old")
        self.assertTrue(os.path.exists(self.pending))

    def test_failed_rollback_retains_pending_backup(self):
        real_replace = launch_helper.os.replace

        def fail_rollback(source, target):
            if source.endswith(".rollback-new") and target == self.target:
                raise OSError("simulated rollback failure")
            return real_replace(source, target)

        with mock.patch.object(launch_helper.subprocess, "Popen", return_value=_ExitedProcess()), \
             mock.patch.object(launch_helper.os, "replace", side_effect=fail_rollback), \
             mock.patch.object(launch_helper, "_launch_previous"), \
             mock.patch.object(launch_helper, "_notify"):
            result = launch_helper.supervise(self._args(timeout=0.1))
        self.assertEqual(result, 1)
        with open(self.target, "rb") as stream:
            self.assertEqual(stream.read(), b"new")
        self.assertTrue(os.path.exists(self.pending))

    def test_startup_confirmation_requires_expected_version_and_token(self):
        env = {
            "MG_TOOLBOX_UPDATE_CONFIRMATION": self.confirmation,
            "MG_TOOLBOX_UPDATE_CONFIRMATION_TOKEN": "token",
            "MG_TOOLBOX_UPDATE_EXPECTED_VERSION": "0.9.0-beta.5",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertTrue(startup.write_update_confirmation("0.9.0-beta.5"))
        with open(self.confirmation) as stream:
            data = json.load(stream)
        self.assertEqual(data["token"], "token")
        self.assertEqual(data["version"], "0.9.0-beta.5")


if __name__ == "__main__":
    unittest.main()
