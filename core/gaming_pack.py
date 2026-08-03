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
from core.executor import Job, run_command
from core import gaming_readiness as gr


FAMILIES = ("debian", "fedora", "arch", "opensuse")
OPTIONAL_COMPONENTS = {"gamescope", "goverlay"}

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
        "debian": ["steam-devices"], "fedora": None,
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
        "opensuse": None,
    },
    "opengl_32": {
        "debian": ["libgl1-mesa-dri:i386", "libglx-mesa0:i386"],
        "fedora": ["mesa-libGL.i686"],
        "arch": ["lib32-mesa"],
        "opensuse": None,
    },
    "audio_32": {
        "debian": ["libasound2-plugins:i386"],
        "fedora": ["alsa-plugins-pulseaudio.i686"],
        "arch": ["lib32-alsa-plugins"],
        "opensuse": None,
    },
}

# Tumbleweed has official packages that are not consistently present in Leap.
# Keeping the variants separate avoids presenting a rolling-release result as
# valid for Leap.  Runtime repository checks remain authoritative.
_OPENSUSE_TUMBLEWEED = {
    "steam": ["steam"],
    "gamemode": ["gamemode"],
    "mangohud": ["mangohud"],
    "winetricks": ["winetricks"],
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


@dataclass
class ComponentPreview:
    component_id: str
    optional: bool
    state: str
    packages: list = field(default_factory=list)
    repo_hint: str = ""
    installed_packages: list = field(default_factory=list)
    suggested_packages: list = field(default_factory=list)
    unavailable_packages: list = field(default_factory=list)


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
    )


def _packages_for(component_id: str, profile: SystemProfile) -> "list | None":
    if profile.family == "opensuse" and profile.distro_variant == "tumbleweed":
        if component_id in _OPENSUSE_TUMBLEWEED:
            return list(_OPENSUSE_TUMBLEWEED[component_id])
    packages = COMPONENTS[component_id].get(profile.family)
    return list(packages) if packages is not None else None


def _repository_hint(component_id: str, family: str) -> str:
    if component_id != "steam":
        return ""
    return {
        "debian": "contrib/non-free o multiverse",
        "fedora": "RPM Fusion (nonfree)",
        "arch": "multilib",
        "opensuse": "Non-OSS",
    }.get(family, "")


def _preview_unverifiable(component_id: str, optional: bool, packages=None) -> ComponentPreview:
    return ComponentPreview(
        component_id, optional, NOT_VERIFIABLE, list(packages or []),
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


def _is_available(family: str, package: str, job: Optional[Job] = None) -> bool:
    """Query only repositories already configured on the machine."""
    commands = {
        "debian": ["apt-cache", "show", package],
        "fedora": ["dnf", "info", package],
        "arch": ["pacman", "-Si", package],
        "opensuse": ["zypper", "--non-interactive", "info", package],
    }
    command = commands.get(family)
    if command is None:
        return False
    ok, out, error = run_command(command, job=job)
    combined = f"{out}\n{error}".lower()
    if family == "opensuse" and "not found" in combined:
        return False
    return ok and bool(out.strip())


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
                component_id, optional, REPO_NEEDED, packages,
                profile.lib32_repo_hint, unavailable_packages=packages,
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
                installed_packages=installed,
            ))
            continue

        available = []
        unavailable = []
        try:
            for package in missing:
                if _is_available(profile.family, package, job=job):
                    available.append(package)
                else:
                    unavailable.append(package)
        except Exception:
            results.append(ComponentPreview(
                component_id, optional, NOT_VERIFIABLE, packages,
                installed_packages=installed,
            ))
            continue

        hint = _repository_hint(component_id, profile.family) if unavailable else ""
        state = REPO_NEEDED if hint else (NOT_AVAILABLE if unavailable else AVAILABLE)
        results.append(ComponentPreview(
            component_id, optional, state, packages, hint,
            installed_packages=installed,
            suggested_packages=available,
            unavailable_packages=unavailable,
        ))

    return results


def gpu_driver_unverified(profile: SystemProfile) -> bool:
    """Informational warning only: analysis remains safe and available."""
    return not profile.gpu_driver_known_good
