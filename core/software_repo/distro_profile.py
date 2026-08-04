"""
Universal distribution/family detection for "Software e repository".

This does NOT replace core.distro.DistroManager/DistroContext — those
stay the single source of truth for install_cmd()/is_installed() and
every existing feature that already depends on their `family` /
`package_manager` shape. This module answers a narrower, stricter
question those two don't: "is this machine a traditional, immutable or
transactional system, and can we say so with confidence?" — needed
before this page ever offers to touch a repository file.

Detection never trusts ID alone (Pop!_OS, Mint, Peppermint, CachyOS,
Aeon... all set ID to something that isn't in a hardcoded allowlist);
it reads the full os-release surface plus ID_LIKE, then cross-checks
against which package-manager binaries actually exist. When the two
disagree, `confident` is False and the caller must refuse any write —
see package_engine.py.
"""
import os
import shutil
from dataclasses import dataclass, field
from typing import Callable, Optional

# Every os-release field the spec asks us to read — never ID alone.
_OS_RELEASE_FIELDS = (
    "ID", "ID_LIKE", "NAME", "PRETTY_NAME", "VERSION_ID",
    "VERSION_CODENAME", "UBUNTU_CODENAME", "VARIANT", "VARIANT_ID",
)

# Tool binaries this page cares about — presence is checked for real,
# never inferred from the distro name.
TRACKED_TOOLS = (
    "apt", "apt-get", "dpkg", "dnf", "rpm-ostree", "pacman", "zypper",
    "transactional-update", "flatpak",
)

FAMILY_DEBIAN = "debian"
FAMILY_FEDORA = "fedora"
FAMILY_ARCH = "arch"
FAMILY_OPENSUSE = "opensuse"
FAMILY_UNKNOWN = "unknown"

SYSTEM_TRADITIONAL = "traditional"
SYSTEM_IMMUTABLE = "immutable"
SYSTEM_TRANSACTIONAL = "transactional"
SYSTEM_UNKNOWN = "unknown"

_DEBIAN_IDS = {"debian", "ubuntu", "pop", "linuxmint", "peppermint",
               "elementary", "zorin", "kali", "mxlinux", "neon", "raspbian"}
_FEDORA_IDS = {"fedora", "rhel", "centos", "rocky", "almalinux", "nobara"}
_ARCH_IDS = {"arch", "archarm", "manjaro", "garuda", "endeavouros",
             "cachyos", "artix"}
_OPENSUSE_IDS = {"opensuse", "opensuse-leap", "opensuse-tumbleweed",
                  "opensuse-microos", "opensuse-aeon", "opensuse-kalpa",
                  "sles", "sled"}

# VARIANT_ID / ID values that mark an image-based (rpm-ostree) Fedora
# variant — Silverblue/Kinoite/Sericea/Onyx and their generic "atomic
# desktops" successors.
_FEDORA_IMMUTABLE_MARKERS = {
    "silverblue", "kinoite", "sericea", "onyx", "atomic",
    "iot", "coreos",
}
_OPENSUSE_TRANSACTIONAL_IDS = {"opensuse-microos", "opensuse-aeon", "opensuse-kalpa"}


@dataclass
class DistroProfile:
    id: str = ""
    id_like: list = field(default_factory=list)
    name: str = ""
    pretty_name: str = ""
    version_id: str = ""
    version_codename: str = ""
    ubuntu_codename: str = ""
    variant: str = ""
    variant_id: str = ""

    family: str = FAMILY_UNKNOWN
    package_manager: str = "unknown"
    system_type: str = SYSTEM_UNKNOWN

    tools_present: dict = field(default_factory=dict)

    # False when the family/package-manager guess and the real tool
    # presence on disk disagree — the UI must show "Rilevamento da
    # verificare" and refuse any repository write in that state.
    confident: bool = True
    uncertainty_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "id_like": list(self.id_like), "name": self.name,
            "pretty_name": self.pretty_name, "version_id": self.version_id,
            "version_codename": self.version_codename,
            "ubuntu_codename": self.ubuntu_codename,
            "variant": self.variant, "variant_id": self.variant_id,
            "family": self.family, "package_manager": self.package_manager,
            "system_type": self.system_type,
            "tools_present": dict(self.tools_present),
            "confident": self.confident,
            "uncertainty_reason": self.uncertainty_reason,
        }


def _parse_os_release(path: str) -> dict:
    values = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, raw = line.partition("=")
                key = key.strip()
                if key not in _OS_RELEASE_FIELDS:
                    continue
                values[key] = raw.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def _family_from_ids(distro_id: str, id_like: list) -> str:
    tokens = [distro_id] + list(id_like)
    if any(t in _ARCH_IDS or t == "arch" for t in tokens):
        return FAMILY_ARCH
    if any(t in _OPENSUSE_IDS or "suse" in t for t in tokens):
        return FAMILY_OPENSUSE
    if any(t in _FEDORA_IDS or t in ("rhel", "fedora") for t in tokens):
        return FAMILY_FEDORA
    if any(t in _DEBIAN_IDS or t in ("debian", "ubuntu") for t in tokens):
        return FAMILY_DEBIAN
    return FAMILY_UNKNOWN


_FAMILY_PACKAGE_MANAGER = {
    FAMILY_DEBIAN: "apt",
    FAMILY_FEDORA: "dnf",
    FAMILY_ARCH: "pacman",
    FAMILY_OPENSUSE: "zypper",
    FAMILY_UNKNOWN: "unknown",
}

# family -> tool names that, if ANY is present, corroborate that family.
_FAMILY_CORROBORATING_TOOLS = {
    FAMILY_DEBIAN: {"apt", "apt-get", "dpkg"},
    FAMILY_FEDORA: {"dnf", "rpm-ostree"},
    FAMILY_ARCH: {"pacman"},
    FAMILY_OPENSUSE: {"zypper", "transactional-update"},
}


def _detect_system_type(family: str, distro_id: str, variant_id: str,
                          tools: dict, run_root: str) -> str:
    if family == FAMILY_FEDORA:
        marker_hit = any(m in variant_id for m in _FEDORA_IMMUTABLE_MARKERS)
        ostree_booted = os.path.exists(os.path.join(run_root, "ostree-booted"))
        if marker_hit or ostree_booted or (tools.get("rpm-ostree") and not tools.get("dnf")):
            return SYSTEM_IMMUTABLE
        return SYSTEM_TRADITIONAL
    if family == FAMILY_OPENSUSE:
        if distro_id in _OPENSUSE_TRANSACTIONAL_IDS or tools.get("transactional-update"):
            return SYSTEM_TRANSACTIONAL
        return SYSTEM_TRADITIONAL
    if family in (FAMILY_DEBIAN, FAMILY_ARCH):
        return SYSTEM_TRADITIONAL
    return SYSTEM_UNKNOWN


def detect_distro_profile(
    os_release_path: str = "/etc/os-release",
    run_root: str = "/run",
    which: Optional[Callable[[str], "str | None"]] = None,
) -> DistroProfile:
    """Read-only, side-effect-free detection. `which` is injectable so
    tests can fake tool presence without touching the real PATH."""
    which = which or shutil.which
    fields = _parse_os_release(os_release_path)

    distro_id = fields.get("ID", "").lower()
    id_like = [t for t in fields.get("ID_LIKE", "").lower().split() if t]
    variant_id = fields.get("VARIANT_ID", "").lower()

    tools_present = {tool: bool(which(tool)) for tool in TRACKED_TOOLS}

    family = _family_from_ids(distro_id, id_like)
    package_manager = _FAMILY_PACKAGE_MANAGER[family]

    confident = True
    reason = ""
    if family == FAMILY_UNKNOWN:
        confident = False
        reason = "family_unresolved"
    else:
        corroborating = _FAMILY_CORROBORATING_TOOLS.get(family, set())
        if corroborating and not any(tools_present.get(t) for t in corroborating):
            # os-release says one family, but none of the tools that
            # family implies are actually on this machine.
            confident = False
            reason = "tools_missing_for_family"

    system_type = _detect_system_type(family, distro_id, variant_id, tools_present, run_root)
    if system_type == SYSTEM_UNKNOWN and family != FAMILY_UNKNOWN:
        # Shouldn't normally happen (every known family resolves a
        # system_type), but never silently claim "traditional".
        confident = confident and True

    return DistroProfile(
        id=distro_id,
        id_like=id_like,
        name=fields.get("NAME", ""),
        pretty_name=fields.get("PRETTY_NAME", ""),
        version_id=fields.get("VERSION_ID", ""),
        version_codename=fields.get("VERSION_CODENAME", ""),
        ubuntu_codename=fields.get("UBUNTU_CODENAME", ""),
        variant=fields.get("VARIANT", ""),
        variant_id=variant_id,
        family=family,
        package_manager=package_manager if confident else "unknown",
        system_type=system_type,
        tools_present=tools_present,
        confident=confident,
        uncertainty_reason=reason,
    )
