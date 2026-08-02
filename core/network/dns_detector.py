"""
Detects what's REALLY managing DNS on this machine — not just which
distro it is. Order: NetworkManager, then systemd-resolved without NM,
then "unknown" (read-only, never touches /etc/resolv.conf directly).
"""
from core.executor import run_command
from core.network.dns_models import NetworkConnection, BackendKind

# Connection types that aren't "a real internet connection a user would
# want DNS changed on" — container/virt bridges, loopback, etc.
_EXCLUDED_TYPES = ("bridge", "loopback", "tun", "dummy", "veth")
_VPN_TYPES = ("vpn", "wireguard")


def _cmd_exists(cmd: str) -> bool:
    import shutil
    return shutil.which(cmd) is not None


def _service_active(name: str) -> bool:
    ok, out, _ = run_command(["systemctl", "is-active", name])
    return ok and out.strip() == "active"


def detect_backend() -> str:
    if _cmd_exists("nmcli") and _service_active("NetworkManager"):
        return BackendKind.NETWORKMANAGER
    if _cmd_exists("resolvectl") and _service_active("systemd-resolved"):
        return BackendKind.RESOLVED_ONLY
    return BackendKind.UNKNOWN


def _default_route_device() -> str:
    ok, out, _ = run_command(["ip", "route", "show", "default"])
    if not ok:
        return ""
    for line in out.splitlines():
        parts = line.split()
        if "dev" in parts:
            idx = parts.index("dev")
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return ""


def list_connections() -> list:
    """Real NetworkManager connections currently active, excluding
    container/virt bridges and loopback. VPN connections are included
    (flagged is_vpn) rather than hidden, so the UI can warn about them."""
    ok, out, _ = run_command(["nmcli", "-t", "-f", "NAME,TYPE,UUID,DEVICE", "connection", "show", "--active"])
    if not ok:
        return []
    default_device = _default_route_device()
    connections = []
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue
        name, conn_type, uuid, device = parts[0], parts[1], parts[2], parts[3]
        if conn_type in _EXCLUDED_TYPES:
            continue
        connections.append(NetworkConnection(
            uuid=uuid, name=name, conn_type=conn_type, device=device,
            is_vpn=conn_type in _VPN_TYPES,
            is_default_route=(device == default_device and bool(default_device)),
        ))
    return connections


def primary_connection() -> "NetworkConnection | None":
    """The connection that's actually providing internet access right
    now — the one carrying the default route — or None if it can't be
    determined. Never guesses among several candidates."""
    conns = list_connections()
    for c in conns:
        if c.is_default_route:
            return c
    # Fallback: exactly one non-VPN connection present -> unambiguous.
    non_vpn = [c for c in conns if not c.is_vpn]
    if len(non_vpn) == 1:
        return non_vpn[0]
    return None


def has_vpn_active() -> bool:
    return any(c.is_vpn for c in list_connections())
