"""
Manages ONLY /etc/sysctl.d/90-mg-linux-toolbox.conf — never any other
file. Root-only in practice (called from core/priv_writer.py while it
runs as root via pkexec).

Rules from the spec:
- one file, owned only by us;
- atomic write (temp file + rename), symlink-safe;
- backup before every change;
- no duplicate keys;
- only touches keys we manage (never re-writes lines belonging to
  something else that might coexist in the same file).
"""
import re

from core.persistence.atomic_io import atomic_write_text, backup_file

SYSCTL_FILE = "/etc/sysctl.d/90-mg-linux-toolbox.conf"
HEADER = "# Gestito da M.G Linux Toolbox — non modificare a mano.\n"

# Only these keys may ever be written to this file. Anything else is
# rejected before it ever reaches a file write.
KNOWN_KEYS = {
    "vm.swappiness", "vm.page-cluster", "net.ipv4.tcp_congestion_control",
    "kernel.dmesg_restrict", "kernel.kptr_restrict", "kernel.yama.ptrace_scope",
}


def _read_lines() -> list:
    try:
        with open(SYSCTL_FILE) as f:
            return f.readlines()
    except FileNotFoundError:
        return []


def read_key(key: str) -> str | None:
    for line in _read_lines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip()
    return None


def write_key(key: str, value: str):
    if key not in KNOWN_KEYS:
        raise ValueError(f"Refusing to manage unknown sysctl key: {key}")

    lines = _read_lines()
    out = []
    written = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        k, _, _ = stripped.partition("=")
        if k.strip() == key:
            if not written:
                out.append(f"{key} = {value}\n")
                written = True
            # else: drop duplicate occurrence of the same key
        else:
            out.append(line)

    if not written:
        if not out or not out[0].startswith("#"):
            out.insert(0, HEADER)
        out.append(f"{key} = {value}\n")

    backup_file(SYSCTL_FILE)
    atomic_write_text(SYSCTL_FILE, "".join(out), mode=0o644)


def remove_key(key: str):
    lines = _read_lines()
    if not lines:
        return
    out = [line for line in lines
           if not (("=" in line) and line.split("=")[0].strip() == key)]
    backup_file(SYSCTL_FILE)
    if all(line.strip().startswith("#") or not line.strip() for line in out):
        # nothing meaningful left — remove the file entirely
        import os
        if os.path.exists(SYSCTL_FILE):
            os.remove(SYSCTL_FILE)
    else:
        atomic_write_text(SYSCTL_FILE, "".join(out), mode=0o644)
