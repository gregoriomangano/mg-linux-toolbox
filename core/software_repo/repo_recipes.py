"""
Additional-repository recipes: a versioned, closed catalogue — never a
free-text URL box. Every recipe here is data (compatible family/
versions/system type, risk, files touched, conflicts) plus, for the
"guided" level only, a fixed argv-list enable/disable command built
from validated os-release fields (never a distro-supplied string
concatenated into a shell command).

Level "guided" is auto-offered by the page after the checks in
plan_activation(); level "advanced" is informational only in this
first implementation — the UI must show the strong warning text and
never wire an automatic activation button for it.
"""
import re
from dataclasses import dataclass, field

from core.executor import run_pkexec_full, run_command_full, INSTALL_TIMEOUT

LEVEL_GUIDED = "guided"
LEVEL_ADVANCED = "advanced"

_CODENAME_RE = re.compile(r"^[a-z][a-z0-9]*$")
_VERSION_ID_RE = re.compile(r"^\d{1,3}$")


@dataclass
class RepoRecipe:
    id: str
    name_key: str
    description_key: str
    family: str
    compatible_distros: list          # [] = any distro in the family
    compatible_system_types: list     # e.g. ["traditional"]
    level: str
    source_key: str
    risk: str                          # "low" | "medium" | "high"
    verify_method_key: str
    files_involved: list
    backup_required: bool
    known_conflicts: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


RECIPES = [
    RepoRecipe(
        id="ubuntu_universe", name_key="recipe_ubuntu_universe_name",
        description_key="recipe_ubuntu_universe_desc",
        family="debian", compatible_distros=["ubuntu", "pop", "linuxmint", "peppermint"],
        compatible_system_types=["traditional"], level=LEVEL_GUIDED,
        source_key="recipe_source_ubuntu_official", risk="low",
        verify_method_key="recipe_verify_apt_update",
        files_involved=["/etc/apt/sources.list", "/etc/apt/sources.list.d/*"],
        backup_required=True,
    ),
    RepoRecipe(
        id="ubuntu_multiverse", name_key="recipe_ubuntu_multiverse_name",
        description_key="recipe_ubuntu_multiverse_desc",
        family="debian", compatible_distros=["ubuntu", "pop", "linuxmint", "peppermint"],
        compatible_system_types=["traditional"], level=LEVEL_GUIDED,
        source_key="recipe_source_ubuntu_official", risk="low",
        verify_method_key="recipe_verify_apt_update",
        files_involved=["/etc/apt/sources.list", "/etc/apt/sources.list.d/*"],
        backup_required=True,
    ),
    RepoRecipe(
        id="debian_backports", name_key="recipe_debian_backports_name",
        description_key="recipe_debian_backports_desc",
        family="debian", compatible_distros=["debian"],
        compatible_system_types=["traditional"], level=LEVEL_GUIDED,
        source_key="recipe_source_debian_official", risk="low",
        verify_method_key="recipe_verify_apt_update",
        files_involved=["/etc/apt/sources.list.d/*backports*"],
        backup_required=True,
    ),
    RepoRecipe(
        id="ubuntu_backports", name_key="recipe_ubuntu_backports_name",
        description_key="recipe_ubuntu_backports_desc",
        family="debian", compatible_distros=["ubuntu", "pop", "linuxmint"],
        compatible_system_types=["traditional"], level=LEVEL_GUIDED,
        source_key="recipe_source_ubuntu_official", risk="low",
        verify_method_key="recipe_verify_apt_update",
        files_involved=["/etc/apt/sources.list.d/*backports*"],
        backup_required=True,
    ),
    RepoRecipe(
        id="rpmfusion", name_key="recipe_rpmfusion_name",
        description_key="recipe_rpmfusion_desc",
        family="fedora", compatible_distros=["fedora"],
        compatible_system_types=["traditional"], level=LEVEL_GUIDED,
        source_key="recipe_source_rpmfusion", risk="medium",
        verify_method_key="recipe_verify_dnf_repolist",
        files_involved=["/etc/yum.repos.d/rpmfusion-*.repo"],
        backup_required=False,
        known_conflicts=["negativo17"],
    ),
    RepoRecipe(
        id="negativo17", name_key="recipe_negativo17_name",
        description_key="recipe_negativo17_desc",
        family="fedora", compatible_distros=["fedora"],
        compatible_system_types=["traditional"], level=LEVEL_ADVANCED,
        source_key="recipe_source_negativo17", risk="medium",
        verify_method_key="recipe_verify_dnf_repolist",
        files_involved=["/etc/yum.repos.d/*negativo17*"],
        backup_required=False,
        known_conflicts=["rpmfusion"],
    ),
    RepoRecipe(
        id="packman", name_key="recipe_packman_name",
        description_key="recipe_packman_desc",
        family="opensuse", compatible_distros=[],
        compatible_system_types=["traditional"], level=LEVEL_ADVANCED,
        source_key="recipe_source_packman", risk="medium",
        verify_method_key="recipe_verify_zypper_lr",
        files_involved=["/etc/zypp/repos.d/*packman*"],
        backup_required=False,
    ),
    RepoRecipe(
        id="debian_multimedia", name_key="recipe_debian_multimedia_name",
        description_key="recipe_debian_multimedia_desc",
        family="debian", compatible_distros=["debian"],
        compatible_system_types=["traditional"], level=LEVEL_ADVANCED,
        source_key="recipe_source_debian_multimedia", risk="medium",
        verify_method_key="recipe_verify_apt_update",
        files_involved=["/etc/apt/sources.list.d/*multimedia*"],
        backup_required=True,
    ),
    RepoRecipe(
        id="ppa_generic", name_key="recipe_ppa_name",
        description_key="recipe_ppa_desc",
        family="debian", compatible_distros=["ubuntu"],
        compatible_system_types=["traditional"], level=LEVEL_ADVANCED,
        source_key="recipe_source_ppa", risk="high",
        verify_method_key="recipe_verify_apt_update",
        files_involved=["/etc/apt/sources.list.d/*"],
        backup_required=True,
    ),
    RepoRecipe(
        id="chaotic_aur", name_key="recipe_chaotic_aur_name",
        description_key="recipe_chaotic_aur_desc",
        family="arch", compatible_distros=[],
        compatible_system_types=["traditional"], level=LEVEL_ADVANCED,
        source_key="recipe_source_chaotic_aur", risk="high",
        verify_method_key="recipe_verify_pacman_conf",
        files_involved=["/etc/pacman.conf"],
        backup_required=True,
    ),
    RepoRecipe(
        id="opensuse_obs", name_key="recipe_obs_name",
        description_key="recipe_obs_desc",
        family="opensuse", compatible_distros=[],
        compatible_system_types=["traditional"], level=LEVEL_ADVANCED,
        source_key="recipe_source_obs", risk="high",
        verify_method_key="recipe_verify_zypper_lr",
        files_involved=["/etc/zypp/repos.d/*"],
        backup_required=True,
    ),
]

RECIPES_BY_ID = {r.id: r for r in RECIPES}


def recipes_for_profile(distro_profile) -> list:
    """Recipes whose family/distro/system_type match, most-specific
    compatible_distros first — never a recipe outside this family."""
    out = []
    for r in RECIPES:
        if r.family != distro_profile.family:
            continue
        if r.compatible_distros and distro_profile.id not in r.compatible_distros:
            continue
        if r.compatible_system_types and distro_profile.system_type not in r.compatible_system_types:
            continue
        out.append(r)
    return out


def conflicts_for(recipe_id: str, already_enabled_ids: set) -> list:
    recipe = RECIPES_BY_ID.get(recipe_id)
    if not recipe:
        return []
    return [c for c in recipe.known_conflicts if c in already_enabled_ids]


@dataclass
class ActivationPlan:
    recipe_id: str
    allowed: bool
    reason: str = ""
    preview_lines: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)


def plan_activation(recipe_id: str, distro_profile, already_enabled_ids: set = frozenset()) -> ActivationPlan:
    recipe = RECIPES_BY_ID.get(recipe_id)
    if recipe is None:
        return ActivationPlan(recipe_id, False, reason="recipe_unknown")
    if recipe.level != LEVEL_GUIDED:
        return ActivationPlan(recipe_id, False, reason="recipe_advanced_info_only")
    if not distro_profile.confident:
        return ActivationPlan(recipe_id, False, reason="recipe_distro_unverified")
    if recipe not in recipes_for_profile(distro_profile):
        return ActivationPlan(recipe_id, False, reason="recipe_not_compatible")

    conflicts = conflicts_for(recipe_id, already_enabled_ids)
    if conflicts:
        return ActivationPlan(recipe_id, False, reason="recipe_conflict_detected", conflicts=conflicts)

    return ActivationPlan(recipe_id, True, preview_lines=list(recipe.files_involved))


@dataclass
class RecipeResult:
    ok: bool
    friendly_message: str = ""
    technical_detail: str = ""


def _validated_codename(distro_profile) -> "str | None":
    codename = (distro_profile.version_codename or distro_profile.ubuntu_codename or "").lower()
    if _CODENAME_RE.match(codename):
        return codename
    return None


def enable_recipe(recipe_id: str, distro_profile, job=None) -> RecipeResult:
    """Only the guided-level recipes are executable — everything else
    returns a clean 'informational only' failure instead of running
    anything."""
    plan = plan_activation(recipe_id, distro_profile)
    if not plan.allowed:
        return RecipeResult(False, friendly_message=plan.reason)

    if recipe_id in ("ubuntu_universe", "ubuntu_multiverse"):
        component = "universe" if recipe_id == "ubuntu_universe" else "multiverse"
        result = run_pkexec_full(["add-apt-repository", "-y", component], timeout=INSTALL_TIMEOUT, job=job)
        return RecipeResult(result.ok,
                             friendly_message="recipe_enable_success" if result.ok else "recipe_enable_failed",
                             technical_detail="" if result.ok else result.technical_detail())

    if recipe_id == "debian_backports":
        codename = _validated_codename(distro_profile)
        if not codename:
            return RecipeResult(False, friendly_message="recipe_codename_unresolved")
        line = f"deb http://deb.debian.org/debian {codename}-backports main"
        result = run_pkexec_full(["add-apt-repository", "-y", line], timeout=INSTALL_TIMEOUT, job=job)
        return RecipeResult(result.ok,
                             friendly_message="recipe_enable_success" if result.ok else "recipe_enable_failed",
                             technical_detail="" if result.ok else result.technical_detail())

    if recipe_id == "ubuntu_backports":
        codename = _validated_codename(distro_profile)
        if not codename:
            return RecipeResult(False, friendly_message="recipe_codename_unresolved")
        line = f"deb http://archive.ubuntu.com/ubuntu {codename}-backports main restricted universe multiverse"
        result = run_pkexec_full(["add-apt-repository", "-y", line], timeout=INSTALL_TIMEOUT, job=job)
        return RecipeResult(result.ok,
                             friendly_message="recipe_enable_success" if result.ok else "recipe_enable_failed",
                             technical_detail="" if result.ok else result.technical_detail())

    if recipe_id == "rpmfusion":
        version = distro_profile.version_id
        if not _VERSION_ID_RE.match(version or ""):
            return RecipeResult(False, friendly_message="recipe_version_unresolved")
        urls = [
            f"https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-{version}.noarch.rpm",
            f"https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-{version}.noarch.rpm",
        ]
        result = run_pkexec_full(["dnf", "install", "-y"] + urls, timeout=INSTALL_TIMEOUT, job=job)
        return RecipeResult(result.ok,
                             friendly_message="recipe_enable_success" if result.ok else "recipe_enable_failed",
                             technical_detail="" if result.ok else result.technical_detail())

    return RecipeResult(False, friendly_message="recipe_not_implemented")


def disable_recipe(recipe_id: str, distro_profile, job=None) -> RecipeResult:
    if recipe_id in ("ubuntu_universe", "ubuntu_multiverse"):
        component = "universe" if recipe_id == "ubuntu_universe" else "multiverse"
        result = run_pkexec_full(["add-apt-repository", "-y", "--remove", component],
                                 timeout=INSTALL_TIMEOUT, job=job)
        return RecipeResult(result.ok,
                             friendly_message="recipe_disable_success" if result.ok else "recipe_disable_failed",
                             technical_detail="" if result.ok else result.technical_detail())

    if recipe_id in ("debian_backports", "ubuntu_backports"):
        codename = _validated_codename(distro_profile)
        if not codename:
            return RecipeResult(False, friendly_message="recipe_codename_unresolved")
        base = "deb.debian.org/debian" if recipe_id == "debian_backports" else "archive.ubuntu.com/ubuntu"
        suffix = "main" if recipe_id == "debian_backports" else "main restricted universe multiverse"
        line = f"deb http://{base} {codename}-backports {suffix}"
        result = run_pkexec_full(["add-apt-repository", "-y", "--remove", line],
                                 timeout=INSTALL_TIMEOUT, job=job)
        return RecipeResult(result.ok,
                             friendly_message="recipe_disable_success" if result.ok else "recipe_disable_failed",
                             technical_detail="" if result.ok else result.technical_detail())

    return RecipeResult(False, friendly_message="recipe_not_implemented")
