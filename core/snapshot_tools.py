"""
Read-only detection of system-level snapshot tools already present and
configured on this machine — Timeshift, Snapper, Btrfs (as the root
filesystem), transactional-update, and rpm-ostree.

This module NEVER installs any of these tools and never creates,
deletes, or restores a snapshot through them — it only reports what's
really there, for the "Snapshot completi del sistema" section of the
"Cronologia e ripristino" page. Listing existing snapshots (list_*
functions below) runs the tool's own real, non-destructive "list"
command; nothing here ever mutates system state.
"""
import os
import shutil

from core.executor import run_command

TIMESHIFT = "timeshift"
SNAPPER = "snapper"
BTRFS = "btrfs"
TRANSACTIONAL_UPDATE = "transactional_update"
RPM_OSTREE = "rpm_ostree"


def _timeshift_status() -> dict:
    installed = shutil.which("timeshift") is not None
    configured = installed and (
        os.path.isfile("/etc/timeshift/timeshift.json")
        or os.path.isfile("/etc/timeshift.json")
    )
    return {"tool": TIMESHIFT, "installed": installed, "configured": bool(configured)}


def _snapper_status() -> dict:
    installed = shutil.which("snapper") is not None
    configs_dir = "/etc/snapper/configs"
    configured = installed and os.path.isdir(configs_dir) and bool(os.listdir(configs_dir) if os.path.isdir(configs_dir) else [])
    return {"tool": SNAPPER, "installed": installed, "configured": bool(configured)}


def _btrfs_status(proc_root: str = "/proc") -> dict:
    """"Installed" here means the root filesystem actually is Btrfs —
    there's no separate daemon to be "installed" the way Timeshift or
    Snapper are; the kernel driver either has it mounted as / or not."""
    is_root_btrfs = False
    try:
        with open(os.path.join(proc_root, "mounts")) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "/" and parts[2] == "btrfs":
                    is_root_btrfs = True
                    break
    except OSError:
        pass
    has_tool = shutil.which("btrfs") is not None
    return {"tool": BTRFS, "installed": is_root_btrfs and has_tool, "configured": is_root_btrfs}


def _transactional_update_status() -> dict:
    installed = shutil.which("transactional-update") is not None
    return {"tool": TRANSACTIONAL_UPDATE, "installed": installed, "configured": installed}


def _rpm_ostree_status() -> dict:
    installed = shutil.which("rpm-ostree") is not None
    configured = False
    if installed:
        ok, out, _ = run_command(["rpm-ostree", "status"], timeout=5)
        configured = ok and bool(out.strip())
    return {"tool": RPM_OSTREE, "installed": installed, "configured": configured}


def detect_tools(proc_root: str = "/proc") -> list:
    """One entry per tool, in a fixed order — installed AND configured is
    what actually matters for showing "use it" controls; installed-only
    is shown too, just without a "list snapshots" action."""
    return [
        _timeshift_status(),
        _snapper_status(),
        _btrfs_status(proc_root),
        _transactional_update_status(),
        _rpm_ostree_status(),
    ]


def list_timeshift_snapshots() -> list:
    """Parses `timeshift --list` real output — one real, read-only call,
    never mutates anything. Returns [] if timeshift isn't installed/
    configured or the command fails for any reason."""
    if shutil.which("timeshift") is None:
        return []
    ok, out, _ = run_command(["timeshift", "--list"], timeout=20)
    if not ok:
        return []
    snapshots = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(("Num", "-", "No snapshots")):
            continue
        parts = [p.strip() for p in line.split("|")] if "|" in line else line.split()
        if len(parts) >= 2:
            snapshots.append(line)
    return snapshots


def list_snapper_snapshots(config: str = "root") -> list:
    """Parses `snapper -c <config> list` real output, read-only."""
    if shutil.which("snapper") is None:
        return []
    ok, out, _ = run_command(["snapper", "-c", config, "list"], timeout=20)
    if not ok:
        return []
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    return lines[2:] if len(lines) > 2 else []  # skip the two header/separator lines
