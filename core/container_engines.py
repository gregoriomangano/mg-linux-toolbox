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

from core.executor import run_command

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
