"""
Central, queryable operations history for M.G Linux Toolbox — the
"Cronologia attività" section of the "Cronologia e ripristino" page.

Every privileged operation that goes through
`core.persistence.priv_client.PrivilegedWriter.execute()` (every kernel
feature, and every virtualization/AppArmor/SELinux/updates writer
dispatched the same way through `core/priv_writer.py`'s FEATURE_WRITERS)
is recorded here automatically by that one call site — new features get
history for free the moment their writer goes through the same choke
point every existing writer already uses. See priv_client.py.

Storage: SQLite at ~/.local/share/mg-linux-toolbox/history.db
(XDG_DATA_HOME), written by the unprivileged GUI process itself — no
root needed, since this only ever records what the GUI already knows
(feature_id, requested value, distro context) plus what the privileged
helper returns over its normal JSON result (never a filesystem path).

Never stores: passwords, tokens/API keys, Wi-Fi SSIDs, MAC addresses,
hardware serial numbers, or personal file paths — see _sanitize() below,
which redacts by *key name*, not by guessing at value shape.
"""
import json
import os
import re
import sqlite3
import threading
import uuid as uuid_mod
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from core.persistence.atomic_io import write_json_atomic

ACTIVATION = "activation"
DEACTIVATION = "deactivation"
INSTALLATION = "installation"
CONFIGURATION = "configuration"
TEMPORARY_CHANGE = "temporary_change"
PERMANENT_CHANGE = "permanent_change"
ERROR = "error"
VERIFICATION = "verification"
REBOOT_REQUIRED = "reboot_required"
RESTORE = "restore"

ENTRY_TYPES = {
    ACTIVATION, DEACTIVATION, INSTALLATION, CONFIGURATION, TEMPORARY_CHANGE,
    PERMANENT_CHANGE, ERROR, VERIFICATION, REBOOT_REQUIRED, RESTORE,
}

# Redacts by key name, recursively, regardless of what the value looks
# like — this is what lets a brand-new writer's structured payload stay
# safe without this module needing to know its shape in advance.
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|ssid|mac[_-]?address|^mac$|serial)",
    re.IGNORECASE,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    transaction_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    page TEXT NOT NULL,
    feature_id TEXT NOT NULL,
    device_id TEXT,
    entry_type TEXT NOT NULL,
    previous_value TEXT,
    new_value TEXT,
    verified_value TEXT,
    result TEXT NOT NULL,
    friendly_message TEXT,
    technical_detail TEXT,
    distro_id TEXT,
    distro_provider TEXT,
    mode TEXT,
    reboot_required INTEGER NOT NULL DEFAULT 0,
    rollback_available INTEGER NOT NULL DEFAULT 0,
    restored_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_feature ON history(feature_id);
CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history(timestamp);
CREATE INDEX IF NOT EXISTS idx_history_page ON history(page);
"""


def data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def db_path() -> str:
    return os.path.join(data_home(), "mg-linux-toolbox", "history.db")


def _sanitize(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k] = "***" if _SENSITIVE_KEY_PATTERN.search(str(k)) else _sanitize(v)
        return out
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def _serialize(value) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(_sanitize(value))


def _deserialize(raw: Optional[str]):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


@dataclass
class HistoryEntry:
    page: str
    feature_id: str
    entry_type: str
    result: str  # "ok" | "failed"
    transaction_id: str = field(default_factory=lambda: uuid_mod.uuid4().hex)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    device_id: Optional[str] = None
    previous_value: object = None
    new_value: object = None
    verified_value: object = None
    friendly_message: str = ""
    technical_detail: str = ""
    distro_id: str = ""
    distro_provider: str = ""
    mode: Optional[str] = None  # "temporary" | "permanent"
    reboot_required: bool = False
    rollback_available: bool = False
    restored_at: Optional[str] = None

    def __post_init__(self):
        if self.entry_type not in ENTRY_TYPES:
            raise ValueError(f"unknown history entry_type: {self.entry_type!r}")


class HistoryStore:
    def __init__(self, path: str = None):
        self.path = path or db_path()
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, entry: HistoryEntry) -> str:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO history (transaction_id, timestamp, page, feature_id, device_id, "
                "entry_type, previous_value, new_value, verified_value, result, friendly_message, "
                "technical_detail, distro_id, distro_provider, mode, reboot_required, "
                "rollback_available, restored_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry.transaction_id, entry.timestamp, entry.page, entry.feature_id, entry.device_id,
                    entry.entry_type, _serialize(entry.previous_value), _serialize(entry.new_value),
                    _serialize(entry.verified_value), entry.result, entry.friendly_message,
                    entry.technical_detail, entry.distro_id, entry.distro_provider, entry.mode,
                    int(entry.reboot_required), int(entry.rollback_available), entry.restored_at,
                ),
            )
            conn.commit()
        return entry.transaction_id

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["previous_value"] = _deserialize(d["previous_value"])
        d["new_value"] = _deserialize(d["new_value"])
        d["verified_value"] = _deserialize(d["verified_value"])
        d["reboot_required"] = bool(d["reboot_required"])
        d["rollback_available"] = bool(d["rollback_available"])
        return d

    def get(self, transaction_id: str) -> Optional[dict]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM history WHERE transaction_id = ?", (transaction_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def query(self, *, search: str = None, page: str = None, entry_type: str = None,
              result: str = None, limit: int = 200) -> list:
        clauses, params = [], []
        if page:
            clauses.append("page = ?")
            params.append(page)
        if entry_type:
            clauses.append("entry_type = ?")
            params.append(entry_type)
        if result:
            clauses.append("result = ?")
            params.append(result)
        if search:
            clauses.append("(feature_id LIKE ? OR friendly_message LIKE ? OR technical_detail LIKE ?)")
            like = f"%{search}%"
            params += [like, like, like]
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM history {where} ORDER BY timestamp DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def mark_restored(self, transaction_id: str, at: str = None) -> bool:
        at = at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock, closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE history SET restored_at = ? WHERE transaction_id = ?", (at, transaction_id)
            )
            conn.commit()
            return cur.rowcount > 0

    def export_json(self, path: str):
        entries = self.query(limit=1_000_000)
        write_json_atomic(path, {"entries": entries}, mode=0o600)

    def clear(self):
        with self._lock, closing(self._connect()) as conn:
            conn.execute("DELETE FROM history")
            conn.commit()


def record_operation(page: str, feature_id: str, entry_type: str, ok: bool, **kwargs) -> str:
    """
    Convenience for callers that don't go through
    PrivilegedWriter.execute() — KVM/IOMMU/VFIO setup actions run plain
    pkexec commands directly (core/virt_setup.py, core/bootloader_iommu.py,
    core/vfio_setup.py), not a KernelFeature writer, so they can't get
    history "for free" the way every KernelFeature-backed writer does.
    Same distro-context stamping, same store, just a smaller call site.
    """
    from core.distro import get_context
    ctx = get_context()
    entry = HistoryEntry(
        page=page, feature_id=feature_id, entry_type=entry_type,
        result="ok" if ok else "failed",
        distro_id=ctx.id, distro_provider=ctx.package_manager,
        **kwargs,
    )
    return default_history_store().record(entry)


_default_store = None
_default_store_lock = threading.Lock()


def default_history_store() -> HistoryStore:
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = HistoryStore()
    return _default_store
