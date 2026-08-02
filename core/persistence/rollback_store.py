"""
The state needed to restore a kernel-feature change later: initial value,
last value WE applied, when, temporary/persistent, kernel version, and an
optional device id. This is the "system state" the spec asks to keep
separate from the human-readable history log.

Lives at /var/lib/mg-linux-toolbox/state.json — root:root, mode 0644
(world-readable so the unprivileged GUI can *display* it, writable only
by the privileged writer running as root). The GUI process only ever
calls get()/all() here; put() is only ever called from inside
core/priv_writer.py while it's running as root via pkexec.
"""
import os
import platform
from dataclasses import asdict, dataclass
from typing import Optional

from core.persistence.atomic_io import read_json, write_json_atomic, backup_file

DEFAULT_STATE_PATH = "/var/lib/mg-linux-toolbox/state.json"


@dataclass
class FeatureRecord:
    feature_id: str
    initial_value: object
    last_applied_value: object = None
    last_applied_at: Optional[str] = None
    mode: Optional[str] = None          # "temporary" | "persistent"
    kernel_version: Optional[str] = None
    device_id: Optional[str] = None
    # ZRAM-specific ownership proof: only ever set by ZramWriter for a
    # device it actually created itself — used to tell a Toolbox-owned
    # ZRAM apart from one some other component set up, so we never touch
    # a device we didn't create (see core/kernel_features/memory.py).
    size_bytes: Optional[int] = None
    algorithm: Optional[str] = None
    priority: Optional[int] = None


class JsonStateStore:
    """Generic get/put over a JSON file of {key: FeatureRecord-as-dict}."""

    def __init__(self, path: str = DEFAULT_STATE_PATH):
        self.path = path

    def get(self, key: str) -> Optional[FeatureRecord]:
        data = read_json(self.path, default={})
        raw = data.get(key)
        return FeatureRecord(**raw) if raw else None

    def all(self) -> dict:
        data = read_json(self.path, default={})
        return {k: FeatureRecord(**v) for k, v in data.items()}

    def put(self, record: FeatureRecord):
        """Root-only in practice: /var/lib/mg-linux-toolbox is root-owned."""
        data = read_json(self.path, default={})
        if record.kernel_version is None:
            record.kernel_version = platform.release()
        data[record.feature_id] = asdict(record)
        backup_file(self.path)
        write_json_atomic(self.path, data, mode=0o644)

    def delete(self, key: str):
        data = read_json(self.path, default={})
        if key in data:
            del data[key]
            backup_file(self.path)
            write_json_atomic(self.path, data, mode=0o644)


def default_state_store() -> JsonStateStore:
    return JsonStateStore(DEFAULT_STATE_PATH)
