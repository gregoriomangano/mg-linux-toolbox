"""
Safe external-link launcher — the only way any page in this app opens
something outside itself. Always hands off to the system's default
browser via Gio.AppInfo.launch_default_for_uri(); this app never
embeds a browser or any web content (no WebKitGTK anywhere in this
codebase), so an "external link" always means "leaves the app".

Only ever opens a well-formed https:// URL. Rejects http://, file://,
javascript:, mailto:, and anything malformed — a coding mistake that
turns a hardcoded https:// constant into something else should fail
closed, not silently open an unintended scheme.
"""
from urllib.parse import urlparse

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio


def is_safe_https_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def open_external_url(url: str) -> bool:
    """Returns True only if a well-formed https:// URL was actually
    handed off to the default browser."""
    if not is_safe_https_url(url):
        return False
    try:
        return bool(Gio.AppInfo.launch_default_for_uri(url, None))
    except Exception:
        return False
