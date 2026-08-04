"""
"Salute pacchetti" — read-only/dry-run health scan using each distro's
own native package manager (never a custom dependency resolver), plus
guarded repair actions that always show a preview before touching
anything. See page spec section D.
"""
from dataclasses import dataclass, field

from core.executor import run_command_full, run_pkexec_full, INSTALL_TIMEOUT
from core.software_repo import flatpak_manager as fp


@dataclass
class HealthReport:
    family: str
    broken_packages: list = field(default_factory=list)
    orphan_packages: list = field(default_factory=list)
    cache_reclaimable_human: str = ""
    unused_flatpak_runtimes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)   # i18n keys: unreachable repo, missing signature, ...
    scan_ok: bool = True

    def to_dict(self) -> dict:
        return dict(self.__dict__)


# ── Debian/Ubuntu ────────────────────────────────────────────────────

def _scan_debian(job=None) -> HealthReport:
    report = HealthReport(family="debian")

    r = run_command_full(["dpkg", "-C"], timeout=30, job=job)
    if r.ok is False and r.error:
        report.scan_ok = False
        report.warnings.append("health_scan_dpkg_unavailable")
    elif r.stdout.strip():
        report.broken_packages = [l.strip() for l in r.stdout.splitlines() if l.strip()]

    # Dry-run only: apt-get -s never installs/removes anything.
    r = run_command_full(["apt-get", "-s", "autoremove"], timeout=60, job=job)
    if r.ok:
        in_block = False
        for line in r.stdout.splitlines():
            if line.startswith("The following packages will be REMOVED"):
                in_block = True
                continue
            if in_block:
                if line.startswith(" "):
                    report.orphan_packages += line.split()
                else:
                    in_block = False

    from backend.all import cache_size_human
    report.cache_reclaimable_human = cache_size_human()
    return report


def _repair_debian(job=None):
    return run_pkexec_full(["apt-get", "install", "-f", "-y"], timeout=INSTALL_TIMEOUT, job=job)


def _remove_orphans_debian(job=None):
    return run_pkexec_full(["apt-get", "autoremove", "-y"], timeout=INSTALL_TIMEOUT, job=job)


def _update_indexes_debian(job=None):
    return run_pkexec_full(["apt-get", "update"], timeout=INSTALL_TIMEOUT, job=job)


# ── Fedora ────────────────────────────────────────────────────────────

def _scan_fedora(job=None) -> HealthReport:
    report = HealthReport(family="fedora")

    r = run_command_full(["dnf", "check", "--dependencies", "--duplicates"], timeout=60, job=job)
    if r.error:
        report.scan_ok = False
        report.warnings.append("health_scan_dnf_unavailable")
    elif not r.ok and r.stdout.strip():
        report.broken_packages = [l.strip() for l in r.stdout.splitlines() if l.strip()]

    r = run_command_full(["dnf", "repoquery", "--unneeded"], timeout=60, job=job)
    if r.ok and r.stdout.strip():
        report.orphan_packages = [l.strip() for l in r.stdout.splitlines() if l.strip()]

    from backend.all import cache_size_human
    report.cache_reclaimable_human = cache_size_human()
    return report


def _repair_fedora(job=None):
    return run_pkexec_full(["dnf", "distro-sync", "-y"], timeout=INSTALL_TIMEOUT, job=job)


def _remove_orphans_fedora(job=None):
    return run_pkexec_full(["dnf", "autoremove", "-y"], timeout=INSTALL_TIMEOUT, job=job)


def _update_indexes_fedora(job=None):
    return run_pkexec_full(["dnf", "makecache"], timeout=INSTALL_TIMEOUT, job=job)


# ── Arch ─────────────────────────────────────────────────────────────

def _scan_arch(job=None) -> HealthReport:
    report = HealthReport(family="arch")

    r = run_command_full(["pacman", "-Dk"], timeout=30, job=job)
    if r.error:
        report.scan_ok = False
        report.warnings.append("health_scan_pacman_unavailable")
    elif not r.ok and r.stdout.strip():
        report.broken_packages = [l.strip() for l in r.stdout.splitlines() if l.strip()]

    r = run_command_full(["pacman", "-Qtdq"], timeout=30, job=job)
    if r.ok and r.stdout.strip():
        report.orphan_packages = [l.strip() for l in r.stdout.splitlines() if l.strip()]

    from backend.all import cache_size_human
    report.cache_reclaimable_human = cache_size_human()
    return report


def _repair_arch(job=None):
    return run_pkexec_full(["pacman", "-Syu", "--noconfirm"], timeout=INSTALL_TIMEOUT, job=job)


def _remove_orphans_arch(job=None):
    r = run_command_full(["pacman", "-Qtdq"], timeout=30, job=job)
    orphans = [l.strip() for l in r.stdout.splitlines() if l.strip()] if r.ok else []
    if not orphans:
        return run_command_full(["true"])
    return run_pkexec_full(["pacman", "-Rns", "--noconfirm"] + orphans, timeout=INSTALL_TIMEOUT, job=job)


def _update_indexes_arch(job=None):
    return run_pkexec_full(["pacman", "-Sy"], timeout=INSTALL_TIMEOUT, job=job)


# ── openSUSE ─────────────────────────────────────────────────────────

def _scan_opensuse(job=None) -> HealthReport:
    report = HealthReport(family="opensuse")

    r = run_command_full(["zypper", "--non-interactive", "verify", "--dry-run"], timeout=60, job=job)
    if r.error:
        report.scan_ok = False
        report.warnings.append("health_scan_zypper_unavailable")
    elif not r.ok and r.stdout.strip():
        report.broken_packages = [l.strip() for l in r.stdout.splitlines() if l.strip()]

    from backend.all import cache_size_human
    report.cache_reclaimable_human = cache_size_human()
    return report


def _repair_opensuse(job=None):
    return run_pkexec_full(["zypper", "--non-interactive", "verify"], timeout=INSTALL_TIMEOUT, job=job)


def _update_indexes_opensuse(job=None):
    return run_pkexec_full(["zypper", "--non-interactive", "refresh"], timeout=INSTALL_TIMEOUT, job=job)


_SCANNERS = {"debian": _scan_debian, "fedora": _scan_fedora, "arch": _scan_arch, "opensuse": _scan_opensuse}
_REPAIRERS = {"debian": _repair_debian, "fedora": _repair_fedora, "arch": _repair_arch, "opensuse": _repair_opensuse}
# openSUSE deliberately absent: there is no official, safe one-step
# zypper removal command (see remove_orphans() below) — never route it
# through a real pkexec call that would falsely report success.
_ORPHAN_REMOVERS = {"debian": _remove_orphans_debian, "fedora": _remove_orphans_fedora,
                     "arch": _remove_orphans_arch}
_INDEX_UPDATERS = {"debian": _update_indexes_debian, "fedora": _update_indexes_fedora,
                    "arch": _update_indexes_arch, "opensuse": _update_indexes_opensuse}


def scan_system_health(family: str, job=None) -> HealthReport:
    scanner = _SCANNERS.get(family)
    if scanner is None:
        return HealthReport(family=family, scan_ok=False, warnings=["health_scan_family_unsupported"])
    report = scanner(job=job)
    if fp.flatpak_installed():
        report.unused_flatpak_runtimes = fp.list_unused_runtimes(job=job)
    return report


@dataclass
class RepairResult:
    ok: bool
    friendly_message: str = ""
    technical_detail: str = ""


def repair_dependencies(family: str, job=None) -> RepairResult:
    fn = _REPAIRERS.get(family)
    if fn is None:
        return RepairResult(False, friendly_message="health_action_family_unsupported")
    result = fn(job=job)
    return RepairResult(result.ok,
                         friendly_message="health_repair_success" if result.ok else "health_repair_failed",
                         technical_detail="" if result.ok else result.technical_detail())


def remove_orphans(family: str, job=None) -> RepairResult:
    if family == "opensuse":
        # `zypper packages --orphaned` only ever LISTS candidates — unlike
        # apt-get/dnf autoremove or pacman -Rns, there is no official
        # zypper subcommand that safely removes them in one step. Never
        # runs zypper at all here (no privilege request, no false
        # "success") until a real, safe procedure exists.
        return RepairResult(False, friendly_message="health_orphans_opensuse_not_supported")
    fn = _ORPHAN_REMOVERS.get(family)
    if fn is None:
        return RepairResult(False, friendly_message="health_action_family_unsupported")
    result = fn(job=job)
    return RepairResult(result.ok,
                         friendly_message="health_orphans_removed_success" if result.ok else "health_orphans_removed_failed",
                         technical_detail="" if result.ok else result.technical_detail())


def update_indexes(family: str, job=None) -> RepairResult:
    fn = _INDEX_UPDATERS.get(family)
    if fn is None:
        return RepairResult(False, friendly_message="health_action_family_unsupported")
    result = fn(job=job)
    return RepairResult(result.ok,
                         friendly_message="health_indexes_updated_success" if result.ok else "health_indexes_updated_failed",
                         technical_detail="" if result.ok else result.technical_detail())


def clean_package_cache(family: str, job=None) -> RepairResult:
    """Cache-only cleanup, moved here from "Sistema e disco" (2026-08-04)
    — orphan removal is now Section D's own separate, previewed action,
    never bundled silently into cache cleanup."""
    from core.distro import distro
    if distro.is_arch:
        result = run_pkexec_full(["pacman", "-Sc", "--noconfirm"], timeout=INSTALL_TIMEOUT, job=job)
    elif distro.is_fedora:
        result = run_pkexec_full(["dnf", "clean", "all"], timeout=INSTALL_TIMEOUT, job=job)
    elif distro.is_opensuse:
        result = run_pkexec_full(["zypper", "clean", "--all"], timeout=INSTALL_TIMEOUT, job=job)
    else:
        result = run_pkexec_full(["apt-get", "clean"], timeout=INSTALL_TIMEOUT, job=job)
    return RepairResult(result.ok,
                         friendly_message="health_cache_cleaned_success" if result.ok else "health_cache_cleaned_failed",
                         technical_detail="" if result.ok else result.technical_detail())


def repair_flatpak_unused(job=None) -> RepairResult:
    result = fp.remove_unused_runtimes(job=job)
    return RepairResult(result.ok, friendly_message=result.friendly_message, technical_detail=result.technical_detail)
