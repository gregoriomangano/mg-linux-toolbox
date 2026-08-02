"""
"Punto di ripristino Toolbox" — a named snapshot of every setting M.G
Linux Toolbox can genuinely read AND restore, taken across all
currently registered KernelFeature singletons plus the per-real-disk
I/O scheduler (which can't come from the global registry — see
_default_features() below for why).

Deliberately excludes anything that isn't a simple, safely-repeatable
value write: IOMMU/VFIO (bootloader/initramfs edits that need a reboot
just to verify), KVM setup (a multi-step install+service action, not a
single value), automatic-updates configuration, and anything about the
kernel package, Secure Boot, BIOS, or an unrecognized bootloader — none
of that belongs in a "restore my Toolbox settings" checkpoint, and none
of it is included here even if a future feature registers under one of
those id prefixes.

Storage: one JSON file per checkpoint under
~/.local/share/mg-linux-toolbox/checkpoints/<id>.json — unprivileged,
written by the GUI process itself. Restoring still goes through each
feature's own apply_temporary()/apply_persistent(), which is what
actually needs pkexec — this module never writes to /proc or /sys
itself.
"""
import os
import uuid as uuid_mod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from core.distro import get_context
from core.kernel_features import registry
from core.kernel_features.base import SupportStatus
from core.kernel_features.storage import IOSchedulerFeature, list_real_disks
from core.persistence.atomic_io import read_json, write_json_atomic
from core.persistence.history_store import data_home
from core.version import APP_VERSION

# See module docstring — these prefixes are never captured even if a
# feature with that id is passed in explicitly.
EXCLUDED_ID_PREFIXES = ("virt.iommu", "virt.vfio", "virt.kvm", "updates.")

APPLIED = "applied"
FAILED = "failed"
SKIPPED_MATCHING = "skipped_already_matching"
SKIPPED_UNSUPPORTED = "skipped_unsupported"


def checkpoints_dir() -> str:
    return os.path.join(data_home(), "mg-linux-toolbox", "checkpoints")


def _checkpoint_path(checkpoint_id: str) -> str:
    return os.path.join(checkpoints_dir(), f"{checkpoint_id}.json")


def _is_excluded(feature_id: str) -> bool:
    return any(feature_id.startswith(p) for p in EXCLUDED_ID_PREFIXES)


def _default_features(sys_root: str = "/sys") -> list:
    """
    registry.all_features() only has singleton features — a per-device
    feature like IOSchedulerFeature is registered once per disk under
    the exact same feature.id ("storage.io_scheduler"), so each new
    disk's register() call overwrites the previous one in that global
    dict. Real disks are therefore always enumerated fresh here instead
    of trusted from whatever happens to be registered at snapshot time.
    """
    features = [f for f in registry.all_features() if not _is_excluded(f.id)]
    features += [IOSchedulerFeature(device_id=d, sys_root=sys_root) for d, _ in list_real_disks(sys_root)]
    return features


def _extract_value(raw):
    """Bracket-notation sysfs features (I/O scheduler, THP) report
    {"available": [...], "current": "..."} from read_current() — the
    checkpoint only needs the scalar that apply_temporary() actually
    accepts back. Every other feature's value already round-trips as-is
    (bool, int, or a structured dict like battery thresholds)."""
    if isinstance(raw, dict) and "current" in raw and "available" in raw:
        return raw["current"]
    return raw


@dataclass
class CheckpointEntry:
    feature_id: str
    device_id: Optional[str]
    value: object
    mode: str  # "temporary" | "permanent" — how it was applied when snapshotted


@dataclass
class Checkpoint:
    id: str
    name: str
    created_at: str
    distro: dict
    app_version: str
    entries: list  # list[CheckpointEntry]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "distro": self.distro,
            "app_version": self.app_version,
            "entries": [e.__dict__ for e in self.entries],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Checkpoint":
        return cls(
            id=d["id"], name=d["name"], created_at=d["created_at"],
            distro=d.get("distro", {}), app_version=d.get("app_version", ""),
            entries=[CheckpointEntry(**e) for e in d.get("entries", [])],
        )


@dataclass
class RestoreStepResult:
    feature_id: str
    device_id: Optional[str]
    outcome: str
    detail: str = ""


@dataclass
class RestoreReport:
    checkpoint_id: str
    pre_restore_checkpoint_id: Optional[str]
    steps: list  # list[RestoreStepResult]

    @property
    def status(self) -> str:
        """Never "success" unless every actionable step actually applied
        — a partially-applied restore must never be reported as if it
        fully succeeded."""
        if any(s.outcome == FAILED for s in self.steps):
            return "partial" if any(s.outcome == APPLIED for s in self.steps) else "failed"
        return "success"


def create(name: str, features: list = None) -> Checkpoint:
    features = _default_features() if features is None else features
    entries = []
    for feature in features:
        if _is_excluded(feature.id):
            continue
        try:
            status = feature.probe()
        except Exception:
            continue
        if status not in (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_PERSISTENT):
            continue
        result = feature.read_current()
        if not result.ok:
            continue
        entries.append(CheckpointEntry(
            feature_id=feature.id,
            device_id=getattr(feature, "device_id", None),
            value=_extract_value(result.value),
            mode="permanent" if status == SupportStatus.SUPPORTED_PERSISTENT else "temporary",
        ))
    checkpoint = Checkpoint(
        id=uuid_mod.uuid4().hex,
        name=name,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        distro=get_context().to_dict(),
        app_version=APP_VERSION,
        entries=entries,
    )
    os.makedirs(checkpoints_dir(), exist_ok=True)
    write_json_atomic(_checkpoint_path(checkpoint.id), checkpoint.to_dict(), mode=0o600)
    return checkpoint


def list_checkpoints() -> list:
    """Summaries only (id, name, created_at, distro, how many settings)
    — cheap enough to call every time the history page is opened."""
    out = []
    try:
        names = os.listdir(checkpoints_dir())
    except OSError:
        return out
    for name in sorted(names):
        if not name.endswith(".json"):
            continue
        data = read_json(os.path.join(checkpoints_dir(), name), default=None)
        if not data:
            continue
        out.append({
            "id": data.get("id"), "name": data.get("name"),
            "created_at": data.get("created_at"), "distro": data.get("distro", {}),
            "entry_count": len(data.get("entries", [])),
        })
    out.sort(key=lambda c: c["created_at"], reverse=True)
    return out


def get(checkpoint_id: str) -> Optional[Checkpoint]:
    data = read_json(_checkpoint_path(checkpoint_id), default=None)
    return Checkpoint.from_dict(data) if data else None


def delete(checkpoint_id: str) -> bool:
    path = _checkpoint_path(checkpoint_id)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def export_checkpoint(checkpoint_id: str, dest_path: str) -> bool:
    checkpoint = get(checkpoint_id)
    if checkpoint is None:
        return False
    write_json_atomic(dest_path, checkpoint.to_dict(), mode=0o600)
    return True


def _find_feature(features: list, feature_id: str, device_id: Optional[str]):
    for f in features:
        if f.id == feature_id and getattr(f, "device_id", None) == device_id:
            return f
    return None


def plan_restore(checkpoint_id: str, features: list = None) -> list:
    """Returns only the entries whose stored value actually differs from
    the current one — "cosa cambierà", never a no-op step. Each item:
    {feature_id, device_id, mode, current, target}."""
    checkpoint = get(checkpoint_id)
    if checkpoint is None:
        return []
    features = _default_features() if features is None else features
    plan = []
    for entry in checkpoint.entries:
        feature = _find_feature(features, entry.feature_id, entry.device_id)
        if feature is None:
            continue
        current_result = feature.read_current()
        if not current_result.ok:
            continue
        current_value = _extract_value(current_result.value)
        if current_value == entry.value:
            continue
        plan.append({
            "feature_id": entry.feature_id, "device_id": entry.device_id,
            "mode": entry.mode, "current": current_value, "target": entry.value,
        })
    return plan


def _apply_one(feature, entry_mode: str, target_value):
    if entry_mode == "permanent":
        return feature.apply_persistent(target_value)
    return feature.apply_temporary(target_value)


def restore(checkpoint_id: str, features: list = None, auto_checkpoint: bool = True) -> RestoreReport:
    """
    1. snapshot the current state first (so restoring is itself
       reversible), unless the caller already did that;
    2. compute the plan (only real, needed changes);
    3. apply one feature at a time;
    4. verify each one by re-reading it;
    5. never report "success" if anything failed.
    """
    features = _default_features() if features is None else features
    pre_restore_id = create("Automatico prima del ripristino", features=features).id if auto_checkpoint else None

    plan = plan_restore(checkpoint_id, features=features)
    steps = []
    for step in plan:
        feature = _find_feature(features, step["feature_id"], step["device_id"])
        if feature is None:
            steps.append(RestoreStepResult(step["feature_id"], step["device_id"],
                                            SKIPPED_UNSUPPORTED, "feature not available on this machine"))
            continue
        result = _apply_one(feature, step["mode"], step["target"])
        if not result.ok:
            steps.append(RestoreStepResult(step["feature_id"], step["device_id"], FAILED,
                                            result.friendly_message or result.technical_detail))
            continue
        verify = feature.read_current()
        verified_value = _extract_value(verify.value) if verify.ok else None
        if verify.ok and verified_value == step["target"]:
            steps.append(RestoreStepResult(step["feature_id"], step["device_id"], APPLIED))
        else:
            steps.append(RestoreStepResult(step["feature_id"], step["device_id"], FAILED,
                                            "kf_err_write_mismatch"))
    return RestoreReport(checkpoint_id=checkpoint_id, pre_restore_checkpoint_id=pre_restore_id, steps=steps)
