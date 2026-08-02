"""
Real KVM/libvirt setup actions — "Configura KVM", "Installa Virt-Manager",
"Apri Virt-Manager", "Disattiva servizi", "Ripristina configurazione
Toolbox". Deliberately NOT modeled as a KernelFeature: this is a
multi-step install + service action, not a single sysfs/proc value, so
it follows the same pattern already used for Docker/Podman/Distrobox in
core/container_engines.py + backend/all.py (status functions here,
plain pkexec calls via core.executor, no priv_writer involved).

State needed to make "Ripristina configurazione Toolbox" real (not
fake) is recorded unprivileged, before any privileged action, at
~/.local/state/mg-linux-toolbox/virt_setup.json — mirroring
core/game_mode.py's pattern. Restoring only ever undoes what THIS
module itself changed (service enabled/started); it never uninstalls
packages (same rule as every other install action in this app) and
never unloads the kvm module (a VM could be using it).
"""
import os
import shutil
import subprocess

from core.distro import distro
from core.executor import run_command, run_pkexec
from core.persistence import history_store as hs
from core.persistence.atomic_io import read_json, write_json_atomic
from core.persistence.history_store import data_home

LIBVIRTD_SERVICE = "libvirtd"


def _log(feature_id: str, entry_type: str, ok: bool, **kwargs):
    try:
        hs.record_operation("virt", feature_id, entry_type, ok, **kwargs)
    except Exception:
        pass


def _state_home() -> str:
    return os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")


def state_path() -> str:
    return os.path.join(_state_home(), "mg-linux-toolbox", "virt_setup.json")


def _service_active(name: str) -> bool:
    ok, out, _ = run_command(["systemctl", "is-active", name])
    return ok and out.strip() == "active"


def _service_enabled(name: str) -> bool:
    ok, out, _ = run_command(["systemctl", "is-enabled", name])
    return ok and out.strip() == "enabled"


def qemu_installed() -> bool:
    return shutil.which("qemu-system-x86_64") is not None or shutil.which("qemu-kvm") is not None


def libvirt_installed() -> bool:
    return shutil.which("virsh") is not None


def virt_manager_installed() -> bool:
    return shutil.which("virt-manager") is not None


def _install_packages_cmd(packages: list) -> list:
    """Never mixes packages from different distro families — one
    branch per family, exactly like every other install in this app."""
    if distro.is_arch:
        return ["pacman", "-S", "--noconfirm"] + packages["arch"]
    if distro.is_fedora:
        return ["dnf", "install", "-y"] + packages["fedora"]
    if distro.is_opensuse:
        return ["zypper", "--non-interactive", "install"] + packages["opensuse"]
    return ["apt-get", "install", "-y"] + packages["debian"]


_KVM_PACKAGES = {
    "debian": ["qemu-system", "libvirt-daemon-system", "libvirt-clients", "bridge-utils"],
    "arch": ["qemu-full", "libvirt", "virt-install", "dnsmasq", "bridge-utils"],
    "fedora": ["qemu-kvm", "libvirt", "virt-install"],
    "opensuse": ["qemu-kvm", "libvirt"],
}

_VIRT_MANAGER_PACKAGE = {
    "debian": ["virt-manager"], "arch": ["virt-manager"],
    "fedora": ["virt-manager"], "opensuse": ["virt-manager"],
}


def configure_kvm(job=None) -> dict:
    """
    Idempotent: installs the distro's real qemu/libvirt packages if
    missing, loads kvm_amd/kvm_intel, enables+starts libvirtd — and
    records exactly what changed so restore_kvm_configuration() can
    undo only that, never more.
    """
    from core import virt_readiness as vr

    status = vr.check_kvm()
    if not status["cpu_supported"]:
        return {"ok": False, "reason": "cpu_unsupported"}

    state = {"packages_installed_by_toolbox": False,
             "service_was_active_before": _service_active(LIBVIRTD_SERVICE),
             "service_was_enabled_before": _service_enabled(LIBVIRTD_SERVICE)}

    if not (qemu_installed() and libvirt_installed()):
        run_pkexec(_install_packages_cmd(_KVM_PACKAGES), timeout=300, job=job)
        state["packages_installed_by_toolbox"] = qemu_installed() and libvirt_installed()

    from backend.all import kvm_load
    kvm_load()

    run_pkexec(["systemctl", "enable", "--now", LIBVIRTD_SERVICE], job=job)

    os.makedirs(os.path.dirname(state_path()), exist_ok=True)
    write_json_atomic(state_path(), state, mode=0o600)

    final = vr.check_kvm()
    result_ok = final["state"] in ("ready", "missing_permissions")
    _log("virt.kvm", hs.CONFIGURATION, result_ok, new_value=final["state"])
    return {"ok": result_ok, "status": final}


def restore_kvm_configuration() -> dict:
    """Undoes only what configure_kvm() itself changed about the
    libvirtd service — never uninstalls packages, never touches group
    membership, never unloads the kvm module."""
    state = read_json(state_path(), default={})
    if not state:
        return {"ok": False, "reason": "nothing_to_restore"}

    if not state.get("service_was_active_before") and _service_active(LIBVIRTD_SERVICE):
        run_pkexec(["systemctl", "stop", LIBVIRTD_SERVICE])
    if not state.get("service_was_enabled_before") and _service_enabled(LIBVIRTD_SERVICE):
        run_pkexec(["systemctl", "disable", LIBVIRTD_SERVICE])

    if os.path.exists(state_path()):
        os.remove(state_path())
    _log("virt.kvm", hs.RESTORE, True)
    return {"ok": True}


def deactivate_kvm_services() -> dict:
    """Explicit, unconditional "Disattiva servizi" — separate from
    restore: always stops+disables libvirtd, regardless of prior state."""
    run_pkexec(["systemctl", "disable", "--now", LIBVIRTD_SERVICE])
    result_ok = not _service_active(LIBVIRTD_SERVICE)
    _log("virt.kvm", hs.DEACTIVATION, result_ok)
    return {"ok": result_ok}


def install_virt_manager(job=None) -> bool:
    run_pkexec(_install_packages_cmd(_VIRT_MANAGER_PACKAGE), timeout=300, job=job)
    installed = virt_manager_installed()
    _log("virt.virt_manager", hs.INSTALLATION, installed)
    return installed


def open_virt_manager() -> bool:
    """Launches the real virt-manager GUI, detached from this process —
    never waited on, never killed when M.G Linux Toolbox exits."""
    if not virt_manager_installed():
        return False
    try:
        subprocess.Popen(["virt-manager"], start_new_session=True,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False
