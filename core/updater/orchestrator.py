"""
The complete one-click update flow, as one testable function per mode —
wires together the existing pieces (github_provider, update_state,
downloader, verifier, installer) without duplicating any of their
logic. No GTK here: the UI passes callbacks.

Managed flow (the app runs from ~/.local/opt/mg-linux-toolbox/):
  download AppImage + checksum into a private temp dir → verify the
  asset name/architecture → verify SHA-256 (the file is never made
  executable before this passes) → back up the current version (one
  kept) → atomic replace → update the .version file → optionally update
  the privileged helper → clean up → offer restart. Every failure
  leaves the previous version installed and its backup untouched.

Portable flow: the running AppImage is NEVER silently overwritten.
The UI offers "install managed + update" or "download only" —
download_only() saves the verified new file where the user chose.

Restart: always the STABLE managed path — never the /tmp/.mount_*
path of the currently mounted AppImage.
"""
import os
import shutil
import subprocess
import tempfile
import secrets
import sys

from core.updater import downloader, installer, verifier
from core.updater.models import InstallResult, ReleaseInfo

VERSION_FILE = os.path.join(installer.MANAGED_DIR, ".version")
LAST_PENDING_BACKUP_PATH = ""
LAST_UPDATE_VERSION = ""


class UpdateError(Exception):
    def __init__(self, friendly_message: str, technical_detail: str = ""):
        super().__init__(technical_detail or friendly_message)
        self.friendly_message = friendly_message
        self.technical_detail = technical_detail


def _select_assets(release: ReleaseInfo):
    arch = installer.current_arch()
    if not arch:
        raise UpdateError("updater_unsupported_arch")
    asset = installer.select_asset(release, arch)
    if asset is None:
        raise UpdateError("updater_asset_missing")
    checksum_asset = installer.select_checksum_asset(release, arch)
    if checksum_asset is None:
        raise UpdateError("updater_checksum_missing")
    return asset, checksum_asset


def _download_and_verify(release: ReleaseInfo, work_dir: str,
                          on_progress=None, cancel_token=None) -> str:
    """Downloads both assets into work_dir and returns the path of the
    VERIFIED AppImage. Raises UpdateError on any failure — the caller's
    cleanup removes work_dir, nothing else was touched yet."""
    asset, checksum_asset = _select_assets(release)

    appimage_path = os.path.join(work_dir, asset.name)
    result = downloader.download_asset(asset.download_url, appimage_path,
                                        expected_size=asset.size,
                                        on_progress=on_progress, cancel_token=cancel_token)
    if not result.ok:
        raise UpdateError(result.friendly_message or "updater_download_failed",
                          result.technical_detail)

    checksum_path = os.path.join(work_dir, checksum_asset.name)
    result = downloader.download_asset(checksum_asset.download_url, checksum_path,
                                        cancel_token=cancel_token)
    if not result.ok:
        raise UpdateError(result.friendly_message or "updater_download_failed",
                          result.technical_detail)

    try:
        with open(checksum_path) as f:
            expected = verifier.parse_checksum_file(f.read())
    except OSError as e:
        raise UpdateError("updater_checksum_missing", str(e))
    if not verifier.verify_file(appimage_path, expected):
        raise UpdateError("updater_checksum_mismatch",
                          f"expected {expected}, file {appimage_path}")
    return appimage_path


def _work_dir() -> str:
    # Private temp dir (0700 by mkdtemp default) — never a shared /tmp
    # file another user could tamper with between download and verify.
    return tempfile.mkdtemp(prefix="mg-toolbox-update-")


def perform_managed_update(release: ReleaseInfo, current_version: str,
                            on_progress=None, cancel_token=None) -> InstallResult:
    global LAST_PENDING_BACKUP_PATH, LAST_UPDATE_VERSION
    work_dir = _work_dir()
    try:
        try:
            verified_path = _download_and_verify(release, work_dir, on_progress, cancel_token)
        except UpdateError as e:
            return InstallResult(False, friendly_message=e.friendly_message,
                                  technical_detail=e.technical_detail)

        # Backup BEFORE any replacement; keep exactly one previous version.
        try:
            LAST_PENDING_BACKUP_PATH = installer.pending_backup_current(
                installer.MANAGED_APPIMAGE_PATH, installer.BACKUP_DIR,
                current_version or "unknown")
            LAST_UPDATE_VERSION = release.version
        except OSError as e:
            return InstallResult(False, friendly_message="updater_backup_failed",
                                  technical_detail=str(e))

        # Only now, after verification, the file becomes executable.
        try:
            os.chmod(verified_path, 0o755)
        except OSError as e:
            return InstallResult(False, friendly_message="updater_replace_failed",
                                  technical_detail=str(e))
        result = installer.replace_atomically(verified_path, installer.MANAGED_APPIMAGE_PATH)
        if not result.ok:
            return result

        try:
            with open(VERSION_FILE, "w") as f:
                f.write(release.version + "\n")
        except OSError:
            pass  # non-fatal: the AppImage itself knows its version
        return InstallResult(True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def download_only(release: ReleaseInfo, dest_dir: str,
                   on_progress=None, cancel_token=None) -> InstallResult:
    """Portable mode's "Scarica soltanto la nuova AppImage": verified
    download saved into a user-chosen folder; the running file is never
    touched. The saved file IS made executable — it has already passed
    the checksum, and the user's next step is running it."""
    work_dir = _work_dir()
    try:
        try:
            verified_path = _download_and_verify(release, work_dir, on_progress, cancel_token)
        except UpdateError as e:
            return InstallResult(False, friendly_message=e.friendly_message,
                                  technical_detail=e.technical_detail)
        dest_path = os.path.join(dest_dir, os.path.basename(verified_path))
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(verified_path, dest_path)
            os.chmod(dest_path, 0o755)
        except OSError as e:
            return InstallResult(False, friendly_message="updater_disk_error", technical_detail=str(e))
        return InstallResult(True, technical_detail=dest_path)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ── Privileged helper update ──────────────────────────────────────────
def helper_update_needed() -> bool:
    """True when a helper is installed but older than the version this
    app ships. A MISSING helper is not "an update" — that's the managed
    install flow's job (install.sh)."""
    from core.persistence import priv_client
    from core.privileged import helper_meta
    status = priv_client.installed_helper_status()
    if status.state != priv_client.HELPER_READY:
        return status.state == priv_client.HELPER_INCOMPATIBLE
    def _tuple(v):
        try:
            return tuple(int(p) for p in v.split("."))
        except ValueError:
            return ()
    return _tuple(status.version) < _tuple(helper_meta.HELPER_VERSION)


def update_helper_from_appimage(appimage_path: str) -> InstallResult:
    """
    Extracts the helper from an ALREADY VERIFIED AppImage file (its
    checksum passed in this same run — never the FUSE mount of the
    running app) and asks the installed helper's closed `self_update`
    action to install it. Technical decision, documented: extraction
    from the verified AppImage needs no second download or separate
    signed archive, and the root side re-validates the candidate anyway
    (sha256, python parse, version markers, no symlink, size cap,
    backup + atomic replace + rollback).
    """
    import hashlib
    from core.persistence.priv_client import default_privileged_writer
    work_dir = _work_dir()
    try:
        try:
            subprocess.run([appimage_path, "--appimage-extract", "mg-privileged-helper"],
                           cwd=work_dir, capture_output=True, timeout=60, check=True)
        except (OSError, subprocess.SubprocessError) as e:
            return InstallResult(False, friendly_message="helper_update_err_source",
                                  technical_detail=str(e))
        candidate = os.path.join(work_dir, "squashfs-root", "mg-privileged-helper")
        if not os.path.isfile(candidate):
            return InstallResult(False, friendly_message="helper_update_err_source",
                                  technical_detail="helper not found inside the AppImage")
        with open(candidate, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        result = default_privileged_writer().execute(
            "helper.update", "self_update",
            {"source_path": candidate, "expected_sha256": digest})
        if not result.ok:
            return InstallResult(False,
                                  friendly_message=result.friendly_message or "helper_update_err_install",
                                  technical_detail=result.technical_detail)
        return InstallResult(True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ── Restart ───────────────────────────────────────────────────────────
def restart_into_managed() -> bool:
    """Starts the external supervisor for confirmation and rollback."""
    target = installer.MANAGED_APPIMAGE_PATH
    pending = LAST_PENDING_BACKUP_PATH
    version = LAST_UPDATE_VERSION
    if not pending or not version or not os.path.isfile(pending):
        return False
    if not os.path.isfile(target) or not os.access(target, os.X_OK):
        return False
    helper_source = os.path.join(os.path.dirname(__file__), "launch_helper.py")
    runtime_dir = tempfile.mkdtemp(prefix="mg-toolbox-update-")
    helper_path = os.path.join(runtime_dir, "launch_helper.py")
    confirmation = os.path.join(runtime_dir, "confirmation.json")
    token = secrets.token_urlsafe(32)
    log_path = os.path.join(installer.BACKUP_DIR, "update-last-result.log")
    try:
        shutil.copy2(helper_source, helper_path)
        os.chmod(helper_path, 0o700)
        subprocess.Popen([
            sys.executable, helper_path,
            "--target", target,
            "--pending", pending,
            "--backup-dir", installer.BACKUP_DIR,
            "--version", version,
            "--previous-version", os.path.basename(pending)[len(installer.PENDING_BACKUP_PREFIX):-len(".AppImage")],
            "--confirmation", confirmation,
            "--token", token,
            "--log", log_path,
        ], cwd=runtime_dir, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        shutil.rmtree(runtime_dir, ignore_errors=True)
        return False
    return True
