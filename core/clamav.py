"""
ClamAV integration — detection, installation, signature updates and
on-demand scanning. Not modeled as a KernelFeature (no sysfs/sysctl
value to try/restore): this follows the same direct-pkexec pattern as
core/apparmor_setup.py, with its own history logging since it doesn't
go through core/priv_writer.py's FEATURE_WRITERS.

Scope, deliberately narrow for this first integration:
  - detection + installation from the distribution's OWN already
    configured repositories only — never an external repo, PPA, AUR,
    OBS project, RPM Fusion or Packman;
  - freshclam signature updates;
  - a single on-demand clamscan of one file or one folder the user
    explicitly picks, read-only (never deletes/moves/quarantines
    anything — that is deliberately out of scope for this session).

ClamAV is a real, useful signature+heuristic scanner, but it is NOT a
"real-time protection" product in its default configuration — clamd
alone (or just having the packages installed) does not watch the
filesystem continuously. Real-time-ish coverage needs clamonacc
(on-access scanning), which this module never enables automatically
(see SECURITY notes below). Every user-facing string built on top of
this module must stay honest about that distinction.
"""
import os
import shutil
import time
from dataclasses import dataclass, field

from core.executor import run_command, run_command_full, run_pkexec_full, INSTALL_TIMEOUT, Job
from core.distro import distro

# ─── Package mapping — official repositories only ─────────────────
# Verified package names per family (Gregorio-provided, cross-checked
# against each distribution's own package search). Never expanded with
# a repository URL anywhere in this module — install() below always
# goes through the same install_cmd-style invocation every other
# feature in this app uses, hitting only whatever's already configured.
CLAMAV_PACKAGES = {
    "debian":   ["clamav", "clamav-daemon"],
    "fedora":   ["clamav", "clamd", "clamav-update"],
    "opensuse": ["clamav"],
    "arch":     ["clamav"],
}

# Real unit names differ per distro (and clamd is a *templated* unit —
# clamd@scan — on Fedora/openSUSE); tried in order, first one that
# actually exists on THIS system wins. Never assumed.
_CLAMD_SERVICE_CANDIDATES = ["clamav-daemon", "clamd@scan", "clamd"]
_FRESHCLAM_SERVICE_CANDIDATES = ["clamav-freshclam", "freshclam"]

_SIGNATURE_DB_DIRS = ["/var/lib/clamav"]
_SIGNATURE_MAIN_NAMES = ("main.cvd", "main.cld")
_SIGNATURE_DAILY_NAMES = ("daily.cvd", "daily.cld")
# freshclam's own recommended cadence is roughly daily; flag as
# "outdated" only once it's clearly stale, never on a false positive
# from a machine that was merely off for a day or two.
_STALE_SECONDS = 10 * 24 * 60 * 60  # 10 days

STATE_NOT_INSTALLED = "not_installed"
STATE_INSTALLED = "installed"
STATE_READY = "ready"
STATE_SIGNATURES_OUTDATED = "signatures_outdated"
STATE_UNKNOWN = "unknown"


def _family() -> str:
    if distro.is_arch:
        return "arch"
    if distro.is_fedora:
        return "fedora"
    if distro.is_opensuse:
        return "opensuse"
    return "debian"


def packages_for_this_distro() -> list:
    return list(CLAMAV_PACKAGES.get(_family(), []))


def is_installed() -> bool:
    """Best-effort check using the first (most representative) package
    for this distro's family — same approach already used for printer
    driver sets (backend.all.printer_set_installed)."""
    pkgs = packages_for_this_distro()
    if not pkgs:
        return False
    return distro.is_installed({_family(): pkgs[0]})


def installed_packages() -> list:
    """Only the packages from CLAMAV_PACKAGES this distro's package
    manager confirms are REALLY installed right now — never assumes
    the whole family list is present just because one of them is (a
    Fedora machine might have clamav+clamd but never have gotten
    clamav-update, for instance). Used before uninstall() so it only
    ever names packages that genuinely exist on this system."""
    family = _family()
    return [p for p in CLAMAV_PACKAGES.get(family, []) if distro.is_installed({family: p})]


def is_available_in_repos() -> bool:
    """True only if EVERY package this distro needs is really visible
    through the package manager right now — a real, live check
    (core.repo_check.is_available, per package), never an assumption.
    Never touches repository configuration itself."""
    from core.repo_check import is_available
    pkgs = packages_for_this_distro()
    if not pkgs:
        return False
    family = _family()
    return all(is_available({family: pkg}) for pkg in pkgs)


def install(job: "Job | None" = None):
    """Installs every package this distro needs with a single pkexec
    call, from whatever repositories are already configured — never
    adds, enables or points at any repository itself. Returns a
    CommandResult (truthy on success, with real diagnostics)."""
    pkgs = packages_for_this_distro()
    if not pkgs:
        from core.executor import CommandResult
        return CommandResult([], False, None, "", "", 0.0,
                              error="no packages known for this distribution")
    family = _family()
    if family == "arch":
        cmd = ["pacman", "-S", "--noconfirm"] + pkgs
    elif family == "opensuse":
        cmd = ["zypper", "--non-interactive", "install"] + pkgs
    elif family == "fedora":
        cmd = ["dnf", "install", "-y"] + pkgs
    else:
        cmd = ["apt-get", "install", "-y"] + pkgs
    return run_pkexec_full(cmd, timeout=INSTALL_TIMEOUT, job=job)


def uninstall(job: "Job | None" = None):
    """Removes ONLY the packages this system's package manager confirms
    are really installed (installed_packages() — never the full family
    list blindly), after stopping any ClamAV services actually detected
    (best-effort; a failed stop doesn't block the removal). Never
    --purge, never autoremove/-Rns, never touches repository
    configuration, never deletes files by hand. Returns a CommandResult."""
    pkgs = installed_packages()
    if not pkgs:
        from core.executor import CommandResult
        return CommandResult([], False, None, "", "", 0.0,
                              error="no ClamAV package detected as installed")

    for name in (clamd_service_name(), freshclam_service_name()):
        if name is not None:
            run_pkexec_full(["systemctl", "stop", name], timeout=INSTALL_TIMEOUT)

    family = _family()
    if family == "arch":
        cmd = ["pacman", "-R", "--noconfirm"] + pkgs
    elif family == "opensuse":
        cmd = ["zypper", "--non-interactive", "remove"] + pkgs
    elif family == "fedora":
        cmd = ["dnf", "remove", "-y"] + pkgs
    else:
        cmd = ["apt-get", "remove", "-y"] + pkgs
    return run_pkexec_full(cmd, timeout=INSTALL_TIMEOUT, job=job)


# ─── Services (informational only — never assumed, never auto-enabled) ──
def _service_exists(name: str) -> bool:
    ok, out, _ = run_command(["systemctl", "list-unit-files", f"{name}.service"])
    return ok and name in out


def _service_active(name: str) -> bool:
    ok, out, _ = run_command(["systemctl", "is-active", name])
    return out == "active"


def clamd_service_name() -> "str | None":
    for name in _CLAMD_SERVICE_CANDIDATES:
        if _service_exists(name):
            return name
    return None


def clamd_active() -> "bool | None":
    """None means "no clamd unit could be detected on this system at
    all" — genuinely different from False ("detected, but not
    running"), and the UI must not conflate the two."""
    name = clamd_service_name()
    if name is None:
        return None
    return _service_active(name)


def clamd_manageable() -> bool:
    """True only when a real clamd unit was actually detected on this
    system — Avvia/Ferma must never be offered otherwise. A system
    with ClamAV installed but no daemon unit (on-demand scanning only)
    is a perfectly normal, supported state, not an error."""
    return clamd_service_name() is not None


def clamd_start(job: "Job | None" = None):
    name = clamd_service_name()
    if name is None:
        from core.executor import CommandResult
        return CommandResult([], False, None, "", "", 0.0, error="no clamd unit detected on this system")
    return run_pkexec_full(["systemctl", "start", name], timeout=INSTALL_TIMEOUT, job=job)


def clamd_stop(job: "Job | None" = None):
    """Stops the scan-daemon unit only — never a claim about
    on-demand scanning (clamscan) or the signature database, both of
    which keep working exactly the same either way."""
    name = clamd_service_name()
    if name is None:
        from core.executor import CommandResult
        return CommandResult([], False, None, "", "", 0.0, error="no clamd unit detected on this system")
    return run_pkexec_full(["systemctl", "stop", name], timeout=INSTALL_TIMEOUT, job=job)


def freshclam_service_name() -> "str | None":
    for name in _FRESHCLAM_SERVICE_CANDIDATES:
        if _service_exists(name):
            return name
    return None


# ─── Signature definitions ─────────────────────────────────────────
def freshclam_present() -> bool:
    return shutil.which("freshclam") is not None


def update_definitions(job: "Job | None" = None):
    """Runs freshclam with root privileges (it needs write access to
    /var/lib/clamav, owned by the clamav/vscan system user) — same
    plain-pkexec pattern as every other privileged action in this app.
    Returns a CommandResult."""
    return run_pkexec_full(["freshclam"], timeout=INSTALL_TIMEOUT, job=job)


def _newest_mtime(dir_path: str, names: tuple) -> "float | None":
    best = None
    for name in names:
        p = os.path.join(dir_path, name)
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        if best is None or mtime > best:
            best = mtime
    return best


def signatures_status() -> str:
    """One of 'ready' | 'outdated' | 'missing' | 'unknown' — read-only,
    based only on what's really on disk, never a guess."""
    db_dir = next((d for d in _SIGNATURE_DB_DIRS if os.path.isdir(d)), None)
    if db_dir is None:
        return "unknown"
    main_mtime = _newest_mtime(db_dir, _SIGNATURE_MAIN_NAMES)
    daily_mtime = _newest_mtime(db_dir, _SIGNATURE_DAILY_NAMES)
    if main_mtime is None or daily_mtime is None:
        return "missing"
    if (time.time() - daily_mtime) > _STALE_SECONDS:
        return "outdated"
    return "ready"


def state() -> str:
    """The single real, detected state the UI shows — never a
    fabricated "protected" claim."""
    if not is_installed():
        return STATE_NOT_INSTALLED
    try:
        sig = signatures_status()
    except Exception:
        return STATE_UNKNOWN
    if sig == "ready":
        return STATE_READY
    if sig == "outdated":
        return STATE_SIGNATURES_OUTDATED
    if sig == "missing":
        return STATE_INSTALLED
    return STATE_UNKNOWN


# ─── Scanning ───────────────────────────────────────────────────────
@dataclass
class ScanResult:
    ok: bool
    infected_count: int = 0
    error: str = ""             # "" | "path_not_found" | "scanner_error" | "cancelled" | "timed_out"
    technical_detail: str = ""
    infected_files: list = field(default_factory=list)

    def __bool__(self):
        return self.ok


# clamscan exit codes: 0 = no virus, 1 = virus(es) found, 2 = error.
_EXIT_CLEAN, _EXIT_INFECTED, _EXIT_ERROR = 0, 1, 2

# clamscan can legitimately run for a long time over a large folder —
# this is always invoked from a background thread by the UI, never the
# GTK main thread, so a generous bound is fine here.
_SCAN_TIMEOUT = 1800


def scan_path(path: str, job: "Job | None" = None) -> ScanResult:
    """Scans exactly one file or one folder the user picked. The path
    is always passed as its own argv element (never interpolated into
    a shell string), and is validated to really exist first — a
    made-up or missing path is reported as a clean failure, never
    handed to the scanner. Never deletes, moves or quarantines
    anything; read-only, informational scanning only."""
    if not isinstance(path, str) or not path:
        return ScanResult(False, error="path_not_found", technical_detail="empty path")
    if not os.path.exists(path):
        return ScanResult(False, error="path_not_found", technical_detail=f"no such path (length {len(path)})")

    if os.path.isdir(path):
        cmd = ["clamscan", "-r", "--infected", path]
    else:
        cmd = ["clamscan", "--infected", path]

    result = run_command_full(cmd, timeout=_SCAN_TIMEOUT, job=job)

    if job is not None and job.cancelled:
        return ScanResult(False, error="cancelled", technical_detail=result.technical_detail())
    if result.timed_out:
        return ScanResult(False, error="timed_out", technical_detail=result.technical_detail())
    if result.error:
        # clamscan itself isn't runnable (not installed, etc.)
        return ScanResult(False, error="scanner_error", technical_detail=result.technical_detail())

    if result.returncode == _EXIT_CLEAN:
        return ScanResult(True, infected_count=0)
    if result.returncode == _EXIT_INFECTED:
        infected_files = [line.rsplit(":", 1)[0].strip()
                           for line in result.stdout.splitlines() if line.strip().endswith("FOUND")]
        return ScanResult(True, infected_count=len(infected_files) or 1, infected_files=infected_files)
    # returncode == 2 (or anything unexpected)
    return ScanResult(False, error="scanner_error", technical_detail=result.technical_detail())


# ─── History (Cronologia) ───────────────────────────────────────────
def _log(feature_id: str, entry_type: str, ok: bool, technical_detail: str = ""):
    """Never receives a filesystem path — callers are responsible for
    keeping the scanned path itself out of technical_detail, per the
    "never log personal paths unless necessary" rule."""
    try:
        from core.persistence import history_store as hs
        hs.record_operation("security", feature_id, entry_type, ok, technical_detail=technical_detail)
    except Exception:
        pass


def log_install(ok: bool, technical_detail: str = ""):
    from core.persistence import history_store as hs
    _log("clamav.install", hs.INSTALLATION, ok, technical_detail)


def log_uninstall(ok: bool, technical_detail: str = ""):
    from core.persistence import history_store as hs
    _log("clamav.uninstall", hs.DEACTIVATION, ok, technical_detail)


def log_definitions_update(ok: bool, technical_detail: str = ""):
    from core.persistence import history_store as hs
    _log("clamav.freshclam", hs.CONFIGURATION, ok, technical_detail)


def log_service_toggle(ok: bool, enabled: bool, technical_detail: str = ""):
    from core.persistence import history_store as hs
    entry_type = hs.ACTIVATION if enabled else hs.DEACTIVATION
    _log("clamav.service", entry_type, ok, technical_detail)


def log_scan(ok: bool, infected_count: int = 0, technical_detail: str = ""):
    from core.persistence import history_store as hs
    entry_type = hs.VERIFICATION if ok else hs.ERROR
    detail = f"infected={infected_count}" if ok else technical_detail
    _log("clamav.scan", entry_type, ok, detail)
