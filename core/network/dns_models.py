"""
Data shapes shared by the DNS subsystem. Kept dependency-free (no GTK,
no subprocess) so they're trivial to construct in tests.
"""
from dataclasses import dataclass, field
from typing import Optional


class BackendKind:
    NETWORKMANAGER = "networkmanager"
    RESOLVED_ONLY = "resolved"
    UNKNOWN = "unknown"


@dataclass
class DnsProvider:
    id: str
    name_key: str
    desc_key: str
    ipv4: list = field(default_factory=list)
    ipv6: list = field(default_factory=list)
    pro_key: str = ""
    con_key: str = ""


@dataclass
class NetworkConnection:
    """One NetworkManager connection profile."""
    uuid: str
    name: str
    conn_type: str      # e.g. "802-3-ethernet", "802-11-wireless", "vpn", "wireguard", "bridge"
    device: str
    is_vpn: bool = False
    is_default_route: bool = False


@dataclass
class DnsSnapshot:
    """Enough state to restore a connection's DNS exactly as it was —
    never includes SSID/MAC/personal data, just the DNS-relevant knobs."""
    uuid: str
    ipv4_dns: list = field(default_factory=list)
    ipv4_ignore_auto_dns: bool = False
    ipv6_dns: list = field(default_factory=list)
    ipv6_ignore_auto_dns: bool = False


@dataclass
class DnsChangeResult:
    ok: bool
    verified: bool = False
    rolled_back: bool = False
    friendly_message: str = ""
    technical_detail: str = ""
