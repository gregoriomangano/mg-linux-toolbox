#!/usr/bin/env bash
# M.G Linux Toolbox — simple uninstaller.
#
# Removes only what install.sh itself created:
#   ~/.local/opt/mg-linux-toolbox/   (the installed AppImage + its backup)
#   ~/.local/bin/mg-linux-toolbox    (the launcher command)
#   ~/.local/share/applications/mg-linux-toolbox.desktop
#   ~/.local/share/icons/hicolor/256x256/apps/mg-linux-toolbox.png
#
# Never removes system dependencies (Python/GTK4/libadwaita/FUSE — other
# programs may need them), and never removes the app's own personal data
# (history, checkpoints, settings under ~/.local/share/mg-linux-toolbox
# and ~/.local/state/mg-linux-toolbox) unless --purge is given AND the
# user explicitly confirms.
set -euo pipefail

INSTALL_DIR="$HOME/.local/opt/mg-linux-toolbox"
BIN_PATH="$HOME/.local/bin/mg-linux-toolbox"

# Root-owned files installed by install.sh's "componente amministrativo"
# step — ONLY these exact paths are ever removed with sudo, and only if
# they exist. Nothing belonging to other programs is ever touched.
HELPER_CANDIDATE_PATHS=(
    "/usr/libexec/mg-linux-toolbox/mg-privileged-helper"
    "/usr/lib/mg-linux-toolbox/mg-privileged-helper"
)
POLKIT_POLICY_PATH="/usr/share/polkit-1/actions/it.manganogregorio.mg-linux-toolbox.policy"
XDG_DATA_HOME_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
XDG_STATE_HOME_DIR="${XDG_STATE_HOME:-$HOME/.local/state}"
DESKTOP_PATH="$XDG_DATA_HOME_DIR/applications/mg-linux-toolbox.desktop"
ICON_PATH="$XDG_DATA_HOME_DIR/icons/hicolor/256x256/apps/mg-linux-toolbox.png"

# Real personal data written by the running app (never touched unless
# --purge + explicit confirmation) — same paths the app itself uses,
# see core/persistence/history_store.py and core/game_mode.py.
DATA_DIR="$XDG_DATA_HOME_DIR/mg-linux-toolbox"
STATE_DIR="$XDG_STATE_HOME_DIR/mg-linux-toolbox"

say() { printf '%s\n' "$1"; }
fail() { printf 'Errore: %s\n' "$1" >&2; exit 1; }

PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        --help|-h)
            cat <<'HELP'
M.G Linux Toolbox — disinstaller

Uso:
  uninstall.sh            Rimuove il programma (mai i tuoi dati personali)
  uninstall.sh --purge    Rimuove anche i dati personali dell'app, dopo conferma
  uninstall.sh --help     Mostra questo messaggio
HELP
            exit 0
            ;;
        *) fail "Opzione non riconosciuta: $arg (usa --help per l'elenco)" ;;
    esac
done

if [ "$(id -u)" -eq 0 ]; then
    fail "Non eseguire questo script come root o con sudo."
fi

app_installed() {
    [ -d "$INSTALL_DIR" ] || [ -f "$BIN_PATH" ] || [ -f "$DESKTOP_PATH" ]
}

personal_data_exists() {
    [ -d "$DATA_DIR" ] || [ -d "$STATE_DIR" ]
}

# Nothing at all to do only if the app itself is gone AND (we weren't
# asked to purge OR there's no personal data left either) — running
# "uninstall.sh --purge" a second time, after a plain uninstall already
# removed the app, must still be able to reach any personal data left
# behind, not just exit early claiming "nothing installed".
if ! app_installed && { [ "$PURGE" -eq 0 ] || ! personal_data_exists; }; then
    say "M.G Linux Toolbox non risulta installato con il metodo automatico. Nulla da rimuovere."
    exit 0
fi

if app_installed; then
    say "Verranno rimossi:"
    [ -d "$INSTALL_DIR" ] && say "  - $INSTALL_DIR"
    [ -f "$BIN_PATH" ] && say "  - $BIN_PATH"
    [ -f "$DESKTOP_PATH" ] && say "  - $DESKTOP_PATH"
    [ -f "$ICON_PATH" ] && say "  - $ICON_PATH"

    rm -rf -- "$INSTALL_DIR"
    rm -f -- "$BIN_PATH"
    rm -f -- "$DESKTOP_PATH"
    rm -f -- "$ICON_PATH"

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$XDG_DATA_HOME_DIR/applications" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -d "$XDG_DATA_HOME_DIR/icons/hicolor" ]; then
        gtk-update-icon-cache -f -t "$XDG_DATA_HOME_DIR/icons/hicolor" >/dev/null 2>&1 || true
    fi

    say "M.G Linux Toolbox è stata rimossa."
    say "Le dipendenze di sistema (Python, GTK4, libadwaita, FUSE) non sono state toccate:"
    say "potrebbero servire ad altri programmi."

    # ── Root-owned privileged component (helper + Polkit policy) ──────
    ROOT_FILES_TO_REMOVE=()
    for candidate in "${HELPER_CANDIDATE_PATHS[@]}"; do
        # Never follow an unexpected symlink: only a regular file at the
        # exact known path is ever considered ours.
        [ -f "$candidate" ] && [ ! -L "$candidate" ] && ROOT_FILES_TO_REMOVE+=("$candidate")
    done
    [ -f "$POLKIT_POLICY_PATH" ] && [ ! -L "$POLKIT_POLICY_PATH" ] && ROOT_FILES_TO_REMOVE+=("$POLKIT_POLICY_PATH")

    if [ "${#ROOT_FILES_TO_REMOVE[@]}" -gt 0 ]; then
        say ""
        say "Il componente amministrativo installato da M.G Linux Toolbox verrà rimosso"
        say "con la tua password. Verranno eliminati SOLTANTO questi file di sistema:"
        for f in "${ROOT_FILES_TO_REMOVE[@]}"; do
            say "  - $f"
        done
        if sudo rm -f -- "${ROOT_FILES_TO_REMOVE[@]}"; then
            for d in /usr/libexec/mg-linux-toolbox /usr/lib/mg-linux-toolbox; do
                sudo rmdir --ignore-fail-on-non-empty "$d" 2>/dev/null || true
            done
            say "Componente amministrativo rimosso."
        else
            say "Attenzione: il componente amministrativo non è stato rimosso (password annullata?)."
            say "Puoi rimuoverlo in seguito rieseguendo questo script."
        fi
    fi
else
    say "M.G Linux Toolbox non risulta installata con il metodo automatico (nessun file del programma da rimuovere)."
fi

if [ "$PURGE" -eq 0 ]; then
    if personal_data_exists; then
        say ""
        say "I tuoi dati personali (cronologia, punti di ripristino, impostazioni) sono"
        say "stati conservati. Per rimuoverli esegui: uninstall.sh --purge"
    fi
    exit 0
fi

# ── --purge: only ever this app's own two known data directories ──────
say ""
if ! personal_data_exists; then
    say "Non ci sono dati personali da eliminare."
    exit 0
fi

say "--purge eliminerà anche questi dati personali, in modo permanente:"
[ -d "$DATA_DIR" ] && say "  - $DATA_DIR (cronologia, punti di ripristino)"
[ -d "$STATE_DIR" ] && say "  - $STATE_DIR (impostazioni salvate)"
say ""
printf 'Scrivi ESATTAMENTE "elimina" per confermare, o premi Invio per annullare: '
read -r CONFIRMATION

if [ "$CONFIRMATION" != "elimina" ]; then
    say "Eliminazione annullata. Nessun dato personale è stato toccato."
    exit 0
fi

# Defense in depth: never remove anything unless the path is exactly
# one of the two known, non-empty, expected directories under $HOME —
# never act on an empty variable or an unexpected path.
purge_dir() {
    local dir="$1"
    case "$dir" in
        "$DATA_DIR"|"$STATE_DIR")
            [ -n "$dir" ] && [ -d "$dir" ] && rm -rf -- "$dir"
            ;;
        *)
            fail "percorso inatteso, eliminazione rifiutata per sicurezza: $dir"
            ;;
    esac
}

[ -d "$DATA_DIR" ] && purge_dir "$DATA_DIR"
[ -d "$STATE_DIR" ] && purge_dir "$STATE_DIR"

say "Dati personali eliminati."
