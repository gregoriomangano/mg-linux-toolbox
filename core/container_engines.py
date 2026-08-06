"""
Real readiness checks for Docker, Podman and Distrobox — never trust
"package installed" alone. Each check runs the actual command (daemon
status, `docker info` / `podman info`, socket access) so the reported
state reflects what will genuinely happen if the user clicks something.
No function here ever adds the user to the docker group automatically —
elevated-privilege implications are surfaced as text, decided by the
user elsewhere in the UI.
"""
import os
import shutil
from dataclasses import dataclass, field
from typing import Optional

from core.executor import run_command, run_command_full, run_pkexec_full, INSTALL_TIMEOUT, Job

# ─── Docker ────────────────────────────────────────────────────────────
DOCKER_STATE_NOT_INSTALLED = "not_installed"
DOCKER_STATE_NOT_STARTED = "not_started"
DOCKER_STATE_MISSING_PERMISSIONS = "missing_permissions"
DOCKER_STATE_READY = "ready"


def docker_status() -> dict:
    if not shutil.which("docker"):
        return {"state": DOCKER_STATE_NOT_INSTALLED, "daemon_active": False,
                "socket_accessible": False, "info_ok": False}

    ok, out, _ = run_command(["systemctl", "is-active", "docker"])
    daemon_active = ok and out.strip() == "active"

    socket_path = "/var/run/docker.sock"
    socket_accessible = os.path.exists(socket_path) and os.access(socket_path, os.R_OK | os.W_OK)

    info_ok, _, _ = run_command(["docker", "info"])

    if not daemon_active:
        state = DOCKER_STATE_NOT_STARTED
    elif not info_ok or not socket_accessible:
        state = DOCKER_STATE_MISSING_PERMISSIONS
    else:
        state = DOCKER_STATE_READY

    return {
        "state": state,
        "daemon_active": daemon_active,
        "socket_accessible": socket_accessible,
        "info_ok": info_ok,
    }


# ─── Podman ────────────────────────────────────────────────────────────
PODMAN_STATE_NOT_INSTALLED = "not_installed"
PODMAN_STATE_NOT_READY = "not_ready"
PODMAN_STATE_READY = "ready"


def _has_subid_entry(path: str, user: str) -> bool:
    try:
        with open(path) as f:
            return any(line.startswith(f"{user}:") for line in f)
    except OSError:
        return False


def podman_status() -> dict:
    if not shutil.which("podman"):
        return {"state": PODMAN_STATE_NOT_INSTALLED, "rootless": None,
                "subuid_configured": False, "subgid_configured": False,
                "has_newuidmap": False, "has_newgidmap": False, "info_ok": False}

    info_ok, out, _ = run_command(["podman", "info", "--format", "{{.Host.Security.Rootless}}"])
    rootless = out.strip() == "true" if info_ok else None

    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    subuid_ok = _has_subid_entry("/etc/subuid", user)
    subgid_ok = _has_subid_entry("/etc/subgid", user)
    has_newuidmap = bool(shutil.which("newuidmap"))
    has_newgidmap = bool(shutil.which("newgidmap"))

    if not info_ok:
        state = PODMAN_STATE_NOT_READY
    elif rootless and (not subuid_ok or not subgid_ok or not has_newuidmap or not has_newgidmap):
        state = PODMAN_STATE_NOT_READY
    else:
        state = PODMAN_STATE_READY

    return {
        "state": state,
        "rootless": rootless,
        "subuid_configured": subuid_ok,
        "subgid_configured": subgid_ok,
        "has_newuidmap": has_newuidmap,
        "has_newgidmap": has_newgidmap,
        "info_ok": info_ok,
    }


# ─── Distrobox ──────────────────────────────────────────────────────────
DISTROBOX_STATE_NOT_INSTALLED = "not_installed"
DISTROBOX_STATE_NO_BACKEND = "no_backend"
DISTROBOX_STATE_READY = "ready"


def distrobox_status() -> dict:
    if not shutil.which("distrobox"):
        return {"state": DISTROBOX_STATE_NOT_INSTALLED, "backend": None,
                "version": "", "rootless": None}

    ok, out, _ = run_command(["distrobox", "version"])
    version = out.strip() if ok else ""

    podman = podman_status()
    docker = docker_status()
    if podman["state"] == PODMAN_STATE_READY:
        backend, rootless = "podman", podman["rootless"]
    elif docker["state"] == DOCKER_STATE_READY:
        backend, rootless = "docker", False
    else:
        backend, rootless = None, None

    state = DISTROBOX_STATE_READY if backend else DISTROBOX_STATE_NO_BACKEND

    return {"state": state, "backend": backend, "version": version, "rootless": rootless}


# ─── Distrobox consent-gated test container ────────────────────────────
_TEST_CONTAINER_NAME = "mg-linux-toolbox-test"
_TEST_IMAGE = "alpine:latest"


def distrobox_test_plan() -> dict:
    """What the consent-gated test would do — shown to the user BEFORE
    they agree to anything, never run implicitly."""
    return {"image": _TEST_IMAGE, "container_name": _TEST_CONTAINER_NAME,
            "command": "echo ok"}


@dataclass
class DistroboxInstallResult:
    ok: bool
    steps: list = field(default_factory=list)  # [(label, CommandResult), ...]
    backend: Optional[str] = None
    friendly_message: str = ""

    def __bool__(self):
        return self.ok

    def technical_detail(self) -> str:
        """Real command, exit code, stdout and stderr for every step
        actually attempted — the "Dettagli errore" disclosure."""
        return "\n\n".join(f"[{label}]\n{result.technical_detail()}" for label, result in self.steps)


def distrobox_install_plan() -> dict:
    """What a real distrobox_install() call would do, computed without
    installing or guessing anything — shown to the user for confirmation
    BEFORE any privileged command runs. Prefers Podman when no backend
    package exists at all yet: it works rootless, unlike Docker it needs
    no system service the user hasn't agreed to enable, and it's already
    this app's preferred backend everywhere else (see distrobox_status()).

    Only proposes installing Podman when its package is genuinely
    missing — if it's present but not ready (e.g. subuid/subgid not
    configured, no newuidmap), reinstalling the package fixes nothing,
    so that case is left for distrobox_install()'s final verification to
    report honestly instead of running a pointless zypper/apt/dnf call.
    """
    podman_ready = podman_status()["state"] == PODMAN_STATE_READY
    docker_ready = docker_status()["state"] == DOCKER_STATE_READY
    backend_ready = podman_ready or docker_ready
    podman_missing = not shutil.which("podman")
    install_podman = (not backend_ready) and podman_missing
    packages = []
    if not shutil.which("distrobox"):
        packages.append("distrobox")
    if install_podman:
        packages.append("podman")
    return {
        "packages": packages,
        "needs_backend": install_podman,
        "backend_choice": "podman" if install_podman else None,
    }


def distrobox_install(job: Optional[Job] = None) -> DistroboxInstallResult:
    """
    Real flow, one captured command/exit-code/stdout/stderr per step —
    never a bare "did the package end up on disk" assumption:
      1) if no backend (Podman or Docker) is actually ready, install
         Podman (see distrobox_install_plan for why) — never silently
         fall back to Docker, which the user hasn't agreed to enable;
      2) install the distrobox package itself, if it's missing;
      3) verify `distrobox version` actually runs;
      4) verify the backend is now really ready (`podman info` /
         `docker info`), not just "the package is on disk".
    Step 4 failing means this returns ok=False: a distrobox binary with
    no working container backend is not a working install, regardless
    of what steps 1-3 reported.
    """
    from core.distro import distro

    steps = []
    plan = distrobox_install_plan()

    if plan["needs_backend"]:
        cmd = distro.install_cmd({"default": "podman"})
        if not cmd:
            return DistroboxInstallResult(False, steps, None, "distrobox_install_no_command")
        result = run_pkexec_full(cmd, timeout=INSTALL_TIMEOUT, job=job)
        steps.append(("install_podman", result))
        if not result.ok:
            return DistroboxInstallResult(False, steps, "podman", "distrobox_install_backend_failed")

    if "distrobox" in plan["packages"]:
        cmd = distro.install_cmd({"default": "distrobox"})
        if not cmd:
            return DistroboxInstallResult(False, steps, plan["backend_choice"], "distrobox_install_no_command")
        result = run_pkexec_full(cmd, timeout=INSTALL_TIMEOUT, job=job)
        steps.append(("install_distrobox", result))
        if not result.ok:
            return DistroboxInstallResult(False, steps, plan["backend_choice"], "distrobox_install_failed")

    version_result = run_command_full(["distrobox", "version"])
    steps.append(("distrobox_version", version_result))
    if not version_result.ok:
        return DistroboxInstallResult(False, steps, plan["backend_choice"], "distrobox_install_verify_failed")

    podman_ready = podman_status()["state"] == PODMAN_STATE_READY
    docker_ready = docker_status()["state"] == DOCKER_STATE_READY
    if not (podman_ready or docker_ready):
        return DistroboxInstallResult(False, steps, plan["backend_choice"], "distrobox_install_no_working_backend")

    backend = plan["backend_choice"] or ("podman" if podman_ready else "docker")
    return DistroboxInstallResult(True, steps, backend, "distrobox_install_done")


def run_distrobox_test() -> dict:
    """Only ever called after the user has explicitly consented to the
    plan from distrobox_test_plan(). Creates a small temporary container,
    runs one harmless command inside it, then deletes it — regardless of
    outcome, cleanup is attempted so nothing is left behind."""
    create_ok, create_out, create_err = run_command(
        ["distrobox", "create", "--yes", "--name", _TEST_CONTAINER_NAME, "--image", _TEST_IMAGE],
        timeout=180)
    if not create_ok:
        return {"ok": False, "step": "create", "detail": create_err or create_out}

    run_ok, run_out, run_err = run_command(
        ["distrobox", "enter", _TEST_CONTAINER_NAME, "--", "echo", "ok"], timeout=60)

    run_command(["distrobox", "rm", "--force", _TEST_CONTAINER_NAME], timeout=60)

    if not run_ok or "ok" not in run_out:
        return {"ok": False, "step": "run", "detail": run_err or run_out}
    return {"ok": True, "step": "done", "detail": ""}
