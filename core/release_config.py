"""
Release/distribution configuration — the ONE place the updater's target
repository is named. No token, password, or credential ever belongs
here: GitHub Releases on a public repository are read over a plain,
unauthenticated HTTPS GET (see core/updater/github_provider.py), so
none is needed. The GitHub repository itself has not been created yet
at the time this is set — the app must keep degrading gracefully (a
plain "not published yet" message, never a raw HTTP error) until it
exists and has its first release.
"""
import os

GITHUB_OWNER = "gregoriomangano"
GITHUB_REPOSITORY = "mg-linux-toolbox"
# Verified against README.md/README_EN.md/README_IT.md, which all list
# this exact channel — never a guessed handle.
YOUTUBE_URL = "https://www.youtube.com/@GregorioMangano"
WEBSITE_URL = "https://manganogregorio.it"
ISSUES_URL = ""
PROJECT_PAGE_URL = "https://www.manganogregorio.it/m-g-linux-toolbox/"
CONTACT_URL = "https://www.manganogregorio.it/#contatti"
# No "info@..." mailbox is verified anywhere in the project yet — left
# empty deliberately (same convention as ISSUES_URL above) rather than
# guessing a local-part on top of the one verified domain. The one
# place to fill in once it exists; the Help & Support page already
# hides the Email row/button whenever this is empty.
SUPPORT_EMAIL = ""

LICENSE_NAME = "GNU General Public License v3.0 or later"
LICENSE_SPDX = "GPL-3.0-or-later"


def github_configured() -> bool:
    return bool(GITHUB_OWNER and GITHUB_REPOSITORY)


def github_repo_full() -> str:
    """"owner/repo" for display — never used to build a URL that could
    embed a credential, just a plain identifier shown to the user."""
    return f"{GITHUB_OWNER}/{GITHUB_REPOSITORY}" if github_configured() else ""


def license_file_path() -> str:
    """Absolute path to the LICENSE file at the project root — resolved
    from this module's own location so it works both running from
    source and from inside the AppImage."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "LICENSE")
