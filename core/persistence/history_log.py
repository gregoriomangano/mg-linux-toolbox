"""
Human-readable operations history, shown in the future "Registro e
ripristino" page. Deliberately separate from rollback_store's
/var/lib state: this is cosmetic/informational only and never used to
decide how to restore anything.

Lives under $XDG_STATE_HOME/mg-linux-toolbox/history.json, falling back
to ~/.local/state/mg-linux-toolbox/history.json — written by the
unprivileged GUI process itself, no root needed.

Never stores: passwords, IPs, MACs, SSIDs, tokens, serials, personal
paths.
"""
import os

from core.persistence.atomic_io import read_json, write_json_atomic

MAX_ENTRIES = 500


def _state_home() -> str:
    return os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")


def history_path() -> str:
    return os.path.join(_state_home(), "mg-linux-toolbox", "history.json")


def append(entry: dict):
    """entry should already be scrubbed of any sensitive data by the caller."""
    path = history_path()
    data = read_json(path, default={"entries": []})
    entries = data.get("entries", [])
    entries.append(entry)
    data["entries"] = entries[-MAX_ENTRIES:]
    write_json_atomic(path, data, mode=0o600)


def all_entries() -> list:
    return read_json(history_path(), default={"entries": []}).get("entries", [])


def clear():
    write_json_atomic(history_path(), {"entries": []}, mode=0o600)
