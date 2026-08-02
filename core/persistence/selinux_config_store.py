"""
Manages ONLY the SELINUX= key in /etc/selinux/config — never any other
line in that file (SELINUXTYPE=, the policy type, is never touched).
Root-only in practice (called from core/priv_writer.py while it runs
as root via pkexec). Same rules as sysctl_store.py: atomic write,
backup before every change, only the one key we manage.

Deliberately only ever writes "enforcing" or "permissive" — never
"disabled". Disabling SELinux entirely needs a reboot and behaves very
differently (no /sys/fs/selinux at all afterwards); that's out of
scope for a simple enforcing/permissive toggle and is refused here as
a matter of policy, not just validation.
"""
from core.persistence.atomic_io import atomic_write_text, backup_file

SELINUX_CONFIG_FILE = "/etc/selinux/config"
HEADER_COMMENT_PREFIX = "#"

ALLOWED_VALUES = {"enforcing", "permissive"}


def _read_lines() -> list:
    try:
        with open(SELINUX_CONFIG_FILE) as f:
            return f.readlines()
    except FileNotFoundError:
        return []


def read_mode() -> "str | None":
    for line in _read_lines():
        stripped = line.strip()
        if stripped.startswith(HEADER_COMMENT_PREFIX) or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == "SELINUX":
            return value.strip()
    return None


def write_mode(value: str):
    if value not in ALLOWED_VALUES:
        raise ValueError(f"Refusing to write unsupported SELINUX mode: {value!r}")

    lines = _read_lines()
    out = []
    written = False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(HEADER_COMMENT_PREFIX) and "=" in stripped:
            key, _, _ = stripped.partition("=")
            if key.strip() == "SELINUX":
                if not written:
                    out.append(f"SELINUX={value}\n")
                    written = True
                continue
        out.append(line)

    if not written:
        out.append(f"SELINUX={value}\n")

    backup_file(SELINUX_CONFIG_FILE)
    atomic_write_text(SELINUX_CONFIG_FILE, "".join(out), mode=0o644)
