"""
Unprivileged-side client for the privileged helper.

CRITICAL security properties:
- This NEVER sends a filesystem path to the privileged side. It only
  sends (feature_id, action, value, device_id) — plain, short strings.
  The privileged helper is the only place that knows which real /proc,
  /sys or /etc path a feature_id maps to, and it validates action/value
  itself before touching anything.
- The privileged side is ONLY ever the system-installed helper
  (/usr/libexec/mg-linux-toolbox/mg-privileged-helper, root-owned) —
  verified for owner, group, permissions and version before every call.
  A path inside an AppImage FUSE mount (/tmp/.mount_*) is never passed
  to pkexec: root cannot traverse the user's FUSE mount (the Beta 3
  "Errno 13" bug), and a root process must never execute a file from a
  location the unprivileged user can modify.
- Development fallback: when running from a plain source checkout (no
  $APPIMAGE in the environment), core/priv_writer.py may be used
  directly so `python3 main.py` keeps working for development — but
  never when that file lives under /tmp.
"""
import json
import os
from dataclasses import dataclass
import subprocess

from core.kernel_features.base import OpResult
from core.persistence import history_store as _history
from core.privileged import helper_meta

_PRIV_WRITER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "priv_writer.py"
)

# Helper states surfaced to the UI ("Componente amministrativo").
HELPER_READY = "ready"
HELPER_MISSING = "missing"
HELPER_WRONG_OWNER = "wrong_owner"
HELPER_USER_WRITABLE = "user_writable"
HELPER_INCOMPATIBLE = "incompatible"
HELPER_UNREADABLE = "unreadable"


@dataclass
class HelperStatus:
    state: str
    path: str = ""
    version: str = ""
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.state == HELPER_READY


def _check_installed_helper(path: str) -> HelperStatus:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return HelperStatus(HELPER_MISSING, path=path)
    except OSError as e:
        return HelperStatus(HELPER_UNREADABLE, path=path, detail=str(e))
    import stat as _stat
    if not _stat.S_ISREG(st.st_mode):
        return HelperStatus(HELPER_WRONG_OWNER, path=path, detail="not a regular file")
    if st.st_uid != 0 or st.st_gid != 0:
        return HelperStatus(HELPER_WRONG_OWNER, path=path,
                            detail=f"owner {st.st_uid}:{st.st_gid}, expected 0:0")
    if st.st_mode & 0o022:
        return HelperStatus(HELPER_USER_WRITABLE, path=path,
                            detail=f"mode {oct(st.st_mode & 0o777)} is group/other writable")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(65536)
    except OSError as e:
        return HelperStatus(HELPER_UNREADABLE, path=path, detail=str(e))
    version = helper_meta.parse_marker(head, helper_meta.VERSION_MARKER) or ""
    protocol = helper_meta.parse_marker(head, helper_meta.PROTOCOL_MARKER) or ""
    if protocol != str(helper_meta.PROTOCOL_VERSION):
        return HelperStatus(HELPER_INCOMPATIBLE, path=path, version=version,
                            detail=f"protocol {protocol!r}, app expects {helper_meta.PROTOCOL_VERSION}")
    return HelperStatus(HELPER_READY, path=path, version=version)


def installed_helper_status() -> HelperStatus:
    """Status of the system-installed helper, first install dir that has
    anything; HELPER_MISSING if none does."""
    for path in helper_meta.HELPER_INSTALL_PATHS:
        status = _check_installed_helper(path)
        if status.state != HELPER_MISSING:
            return status
    return HelperStatus(HELPER_MISSING, path=helper_meta.HELPER_INSTALL_PATHS[0])


def running_from_appimage() -> bool:
    return bool(os.environ.get("APPIMAGE"))


def _dev_writer_usable() -> bool:
    """True only for a plain source checkout: never inside an AppImage
    launch, and never for a priv_writer.py that lives under /tmp."""
    if running_from_appimage():
        return False
    real = os.path.realpath(_PRIV_WRITER_PATH)
    if real.startswith("/tmp/"):
        return False
    return os.path.isfile(real)


def resolve_privileged_argv() -> "tuple[list, HelperStatus]":
    """The argv prefix to run the privileged side with, plus the helper
    status that justified it. Empty argv means privileged operations
    must be refused (portable AppImage without the installed helper, or
    a helper that failed verification)."""
    status = installed_helper_status()
    if status.usable:
        return ["pkexec", status.path], status
    if status.state == HELPER_MISSING and _dev_writer_usable():
        return ["pkexec", "python3", _PRIV_WRITER_PATH], status
    return [], status


_HELPER_STATE_MESSAGE = {
    HELPER_MISSING: "kf_err_helper_missing",
    HELPER_WRONG_OWNER: "kf_err_helper_untrusted",
    HELPER_USER_WRITABLE: "kf_err_helper_untrusted",
    HELPER_INCOMPATIBLE: "kf_err_helper_incompatible",
    HELPER_UNREADABLE: "kf_err_helper_missing",
}

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
                 history_store: "_history.HistoryStore | None" = None,
                 argv_resolver=resolve_privileged_argv):
        self.priv_writer_path = priv_writer_path
        self.timeout = timeout
        self._history_store = history_store
        self._argv_resolver = argv_resolver

    def execute(self, feature_id: str, action: str, value=None, device_id: str = None,
                force: bool = False, record_history: bool = True) -> OpResult:
        previous_value = self._read_previous_value(feature_id, device_id) if record_history else None

        argv_prefix, helper_status = self._argv_resolver()
        if not argv_prefix:
            result = OpResult(False,
                              friendly_message=_HELPER_STATE_MESSAGE.get(
                                  helper_status.state, "kf_err_helper_missing"),
                              technical_detail=f"helper state={helper_status.state} "
                                               f"path={helper_status.path} {helper_status.detail}".strip())
            if record_history:
                self._record(feature_id, action, value, device_id, result, previous_value)
            return result
        # Hard guarantee: never hand pkexec a path inside an AppImage
        # FUSE mount — root can't traverse it and the file would be
        # user-controlled anyway.
        for part in argv_prefix:
            if part.startswith("/tmp/.mount_"):
                result = OpResult(False, friendly_message="kf_err_helper_missing",
                                  technical_detail="refused to escalate a path inside the AppImage mount")
                if record_history:
                    self._record(feature_id, action, value, device_id, result, previous_value)
                return result

        args = argv_prefix + [feature_id, action]
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


def run_helper_diagnostics(timeout: int = 10) -> OpResult:
    """
    "Verifica funzionamento" for the admin component: asks the installed
    helper for its version via the read-only `diagnose` action. Runs the
    helper directly (no pkexec, no password) — the action changes
    nothing and answers fine unprivileged. Never falls back to the
    source-tree writer: this diagnoses the INSTALLED component only.
    """
    status = installed_helper_status()
    if not status.usable:
        return OpResult(False,
                        friendly_message=_HELPER_STATE_MESSAGE.get(status.state, "kf_err_helper_missing"),
                        technical_detail=f"state={status.state} path={status.path} {status.detail}".strip())
    try:
        proc = subprocess.run([status.path, "helper.ping", "diagnose", "", "", "0"],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
    try:
        payload = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return OpResult(False, friendly_message="kf_err_generic",
                        technical_detail=f"non-JSON reply: {(proc.stdout or proc.stderr).strip()[:200]}")
    if not payload.get("ok"):
        return OpResult(False, friendly_message="kf_err_generic",
                        technical_detail=payload.get("technical_detail", ""))
    return OpResult(True, value=payload.get("value"))
