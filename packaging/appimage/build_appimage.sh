#!/usr/bin/env bash
# Builds an AppImage into dist/, reading the version from
# core/version.py (the single source of truth — never hardcode it here).
#
# Output:
#   dist/MG-Linux-Toolbox-<version>-<arch>.AppImage
#   dist/MG-Linux-Toolbox-<version>-<arch>.AppImage.sha256
#
# Only builds for the architecture actually running this script — never
# fabricates an aarch64 build on an x86_64 machine (or vice versa).
# Never touches unrelated files and never uploads or publishes anything.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APPDIR_BUILD="$SCRIPT_DIR/.build-AppDir"
APPIMAGETOOL="$SCRIPT_DIR/tools/appimagetool.AppImage"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

fail() { log_error "$*"; exit 1; }

# ── Dependency checks — fail clearly, don't half-build ────────────
command -v python3 >/dev/null 2>&1 || fail "python3 non trovato. Installalo prima di continuare."
[ -x "$APPIMAGETOOL" ] || fail "appimagetool non trovato in $APPIMAGETOOL (atteso già presente in packaging/appimage/tools/)."
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum non trovato (fa parte di coreutils)."
command -v git >/dev/null 2>&1 || fail "git non trovato: serve per includere soltanto i file ufficiali tracciati."
command -v rsync >/dev/null 2>&1 || fail "rsync non trovato. Installalo prima di continuare."

# ── Version + architecture ────────────────────────────────────────
APP_VERSION="$(python3 -c 'import sys; sys.path.insert(0, "'"$ROOT_DIR"'"); from core.version import APP_VERSION; print(APP_VERSION)')"
[ -n "$APP_VERSION" ] || fail "Impossibile leggere APP_VERSION da core/version.py."

HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
    x86_64)  APPIMAGE_ARCH="x86_64" ;;
    aarch64) APPIMAGE_ARCH="aarch64" ;;
    *) fail "Architettura '$HOST_ARCH' non supportata da questo script (solo x86_64/aarch64)." ;;
esac
log_info "Versione: $APP_VERSION — Architettura rilevata: $APPIMAGE_ARCH"
log_warn "Verrà buildata SOLO l'architettura $APPIMAGE_ARCH (quella di questa macchina) — nessuna build aarch64 finta su host diversi."

# ── Fresh AppDir payload (never reuse a stale one) ────────────────
log_info "Preparazione AppDir di build..."
rm -rf "$APPDIR_BUILD"
mkdir -p "$APPDIR_BUILD/usr/share/mg-linux-toolbox"
mkdir -p "$APPDIR_BUILD/usr/share/metainfo"
mkdir -p "$APPDIR_BUILD/usr/share/applications"

cp "$SCRIPT_DIR/AppDir/AppRun" "$APPDIR_BUILD/AppRun"
chmod +x "$APPDIR_BUILD/AppRun"
cp "$SCRIPT_DIR/AppDir/it.manganogregorio.MGLinuxToolbox.desktop" \
    "$APPDIR_BUILD/it.manganogregorio.MGLinuxToolbox.desktop"
cp "$SCRIPT_DIR/AppDir/it.manganogregorio.MGLinuxToolbox.desktop" \
    "$APPDIR_BUILD/usr/share/applications/it.manganogregorio.MGLinuxToolbox.desktop"
cp "$SCRIPT_DIR/AppDir/mg-linux-toolbox.png" "$APPDIR_BUILD/mg-linux-toolbox.png"
cp "$SCRIPT_DIR/AppDir/usr/share/metainfo/it.manganogregorio.MGLinuxToolbox.appdata.xml" \
    "$APPDIR_BUILD/usr/share/metainfo/it.manganogregorio.MGLinuxToolbox.appdata.xml"

# Only official files tracked by this repository are eligible for the
# payload. This prevents an ignored backup, report, cache or local file
# accidentally left inside a source directory from entering the AppImage.
git -C "$ROOT_DIR" ls-files -z -- main.py core backend ui assets LICENSE \
    | rsync -a --from0 --files-from=- "$ROOT_DIR/" \
        "$APPDIR_BUILD/usr/share/mg-linux-toolbox/"

log_ok "AppDir pronta: $APPDIR_BUILD"

# ── Build ──────────────────────────────────────────────────────────
mkdir -p "$DIST_DIR"
OUT_NAME="MG-Linux-Toolbox-${APP_VERSION}-${APPIMAGE_ARCH}.AppImage"
OUT_PATH="$DIST_DIR/$OUT_NAME"

log_info "Esecuzione di appimagetool..."
ARCH="$APPIMAGE_ARCH" "$APPIMAGETOOL" "$APPDIR_BUILD" "$OUT_PATH"

[ -s "$OUT_PATH" ] || fail "appimagetool ha terminato ma $OUT_PATH è vuoto o assente."

# ── Checksum ────────────────────────────────────────────────────────
( cd "$DIST_DIR" && sha256sum "$OUT_NAME" > "${OUT_NAME}.sha256" )

log_ok "Build completata:"
log_ok "  $OUT_PATH"
log_ok "  ${OUT_PATH}.sha256"
log_warn "Nessuna pubblicazione automatica — questo script si ferma qui."

rm -rf "$APPDIR_BUILD"
