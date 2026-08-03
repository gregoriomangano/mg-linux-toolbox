"""
Single source of truth for the app's name/version/channel. Every other
part of the app (About page, updater, diagnostics, changelog, packaging
scripts) reads from here — never duplicate the version string elsewhere.
"""

APP_NAME = "M.G Linux Toolbox"
APP_VERSION = "0.9.0-beta.3"
UPDATE_CHANNEL = "beta"  # "stable" | "beta" — controls which releases the updater considers


def display_version() -> str:
    return f"{APP_NAME} {APP_VERSION}"
