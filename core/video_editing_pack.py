"""Gaming Pack V1: read-only system analysis and package preview.

This module deliberately contains no installation, removal, repository
configuration, driver or kernel operation.  It only identifies the running
system and asks its existing package database which named packages are already
installed or are available from repositories the user configured beforehand.

The Debian-family mapping was exercised on the Pop!_OS 24.04 development
machine.  Fedora, Arch-family and openSUSE mappings were checked against their
official package indexes, but still require testing on real machines.
"""
import shutil
from dataclasses import dataclass, field
from typing import Callable, Optional

from core.distro import get_context
from core.executor import Job, run_command, run_command_full
from core import gaming_readiness as gr
from core import gpu_vendor as gv


FAMILIES = ("debian", "fedora", "arch", "opensuse")
OPTIONAL_COMPONENTS = {"gamescope", "goverlay", "mangohud_32"}
COMMON_COMPONENTS = ("gamemode", "mangohud", "goverlay", "vulkan_64")

# None means that no sufficiently reliable official-repository mapping was
# found.  It is reported as "not verifiable", never replaced by a guessed name.
COMPONENTS = {
    "steam": {
        "debian": ["steam"], "fedora": ["steam"], "arch": ["steam"],
        "opensuse": None,
    },
    "gamemode": {
        "debian": ["gamemode"], "fedora": ["gamemode"], "arch": ["gamemode"],
        "opensuse": None,
    },
    "mangohud": {
        "debian": ["mangohud"], "fedora": ["mangohud"], "arch": ["mangohud"],
        "opensuse": None,
    },
    "mangohud_32": {
        # 32-bit companion package, needed for MangoHud to overlay 32-bit
        # games. Not the same package as "mangohud" above — openSUSE
        # ships them as two separate RPMs (confirmed on a real
        # Tumbleweed machine; other families were not checked for this
        # split and stay unverified rather than guessed).
        "debian": None, "fedora": None, "arch": None,
        "opensuse": None,
    },
    "gamescope": {
        "debian": ["gamescope"], "fedora": ["gamescope"], "arch": ["gamescope"],
        "opensuse": None,
    },
    "goverlay": {
        "debian": ["goverlay"], "fedora": ["goverlay"], "arch": ["goverlay"],
        "opensuse": None,
    },
    "lutris": {
        "debian": ["lutris"], "fedora": ["lutris"], "arch": ["lutris"],
        "opensuse": None,
    },
    "protontricks": {
        "debian": ["protontricks"], "fedora": ["protontricks"],
        "arch": ["protontricks"], "opensuse": None,
    },
    "winetricks": {
        "debian": ["winetricks"], "fedora": ["winetricks"],
        "arch": ["winetricks"], "opensuse": None,
    },
    "steam_devices": {
        "debian": ["steam-devices"], "fedora": ["steam-devices"],
        "arch": ["steam-devices"], "opensuse": None,
    },
    "vulkan_64": {
        "debian": ["libvulkan1", "vulkan-tools"],
        "fedora": ["vulkan-loader", "vulkan-tools"],
        "arch": ["vulkan-icd-loader", "vulkan-tools"],
        "opensuse": ["libvulkan1", "vulkan-tools"],
    },
    "vulkan_32": {
        "debian": ["libvulkan1:i386"],
        "fedora": ["vulkan-loader.i686"],
        "arch": ["lib32-vulkan-icd-loader"],
        # openSUSE has no single vendor-neutral package here: the loader
        # alone doesn't provide working 32-bit Vulkan without a matching
        # ICD. See _OPENSUSE_VULKAN_ICD_32BIT / _packages_for() below —
        # this entry is intentionally never used for that family.
        "opensuse": None,
    },
    "opengl_32": {
        "debian": ["libgl1-mesa-dri:i386", "libglx-mesa0:i386"],
        "fedora": ["mesa-libGL.i686"],
        "arch": ["lib32-mesa"],
        # Confirmed on a real openSUSE Tumbleweed machine only (see
        # _OPENSUSE_TUMBLEWEED) — not assumed valid for Leap.
        "opensuse": None,
    },
    "audio_32": {
        "debian": ["libasound2-plugins:i386"],
        "fedora": ["alsa-plugins-pulseaudio.i686"],
        "arch": ["lib32-alsa-plugins"],
        # Confirmed on a real openSUSE Tumbleweed machine only (see
        # _OPENSUSE_TUMBLEWEED) — not assumed valid for Leap.
        "opensuse": None,
    },
}

# openSUSE 32-bit Vulkan ICD per GPU vendor (core.gpu_vendor values) —
# same real, `zypper`-confirmed names as backend.all._OPENSUSE_VULKAN_ICD_32BIT.
# Deliberately no entry for a proprietary NVIDIA driver or an
# unrecognized GPU: guessing a GPU-specific package is exactly what this
# module must never do (see ReadOnlySafetyTests — it also never installs
# anything, guessed or not).
_OPENSUSE_VULKAN_ICD_32BIT = {
    "amd": "libvulkan_radeon-32bit",
    "intel": "libvulkan_intel-32bit",
    "nouveau": "libvulkan_nouveau-32bit",
}

# Tumbleweed has official packages that are not consistently present in Leap.
# Keeping the variants separate avoids presenting a rolling-release result as
# valid for Leap.  Runtime repository checks remain authoritative.
#
# 2026-08-05: re-verified for real with `zypper search -s`/`zypper info`
# on a Tumbleweed machine (AMD/amdgpu GPU) — steam is served from the
# default "Repository principale (NON-OSS)" (part of a stock Tumbleweed
# install, not an external/Packman-style repo this app would need to
# add), everything else from "Repository principale (OSS)". mangohud,
# gamescope and goverlay were NOT installed on that machine but ARE
# present/available there, confirming the name itself is real even
# though the component's install state is "available", not
# "already_installed".
_OPENSUSE_TUMBLEWEED = {
    "steam": ["steam"],
    "gamemode": ["gamemode"],
    "mangohud": ["mangohud"],
    "mangohud_32": ["mangohud-32bit"],
    "gamescope": ["gamescope"],
    "goverlay": ["goverlay"],
    "lutris": ["lutris"],
    "protontricks": ["protontricks"],
    "winetricks": ["winetricks"],
    "steam_devices": ["steam-devices"],
    "opengl_32": ["Mesa-libGL1-32bit"],
    "audio_32": ["alsa-plugins-pulse-32bit"],
}

_LIB32_COMPONENTS = {"vulkan_32", "opengl_32", "audio_32"}

ALREADY_INSTALLED = "already_installed"
AVAILABLE = "available"
NOT_AVAILABLE = "not_available"
REPO_NEEDED = "repo_needed"
NOT_SUITABLE = "not_suitable"
NOT_VERIFIABLE = "not_verifiable"


@dataclass
class SystemProfile:
    family: str
    distro_pretty_name: str
    package_manager: str
    architecture: str
    gpu_driver: str
    gpu_driver_known_good: bool
    vulkan_ok: bool
    lib32_active: bool
    lib32_repo_hint: str
    distro_variant: str = ""
    package_manager_available: bool = True
    gpu_vendor: str = gv.UNKNOWN


@dataclass
class ComponentPreview:
    component_id: str
    optional: bool
    state: str
    packages: list = field(default_factory=list)
    repo_hint: str = ""
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


def _variant_from_context(ctx, family: str) -> str:
    if family != "opensuse":
        return ""
    identity = f"{ctx.id} {ctx.id_like} {ctx.pretty_name} {ctx.version_id}".lower()
    return "tumbleweed" if "tumbleweed" in identity else "leap"


def _lib32_status(family: str) -> "tuple[bool, str]":
    """Read existing multilib configuration without changing it."""
    if family == "debian":
        ok, out, _ = run_command(["dpkg", "--print-foreign-architectures"])
        active = ok and "i386" in out.split()
        return active, "" if active else "architettura i386"
    if family == "arch":
        try:
            with open("/etc/pacman.conf", encoding="utf-8") as config:
                active = any(line.strip() == "[multilib]" for line in config)
        except OSError:
            active = False
        return active, "" if active else "multilib"
    if family in ("fedora", "opensuse"):
        return True, ""
    return False, ""


def detect_system(sys_root: str = "/sys") -> SystemProfile:
    """Build a read-only profile of distro, GPU and package facilities."""
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
    driver_item = gr.check_gpu_driver(sys_root=sys_root)
    vulkan_item = gr.check_vulkan()
    lib32_active, lib32_hint = _lib32_status(family)
    return SystemProfile(
        family=family,
        distro_pretty_name=ctx.pretty_name or ctx.id or "Distribuzione sconosciuta",
        package_manager=package_manager,
        architecture=ctx.architecture,
        gpu_driver=driver_item.detail,
        gpu_driver_known_good=driver_item.state in (gr.READY, gr.ALMOST_READY),
        vulkan_ok=vulkan_item.state == gr.READY,
        lib32_active=lib32_active,
        lib32_repo_hint=lib32_hint,
        distro_variant=_variant_from_context(ctx, family),
        package_manager_available=bool(query_tools) and all(shutil.which(tool) for tool in query_tools),
        gpu_vendor=gv.detect_gpu_vendor(sys_root=sys_root),
    )


def _packages_for(component_id: str, profile: SystemProfile) -> "list | None":
    is_opensuse_tumbleweed = profile.family == "opensuse" and profile.distro_variant == "tumbleweed"
    if component_id == "vulkan_32" and is_opensuse_tumbleweed:
        # GPU-vendor-dependent, and only verified on Tumbleweed: the
        # loader alone isn't enough, and there is no confirmed package
        # for a proprietary NVIDIA driver or an unrecognized GPU here —
        # return None (not_verifiable) rather than guess one.
        icd = _OPENSUSE_VULKAN_ICD_32BIT.get(profile.gpu_vendor)
        return ["libvulkan1-32bit", icd] if icd else None
    if is_opensuse_tumbleweed and component_id in _OPENSUSE_TUMBLEWEED:
        return list(_OPENSUSE_TUMBLEWEED[component_id])
    packages = COMPONENTS[component_id].get(profile.family)
    return list(packages) if packages is not None else None


def _preview_unverifiable(component_id: str, optional: bool, packages=None) -> ComponentPreview:
    return ComponentPreview(
        component_id, optional, NOT_VERIFIABLE, list(packages or []),
        common=component_id in COMMON_COMPONENTS,
    )


def _is_installed(family: str, package: str, job: Optional[Job] = None) -> bool:
    """Query the detected family's package database directly.

    This intentionally does not use DistroManager's Debian fallback: a new or
    derivative distribution must never be queried with the wrong tool.
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
    available=None means the query itself was not runnable or not trustworthy.
    """
    if family == "debian":
        policy = _command_result_or_none(["apt-cache", "policy", package], job=job)
        show = _command_result_or_none(["apt-cache", "show", package], job=job)
        if policy is None or show is None:
            return None, ""
        text = f"{policy.stdout}\n{show.stdout}\n{policy.stderr}\n{show.stderr}"
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
    profile = profile or detect_system()
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
        if component_id in _LIB32_COMPONENTS and profile.architecture not in (
            "x86_64", "amd64", "i686", "i386",
        ):
            results.append(ComponentPreview(component_id, optional, NOT_SUITABLE, packages))
            continue
        if component_id in _LIB32_COMPONENTS and not profile.lib32_active:
            results.append(ComponentPreview(
                component_id, optional, NOT_AVAILABLE, packages,
                profile.lib32_repo_hint, component_id in COMMON_COMPONENTS,
                unavailable_packages=packages,
            ))
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
            component_id, optional, state, packages, profile.lib32_repo_hint if unavailable and component_id in _LIB32_COMPONENTS and not profile.lib32_active else "", component_id in COMMON_COMPONENTS,
            installed_packages=installed,
            suggested_packages=available,
            unavailable_packages=unavailable,
            repositories=repositories,
        ))

    return results


def gpu_driver_unverified(profile: SystemProfile) -> bool:
    """Informational warning only: analysis remains safe and available."""
    return not profile.gpu_driver_known_good
