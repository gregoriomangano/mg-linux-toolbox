"""
Validation for user-supplied ("Personalizzato") DNS server addresses.
Uses only Python's standard `ipaddress` module — no string ever reaches
a shell, and no arbitrary text is accepted as-is.
"""
import ipaddress

MAX_SERVERS_PER_FAMILY = 3


def validate_servers(raw_list: list) -> "tuple[bool, list]":
    """
    raw_list: strings the user typed (already split on whitespace/comma
    by the caller). Returns (all_valid, cleaned_addresses) — cleaned
    contains the canonical str() of each parsed address, in order,
    de-duplicated. An empty raw_list is valid (means "no custom servers
    of this family").
    """
    if len(raw_list) > MAX_SERVERS_PER_FAMILY:
        return False, []
    cleaned = []
    seen = set()
    for raw in raw_list:
        text = raw.strip()
        if not text:
            continue
        try:
            addr = ipaddress.ip_address(text)
        except ValueError:
            return False, []
        s = str(addr)
        if s not in seen:
            seen.add(s)
            cleaned.append(s)
    return True, cleaned


def split_by_family(raw_list: list) -> "tuple[list, list]":
    """Splits a mixed list of address strings into (ipv4, ipv6), silently
    dropping anything that doesn't parse — callers should validate first
    with validate_servers() to know whether to reject the whole input."""
    v4, v6 = [], []
    for raw in raw_list:
        text = raw.strip()
        if not text:
            continue
        try:
            addr = ipaddress.ip_address(text)
        except ValueError:
            continue
        (v4 if addr.version == 4 else v6).append(str(addr))
    return v4, v6
