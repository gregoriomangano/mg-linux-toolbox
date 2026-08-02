"""
Repository availability check — used before ever offering an Install
button, so we don't try (and fail with a confusing package-manager error)
when a package simply isn't in the repositories configured on this
system. Package availability genuinely varies between versions of the
"same" distro (e.g. an LTS release vs a rolling one, or a minimal repo
set vs one with extra components enabled), so this is a real runtime
check, not a static assumption.

Fails open (treats a package as "available, go ahead and try") only when
the check itself couldn't even run (package-manager command missing) —
that's our problem, not a reason to block a legitimate install. Once the
package manager actually runs and reports nothing, we trust it.
"""
from core.executor import run_command_full
from core.distro import distro


def _fails_open_if_tool_missing(result) -> "bool | None":
    """Returns True/False to short-circuit, or None to keep checking."""
    if result.error:
        return True  # the package-manager binary itself wasn't runnable
    return None


def is_available(packages: dict) -> bool:
    """
    packages: same shape as distro.install_cmd()'s argument, e.g.
    {"debian": "pkg", "arch": "pkg", "fedora": "pkg", "opensuse": "pkg"}.
    Returns True if the package is (as far as we can tell) installable
    from the repositories configured on this system right now.
    """
    if distro.is_arch:
        pkg = packages.get("arch", packages.get("default", ""))
        if not pkg:
            return True
        r = run_command_full(["pacman", "-Si", pkg])
        shortcut = _fails_open_if_tool_missing(r)
        if shortcut is not None:
            return shortcut
        return r.ok and bool(r.stdout.strip())

    if distro.is_opensuse:
        pkg = packages.get("opensuse", packages.get("default", ""))
        if not pkg:
            return True
        r = run_command_full(["zypper", "--non-interactive", "info", pkg])
        shortcut = _fails_open_if_tool_missing(r)
        if shortcut is not None:
            return shortcut
        return r.ok and "not found" not in r.stdout.lower()

    if distro.is_fedora:
        pkg = packages.get("fedora", packages.get("default", ""))
        if not pkg:
            return True
        r = run_command_full(["dnf", "info", pkg])
        shortcut = _fails_open_if_tool_missing(r)
        if shortcut is not None:
            return shortcut
        return r.ok and bool(r.stdout.strip())

    # Debian family (Debian, Ubuntu, Mint, Pop!_OS, ...)
    pkg = packages.get("debian", packages.get("default", ""))
    if not pkg:
        return True
    r = run_command_full(["apt-cache", "show", pkg])
    shortcut = _fails_open_if_tool_missing(r)
    if shortcut is not None:
        return shortcut
    return r.ok and bool(r.stdout.strip())
