"""
Tests for core/updater/orchestrator.py — the complete one-click update
flow. Per project policy nothing here touches the network or the real
~/.local/opt install: downloads are faked by writing local files,
"managed" paths point into temp dirs, and no AppImage is ever executed.
"""
import hashlib
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.updater import installer, orchestrator
from core.updater.models import DownloadResult, ReleaseAsset, ReleaseInfo


def _release(version="0.9.0-beta.5", arch="x86_64", with_checksum=True, content=b"new appimage"):
    name = f"MG-Linux-Toolbox-{version}-{arch}.AppImage"
    assets = [ReleaseAsset(name=name, download_url=f"https://example.invalid/{name}", size=len(content))]
    if with_checksum:
        assets.append(ReleaseAsset(name=f"{name}.sha256",
                                    download_url=f"https://example.invalid/{name}.sha256"))
    return ReleaseInfo(tag=f"v{version}", version=version, prerelease=True,
                        channel="beta", assets=assets)


class _FakeDownloads:
    """Substitutes downloader.download_asset: 'downloads' by writing the
    canned bytes for each URL to dest_path."""

    def __init__(self, content=b"new appimage", checksum_of=None, fail_appimage=False,
                 fail_checksum=False):
        self.content = content
        self.checksum = checksum_of if checksum_of is not None else hashlib.sha256(content).hexdigest()
        self.fail_appimage = fail_appimage
        self.fail_checksum = fail_checksum

    def __call__(self, url, dest_path, expected_size=0, on_progress=None, cancel_token=None,
                 timeout=30):
        if url.endswith(".sha256"):
            if self.fail_checksum:
                return DownloadResult(False, friendly_message="updater_no_network")
            data = f"{self.checksum}  {os.path.basename(dest_path)[:-7]}\n".encode()
        else:
            if self.fail_appimage:
                return DownloadResult(False, friendly_message="updater_no_network")
            data = self.content
        with open(dest_path, "wb") as f:
            f.write(data)
        if on_progress is not None:
            on_progress(len(data), len(data))
        return DownloadResult(True, path=dest_path, size=len(data))


class ManagedUpdateTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        managed_dir = os.path.join(self._tmpdir.name, "managed")
        os.makedirs(managed_dir)
        self.managed_path = os.path.join(managed_dir, "MG-Linux-Toolbox.AppImage")
        self.backup_dir = os.path.join(managed_dir, "backup")
        for target, value in (("MANAGED_DIR", managed_dir),
                              ("MANAGED_APPIMAGE_PATH", self.managed_path),
                              ("BACKUP_DIR", self.backup_dir)):
            p = mock.patch.object(installer, target, value)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(orchestrator, "VERSION_FILE",
                              os.path.join(managed_dir, ".version"))
        p.start(); self.addCleanup(p.stop)
        with open(self.managed_path, "wb") as f:
            f.write(b"old appimage")
        os.chmod(self.managed_path, 0o755)

    def _run(self, release=None, downloads=None):
        downloads = downloads or _FakeDownloads()
        with mock.patch.object(orchestrator.downloader, "download_asset", downloads):
            return orchestrator.perform_managed_update(release or _release(), "0.9.0-beta.4")

    def test_full_update_replaces_atomically_and_keeps_one_backup(self):
        result = self._run()
        self.assertTrue(result.ok)
        with open(self.managed_path, "rb") as f:
            self.assertEqual(f.read(), b"new appimage")
        self.assertTrue(os.access(self.managed_path, os.X_OK))
        backups = os.listdir(self.backup_dir)
        self.assertEqual(backups, ["previous-0.9.0-beta.4.AppImage"])
        with open(os.path.join(self.backup_dir, backups[0]), "rb") as f:
            self.assertEqual(f.read(), b"old appimage")
        with open(orchestrator.VERSION_FILE) as f:
            self.assertEqual(f.read().strip(), "0.9.0-beta.5")

    def test_only_one_previous_backup_is_ever_kept(self):
        os.makedirs(self.backup_dir, exist_ok=True)
        stale = os.path.join(self.backup_dir, "previous-0.9.0-beta.3.AppImage")
        with open(stale, "wb") as f:
            f.write(b"very old")
        result = self._run()
        self.assertTrue(result.ok)
        self.assertEqual(os.listdir(self.backup_dir), ["previous-0.9.0-beta.4.AppImage"])

    def test_checksum_mismatch_changes_nothing(self):
        downloads = _FakeDownloads(checksum_of="0" * 64)
        result = self._run(downloads=downloads)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "updater_checksum_mismatch")
        with open(self.managed_path, "rb") as f:
            self.assertEqual(f.read(), b"old appimage")
        self.assertFalse(os.path.isdir(self.backup_dir) and os.listdir(self.backup_dir))

    def test_download_failure_changes_nothing(self):
        result = self._run(downloads=_FakeDownloads(fail_appimage=True))
        self.assertFalse(result.ok)
        with open(self.managed_path, "rb") as f:
            self.assertEqual(f.read(), b"old appimage")

    def test_missing_checksum_asset_refused_before_download(self):
        result = self._run(release=_release(with_checksum=False))
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "updater_checksum_missing")

    def test_wrong_architecture_refused(self):
        release = _release(arch="aarch64")
        with mock.patch.object(installer, "current_arch", return_value="x86_64"):
            result = self._run(release=release)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "updater_asset_missing")

    def test_unsupported_machine_refused(self):
        with mock.patch.object(installer, "current_arch", return_value=""):
            result = self._run()
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "updater_unsupported_arch")

    def test_replace_failure_reports_and_backup_survives(self):
        with mock.patch.object(installer, "replace_atomically",
                               return_value=orchestrator.InstallResult(
                                   False, friendly_message="updater_replace_failed")):
            result = self._run()
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "updater_replace_failed")
        # The pre-replacement backup must still exist for manual recovery.
        self.assertTrue(os.listdir(self.backup_dir))

    def test_temp_work_dir_is_always_cleaned_up(self):
        captured = {}
        real_mkdtemp = tempfile.mkdtemp

        def spy_mkdtemp(prefix=None):
            path = real_mkdtemp(prefix=prefix)
            captured["path"] = path
            return path

        with mock.patch.object(orchestrator.tempfile, "mkdtemp", spy_mkdtemp):
            self._run()
        self.assertFalse(os.path.exists(captured["path"]))


class DownloadOnlyTests(unittest.TestCase):
    def test_saves_verified_file_into_chosen_folder(self):
        with tempfile.TemporaryDirectory() as dest:
            with mock.patch.object(orchestrator.downloader, "download_asset", _FakeDownloads()):
                result = orchestrator.download_only(_release(), dest)
            self.assertTrue(result.ok)
            saved = os.path.join(dest, "MG-Linux-Toolbox-0.9.0-beta.5-x86_64.AppImage")
            self.assertTrue(os.path.isfile(saved))
            self.assertTrue(os.access(saved, os.X_OK))

    def test_checksum_mismatch_saves_nothing(self):
        with tempfile.TemporaryDirectory() as dest:
            with mock.patch.object(orchestrator.downloader, "download_asset",
                                   _FakeDownloads(checksum_of="0" * 64)):
                result = orchestrator.download_only(_release(), dest)
            self.assertFalse(result.ok)
            self.assertEqual(os.listdir(dest), [])


class RestartTests(unittest.TestCase):
    def test_restart_refused_when_managed_appimage_missing(self):
        with mock.patch.object(installer, "MANAGED_APPIMAGE_PATH",
                               "/nonexistent/MG-Linux-Toolbox.AppImage"):
            self.assertFalse(orchestrator.restart_into_managed())

    def test_restart_launches_the_stable_path_detached(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "MG-Linux-Toolbox.AppImage")
            with open(target, "w") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(target, 0o755)
            with mock.patch.object(installer, "MANAGED_APPIMAGE_PATH", target), \
                 mock.patch.object(orchestrator.subprocess, "Popen") as mock_popen:
                self.assertTrue(orchestrator.restart_into_managed())
            argv = mock_popen.call_args[0][0]
            self.assertEqual(argv, [target])
            self.assertNotIn("/tmp/.mount_", argv[0])
            self.assertTrue(mock_popen.call_args[1]["start_new_session"])


class HelperUpdateNeededTests(unittest.TestCase):
    def _status(self, state, version=""):
        from core.persistence.priv_client import HelperStatus
        return HelperStatus(state, version=version)

    def test_missing_helper_is_not_an_update(self):
        from core.persistence import priv_client
        with mock.patch.object(priv_client, "installed_helper_status",
                               return_value=self._status(priv_client.HELPER_MISSING)), \
             mock.patch("core.updater.orchestrator.__name__", orchestrator.__name__):
            self.assertFalse(orchestrator.helper_update_needed())

    def test_older_installed_helper_needs_update(self):
        from core.persistence import priv_client
        with mock.patch.object(priv_client, "installed_helper_status",
                               return_value=self._status(priv_client.HELPER_READY, "0.0.1")):
            self.assertTrue(orchestrator.helper_update_needed())

    def test_current_helper_needs_no_update(self):
        from core.persistence import priv_client
        from core.privileged import helper_meta
        with mock.patch.object(priv_client, "installed_helper_status",
                               return_value=self._status(priv_client.HELPER_READY,
                                                          helper_meta.HELPER_VERSION)):
            self.assertFalse(orchestrator.helper_update_needed())

    def test_incompatible_helper_needs_update(self):
        from core.persistence import priv_client
        with mock.patch.object(priv_client, "installed_helper_status",
                               return_value=self._status(priv_client.HELPER_INCOMPATIBLE)):
            self.assertTrue(orchestrator.helper_update_needed())


class UpdateHelperFromAppimageTests(unittest.TestCase):
    """update_helper_from_appimage(): the one piece of the Beta4->Beta5
    simulated E2E flow not exercised by a real local HTTP server test
    (real_update_test scripts, run manually against the real managed
    install) — extraction genuinely happens against a small real
    self-extracting fake "AppImage" shell script (same technique already
    used by tests/test_install_scripts.py's icon-extraction tests, not
    a real AppImage); only the final root-owned self_update call is
    mocked, at the same default_privileged_writer() boundary every
    other orchestrator test mocks at."""

    def _fake_appimage(self, helper_content: "bytes | None" = b"#!/usr/bin/env python3\nprint('fake helper')\n"):
        script = (
            "#!/bin/sh\n"
            'if [ "$1" = "--appimage-extract" ]; then\n'
            "    mkdir -p squashfs-root\n"
        )
        if helper_content is not None:
            script += (
                '    cat > "squashfs-root/$2" <<\'HELPER_EOF\'\n'
                + helper_content.decode() +
                "HELPER_EOF\n"
            )
        script += "    exit 0\nfi\nexit 0\n"
        path = os.path.join(self._tmpdir.name, "fake-appimage.AppImage")
        with open(path, "w") as f:
            f.write(script)
        os.chmod(path, 0o755)
        return path

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def test_successful_extraction_calls_self_update_with_matching_checksum(self):
        appimage = self._fake_appimage()
        fake_writer = mock.Mock()
        fake_writer.execute.return_value = mock.Mock(ok=True, friendly_message="", technical_detail="")
        with mock.patch("core.persistence.priv_client.default_privileged_writer", return_value=fake_writer):
            result = orchestrator.update_helper_from_appimage(appimage)
        self.assertTrue(result.ok)
        fake_writer.execute.assert_called_once()
        args, _kwargs = fake_writer.execute.call_args
        self.assertEqual(args[0], "helper.update")
        self.assertEqual(args[1], "self_update")
        payload = args[2]
        self.assertIn("source_path", payload)
        self.assertTrue(payload["source_path"].endswith("mg-privileged-helper"))
        # the checksum sent is a real sha256 of the extracted bytes, not a placeholder
        self.assertRegex(payload["expected_sha256"], r"^[0-9a-f]{64}$")

    def test_extraction_failure_reported_without_touching_the_helper(self):
        appimage = os.path.join(self._tmpdir.name, "does-not-exist.AppImage")
        fake_writer = mock.Mock()
        with mock.patch("core.persistence.priv_client.default_privileged_writer", return_value=fake_writer):
            result = orchestrator.update_helper_from_appimage(appimage)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "helper_update_err_source")
        fake_writer.execute.assert_not_called()

    def test_helper_missing_inside_the_appimage_is_reported(self):
        appimage = self._fake_appimage(helper_content=None)  # extracts nothing
        fake_writer = mock.Mock()
        with mock.patch("core.persistence.priv_client.default_privileged_writer", return_value=fake_writer):
            result = orchestrator.update_helper_from_appimage(appimage)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "helper_update_err_source")
        fake_writer.execute.assert_not_called()

    def test_writer_rejection_is_propagated_incompatible_or_bad_checksum(self):
        """Covers both 'helper incompatibile' (downgrade/marker rejection)
        and 'helper con checksum errato' from the root side — either way
        the orchestrator must surface the writer's own failure, not mask
        it as a generic error."""
        appimage = self._fake_appimage()
        fake_writer = mock.Mock()
        fake_writer.execute.return_value = mock.Mock(
            ok=False, friendly_message="helper_update_err_downgrade", technical_detail="candidate older")
        with mock.patch("core.persistence.priv_client.default_privileged_writer", return_value=fake_writer):
            result = orchestrator.update_helper_from_appimage(appimage)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "helper_update_err_downgrade")

    def test_polkit_cancellation_style_failure_is_propagated(self):
        """The writer boundary is exactly where a cancelled/denied
        pkexec authentication surfaces as a plain OpResult(False, ...) —
        the orchestrator must not swallow or re-interpret it."""
        appimage = self._fake_appimage()
        fake_writer = mock.Mock()
        fake_writer.execute.return_value = mock.Mock(
            ok=False, friendly_message="kf_err_permission", technical_detail="Authentication failed")
        with mock.patch("core.persistence.priv_client.default_privileged_writer", return_value=fake_writer):
            result = orchestrator.update_helper_from_appimage(appimage)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "kf_err_permission")

    def test_temp_work_dir_always_cleaned_up_success_and_failure(self):
        captured = []
        real_mkdtemp = tempfile.mkdtemp

        def spy_mkdtemp(prefix=None):
            path = real_mkdtemp(prefix=prefix)
            captured.append(path)
            return path

        appimage = self._fake_appimage()
        fake_writer = mock.Mock()
        fake_writer.execute.return_value = mock.Mock(ok=True, friendly_message="", technical_detail="")
        with mock.patch.object(orchestrator.tempfile, "mkdtemp", spy_mkdtemp), \
             mock.patch("core.persistence.priv_client.default_privileged_writer", return_value=fake_writer):
            orchestrator.update_helper_from_appimage(appimage)
        bad_appimage = os.path.join(self._tmpdir.name, "missing.AppImage")
        with mock.patch.object(orchestrator.tempfile, "mkdtemp", spy_mkdtemp):
            orchestrator.update_helper_from_appimage(bad_appimage)
        self.assertEqual(len(captured), 2)
        for path in captured:
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
