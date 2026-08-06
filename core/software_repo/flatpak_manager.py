"""
Flatpak / Flathub detection and guided setup.

Detection is always unprivileged (`flatpak remotes --system` /
`--user` are plain read commands, no root needed). Every write here
(remote-add, install, repair, unused-runtime removal) is one call from
the app's fixed allow-list, run with argument lists — never a shell
string built out of GUI input — and system-scope writes only ever go
through pkexec, requested at the moment of the click, never silently.
"""
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from core.executor import run_command, run_command_full, run_pkexec_full, INSTALL_TIMEOUT
from core.distro import distro

FLATHUB_REMOTE_NAME = "flathub"
FLATHUB_REPO_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"
FLATHUB_HOST = "dl.flathub.org"

SCOPE_SYSTEM = "system"
SCOPE_USER = "user"

# The real, stable Flatpak application id — read from here everywhere
# else in the app that needs it (never re-typed/guessed at a call site).
FLATSEAL_APP_ID = "com.github.tchx84.Flatseal"

_PORTAL_BACKENDS = ("xdg-desktop-portal-gtk", "xdg-desktop-portal-gnome", "xdg-desktop-portal-kde")


@dataclass
class FlatpakRemote:
    name: str
    url: str
    enabled: bool
    scope: str  # "system" | "user"

    @property
    def is_flathub(self) -> bool:
        return self.name == FLATHUB_REMOTE_NAME or FLATHUB_HOST in self.url


@dataclass
class FlatpakState:
    installed: bool = False
    version: str = ""
    system_remotes: list = field(default_factory=list)   # list[FlatpakRemote]
    user_remotes: list = field(default_factory=list)      # list[FlatpakRemote]
    flathub_system: bool = False
    flathub_user: bool = False
    other_remotes: list = field(default_factory=list)     # list[FlatpakRemote], non-flathub
    portal_present: bool = False
    portal_backend: str = ""     # "gtk" | "kde" | "gnome" | ""
    integration_complete: bool = False

    def to_dict(self) -> dict:
        return {
            "installed": self.installed, "version": self.version,
            "system_remotes": [r.__dict__ for r in self.system_remotes],
            "user_remotes": [r.__dict__ for r in self.user_remotes],
            "flathub_system": self.flathub_system, "flathub_user": self.flathub_user,
            "other_remotes": [r.__dict__ for r in self.other_remotes],
            "portal_present": self.portal_present, "portal_backend": self.portal_backend,
            "integration_complete": self.integration_complete,
        }


def flatpak_installed(which=None) -> bool:
    import shutil
    which = which or shutil.which
    return bool(which("flatpak"))


def flatpak_version() -> str:
    ok, out, _ = run_command(["flatpak", "--version"])
    if not ok:
        return ""
    # "Flatpak 1.15.6" -> "1.15.6"
    parts = out.strip().split()
    return parts[-1] if parts else ""


def _parse_remotes(output: str, scope: str) -> list:
    """Parses `flatpak remotes --{scope} --columns=name,url,options`
    (tab-separated). Tolerates older flatpak output missing a URL
    column by leaving url empty rather than crashing."""
    remotes = []
    for line in output.splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        cols = line.split("\t")
        name = cols[0].strip() if len(cols) > 0 else ""
        url = cols[1].strip() if len(cols) > 1 else ""
        options = cols[2].strip() if len(cols) > 2 else ""
        if not name:
            continue
        enabled = "disabled" not in options.lower()
        remotes.append(FlatpakRemote(name=name, url=url, enabled=enabled, scope=scope))
    return remotes


def list_remotes(scope: str) -> list:
    r = run_command_full(["flatpak", "remotes", f"--{scope}", "--columns=name,url,options"])
    if not r.ok:
        return []
    return _parse_remotes(r.stdout, scope)


# Real portal backends are almost never on $PATH — they're launched by
# a systemd user service, not typed at a shell, so distros install
# them under libexec-style directories. shutil.which() alone used to
# report "portal missing" on a completely normal, fully-integrated
# Debian/Ubuntu/Fedora/Arch desktop simply because it was looking in
# the wrong place — confirmed on this exact machine (Pop!_OS/COSMIC):
# xdg-desktop-portal, -gtk and -cosmic are all installed, all under
# /usr/libexec, none reachable via which().
_PORTAL_SEARCH_DIRS = (
    "/usr/libexec", "/usr/lib/xdg-desktop-portal", "/usr/lib",
    "/usr/local/libexec", "/usr/local/lib",
)


def _portal_binary_present(name: str) -> bool:
    import os
    import shutil
    if shutil.which(name):
        return True
    return any(os.path.isfile(os.path.join(d, name)) for d in _PORTAL_SEARCH_DIRS)


def _detect_portal() -> "tuple[bool, str]":
    if _portal_binary_present("xdg-desktop-portal-kde"):
        return True, "kde"
    if _portal_binary_present("xdg-desktop-portal-cosmic"):
        return True, "cosmic"
    if _portal_binary_present("xdg-desktop-portal-gnome"):
        return True, "gnome"
    if _portal_binary_present("xdg-desktop-portal-gtk"):
        return True, "gtk"
    if _portal_binary_present("xdg-desktop-portal"):
        return True, ""
    return False, ""


def detect_flatpak_state() -> FlatpakState:
    installed = flatpak_installed()
    state = FlatpakState(installed=installed)
    if not installed:
        return state

    state.version = flatpak_version()
    state.system_remotes = list_remotes(SCOPE_SYSTEM)
    state.user_remotes = list_remotes(SCOPE_USER)
    state.flathub_system = any(r.is_flathub and r.enabled for r in state.system_remotes)
    state.flathub_user = any(r.is_flathub and r.enabled for r in state.user_remotes)
    state.other_remotes = [r for r in state.system_remotes + state.user_remotes if not r.is_flathub]

    portal_present, backend = _detect_portal()
    state.portal_present = portal_present
    state.portal_backend = backend
    state.integration_complete = portal_present and (state.flathub_system or state.flathub_user)
    return state


# ── Per-application install state (Flatseal and friends) ───────────
#
# A real, beginner-facing bug this fixes: "Installa Flatseal" used to
# unconditionally run `flatpak install --system ... Flatseal`. On a
# machine with only the *personal* Flathub configured (no system-wide
# Flathub at all — a completely normal, common setup) that command
# always failed, and always showed "Non è stato possibile installare
# Flatseal" even when Flatseal was already installed for the user.
#
# These states mirror exactly what the spec's "Fase 3" asks the UI to
# be able to show, so a page never has to re-derive them from raw
# flatpak output itself.
APP_NOT_INSTALLED = "not_installed"
APP_INSTALLED_USER = "installed_user"
APP_INSTALLED_SYSTEM = "installed_system"
APP_INSTALLED_BOTH = "installed_both"
APP_FLATPAK_UNAVAILABLE = "flatpak_unavailable"
APP_FLATHUB_USER_UNAVAILABLE = "flathub_user_unavailable"
APP_FLATHUB_SYSTEM_UNAVAILABLE = "flathub_system_unavailable"
APP_UNDETERMINED = "undetermined"


@dataclass
class FlatpakAppStatus:
    app_id: str
    flatpak_installed: bool
    determined: bool = True
    installed_user: bool = False
    installed_system: bool = False
    flathub_user_available: bool = False
    flathub_system_available: bool = False

    @property
    def state(self) -> str:
        if not self.flatpak_installed:
            return APP_FLATPAK_UNAVAILABLE
        if not self.determined:
            return APP_UNDETERMINED
        if self.installed_user and self.installed_system:
            return APP_INSTALLED_BOTH
        if self.installed_user:
            return APP_INSTALLED_USER
        if self.installed_system:
            return APP_INSTALLED_SYSTEM
        # Not installed anywhere — is there even a Flathub to install
        # it from? Reported per-scope so the UI can say exactly which
        # one is missing, rather than a single "not configured" blur.
        if not self.flathub_user_available and self.flathub_system_available:
            return APP_FLATHUB_USER_UNAVAILABLE
        if not self.flathub_system_available and self.flathub_user_available:
            return APP_FLATHUB_SYSTEM_UNAVAILABLE
        return APP_NOT_INSTALLED

    @property
    def installed(self) -> bool:
        return self.installed_user or self.installed_system

    @property
    def any_scope_available(self) -> bool:
        return self.flathub_user_available or self.flathub_system_available


def _flatpak_info_installed(app_id: str, scope: str, job=None) -> "bool | None":
    """True/False when flatpak gave a real, parseable answer; None when
    the check itself couldn't be trusted (flatpak errored in a way that
    isn't the ordinary 'not installed' case) — never guessed."""
    r = run_command_full(["flatpak", "info", f"--{scope}", app_id], timeout=15, job=job)
    if r.ok:
        return True
    if r.error:
        return None  # flatpak binary itself didn't run
    combined = f"{r.stdout}\n{r.stderr}".lower()
    if "not installed" in combined or "no installation" in combined or "not found" in combined:
        return False
    return None


def flatpak_app_status(app_id: str, job=None) -> FlatpakAppStatus:
    if not flatpak_installed():
        return FlatpakAppStatus(app_id, flatpak_installed=False)

    user_result = _flatpak_info_installed(app_id, SCOPE_USER, job=job)
    system_result = _flatpak_info_installed(app_id, SCOPE_SYSTEM, job=job)
    determined = user_result is not None and system_result is not None

    flathub_state = detect_flatpak_state()
    return FlatpakAppStatus(
        app_id=app_id, flatpak_installed=True, determined=determined,
        installed_user=bool(user_result), installed_system=bool(system_result),
        flathub_user_available=flathub_state.flathub_user,
        flathub_system_available=flathub_state.flathub_system,
    )


def open_flatpak_app(app_id: str) -> bool:
    """Launches an already-installed Flatpak app, detached from this
    process — never waited on, never killed when the Toolbox exits.
    Same pattern as core.virt_setup.open_virt_manager()."""
    try:
        subprocess.Popen(["flatpak", "run", app_id], start_new_session=True,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


# ── Shared error classification for flatpak write commands ─────────
#
# One place that turns a CommandResult from any `flatpak ...` write
# into the specific i18n key the spec's Fase 3 "ERRORI" list asks for,
# instead of every call site inventing its own generic failure text.
def _classify_flatpak_error(result, job=None) -> str:
    if job is not None and getattr(job, "cancelled", False):
        return "flatpak_err_operation_cancelled"
    if result.cancelled:
        return "flatpak_err_operation_cancelled"
    if result.timed_out:
        return "flatpak_err_no_connection"
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if not combined.strip() and result.returncode in (126, 127):
        # pkexec's own exit codes for "dismissed"/"not authorized",
        # with no flatpak output at all because it never even ran.
        return "flatpak_err_auth_cancelled"
    if "dismissed" in combined or "request dismissed" in combined:
        return "flatpak_err_auth_cancelled"
    if "not authorized" in combined or "permission denied" in combined or "authentication failed" in combined:
        return "flatpak_err_permission_denied"
    if ("unable to connect" in combined or "could not connect" in combined
            or "temporary failure in name resolution" in combined or "network is unreachable" in combined):
        return "flatpak_err_no_connection"
    if "no remote refs found" in combined or "not found" in combined or "no such ref" in combined:
        return "flatpak_err_package_not_found"
    if "not configured" in combined or "no remote " in combined:
        return "flatpak_err_flathub_not_configured"
    return "flatpak_err_install_failed"


# ── Guided setup ────────────────────────────────────────────────────

_FLATPAK_INSTALL_PKG = {
    "debian": "flatpak", "arch": "flatpak", "fedora": "flatpak",
    "opensuse": "flatpak", "default": "flatpak",
}


@dataclass
class SetupResult:
    ok: bool
    friendly_message: str = ""
    technical_detail: str = ""
    reboot_required: bool = False
    logout_recommended: bool = False


def install_flatpak(distro_profile, job=None) -> SetupResult:
    """distro_profile: a software_repo.distro_profile.DistroProfile (or
    anything with .family/.system_type). Never runs a raw package-manager
    command on an immutable/transactional system — those get an honest
    'not automated yet' result instead of a command that could half-apply."""
    if flatpak_installed():
        return SetupResult(True, friendly_message="flatpak_already_installed")

    system_type = getattr(distro_profile, "system_type", "traditional")
    family = getattr(distro_profile, "family", "unknown")

    if system_type in ("immutable", "transactional"):
        return SetupResult(False, friendly_message="flatpak_manual_procedure_required")

    if family == "unknown":
        return SetupResult(False, friendly_message="flatpak_family_unresolved")

    cmd = distro.install_cmd(_FLATPAK_INSTALL_PKG)
    if not cmd:
        return SetupResult(False, friendly_message="flatpak_family_unresolved")
    result = run_pkexec_full(cmd, timeout=INSTALL_TIMEOUT, job=job)
    ok = result.ok and flatpak_installed()
    return SetupResult(ok,
                        friendly_message="flatpak_install_success" if ok else "flatpak_install_failed",
                        technical_detail="" if ok else result.technical_detail())


def remove_flatpak(distro_profile, job=None) -> SetupResult:
    """Removes only the `flatpak` package itself, through the normal
    package manager — never touches installed Flatpak apps, their data
    or /var/lib/flatpak / ~/.local/share/flatpak. Those stay exactly
    where they are; only the flatpak command/service goes away. Same
    immutable/transactional and unresolved-family guards as
    install_flatpak(), for the same reason: never a command that could
    half-apply on a system this app can't safely act on."""
    if not flatpak_installed():
        return SetupResult(True, friendly_message="flatpak_not_installed_nothing_to_remove")

    system_type = getattr(distro_profile, "system_type", "traditional")
    family = getattr(distro_profile, "family", "unknown")

    if system_type in ("immutable", "transactional"):
        return SetupResult(False, friendly_message="flatpak_manual_procedure_required")
    if family == "unknown":
        return SetupResult(False, friendly_message="flatpak_family_unresolved")

    cmd = distro.remove_cmd(_FLATPAK_INSTALL_PKG)
    if not cmd:
        return SetupResult(False, friendly_message="flatpak_family_unresolved")
    result = run_pkexec_full(cmd, timeout=INSTALL_TIMEOUT, job=job)
    ok = result.ok and not flatpak_installed()
    return SetupResult(ok,
                        friendly_message="flatpak_remove_success" if ok else "flatpak_remove_failed",
                        technical_detail="" if ok else result.technical_detail())


def add_flathub_remote(scope: str, job=None) -> SetupResult:
    assert scope in (SCOPE_SYSTEM, SCOPE_USER)
    cmd = ["flatpak", "remote-add", "--if-not-exists", f"--{scope}",
           FLATHUB_REMOTE_NAME, FLATHUB_REPO_URL]
    if scope == SCOPE_SYSTEM:
        result = run_pkexec_full(cmd, timeout=INSTALL_TIMEOUT, job=job)
    else:
        result = run_command_full(cmd, timeout=INSTALL_TIMEOUT, job=job)

    remotes = list_remotes(scope)
    configured = any(r.is_flathub and r.enabled for r in remotes)
    return SetupResult(
        result.ok and configured,
        friendly_message="flathub_added_success" if (result.ok and configured) else "flathub_added_failed",
        technical_detail="" if (result.ok and configured) else result.technical_detail(),
        logout_recommended=result.ok and configured,
    )


def configure_flatpak_and_flathub(distro_profile, scope: str, job=None) -> SetupResult:
    """The single "Configura Flatpak e Flathub" action: installs the
    flatpak package if missing, then adds Flathub at the chosen scope.
    Never installs any application, never touches Snap/Discover/Software."""
    install_result = install_flatpak(distro_profile, job=job)
    if not install_result.ok and not flatpak_installed():
        return install_result
    remote_result = add_flathub_remote(scope, job=job)
    return SetupResult(
        remote_result.ok,
        friendly_message=remote_result.friendly_message,
        technical_detail=remote_result.technical_detail,
        logout_recommended=remote_result.logout_recommended,
    )


def install_flatpak_app(app_id: str, scope: str, job=None) -> SetupResult:
    """Generic scope-aware Flatpak app installer — Flatseal is the
    first caller, but nothing here is Flatseal-specific.

    Always re-checks real state first: an app already installed (in
    ANY scope) is reported as such and never re-installed or shown as
    a failure. `scope` must be explicit — this function never guesses
    between --user and --system on the caller's behalf."""
    assert scope in (SCOPE_SYSTEM, SCOPE_USER)
    if not flatpak_installed():
        return SetupResult(False, friendly_message="flatpak_err_not_installed_yet")

    status = flatpak_app_status(app_id, job=job)
    if status.installed:
        return SetupResult(True, friendly_message="app_already_installed")

    available = status.flathub_system_available if scope == SCOPE_SYSTEM else status.flathub_user_available
    if not available:
        return SetupResult(False, friendly_message="flatpak_err_flathub_not_configured")

    cmd = ["flatpak", "install", f"--{scope}", "--noninteractive", FLATHUB_REMOTE_NAME, app_id]
    result = run_pkexec_full(cmd, timeout=INSTALL_TIMEOUT, job=job) if scope == SCOPE_SYSTEM \
        else run_command_full(cmd, timeout=INSTALL_TIMEOUT, job=job)

    if not result.ok:
        return SetupResult(False, friendly_message=_classify_flatpak_error(result, job=job),
                            technical_detail=result.technical_detail())

    verify = flatpak_app_status(app_id, job=job)
    installed_in_scope = verify.installed_system if scope == SCOPE_SYSTEM else verify.installed_user
    if not installed_in_scope:
        return SetupResult(False, friendly_message="flatpak_err_verification_failed",
                            technical_detail=result.technical_detail())
    return SetupResult(True, friendly_message="app_install_success")


_FLATSEAL_MESSAGE_OVERRIDES = {
    "app_already_installed": "flatseal_already_installed",
    "app_install_success": "flatseal_install_success",
}


def install_flatseal(scope: str, job=None) -> SetupResult:
    """Thin Flatseal-named wrapper around the generic app installer, so
    the message says 'Flatseal' (as the spec's exact wording asks)
    while install_flatpak_app itself stays reusable for any future
    Flatpak app button."""
    result = install_flatpak_app(FLATSEAL_APP_ID, scope, job=job)
    overridden_key = _FLATSEAL_MESSAGE_OVERRIDES.get(result.friendly_message)
    if overridden_key:
        return SetupResult(result.ok, friendly_message=overridden_key,
                            technical_detail=result.technical_detail,
                            reboot_required=result.reboot_required,
                            logout_recommended=result.logout_recommended)
    return result


def check_flatpak_updates(job=None) -> "tuple[bool, list]":
    """Read-only: lists what WOULD be updated, never applies anything."""
    r = run_command_full(["flatpak", "remote-ls", "--updates", "--columns=application,version"], timeout=30, job=job)
    if not r.ok:
        return False, []
    items = [line.split("\t")[0] for line in r.stdout.splitlines() if line.strip()]
    return True, items


def apply_flatpak_updates(job=None) -> SetupResult:
    result = run_pkexec_full(["flatpak", "update", "--noninteractive"], timeout=INSTALL_TIMEOUT, job=job)
    return SetupResult(result.ok,
                        friendly_message="flatpak_update_success" if result.ok else "flatpak_update_failed",
                        technical_detail="" if result.ok else result.technical_detail())


def list_unused_runtimes(job=None) -> list:
    r = run_command_full(["flatpak", "list", "--unused", "--columns=application"], timeout=30, job=job)
    if not r.ok:
        return []
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def remove_unused_runtimes(job=None) -> SetupResult:
    result = run_pkexec_full(["flatpak", "uninstall", "--unused", "--noninteractive"],
                             timeout=INSTALL_TIMEOUT, job=job)
    return SetupResult(result.ok,
                        friendly_message="flatpak_unused_removed_success" if result.ok else "flatpak_unused_removed_failed",
                        technical_detail="" if result.ok else result.technical_detail())


def repair_flatpak(scope: str, job=None) -> SetupResult:
    assert scope in (SCOPE_SYSTEM, SCOPE_USER)
    cmd = ["flatpak", "repair", f"--{scope}"]
    if scope == SCOPE_SYSTEM:
        result = run_pkexec_full(cmd, timeout=INSTALL_TIMEOUT, job=job)
    else:
        result = run_command_full(cmd, timeout=INSTALL_TIMEOUT, job=job)
    return SetupResult(result.ok,
                        friendly_message="flatpak_repair_success" if result.ok else "flatpak_repair_failed",
                        technical_detail="" if result.ok else result.technical_detail())
