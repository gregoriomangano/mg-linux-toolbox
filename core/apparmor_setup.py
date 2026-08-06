"""
Real AppArmor actions — service on/off, reload profiles, list profiles,
per-profile enforce/complain/disable, restore. Not modeled as a
KernelFeature: AppArmor is profile-based (many independent items with
string names, not one sysfs value), so this follows the same
direct-pkexec pattern as core/virt_setup.py, with its own history
logging (core.persistence.history_store.record_operation) since it
doesn't go through core/priv_writer.py's FEATURE_WRITERS.

Per-profile mode changes are tracked in an unprivileged state file
(~/.local/state/mg-linux-toolbox/apparmor_profiles.json) so "Ripristina"
can put a profile back to the mode it was in before THIS app touched
it — never a fabricated default.
"""
import os
import re

from core.executor import run_command, run_pkexec
from core.executor import command_exists
from core.persistence import history_store as hs
from core.persistence.atomic_io import read_json, write_json_atomic

SERVICE_NAME = "apparmor"

ENFORCE = "enforce"
COMPLAIN = "complain"
DISABLED = "disabled"


def _log(entry_type: str, ok: bool, **kwargs):
    try:
        hs.record_operation("security", "apparmor.profile", entry_type, ok, **kwargs)
    except Exception:
        pass


def _state_home() -> str:
    return os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")


def state_path() -> str:
    return os.path.join(_state_home(), "mg-linux-toolbox", "apparmor_profiles.json")


def is_installed() -> bool:
    return command_exists("aa-status")


def service_active() -> bool:
    ok, out, _ = run_command(["systemctl", "is-active", SERVICE_NAME])
    return ok and out.strip() == "active"


def service_enabled() -> bool:
    ok, out, _ = run_command(["systemctl", "is-enabled", SERVICE_NAME])
    return ok and out.strip() == "enabled"


_SECTION_HEADER = re.compile(r"(\d+) profiles are in (\w+) mode")


def parse_aa_status(text: str) -> list:
    """Parses the real `aa-status` human-readable output — the section
    headers ("N profiles are in enforce/complain mode.") followed by
    one indented profile path per line — into
    [{"path","mode"}, ...]. No package/tool ever needs to be installed
    beyond apparmor-utils, which ships aa-status itself."""
    profiles = []
    current_mode = None
    for line in text.splitlines():
        header = _SECTION_HEADER.search(line)
        if header is not None:
            mode = header.group(2)
            current_mode = mode if mode in (ENFORCE, COMPLAIN) else None
            continue
        if line.startswith("   ") and current_mode is not None:
            path = line.strip()
            if path:
                profiles.append({"path": path, "mode": current_mode})
        elif line.strip() and not line.startswith(" "):
            current_mode = None  # left the profile-listing section entirely
    return profiles


def list_profiles() -> list:
    ok, out, _ = run_command(["aa-status"], timeout=15)
    if not ok:
        return []
    return parse_aa_status(out)


def _record_previous_mode(profile_path: str, mode: str):
    state = read_json(state_path(), default={})
    if profile_path not in state:  # only ever record the FIRST-seen mode, like rollback_store's initial_value
        state[profile_path] = mode
        os.makedirs(os.path.dirname(state_path()), exist_ok=True)
        write_json_atomic(state_path(), state, mode=0o600)


def enable_service() -> bool:
    run_pkexec(["systemctl", "enable", "--now", SERVICE_NAME])
    result = service_active()
    _log(hs.ACTIVATION, result)
    return result


def disable_service() -> bool:
    run_pkexec(["systemctl", "disable", "--now", SERVICE_NAME])
    result = not service_active()
    _log(hs.DEACTIVATION, result)
    return result


def reload_profiles() -> bool:
    ok, _, _ = run_pkexec(["systemctl", "reload", SERVICE_NAME])
    _log(hs.CONFIGURATION, ok)
    return ok


def enforce_profile(profile_path: str) -> bool:
    current = next((p["mode"] for p in list_profiles() if p["path"] == profile_path), None)
    if current is not None:
        _record_previous_mode(profile_path, current)
    ok, _, _ = run_pkexec(["aa-enforce", profile_path])
    _log(hs.CONFIGURATION, ok, new_value={"path": profile_path, "mode": ENFORCE})
    return ok


def complain_profile(profile_path: str) -> bool:
    current = next((p["mode"] for p in list_profiles() if p["path"] == profile_path), None)
    if current is not None:
        _record_previous_mode(profile_path, current)
    ok, _, _ = run_pkexec(["aa-complain", profile_path])
    _log(hs.CONFIGURATION, ok, new_value={"path": profile_path, "mode": COMPLAIN})
    return ok


def disable_profile(profile_path: str) -> bool:
    current = next((p["mode"] for p in list_profiles() if p["path"] == profile_path), None)
    if current is not None:
        _record_previous_mode(profile_path, current)
    ok, _, _ = run_pkexec(["aa-disable", profile_path])
    _log(hs.DEACTIVATION, ok, new_value={"path": profile_path, "mode": DISABLED})
    return ok


def restore_profile(profile_path: str) -> dict:
    """Puts a single profile back to the mode M.G Linux Toolbox first
    saw it in — never a hardcoded "enforce" default."""
    state = read_json(state_path(), default={})
    previous_mode = state.get(profile_path)
    if previous_mode is None:
        return {"ok": False, "reason": "nothing_to_restore"}

    command = {ENFORCE: "aa-enforce", COMPLAIN: "aa-complain", DISABLED: "aa-disable"}[previous_mode]
    ok, _, _ = run_pkexec([command, profile_path])
    if ok:
        del state[profile_path]
        write_json_atomic(state_path(), state, mode=0o600)
    _log(hs.RESTORE, ok, new_value={"path": profile_path, "mode": previous_mode})
    return {"ok": ok, "mode": previous_mode}
