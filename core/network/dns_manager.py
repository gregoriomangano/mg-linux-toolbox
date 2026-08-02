"""
Top-level orchestration for "DNS con un clic" — the only module the GUI
talks to. The GUI passes provider_id + a validated connection UUID +
temporary/permanent; this module resolves what's actually allowed and
safe to do, snapshots the current configuration, applies, verifies with
a REAL DNS resolution (never just ping), and automatically rolls back on
failure. Nothing here ever writes /etc/resolv.conf directly, and nothing
is logged beyond provider id + connection type + outcome — no SSID, MAC,
or other connection-identifying details.
"""
import socket
import time

from core.network import dns_detector, dns_networkmanager as nm, dns_resolved as resolved
from core.network import dns_providers
from core.network.dns_models import BackendKind, DnsChangeResult
from core.network.dns_validator import validate_servers, split_by_family
from core.persistence import history_log

# A real query against a neutral, always-resolvable, IANA-reserved test
# domain — not tied to any of the DNS providers we offer, so the check
# never looks like it's favoring one of them.
_VERIFY_HOST = "example.com"
_VERIFY_PORT = 443
_VERIFY_TIMEOUT_SECONDS = 5


def _dns_resolution_works() -> bool:
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(_VERIFY_TIMEOUT_SECONDS)
    try:
        socket.getaddrinfo(_VERIFY_HOST, _VERIFY_PORT)
        return True
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(old_timeout)


def _resolve_servers(provider_id: str, custom_ipv4=None, custom_ipv6=None):
    if provider_id == dns_providers.CUSTOM:
        return list(custom_ipv4 or []), list(custom_ipv6 or [])
    provider = dns_providers.get(provider_id)
    if provider is None:
        return None, None
    return list(provider.ipv4), list(provider.ipv6)


def _log(provider_id: str, conn_type: str, outcome: str):
    history_log.append({
        "kind": "dns_change",
        "provider": provider_id,
        "connection_type": conn_type,
        "outcome": outcome,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })


def try_provider(uuid: str, provider_id: str, custom_ipv4=None, custom_ipv6=None) -> DnsChangeResult:
    """
    "Prova questo DNS": applies temporarily, verifies with a real query,
    and automatically restores on failure. Only ever acts on the
    connection UUID given (never "all connections").
    """
    backend = dns_detector.detect_backend()
    if backend != BackendKind.NETWORKMANAGER:
        return DnsChangeResult(False, friendly_message="dns_backend_not_writable")

    conns = dns_detector.list_connections()
    conn = next((c for c in conns if c.uuid == uuid), None)
    if conn is None:
        return DnsChangeResult(False, friendly_message="dns_connection_not_found")

    ipv4_servers, ipv6_servers = _resolve_servers(provider_id, custom_ipv4, custom_ipv6)
    if ipv4_servers is None:
        return DnsChangeResult(False, friendly_message="dns_unknown_provider")
    if provider_id == dns_providers.CUSTOM:
        ok4, _ = validate_servers(custom_ipv4 or [])
        ok6, _ = validate_servers(custom_ipv6 or [])
        if not (ok4 and ok6):
            return DnsChangeResult(False, friendly_message="dns_invalid_custom_address")

    snapshot = nm.read_snapshot(uuid)

    if provider_id == dns_providers.AUTOMATIC:
        applied = nm.apply_automatic(uuid)
    else:
        applied = nm.apply_dns(uuid, ipv4_servers, ipv6_servers)

    if not applied:
        _log(provider_id, conn.conn_type, "apply_failed")
        return DnsChangeResult(False, friendly_message="dns_apply_failed")

    if _dns_resolution_works():
        _log(provider_id, conn.conn_type, "verified")
        return DnsChangeResult(True, verified=True)

    # Verification failed — roll back automatically, then confirm the
    # rollback itself actually restored resolution.
    restored = nm.restore_snapshot(snapshot)
    restore_verified = restored and _dns_resolution_works()
    _log(provider_id, conn.conn_type, "verification_failed_rolled_back" if restore_verified else "rollback_failed")
    return DnsChangeResult(
        False, rolled_back=restored,
        friendly_message="dns_verification_failed_restored" if restore_verified else "dns_rollback_failed",
    )


def use_always(uuid: str, provider_id: str, custom_ipv4=None, custom_ipv6=None) -> DnsChangeResult:
    """"Usa sempre questo DNS": same safe flow as try_provider — the
    persistence comes from NetworkManager's own connection profile, which
    already survives reboots without any extra step here."""
    return try_provider(uuid, provider_id, custom_ipv4, custom_ipv6)


def restore_automatic(uuid: str) -> DnsChangeResult:
    return try_provider(uuid, dns_providers.AUTOMATIC)
