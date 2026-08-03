"""
Single source of truth for everything the unprivileged client and the
privileged helper must agree on: where the installed helper lives, its
version, and the protocol version of the (feature_id, action, value,
device_id, force) contract. No GTK, no subprocess — importable from
both sides and from the build script.
"""

# Version of the privileged helper component itself — bumped whenever
# the helper's behaviour or feature set changes. Independent from the
# app version so an unchanged helper doesn't force a reinstall.
HELPER_VERSION = "1.0.0"

# Protocol between GUI client and helper. The client refuses to talk to
# a helper whose major protocol differs from its own.
PROTOCOL_VERSION = 1

# Stable filesystem locations for the installed helper, in preference
# order. /usr/libexec is the FHS home for internal executables; some
# distributions without it use /usr/lib instead. NEVER anywhere a
# normal user can write (no $HOME, no /tmp, no AppImage mount).
HELPER_INSTALL_DIRS = (
    "/usr/libexec/mg-linux-toolbox",
    "/usr/lib/mg-linux-toolbox",
)
HELPER_FILENAME = "mg-privileged-helper"
HELPER_INSTALL_PATHS = tuple(
    f"{d}/{HELPER_FILENAME}" for d in HELPER_INSTALL_DIRS
)

# Polkit action that authorizes running the installed helper.
POLKIT_ACTION_ID = "it.manganogregorio.mg-linux-toolbox.modify-system"
POLKIT_POLICY_FILENAME = "it.manganogregorio.mg-linux-toolbox.policy"

# The marker line the client greps for inside the installed helper file
# to learn its version without executing anything.
VERSION_MARKER = "MG_HELPER_VERSION"
PROTOCOL_MARKER = "MG_HELPER_PROTOCOL"


def parse_marker(text: str, marker: str) -> "str | None":
    """Finds `MARKER = "value"` in the helper's source text."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(marker):
            _, _, value = stripped.partition("=")
            value = value.strip().strip('"').strip("'")
            if value:
                return value
    return None
