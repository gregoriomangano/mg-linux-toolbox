"""Generic install-state tracking shared by "pack" modules (see
core/gaming_pack.py, core/video_editing_pack.py) — records which packages
the Toolbox itself installed for a given component, so they can be removed
safely later (see core/package_pack_installer.py's external-change
detection: a component is only ever offered for removal if every package
it recorded is still really installed).

gaming_pack_state.py predates this and keeps its own gaming_pack.json file
untouched — this is used by video_editing_pack (state file
video_editing_pack.json) and any future pack, one JSON file per pack name.
"""
import os
import time

from core.persistence.atomic_io import read_json, write_json_atomic


def _state_home() -> str:
    return os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")


def state_path(pack_name: str) -> str:
    return os.path.join(_state_home(), "mg-linux-toolbox", f"{pack_name}.json")


def load_state(pack_name: str) -> dict:
    data = read_json(state_path(pack_name), default={})
    if not isinstance(data, dict):
        return {"records": {}}
    records = data.get("records")
    if not isinstance(records, dict):
        data["records"] = {}
    return data


def save_state(pack_name: str, data: dict):
    os.makedirs(os.path.dirname(state_path(pack_name)), exist_ok=True)
    write_json_atomic(state_path(pack_name), data, mode=0o600)


def get_record(pack_name: str, component_id: str) -> "dict | None":
    return load_state(pack_name).get("records", {}).get(component_id)


def record_install(pack_name: str, profile, component_id: str, installed_packages: list,
                    preexisting_packages: list, command: list, result: str = "installed"):
    data = load_state(pack_name)
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
    save_state(pack_name, data)


def clear_records(pack_name: str, component_ids: list):
    data = load_state(pack_name)
    records = data.get("records", {})
    for component_id in component_ids:
        records.pop(component_id, None)
    save_state(pack_name, data)
