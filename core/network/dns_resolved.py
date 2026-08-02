"""
systemd-resolved-only backend (no NetworkManager). Per-link, temporary
only — resolvectl has no concept of "make this survive a reboot" without
also owning the network configuration (which resolved alone doesn't),
and this NEVER writes /etc/resolv.conf directly.
"""
import re

from core.executor import run_command, run_pkexec

_LINK_RE = re.compile(r"^Link\s+(\d+)\s+\(([^)]+)\)")


def list_links() -> list:
    """Returns [(link_index, link_name), ...] from `resolvectl status`,
    skipping the loopback link."""
    ok, out, _ = run_command(["resolvectl", "status", "--no-pager"])
    if not ok:
        return []
    links = []
    for line in out.splitlines():
        m = _LINK_RE.match(line.strip())
        if m and m.group(2) != "lo":
            links.append((m.group(1), m.group(2)))
    return links


def apply_dns_temporary(link_name: str, servers: list) -> bool:
    if not servers:
        return False
    ok, _, _ = run_pkexec(["resolvectl", "dns", link_name] + servers)
    return ok


def revert_link(link_name: str) -> bool:
    ok, _, _ = run_pkexec(["resolvectl", "revert", link_name])
    return ok
