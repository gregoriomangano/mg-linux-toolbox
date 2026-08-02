"""
Unprivileged-side client for the privileged writer.

CRITICAL security property: this NEVER sends a filesystem path to the
privileged side. It only sends (feature_id, action, value, device_id) —
plain, short strings. The privileged script (core/priv_writer.py) is the
only place that knows which real /proc or /sys path a feature_id maps to,
and it validates action/value itself before touching anything. The GUI
cannot ask for an arbitrary path to be written, even one under /proc or
/sys.
"""
import json
import os
import subprocess

from core.kernel_features.base import OpResult
from core.persistence import history_store as _history

_PRIV_WRITER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "priv_writer.py"
)

# feature_id prefix -> which page's "Cronologia attività" it belongs to.
# Checked in order, longest/most-specific first within a family.
_PAGE_BY_PREFIX = [
    # Most specific first: TCP congestion control's FeatureRow lives on
    # the Kernel page (grouped under "Rete kernel"), not the Network
    # page, even though its feature id starts with "network.".
    ("network.tcp_congestion_control", "kernel"),
    ("cpu.", "kernel"), ("memory.", "kernel"), ("storage.", "kernel"), ("monitoring.", "kernel"),
    ("battery.", "performance"), ("device_power", "performance"),
    ("audio.", "audio"),
    ("virt.", "virt"),
    ("apparmor.", "security"), ("selinux.", "security"), ("updates.", "security"),
    # dmesg_restrict/kptr_restrict/ptrace_scope/protected_paths live on
    # the Kernel page (grouped under "Sicurezza kernel"), not the
    # Security page — their feature ids start with "security." but the
    # card itself is on "kernel".
    ("security.", "kernel"),
    ("network.", "network"), ("dns.", "network"),
]

_ENTRY_TYPE_BY_ACTION = {
    "apply_temporary": _history.TEMPORARY_CHANGE,
    "apply_persistent": _history.PERMANENT_CHANGE,
    "restore": _history.RESTORE,
    "enable": _history.ACTIVATION,
    "disable": _history.DEACTIVATION,
    "install": _history.INSTALLATION,
    "configure": _history.CONFIGURATION,
    "verify": _history.VERIFICATION,
}

# Actions after which "Ripristina questa modifica" makes sense from the
# history page — i.e. everything except a restore/verify entry itself.
_ROLLBACK_ELIGIBLE_ACTIONS = {"apply_temporary", "apply_persistent", "enable", "disable", "configure"}


def _infer_page(feature_id: str) -> str:
    for prefix, page in _PAGE_BY_PREFIX:
        if feature_id.startswith(prefix):
            return page
    return "other"


def _record_key(feature_id: str, device_id: "str | None") -> str:
    return f"{feature_id}:{device_id}" if device_id else feature_id


class PrivilegedWriter:
    def __init__(self, priv_writer_path: str = _PRIV_WRITER_PATH, timeout: int = 15,
                 history_store: "_history.HistoryStore | None" = None):
        self.priv_writer_path = priv_writer_path
        self.timeout = timeout
        self._history_store = history_store

    def execute(self, feature_id: str, action: str, value=None, device_id: str = None,
                force: bool = False, record_history: bool = True) -> OpResult:
        previous_value = self._read_previous_value(feature_id, device_id) if record_history else None

        args = ["pkexec", "python3", self.priv_writer_path, feature_id, action]
        if value is None:
            args.append("")
        elif isinstance(value, (dict, list)):
            # Writers that take structured values (e.g. battery thresholds,
            # audio power-save seconds+controller) parse this side with
            # json.loads() — str(dict) would produce Python repr (single
            # quotes, True/False/None) instead of valid JSON.
            args.append(json.dumps(value))
        else:
            args.append(str(value))
        args.append(device_id or "")
        args.append("1" if force else "0")
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            result = OpResult(False, friendly_message="kf_err_generic",
                               technical_detail="privileged helper timed out")
            if record_history:
                self._record(feature_id, action, value, device_id, result, previous_value)
            return result
        except FileNotFoundError as e:
            result = OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
            if record_history:
                self._record(feature_id, action, value, device_id, result, previous_value)
            return result

        try:
            payload = json.loads(proc.stdout.strip() or "{}")
        except json.JSONDecodeError:
            payload = {}

        if proc.returncode != 0 and "ok" not in payload:
            result = OpResult(False, friendly_message="kf_err_generic",
                               technical_detail=(proc.stderr or proc.stdout or "").strip())
        else:
            result = OpResult(
                ok=bool(payload.get("ok")),
                value=payload.get("value"),
                friendly_message=payload.get("friendly_message", ""),
                technical_detail=payload.get("technical_detail", ""),
                reboot_required=bool(payload.get("reboot_required", False)),
            )

        if record_history:
            self._record(feature_id, action, value, device_id, result, previous_value)
        return result

    def _read_previous_value(self, feature_id: str, device_id: "str | None"):
        """Best-effort: the state file is world-readable but may not exist
        yet (first-ever operation on this feature) — that's not an error,
        it just means there's no "previous value" to show."""
        try:
            from core.persistence.rollback_store import default_state_store
            rec = default_state_store().get(_record_key(feature_id, device_id))
        except Exception:
            return None
        if rec is None:
            return None
        return rec.last_applied_value if rec.last_applied_value is not None else rec.initial_value

    def _record(self, feature_id: str, action: str, value, device_id, result: OpResult, previous_value):
        """History logging must never be able to break the real privileged
        operation whose outcome it's just recording."""
        try:
            entry_type = _ENTRY_TYPE_BY_ACTION.get(action, _history.CONFIGURATION)
            if not result.ok:
                entry_type = _history.ERROR
            elif result.reboot_required:
                entry_type = _history.REBOOT_REQUIRED

            from core.distro import get_context
            ctx = get_context()

            store = self._history_store or _history.default_history_store()
            store.record(_history.HistoryEntry(
                page=_infer_page(feature_id),
                feature_id=feature_id,
                device_id=device_id,
                entry_type=entry_type,
                result="ok" if result.ok else "failed",
                previous_value=previous_value,
                new_value=value,
                verified_value=result.value,
                friendly_message=result.friendly_message,
                technical_detail=result.technical_detail,
                distro_id=ctx.id,
                distro_provider=ctx.package_manager,
                mode="temporary" if action == "apply_temporary" else
                     ("permanent" if action == "apply_persistent" else None),
                reboot_required=result.reboot_required,
                rollback_available=result.ok and action in _ROLLBACK_ELIGIBLE_ACTIONS,
            ))
        except Exception:
            pass


def default_privileged_writer() -> PrivilegedWriter:
    return PrivilegedWriter()
