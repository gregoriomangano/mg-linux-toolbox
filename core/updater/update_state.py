"""
Update decision logic: which release (if any) counts as "the latest
applicable one" for this install's channel, and whether it's actually
newer than what's running. Also the 24h auto-check throttle.

Channel rules:
  - "beta"   sees beta and stable releases, never alpha.
  - "stable" sees only stable releases, ignores every prerelease.
"""
import json
import os
import time

from core.updater import semver
from core.updater.models import UpdateCheckResult

AUTO_CHECK_INTERVAL_SECONDS = 24 * 3600


def _state_home() -> str:
    return os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")


def update_state_path() -> str:
    return os.path.join(_state_home(), "mg-linux-toolbox", "update_check.json")


def find_latest_for_channel(releases: list, channel_pref: str):
    candidates = []
    for r in releases:
        if r.channel == "alpha":
            continue
        if channel_pref == "stable" and r.channel != "stable":
            continue
        sv = semver.parse(r.version)
        if sv is None:
            continue
        candidates.append((sv, r))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0].__str__())  # stable pre-sort, real order below
    best_sv, best_release = candidates[0]
    for sv, release in candidates[1:]:
        if semver.compare(sv, best_sv) > 0:
            best_sv, best_release = sv, release
    return best_release


def check_for_update(current_version: str, releases: list, channel_pref: str) -> UpdateCheckResult:
    latest = find_latest_for_channel(releases, channel_pref)
    if latest is None:
        # Not an error — the repository answered fine, it just has no
        # release for this channel yet (expected before the first
        # publication). Never shown as "you're up to date", which would
        # wrongly imply there's something to compare against.
        return UpdateCheckResult(update_available=False, latest=None, current_version=current_version,
                                  friendly_message="updater_no_releases_yet")
    current_sv = semver.parse(current_version)
    latest_sv = semver.parse(latest.version)
    newer = semver.is_newer(latest_sv, current_sv)
    return UpdateCheckResult(update_available=newer, latest=latest, current_version=current_version)


def read_last_check_at() -> "float | None":
    try:
        with open(update_state_path()) as f:
            data = json.load(f)
        return data.get("last_check_at")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def record_check_now(now: "float | None" = None):
    path = update_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump({"last_check_at": now if now is not None else time.time()}, f)
    os.replace(tmp, path)


def should_auto_check(now: "float | None" = None) -> bool:
    now = now if now is not None else time.time()
    last = read_last_check_at()
    if last is None:
        return True
    return (now - last) >= AUTO_CHECK_INTERVAL_SECONDS
