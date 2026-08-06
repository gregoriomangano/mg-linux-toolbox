"""State tracking for Gaming Pack installs performed by the Toolbox."""
import os
import time

from core.persistence.atomic_io import read_json, write_json_atomic


def _state_home() -> str:
    return os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")


def state_path() -> str:
    return os.path.join(_state_home(), "mg-linux-toolbox", "gaming_pack.json")


def load_state() -> dict:
    data = read_json(state_path(), default={})
    if not isinstance(data, dict):
        return {"records": {}}
    records = data.get("records")
    if not isinstance(records, dict):
        data["records"] = {}
    return data


def save_state(data: dict):
    os.makedirs(os.path.dirname(state_path()), exist_ok=True)
    write_json_atomic(state_path(), data, mode=0o600)


def get_record(component_id: str) -> "dict | None":
    return load_state().get("records", {}).get(component_id)


def record_install(profile, component_id: str, installed_packages: list, preexisting_packages: list,
                   command: list, result: str = "installed"):
    data = load_state()
    data["records"][component_id] = {
        "updated_at": int(time.time()),
        "distribution": profile.distro_pretty_name,
        "family": profile.family,
        "component": component_id,
        "installed_packages": list(installed_packages),
        "preexisting_packages": list(preexisting_packages),
        "command": list(command),
        "result": result,
    }
    save_state(data)


def clear_records(component_ids: list):
    data = load_state()
    records = data.get("records", {})
    for component_id in component_ids:
        records.pop(component_id, None)
    save_state(data)
