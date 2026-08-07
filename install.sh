#!/usr/bin/env bash
# M.G Linux Toolbox — automatic installer for beginners.
#
# The definitive public command will be documented after the first release.
#
# Design rules (do not change without re-reading these):
#   - Runs entirely as the NORMAL user. Never re-execs itself with sudo,
#     never expects "curl ... | sudo bash". sudo is only ever invoked for
#     the few real system dependency packages (Python/GTK4/libadwaita/
#     FUSE), one real package-manager call at a time, never for anything
#     under the user's own home directory.
#   - The app itself is installed entirely under the user's own $HOME
#     (~/.local/opt, ~/.local/bin, ~/.local/share) — nothing under /usr,
#     /opt, or any other system path.
#   - Every message a beginner sees is a short, plain sentence — never a
#     raw curl/tar/HTTP error.
#   - Idempotent: running this twice never creates duplicate files, and
#     correctly turns an existing install into an update.
#   - The downloaded checksum is verified BEFORE the AppImage that's
#     already installed (if any) is ever touched. A failed checksum
#     changes nothing.
#
# This script is a bootstrap installer, not a second copy of the
# in-app updater (core/updater/ inside the Python source, used by the
# "Check for updates" button while the app is already running). It only
# needs enough logic to pick the right release/asset once, at install
# time, when the app's own Python package isn't on disk yet to import.
set -euo pipefail

# ── Centralized configuration ───────────────────────────────────
APP_VERSION="0.9.0-beta.7"
APPIMAGE_RELEASE_NAME="MG-Linux-Toolbox-${APP_VERSION}-x86_64.AppImage"
GITHUB_OWNER="gregoriomangano"
GITHUB_REPOSITORY="mg-linux-toolbox"
GITHUB_REPOSITORY_URL="https://github.com/${GITHUB_OWNER}/${GITHUB_REPOSITORY}"
RELEASE_TAG="v${APP_VERSION}"
RELEASE_BASE_URL="${GITHUB_REPOSITORY_URL}/releases/download/${RELEASE_TAG}"
RELEASE_APPIMAGE_URL="${RELEASE_BASE_URL}/${APPIMAGE_RELEASE_NAME}"
RELEASE_CHECKSUM_URL="${RELEASE_APPIMAGE_URL}.sha256"
APP_DISPLAY_NAME="M.G Linux Toolbox"
# During the Beta phase this defaults to "beta" (sees beta AND stable
# releases, never alpha). Once a stable release exists and this project
# moves past Beta, change ONLY this one line to "stable" — every other
# piece of channel logic in this script reads from here.
DEFAULT_CHANNEL="beta"

INSTALL_DIR="$HOME/.local/opt/mg-linux-toolbox"
BACKUP_DIR="$INSTALL_DIR/backup"
APPIMAGE_NAME="MG-Linux-Toolbox.AppImage"
APPIMAGE_PATH="$INSTALL_DIR/$APPIMAGE_NAME"
VERSION_FILE="$INSTALL_DIR/.version"
CHANNEL_FILE="$INSTALL_DIR/.channel"
BIN_DIR="$HOME/.local/bin"
BIN_PATH="$BIN_DIR/mg-linux-toolbox"
XDG_DATA_HOME_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
DESKTOP_DIR="$XDG_DATA_HOME_DIR/applications"
DESKTOP_PATH="$DESKTOP_DIR/mg-linux-toolbox.desktop"
ICON_DIR="$XDG_DATA_HOME_DIR/icons/hicolor/256x256/apps"
ICON_PATH="$ICON_DIR/mg-linux-toolbox.png"

# Root-owned privileged component (the ONLY files this script ever
# installs with sudo outside $HOME). /usr/libexec is preferred; /usr/lib
# is the fallback for distributions without it.
HELPER_NAME="mg-privileged-helper"
if [ -d /usr/libexec ]; then
    HELPER_DIR="/usr/libexec/mg-linux-toolbox"
else
    HELPER_DIR="/usr/lib/mg-linux-toolbox"
fi
HELPER_PATH="$HELPER_DIR/$HELPER_NAME"
POLKIT_ACTIONS_DIR="/usr/share/polkit-1/actions"
POLKIT_POLICY_NAME="it.manganogregorio.mg-linux-toolbox.policy"
POLKIT_POLICY_PATH="$POLKIT_ACTIONS_DIR/$POLKIT_POLICY_NAME"

API_BASE="https://api.github.com"
UA="mg-linux-toolbox-install.sh"

# ── Simple, human messages — never a raw technical error ──────────────
say()     { printf '%s\n' "$1"; }
say_step(){ printf '\n==> %s\n' "$1"; }
say_err() { printf 'Errore: %s\n' "$1" >&2; }
say_warn(){ printf 'Attenzione: %s\n' "$1"; }

fail() {
    say_err "$1"
    exit 1
}

# ── Argument parsing ────────────────────────────────────────────────
ACTION="install"
CHANNEL_OVERRIDE=""
DO_DEPS=1

print_help() {
    cat <<'HELP'
M.G Linux Toolbox — installer automatico

Uso:
  install.sh                Installa oppure aggiorna M.G Linux Toolbox
  install.sh --beta         Usa il canale Beta (include le pre-release)
  install.sh --stable       Usa soltanto le versioni stabili
  install.sh --check        Controlla solo se è disponibile una nuova versione
  install.sh --no-deps      Non installare le dipendenze di sistema
  install.sh --help         Mostra questo messaggio

Il programma viene installato soltanto nella tua cartella personale:
  ~/.local/opt/mg-linux-toolbox/

Non serve mai eseguire questo script con "sudo".
HELP
}

for arg in "$@"; do
    case "$arg" in
        --beta) CHANNEL_OVERRIDE="beta" ;;
        --stable) CHANNEL_OVERRIDE="stable" ;;
        --check) ACTION="check" ;;
        --no-deps) DO_DEPS=0 ;;
        --help|-h) print_help; exit 0 ;;
        *) fail "Opzione non riconosciuta: $arg (usa --help per l'elenco)" ;;
    esac
done

if [ "$(id -u)" -eq 0 ]; then
    fail "Non eseguire questo script come root o con sudo. Eseguilo come utente normale: sudo lo richiederà da solo, soltanto se serve davvero."
fi

# ── Distribution detection ─────────────────────────────────────────
DISTRO_ID=""
DISTRO_ID_LIKE=""
DISTRO_VERSION_ID=""
DISTRO_NAME=""
DISTRO_FAMILY=""   # debian | fedora | arch | opensuse | unknown

detect_distro() {
    local os_release_file="${MG_TOOLBOX_OS_RELEASE:-/etc/os-release}"
    if [ -r "$os_release_file" ]; then
        # shellcheck disable=SC1090,SC1091
        . "$os_release_file"
        DISTRO_ID="${ID:-}"
        DISTRO_ID_LIKE="${ID_LIKE:-}"
        DISTRO_VERSION_ID="${VERSION_ID:-}"
        DISTRO_NAME="${PRETTY_NAME:-$ID}"
    fi

    local combined="$DISTRO_ID $DISTRO_ID_LIKE"
    case "$combined" in
        *debian*|*ubuntu*|*mint*|*pop*|*elementary*) DISTRO_FAMILY="debian" ;;
        *fedora*|*rhel*|*centos*|*rocky*|*alma*) DISTRO_FAMILY="fedora" ;;
        *arch*|*manjaro*|*garuda*|*endeavour*) DISTRO_FAMILY="arch" ;;
        *opensuse*|*suse*|*sles*) DISTRO_FAMILY="opensuse" ;;
        *) DISTRO_FAMILY="unknown" ;;
    esac
}

detect_arch() {
    case "$(uname -m)" in
        x86_64|amd64) echo "x86_64" ;;
        *) echo "" ;;
    esac
}

# ── Package manager detection (real tool present, not guessed) ────────
PKG_MANAGER=""
detect_pkg_manager() {
    if command -v apt-get >/dev/null 2>&1; then PKG_MANAGER="apt"
    elif command -v dnf5 >/dev/null 2>&1; then PKG_MANAGER="dnf5"
    elif command -v dnf >/dev/null 2>&1; then PKG_MANAGER="dnf"
    elif command -v pacman >/dev/null 2>&1; then PKG_MANAGER="pacman"
    elif command -v zypper >/dev/null 2>&1; then PKG_MANAGER="zypper"
    else PKG_MANAGER=""
    fi
}

# Returns 0 (available) / 1 (not available) — never installs anything,
# just checks, so a missing package-name *variant* (e.g. libfuse2 vs.
# libfuse2t64) can be tried without failing the whole run.
pkg_available() {
    local pkg="$1"
    case "$PKG_MANAGER" in
        apt) apt-cache show "$pkg" >/dev/null 2>&1 ;;
        dnf5) dnf5 info "$pkg" >/dev/null 2>&1 ;;
        dnf) dnf info "$pkg" >/dev/null 2>&1 ;;
        pacman) pacman -Si "$pkg" >/dev/null 2>&1 || pacman -Qi "$pkg" >/dev/null 2>&1 ;;
        # "zypper info" only matches real package names, not virtual
        # capabilities (e.g. Tumbleweed's rolling "python3" is provided
        # by a versioned package like python313-base, not a package
        # literally named "python3"). "search --provides" resolves
        # capabilities the same way "zypper install" would, read-only
        # and without needing sudo just to check.
        zypper) zypper --non-interactive search --provides --match-exact "$pkg" >/dev/null 2>&1 ;;
        *) return 1 ;;
    esac
}

pkg_installed() {
    local pkg="$1"
    case "$PKG_MANAGER" in
        apt) dpkg -s "$pkg" >/dev/null 2>&1 ;;
        dnf5|dnf) rpm -q "$pkg" >/dev/null 2>&1 ;;
        pacman) pacman -Qi "$pkg" >/dev/null 2>&1 ;;
        zypper) rpm -q "$pkg" >/dev/null 2>&1 ;;
        *) return 1 ;;
    esac
}

pkg_install() {
    local pkg="$1"
    case "$PKG_MANAGER" in
        apt) sudo apt-get install -y "$pkg" ;;
        dnf5) sudo dnf5 install -y "$pkg" ;;
        dnf) sudo dnf install -y "$pkg" ;;
        pacman) sudo pacman -S --noconfirm --needed "$pkg" ;;
        zypper) sudo zypper --non-interactive install "$pkg" ;;
        *) return 1 ;;
    esac
}

# Tries each candidate name in order, installs the FIRST one that's
# really available in this system's repositories. Never fails the
# whole run just because one naming variant doesn't exist here
# (e.g. libfuse2 on older Debian/Ubuntu vs. libfuse2t64 on newer ones).
install_first_available() {
    local label="$1"; shift
    local candidate
    for candidate in "$@"; do
        if pkg_installed "$candidate"; then
            say "  già presente: $candidate"
            return 0
        fi
        if pkg_available "$candidate"; then
            say "  installo: $candidate"
            if pkg_install "$candidate"; then
                return 0
            fi
        fi
    done
    say_warn "nessuna delle varianti note di \"$label\" è stata trovata in questo sistema ($*). Se il programma non si avvia, potrebbe servire installarla manualmente."
    return 1
}

# Builds a read-only preview of what is missing. The user sees the
# complete list and confirms once before any sudo/package-manager call.
MISSING_DEP_PACKAGES=()
MISSING_DEP_LABELS=()
inspect_dependency() {
    local label="$1"; shift
    local candidate first_available=""
    for candidate in "$@"; do
        if pkg_installed "$candidate"; then
            return 0
        fi
        if [ -z "$first_available" ] && pkg_available "$candidate"; then
            first_available="$candidate"
        fi
    done
    if [ -n "$first_available" ]; then
        MISSING_DEP_LABELS+=("$label")
        MISSING_DEP_PACKAGES+=("$first_available")
    else
        say_warn "$label non risulta installato e non è stato trovato con un nome noto nei repository della distribuzione."
    fi
}

confirm_missing_dependencies() {
    [ "$DO_DEPS" -eq 1 ] || return 0
    MISSING_DEP_PACKAGES=()
    MISSING_DEP_LABELS=()

    case "$DISTRO_FAMILY" in
        debian)
            inspect_dependency "Python 3" python3
            inspect_dependency "PyGObject" python3-gi
            inspect_dependency "GTK4" gir1.2-gtk-4.0
            inspect_dependency "Libadwaita" gir1.2-adw-1
            inspect_dependency "FUSE" libfuse2t64 libfuse2 libfuse3-3
            ;;
        fedora)
            inspect_dependency "Python 3" python3
            inspect_dependency "PyGObject" python3-gobject
            inspect_dependency "GTK4" gtk4
            inspect_dependency "Libadwaita" libadwaita
            inspect_dependency "FUSE" fuse fuse-libs fuse2fs
            ;;
        arch)
            inspect_dependency "Python 3" python
            inspect_dependency "PyGObject" python-gobject
            inspect_dependency "GTK4" gtk4
            inspect_dependency "Libadwaita" libadwaita
            inspect_dependency "FUSE" fuse2 fuse3
            ;;
        opensuse)
            inspect_dependency "Python 3" python3
            inspect_dependency "PyGObject" python3-gobject
            inspect_dependency "GTK4" typelib-1_0-Gtk-4_0 gtk4
            inspect_dependency "Libadwaita" typelib-1_0-Adw-1 libadwaita
            inspect_dependency "FUSE" libfuse2 fuse
            ;;
        *) return 0 ;;
    esac

    if [ "${#MISSING_DEP_PACKAGES[@]}" -eq 0 ]; then
        say "Le dipendenze richieste risultano già presenti."
        return 0
    fi

    say "Prima di continuare servono questi componenti di sistema:"
    local index
    for index in "${!MISSING_DEP_PACKAGES[@]}"; do
        say "  - ${MISSING_DEP_LABELS[$index]} (${MISSING_DEP_PACKAGES[$index]})"
    done
    say "sudo verrà usato soltanto per installare questi pacchetti condivisi."

    local answer=""
    if [ -r /dev/tty ]; then
        printf 'Vuoi installarli ora? [s/N] ' > /dev/tty
        read -r answer < /dev/tty || true
    else
        printf 'Vuoi installarli ora? [s/N] '
        read -r answer || true
    fi
    case "$answer" in
        s|S|si|SI|sì|SÌ) ;;
        *) fail "Installazione annullata: nessun pacchetto di sistema è stato modificato." ;;
    esac
}

install_dependencies() {
    if [ "$DO_DEPS" -eq 0 ]; then
        say "Installazione delle dipendenze saltata (--no-deps)."
        return 0
    fi
    say_step "Controllo delle dipendenze."

    # Every call below is deliberately followed by "|| true": a missing
    # package-name variant on this particular system must only produce
    # the warning printed inside install_first_available() and let the
    # NEXT dependency still be checked/installed — under this script's
    # "set -e", a bare non-zero return here would otherwise silently
    # abort the entire installation right after the first warning.
    case "$DISTRO_FAMILY" in
        debian)
            install_first_available "Python 3" python3 || true
            install_first_available "PyGObject" python3-gi || true
            install_first_available "GTK4 (introspection)" gir1.2-gtk-4.0 || true
            install_first_available "libadwaita (introspection)" gir1.2-adw-1 || true
            install_first_available "FUSE (per l'AppImage)" libfuse2t64 libfuse2 libfuse3-3 || true
            ;;
        fedora)
            install_first_available "Python 3" python3 || true
            install_first_available "PyGObject" python3-gobject || true
            install_first_available "GTK4" gtk4 || true
            install_first_available "libadwaita" libadwaita || true
            install_first_available "FUSE (per l'AppImage)" fuse fuse-libs fuse2fs || true
            ;;
        arch)
            install_first_available "Python 3" python || true
            install_first_available "PyGObject" python-gobject || true
            install_first_available "GTK4" gtk4 || true
            install_first_available "libadwaita" libadwaita || true
            install_first_available "FUSE (per l'AppImage)" fuse2 fuse3 || true
            ;;
        opensuse)
            install_first_available "Python 3" python3 || true
            install_first_available "PyGObject" python3-gobject || true
            install_first_available "GTK4 (typelib)" typelib-1_0-Gtk-4_0 gtk4 || true
            install_first_available "libadwaita (typelib)" typelib-1_0-Adw-1 libadwaita || true
            install_first_available "FUSE (per l'AppImage)" libfuse2 fuse || true
            ;;
        *)
            say_warn "distribuzione non riconosciuta automaticamente: le dipendenze non sono state installate. Assicurati di avere Python 3, PyGObject, GTK4, libadwaita e FUSE."
            ;;
    esac
}

# ── GitHub release selection (embedded, minimal — see file header) ────
# Uses python3 (already ensured present by install_dependencies) purely
# as a portable JSON-capable HTTP client — no extra tool (jq, etc.)
# required on a fresh system.
fetch_release_json() {
    python3 - "$GITHUB_OWNER" "$GITHUB_REPOSITORY" "${MG_TOOLBOX_API_BASE:-https://api.github.com}" <<'PYEOF'
import json, sys, urllib.request, urllib.error

owner, repo, api_base = sys.argv[1], sys.argv[2], sys.argv[3]
url = f"{api_base}/repos/{owner}/{repo}/releases"
req = urllib.request.Request(url, headers={
    "Accept": "application/vnd.github+json",
    "User-Agent": "mg-linux-toolbox-install.sh",
})
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(resp.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print(f"__HTTP_ERROR__{e.code}")
except urllib.error.URLError as e:
    print(f"__NETWORK_ERROR__{e.reason}")
PYEOF
}

# Picks, from the raw releases JSON, the best release for the given
# channel + the exact asset names for this architecture. Prints three
# lines: version, appimage_url, checksum_url (empty version if none
# found). Mirrors core/updater/semver.py's precedence rules in
# miniature — deliberately NOT importing that module (it isn't on disk
# until the AppImage itself is installed).
select_release_assets() {
    local raw_json="$1" channel="$2" arch="$3" current_version="$4"
    local json_tmp
    json_tmp="$(mktemp)"
    printf '%s' "$raw_json" > "$json_tmp"
    python3 - "$channel" "$arch" "$json_tmp" "$current_version" <<'PYEOF'
import json, re, sys

channel, arch, json_path, current_version = sys.argv[1:5]
with open(json_path, encoding="utf-8") as f:
    raw = f.read()

SEMVER_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")

def parse(v):
    m = SEMVER_RE.match(v.strip())
    if not m:
        return None
    return (int(m["major"]), int(m["minor"]), int(m["patch"]), m["prerelease"])

def rel_channel(pre):
    if pre is None:
        return "stable"
    p = pre.lower()
    if p.startswith("alpha"):
        return "alpha"
    if p.startswith("beta"):
        return "beta"
    if p.startswith("rc"):
        return "beta"
    return "unknown"

def sort_key(v):
    major, minor, patch, pre = v
    if pre is None:
        return (major, minor, patch, 1, ())
    parts = tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split("."))
    return (major, minor, patch, 0, parts)

try:
    releases = json.loads(raw)
except json.JSONDecodeError:
    print("")
    sys.exit(0)

if not isinstance(releases, list):
    print("")
    sys.exit(0)

candidates = []
for r in releases:
    if r.get("draft"):
        continue
    tag = r.get("tag_name", "")
    version_str = tag[1:] if tag.startswith("v") else tag
    parsed = parse(version_str)
    if parsed is None:
        continue
    ch = rel_channel(parsed[3])
    if ch == "alpha":
        continue
    if channel == "stable" and ch != "stable":
        continue
    candidates.append((parsed, version_str, r.get("assets", [])))

if not candidates:
    print("")
    sys.exit(0)

candidates.sort(key=lambda c: sort_key(c[0]))
best_parsed, version, assets = candidates[-1]

# Never offer an older (or identical) release as "an update" — real
# SemVer ordering, not a string-equality check.
current_parsed = parse(current_version) if current_version else None
is_newer = 1 if current_parsed is None else (1 if sort_key(best_parsed) > sort_key(current_parsed) else 0)

appimage_name = f"MG-Linux-Toolbox-{version}-{arch}.AppImage"
checksum_name = f"{appimage_name}.sha256"
appimage_url = ""
checksum_url = ""
for a in assets:
    if a.get("name") == appimage_name:
        appimage_url = a.get("browser_download_url", "")
    elif a.get("name") == checksum_name:
        checksum_url = a.get("browser_download_url", "")

print(version)
print(appimage_url)
print(checksum_url)
print(is_newer)
PYEOF
    local rc=$?
    rm -f "$json_tmp"
    return $rc
}

# ── Privileged component (helper + Polkit policy) ─────────────────────
# The helper is extracted from the ALREADY VERIFIED AppImage (checksum
# checked earlier in this run), never downloaded separately. sudo is
# used only for these files, after showing exactly what will be
# installed. Idempotent: if the installed helper is byte-identical and
# correctly owned, sudo is never even invoked.

extract_privileged_component() {
    # $1 = destination dir. Extracts helper + policy from the AppImage.
    local dest="$1"
    (cd "$dest" && "$APPIMAGE_PATH" --appimage-extract "$HELPER_NAME" >/dev/null 2>&1) || true
    (cd "$dest" && "$APPIMAGE_PATH" --appimage-extract "$POLKIT_POLICY_NAME" >/dev/null 2>&1) || true
    if [ ! -f "$dest/squashfs-root/$HELPER_NAME" ] || [ ! -f "$dest/squashfs-root/$POLKIT_POLICY_NAME" ]; then
        return 1
    fi
    return 0
}

helper_already_current() {
    # Identical content AND root:root AND not group/other-writable.
    local candidate="$1"
    [ -f "$HELPER_PATH" ] || return 1
    local installed_sha candidate_sha owner mode
    read -r installed_sha _ < <(sha256sum "$HELPER_PATH") || return 1
    read -r candidate_sha _ < <(sha256sum "$candidate") || return 1
    [ "$installed_sha" = "$candidate_sha" ] || return 1
    owner="$(stat -c '%u:%g' "$HELPER_PATH" 2>/dev/null)" || return 1
    [ "$owner" = "0:0" ] || return 1
    mode="$(stat -c '%a' "$HELPER_PATH" 2>/dev/null)" || return 1
    case "$mode" in *[2367]?|*?[2367]) return 1 ;; esac
    [ -f "$POLKIT_POLICY_PATH" ] || return 1
    return 0
}

verify_privileged_install() {
    local owner mode
    owner="$(stat -c '%u:%g' "$HELPER_PATH" 2>/dev/null)" || return 1
    [ "$owner" = "0:0" ] || return 1
    mode="$(stat -c '%a' "$HELPER_PATH" 2>/dev/null)" || return 1
    [ "$mode" = "755" ] || return 1
    [ -f "$POLKIT_POLICY_PATH" ] || return 1
    return 0
}

install_privileged_component() {
    say_step "Componente amministrativo (funzioni che richiedono la password)."

    local extract_dir="$TMP_DIR/priv"
    mkdir -p "$extract_dir"
    if ! extract_privileged_component "$extract_dir"; then
        say_warn "il componente amministrativo non è incluso in questo pacchetto: le funzioni che richiedono privilegi resteranno disattivate. L'app funziona comunque in sola lettura."
        return 0
    fi
    local new_helper="$extract_dir/squashfs-root/$HELPER_NAME"
    local new_policy="$extract_dir/squashfs-root/$POLKIT_POLICY_NAME"

    # The policy authorizes ONLY the exact installed path; align it when
    # this system uses the /usr/lib fallback instead of /usr/libexec.
    if [ "$HELPER_DIR" != "/usr/libexec/mg-linux-toolbox" ]; then
        sed -i "s|/usr/libexec/mg-linux-toolbox/$HELPER_NAME|$HELPER_PATH|" "$new_policy"
    fi

    if helper_already_current "$new_helper"; then
        say "Il componente amministrativo è già installato e aggiornato."
        return 0
    fi

    say "Per abilitare le modifiche di sistema (KSM, CPU, batteria, VFIO, ecc.)"
    say "verranno installati, con la tua password, SOLTANTO questi file di sistema:"
    say "  - $HELPER_PATH        (eseguibile di root, proprietà root:root)"
    say "  - $POLKIT_POLICY_PATH (regola di autorizzazione Polkit)"
    say "Nient'altro fuori dalla tua cartella personale verrà toccato."

    local answer=""
    if [ -n "${MG_TOOLBOX_HELPER_ANSWER:-}" ]; then
        # Test/automation hook: never used interactively.
        answer="$MG_TOOLBOX_HELPER_ANSWER"
    elif { printf 'Vuoi installare il componente amministrativo ora? [s/N] ' > /dev/tty; } 2>/dev/null; then
        read -r answer < /dev/tty 2>/dev/null || true
    elif [ -t 0 ]; then
        printf 'Vuoi installare il componente amministrativo ora? [s/N] '
        read -r answer || true
    else
        say "Esecuzione non interattiva: il componente amministrativo non è stato installato."
        say "Potrai installarlo in seguito rieseguendo questo script da un terminale."
        return 0
    fi
    case "$answer" in
        s|S|si|SI|sì|SÌ) ;;
        *)
            say "Componente amministrativo non installato: l'app funzionerà in sola lettura."
            say "Potrai installarlo in seguito rieseguendo questo script."
            return 0
            ;;
    esac

    # Keep the previous helper for rollback if a later step fails.
    local had_previous=0
    if [ -f "$HELPER_PATH" ]; then
        had_previous=1
        sudo cp -f -- "$HELPER_PATH" "$HELPER_PATH.previous" || true
    fi

    if ! sudo install -d -o root -g root -m 0755 "$HELPER_DIR" || \
       ! sudo install -o root -g root -m 0755 -- "$new_helper" "$HELPER_PATH" || \
       ! sudo install -o root -g root -m 0644 -- "$new_policy" "$POLKIT_POLICY_PATH"; then
        if [ "$had_previous" -eq 1 ] && sudo test -f "$HELPER_PATH.previous"; then
            sudo mv -f -- "$HELPER_PATH.previous" "$HELPER_PATH" || true
        fi
        say_warn "l'installazione del componente amministrativo non è riuscita. L'app funziona comunque: le funzioni che richiedono privilegi resteranno disattivate."
        return 0
    fi
    sudo rm -f -- "$HELPER_PATH.previous" 2>/dev/null || true

    if verify_privileged_install; then
        say "Componente amministrativo installato e verificato (root:root, permessi corretti)."
    else
        say_warn "il componente amministrativo è stato copiato ma la verifica di proprietario/permessi non è riuscita. Riesegui lo script o controlla manualmente $HELPER_PATH."
    fi
}

# Lets the test suite `source` this file to unit-test individual
# functions (distro/package-manager detection, package-name fallback
# logic) without ever running the real install flow.
if [ "${MG_TOOLBOX_SOURCE_ONLY:-0}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi

# ── Main flow ───────────────────────────────────────────────────────
detect_distro
detect_pkg_manager
ARCH="$(detect_arch)"

if [ -n "$DISTRO_NAME" ]; then
    say "Distribuzione riconosciuta: $DISTRO_NAME."
else
    say_warn "non è stato possibile riconoscere con certezza la distribuzione (procedo comunque)."
fi

if [ -z "$ARCH" ]; then
    fail "Architettura del computer non supportata (\"$(uname -m)\"). Il pacchetto disponibile al momento è soltanto per x86_64."
fi

CHANNEL="${CHANNEL_OVERRIDE:-$DEFAULT_CHANNEL}"
if [ -z "$CHANNEL_OVERRIDE" ] && [ -f "$CHANNEL_FILE" ]; then
    CHANNEL="$(cat "$CHANNEL_FILE")"
fi

CURRENT_VERSION=""
[ -f "$VERSION_FILE" ] && CURRENT_VERSION="$(cat "$VERSION_FILE")"

LATEST_VERSION=""
APPIMAGE_URL=""
CHECKSUM_URL=""
IS_NEWER=""
LOCAL_SOURCE="${MG_TOOLBOX_LOCAL_SOURCE:-}"
LOCAL_CHECKSUM="${MG_TOOLBOX_LOCAL_CHECKSUM:-}"
LOCAL_MODE=0

if [ -n "$LOCAL_SOURCE" ] || [ -n "$LOCAL_CHECKSUM" ]; then
    [ -n "$LOCAL_SOURCE" ] && [ -n "$LOCAL_CHECKSUM" ] || \
        fail "La modalità locale richiede sia il file AppImage sia il relativo checksum."
    [ -f "$LOCAL_SOURCE" ] && [ -r "$LOCAL_SOURCE" ] || \
        fail "Il file AppImage locale indicato non è leggibile."
    [ -f "$LOCAL_CHECKSUM" ] && [ -r "$LOCAL_CHECKSUM" ] || \
        fail "Il checksum locale indicato non è leggibile."
    LOCAL_MODE=1
    LATEST_VERSION="$APP_VERSION"
    APPIMAGE_URL="$LOCAL_SOURCE"
    CHECKSUM_URL="$LOCAL_CHECKSUM"
    if [ -z "$CURRENT_VERSION" ] || [ "$CURRENT_VERSION" != "$LATEST_VERSION" ]; then
        IS_NEWER=1
    else
        IS_NEWER=0
    fi
    say_step "Uso del pacchetto locale autorizzato per il collaudo."
else
    say_step "Download delle informazioni sulla versione più recente."
    RAW_JSON="$(fetch_release_json)"

    case "$RAW_JSON" in
        __HTTP_ERROR__404*)
            fail "La prima versione online non è ancora disponibile. Riprova più tardi."
            ;;
        __HTTP_ERROR__*)
            fail "Il server di GitHub ha risposto con un errore. Riprova più tardi."
            ;;
        __NETWORK_ERROR__*)
            fail "Non è stato possibile contattare GitHub. Controlla la connessione a Internet."
            ;;
    esac

    {
        read -r LATEST_VERSION || true
        read -r APPIMAGE_URL || true
        read -r CHECKSUM_URL || true
        read -r IS_NEWER || true
    } < <(select_release_assets "$RAW_JSON" "$CHANNEL" "$ARCH" "$CURRENT_VERSION")
fi

if [ -z "${LATEST_VERSION:-}" ]; then
    fail "La prima versione online non è ancora disponibile per il canale \"$CHANNEL\"."
fi

if [ -z "${APPIMAGE_URL:-}" ] || [ -z "${CHECKSUM_URL:-}" ]; then
    fail "Non è disponibile una versione per questa architettura ($ARCH)."
fi

if [ "$ACTION" = "check" ]; then
    if [ -z "$CURRENT_VERSION" ]; then
        say "M.G Linux Toolbox non risulta installato. Versione disponibile: $LATEST_VERSION."
    elif [ "${IS_NEWER:-0}" = "1" ]; then
        say "È disponibile una nuova versione: $LATEST_VERSION (hai la $CURRENT_VERSION)."
    else
        say "Stai già usando la versione più recente ($CURRENT_VERSION)."
    fi
    exit 0
fi

if [ -n "$CURRENT_VERSION" ] && [ "${IS_NEWER:-0}" != "1" ] && [ -f "$APPIMAGE_PATH" ]; then
    say "Stai già usando la versione più recente ($CURRENT_VERSION)."
    exit 0
fi

confirm_missing_dependencies
install_dependencies

say_step "Download di $APP_DISPLAY_NAME $LATEST_VERSION."
mkdir -p "$INSTALL_DIR" "$BACKUP_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"

TMP_DIR="$(mktemp -d)"
cleanup_tmp() { rm -rf "$TMP_DIR"; }
trap cleanup_tmp EXIT

DOWNLOAD_APPIMAGE="$TMP_DIR/$APPIMAGE_NAME.part"
DOWNLOAD_CHECKSUM="$TMP_DIR/checksum.sha256"

if [ "$LOCAL_MODE" -eq 1 ]; then
    if ! cp -f -- "$APPIMAGE_URL" "$DOWNLOAD_APPIMAGE"; then
        fail "Il pacchetto locale non è stato copiato. La versione attuale non è stata modificata."
    fi
    if ! cp -f -- "$CHECKSUM_URL" "$DOWNLOAD_CHECKSUM"; then
        fail "Il checksum locale non è stato copiato. La versione attuale non è stata modificata."
    fi
else
    if ! curl -fsSL -o "$DOWNLOAD_APPIMAGE" "$APPIMAGE_URL"; then
        fail "Il download non è riuscito. La versione attuale non è stata modificata."
    fi
    if ! curl -fsSL -o "$DOWNLOAD_CHECKSUM" "$CHECKSUM_URL"; then
        fail "Il download del file di controllo non è riuscito. La versione attuale non è stata modificata."
    fi
fi

say_step "Controllo di sicurezza del file scaricato."
# Pure-bash field extraction (read/parameter expansion) instead of awk:
# a minimal system may not have awk pre-installed, and this script
# never adds it to any distro's dependency list.
read -r EXPECTED_SHA _ < "$DOWNLOAD_CHECKSUM"
read -r ACTUAL_SHA _ < <(sha256sum "$DOWNLOAD_APPIMAGE")

if [[ ! "$EXPECTED_SHA" =~ ^[[:xdigit:]]{64}$ ]] || [ "${EXPECTED_SHA,,}" != "$ACTUAL_SHA" ]; then
    fail "Il file scaricato non ha superato il controllo di sicurezza. La versione attuale non è stata modificata."
fi
say "Il file è stato controllato correttamente."
chmod 755 "$DOWNLOAD_APPIMAGE"

say_step "Controllo dei requisiti minimi (GTK4, Libadwaita)."
# Real check against THIS system's installed libraries, using the
# verified AppImage's own bundled core/version.py as the single source
# of truth (never a duplicated, driftable copy of the version numbers
# here) — before ever declaring the installation successful, per the
# permanent rule: an install must not succeed only for the app to fail
# with a version error the very first time it's launched.
VERSION_EXTRACT_DIR="$TMP_DIR/version-check"
mkdir -p "$VERSION_EXTRACT_DIR"
if (cd "$VERSION_EXTRACT_DIR" && "$DOWNLOAD_APPIMAGE" --appimage-extract usr/share/mg-linux-toolbox/core/version.py) >/dev/null 2>&1; then
    # Run from inside VERSION_EXTRACT_DIR (never from wherever this
    # script was invoked): `python3 -c` implicitly puts the current
    # directory first on sys.path, and if install.sh is ever run with
    # its cwd inside a checkout that happens to contain a real `core`
    # package, that would shadow PYTHONPATH and silently check the
    # WRONG (real, already-installed) core.version instead of the
    # just-downloaded AppImage's own bundled copy — caught by this
    # script's own test suite the first time this was written.
    VERSION_CHECK_OUTPUT="$(cd "$VERSION_EXTRACT_DIR" && PYTHONPATH="$VERSION_EXTRACT_DIR/squashfs-root/usr/share/mg-linux-toolbox" python3 -c '
import sys
try:
    from core.version import check_runtime_requirements
except Exception:
    print("IMPORT_FAILED")
    sys.exit(1)
req = check_runtime_requirements()
if req["ok"]:
    print("OK")
    sys.exit(0)
print("Trovata: " + str(req.get("found", {})))
print("Richiesta: " + str(req["required"]))
sys.exit(1)
' 2>/dev/null)" || true
    rm -rf "$VERSION_EXTRACT_DIR/squashfs-root"
    if [ "$VERSION_CHECK_OUTPUT" != "OK" ]; then
        say_err "M.G Linux Toolbox richiede una versione recente di GTK4 e Libadwaita."
        say_err "La versione presente su questo sistema è troppo vecchia."
        if [ "$VERSION_CHECK_OUTPUT" = "IMPORT_FAILED" ] || [ -z "$VERSION_CHECK_OUTPUT" ]; then
            say_err "PyGObject, GTK4 o Libadwaita non risultano installati."
        else
            printf '%s\n' "$VERSION_CHECK_OUTPUT" >&2
        fi
        fail "Installazione interrotta: i requisiti minimi non sono soddisfatti. La versione attuale non è stata modificata."
    fi
    say "Requisiti minimi soddisfatti."
else
    say_warn "non è stato possibile verificare i requisiti minimi prima dell'installazione (estrazione fallita); verranno controllati al primo avvio."
fi

# Back up whatever is currently installed BEFORE touching it, so a
# problem after this point can still be rolled back.
if [ -f "$APPIMAGE_PATH" ] && [ -n "$CURRENT_VERSION" ]; then
    cp -f "$APPIMAGE_PATH" "$BACKUP_DIR/previous-$CURRENT_VERSION.AppImage"
fi

if ! mv -f "$DOWNLOAD_APPIMAGE" "$APPIMAGE_PATH"; then
    if [ -f "$BACKUP_DIR/previous-$CURRENT_VERSION.AppImage" ]; then
        cp -f "$BACKUP_DIR/previous-$CURRENT_VERSION.AppImage" "$APPIMAGE_PATH"
    fi
    fail "L'installazione non è stata completata. La versione precedente non è stata modificata."
fi
echo "$LATEST_VERSION" > "$VERSION_FILE"
echo "$CHANNEL" > "$CHANNEL_FILE"

say_step "Creazione del comando e della voce nel menu."

cat > "$BIN_PATH" <<LAUNCHER
#!/usr/bin/env bash
exec "$APPIMAGE_PATH" "\$@"
LAUNCHER
chmod 755 "$BIN_PATH"

# The icon is extracted from the AppImage itself (mg-linux-toolbox.png
# at its root) — no separate download needed. Extracted inside the
# script's own $TMP_DIR (guaranteed to exist and be writable, cleaned up
# by the EXIT trap) rather than the caller's ambient working directory,
# which "curl ... | bash" never controls and can't assume is writable
# (or even still exists) at this point in the run.
if (cd "$TMP_DIR" && "$APPIMAGE_PATH" --appimage-extract mg-linux-toolbox.png) >/dev/null 2>&1; then
    if [ -f "$TMP_DIR/squashfs-root/mg-linux-toolbox.png" ]; then
        cp -f "$TMP_DIR/squashfs-root/mg-linux-toolbox.png" "$ICON_PATH"
    fi
    rm -rf "$TMP_DIR/squashfs-root"
fi

cat > "$DESKTOP_PATH" <<DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=$APP_DISPLAY_NAME
GenericName=System Toolbox
Comment=Pannello di controllo di sistema per Linux
Exec=$BIN_PATH
Icon=$([ -f "$ICON_PATH" ] && echo "$ICON_PATH" || echo "mg-linux-toolbox")
Terminal=false
StartupNotify=true
Categories=System;GTK;
Keywords=system;tools;utilities;settings;linux;toolbox;
DESKTOP
chmod 644 "$DESKTOP_PATH"

# Only refresh caches that actually exist on this system — never fail
# the install just because one of these optional tools is missing.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -d "$XDG_DATA_HOME_DIR/icons/hicolor" ]; then
    gtk-update-icon-cache -f -t "$XDG_DATA_HOME_DIR/icons/hicolor" >/dev/null 2>&1 || true
fi

say "M.G Linux Toolbox è stata aggiunta al menu delle applicazioni."

install_privileged_component

say_step "Installazione completata."
say "Versione installata: $LATEST_VERSION"
say "Trovi \"$APP_DISPLAY_NAME\" nel menu delle applicazioni del tuo desktop,"
say "oppure eseguendo \"mg-linux-toolbox\" da un terminale."
