"""Privileged install/remove operations for Gaming Pack components."""
from dataclasses import dataclass, field
from typing import Optional

from core.executor import run_pkexec_full, run_command_full, INSTALL_TIMEOUT, Job, CommandResult
from core import gaming_pack as gp
from core import gaming_pack_state as gps

_INSTALL_COMMAND = {
    "debian": lambda pkgs: ["apt-get", "install", "-y", *pkgs],
    "fedora": lambda pkgs: ["dnf", "install", "-y", *pkgs],
    "arch": lambda pkgs: ["pacman", "-S", "--noconfirm", *pkgs],
    "opensuse": lambda pkgs: ["zypper", "--non-interactive", "install", *pkgs],
}
_REMOVE_COMMAND = {
    "debian": lambda pkgs: ["apt-get", "remove", "-y", *pkgs],
    "fedora": lambda pkgs: ["dnf", "remove", "-y", *pkgs],
    "arch": lambda pkgs: ["pacman", "-R", "--noconfirm", *pkgs],
    "opensuse": lambda pkgs: ["zypper", "--non-interactive", "remove", *pkgs],
}


@dataclass
class InstallSelectionResult:
    ok: bool
    installed_packages: list = field(default_factory=list)
    verified_packages: list = field(default_factory=list)
    skipped_component_ids: list = field(default_factory=list)
    friendly_message: str = ""
    technical_detail: str = ""
    command: list = field(default_factory=list)


def _command_result_text(result: CommandResult, package_manager: str, step: str, package: str = "") -> str:
    return "\n".join([
        f"step: {step}",
        f"package manager: {package_manager}",
        f"package: {package or '—'}",
        result.technical_detail(),
    ])


def _dedupe(seq: list) -> list:
    seen = set()
    out = []
    for item in seq:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _probe_debian(package: str, job=None) -> "tuple[bool, str]":
    policy = run_command_full(["apt-cache", "policy", package], timeout=60, job=job)
    show = run_command_full(["apt-cache", "show", package], timeout=60, job=job)
    candidate = ""
    for line in policy.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Candidate:"):
            candidate = stripped.split(":", 1)[1].strip()
            break
    return (
        bool(policy.ok and show.ok and candidate and candidate != "(none)" and show.stdout.strip()),
        "\n\n".join([
            _command_result_text(policy, "apt", "availability-precheck", package),
            _command_result_text(show, "apt", "availability-precheck", package),
        ]),
    )


def _probe_fedora(package: str, job=None) -> "tuple[bool, str]":
    repoquery = run_command_full(["dnf", "repoquery", "--info", package], timeout=60, job=job)
    if repoquery.ok and repoquery.stdout.strip():
        return True, _command_result_text(repoquery, "dnf", "availability-precheck", package)
    info = run_command_full(["dnf", "info", package], timeout=60, job=job)
    text = f"{repoquery.stdout}\n{repoquery.stderr}\n{info.stdout}\n{info.stderr}".lower()
    available = info.ok and info.stdout.strip() and "no matching packages" not in text and "unable to match" not in text
    return available, "\n\n".join([
        _command_result_text(repoquery, "dnf", "availability-precheck", package),
        _command_result_text(info, "dnf", "availability-precheck", package),
    ])


def _probe_arch(package: str, job=None) -> "tuple[bool, str]":
    result = run_command_full(["pacman", "-Si", package], timeout=60, job=job)
    return bool(result.ok and result.stdout.strip()), _command_result_text(result, "pacman", "availability-precheck", package)


def _probe_opensuse(package: str, job=None) -> "tuple[bool, str]":
    search = run_command_full(["zypper", "--non-interactive", "search", "-s", package], timeout=60, job=job)
    info = run_command_full(["zypper", "--non-interactive", "info", package], timeout=60, job=job)
    text = f"{search.stdout}\n{search.stderr}\n{info.stdout}\n{info.stderr}".lower()
    available = info.ok and info.stdout.strip() and "not found" not in text and "no matching items found" not in text
    return available, "\n\n".join([
        _command_result_text(search, "zypper", "availability-precheck", package),
        _command_result_text(info, "zypper", "availability-precheck", package),
    ])


_PROBE_COMMAND = {
    "debian": _probe_debian,
    "fedora": _probe_fedora,
    "arch": _probe_arch,
    "opensuse": _probe_opensuse,
}


def _preflight_availability(family: str, packages: list, job=None) -> "tuple[bool, str]":
    probe = _PROBE_COMMAND.get(family)
    if probe is None:
        return False, f"step: availability-precheck\npackage manager: {family or 'unknown'}\npackage: —\nerror: unsupported family"
    details = []
    for package in packages:
        available, detail = probe(package, job=job)
        details.append(detail)
        if not available:
            details.append(f"blocking package: {package}")
            return False, "\n\n".join(details)
    return True, "\n\n".join(details)


def install_selected(
    component_ids: list,
    profile,
    previews: list,
    job: Optional[Job] = None,
) -> InstallSelectionResult:
    """
    previews must be the exact list a gp.scan() call returned for the
    current profile — component_ids not present there, or present but
    not in gp.AVAILABLE state, are skipped rather than guessed at.
    """
    by_id = {p.component_id: p for p in previews}
    family = profile.family if hasattr(profile, "family") else profile
    packages = []
    skipped = []
    installed_by_component = {}
    preexisting_by_component = {}
    for component_id in component_ids:
        preview = by_id.get(component_id)
        if preview is None or preview.state != gp.AVAILABLE or not preview.suggested_packages:
            skipped.append(component_id)
            continue
        packages.extend(preview.suggested_packages)
        installed_by_component[component_id] = list(preview.suggested_packages)
        preexisting_by_component[component_id] = list(preview.installed_packages)
    packages = _dedupe(packages)

    if not packages:
        return InstallSelectionResult(
            False, skipped_component_ids=skipped,
            friendly_message="gaming_pack_install_nothing_selected",
        )

    command_builder = _INSTALL_COMMAND.get(family)
    if command_builder is None:
        return InstallSelectionResult(
            False, skipped_component_ids=component_ids,
            friendly_message="gaming_pack_install_unsupported_family",
        )

    preflight_ok, preflight_detail = _preflight_availability(family, packages, job=job)
    if not preflight_ok:
        return InstallSelectionResult(
            False,
            skipped_component_ids=skipped,
            friendly_message="gaming_pack_install_precheck_failed",
            technical_detail=preflight_detail,
        )

    install_command = command_builder(packages)
    result = run_pkexec_full(install_command, timeout=INSTALL_TIMEOUT, job=job)
    if not result.ok:
        return InstallSelectionResult(
            False,
            installed_packages=[],
            skipped_component_ids=skipped,
            friendly_message="gaming_pack_install_failed",
            technical_detail=_command_result_text(result, family, "install-transaction"),
            command=install_command,
        )

    verified = []
    verification_failures = []
    for package in packages:
        if gp._is_installed(family, package, job=job):
            verified.append(package)
        else:
            verification_failures.append(package)

    if verification_failures:
        return InstallSelectionResult(
            False,
            installed_packages=[],
            verified_packages=verified,
            skipped_component_ids=skipped,
            friendly_message="gaming_pack_install_verification_failed",
            technical_detail="\n\n".join([
                _command_result_text(result, family, "install-transaction"),
                "step: post-install verification",
                f"package manager: {family}",
                "missing after successful transaction: " + ", ".join(verification_failures),
            ]),
            command=install_command,
        )

    if hasattr(profile, "family"):
        for component_id, installed_packages in installed_by_component.items():
            gps.record_install(
                profile,
                component_id,
                installed_packages,
                preexisting_by_component.get(component_id, []),
                install_command,
            )

    return InstallSelectionResult(
        True,
        installed_packages=packages,
        verified_packages=verified,
        skipped_component_ids=skipped,
        friendly_message="gaming_pack_install_done",
        technical_detail="",
        command=install_command,
    )


def removable_component_ids(profile, previews: list) -> set:
    if not hasattr(profile, "family"):
        return set()
    removable = set()
    for preview in previews:
        if preview.state != gp.ALREADY_INSTALLED:
            continue
        record = gps.get_record(preview.component_id)
        if not record or record.get("family") != profile.family:
            continue
        installed_packages = record.get("installed_packages") or []
        if installed_packages and all(gp._is_installed(profile.family, package) for package in installed_packages):
            removable.add(preview.component_id)
    return removable


def remove_selected(component_ids: list, profile, previews: list, job: Optional[Job] = None) -> InstallSelectionResult:
    family = profile.family if hasattr(profile, "family") else profile
    command_builder = _REMOVE_COMMAND.get(family)
    if command_builder is None:
        return InstallSelectionResult(False, friendly_message="gaming_pack_remove_unsupported_family")

    by_id = {p.component_id: p for p in previews}
    removable_ids = []
    packages = []
    for component_id in component_ids:
        preview = by_id.get(component_id)
        record = gps.get_record(component_id)
        if preview is None or preview.state != gp.ALREADY_INSTALLED or not record or record.get("family") != family:
            continue
        installed_packages = list(record.get("installed_packages") or [])
        if not installed_packages:
            continue
        missing = [pkg for pkg in installed_packages if not gp._is_installed(family, pkg, job=job)]
        if missing:
            return InstallSelectionResult(
                False,
                friendly_message="gaming_pack_remove_precheck_failed",
                technical_detail="\n".join([
                    "step: removal-precheck",
                    f"package manager: {family}",
                    f"package: {', '.join(missing)}",
                    "error: recorded package is no longer installed",
                ]),
            )
        removable_ids.append(component_id)
        packages.extend(installed_packages)
    packages = _dedupe(packages)
    if not packages:
        return InstallSelectionResult(False, friendly_message="gaming_pack_remove_nothing_selected")

    remove_command = command_builder(packages)
    result = run_pkexec_full(remove_command, timeout=INSTALL_TIMEOUT, job=job)
    if not result.ok:
        return InstallSelectionResult(
            False,
            friendly_message="gaming_pack_remove_failed",
            technical_detail=_command_result_text(result, family, "remove-transaction"),
            command=remove_command,
        )

    still_installed = [pkg for pkg in packages if gp._is_installed(family, pkg, job=job)]
    if still_installed:
        return InstallSelectionResult(
            False,
            friendly_message="gaming_pack_remove_verification_failed",
            technical_detail="\n\n".join([
                _command_result_text(result, family, "remove-transaction"),
                "step: post-remove verification",
                f"package manager: {family}",
                "still installed after successful transaction: " + ", ".join(still_installed),
            ]),
            command=remove_command,
        )

    gps.clear_records(removable_ids)
    return InstallSelectionResult(
        True,
        installed_packages=packages,
        verified_packages=packages,
        friendly_message="gaming_pack_remove_done",
        command=remove_command,
    )
