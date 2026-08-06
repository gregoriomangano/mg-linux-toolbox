"""
Read-only software-repository detection for "Software e repository".

Everything in this module only ever reads files already on disk (or
runs a plain read command like `flatpak remotes`) — nothing here writes
or modifies a repository. See repo_recipes.py for the guarded
activation flow. Every URI shown to the user goes through
redact_credentials() first, so a Personal-Access-Token PPA URL or a
`user:pass@` mirror never ends up on screen or in the History.
"""
import configparser
import glob
import io
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

KIND_OFFICIAL = "official"
KIND_UNIVERSAL = "universal"
KIND_COMMUNITY = "community"
KIND_EXTERNAL = "external"
KIND_UNKNOWN = "unknown"
KIND_NEEDS_REVIEW = "needs_review"

_CRED_QUERY_KEYS = re.compile(r"(token|apikey|api_key|key|password|passwd|secret|auth)", re.IGNORECASE)


def redact_credentials(uri: str) -> str:
    """Strips any userinfo (user:pass@host) and masks sensitive query
    parameters, without touching the rest of the URL. Never raises —
    an unparsable string is returned with a best-effort regex mask
    instead of being shown raw."""
    if not uri:
        return uri
    try:
        parts = urlsplit(uri)
        netloc = parts.netloc
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[1]
            netloc = "***@" + netloc
        query = parts.query
        if query:
            new_pairs = []
            for pair in query.split("&"):
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    if _CRED_QUERY_KEYS.search(k):
                        v = "***"
                    new_pairs.append(f"{k}={v}")
                else:
                    new_pairs.append(pair)
            query = "&".join(new_pairs)
        return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    except Exception:
        return re.sub(r"//[^/@]+@", "//***@", uri)


@dataclass
class RepoEntry:
    name: str
    family: str            # "debian" | "fedora" | "arch" | "opensuse" | "flatpak"
    kind: str
    enabled: bool
    source_file: str
    uri: str                # already redacted
    alias: str = ""        # Zypper section ID ("repo-oss") or raw Flatpak remote name
    scope: str = ""        # Flatpak scope only: "system" | "user" (empty otherwise)
    signed: "bool | None" = None
    suites: list = field(default_factory=list)
    components: str = ""
    warnings: list = field(default_factory=list)
    duplicate_files: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


# Warning codes stored on RepoEntry are plain identifiers, never shown
# directly — the UI maps each one through this table to a translated
# sentence. Anything not listed here still renders as a real, if
# generic, translated sentence (never the bare code) via the
# "warning_unknown" fallback in the UI layer.
WARNING_NO_HOST = "no_host"
WARNING_NO_URI = "no_uri"
WARNING_GPGCHECK_DISABLED = "gpgcheck_disabled"
WARNING_SIGNATURE_UNSPECIFIED = "signature_unspecified"
WARNING_AUR_NOT_A_REPO = "aur_is_not_a_binary_repo"
WARNING_DUPLICATE_CONFIG = "duplicate_configuration"


# ── Human-readable naming (2026-08-05) ───────────────────────────────
#
# Never show a bare suite/channel name ("noble", "stable") or a raw
# URI as the repository's headline name — those are release-channel
# details, not identity. Real repro case that motivated this: Pop!_OS
# 24.04's own /etc/apt/sources.list.d/*.list files (docker.com,
# github.com CLI, ...) have no X-Repolib-Name, so the old code fell
# back to the suite token and showed "noble" / "stable" as the name.
def _readable_name_from_filename(source_file: str) -> str:
    base = os.path.basename(source_file)
    base = re.sub(r"\.(list|sources|repo)$", "", base)
    words = [w for w in re.split(r"[-_]+", base) if w]
    if not words:
        return base or source_file
    return " ".join(w.capitalize() for w in words)


def _normalize_repolib_name(name: str) -> str:
    """Pop!_OS's own X-Repolib-Name field can't contain "!" in that
    context, so it ships as "Pop_OS ..." — this is the distro's own
    real branding (used throughout this project already), not a
    guess, so it's safe to restore the "!" for display."""
    if name.startswith("Pop_OS"):
        return "Pop!_OS" + name[len("Pop_OS"):]
    return name


# ── APT (Debian/Ubuntu family) ──────────────────────────────────────

_OFFICIAL_APT_HOSTS = (
    "archive.ubuntu.com", "security.ubuntu.com", "ports.ubuntu.com",
    "deb.debian.org", "security.debian.org", "ftp.debian.org",
    "apt.pop-os.org", "packages.linuxmint.com", "extra.linuxmint.com",
)


def _classify_apt(uri: str) -> "tuple[str, list]":
    """"Universale" is reserved for genuinely cross-platform sources
    (Flathub) — an APT component like universe/multiverse or a
    backports suite is still first-party Ubuntu/Debian/Pop!_OS
    content from the SAME official host, so it stays "Ufficiale"
    rather than being relabelled "Universale" as a generic catch-all."""
    warnings = []
    host = urlsplit(uri).netloc.split("@")[-1]
    if any(h in host for h in _OFFICIAL_APT_HOSTS):
        return KIND_OFFICIAL, warnings
    if "ppa.launchpad.net" in host or host.endswith(".launchpadcontent.net"):
        return KIND_EXTERNAL, warnings
    if not host:
        warnings.append(WARNING_NO_HOST)
        return KIND_NEEDS_REVIEW, warnings
    return KIND_EXTERNAL, warnings


def _parse_apt_oneline(line: str, source_file: str) -> "RepoEntry | None":
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if not (line.startswith("deb ") or line.startswith("deb-src ")):
        return None
    tokens = line.split()
    tokens = tokens[1:]  # drop "deb"/"deb-src"
    options = ""
    if tokens and tokens[0].startswith("["):
        # collect the bracketed options blob (may be one or more tokens)
        opt_tokens = []
        while tokens and not tokens[0].endswith("]"):
            opt_tokens.append(tokens.pop(0))
        if tokens:
            opt_tokens.append(tokens.pop(0))
        options = " ".join(opt_tokens).strip("[]")
    if not tokens:
        return None
    uri = tokens[0]
    suite = tokens[1] if len(tokens) > 1 else ""
    components = " ".join(tokens[2:])
    signed = None
    if "signed-by" in options.lower():
        signed = True
    kind, warnings = _classify_apt(uri)
    return RepoEntry(
        name=_readable_name_from_filename(source_file), family="debian", kind=kind, enabled=True,
        source_file=source_file, uri=redact_credentials(uri),
        signed=signed, suites=[suite] if suite else [], components=components, warnings=warnings,
    )


def _parse_apt_deb822(text: str, source_file: str) -> list:
    """One stanza -> one RepoEntry per URI, with every listed suite
    kept together as a single logical repository (Suites: can list
    several release channels for the exact same repo — e.g. Pop!_OS's
    own system.sources lists 'noble noble-security noble-updates
    noble-backports' in ONE stanza; splitting that into four separate
    rows, each still just named after the shared X-Repolib-Name, is
    exactly the "più righe Pop_OS System Sources" duplication this
    fixes)."""
    entries = []
    for block in re.split(r"\n\s*\n", text):
        fields = {}
        cur_key = None
        for raw_line in block.splitlines():
            if not raw_line.strip() or raw_line.strip().startswith("#"):
                continue
            if raw_line[:1].isspace() and cur_key:
                fields[cur_key] += " " + raw_line.strip()
                continue
            if ":" not in raw_line:
                continue
            key, _, val = raw_line.partition(":")
            cur_key = key.strip()
            fields[cur_key] = val.strip()
        if not fields:
            continue
        uris = fields.get("URIs", fields.get("URI", ""))
        suites = fields.get("Suites", "").split()
        components = fields.get("Components", "")
        enabled = fields.get("Enabled", "yes").strip().lower() != "no"
        signed_by = fields.get("Signed-By", "")
        signed = bool(signed_by) if signed_by else None
        repolib_name = fields.get("X-Repolib-Name")
        for uri in uris.split():
            kind, warnings = _classify_apt(uri)
            if signed is None:
                warnings = warnings + [WARNING_SIGNATURE_UNSPECIFIED]
            name = _normalize_repolib_name(repolib_name) if repolib_name \
                else _readable_name_from_filename(source_file)
            entries.append(RepoEntry(
                name=name, family="debian", kind=kind, enabled=enabled,
                source_file=source_file, uri=redact_credentials(uri),
                signed=signed, suites=suites, components=components,
                warnings=warnings,
            ))
    return entries


def _flag_duplicate_configurations(entries: list) -> None:
    """A real, problematic duplicate: two DIFFERENT files defining the
    exact same (uri, suites, components) — not merely two release
    channels of the same repo, which are already kept together as one
    entry by the parsers above. Mutates entries in place."""
    seen = {}
    for e in entries:
        dup_key = (e.uri, tuple(sorted(e.suites)), e.components)
        if dup_key in seen:
            other = seen[dup_key]
            if e.source_file not in other.duplicate_files:
                other.warnings.append(WARNING_DUPLICATE_CONFIG)
                other.duplicate_files.append(e.source_file)
            if other.source_file not in e.duplicate_files:
                e.warnings.append(WARNING_DUPLICATE_CONFIG)
                e.duplicate_files.append(other.source_file)
        else:
            seen[dup_key] = e


def scan_apt(sources_list: str = "/etc/apt/sources.list",
             sources_list_d: str = "/etc/apt/sources.list.d") -> list:
    entries = []
    files = []
    if os.path.isfile(sources_list):
        files.append(sources_list)
    if os.path.isdir(sources_list_d):
        files += sorted(glob.glob(os.path.join(sources_list_d, "*.list")))
        files += sorted(glob.glob(os.path.join(sources_list_d, "*.sources")))

    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if path.endswith(".sources"):
            entries += _parse_apt_deb822(text, path)
        else:
            for line in text.splitlines():
                entry = _parse_apt_oneline(line, path)
                if entry:
                    entries.append(entry)

    _flag_duplicate_configurations(entries)
    return entries


# ── DNF (Fedora family) ─────────────────────────────────────────────

_OFFICIAL_FEDORA_BASE_IDS = {"fedora", "updates", "updates-testing", "fedora-cisco-openh264"}
# Suffixes Fedora's own repo files append to an otherwise-official base
# id (debuginfo/source variants of the same repo) — stripped before
# matching against _OFFICIAL_FEDORA_BASE_IDS, so e.g.
# "updates-testing-debuginfo" is recognized as official exactly like
# "updates-testing" already was.
_FEDORA_ID_SUFFIXES = ("-debuginfo", "-debug", "-source")


def _fedora_base_id(section_id: str) -> str:
    lowered = section_id.lower()
    for suffix in _FEDORA_ID_SUFFIXES:
        if lowered.endswith(suffix):
            return lowered[: -len(suffix)]
    return lowered


def scan_dnf(repos_d: str = "/etc/yum.repos.d") -> list:
    entries = []
    if not os.path.isdir(repos_d):
        return entries
    for path in sorted(glob.glob(os.path.join(repos_d, "*.repo"))):
        parser = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                parser.read_file(f)
        except (OSError, configparser.Error):
            continue
        for section in parser.sections():
            fields = dict(parser.items(section))
            base = fields.get("baseurl", "")
            metalink = fields.get("metalink", "")
            mirrorlist = fields.get("mirrorlist", "")
            uri = base or metalink or mirrorlist
            enabled = fields.get("enabled", "1").strip() not in ("0", "false", "no")
            gpgcheck = fields.get("gpgcheck", "1").strip() not in ("0", "false", "no")
            warnings = []
            lowered_id = section.lower()
            # Copr ("copr:...") is Fedora's own third-party/community
            # repo system — the exact equivalent of an Ubuntu PPA, not
            # an official source, even though its own id/URL routinely
            # contains the substring "fedora" (2026-08-05: found on a
            # real Fedora 44 VM — "[copr:copr.fedorainfracloud.org:
            # phracek:PyCharm]" was being classified "Ufficiale" purely
            # because "fedora" appears inside "fedorainfracloud").
            if lowered_id.startswith("copr:") or lowered_id.startswith("_copr:"):
                kind = KIND_EXTERNAL
            elif _fedora_base_id(section) in _OFFICIAL_FEDORA_BASE_IDS:
                kind = KIND_OFFICIAL
            elif "rpmfusion" in lowered_id:
                kind = KIND_COMMUNITY
            elif not uri:
                kind = KIND_NEEDS_REVIEW
                warnings.append(WARNING_NO_URI)
            else:
                kind = KIND_EXTERNAL
            if not gpgcheck:
                warnings.append(WARNING_GPGCHECK_DISABLED)
            entries.append(RepoEntry(
                name=fields.get("name", section), family="fedora", kind=kind,
                enabled=enabled, source_file=path, uri=redact_credentials(uri),
                signed=gpgcheck, warnings=warnings,
            ))
    return entries


# ── Pacman (Arch family) ────────────────────────────────────────────

_OFFICIAL_PACMAN_REPOS = {"core", "extra", "multilib", "core-testing",
                            "extra-testing", "multilib-testing"}


def _parse_pacman_conf(path: str, seen_files: set) -> list:
    if path in seen_files or not os.path.isfile(path):
        return []
    seen_files.add(path)
    entries = []
    current_section = None
    current_servers = []
    current_siglevel = ""

    def flush():
        if current_section and current_section != "options":
            kind = KIND_OFFICIAL if current_section.lower() in _OFFICIAL_PACMAN_REPOS else KIND_EXTERNAL
            warnings = []
            if current_section.lower() == "aur":
                warnings.append(WARNING_AUR_NOT_A_REPO)
                kind = KIND_NEEDS_REVIEW
            entries.append(RepoEntry(
                name=current_section, family="arch", kind=kind, enabled=True,
                source_file=path, uri=redact_credentials("; ".join(current_servers)),
                signed=("Never" not in current_siglevel) if current_siglevel else None,
                warnings=warnings,
            ))

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                if line.startswith("[") and line.endswith("]"):
                    flush()
                    current_section = line[1:-1]
                    current_servers = []
                    current_siglevel = ""
                    continue
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if key == "Server":
                    current_servers.append(val)
                elif key == "SigLevel":
                    current_siglevel = val
                elif key == "Include":
                    for inc_path in sorted(glob.glob(val)):
                        entries += _parse_pacman_conf(inc_path, seen_files)
        flush()
    except OSError:
        return entries
    return entries


def scan_pacman(conf_path: str = "/etc/pacman.conf") -> list:
    return _parse_pacman_conf(conf_path, set())


# ── Zypper (openSUSE family) ────────────────────────────────────────

_OFFICIAL_ZYPPER_HOSTS = ("download.opensuse.org", "codecs.opensuse.org", "download.nvidia.com")


def scan_zypper(repos_d: str = "/etc/zypp/repos.d") -> list:
    entries = []
    if not os.path.isdir(repos_d):
        return entries
    for path in sorted(glob.glob(os.path.join(repos_d, "*.repo"))):
        parser = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                parser.read_file(f)
        except (OSError, configparser.Error):
            continue
        for section in parser.sections():
            fields = dict(parser.items(section))
            uri = fields.get("baseurl", "")
            enabled = fields.get("enabled", "1").strip() not in ("0", "false", "no")
            gpgcheck = fields.get("gpgcheck", "1").strip() not in ("0", "false", "no")
            warnings = []
            lname = section.lower()
            host = urlsplit(uri).netloc.split("@")[-1] if uri else ""
            # 2026-08-05: real bug found on a Tumbleweed KDE VM — the
            # alias for the official "Main Update Repository" is
            # "download.opensuse.org-tumbleweed" (no "update"
            # substring at all; only the localized `name=` display
            # text says so), so it fell through to "Esterno". Host-
            # based detection (same approach already used for APT)
            # doesn't depend on what any particular alias happens to
            # spell out.
            if host in _OFFICIAL_ZYPPER_HOSTS:
                kind = KIND_OFFICIAL
            elif "oss" in lname or "non-oss" in lname or "update" in lname:
                kind = KIND_OFFICIAL
            elif "packman" in lname:
                kind = KIND_COMMUNITY
            elif not uri:
                kind = KIND_NEEDS_REVIEW
                warnings.append(WARNING_NO_URI)
            else:
                kind = KIND_EXTERNAL
            if not gpgcheck:
                warnings.append(WARNING_GPGCHECK_DISABLED)
            entries.append(RepoEntry(
                name=fields.get("name", section), family="opensuse", kind=kind,
                enabled=enabled, source_file=path, uri=redact_credentials(uri),
                alias=section, signed=gpgcheck, warnings=warnings,
            ))
    return entries


# ── Flatpak remotes (reused as "repositories" too) ──────────────────

def scan_flatpak() -> list:
    from core.software_repo.flatpak_manager import list_remotes, SCOPE_SYSTEM, SCOPE_USER
    entries = []
    for scope in (SCOPE_SYSTEM, SCOPE_USER):
        for r in list_remotes(scope):
            kind = KIND_UNIVERSAL if r.is_flathub else KIND_EXTERNAL
            entries.append(RepoEntry(
                name=f"{r.name} ({scope})", family="flatpak", kind=kind,
                enabled=r.enabled, source_file=f"flatpak --{scope}",
                uri=redact_credentials(r.url), alias=r.name, scope=scope, signed=None,
            ))
    return entries


# ── Dispatch + summary ───────────────────────────────────────────────

_FAMILY_SCANNERS = {
    "debian": lambda: scan_apt(),
    "fedora": lambda: scan_dnf(),
    "arch": lambda: scan_pacman(),
    "opensuse": lambda: scan_zypper(),
}


def scan_all(family: str, include_flatpak: bool = True) -> dict:
    entries = []
    scanner = _FAMILY_SCANNERS.get(family)
    if scanner:
        entries += scanner()
    if include_flatpak:
        entries += scan_flatpak()

    summary = {
        "official_active": sum(1 for e in entries if e.kind in (KIND_OFFICIAL, KIND_UNIVERSAL) and e.enabled),
        "external_active": sum(1 for e in entries if e.kind in (KIND_EXTERNAL, KIND_COMMUNITY) and e.enabled),
        "disabled": sum(1 for e in entries if not e.enabled),
        "needs_review": sum(1 for e in entries if e.kind in (KIND_NEEDS_REVIEW, KIND_UNKNOWN) or e.warnings),
    }
    return {"entries": [e.to_dict() for e in entries], "summary": summary}
