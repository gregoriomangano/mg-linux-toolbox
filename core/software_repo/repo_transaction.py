"""
Shared transaction engine for anything that enables/disables a
software repository (APT, DNF, Zypper, Flatpak remotes). One state
machine, reused everywhere, instead of each family re-inventing its
own ad-hoc "just run the command" flow with no real backup or
rollback — see repo_recipes.py and the Salute pacchetti actions for
callers.

Every file-based operation backs up the exact file(s) about to change
BEFORE touching them, verifies the real result afterward (never trusts
a zero exit code alone), and restores the backup automatically if
verification fails — so a transaction can never leave a repository
half-configured. Every privileged step runs one fixed argv list
through pkexec (see core.executor) — nothing here ever concatenates
GUI/user-supplied text into a shell string.
"""
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from core.executor import run_command_full, run_pkexec_full, INSTALL_TIMEOUT

PLANNED = "planned"
BACKUP_CREATED = "backup_created"
BACKUP_FAILED = "backup_failed"
APPLYING = "applying"
APPLIED = "applied"
VERIFYING = "verifying"
VERIFIED = "verified"
VERIFICATION_FAILED = "verification_failed"
ROLLING_BACK = "rolling_back"
ROLLED_BACK = "rolled_back"
ROLLBACK_FAILED = "rollback_failed"
NOTHING_TO_ROLL_BACK = "nothing_to_roll_back"
CANCELLED = "cancelled"
UNSUPPORTED = "unsupported"
UNDETERMINED = "undetermined"

_BACKUP_DIR = "/var/lib/mg-linux-toolbox/repo-backups"


@dataclass
class TransactionResult:
    state: str
    ok: bool
    friendly_message: str = ""
    technical_detail: str = ""
    backup_paths: dict = field(default_factory=dict)   # original_path -> backup_path
    files_involved: list = field(default_factory=list)
    rollback_available: bool = False


def _backup_path_for(original_path: str) -> str:
    safe_name = original_path.strip("/").replace("/", "__")
    return os.path.join(_BACKUP_DIR, f"{safe_name}.{int(time.time())}.bak")


class BackupFailedError(Exception):
    """Raised by backup_file() when the source file exists but the
    privileged backup copy itself failed (mkdir or cp denied/errored) —
    distinct from the file simply not existing yet (which returns None,
    not an error: see docstring below). run_transaction() catches this
    and aborts BEFORE calling apply_fn, so a failed backup can never be
    followed by a real modification with nothing to restore it from."""


def backup_file(path: str, job=None) -> "str | None":
    """Privileged copy of `path` to a fixed backup directory, via a
    single fixed argv (`cp -a`) — never a shell string. Returns the
    backup path, or None if the source file doesn't exist yet (nothing
    to back up is not an error: the file may be newly created by this
    transaction). Raises BackupFailedError if the file DOES exist but
    the privileged copy itself failed."""
    check = run_command_full(["test", "-e", path])
    if not check.ok:
        return None
    backup_path = _backup_path_for(path)
    mkdir_result = run_pkexec_full(["mkdir", "-p", _BACKUP_DIR], timeout=INSTALL_TIMEOUT, job=job)
    if not mkdir_result.ok:
        raise BackupFailedError(f"could not create backup directory: {mkdir_result.technical_detail()}")
    result = run_pkexec_full(["cp", "-a", path, backup_path], timeout=INSTALL_TIMEOUT, job=job)
    if not result.ok:
        raise BackupFailedError(f"could not back up {path}: {result.technical_detail()}")
    return backup_path


def restore_file(original_path: str, backup_path: str, job=None) -> bool:
    result = run_pkexec_full(["cp", "-a", backup_path, original_path], timeout=INSTALL_TIMEOUT, job=job)
    return result.ok


def remove_file(path: str, job=None) -> bool:
    """Used to roll back a transaction that CREATED a new file (no
    backup exists because the file didn't exist before)."""
    result = run_pkexec_full(["rm", "-f", path], timeout=INSTALL_TIMEOUT, job=job)
    return result.ok


def _paths_equal(path_a: str, path_b: str) -> "bool | None":
    """Filesystem-state comparison used to prove rollback changed state.

    True means identical, False means materially different, and None
    means the comparison itself could not be completed safely.
    """
    try:
        stat_a = os.stat(path_a, follow_symlinks=False)
        stat_b = os.stat(path_b, follow_symlinks=False)
    except OSError:
        return None
    metadata_a = (stat_a.st_mode, stat_a.st_uid, stat_a.st_gid,
                  stat_a.st_size, stat_a.st_mtime_ns)
    metadata_b = (stat_b.st_mode, stat_b.st_uid, stat_b.st_gid,
                  stat_b.st_size, stat_b.st_mtime_ns)
    if metadata_a != metadata_b:
        return False
    result = run_command_full(["cmp", "-s", path_a, path_b])
    if result.ok:
        return True
    if result.returncode == 1:
        return False
    return None


def run_transaction(
    files_involved: list,
    apply_fn: Callable[[], bool],
    verify_fn: Callable[[], bool],
    created_files: Optional[list] = None,
    job=None,
) -> TransactionResult:
    """Generic backup -> apply -> verify -> (rollback on failure) flow.

    files_involved: existing files that will be modified — each is
        backed up before apply_fn runs.
    created_files: paths apply_fn may CREATE from scratch (no backup
        possible/needed) — rolled back by deletion, not restore.
    apply_fn: performs the real privileged change, returns True/False.
    verify_fn: re-reads real state afterward (never trusts apply_fn's
        own return value alone) and returns True/False.
    """
    created_files = created_files or []
    result = TransactionResult(state=PLANNED, ok=False, files_involved=list(files_involved) + list(created_files))

    backups = {}
    try:
        for path in files_involved:
            backup_path = backup_file(path, job=job)
            if backup_path is not None:
                backups[path] = backup_path
    except BackupFailedError as exc:
        # A mandatory backup failed — stop here, before apply_fn ever
        # runs. No file has been touched yet, so there is nothing to
        # roll back (and nothing above the ROLLED_BACK/ROLLBACK_FAILED
        # states would honestly describe this: the transaction never
        # got that far).
        result.state = BACKUP_FAILED
        result.friendly_message = "repo_tx_backup_failed"
        result.technical_detail = str(exc)
        return result
    result.backup_paths = backups
    result.state = BACKUP_CREATED

    result.state = APPLYING
    try:
        applied = bool(apply_fn())
    except Exception as exc:
        result.state = VERIFICATION_FAILED
        result.technical_detail = str(exc)
        _rollback(result, backups, created_files, job)
        return result

    if not applied:
        result.state = VERIFICATION_FAILED
        result.friendly_message = "repo_tx_apply_failed"
        _rollback(result, backups, created_files, job)
        return result
    result.state = APPLIED

    result.state = VERIFYING
    try:
        verified = bool(verify_fn())
    except Exception as exc:
        verified = False
        result.technical_detail = str(exc)

    if verified:
        result.state = VERIFIED
        result.ok = True
        result.rollback_available = bool(backups) or bool(created_files)
        return result

    result.state = VERIFICATION_FAILED
    result.friendly_message = "repo_tx_verification_failed"
    _rollback(result, backups, created_files, job)
    return result


def _rollback(result: TransactionResult, backups: dict, created_files: list, job) -> None:
    # A path merely declared in created_files is not proof that apply_fn
    # actually created it.  In particular, an apply failure may happen
    # before the first write; `rm -f` would then return success for the
    # still-absent path and make us falsely report ROLLED_BACK.  Only
    # existing filesystem objects (including broken symlinks) are real
    # deletion targets.
    created_targets = [path for path in created_files if os.path.lexists(path)]
    if not backups and not created_targets:
        # Nothing was actually backed up or created — there is nothing
        # to roll back to. ROLLED_BACK must never be reported here: it
        # would falsely imply a file was restored when none was.
        result.state = NOTHING_TO_ROLL_BACK
        result.ok = False
        return
    attempted = False
    changed = False
    ok = True
    for original_path, backup_path in backups.items():
        before = _paths_equal(original_path, backup_path)
        if before is True:
            # The file already equals its backup: copying it again would
            # be a successful command, but not a real rollback.
            continue
        attempted = True
        if not restore_file(original_path, backup_path, job=job):
            ok = False
            continue
        after = _paths_equal(original_path, backup_path)
        if before is False and after is True:
            changed = True
        else:
            # A successful cp without a verifiable state change is not a
            # successful rollback.
            ok = False
    for created_path in created_targets:
        attempted = True
        if not remove_file(created_path, job=job) or os.path.lexists(created_path):
            ok = False
        else:
            changed = True
    if not attempted:
        result.state = NOTHING_TO_ROLL_BACK
    elif not ok or not changed:
        result.state = ROLLBACK_FAILED
    else:
        result.state = ROLLED_BACK
    result.ok = False
