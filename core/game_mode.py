"""
Temporary "Modalità Gioco" profile — stacks together already-verified,
individually-tested KernelFeature applies (Turbo Boost, Governor, EPP,
ACPI Platform Profile) plus the active power-profile daemon, only for
whichever of these genuinely exist and aren't already at the gaming
target. Never claims more FPS — just states what it changed.

Rollback reuses each underlying feature's OWN restore() (already tested
by its own KernelFeature suite) instead of a second, parallel undo
mechanism — only a short list of "what did we touch" needs to be
remembered here, in a plain state file (not real system state).
"""
import os

from core.persistence.atomic_io import read_json, write_json_atomic
from core.kernel_features.registry import register
from core.kernel_features.cpu import TurboBoostFeature, GovernorFeature, EPPFeature
from core.kernel_features.battery import PlatformProfileFeature
from core.kernel_features.base import SupportStatus
from core import power_providers

_FEATURE_CLASSES = {
    "cpu.turbo_boost": TurboBoostFeature,
    "cpu.governor": GovernorFeature,
    "cpu.epp": EPPFeature,
    "battery.platform_profile": PlatformProfileFeature,
}


def _state_home() -> str:
    return os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")


def state_path() -> str:
    return os.path.join(_state_home(), "mg-linux-toolbox", "game_mode.json")


def is_active() -> bool:
    return bool(read_json(state_path(), default={}).get("touched"))


def _supported(feature) -> bool:
    return feature.probe() in (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_PERSISTENT)


def _feature_for(feature_id: str):
    return register(_FEATURE_CLASSES[feature_id]())


def plan() -> list:
    """Only real, needed changes — nothing already at the gaming target
    is included, so "quante modifiche" is always genuine."""
    changes = []

    turbo = _feature_for("cpu.turbo_boost")
    if _supported(turbo):
        cur = turbo.read_current()
        if cur.ok and cur.value is False:
            changes.append({"kind": "kernel_feature", "id": "cpu.turbo_boost", "target": True,
                             "label_key": "game_mode_change_turbo"})

    governor = _feature_for("cpu.governor")
    if _supported(governor) and "performance" in (governor.read_available() or []):
        cur = governor.read_current()
        if cur.ok and cur.value != "performance":
            changes.append({"kind": "kernel_feature", "id": "cpu.governor", "target": "performance",
                             "label_key": "game_mode_change_governor"})

    epp = _feature_for("cpu.epp")
    if _supported(epp) and "performance" in (epp.read_available() or []):
        cur = epp.read_current()
        if cur.ok and cur.value != "performance":
            changes.append({"kind": "kernel_feature", "id": "cpu.epp", "target": "performance",
                             "label_key": "game_mode_change_epp"})

    platform = _feature_for("battery.platform_profile")
    if _supported(platform) and "performance" in (platform.read_available() or []):
        cur = platform.read_current()
        if cur.ok and cur.value != "performance":
            changes.append({"kind": "kernel_feature", "id": "battery.platform_profile", "target": "performance",
                             "label_key": "game_mode_change_platform_profile"})

    import backend.all as B
    active_provider = power_providers.resolve()["active"]
    if active_provider == "power-profiles-daemon" and B.get_power_profile() != "performance":
        changes.append({"kind": "power_profile_ppd", "previous": B.get_power_profile(), "target": "performance",
                         "label_key": "game_mode_change_power_profile"})
    elif active_provider == "system76-power" and B.get_system76_power_profile() != "performance":
        changes.append({"kind": "power_profile_system76", "previous": B.get_system76_power_profile(),
                         "target": "performance", "label_key": "game_mode_change_power_profile"})

    return changes


def _apply_one(change: dict) -> bool:
    import backend.all as B
    kind = change["kind"]
    if kind == "kernel_feature":
        return _feature_for(change["id"]).apply_temporary(change["target"]).ok
    if kind == "power_profile_ppd":
        B.set_power_profile(change["target"])
        return B.get_power_profile() == change["target"]
    if kind == "power_profile_system76":
        B.set_system76_power_profile(change["target"])
        return B.get_system76_power_profile() == change["target"]
    return False


def _restore_one(change: dict):
    import backend.all as B
    kind = change["kind"]
    if kind == "kernel_feature":
        _feature_for(change["id"]).restore(force=True)
    elif kind == "power_profile_ppd":
        B.set_power_profile(change["previous"])
    elif kind == "power_profile_system76":
        B.set_system76_power_profile(change["previous"])


def activate(changes: list):
    """Applies each planned change in order; on any single failure, rolls
    back everything already applied (never leaves a half-applied
    profile) and returns (False, failed_change)."""
    touched = []
    for change in changes:
        if not _apply_one(change):
            for done in reversed(touched):
                _restore_one(done)
            return False, change
        touched.append(change)
    write_json_atomic(state_path(), {"touched": touched})
    return True, None


def deactivate() -> bool:
    state = read_json(state_path(), default={})
    for change in reversed(state.get("touched", [])):
        _restore_one(change)
    write_json_atomic(state_path(), {"touched": []})
    return True
