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

from core.updater.models import InstallResult

ARCH_MAP = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}

MANAGED_DIR = os.path.expanduser("~/.local/opt/mg-linux-toolbox")
MANAGED_APPIMAGE_NAME = "MG-Linux-Toolbox.AppImage"
MANAGED_APPIMAGE_PATH = os.path.join(MANAGED_DIR, MANAGED_APPIMAGE_NAME)
DESKTOP_ENTRY_DIR = os.path.expanduser("~/.local/share/applications")
DESKTOP_ENTRY_PATH = os.path.join(DESKTOP_ENTRY_DIR, "mg-linux-toolbox.desktop")
BACKUP_DIR = os.path.join(MANAGED_DIR, "backup")

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
    try:
        os.makedirs(managed_dir, exist_ok=True)
        dest = os.path.join(managed_dir, MANAGED_APPIMAGE_NAME)
        shutil.copy2(source_appimage_path, dest)
        os.chmod(dest, 0o755)
        os.makedirs(os.path.dirname(desktop_entry_path), exist_ok=True)
        entry = _DESKTOP_ENTRY_TEMPLATE.format(
            app_name=APP_NAME, comment=APP_NAME, exec_path=dest, icon_name=icon_name)
        with open(desktop_entry_path, "w") as f:
            f.write(entry)
        os.chmod(desktop_entry_path, 0o755)
    except OSError as e:
        return InstallResult(False, friendly_message="updater_managed_install_failed", technical_detail=str(e))
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


def replace_atomically(new_verified_path: str, target_path: str) -> InstallResult:
    """
    The new file must already be verified (checksum) and executable-bit
    set by the caller before this runs. Uses os.replace (atomic on the
    same filesystem) so there's never a moment where target_path is a
    half-written file.
    """
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(new_verified_path, "rb") as f:
            os.fsync(f.fileno())
        os.replace(new_verified_path, target_path)
        os.chmod(target_path, 0o755)
    except OSError as e:
        return InstallResult(False, friendly_message="updater_replace_failed", technical_detail=str(e))
    return InstallResult(True)


def restore_previous(backup_path: str, target_path: str) -> InstallResult:
    if not os.path.isfile(backup_path):
        return InstallResult(False, friendly_message="updater_no_backup_available")
    try:
        os.replace(backup_path, target_path)
        os.chmod(target_path, 0o755)
    except OSError as e:
        return InstallResult(False, friendly_message="updater_restore_failed", technical_detail=str(e))
    return InstallResult(True)
