"""Plain data shapes for the updater — no GTK, no network calls here."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReleaseAsset:
    name: str
    download_url: str
    size: int = 0


@dataclass
class ReleaseInfo:
    tag: str
    version: str            # SemVer string, tag with any leading "v" stripped
    prerelease: bool
    channel: str            # "stable" | "beta" | "alpha" | "unknown"
    notes: str = ""
    assets: list = field(default_factory=list)
    published_at: str = ""


@dataclass
class UpdateCheckResult:
    update_available: bool
    latest: "Optional[ReleaseInfo]" = None
    current_version: str = ""
    friendly_message: str = ""
    technical_detail: str = ""


@dataclass
class DownloadResult:
    ok: bool
    path: str = ""
    size: int = 0
    friendly_message: str = ""
    technical_detail: str = ""


@dataclass
class InstallResult:
    ok: bool
    friendly_message: str = ""
    technical_detail: str = ""
