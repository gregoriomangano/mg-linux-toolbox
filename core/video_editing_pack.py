"""Video Editing Pack V1: read-only system analysis and package preview.

Same read-only contract as core/gaming_pack.py, from which this module
started as a plain copy: it never installs, removes, configures a
repository, or touches a driver/kernel setting. It only asks the running
distro's package manager which named packages are already present or
available from repositories the user already configured, plus one *real*
capability probe — the installed ffmpeg's own list of compiled-in hardware
accelerators — instead of guessing GPU-encoder support from the GPU vendor
alone (a correct GPU can still sit behind an ffmpeg build with no
VAAPI/NVENC/QSV support at all).

Package-name mappings below were checked against each distribution's real
package index (packages.debian.org, Arch's official repos, live on a Fedora
44 machine) on 2026-08-07. openSUSE names were checked against Packman —
openSUSE's own documented multimedia repository — rather than a live
Tumbleweed install: ffmpeg and obs-studio are not in openSUSE's default OSS
repo at all (patent/codec licensing, same reason Fedora needs RPM Fusion),
so scan() will correctly report them "not available" there unless the user
has already added Packman themselves; this app never adds it automatically.
Where a name could not be confirmed this way, the mapping is None (never a
guessed name) — scan() reports that as "not_verifiable".
"""
import shutil
from dataclasses import dataclass, field
from typing import Callable, Optional

from core.distro import get_context
from core.executor import Job, run_command, run_command_full

FAMILIES = ("debian", "fedora", "arch", "opensuse")

# ffmpeg is the one dependency the other two effectively assume is present
# (encoding/decoding backbone); obs_studio and kdenlive are "pick what you
# need" tools, not implied by one another.
COMMON_COMPONENTS = ("ffmpeg",)
OPTIONAL_COMPONENTS: set = set()

# None means no sufficiently reliable official-repository mapping was found.
# Reported as "not verifiable", never replaced by a guessed name.
COMPONENTS = {
    "ffmpeg": {
        "debian": ["ffmpeg"], "fedora": ["ffmpeg"], "arch": ["ffmpeg"], "opensuse": ["ffmpeg"],
    },
    "obs_studio": {
        "debian": ["obs-studio"], "fedora": ["obs-studio"], "arch": ["obs-studio"], "opensuse": ["obs-studio"],
    },
    "kdenlive": {
        "debian": ["kdenlive"], "fedora": ["kdenlive"], "arch": ["kdenlive"], "opensuse": ["kdenlive"],
    },
}

ALREADY_INSTALLED = "already_installed"
AVAILABLE = "available"
NOT_AVAILABLE = "not_available"
NOT_VERIFIABLE = "not_verifiable"


@dataclass
class SystemProfile:
    family: str
    distro_pretty_name: str
    package_manager: str
    architecture: str
    package_manager_available: bool = True
    ffmpeg_present: bool = False
    hwaccels: list = field(default_factory=list)


@dataclass
class ComponentPreview:
    component_id: str
    optional: bool
    state: str
    packages: list = field(default_factory=list)
    common: bool = False
    installed_packages: list = field(default_factory=list)
    suggested_packages: list = field(default_factory=list)
    unavailable_packages: list = field(default_factory=list)
    repositories: list = field(default_factory=list)


def _family_from_identity(identifier: str, id_like: str) -> str:
    identity = f"{identifier} {id_like}".lower()
    families = (
        ("arch", ("arch", "manjaro", "garuda", "endeavour", "cachy")),
        ("fedora", ("fedora", "rhel", "centos", "rocky", "alma")),
        ("opensuse", ("opensuse", "suse", "sles")),
        ("debian", ("debian", "ubuntu", "pop", "mint", "elementary", "kali", "mxlinux")),
    )
    for family, markers in families:
        if any(marker in identity for marker in markers):
            return family
    return "unknown"


def _probe_hwaccels(job: Optional[Job] = None) -> list:
    """Real probe of the installed ffmpeg's compiled-in hardware
    accelerators (vaapi, nvenc, qsv...) — never inferred from the GPU
    vendor alone, since a correct GPU can still sit behind an ffmpeg build
    with no hardware-encoder support at all."""
    if not shutil.which("ffmpeg"):
        return []
    result = run_command_full(["ffmpeg", "-hide_banner", "-hwaccels"], job=job)
    if not result.ok:
        return []
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    # First non-empty line is ffmpeg's own "Hardware acceleration methods:"
    # header, not an accelerator name.
    return lines[1:] if lines else []


def detect_system(job: Optional[Job] = None) -> SystemProfile:
    """Build a read-only profile of distro and package facilities."""
    ctx = get_context()
    family = _family_from_identity(ctx.id, ctx.id_like)
    package_manager = {
        "debian": "apt", "fedora": "dnf", "arch": "pacman", "opensuse": "zypper",
    }.get(family, "")
    query_tools = {
        "debian": ("apt-cache", "dpkg-query"),
        "fedora": ("dnf", "rpm"),
        "arch": ("pacman",),
        "opensuse": ("zypper", "rpm"),
    }.get(family, ())
    ffmpeg_present = bool(shutil.which("ffmpeg"))
    return SystemProfile(
        family=family,
        distro_pretty_name=ctx.pretty_name or ctx.id or "Distribuzione sconosciuta",
        package_manager=package_manager,
        architecture=ctx.architecture,
        package_manager_available=bool(query_tools) and all(shutil.which(tool) for tool in query_tools),
        ffmpeg_present=ffmpeg_present,
        hwaccels=_probe_hwaccels(job=job) if ffmpeg_present else [],
    )


def _packages_for(component_id: str, profile: SystemProfile) -> "list | None":
    packages = COMPONENTS[component_id].get(profile.family)
    return list(packages) if packages is not None else None


def _preview_unverifiable(component_id: str, optional: bool, packages=None) -> ComponentPreview:
    return ComponentPreview(
        component_id, optional, NOT_VERIFIABLE, list(packages or []),
        common=component_id in COMMON_COMPONENTS,
    )


def _is_installed(family: str, package: str, job: Optional[Job] = None) -> bool:
    """Query the detected family's package database directly.

    This intentionally does not fall back to a "closest guess" tool for an
    unrecognized family: an unknown distro must report as unverifiable,
    never be silently queried with the wrong package manager.
    """
    if family == "arch":
        ok, _, _ = run_command(["pacman", "-Q", package], job=job)
        return ok
    if family in ("fedora", "opensuse"):
        ok, _, _ = run_command(["rpm", "-q", package], job=job)
        return ok
    if family == "debian":
        ok, out, _ = run_command(["dpkg-query", "-W", "-f=${Status}", package], job=job)
        return ok and "install ok installed" in out.lower()
    return False


def _command_result_or_none(command: list, job: Optional[Job] = None):
    result = run_command_full(command, job=job)
    return result if result.error == "" else None


def _availability_probe(family: str, package: str, job: Optional[Job] = None) -> "tuple[bool | None, str]":
    """Return (available, repository).

    available=True/False means the package manager really answered.
    available=None means the query itself was not runnable or not
    trustworthy — callers must treat that as unverifiable, never as False.
    """
    if family == "debian":
        policy = _command_result_or_none(["apt-cache", "policy", package], job=job)
        show = _command_result_or_none(["apt-cache", "show", package], job=job)
        if policy is None or show is None:
            return None, ""
        candidate = ""
        repository = ""
        for line in policy.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Candidate:"):
                candidate = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("500 http") or stripped.startswith("100 http"):
                repository = stripped.split()[-3] if len(stripped.split()) >= 3 else repository
        return bool(candidate and candidate != "(none)" and show.ok and show.stdout.strip()), repository

    if family == "fedora":
        repoquery = _command_result_or_none(["dnf", "repoquery", "--info", package], job=job)
        info = _command_result_or_none(["dnf", "info", package], job=job)
        if repoquery is None or info is None:
            return None, ""
        text = f"{repoquery.stdout}\n{info.stdout}\n{repoquery.stderr}\n{info.stderr}".lower()
        if "no matching packages" in text or "unable to match" in text:
            return False, ""
        repository = ""
        for line in info.stdout.splitlines():
            if line.strip().startswith("Repository"):
                repository = line.split(":", 1)[1].strip()
                break
        return bool(info.ok and info.stdout.strip()), repository

    if family == "arch":
        result = _command_result_or_none(["pacman", "-Si", package], job=job)
        if result is None:
            return None, ""
        repository = ""
        for line in result.stdout.splitlines():
            if line.lower().startswith("repository"):
                repository = line.split(":", 1)[1].strip()
                break
        return bool(result.ok and result.stdout.strip()), repository

    if family == "opensuse":
        search = _command_result_or_none(["zypper", "--non-interactive", "search", "-s", package], job=job)
        info = _command_result_or_none(["zypper", "--non-interactive", "info", package], job=job)
        if search is None or info is None:
            return None, ""
        text = f"{search.stdout}\n{info.stdout}\n{search.stderr}\n{info.stderr}".lower()
        if "not found" in text or "no matching items found" in text:
            return False, ""
        repository = ""
        for line in info.stdout.splitlines():
            if line.strip().startswith("Repository"):
                repository = line.split(":", 1)[1].strip()
                break
        return bool(info.ok and info.stdout.strip()), repository

    return None, ""


def _is_available(family: str, package: str, job: Optional[Job] = None) -> bool:
    available, _repo = _availability_probe(family, package, job=job)
    return bool(available)


def scan(
    profile: Optional[SystemProfile] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    job: Optional[Job] = None,
) -> list:
    """Return package-level analysis; never mutate package or repo state."""
    profile = profile or detect_system(job=job)
    results = []

    for component_id in COMPONENTS:
        if cancel_check is not None and cancel_check():
            break
        optional = component_id in OPTIONAL_COMPONENTS
        packages = _packages_for(component_id, profile)

        if profile.family not in FAMILIES or not profile.package_manager_available:
            results.append(_preview_unverifiable(component_id, optional, packages))
            continue
        if packages is None:
            results.append(_preview_unverifiable(component_id, optional))
            continue

        installed = []
        missing = []
        try:
            for package in packages:
                if _is_installed(profile.family, package, job=job):
                    installed.append(package)
                else:
                    missing.append(package)
        except Exception:
            results.append(_preview_unverifiable(component_id, optional, packages))
            continue

        if not missing:
            results.append(ComponentPreview(
                component_id, optional, ALREADY_INSTALLED, packages,
                common=component_id in COMMON_COMPONENTS,
                installed_packages=installed,
            ))
            continue

        available = []
        unavailable = []
        repositories = []
        try:
            for package in missing:
                package_available, repository = _availability_probe(profile.family, package, job=job)
                if package_available is None:
                    raise RuntimeError("availability probe failed")
                if package_available:
                    available.append(package)
                    if repository:
                        repositories.append(f"{package}: {repository}")
                else:
                    unavailable.append(package)
        except Exception:
            results.append(ComponentPreview(
                component_id, optional, NOT_VERIFIABLE, packages,
                common=component_id in COMMON_COMPONENTS,
                installed_packages=installed,
            ))
            continue

        state = NOT_AVAILABLE if unavailable else AVAILABLE
        results.append(ComponentPreview(
            component_id, optional, state, packages,
            common=component_id in COMMON_COMPONENTS,
            installed_packages=installed,
            suggested_packages=available,
            unavailable_packages=unavailable,
            repositories=repositories,
        ))

    return results
