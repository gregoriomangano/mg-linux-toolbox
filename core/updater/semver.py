"""
Minimal SemVer 2.0.0 parser/comparator — just enough to order releases
and classify prerelease channel (alpha/beta/stable). No third-party
dependency; stdlib `re` only.
"""
import re
from dataclasses import dataclass
from typing import Optional

_SEMVER_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


@dataclass
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: Optional[str] = None  # e.g. "beta.1", None for a plain release

    def __str__(self):
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{self.prerelease}" if self.prerelease else base


def parse(version_str: str) -> "Optional[SemVer]":
    if not version_str:
        return None
    m = _SEMVER_RE.match(version_str.strip())
    if not m:
        return None
    return SemVer(
        major=int(m.group("major")),
        minor=int(m.group("minor")),
        patch=int(m.group("patch")),
        prerelease=m.group("prerelease"),
    )


def channel_of(v: "Optional[SemVer]") -> str:
    if v is None:
        return "unknown"
    if v.prerelease is None:
        return "stable"
    pre = v.prerelease.lower()
    if pre.startswith("alpha"):
        return "alpha"
    if pre.startswith("beta"):
        return "beta"
    if pre.startswith("rc"):
        return "beta"  # release candidates are shown to beta-channel users
    return "unknown"


def _prerelease_identifiers(prerelease: Optional[str]) -> list:
    return prerelease.split(".") if prerelease else []


def _compare_identifier(a: str, b: str) -> int:
    a_num, b_num = a.isdigit(), b.isdigit()
    if a_num and b_num:
        ai, bi = int(a), int(b)
        return (ai > bi) - (ai < bi)
    if a_num != b_num:
        # Per SemVer spec: numeric identifiers always have lower
        # precedence than alphanumeric ones.
        return -1 if a_num else 1
    return (a > b) - (a < b)


def compare(a: "Optional[SemVer]", b: "Optional[SemVer]") -> int:
    """Returns -1/0/1. A missing/unparseable version always sorts lowest."""
    if a is None and b is None:
        return 0
    if a is None:
        return -1
    if b is None:
        return 1
    for x, y in ((a.major, b.major), (a.minor, b.minor), (a.patch, b.patch)):
        if x != y:
            return -1 if x < y else 1
    # Same major.minor.patch — a release with NO prerelease outranks one
    # that has one (1.0.0 > 1.0.0-beta.1).
    if a.prerelease is None and b.prerelease is None:
        return 0
    if a.prerelease is None:
        return 1
    if b.prerelease is None:
        return -1
    a_ids, b_ids = _prerelease_identifiers(a.prerelease), _prerelease_identifiers(b.prerelease)
    for ai, bi in zip(a_ids, b_ids):
        c = _compare_identifier(ai, bi)
        if c != 0:
            return c
    if len(a_ids) != len(b_ids):
        return -1 if len(a_ids) < len(b_ids) else 1
    return 0


def is_newer(candidate: "Optional[SemVer]", current: "Optional[SemVer]") -> bool:
    return compare(candidate, current) > 0
