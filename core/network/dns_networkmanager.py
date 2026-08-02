"""
NetworkManager-backed DNS read/apply/restore, keyed by connection UUID
(never by name alone — names aren't unique and can be re-used).
"""
from core.executor import run_command, run_pkexec
from core.network.dns_models import DnsSnapshot

# The generic run_pkexec default (10s) is too tight here: `nmcli
# connection modify` + `nmcli connection up` go through a real pkexec/
# polkit authentication round-trip, and empirically (measured on a real
# machine) can take anywhere from ~4s to just over 10s depending on
# polkit/agent responsiveness — the same class of problem INSTALL_TIMEOUT
# already fixed for package installs in backend/all.py.
DNS_PKEXEC_TIMEOUT = 30


def _get_field(uuid: str, field: str) -> str:
    ok, out, _ = run_command(["nmcli", "-g", field, "connection", "show", uuid])
    return out.strip() if ok else ""


def _servers_from_field(raw: str) -> list:
    """nmcli separates multiple DNS servers with a comma inside -g output
    (escaped as needed); this also tolerates a plain space-separated
    value (what we write back ourselves)."""
    if not raw:
        return []
    raw = raw.replace(r"\,", " ").replace(",", " ")
    return [s for s in raw.split() if s]


def read_snapshot(uuid: str) -> DnsSnapshot:
    """Reads exactly the fields we might change, so restore() can put
    them back precisely as found — nothing more, nothing SSID/MAC-like."""
    ipv4_dns = _servers_from_field(_get_field(uuid, "ipv4.dns"))
    ipv4_ignore = _get_field(uuid, "ipv4.ignore-auto-dns") == "yes"
    ipv6_dns = _servers_from_field(_get_field(uuid, "ipv6.dns"))
    ipv6_ignore = _get_field(uuid, "ipv6.ignore-auto-dns") == "yes"
    return DnsSnapshot(uuid=uuid, ipv4_dns=ipv4_dns, ipv4_ignore_auto_dns=ipv4_ignore,
                        ipv6_dns=ipv6_dns, ipv6_ignore_auto_dns=ipv6_ignore)


def ipv6_available(uuid: str) -> bool:
    method = _get_field(uuid, "ipv6.method")
    return method not in ("", "disabled", "link-local")


def _modify(uuid: str, ipv4_servers: list, ipv4_ignore_auto: bool,
            ipv6_servers: list, ipv6_ignore_auto: bool) -> bool:
    args = [
        "nmcli", "connection", "modify", uuid,
        "ipv4.dns", " ".join(ipv4_servers),
        "ipv4.ignore-auto-dns", "yes" if ipv4_ignore_auto else "no",
    ]
    if ipv6_servers or ipv6_ignore_auto or ipv6_available(uuid):
        args += [
            "ipv6.dns", " ".join(ipv6_servers),
            "ipv6.ignore-auto-dns", "yes" if ipv6_ignore_auto else "no",
        ]
    ok, _, _ = run_pkexec(args, timeout=DNS_PKEXEC_TIMEOUT)
    return ok


def _reactivate(uuid: str) -> bool:
    ok, _, _ = run_pkexec(["nmcli", "connection", "up", uuid], timeout=DNS_PKEXEC_TIMEOUT)
    return ok


def apply_dns(uuid: str, ipv4_servers: list, ipv6_servers: list) -> bool:
    """Sets explicit DNS servers and re-activates the connection so the
    change actually takes effect (this is the "may disconnect briefly"
    step the UI warns about beforehand)."""
    ignore_auto = bool(ipv4_servers) or bool(ipv6_servers)
    if not _modify(uuid, ipv4_servers, ignore_auto, ipv6_servers, ignore_auto):
        return False
    return _reactivate(uuid)


def apply_automatic(uuid: str) -> bool:
    """"Automatico" means: stop overriding DNS, go back to whatever the
    network (router/ISP/DHCP) provides."""
    if not _modify(uuid, [], False, [], False):
        return False
    return _reactivate(uuid)


def restore_snapshot(snapshot: DnsSnapshot) -> bool:
    if not _modify(snapshot.uuid, snapshot.ipv4_dns, snapshot.ipv4_ignore_auto_dns,
                    snapshot.ipv6_dns, snapshot.ipv6_ignore_auto_dns):
        return False
    return _reactivate(snapshot.uuid)
