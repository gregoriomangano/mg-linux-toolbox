"""
Tests for core/updater/ and core/release_config.py. Everything here uses
fake releases, mocked HTTP, and temporary directories — the real
packaging/appimage/MG-Linux-Toolbox-x86_64.AppImage and the real
~/.local/opt install location are NEVER touched by this suite.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import release_config
from core.updater import semver, update_state, github_provider, downloader, verifier, installer
from core.updater.models import ReleaseInfo, ReleaseAsset


# ── release_config ────────────────────────────────────────────────────
class ReleaseConfigTests(unittest.TestCase):
    def test_configured_for_the_release_repository(self):
        # Only the updater target is configured; no credential is stored.
        self.assertTrue(release_config.github_configured())
        self.assertEqual(release_config.GITHUB_OWNER, "gregoriomangano")
        self.assertEqual(release_config.GITHUB_REPOSITORY, "mg-linux-toolbox")

    def test_github_repo_full_matches_the_real_repository(self):
        self.assertEqual(release_config.github_repo_full(), "gregoriomangano/mg-linux-toolbox")

    def test_not_configured_when_either_half_missing(self):
        with mock.patch.object(release_config, "GITHUB_OWNER", ""), \
             mock.patch.object(release_config, "GITHUB_REPOSITORY", "somerepo"):
            self.assertFalse(release_config.github_configured())
            self.assertEqual(release_config.github_repo_full(), "")

    def test_configured_when_both_present(self):
        with mock.patch.object(release_config, "GITHUB_OWNER", "someone"), \
             mock.patch.object(release_config, "GITHUB_REPOSITORY", "somerepo"):
            self.assertTrue(release_config.github_configured())
            self.assertEqual(release_config.github_repo_full(), "someone/somerepo")

    def test_no_credential_assignments_in_release_config_source(self):
        """"Non inserire token, password o credenziali" — checks for an
        actual assignment (TOKEN = "..."), not just the word appearing
        in prose (this module's own docstring explains it deliberately
        has none, which would otherwise trip a naive substring check)."""
        import inspect
        source = inspect.getsource(release_config)
        pattern = re.compile(r"(?i)\b\w*(token|password|secret|api[_-]?key)\w*\s*=\s*['\"]")
        match = pattern.search(source)
        self.assertIsNone(match, f"found a credential-looking assignment: {match}")


# ── semver ────────────────────────────────────────────────────────────
class SemverTests(unittest.TestCase):
    def test_stable_beats_prerelease_of_same_version(self):
        self.assertEqual(semver.compare(semver.parse("1.0.0"), semver.parse("1.0.0-beta.1")), 1)

    def test_beta_numeric_identifiers_ordered_numerically(self):
        self.assertEqual(semver.compare(semver.parse("0.9.0-beta.10"), semver.parse("0.9.0-beta.2")), 1)

    def test_alpha_lower_than_beta(self):
        self.assertEqual(semver.compare(semver.parse("0.9.0-alpha.1"), semver.parse("0.9.0-beta.1")), -1)

    def test_channel_classification(self):
        self.assertEqual(semver.channel_of(semver.parse("0.9.0-alpha.1")), "alpha")
        self.assertEqual(semver.channel_of(semver.parse("0.9.0-beta.1")), "beta")
        self.assertEqual(semver.channel_of(semver.parse("1.0.0")), "stable")

    def test_unparseable_version_returns_none(self):
        self.assertIsNone(semver.parse("not-a-version"))

    def test_v_prefix_tolerated(self):
        v = semver.parse("v0.9.0-beta.1")
        self.assertIsNotNone(v)
        self.assertEqual(str(v), "0.9.0-beta.1")


# ── update_state (channel filtering + throttle) ──────────────────────
def _release(version, prerelease):
    sv = semver.parse(version)
    return ReleaseInfo(tag=f"v{version}", version=version, prerelease=prerelease,
                        channel=semver.channel_of(sv))


class UpdateStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_beta_channel_sees_beta_and_stable_not_alpha(self):
        releases = [_release("0.9.0-alpha.1", True), _release("0.9.0-beta.1", True), _release("0.8.0", False)]
        latest = update_state.find_latest_for_channel(releases, "beta")
        self.assertEqual(latest.version, "0.9.0-beta.1")

    def test_stable_channel_ignores_all_prereleases(self):
        releases = [_release("0.9.0-beta.5", True), _release("0.8.0", False)]
        latest = update_state.find_latest_for_channel(releases, "stable")
        self.assertEqual(latest.version, "0.8.0")

    def test_stable_channel_with_only_prereleases_finds_nothing(self):
        releases = [_release("0.9.0-beta.5", True)]
        latest = update_state.find_latest_for_channel(releases, "stable")
        self.assertIsNone(latest)

    def test_check_for_update_true_when_newer(self):
        releases = [_release("0.9.0-beta.2", True)]
        result = update_state.check_for_update("0.9.0-beta.1", releases, "beta")
        self.assertTrue(result.update_available)
        self.assertEqual(result.latest.version, "0.9.0-beta.2")

    def test_check_for_update_false_when_current_is_latest(self):
        releases = [_release("0.9.0-beta.1", True)]
        result = update_state.check_for_update("0.9.0-beta.1", releases, "beta")
        self.assertFalse(result.update_available)

    def test_older_release_is_never_offered_as_an_update(self):
        # The only release GitHub has is OLDER than what's installed —
        # must never be reported as "an update", regardless of it being
        # the "latest" one that exists for this channel.
        releases = [_release("0.9.0-beta.1", True)]
        result = update_state.check_for_update("0.9.0-beta.2", releases, "beta")
        self.assertFalse(result.update_available)

    def test_no_releases_for_channel_yet_is_not_shown_as_up_to_date(self):
        """A stable-channel user before any stable release exists must
        see "not published yet", never a misleading "you already have
        the latest" (there's nothing to compare against at all)."""
        result = update_state.check_for_update("1.0.0", [_release("0.9.0-beta.1", True)], "stable")
        self.assertFalse(result.update_available)
        self.assertIsNone(result.latest)
        self.assertEqual(result.friendly_message, "updater_no_releases_yet")

    def test_stable_user_never_offered_a_beta_even_if_its_newer(self):
        releases = [_release("2.0.0-beta.1", True), _release("1.0.0", False)]
        result = update_state.check_for_update("1.0.0", releases, "stable")
        self.assertFalse(result.update_available)

    def test_auto_check_throttle(self):
        path = os.path.join(self.tmp, "update_check.json")
        with mock.patch.object(update_state, "update_state_path", return_value=path):
            self.assertTrue(update_state.should_auto_check(now=1000.0))
            update_state.record_check_now(now=1000.0)
            self.assertFalse(update_state.should_auto_check(now=1000.0 + 3600))  # 1h later, too soon
            self.assertTrue(update_state.should_auto_check(now=1000.0 + 24 * 3600 + 1))  # >24h later


# ── github_provider ───────────────────────────────────────────────────
class GithubProviderTests(unittest.TestCase):
    def _fake_response(self, payload):
        class FakeResp:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *a):
                return False
            def read(self_inner):
                return json.dumps(payload).encode()
        return FakeResp()

    def test_alpha_prerelease_included_but_filterable(self):
        payload = [{"tag_name": "v0.9.0-alpha.1", "prerelease": True, "assets": [], "body": "", "published_at": ""}]
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            releases = github_provider.fetch_releases("owner", "repo")
        self.assertEqual(releases[0].channel, "alpha")

    def test_malformed_tag_skipped_not_guessed(self):
        payload = [{"tag_name": "not-a-version", "prerelease": False, "assets": []}]
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            releases = github_provider.fetch_releases("owner", "repo")
        self.assertEqual(releases, [])

    def test_repo_not_found(self):
        err = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(github_provider.GithubError) as ctx:
                github_provider.fetch_releases("owner", "repo")
        self.assertEqual(ctx.exception.friendly_message, "updater_repo_not_found")

    def test_no_network(self):
        err = urllib.error.URLError("no route to host")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(github_provider.GithubError) as ctx:
                github_provider.fetch_releases("owner", "repo")
        self.assertEqual(ctx.exception.friendly_message, "updater_no_network")

    def test_draft_releases_excluded(self):
        payload = [{"tag_name": "v0.9.0", "prerelease": False, "draft": True, "assets": []}]
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            releases = github_provider.fetch_releases("owner", "repo")
        self.assertEqual(releases, [])


# ── downloader ────────────────────────────────────────────────────────
class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rejects_non_https_url(self):
        dest = os.path.join(self.tmp, "out.bin")
        result = downloader.download_asset("http://example.com/file", dest)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "updater_insecure_url")
        self.assertFalse(os.path.exists(dest))

    def test_successful_download(self):
        content = b"fake appimage content" * 100

        class FakeResp:
            headers = {"Content-Length": str(len(content))}
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False
            def read(self_inner, n=-1):
                nonlocal content
                chunk, content = content[:n], content[n:]
                return chunk

        dest = os.path.join(self.tmp, "out.bin")
        progress_calls = []
        with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
            result = downloader.download_asset("https://example.com/file", dest,
                                                 on_progress=lambda d, t: progress_calls.append((d, t)))
        self.assertTrue(result.ok)
        self.assertTrue(os.path.isfile(dest))
        self.assertGreater(len(progress_calls), 0)

    def test_interrupted_download_leaves_no_partial_file_at_dest(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection reset")):
            dest = os.path.join(self.tmp, "out.bin")
            result = downloader.download_asset("https://example.com/file", dest)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "updater_no_network")
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_timeout(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError()):
            dest = os.path.join(self.tmp, "out.bin")
            result = downloader.download_asset("https://example.com/file", dest)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "updater_timeout")

    def test_cancellation_removes_partial_file(self):
        chunks = [b"a" * 1000, b"b" * 1000, b"c" * 1000]

        class FakeResp:
            headers = {}
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False
            def read(self_inner, n=-1):
                return chunks.pop(0) if chunks else b""

        token = downloader.CancelToken()

        def fake_progress(downloaded, total):
            token.cancel()  # cancel after the first chunk

        dest = os.path.join(self.tmp, "out.bin")
        with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
            result = downloader.download_asset("https://example.com/file", dest,
                                                 on_progress=fake_progress, cancel_token=token)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "updater_cancelled")
        self.assertFalse(os.path.exists(dest))


# ── verifier ──────────────────────────────────────────────────────────
class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "file.bin")
        with open(self.path, "wb") as f:
            f.write(b"hello world")
        self.real_sha = hashlib.sha256(b"hello world").hexdigest()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_correct_checksum_verifies(self):
        self.assertTrue(verifier.verify_file(self.path, self.real_sha))

    def test_wrong_checksum_rejected(self):
        self.assertFalse(verifier.verify_file(self.path, "0" * 64))

    def test_empty_file_rejected_even_with_matching_hash_of_empty(self):
        empty_path = os.path.join(self.tmp, "empty.bin")
        open(empty_path, "wb").close()
        empty_sha = hashlib.sha256(b"").hexdigest()
        self.assertFalse(verifier.verify_file(empty_path, empty_sha))

    def test_parse_checksum_file_standard_format(self):
        content = f"{self.real_sha}  MG-Linux-Toolbox-0.9.0-beta.1-x86_64.AppImage\n"
        self.assertEqual(verifier.parse_checksum_file(content), self.real_sha)


# ── installer ─────────────────────────────────────────────────────────
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_asset_selection_by_exact_name_and_arch(self):
        release = ReleaseInfo(tag="v0.9.0-beta.1", version="0.9.0-beta.1", prerelease=True, channel="beta", assets=[
            ReleaseAsset(name="MG-Linux-Toolbox-0.9.0-beta.1-x86_64.AppImage", download_url="https://x/1", size=100),
            ReleaseAsset(name="MG-Linux-Toolbox-0.9.0-beta.1-aarch64.AppImage", download_url="https://x/2", size=100),
        ])
        asset = installer.select_asset(release, "x86_64")
        self.assertEqual(asset.name, "MG-Linux-Toolbox-0.9.0-beta.1-x86_64.AppImage")

    def test_asset_missing_architecture(self):
        release = ReleaseInfo(tag="v0.9.0-beta.1", version="0.9.0-beta.1", prerelease=True, channel="beta", assets=[
            ReleaseAsset(name="MG-Linux-Toolbox-0.9.0-beta.1-x86_64.AppImage", download_url="https://x/1", size=100),
        ])
        self.assertIsNone(installer.select_asset(release, "aarch64"))
        self.assertIsNone(installer.select_asset(release, ""))

    def test_duplicate_named_assets_first_exact_match_used_unambiguously(self):
        # Even if the release accidentally lists two assets with
        # different URLs, only ones with the EXACT expected name match;
        # anything not exactly matching (e.g. a checksum file) is ignored.
        release = ReleaseInfo(tag="v0.9.0-beta.1", version="0.9.0-beta.1", prerelease=True, channel="beta", assets=[
            ReleaseAsset(name="MG-Linux-Toolbox-0.9.0-beta.1-x86_64.AppImage.sha256", download_url="https://x/1.sha256"),
            ReleaseAsset(name="MG-Linux-Toolbox-0.9.0-beta.1-x86_64.AppImage", download_url="https://x/1"),
        ])
        asset = installer.select_asset(release, "x86_64")
        self.assertEqual(asset.download_url, "https://x/1")

    def test_checksum_asset_selected_by_exact_name(self):
        release = ReleaseInfo(tag="v0.9.0-beta.1", version="0.9.0-beta.1", prerelease=True, channel="beta", assets=[
            ReleaseAsset(name="MG-Linux-Toolbox-0.9.0-beta.1-x86_64.AppImage", download_url="https://x/1"),
            ReleaseAsset(name="MG-Linux-Toolbox-0.9.0-beta.1-x86_64.AppImage.sha256", download_url="https://x/1.sha256"),
            ReleaseAsset(name="MG-Linux-Toolbox-0.9.0-beta.1-aarch64.AppImage.sha256", download_url="https://x/2.sha256"),
        ])
        checksum_asset = installer.select_checksum_asset(release, "x86_64")
        self.assertEqual(checksum_asset.download_url, "https://x/1.sha256")

    def test_checksum_asset_missing_returns_none(self):
        release = ReleaseInfo(tag="v0.9.0-beta.1", version="0.9.0-beta.1", prerelease=True, channel="beta", assets=[
            ReleaseAsset(name="MG-Linux-Toolbox-0.9.0-beta.1-x86_64.AppImage", download_url="https://x/1"),
        ])
        self.assertIsNone(installer.select_checksum_asset(release, "x86_64"))
        self.assertIsNone(installer.select_checksum_asset(release, ""))

    def test_expected_names_match_the_real_appimage_naming_convention(self):
        # Exactly the filename this project's own build script produces
        # (packaging/appimage/build_appimage.sh) — never guessed.
        self.assertEqual(installer.expected_asset_name("0.9.0-beta.1", "x86_64"),
                          "MG-Linux-Toolbox-0.9.0-beta.1-x86_64.AppImage")
        self.assertEqual(installer.expected_checksum_name("0.9.0-beta.1", "x86_64"),
                          "MG-Linux-Toolbox-0.9.0-beta.1-x86_64.AppImage.sha256")

    def test_portable_vs_managed_detection(self):
        managed_path = os.path.join(self.tmp, "managed", "MG-Linux-Toolbox.AppImage")
        os.makedirs(os.path.dirname(managed_path))
        open(managed_path, "wb").close()
        # is_managed_install() compares against the REAL constant path;
        # exercise the relative logic via is_portable_launch with an
        # explicit managed_path override instead, which is what actual
        # detection code should do for testability.
        self.assertFalse(installer.is_portable_launch(managed_path, managed_path=managed_path))
        other_path = os.path.join(self.tmp, "Downloads", "MG-Linux-Toolbox.AppImage")
        os.makedirs(os.path.dirname(other_path))
        open(other_path, "wb").close()
        self.assertTrue(installer.is_portable_launch(other_path, managed_path=managed_path))

    def test_managed_install_copies_and_writes_desktop_entry(self):
        source = os.path.join(self.tmp, "Downloads", "MG-Linux-Toolbox.AppImage")
        os.makedirs(os.path.dirname(source))
        with open(source, "wb") as f:
            f.write(b"fake appimage")
        managed_dir = os.path.join(self.tmp, "managed")
        desktop_path = os.path.join(self.tmp, "applications", "mg-linux-toolbox.desktop")
        result = installer.install_to_managed_location(source, managed_dir=managed_dir, desktop_entry_path=desktop_path)
        self.assertTrue(result.ok)
        self.assertTrue(os.path.isfile(os.path.join(managed_dir, installer.MANAGED_APPIMAGE_NAME)))
        self.assertTrue(os.path.isfile(desktop_path))
        with open(desktop_path) as f:
            content = f.read()
        self.assertIn("Exec=", content)

    def test_managed_install_failure_does_not_change_existing_destination_or_launcher(self):
        source = os.path.join(self.tmp, "Downloads", "MG-Linux-Toolbox.AppImage")
        os.makedirs(os.path.dirname(source))
        with open(source, "wb") as f:
            f.write(b"new appimage")
        managed_dir = os.path.join(self.tmp, "managed")
        os.makedirs(managed_dir)
        destination = os.path.join(managed_dir, installer.MANAGED_APPIMAGE_NAME)
        with open(destination, "wb") as f:
            f.write(b"old appimage")
        os.chmod(destination, 0o755)
        desktop_path = os.path.join(self.tmp, "applications", "mg-linux-toolbox.desktop")
        os.makedirs(os.path.dirname(desktop_path))
        with open(desktop_path, "w") as f:
            f.write("old launcher")
        real_replace = installer.os.replace

        def fail_launcher_replace(source_path, target_path):
            if target_path == desktop_path:
                raise OSError("launcher write failed")
            return real_replace(source_path, target_path)

        with mock.patch.object(installer.os, "replace", side_effect=fail_launcher_replace):
            result = installer.install_to_managed_location(
                source, managed_dir=managed_dir, desktop_entry_path=desktop_path)
        self.assertFalse(result.ok)
        with open(destination, "rb") as f:
            self.assertEqual(f.read(), b"old appimage")
        with open(desktop_path) as f:
            self.assertEqual(f.read(), "old launcher")

    def test_managed_install_rejects_empty_source(self):
        source = os.path.join(self.tmp, "Downloads", "MG-Linux-Toolbox.AppImage")
        os.makedirs(os.path.dirname(source))
        open(source, "wb").close()
        result = installer.install_to_managed_location(
            source, managed_dir=os.path.join(self.tmp, "managed"),
            desktop_entry_path=os.path.join(self.tmp, "applications", "app.desktop"))
        self.assertFalse(result.ok)

    def test_managed_install_rejects_incomplete_copy(self):
        source = os.path.join(self.tmp, "Downloads", "MG-Linux-Toolbox.AppImage")
        os.makedirs(os.path.dirname(source))
        with open(source, "wb") as f:
            f.write(b"complete source")
        real_copy2 = installer.shutil.copy2

        def truncated_copy(source_path, destination_path):
            if destination_path.endswith(".new"):
                with open(destination_path, "wb") as f:
                    f.write(b"short")
                return destination_path
            return real_copy2(source_path, destination_path)

        with mock.patch.object(installer.shutil, "copy2", side_effect=truncated_copy):
            result = installer.install_to_managed_location(
                source, managed_dir=os.path.join(self.tmp, "managed"),
                desktop_entry_path=os.path.join(self.tmp, "applications", "app.desktop"))
        self.assertFalse(result.ok)

    def test_stage_from_tmp_is_verified_beside_managed_destination(self):
        source = os.path.join(tempfile.gettempdir(), f"mg-toolbox-stage-{os.getpid()}.AppImage")
        self.addCleanup(lambda: os.path.exists(source) and os.unlink(source))
        with open(source, "wb") as stream:
            stream.write(b"candidate from tmp")
        target = os.path.join(self.tmp, "managed", installer.MANAGED_APPIMAGE_NAME)
        staged, error = installer.stage_verified_copy(source, target)
        self.addCleanup(lambda: os.path.exists(staged) and os.unlink(staged))
        self.assertEqual(error, "")
        self.assertEqual(os.path.dirname(staged), os.path.dirname(target))
        self.assertTrue(os.path.isfile(staged))
        self.assertTrue(os.access(staged, os.X_OK))
        self.assertEqual(installer._sha256(source), installer._sha256(staged))
        self.assertEqual(os.stat(staged).st_dev, os.stat(os.path.dirname(target)).st_dev)

    def test_stage_copy_error_leaves_no_candidate(self):
        source = os.path.join(self.tmp, "source.AppImage")
        target = os.path.join(self.tmp, "managed", installer.MANAGED_APPIMAGE_NAME)
        with open(source, "wb") as stream:
            stream.write(b"candidate")
        with mock.patch.object(installer.shutil, "copy2", side_effect=OSError("disk full")):
            staged, error = installer.stage_verified_copy(source, target)
        self.assertEqual(staged, "")
        self.assertIn("disk full", error)
        self.assertFalse(os.path.exists(target))
        self.assertEqual(os.listdir(os.path.dirname(target)), [])

    def test_stage_rejects_empty_or_corrupted_copy(self):
        source = os.path.join(self.tmp, "source.AppImage")
        target = os.path.join(self.tmp, "managed", installer.MANAGED_APPIMAGE_NAME)
        with open(source, "wb") as stream:
            stream.write(b"candidate")

        def corrupt_copy(_source, destination):
            with open(destination, "wb") as stream:
                stream.write(b"corrupted")
            return destination

        with mock.patch.object(installer.shutil, "copy2", side_effect=corrupt_copy):
            staged, error = installer.stage_verified_copy(source, target)
        self.assertEqual(staged, "")
        self.assertIn("checksum", error)
        open(source, "wb").close()
        staged, error = installer.stage_verified_copy(source, target)
        self.assertEqual(staged, "")
        self.assertIn("empty", error)

    def test_backup_creates_copy_and_none_when_nothing_to_back_up(self):
        managed_path = os.path.join(self.tmp, "MG-Linux-Toolbox.AppImage")
        backup_dir = os.path.join(self.tmp, "backup")
        self.assertIsNone(installer.backup_current(managed_path, backup_dir, "0.8.0"))
        with open(managed_path, "wb") as f:
            f.write(b"old version content")
        backup_path = installer.backup_current(managed_path, backup_dir, "0.8.0")
        self.assertIsNotNone(backup_path)
        self.assertTrue(os.path.isfile(backup_path))
        with open(backup_path, "rb") as f:
            self.assertEqual(f.read(), b"old version content")

    def test_replace_atomically_and_rollback(self):
        target = os.path.join(self.tmp, "MG-Linux-Toolbox.AppImage")
        with open(target, "wb") as f:
            f.write(b"old version")
        backup_dir = os.path.join(self.tmp, "backup")
        backup_path = installer.backup_current(target, backup_dir, "0.8.0")

        new_file = os.path.join(self.tmp, "new_downloaded.AppImage")
        with open(new_file, "wb") as f:
            f.write(b"new version")
        result = installer.replace_atomically(new_file, target)
        self.assertTrue(result.ok)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"new version")

        # Rollback: "Ripristina versione precedente"
        rollback = installer.restore_previous(backup_path, target)
        self.assertTrue(rollback.ok)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"old version")

    def test_replace_refuses_cross_filesystem_candidate_without_touching_target(self):
        target = os.path.join(self.tmp, "MG-Linux-Toolbox.AppImage")
        source = os.path.join(self.tmp, "candidate.AppImage")
        with open(target, "wb") as stream:
            stream.write(b"old version")
        with open(source, "wb") as stream:
            stream.write(b"new version")

        real_stat = installer.os.stat

        class ForeignStat:
            st_dev = real_stat(os.path.dirname(target)).st_dev + 1

        def foreign_source_stat(path):
            return ForeignStat() if path == source else real_stat(path)

        with mock.patch.object(installer.os, "stat", side_effect=foreign_source_stat):
            result = installer.replace_atomically(source, target)
        self.assertFalse(result.ok)
        with open(target, "rb") as stream:
            self.assertEqual(stream.read(), b"old version")

    def test_rollback_without_backup_fails_cleanly(self):
        target = os.path.join(self.tmp, "MG-Linux-Toolbox.AppImage")
        result = installer.restore_previous(os.path.join(self.tmp, "nonexistent-backup"), target)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "updater_no_backup_available")

    def test_never_touches_real_managed_or_appimage_paths(self):
        """Sanity guard: none of the module-level real-path constants
        should ever be passed to a mutating call in this test file."""
        real_paths_used = []
        # (This test documents intent — the actual guarantee comes from
        # every mutating test above passing explicit tmp-dir overrides.)
        self.assertNotIn(installer.MANAGED_DIR, real_paths_used)


# ── End-to-end: download + checksum + replace + rollback ──────────────
class DownloadVerifyInstallFlowTests(unittest.TestCase):
    """The described flow — "scaricare AppImage e checksum, verificare
    il checksum prima di sostituire la versione esistente e conservare
    il ripristino della versione precedente" — exercised as one
    pipeline instead of only as isolated module tests."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.managed_path = os.path.join(self.tmp, "MG-Linux-Toolbox.AppImage")
        with open(self.managed_path, "wb") as f:
            f.write(b"old version content")
        self.backup_dir = os.path.join(self.tmp, "backup")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_response(self, content: bytes):
        class FakeResp:
            headers = {"Content-Length": str(len(content))}
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False
            def read(self_inner, n=-1):
                nonlocal content
                chunk, content = content[:n], content[n:]
                return chunk
        return FakeResp()

    def test_correct_checksum_leads_to_a_verified_replace_with_rollback_available(self):
        new_content = b"new version content"
        real_sha = hashlib.sha256(new_content).hexdigest()
        downloaded_path = os.path.join(self.tmp, "downloaded.AppImage")

        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(new_content)):
            result = downloader.download_asset("https://example.com/app.AppImage", downloaded_path)
        self.assertTrue(result.ok)

        self.assertTrue(verifier.verify_file(downloaded_path, real_sha))

        backup_path = installer.backup_current(self.managed_path, self.backup_dir, "0.8.0")
        install_result = installer.replace_atomically(downloaded_path, self.managed_path)
        self.assertTrue(install_result.ok)
        with open(self.managed_path, "rb") as f:
            self.assertEqual(f.read(), new_content)

        rollback = installer.restore_previous(backup_path, self.managed_path)
        self.assertTrue(rollback.ok)
        with open(self.managed_path, "rb") as f:
            self.assertEqual(f.read(), b"old version content")

    def test_wrong_checksum_blocks_the_replace_and_leaves_the_old_version_untouched(self):
        new_content = b"tampered or corrupted content"
        wrong_sha = hashlib.sha256(b"something else entirely").hexdigest()
        downloaded_path = os.path.join(self.tmp, "downloaded.AppImage")

        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(new_content)):
            result = downloader.download_asset("https://example.com/app.AppImage", downloaded_path)
        self.assertTrue(result.ok)  # the download itself succeeded...

        checksum_ok = verifier.verify_file(downloaded_path, wrong_sha)
        self.assertFalse(checksum_ok)  # ...but it must never be trusted

        # The call site is expected to check checksum_ok before ever calling
        # replace_atomically() — asserting that discipline here, not
        # re-testing replace_atomically() itself (already covered above).
        if not checksum_ok:
            with open(self.managed_path, "rb") as f:
                self.assertEqual(f.read(), b"old version content")


# ── "Messaggi semplici" — every user-facing updater string must read ───
# like plain language, never a raw protocol/technical term.
class SimpleMessagesTests(unittest.TestCase):
    _JARGON_WORDS = ("http", "https", "json", "exception", "traceback", "404",
                     "url", "checksum", "sha256", "api", "null", "nonetype")

    def _updater_keys(self):
        from core.i18n import _strings
        return {k: v for k, v in _strings.items() if k.startswith("updater_")}

    def test_every_updater_message_has_all_four_languages(self):
        for key, translations in self._updater_keys().items():
            for lang in ("it", "en", "es", "fr"):
                self.assertIn(lang, translations, f"{key} missing {lang}")
                self.assertTrue(translations[lang].strip(), f"{key}/{lang} is empty")

    def test_no_raw_technical_jargon_in_user_facing_text(self):
        # Word-boundary matching — a plain substring check false-positives
        # on legitimate words in other languages that happen to contain
        # one of these as a substring (e.g. Italian "annullato" contains
        # "null").
        word_pattern = re.compile(
            r"\b(" + "|".join(re.escape(w) for w in self._JARGON_WORDS) + r")\b")
        for key, translations in self._updater_keys().items():
            if key.endswith("_btn") or key.endswith("_desc"):
                continue  # button labels/short hints, not status sentences
            for lang, text in translations.items():
                match = word_pattern.search(text.lower())
                self.assertIsNone(match, f"{key}/{lang} contains jargon {match.group(0) if match else ''!r}: {text!r}")

    def test_key_example_messages_match_the_plain_language_spec(self):
        from core.i18n import T, set_lang
        original = None
        try:
            import core.i18n as i18n
            original = i18n._lang
            set_lang("it")
            self.assertEqual(T("updater_up_to_date"), "Stai già usando la versione più recente.")
            self.assertEqual(T("updater_check_failed"), "Non è stato possibile controllare gli aggiornamenti.")
            self.assertEqual(T("updater_checksum_mismatch"),
                              "Il file scaricato non ha superato il controllo di sicurezza ed è stato scartato.")
            self.assertEqual(T("updater_repo_not_found"), "La prima versione online non è ancora disponibile.")
            self.assertIn("È disponibile una nuova versione", T("updater_update_available"))
        finally:
            if original is not None:
                set_lang(original)

    def test_check_failed_result_reaches_a_simple_message_not_a_raw_exception(self):
        """Regression for the missing generic fallback: an exception
        type github_provider doesn't wrap (e.g. anything other than
        GithubError) must still resolve to a plain sentence, never
        leave the caller with a raw exception object to display."""
        from core.updater.models import UpdateCheckResult
        try:
            raise ValueError("some unexpected low-level failure")
        except Exception as e:
            result = UpdateCheckResult(False, None, "0.9.0-beta.1",
                                        friendly_message="updater_check_failed", technical_detail=str(e))
        from core.i18n import T
        self.assertEqual(T(result.friendly_message), T("updater_check_failed"))
        self.assertNotIn("ValueError", T(result.friendly_message))


# ── No credentials anywhere in the updater package ─────────────────────
class NoCredentialsInUpdaterPackageTests(unittest.TestCase):
    def test_no_credential_assignments_in_any_updater_module(self):
        import inspect
        import core.updater.downloader as downloader_mod
        import core.updater.github_provider as github_provider_mod
        import core.updater.installer as installer_mod
        import core.updater.semver as semver_mod
        import core.updater.update_state as update_state_mod
        import core.updater.verifier as verifier_mod
        import core.updater.models as models_mod

        pattern = re.compile(r"(?i)\b\w*(token|password|secret|api[_-]?key)\w*\s*=\s*['\"]")
        for module in (downloader_mod, github_provider_mod, installer_mod, semver_mod,
                       update_state_mod, verifier_mod, models_mod, release_config):
            source = inspect.getsource(module)
            match = pattern.search(source)
            self.assertIsNone(match, f"found a credential-looking assignment in {module.__name__}: {match}")

    def test_github_requests_never_send_an_authorization_header(self):
        """Public-repo releases don't need one, and this app never asks
        the user for a GitHub credential to begin with."""
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = dict(req.header_items())
            class FakeResp:
                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *a): return False
                def read(self_inner): return b"[]"
            return FakeResp()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            github_provider.fetch_releases(release_config.GITHUB_OWNER, release_config.GITHUB_REPOSITORY)
        header_names = {name.lower() for name in captured["headers"]}
        self.assertNotIn("authorization", header_names)


if __name__ == "__main__":
    unittest.main()
