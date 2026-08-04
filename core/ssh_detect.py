"""
Single source of truth for "is an OpenSSH server actually installed on
this machine" — fixes the Beta 4 report: Rete e sicurezza and Servizi
already detected 'not installed' correctly, but Sicurezza's "Accesso
root tramite SSH" toggle stayed clickable regardless, because
root_ssh_disabled() silently returned False (== "not disabled", i.e.
indistinguishable from "installed and allowed") when
/etc/ssh/sshd_config simply didn't exist.

Every page that needs to know "is there an SSH server here" now calls
openssh_server_installed() instead of rolling its own dep_check, so
the three pages can no longer disagree.
"""
import os
import shutil
from dataclasses import dataclass

from core.executor import run_command

_SSHD_PATHS = ("/usr/sbin/sshd", "/usr/local/sbin/sshd", "/sbin/sshd")

STATE_NOT_INSTALLED = "not_installed"
STATE_DISABLED = "disabled"          # PermitRootLogin no
STATE_ALLOWED = "allowed"            # PermitRootLogin yes / prohibit-password / etc. (default = allowed)
STATE_UNDETERMINED = "undetermined"  # installed, but config unreadable


def _sshd_binary_present(which=None) -> bool:
    which = which or shutil.which
    if which("sshd"):
        return True
    return any(os.path.exists(p) for p in _SSHD_PATHS)


def _service_unit_known(name: str) -> bool:
    ok, out, _ = run_command(["systemctl", "list-unit-files", f"{name}.service"])
    return ok and name in out


def openssh_server_installed() -> bool:
    """True if the OpenSSH *server* (not just the ssh client) is really
    on this system — package DB, binary presence and the systemd unit
    file are all checked so no single missing signal (e.g. sshd not on
    an unprivileged user's PATH) causes a false negative."""
    from core.distro import distro
    if distro.is_installed({"debian": "openssh-server", "arch": "openssh",
                             "fedora": "openssh-server", "opensuse": "openssh"}):
        return True
    if _sshd_binary_present():
        return True
    return _service_unit_known("ssh") or _service_unit_known("sshd")


def root_ssh_state(sshd_config_path: str = "/etc/ssh/sshd_config") -> str:
    """Never reads the config file unless the server is actually
    installed — an absent file on a machine without SSH must not be
    mistaken for 'root login allowed'."""
    if not openssh_server_installed():
        return STATE_NOT_INSTALLED
    try:
        with open(sshd_config_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                if line.split()[0].lower() == "permitrootlogin":
                    parts = line.split(None, 1)
                    value = parts[1].strip().lower() if len(parts) > 1 else ""
                    return STATE_DISABLED if value == "no" else STATE_ALLOWED
    except OSError:
        return STATE_UNDETERMINED
    return STATE_ALLOWED  # directive absent => OpenSSH's own default


@dataclass
class RootSshInfo:
    state: str

    @property
    def installed(self) -> bool:
        return self.state != STATE_NOT_INSTALLED

    @property
    def actionable(self) -> bool:
        """Whether the toggle may be shown enabled at all."""
        return self.state in (STATE_DISABLED, STATE_ALLOWED)
