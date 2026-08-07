"""Generic privileged install/remove engine for "pack" modules (see
core/gaming_pack.py, core/video_editing_pack.py).

The package-manager command shape and the pre-flight / post-transaction
verification logic below are identical no matter which pack calls them —
only three things vary per pack, and are supplied through PackInstaller:
- `pack`: the module to ask "is this package installed" (`pack._is_installed`)
  and to read the AVAILABLE/ALREADY_INSTALLED state constants from;
- `pack_name`: which core/package_pack_state.py JSON file records installs;
- `message_prefix`: the i18n key prefix for friendly_message strings
  (e.g. "video_pack" -> "video_pack_install_done", "video_pack_install_failed"...).

core/gaming_pack_installer.py predates this and is not migrated to it —
avoids touching a shipped, tested feature for no functional gain. This is
used by video_editing_pack and any future pack.
"""
from dataclasses import dataclass, field
from typing import Optional

from core.executor import run_pkexec_full, run_command_full, INSTALL_TIMEOUT, Job, CommandResult
from core import package_pack_state as pps

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


class PackInstaller:
    """One instance wraps a single pack module: which family-aware
    is_installed() it queries, which state file it persists to, and which
    i18n key prefix its friendly_message strings share."""

    def __init__(self, pack, pack_name: str, message_prefix: str):
        self._pack = pack
        self._pack_name = pack_name
        self._prefix = message_prefix

    def _msg(self, suffix: str) -> str:
        return f"{self._prefix}_{suffix}"

    def install_selected(self, component_ids: list, profile, previews: list, job: Optional[Job] = None) -> InstallSelectionResult:
        """
        previews must be the exact list a pack.scan() call returned for the
        current profile — component_ids not present there, or present but
        not in pack.AVAILABLE state, are skipped rather than guessed at.
        """
        by_id = {p.component_id: p for p in previews}
        family = profile.family if hasattr(profile, "family") else profile
        packages = []
        skipped = []
        installed_by_component = {}
        preexisting_by_component = {}
        for component_id in component_ids:
            preview = by_id.get(component_id)
            if preview is None or preview.state != self._pack.AVAILABLE or not preview.suggested_packages:
                skipped.append(component_id)
                continue
            packages.extend(preview.suggested_packages)
            installed_by_component[component_id] = list(preview.suggested_packages)
            preexisting_by_component[component_id] = list(preview.installed_packages)
        packages = _dedupe(packages)

        if not packages:
            return InstallSelectionResult(
                False, skipped_component_ids=skipped,
                friendly_message=self._msg("install_nothing_selected"),
            )

        command_builder = _INSTALL_COMMAND.get(family)
        if command_builder is None:
            return InstallSelectionResult(
                False, skipped_component_ids=component_ids,
                friendly_message=self._msg("install_unsupported_family"),
            )

        preflight_ok, preflight_detail = _preflight_availability(family, packages, job=job)
        if not preflight_ok:
            return InstallSelectionResult(
                False,
                skipped_component_ids=skipped,
                friendly_message=self._msg("install_precheck_failed"),
                technical_detail=preflight_detail,
            )

        install_command = command_builder(packages)
        result = run_pkexec_full(install_command, timeout=INSTALL_TIMEOUT, job=job)

        # The transaction's own exit code is never the final word: a
        # package manager can exit non-zero because of an unrelated
        # problem (e.g. a third-party repository with a broken signature)
        # while still completing the requested transaction. What is
        # really on disk decides the outcome.
        verified = [pkg for pkg in packages if self._pack._is_installed(family, pkg, job=job)]
        verified_set = set(verified)
        missing = [pkg for pkg in packages if pkg not in verified_set]
        transaction_detail = _command_result_text(result, family, "install-transaction")

        if not verified:
            return InstallSelectionResult(
                False,
                installed_packages=[],
                verified_packages=[],
                skipped_component_ids=skipped,
                friendly_message=self._msg("install_failed"),
                technical_detail=transaction_detail,
                command=install_command,
            )

        # Register Toolbox ownership for every component whose full set of
        # packages is verified installed — including a partial run — so it
        # can be removed safely later. A component with only some of its
        # packages installed is intentionally left unregistered.
        if hasattr(profile, "family"):
            for component_id, wanted_packages in installed_by_component.items():
                if all(pkg in verified_set for pkg in wanted_packages):
                    pps.record_install(
                        self._pack_name,
                        profile,
                        component_id,
                        wanted_packages,
                        preexisting_by_component.get(component_id, []),
                        install_command,
                    )

        if missing:
            return InstallSelectionResult(
                False,
                installed_packages=verified,
                verified_packages=verified,
                skipped_component_ids=skipped,
                friendly_message=self._msg("install_partial"),
                technical_detail="\n\n".join([
                    transaction_detail,
                    "step: post-install verification",
                    f"package manager: {family}",
                    "installed: " + ", ".join(verified),
                    "still missing: " + ", ".join(missing),
                ]),
                command=install_command,
            )

        return InstallSelectionResult(
            True,
            installed_packages=packages,
            verified_packages=verified,
            skipped_component_ids=skipped,
            friendly_message=self._msg("install_done") if result.ok else self._msg("install_done_with_warning"),
            technical_detail="" if result.ok else transaction_detail,
            command=install_command,
        )

    def removable_component_ids(self, profile, previews: list) -> set:
        if not hasattr(profile, "family"):
            return set()
        removable = set()
        for preview in previews:
            if preview.state != self._pack.ALREADY_INSTALLED:
                continue
            record = pps.get_record(self._pack_name, preview.component_id)
            if not record or record.get("family") != profile.family:
                continue
            installed_packages = record.get("installed_packages") or []
            if installed_packages and all(self._pack._is_installed(profile.family, package) for package in installed_packages):
                removable.add(preview.component_id)
        return removable

    def remove_selected(self, component_ids: list, profile, previews: list, job: Optional[Job] = None) -> InstallSelectionResult:
        family = profile.family if hasattr(profile, "family") else profile
        command_builder = _REMOVE_COMMAND.get(family)
        if command_builder is None:
            return InstallSelectionResult(False, friendly_message=self._msg("remove_unsupported_family"))

        by_id = {p.component_id: p for p in previews}
        removable_ids = []
        packages_by_component = {}
        packages = []
        for component_id in component_ids:
            preview = by_id.get(component_id)
            record = pps.get_record(self._pack_name, component_id)
            if preview is None or preview.state != self._pack.ALREADY_INSTALLED or not record or record.get("family") != family:
                continue
            installed_packages = list(record.get("installed_packages") or [])
            if not installed_packages:
                continue
            missing = [pkg for pkg in installed_packages if not self._pack._is_installed(family, pkg, job=job)]
            if missing:
                return InstallSelectionResult(
                    False,
                    friendly_message=self._msg("remove_precheck_failed"),
                    technical_detail="\n".join([
                        "step: removal-precheck",
                        f"package manager: {family}",
                        f"package: {', '.join(missing)}",
                        "error: recorded package is no longer installed",
                    ]),
                )
            removable_ids.append(component_id)
            packages_by_component[component_id] = installed_packages
            packages.extend(installed_packages)
        packages = _dedupe(packages)
        if not packages:
            return InstallSelectionResult(False, friendly_message=self._msg("remove_nothing_selected"))

        remove_command = command_builder(packages)
        result = run_pkexec_full(remove_command, timeout=INSTALL_TIMEOUT, job=job)

        still_installed = {pkg for pkg in packages if self._pack._is_installed(family, pkg, job=job)}
        removed = [pkg for pkg in packages if pkg not in still_installed]
        transaction_detail = _command_result_text(result, family, "remove-transaction")

        if not removed:
            return InstallSelectionResult(
                False,
                friendly_message=self._msg("remove_failed"),
                technical_detail=transaction_detail,
                command=remove_command,
            )

        # Only clear the local record for a component whose packages are
        # ALL confirmed gone — a component still partly installed keeps
        # its record, so it can be retried or completed later instead of
        # being silently forgotten by the Toolbox.
        fully_cleared = [
            component_id for component_id in removable_ids
            if all(pkg not in still_installed for pkg in packages_by_component[component_id])
        ]
        if fully_cleared:
            pps.clear_records(self._pack_name, fully_cleared)

        if still_installed:
            return InstallSelectionResult(
                False,
                installed_packages=removed,
                friendly_message=self._msg("remove_partial"),
                technical_detail="\n\n".join([
                    transaction_detail,
                    "step: post-remove verification",
                    f"package manager: {family}",
                    "removed: " + ", ".join(removed),
                    "still installed: " + ", ".join(sorted(still_installed)),
                ]),
                command=remove_command,
            )

        return InstallSelectionResult(
            True,
            installed_packages=packages,
            verified_packages=packages,
            friendly_message=self._msg("remove_done") if result.ok else self._msg("remove_done_with_warning"),
            technical_detail="" if result.ok else transaction_detail,
            command=remove_command,
        )
