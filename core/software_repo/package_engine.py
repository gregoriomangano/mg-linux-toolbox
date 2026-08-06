"""
Central package engine for "Software e repository" (Phase 4 of the
2026-08-04 block).

Single place that:
1. detects family/package-manager/immutable-or-transactional (via
   distro_profile.detect_distro_profile),
2. checks installed/available (via core.distro / core.repo_check —
   unchanged, still the source of truth for every existing page),
3. runs one operation from the fixed allow-list below (OPERATIONS),
4. records the outcome in the History (core.persistence.history_store),
5. returns a structured EngineResult the UI renders the same way
   everywhere.

Existing pages (Audio, Stampanti, Gaming, Virtualizzazione) keep using
backend/all.py directly — they already work and the spec says not to
rewrite what isn't broken. This module is the shared engine for the
new "Software e repository" page's own actions, built so those other
pages can be moved onto it gradually later instead of all at once.

Deliberately closed: `run_operation()` only accepts an operation key
already in OPERATIONS. There is no path from GUI-supplied free text to
a privileged command — see module docstrings in flatpak_manager.py,
repo_recipes.py and package_health.py for the actual argv lists.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional

from core.software_repo.distro_profile import detect_distro_profile, DistroProfile
from core.software_repo import flatpak_manager as fp
from core.software_repo import package_health as health
from core.software_repo import repo_recipes as recipes
from core.persistence.history_store import record_operation, CONFIGURATION, ACTIVATION, DEACTIVATION, TEMPORARY_CHANGE

PAGE = "software_repos"


@dataclass
class EngineResult:
    ok: bool
    friendly_message: str = ""
    technical_detail: str = ""
    reboot_required: bool = False
    logout_recommended: bool = False

    @classmethod
    def from_setup(cls, r) -> "EngineResult":
        return cls(r.ok, getattr(r, "friendly_message", ""), getattr(r, "technical_detail", ""),
                    getattr(r, "reboot_required", False), getattr(r, "logout_recommended", False))


def current_profile() -> DistroProfile:
    return detect_distro_profile()


# ── Fixed operation allow-list ──────────────────────────────────────
# key -> (callable(profile, scope, job) -> result-with-.ok, history feature_id, history entry_type)

def _op_configure_flatpak(profile, scope, job):
    return fp.configure_flatpak_and_flathub(profile, scope or fp.SCOPE_USER, job=job)


def _op_add_flathub(profile, scope, job):
    return fp.add_flathub_remote(scope or fp.SCOPE_USER, job=job)


def _op_remove_flatpak(profile, scope, job):
    return fp.remove_flatpak(profile, job=job)


def _op_install_flatseal(profile, scope, job):
    return fp.install_flatseal(scope or fp.SCOPE_USER, job=job)


def _op_update_flatpaks(profile, scope, job):
    return fp.apply_flatpak_updates(job=job)


def _op_remove_unused_flatpak_runtimes(profile, scope, job):
    return fp.remove_unused_runtimes(job=job)


def _op_repair_flatpak(profile, scope, job):
    return fp.repair_flatpak(scope or fp.SCOPE_USER, job=job)


def _op_repair_dependencies(profile, scope, job):
    return health.repair_dependencies(profile.family, job=job)


def _op_remove_orphans(profile, scope, job):
    return health.remove_orphans(profile.family, job=job)


def _op_update_indexes(profile, scope, job):
    return health.update_indexes(profile.family, job=job)


def _op_clean_cache(profile, scope, job):
    return health.clean_package_cache(profile.family, job=job)


def _op_enable_recipe(profile, scope, job):
    """`scope` doubles as the recipe_id for this one operation — kept
    this way so run_operation() never needs a third positional arg."""
    return recipes.enable_recipe(scope, profile, job=job)


def _op_disable_recipe(profile, scope, job):
    return recipes.disable_recipe(scope, profile, job=job)


def _op_toggle_repo(profile, scope, job):
    """Toggle a repository enable/disable state.
    `scope` is JSON-encoded: {"family": "opensuse"|"flatpak", "alias": <section_id>,
    "source_file": <path>, "scope": <flatpak_scope>, "enabled": bool}"""
    import json
    from core.software_repo import repo_toggle as toggle
    try:
        data = json.loads(scope)
    except (json.JSONDecodeError, ValueError):
        from core.software_repo.repo_transaction import EngineResult
        return type('Result', (), {'ok': False, 'friendly_message': 'repo_toggle_invalid_scope'})()

    family = data.get("family", "")
    enabled = data.get("enabled", False)

    if family == "opensuse":
        return toggle.set_zypper_repo_enabled(
            data["source_file"], data["alias"], enabled, job=job)
    elif family == "flatpak":
        return toggle.set_flatpak_remote_enabled(
            data["remote_name"], data["scope"], enabled, job=job)
    else:
        return type('Result', (), {'ok': False, 'friendly_message': 'repo_toggle_unknown_family'})()


def _op_remove_repo(profile, scope, job):
    """Remove a repository entirely.
    `scope` is JSON-encoded: same structure as _op_toggle_repo."""
    import json
    from core.software_repo import repo_toggle as toggle
    try:
        data = json.loads(scope)
    except (json.JSONDecodeError, ValueError):
        return type('Result', (), {'ok': False, 'friendly_message': 'repo_remove_invalid_scope'})()

    family = data.get("family", "")

    if family == "opensuse":
        return toggle.remove_zypper_repo(
            data["source_file"], data["alias"], job=job)
    elif family == "flatpak":
        return toggle.remove_flatpak_remote(
            data["remote_name"], data["scope"], job=job)
    else:
        return type('Result', (), {'ok': False, 'friendly_message': 'repo_remove_unknown_family'})()


# The one, real, version-matched Packman URL this app will ever add
# automatically — fixed here, never built from GUI text. Only offered
# when the detected distro id is exactly "opensuse-tumbleweed" and
# detection is confident (see _op_activate_packman below); Leap and
# any unresolved id are refused, never guessed at.
PACKMAN_TUMBLEWEED_URL = "http://ftp.gwdg.de/pub/linux/misc/packman/suse/openSUSE_Tumbleweed/"
PACKMAN_ALIAS = "packman"


def _op_activate_packman(profile, scope, job):
    """Add the real Packman repository for the exact detected openSUSE
    Tumbleweed release. Refuses on Leap or any distro/version that
    can't be confidently resolved — never falls back to guessing a
    URL. No package is installed and no vendor is switched; this only
    registers repository metadata."""
    from core.software_repo import repo_toggle as toggle
    if profile.family != "opensuse" or not profile.confident or profile.id != "opensuse-tumbleweed":
        return type('Result', (), {'ok': False, 'friendly_message': 'packman_not_compatible'})()
    return toggle.add_zypper_repo(PACKMAN_ALIAS, PACKMAN_TUMBLEWEED_URL, job=job)


# Values store the *name* of the module-level function, not the
# function object itself — resolved dynamically in run_operation()
# below. Binding the object directly here would capture it once at
# import time, so a test's `mock.patch("...package_engine._op_x", ...)`
# would silently patch an unused module attribute while OPERATIONS
# kept calling the original (real, possibly privileged) function. This
# is not hypothetical: it is exactly what happened in this module's
# own test suite before this fix, letting a "mocked" clean_cache/
# configure_flatpak test reach a real run_pkexec_full()/flatpak
# call on whatever machine ran the tests.
OPERATIONS = {
    "configure_flatpak":     ("_op_configure_flatpak", "software_repo.flatpak_setup", CONFIGURATION),
    "add_flathub":           ("_op_add_flathub", "software_repo.flathub_remote", ACTIVATION),
    "remove_flatpak":        ("_op_remove_flatpak", "software_repo.flatpak_remove", DEACTIVATION),
    "install_flatseal":      ("_op_install_flatseal", "software_repo.flatseal", ACTIVATION),
    "update_flatpaks":       ("_op_update_flatpaks", "software_repo.flatpak_update", TEMPORARY_CHANGE),
    "remove_unused_flatpak": ("_op_remove_unused_flatpak_runtimes", "software_repo.flatpak_unused", DEACTIVATION),
    "repair_flatpak":        ("_op_repair_flatpak", "software_repo.flatpak_repair", CONFIGURATION),
    "repair_dependencies":   ("_op_repair_dependencies", "software_repo.repair_dependencies", CONFIGURATION),
    "remove_orphans":        ("_op_remove_orphans", "software_repo.remove_orphans", DEACTIVATION),
    "update_indexes":        ("_op_update_indexes", "software_repo.update_indexes", TEMPORARY_CHANGE),
    "clean_cache":           ("_op_clean_cache", "software_repo.clean_cache", DEACTIVATION),
    "enable_recipe":         ("_op_enable_recipe", "software_repo.repo_recipe", ACTIVATION),
    "disable_recipe":        ("_op_disable_recipe", "software_repo.repo_recipe", DEACTIVATION),
    "toggle_repo":           ("_op_toggle_repo", "software_repo.repo_toggle", CONFIGURATION),
    "remove_repo":           ("_op_remove_repo", "software_repo.repo_remove", DEACTIVATION),
    "activate_packman":      ("_op_activate_packman", "software_repo.packman_activation", ACTIVATION),
}


def run_operation(operation_key: str, profile: Optional[DistroProfile] = None,
                   scope: str = "", job=None, record_history: bool = True) -> EngineResult:
    if operation_key not in OPERATIONS:
        return EngineResult(False, friendly_message="engine_operation_unknown")
    fn_name, feature_id, entry_type = OPERATIONS[operation_key]
    fn = globals()[fn_name]
    profile = profile or current_profile()

    raw = fn(profile, scope, job)
    result = EngineResult.from_setup(raw)

    if record_history:
        try:
            record_operation(
                page=PAGE, feature_id=feature_id, entry_type=entry_type, ok=result.ok,
                new_value=scope or None, friendly_message=result.friendly_message,
                technical_detail=result.technical_detail, reboot_required=result.reboot_required,
                rollback_available=False,
            )
        except Exception:
            pass
    return result
