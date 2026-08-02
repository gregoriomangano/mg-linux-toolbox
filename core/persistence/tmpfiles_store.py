"""
Manages ONLY /etc/tmpfiles.d/90-mg-linux-toolbox.conf — never any other
file. Root-only in practice (called from core/priv_writer.py while it
runs as root via pkexec).

This is the persistence mechanism for a plain sysfs value that has no
/proc/sys equivalent (so sysctl_store.py doesn't apply) — e.g. KSM's
/sys/kernel/mm/ksm/run. systemd-tmpfiles' own real "w" line type
("write this value to this path once, at boot") is the standard way to
make a one-shot sysfs write survive a reboot without a custom unit.

Same rules as sysctl_store.py: one file, atomic write (temp file +
rename), symlink-safe, backup before every change, no duplicate
entries for the same path, only touches paths we manage.
"""
import os

from core.persistence.atomic_io import atomic_write_text, backup_file

TMPFILES_FILE = "/etc/tmpfiles.d/90-mg-linux-toolbox.conf"
HEADER = "# Gestito da M.G Linux Toolbox — non modificare a mano.\n"

# Only these sysfs paths may ever be written to this file.
KNOWN_PATHS = {"/sys/kernel/mm/ksm/run"}


def _read_lines() -> list:
    try:
        with open(TMPFILES_FILE) as f:
            return f.readlines()
    except FileNotFoundError:
        return []


def _line_path(line: str) -> "str | None":
    parts = line.strip().split()
    if len(parts) >= 2 and parts[0] == "w":
        return parts[1]
    return None


def read_value(path: str) -> "str | None":
    for line in _read_lines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        parts = stripped.split(None, 6)
        if len(parts) >= 7 and parts[0] == "w" and parts[1] == path:
            return parts[6]
    return None


def write_value(path: str, value: str):
    if path not in KNOWN_PATHS:
        raise ValueError(f"Refusing to manage unknown tmpfiles path: {path}")

    lines = _read_lines()
    out = []
    written = False
    for line in lines:
        if _line_path(line) == path:
            if not written:
                out.append(f"w {path} - - - - {value}\n")
                written = True
            # else: drop duplicate occurrence of the same path
        else:
            out.append(line)

    if not written:
        if not out or not out[0].startswith("#"):
            out.insert(0, HEADER)
        out.append(f"w {path} - - - - {value}\n")

    backup_file(TMPFILES_FILE)
    atomic_write_text(TMPFILES_FILE, "".join(out), mode=0o644)


def remove_value(path: str):
    lines = _read_lines()
    if not lines:
        return
    out = [line for line in lines if _line_path(line) != path]
    backup_file(TMPFILES_FILE)
    if all(line.strip().startswith("#") or not line.strip() for line in out):
        if os.path.exists(TMPFILES_FILE):
            os.remove(TMPFILES_FILE)
    else:
        atomic_write_text(TMPFILES_FILE, "".join(out), mode=0o644)
