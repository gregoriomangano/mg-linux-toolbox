"""
GitHub Releases as the update source. Read-only (GET), HTTPS only, no
token embedded — public repository releases don't need authentication,
and we never ask the user for one.
"""
import json
import urllib.error
import urllib.request

from core.updater import semver
from core.updater.models import ReleaseAsset, ReleaseInfo

API_BASE = "https://api.github.com"
USER_AGENT = "mg-linux-toolbox-updater"
DEFAULT_TIMEOUT = 10


class GithubError(Exception):
    def __init__(self, friendly_message: str, technical_detail: str = ""):
        super().__init__(technical_detail or friendly_message)
        self.friendly_message = friendly_message
        self.technical_detail = technical_detail


def _get_json(url: str, timeout: int = DEFAULT_TIMEOUT):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise GithubError("updater_repo_not_found", f"HTTP 404 for {url}")
        raise GithubError("updater_http_error", f"HTTP {e.code} for {url}")
    except urllib.error.URLError as e:
        raise GithubError("updater_no_network", str(e.reason))
    except TimeoutError:
        raise GithubError("updater_timeout", f"timed out fetching {url}")
    except json.JSONDecodeError as e:
        raise GithubError("updater_bad_response", str(e))


def _to_release_info(raw: dict) -> "ReleaseInfo | None":
    tag = raw.get("tag_name", "")
    version_str = tag[1:] if tag.startswith("v") else tag
    sv = semver.parse(version_str)
    if sv is None:
        return None
    assets = [
        ReleaseAsset(name=a.get("name", ""), download_url=a.get("browser_download_url", ""), size=a.get("size", 0))
        for a in raw.get("assets", [])
    ]
    return ReleaseInfo(
        tag=tag,
        version=version_str,
        prerelease=bool(raw.get("prerelease", False)),
        channel=semver.channel_of(sv),
        notes=raw.get("body", "") or "",
        assets=assets,
        published_at=raw.get("published_at", "") or "",
    )


def fetch_releases(owner: str, repo: str, timeout: int = DEFAULT_TIMEOUT) -> list:
    """Returns [ReleaseInfo, ...], skipping any tag that isn't valid
    SemVer (never guesses at a malformed version)."""
    url = f"{API_BASE}/repos/{owner}/{repo}/releases"
    raw_releases = _get_json(url, timeout=timeout)
    releases = []
    for raw in raw_releases:
        if raw.get("draft"):
            continue
        info = _to_release_info(raw)
        if info is not None:
            releases.append(info)
    return releases
