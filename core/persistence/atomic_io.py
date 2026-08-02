"""
Small shared helpers for writing files atomically and symlink-safely.
Used both by the privileged writer (root-owned files under /var/lib) and
by the user-side history log (files under XDG_STATE_HOME).
"""
import json
import os
import shutil


def atomic_write_text(path: str, content: str, mode: int = 0o644):
    """
    Writes `content` to `path` atomically (temp file + os.replace) and
    refuses to follow a symlink at the destination, so a pre-planted
    symlink at `path` can't redirect the write somewhere else.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    if os.path.islink(path):
        raise RuntimeError(f"Refusing to write through a symlink: {path}")

    tmp_path = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        os.chmod(path, mode)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def backup_file(path: str):
    """Copies `path` to `path.bak` before we modify it, if it exists."""
    if os.path.exists(path) and not os.path.islink(path):
        shutil.copy2(path, f"{path}.bak")


def read_json(path: str, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def write_json_atomic(path: str, data, mode: int = 0o644):
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode)
