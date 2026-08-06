"""
Where the AppImage lives, how it gets there, and how a new version
replaces it — never with sudo, never touching /usr or system /opt.

Two install modes:
  - "managed": the app has been copied to ~/.local/opt/mg-linux-toolbox/
    with a desktop entry in ~/.local/share/applications/. Updates can
    replace this file directly (it's the user's own, no special
    permissions needed) and a menu shortcut already exists.
  - "portable": running from Downloads/Desktop/a USB stick/some other
    unmanaged path. Never silently overwritten — the user is offered
    "Aggiungi al menu applicazioni" or "Scarica soltanto la nuova
    versione" (a plain file, no auto-replace of whatever they're
    running from, which might be read-only media anyway).
"""
import os
import platform
import shutil
import time
import hashlib
import tempfile

from core.updater.models import InstallResult

ARCH_MAP = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}

MANAGED_DIR = os.path.expanduser("~/.local/opt/mg-linux-toolbox")
MANAGED_APPIMAGE_NAME = "MG-Linux-Toolbox.AppImage"
MANAGED_APPIMAGE_PATH = os.path.join(MANAGED_DIR, MANAGED_APPIMAGE_NAME)
DESKTOP_ENTRY_DIR = os.path.expanduser("~/.local/share/applications")
DESKTOP_ENTRY_PATH = os.path.join(DESKTOP_ENTRY_DIR, "mg-linux-toolbox.desktop")
BACKUP_DIR = os.path.join(MANAGED_DIR, "backup")
PENDING_BACKUP_PREFIX = "pending-previous-"

_DESKTOP_ENTRY_TEMPLATE = """[Desktop Entry]
Type=Application
Name={app_name}
Comment={comment}
Exec={exec_path} %U
Icon={icon_name}
Terminal=false
Categories=System;Settings;
"""


def current_arch() -> str:
    return ARCH_MAP.get(platform.machine(), "")


def expected_asset_name(version: str, arch: str) -> str:
    return f"MG-Linux-Toolbox-{version}-{arch}.AppImage"


def expected_checksum_name(version: str, arch: str) -> str:
    return f"{expected_asset_name(version, arch)}.sha256"


def select_asset(release, arch: str):
    """Picks the asset by exact name + architecture — never "the first
    attachment". Returns None if this architecture has no build."""
    if not arch:
        return None
    name = expected_asset_name(release.version, arch)
    for asset in release.assets:
        if asset.name == name:
            return asset
    return None


def select_checksum_asset(release, arch: str):
    if not arch:
        return None
    name = expected_checksum_name(release.version, arch)
    for asset in release.assets:
        if asset.name == name:
            return asset
    return None


def is_managed_install(running_path: str) -> bool:
    try:
        return os.path.realpath(running_path) == os.path.realpath(MANAGED_APPIMAGE_PATH)
    except OSError:
        return False


def is_portable_launch(running_path: str, managed_path: str = MANAGED_APPIMAGE_PATH) -> bool:
    """True whenever running_path isn't the managed install location —
    covers Downloads, Desktop, a USB stick, or any other ad hoc path."""
    try:
        return os.path.realpath(running_path) != os.path.realpath(managed_path)
    except OSError:
        return True


def is_path_writable(path: str) -> bool:
    """Whether the CONTAINING FOLDER of a running AppImage could actually
    be written to (a read-only mount, an ISO, or a USB stick mounted
    read-only would all say no here) — checked before ever suggesting an
    in-place replace is possible for a portable launch."""
    directory = os.path.dirname(os.path.abspath(path)) if path else ""
    return bool(directory) and os.access(directory, os.W_OK)


def install_to_managed_location(source_appimage_path: str, icon_name: str = "mg-linux-toolbox",
                                 managed_dir: str = MANAGED_DIR,
                                 desktop_entry_path: str = DESKTOP_ENTRY_PATH) -> InstallResult:
    """
    Copies the currently-running AppImage into the user's own home
    directory and writes a desktop entry — no sudo, nothing outside the
    user's own $HOME.
    """
    from core.version import APP_NAME
    temporary = ""
    old_destination = ""
    desktop_temporary = ""
    destination_replaced = False
    rollback_error = ""
    cleanup_errors = []
    try:
        source_stat = os.stat(source_appimage_path)
        if not os.path.isfile(source_appimage_path) or source_stat.st_size == 0:
            return InstallResult(False, friendly_message="updater_managed_install_failed",
                                 technical_detail="the source AppImage is missing or empty")
        os.makedirs(managed_dir, exist_ok=True)
        dest = os.path.join(managed_dir, MANAGED_APPIMAGE_NAME)
        temporary = os.path.join(managed_dir, f".{MANAGED_APPIMAGE_NAME}.new")
        if os.path.isfile(dest):
            old_destination = os.path.join(managed_dir, f".{MANAGED_APPIMAGE_NAME}.old")
            shutil.copy2(dest, old_destination)
            os.chmod(old_destination, 0o755)
            if _sha256(dest) != _sha256(old_destination):
                raise OSError("the existing managed AppImage backup failed verification")
        shutil.copy2(source_appimage_path, temporary)
        os.chmod(temporary, 0o755)
        if not os.path.isfile(temporary) or os.path.getsize(temporary) != source_stat.st_size:
            raise OSError("the copied AppImage is incomplete")
        if _sha256(source_appimage_path) != _sha256(temporary):
            raise OSError("the copied AppImage failed checksum verification")
        os.replace(temporary, dest)
        destination_replaced = True
        os.chmod(dest, 0o755)
        if not os.path.isfile(dest) or os.path.getsize(dest) == 0 or not os.access(dest, os.X_OK):
            raise OSError("the managed AppImage failed destination verification")
        os.makedirs(os.path.dirname(desktop_entry_path), exist_ok=True)
        entry = _DESKTOP_ENTRY_TEMPLATE.format(
            app_name=APP_NAME, comment=APP_NAME, exec_path=dest, icon_name=icon_name)
        desktop_temporary = f"{desktop_entry_path}.new"
        with open(desktop_temporary, "w") as f:
            f.write(entry)
        os.chmod(desktop_temporary, 0o755)
        os.replace(desktop_temporary, desktop_entry_path)
    except OSError as e:
        if destination_replaced:
            try:
                if old_destination and os.path.isfile(old_destination):
                    os.replace(old_destination, dest)
                elif os.path.exists(dest):
                    os.unlink(dest)
            except OSError as restore_error:
                rollback_error = f"; destination rollback failed: {restore_error}"
        for path in (temporary, desktop_temporary, old_destination):
            if path:
                try:
                    os.unlink(path)
                except OSError as cleanup_error:
                    cleanup_errors.append(f"cleanup {path}: {cleanup_error}")
        detail = str(e) + rollback_error
        if cleanup_errors:
            detail += "; " + "; ".join(cleanup_errors)
        return InstallResult(False, friendly_message="updater_managed_install_failed", technical_detail=detail)
    try:
        if old_destination:
            os.unlink(old_destination)
    except OSError as cleanup_error:
        return InstallResult(True, technical_detail=f"managed installation completed; cleanup deferred: {cleanup_error}")
    return InstallResult(True)


def backup_current(managed_path: str, backup_dir: str, version_label: str) -> "str | None":
    """Keeps exactly one previous version around for 'Ripristina versione
    precedente' — returns the backup file path, or None if there was
    nothing to back up yet."""
    if not os.path.isfile(managed_path):
        return None
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, f"previous-{version_label}.AppImage")
    shutil.copy2(managed_path, backup_path)
    return backup_path


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: str) -> None:
    with open(path, "rb") as stream:
        os.fsync(stream.fileno())


def _fsync_directory(path: str) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def stage_verified_copy(source_path: str, target_path: str) -> tuple[str, str]:
    """Copy a verified download beside its final destination.

    Downloads live under /tmp, which may be another filesystem.  The
    temporary replacement must therefore be created in the managed
    directory before os.replace is used.
    """
    target_dir = os.path.dirname(os.path.abspath(target_path))
    temporary = ""
    try:
        source_stat = os.stat(source_path)
        if not os.path.isfile(source_path) or source_stat.st_size == 0:
            raise OSError("the verified AppImage is missing or empty")
        os.makedirs(target_dir, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{os.path.basename(target_path)}.new-", dir=target_dir)
        os.close(descriptor)
        shutil.copy2(source_path, temporary)
        os.chmod(temporary, 0o755)
        if not os.path.isfile(temporary) or os.path.getsize(temporary) != source_stat.st_size:
            raise OSError("the staged AppImage is incomplete")
        if not os.access(temporary, os.X_OK):
            raise OSError("the staged AppImage is not executable")
        if _sha256(source_path) != _sha256(temporary):
            raise OSError("the staged AppImage failed checksum verification")
        _fsync_file(temporary)
        _fsync_directory(target_dir)
        return temporary, ""
    except OSError as exc:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        return "", str(exc)


def pending_backup_current(managed_path: str, backup_dir: str, version_label: str) -> "str | None":
    """Create a verified backup kept until the new startup is confirmed."""
    if not os.path.isfile(managed_path) or os.path.getsize(managed_path) == 0:
        return None
    os.makedirs(backup_dir, exist_ok=True)
    path = os.path.join(backup_dir, f"{PENDING_BACKUP_PREFIX}{version_label}.AppImage")
    fd, temporary = tempfile.mkstemp(prefix="pending-", dir=backup_dir)
    os.close(fd)
    try:
        shutil.copy2(managed_path, temporary)
        os.chmod(temporary, 0o755)
        if not os.path.isfile(temporary) or os.path.getsize(temporary) == 0:
            raise OSError("pending backup is incomplete")
        if _sha256(managed_path) != _sha256(temporary):
            raise OSError("pending backup checksum verification failed")
        _fsync_file(temporary)
        os.replace(temporary, path)
        _fsync_directory(backup_dir)
        return path
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def replace_atomically(new_verified_path: str, target_path: str) -> InstallResult:
    """
    The new file must already be verified (checksum) and executable-bit
    set by the caller before this runs. Uses os.replace (atomic on the
    same filesystem) so there's never a moment where target_path is a
    half-written file.
    """
    try:
        target_dir = os.path.dirname(os.path.abspath(target_path))
        os.makedirs(target_dir, exist_ok=True)
        if os.stat(new_verified_path).st_dev != os.stat(target_dir).st_dev:
            raise OSError("replacement candidate is not on the managed filesystem")
        _fsync_file(new_verified_path)
        os.replace(new_verified_path, target_path)
        os.chmod(target_path, 0o755)
        _fsync_file(target_path)
        _fsync_directory(target_dir)
    except OSError as e:
        return InstallResult(False, friendly_message="updater_replace_failed", technical_detail=str(e))
    return InstallResult(True)


def restore_previous(backup_path: str, target_path: str) -> InstallResult:
    if not os.path.isfile(backup_path):
        return InstallResult(False, friendly_message="updater_no_backup_available")
    try:
        staged_path, error = stage_verified_copy(backup_path, target_path)
        if error:
            raise OSError(error)
        result = replace_atomically(staged_path, target_path)
        if not result.ok:
            return InstallResult(False, friendly_message="updater_restore_failed",
                                 technical_detail=result.technical_detail)
    except OSError as e:
        return InstallResult(False, friendly_message="updater_restore_failed", technical_detail=str(e))
    return InstallResult(True)
