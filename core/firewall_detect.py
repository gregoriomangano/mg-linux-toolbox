"""
Firewall detection — fixes the Peppermint/GUFW report: `ufw status`
needs root, so calling it unprivileged used to fail silently and the
whole check fell through to "firewalld inactive", reporting UFW as
absent even when a user had installed GUFW, enabled UFW and rebooted.
GUFW is only a GUI front-end — it is never itself the firewall, so its
presence is not treated as a signal here.

Detection now combines multiple unprivileged signals instead of one
command that quietly needs a password:
- the `ufw` binary itself,
- the package being installed (distro-native check),
- /etc/ufw/ufw.conf's ENABLED= field (world-readable, no root needed),
- the systemd unit's runtime state, when systemd is available.

Never relies on systemctl alone (`_service_active` returning False on a
non-systemd init, or when the unit genuinely isn't registered yet, does
not by itself mean "not installed").
"""
import os
import shutil
from dataclasses import dataclass

from core.executor import run_command

STATE_UFW_ACTIVE = "ufw_active"
STATE_UFW_INACTIVE = "ufw_inactive"
STATE_UFW_INSTALLED_NOT_CONFIGURED = "ufw_installed_not_configured"
STATE_FIREWALLD_ACTIVE = "firewalld_active"
STATE_FIREWALLD_INACTIVE = "firewalld_inactive"
STATE_NFTABLES_RULES = "nftables_rules"
STATE_NONE_DETECTED = "none_detected"
STATE_UNDETERMINED = "undetermined"


@dataclass
class FirewallStatus:
    state: str
    ufw_installed: bool = False
    firewalld_installed: bool = False
    nft_installed: bool = False


def _read_ufw_conf_enabled(path: str) -> "bool | None":
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip().upper() == "ENABLED":
                    return val.strip().strip('"').lower() == "yes"
    except OSError:
        return None
    return None


def _service_active(name: str, which) -> "bool | None":
    """None (not True/False) when the check itself couldn't run at all
    (no systemd) — that's a missing signal, not evidence of 'inactive'."""
    if not which("systemctl"):
        return None
    ok, out, _ = run_command(["systemctl", "is-active", name])
    if not ok and not out:
        return None
    return out.strip() == "active"


def _has_nftables_rules(which) -> "bool | None":
    if not which("nft"):
        return None
    ok, out, _ = run_command(["nft", "list", "ruleset"])
    if not ok:
        return None  # typically permission denied when unprivileged — inconclusive, not "no rules"
    return bool(out.strip())


def detect_firewall(which=None, ufw_conf_path: str = "/etc/ufw/ufw.conf",
                     is_installed=None) -> FirewallStatus:
    which = which or shutil.which
    if is_installed is None:
        from core.distro import distro
        is_installed = distro.is_installed

    ufw_present = bool(which("ufw"))
    firewall_cmd_present = bool(which("firewall-cmd"))
    nft_present = bool(which("nft"))

    ufw_pkg_installed = ufw_present or is_installed({"debian": "ufw", "arch": "ufw", "default": "ufw"})
    firewalld_pkg_installed = firewall_cmd_present or is_installed(
        {"fedora": "firewalld", "opensuse": "firewalld", "default": "firewalld"})

    if ufw_present or ufw_pkg_installed:
        conf_enabled = _read_ufw_conf_enabled(ufw_conf_path)
        service_active = _service_active("ufw", which)
        if conf_enabled is True or service_active is True:
            return FirewallStatus(STATE_UFW_ACTIVE, ufw_installed=True,
                                   firewalld_installed=firewalld_pkg_installed, nft_installed=nft_present)
        if conf_enabled is False or service_active is False:
            return FirewallStatus(STATE_UFW_INACTIVE, ufw_installed=True,
                                   firewalld_installed=firewalld_pkg_installed, nft_installed=nft_present)
        return FirewallStatus(STATE_UFW_INSTALLED_NOT_CONFIGURED, ufw_installed=True,
                               firewalld_installed=firewalld_pkg_installed, nft_installed=nft_present)

    if firewall_cmd_present or firewalld_pkg_installed:
        active = _service_active("firewalld", which)
        if active is True:
            return FirewallStatus(STATE_FIREWALLD_ACTIVE, firewalld_installed=True)
        if active is False:
            return FirewallStatus(STATE_FIREWALLD_INACTIVE, firewalld_installed=True)
        return FirewallStatus(STATE_UNDETERMINED, firewalld_installed=True)

    nft_rules = _has_nftables_rules(which)
    if nft_rules is True:
        return FirewallStatus(STATE_NFTABLES_RULES, nft_installed=True)
    if nft_rules is None and nft_present:
        return FirewallStatus(STATE_UNDETERMINED, nft_installed=True)

    return FirewallStatus(STATE_NONE_DETECTED)
