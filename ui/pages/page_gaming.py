import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import InstallRow, FeatureRow, make_group, run_install_in_background, report_toggle_result
import backend.all as B
import logging
import threading

from core import gaming_readiness as gr
from core import gaming_pack as gp
from core import gaming_pack_installer as gpi
from core import gaming_pack_state as gps
from core.executor import Job

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge

logger = logging.getLogger(__name__)

# Messages for an operation that verified as fully successful on disk,
# but whose underlying transaction exited with a non-zero code (e.g. an
# unrelated repository problem) — shown as a warning, not a failure.
_WARNING_RESULT_MESSAGES = {"gaming_pack_install_done_with_warning", "gaming_pack_remove_done_with_warning"}
# Messages for an operation where only some of the requested packages
# were verified installed/removed — neither a clean success nor a
# total failure.
_PARTIAL_RESULT_MESSAGES = {"gaming_pack_install_partial", "gaming_pack_remove_partial"}


def _result_css_class(result) -> str:
    if result.friendly_message in _WARNING_RESULT_MESSAGES or result.friendly_message in _PARTIAL_RESULT_MESSAGES:
        return "status-warn"
    return "status-active" if result.ok else "desc-con"

_gaming_ds_strings = {
    "ds_gaming_header_desc": {
        "en": "Check readiness and install common gaming tools.",
        "it": "Controlla la preparazione e installa gli strumenti gaming più comuni.",
        "es": "Comprueba la preparación e instala las herramientas de juego más comunes.",
        "fr": "Vérifiez la préparation et installez les outils de jeu courants.",
    },
    "lib32_blocked_nvidia_proprietary": {
        "en": "32-bit Vulkan needs the proprietary NVIDIA driver's own 32-bit package, which lives in a repository this app never enables automatically. Add it yourself first, then this becomes available.",
        "it": "Il Vulkan a 32 bit richiede il pacchetto 32 bit del driver proprietario NVIDIA, che si trova in un repository che questa app non abilita mai automaticamente. Aggiungilo tu stesso, poi questa opzione diventerà disponibile.",
        "es": "Vulkan de 32 bits necesita el paquete de 32 bits propio del controlador propietario de NVIDIA, que está en un repositorio que esta aplicación nunca habilita automáticamente. Añádelo tú primero y esta opción estará disponible.",
        "fr": "Vulkan 32 bits nécessite le paquet 32 bits propre au pilote propriétaire NVIDIA, situé dans un dépôt que cette application n'active jamais automatiquement. Ajoutez-le vous-même, puis cette option deviendra disponible.",
    },
    "lib32_blocked_unknown_gpu": {
        "en": "The graphics card driver could not be identified, so the correct 32-bit Vulkan driver package can't be determined safely. Nothing was guessed or installed.",
        "it": "Non è stato possibile identificare il driver della scheda grafica, quindi non si può determinare in modo sicuro il pacchetto giusto del driver Vulkan a 32 bit. Non è stato indovinato né installato nulla.",
        "es": "No se pudo identificar el controlador de la tarjeta gráfica, por lo que no se puede determinar con seguridad el paquete correcto del controlador Vulkan de 32 bits. No se ha adivinado ni instalado nada.",
        "fr": "Le pilote de la carte graphique n'a pas pu être identifié ; le bon paquet de pilote Vulkan 32 bits ne peut donc pas être déterminé en toute sécurité. Rien n'a été deviné ni installé.",
    },
}
for _k, _v in _gaming_ds_strings.items():
    _i18n_mod._strings[_k] = _v

# ── Gaming Pack V1 (2026-08-03) ──────────────────────────────────────
_gaming_pack_strings = {
    "gaming_pack_title": {"en": "Gaming Pack", "it": "Gaming Pack", "es": "Gaming Pack", "fr": "Gaming Pack"},
    "gaming_pack_desc": {
        "en": "Checks which gaming components are already present and which can be installed from the configured repositories.",
        "it": "Controlla quali componenti per il gaming sono già presenti e quali possono essere installati dai repository configurati.",
        "es": "Analiza la preparación para jugar y muestra una vista previa de los paquetes que podrían ser útiles. La comprobación en sí no instala nada; tú eliges qué instalar después.",
        "fr": "Analyse la préparation au jeu et affiche un aperçu des paquets éventuellement utiles. La vérification elle-même n'installe rien ; vous choisissez ensuite quoi installer.",
    },
    "gaming_pack_pro": {
        "en": "Shows only components that are actually available for the current distribution.",
        "it": "Mostra soltanto componenti realmente disponibili per la distribuzione in uso.",
        "es": "Detecta la distribución, la GPU y el estado de los paquetes con comprobaciones de solo lectura.",
        "fr": "Détecte la distribution, le GPU et l'état des paquets avec des vérifications en lecture seule.",
    },
    "gaming_pack_con": {
        "en": "Some programs may require extra repositories. The Toolbox never enables them automatically.",
        "it": "Alcuni programmi potrebbero non essere disponibili senza repository aggiuntivi. Il Toolbox non li attiva automaticamente.",
        "es": "Solo se pueden instalar los paquetes que esta comprobación encontró ya disponibles en tus repositorios configurados, con una selección explícita.",
        "fr": "Seuls les paquets que cette vérification a trouvés déjà disponibles dans vos dépôts configurés peuvent être installés, via une sélection explicite.",
    },
    "gaming_pack_scan_btn": {"en": "Check the system", "it": "Controlla il sistema", "es": "Comprobar el sistema", "fr": "Vérifier le système"},
    "gaming_pack_scanning": {"en": "Checking…", "it": "Controllo in corso…", "es": "Comprobando…", "fr": "Vérification…"},
    "gaming_pack_scan_note": {
        "en": "Read-only check: no password, installation, removal, repository, driver, kernel or full-system update operation is performed here.",
        "it": "Controllo in sola lettura: non esegue operazioni su password, installazioni, rimozioni, repository, driver, kernel o aggiornamenti completi del sistema.",
        "es": "Esta comprobación no modifica nada y no pide contraseña.",
        "fr": "Cette vérification ne modifie rien et ne demande jamais de mot de passe.",
    },
    "gaming_pack_select_hint": {
        "en": "Tick the components you want, then install only those.",
        "it": "Seleziona i componenti desiderati, poi installa solo quelli.",
        "es": "Marca los componentes que quieres y luego instala solo esos.",
        "fr": "Cochez les composants souhaités, puis installez uniquement ceux-ci.",
    },
    "gaming_pack_install_selected_btn": {
        "en": "Install selected components", "it": "Installa i componenti selezionati",
        "es": "Instalar los componentes seleccionados", "fr": "Installer les composants sélectionnés",
    },
    "gaming_pack_installing": {"en": "Installing…", "it": "Installazione in corso…", "es": "Instalando…", "fr": "Installation en cours…"},
    "gaming_pack_install_nothing_selected": {
        "en": "No selected component is actually installable right now.",
        "it": "Nessun componente selezionato è realmente installabile in questo momento.",
        "es": "Ningún componente seleccionado se puede instalar ahora mismo.",
        "fr": "Aucun composant sélectionné n'est réellement installable pour le moment.",
    },
    "gaming_pack_install_unsupported_family": {
        "en": "Installation isn't implemented for this distribution yet.",
        "it": "L'installazione non è ancora implementata per questa distribuzione.",
        "es": "La instalación aún no está implementada para esta distribución.",
        "fr": "L'installation n'est pas encore implémentée pour cette distribution.",
    },
    "gaming_pack_install_done": {
        "en": "Selected components installed.", "it": "Componenti selezionati installati.",
        "es": "Componentes seleccionados instalados.", "fr": "Composants sélectionnés installés.",
    },
    "gaming_pack_install_done_with_warning": {
        "en": "Components installed and verified, but a repository reported a problem during the transaction — see details.",
        "it": "Componenti installati e verificati, ma un repository ha segnalato un problema durante l'operazione — vedi dettagli.",
        "es": "Componentes instalados y verificados, pero un repositorio informó un problema durante la operación — ver detalles.",
        "fr": "Composants installés et vérifiés, mais un dépôt a signalé un problème pendant l'opération — voir les détails.",
    },
    "gaming_pack_install_precheck_failed": {
        "en": "One component is no longer available in the configured repositories.",
        "it": "Un componente non è più disponibile nei repository attualmente configurati.",
        "es": "Un componente ya no está disponible en los repositorios configurados.",
        "fr": "Un composant n'est plus disponible dans les dépôts configurés.",
    },
    "gaming_pack_install_failed": {
        "en": "Installation failed.", "it": "Installazione non riuscita.",
        "es": "La instalación ha fallado.", "fr": "Échec de l'installation.",
    },
    "gaming_pack_install_partial": {
        "en": "Partial installation: some packages were verified installed, others were not — see details.",
        "it": "Installazione parziale: alcuni pacchetti risultano installati e verificati, altri no — vedi dettagli.",
        "es": "Instalación parcial: algunos paquetes se instalaron y verificaron, otros no — ver detalles.",
        "fr": "Installation partielle : certains paquets ont été installés et vérifiés, d'autres non — voir les détails.",
    },
    "gaming_pack_remove_selected_btn": {
        "en": "Remove components installed by the Toolbox", "it": "Rimuovi i componenti installati dal Toolbox",
        "es": "Eliminar componentes instalados por Toolbox", "fr": "Supprimer les composants installés par Toolbox",
    },
    "gaming_pack_remove_done": {
        "en": "Selected components removed.", "it": "Componenti selezionati rimossi.",
        "es": "Componentes seleccionados eliminados.", "fr": "Composants sélectionnés supprimés.",
    },
    "gaming_pack_remove_done_with_warning": {
        "en": "Components removed and verified, but a repository reported a problem during the transaction — see details.",
        "it": "Componenti rimossi e verificati, ma un repository ha segnalato un problema durante l'operazione — vedi dettagli.",
        "es": "Componentes eliminados y verificados, pero un repositorio informó un problema durante la operación — ver detalles.",
        "fr": "Composants supprimés et vérifiés, mais un dépôt a signalé un problème pendant l'opération — voir les détails.",
    },
    "gaming_pack_remove_failed": {
        "en": "Removal failed.", "it": "Rimozione non riuscita.",
        "es": "La eliminación ha fallado.", "fr": "Échec de la suppression.",
    },
    "gaming_pack_remove_partial": {
        "en": "Partial removal: some packages were confirmed removed, others are still installed — see details.",
        "it": "Rimozione parziale: alcuni pacchetti risultano rimossi e verificati, altri sono ancora installati — vedi dettagli.",
        "es": "Eliminación parcial: algunos paquetes se confirmaron eliminados, otros siguen instalados — ver detalles.",
        "fr": "Suppression partielle : certains paquets ont été supprimés et vérifiés, d'autres sont encore installés — voir les détails.",
    },
    "gaming_pack_remove_precheck_failed": {
        "en": "Removal was blocked because the recorded state no longer matches the system.",
        "it": "La rimozione è stata bloccata perché lo stato registrato non corrisponde più al sistema.",
        "es": "La eliminación se bloqueó porque el estado registrado ya no coincide con el sistema.",
        "fr": "La suppression a été bloquée, car l'état enregistré ne correspond plus au système.",
    },
    "gaming_pack_remove_nothing_selected": {
        "en": "No removable component is selected.", "it": "Nessun componente rimovibile è selezionato.",
        "es": "No hay ningún componente extraíble seleccionado.", "fr": "Aucun composant supprimable n'est sélectionné.",
    },
    "gaming_pack_remove_unsupported_family": {
        "en": "Removal isn't implemented for this distribution yet.", "it": "La rimozione non è ancora implementata per questa distribuzione.",
        "es": "La eliminación aún no está implementada para esta distribución.", "fr": "La suppression n'est pas encore implémentée pour cette distribution.",
    },
    "gaming_pack_install_confirm_title": {
        "en": "Install selected components", "it": "Installa i componenti selezionati",
        "es": "Instalar componentes seleccionados", "fr": "Installer les composants sélectionnés",
    },
    "gaming_pack_remove_confirm_title": {
        "en": "Remove selected components", "it": "Rimuovi i componenti selezionati",
        "es": "Eliminar componentes seleccionados", "fr": "Supprimer les composants sélectionnés",
    },
    "gaming_pack_confirm_body": {
        "en": "Components: {components}\nPackages: {packages}\n\nOnly one package-manager transaction will be started.",
        "it": "Componenti: {components}\nPacchetti: {packages}\n\nVerrà avviata una sola transazione del gestore pacchetti.",
        "es": "Componentes: {components}\nPaquetes: {packages}\n\nSolo se iniciará una transacción del gestor de paquetes.",
        "fr": "Composants: {components}\nPaquets: {packages}\n\nUne seule transaction du gestionnaire de paquets sera lancée.",
    },
    "gaming_pack_testing_note": {
        "en": "Selectable components are shown only after a live package-manager probe on the current system.",
        "it": "I componenti selezionabili vengono mostrati solo dopo una verifica reale del package manager sul sistema corrente.",
        "es": "Los componentes seleccionables solo se muestran tras una comprobación real del gestor de paquetes en el sistema actual.",
        "fr": "Les composants sélectionnables ne sont affichés qu'après une vérification réelle du gestionnaire de paquets sur le système actuel.",
    },
    "gaming_pack_gpu_blocked": {
        "en": "The graphics driver could not be verified. Package analysis remains informational and no change is made.",
        "it": "Non è stato possibile verificare il driver grafico. L'analisi dei pacchetti resta informativa e non viene effettuata alcuna modifica.",
        "es": "Antes de preparar el sistema para jugar es necesario comprobar el controlador de la tarjeta gráfica. No se ha realizado ningún cambio.",
        "fr": "Avant de préparer le système pour le jeu, il faut vérifier le pilote de la carte graphique. Aucune modification n'a été effectuée.",
    },
    "gaming_pack_state_already_installed": {"en": "Already installed", "it": "Già installato", "es": "Ya instalado", "fr": "Déjà installé"},
    "gaming_pack_state_available": {"en": "Available", "it": "Disponibile", "es": "Disponible", "fr": "Disponible"},
    "gaming_pack_state_not_available": {"en": "Not available in the currently configured repositories", "it": "Non disponibile nei repository attualmente configurati", "es": "No disponible en los repositorios configurados actualmente", "fr": "Non disponible dans les dépôts actuellement configurés"},
    "gaming_pack_state_repo_needed": {"en": "Repository needed", "it": "Repository necessario", "es": "Repositorio necesario", "fr": "Dépôt nécessaire"},
    "gaming_pack_state_not_suitable": {"en": "Not suited to this hardware", "it": "Non adatto all'hardware", "es": "No adecuado para este hardware", "fr": "Non adapté à ce matériel"},
    "gaming_pack_state_not_verifiable": {"en": "Not verifiable yet", "it": "Non verificabile", "es": "No verificable todavía", "fr": "Non vérifiable"},
    "gaming_pack_optional_tag": {"en": "optional", "it": "facoltativo", "es": "opcional", "fr": "facultatif"},
    "gaming_pack_optional_note": {
        "en": "An optional component isn't available in the configured repositories. The remaining preview is still valid.",
        "it": "Un componente facoltativo non è disponibile nei repository configurati. Il resto dell'anteprima rimane valido.",
        "es": "Un componente opcional no está disponible en los repositorios configurados. El resto del Gaming Pack se puede instalar con normalidad.",
        "fr": "Un composant facultatif n'est pas disponible dans les dépôts configurés. Le reste du Gaming Pack peut toujours être installé normalement.",
    },
    "gaming_pack_repo_reason": {
        "en": "Needs the repository: {repo} (not enabled automatically).",
        "it": "Serve il repository: {repo} (non viene abilitato automaticamente).",
        "es": "Necesita el repositorio: {repo} (no se habilita automáticamente).",
        "fr": "Nécessite le dépôt : {repo} (jamais activé automatiquement).",
    },
    "gaming_pack_lib32_explainer": {
        "en": "Some 32-bit libraries are needed because Steam and several Windows games still use them.",
        "it": "Servono alcune librerie a 32 bit perché Steam e diversi giochi Windows le utilizzano ancora.",
        "es": "Se necesitan algunas bibliotecas de 32 bits porque Steam y varios juegos de Windows todavía las usan.",
        "fr": "Certaines bibliothèques 32 bits sont nécessaires car Steam et plusieurs jeux Windows les utilisent encore.",
    },
    "gaming_pack_cancel_btn": {"en": "Cancel check", "it": "Annulla controllo", "es": "Cancelar comprobación", "fr": "Annuler la vérification"},
    "gaming_pack_scan_failed": {"en": "The analysis could not be completed.", "it": "Non è stato possibile completare l'analisi.", "es": "No se pudo completar el análisis.", "fr": "L'analyse n'a pas pu être terminée."},
    "gaming_pack_present_packages": {"en": "Present: {packages}", "it": "Presenti: {packages}", "es": "Presentes: {packages}", "fr": "Présents : {packages}"},
    "gaming_pack_suggested_packages": {"en": "Installable now: {packages}", "it": "Installabili ora: {packages}", "es": "Instalables ahora: {packages}", "fr": "Installables maintenant : {packages}"},
    "gaming_pack_unavailable_packages": {"en": "Not found in configured repositories: {packages}", "it": "Non trovati nei repository configurati: {packages}", "es": "No encontrados en los repositorios configurados: {packages}", "fr": "Introuvables dans les dépôts configurés : {packages}"},
    "gaming_pack_remove_component_btn": {"en": "Remove", "it": "Rimuovi", "es": "Eliminar", "fr": "Supprimer"},

    "gaming_pack_comp_steam": {"en": "Steam", "it": "Steam", "es": "Steam", "fr": "Steam"},
    "gaming_pack_comp_gamemode": {"en": "GameMode", "it": "GameMode", "es": "GameMode", "fr": "GameMode"},
    "gaming_pack_comp_mangohud": {"en": "MangoHud", "it": "MangoHud", "es": "MangoHud", "fr": "MangoHud"},
    "gaming_pack_comp_mangohud_32": {"en": "MangoHud (32-bit)", "it": "MangoHud (32 bit)", "es": "MangoHud (32 bits)", "fr": "MangoHud (32 bits)"},
    "gaming_pack_comp_gamescope": {"en": "Gamescope", "it": "Gamescope", "es": "Gamescope", "fr": "Gamescope"},
    "gaming_pack_comp_goverlay": {"en": "GOverlay", "it": "GOverlay", "es": "GOverlay", "fr": "GOverlay"},
    "gaming_pack_comp_lutris": {"en": "Lutris", "it": "Lutris", "es": "Lutris", "fr": "Lutris"},
    "gaming_pack_comp_protontricks": {"en": "Protontricks", "it": "Protontricks", "es": "Protontricks", "fr": "Protontricks"},
    "gaming_pack_comp_winetricks": {"en": "Winetricks", "it": "Winetricks", "es": "Winetricks", "fr": "Winetricks"},
    "gaming_pack_comp_steam_devices": {"en": "Controller support (udev rules)", "it": "Supporto controller (regole udev)", "es": "Soporte de mandos (reglas udev)", "fr": "Prise en charge manette (règles udev)"},
    "gaming_pack_comp_vulkan_64": {"en": "Vulkan (64-bit)", "it": "Vulkan (64 bit)", "es": "Vulkan (64 bits)", "fr": "Vulkan (64 bits)"},
    "gaming_pack_comp_vulkan_32": {"en": "Vulkan (32-bit)", "it": "Vulkan (32 bit)", "es": "Vulkan (32 bits)", "fr": "Vulkan (32 bits)"},
    "gaming_pack_comp_opengl_32": {"en": "OpenGL (32-bit)", "it": "OpenGL (32 bit)", "es": "OpenGL (32 bits)", "fr": "OpenGL (32 bits)"},
    "gaming_pack_comp_audio_32": {"en": "Audio (32-bit)", "it": "Audio (32 bit)", "es": "Audio (32 bits)", "fr": "Audio (32 bits)"},
    "gaming_pack_common_header": {"en": "Common package", "it": "Pacchetto comune", "es": "Paquete común", "fr": "Paquet commun"},
    "gaming_pack_extra_header": {"en": "Distribution extras", "it": "Extra della distribuzione", "es": "Extras de la distribución", "fr": "Extras de la distribution"},
    "gaming_pack_repositories": {"en": "Repositories: {packages}", "it": "Repository: {packages}", "es": "Repositorios: {packages}", "fr": "Dépôts : {packages}"},
}
for _k, _v in _gaming_pack_strings.items():
    _i18n_mod._strings[_k] = _v

_LIB32_LABEL_COMPONENTS = {"vulkan_32", "opengl_32", "audio_32"}
from core import game_mode
from core.kernel_features.device_power import list_pm_controllable_devices

_READINESS_STATE_KEYS = {
    gr.READY: "gaming_state_ready",
    gr.ALMOST_READY: "gaming_state_almost_ready",
    gr.MISSING_COMPONENTS: "gaming_state_missing_components",
    gr.UNAVAILABLE: "gaming_state_unavailable",
}


class ReadinessRow(FeatureRow):
    """Real, re-checked-on-demand summary of what's actually ready for
    gaming — never trusts installed-package status alone (see
    core/gaming_readiness.py, each check runs the real tool)."""
    def __init__(self):
        self._lines_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._lines_box.set_margin_top(6)
        super().__init__("gaming_readiness", None, risk="low")
        self.add_row(self._lines_box)
        self._refresh_list()

    def _refresh_list(self):
        child = self._lines_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._lines_box.remove(child)
            child = nxt

        items, overall = gr.full_report()
        overall_lbl = Gtk.Label(label=T(_READINESS_STATE_KEYS[overall]), xalign=0)
        overall_lbl.add_css_class("sysinfo-value-large")
        self._lines_box.append(overall_lbl)

        for item in items:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            label = Gtk.Label(label=T(item.label_key), xalign=0, hexpand=True)
            label.add_css_class("sysinfo-value")
            state_lbl = Gtk.Label(label=T(_READINESS_STATE_KEYS[item.state]), xalign=1)
            state_lbl.add_css_class("sysinfo-value-sub")
            row.append(label)
            row.append(state_lbl)
            self._lines_box.append(row)


class GameModeRow(FeatureRow):
    """
    Not a KernelFeature (it stacks several of them together), but still
    follows the same "show what will really change, never promise more
    FPS" spirit. plan() and activate()/deactivate() live in
    core/game_mode.py.
    """
    def __init__(self):
        self._status_lbl = Gtk.Label(wrap=True, xalign=0)
        self._status_lbl.add_css_class("sysinfo-value")
        self._plan_lbl = Gtk.Label(wrap=True, xalign=0)
        self._plan_lbl.add_css_class("sysinfo-value-sub")
        self._toggle_btn = Gtk.Button()
        self._toggle_btn.add_css_class("lt-action-btn")
        self._toggle_btn.connect("clicked", self._on_toggle_clicked)
        self._error_lbl = Gtk.Label(wrap=True, xalign=0)
        self._error_lbl.add_css_class("desc-con")
        self._error_lbl.set_visible(False)

        super().__init__("game_mode", None, risk="medium")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.set_margin_top(6)
        body.append(self._status_lbl)
        body.append(self._plan_lbl)
        body.append(self._toggle_btn)
        body.append(self._error_lbl)
        self.add_row(body)
        self._refresh_view()

    def _refresh_view(self):
        active = game_mode.is_active()
        self._status_lbl.set_text(T("game_mode_active_status") if active else T("game_mode_inactive_status"))
        if active:
            self._toggle_btn.set_label(T("game_mode_deactivate_btn"))
            self._plan_lbl.set_visible(False)
        else:
            self._toggle_btn.set_label(T("game_mode_activate_btn"))
            changes = game_mode.plan()
            if changes:
                self._plan_lbl.set_text(T("game_mode_changes_count").format(n=len(changes)))
            else:
                self._plan_lbl.set_text(T("game_mode_no_changes"))
            self._plan_lbl.set_visible(True)

    def _on_toggle_clicked(self, _btn):
        self._toggle_btn.set_sensitive(False)
        self._error_lbl.set_visible(False)
        going_active = not game_mode.is_active()

        def run():
            if going_active:
                changes = game_mode.plan()
                ok, failed = game_mode.activate(changes) if changes else (True, None)
            else:
                ok, failed = game_mode.deactivate(), None
            GLib.idle_add(self._on_toggle_done, ok, failed)

        threading.Thread(target=run, daemon=True).start()

    def _on_toggle_done(self, ok, failed):
        self._toggle_btn.set_sensitive(True)
        if not ok:
            self._error_lbl.set_text(T("game_mode_failed"))
            self._error_lbl.set_visible(True)
        self._refresh_view()
        return False


class ControllerRow(FeatureRow):
    """Reuses the same device_power 'control' mechanism as the Energia e
    batteria peripheral-power row, filtered to gamepad-category devices
    only, with a beginner-facing 'keep active' framing."""
    def __init__(self):
        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._list_box.set_margin_top(6)
        super().__init__("controller_keep_active", None, risk="low")
        self.add_row(self._list_box)
        self._refresh_list()

    def _refresh_list(self):
        child = self._list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._list_box.remove(child)
            child = nxt

        controllers = [d for d in list_pm_controllable_devices() if d["category"] == "gamepad"]
        if not controllers:
            lbl = Gtk.Label(label=T("controller_none_found"), xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            self._list_box.append(lbl)
            return

        for dev in controllers:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=dev["label"], xalign=0, hexpand=True, wrap=True)
            lbl.add_css_class("sysinfo-value")
            switch = Gtk.Switch(active=not dev["auto"], valign=Gtk.Align.CENTER)  # "keep active" = NOT auto-suspend
            switch.connect("notify::active", self._on_toggle, dev["bus"], dev["device_id"])
            row.append(lbl)
            row.append(switch)
            self._list_box.append(row)

    def _on_toggle(self, switch, _pspec, bus, device_id):
        switch.set_sensitive(False)
        keep_active = switch.get_active()
        setting = "on" if keep_active else "auto"

        def run():
            from core.persistence.priv_client import default_privileged_writer
            writer = default_privileged_writer()
            result = writer.execute("device_power", "apply_temporary", f"{bus}:{device_id}:{setting}")
            GLib.idle_add(self._on_toggle_done, switch, result, keep_active)

        threading.Thread(target=run, daemon=True).start()

    def _on_toggle_done(self, switch, result, want_active):
        switch.set_sensitive(True)
        if not result.ok:
            switch.handler_block_by_func(self._on_toggle)
            switch.set_active(not want_active)
            switch.handler_unblock_by_func(self._on_toggle)
            # No expandable row here to host a full error+details area
            # (this is a compact per-device list row); the friendly
            # message — already correctly distinguishing a missing
            # administrative component from a plain write failure,
            # since it comes straight from the real helper OpResult —
            # is still surfaced via tooltip instead of being silent.
            # History is already recorded by PrivilegedWriter.execute()
            # itself for every call, success or failure.
            switch.set_tooltip_text(T(result.friendly_message or "kf_err_generic"))
        else:
            switch.set_tooltip_text("")
        return False


_STATE_LABEL_KEYS = {
    gp.ALREADY_INSTALLED: "gaming_pack_state_already_installed",
    gp.AVAILABLE: "gaming_pack_state_available",
    gp.NOT_AVAILABLE: "gaming_pack_state_not_available",
    gp.REPO_NEEDED: "gaming_pack_state_repo_needed",
    gp.NOT_SUITABLE: "gaming_pack_state_not_suitable",
    gp.NOT_VERIFIABLE: "gaming_pack_state_not_verifiable",
}
_STATE_CSS = {
    gp.ALREADY_INSTALLED: "status-active",
    gp.AVAILABLE: "badge-low",
    gp.NOT_AVAILABLE: "desc-con",
    gp.REPO_NEEDED: "badge-medium",
    gp.NOT_SUITABLE: "desc-con",
    gp.NOT_VERIFIABLE: "sysinfo-value-sub",
}


class GamingPackRow(FeatureRow):
    """Read-only Gaming Pack V1 analysis and package preview."""

    def __init__(self):
        self._last_profile = None
        self._last_previews = []
        self._scan_running = False
        self._scan_generation = 0
        self._scan_cancel_event = None
        self._scan_job = None
        self._install_running = False
        self._install_job = None
        self._remove_running = False
        self._selected_ids = set()
        self._removable_ids = set()
        self._destroyed = False

        self._scan_btn = Gtk.Button(label=T("gaming_pack_scan_btn"))
        self._scan_btn.add_css_class("lt-action-btn")
        self._scan_btn.connect("clicked", self._on_scan_button_clicked)

        self._scan_note = Gtk.Label(label=T("gaming_pack_scan_note"), xalign=0, wrap=True)
        self._scan_note.add_css_class("sysinfo-value-sub")

        self._testing_note = Gtk.Label(label=T("gaming_pack_testing_note"), xalign=0, wrap=True)
        self._testing_note.add_css_class("desc-what")

        self._blocked_lbl = Gtk.Label(wrap=True, xalign=0)
        self._blocked_lbl.add_css_class("desc-con")
        self._blocked_lbl.set_visible(False)

        self._system_lbl = Gtk.Label(wrap=True, xalign=0)
        self._system_lbl.add_css_class("sysinfo-value-sub")
        self._system_lbl.set_visible(False)

        self._select_hint_lbl = Gtk.Label(label=T("gaming_pack_select_hint"), xalign=0, wrap=True)
        self._select_hint_lbl.add_css_class("sysinfo-value-sub")
        self._select_hint_lbl.set_visible(False)

        self._results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self._install_btn = Gtk.Button(label=T("gaming_pack_install_selected_btn"))
        self._install_btn.add_css_class("lt-action-btn")
        self._install_btn.set_visible(False)
        self._install_btn.set_sensitive(False)
        self._install_btn.connect("clicked", self._on_install_selected_clicked)
        self._remove_btn = Gtk.Button(label=T("gaming_pack_remove_selected_btn"))
        self._remove_btn.add_css_class("destructive-action")
        self._remove_btn.set_visible(False)
        self._remove_btn.set_sensitive(False)
        self._remove_btn.connect("clicked", self._on_remove_selected_clicked)

        self._result_lbl = Gtk.Label(wrap=True, xalign=0)
        self._result_lbl.add_css_class("desc-con")
        self._result_lbl.set_visible(False)
        self._detail_lbl = Gtk.Label(wrap=True, xalign=0, selectable=True)
        self._detail_lbl.add_css_class("sysinfo-value-sub")
        self._detail_lbl.set_visible(False)

        super().__init__("gaming_pack", None, risk="low")
        self.connect("destroy", self._on_destroy)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(6)
        body.append(self._scan_btn)
        body.append(self._scan_note)
        body.append(self._testing_note)
        body.append(self._blocked_lbl)
        body.append(self._system_lbl)
        body.append(self._select_hint_lbl)
        body.append(self._results_box)
        body.append(self._install_btn)
        body.append(self._remove_btn)
        body.append(self._result_lbl)
        body.append(self._detail_lbl)

        self.add_row(body)

    # ── Scan ─────────────────────────────────────────────────────
    def _on_scan_button_clicked(self, _btn):
        if self._scan_running:
            self._scan_cancel_event.set()
            if self._scan_job is not None:
                self._scan_job.cancel()
            return

        self._scan_running = True
        self._scan_generation += 1
        generation = self._scan_generation
        self._scan_cancel_event = threading.Event()
        self._scan_job = Job()
        self._scan_btn.set_label(T("gaming_pack_cancel_btn"))
        self._result_lbl.set_visible(False)
        self._blocked_lbl.set_visible(False)
        self._detail_lbl.set_visible(False)

        def run():
            try:
                profile = gp.detect_system()
                previews = gp.scan(
                    profile,
                    cancel_check=self._scan_cancel_event.is_set,
                    job=self._scan_job,
                )
                error = None
            except Exception as exc:
                profile = None
                previews = []
                error = exc
                logger.warning("Gaming Pack analysis failed", exc_info=True)
            GLib.idle_add(
                self._on_scan_done, generation, profile, previews,
                self._scan_cancel_event.is_set(), error,
            )

        threading.Thread(target=run, name="mg-gaming-pack-scan", daemon=True).start()

    def _on_destroy(self, _widget):
        self._destroyed = True
        self._scan_generation += 1
        if self._scan_cancel_event is not None:
            self._scan_cancel_event.set()
        if self._scan_job is not None:
            self._scan_job.cancel()
        if self._install_job is not None:
            self._install_job.cancel()

    def _reset_scan_button(self):
        self._scan_running = False
        self._scan_job = None
        self._scan_btn.set_label(T("gaming_pack_scan_btn"))

    def _on_scan_done(self, generation, profile, previews, was_cancelled=False, error=None):
        if self._destroyed or generation != self._scan_generation:
            return False
        self._reset_scan_button()
        if error is not None:
            self._result_lbl.set_text(T("gaming_pack_scan_failed"))
            self._result_lbl.set_visible(True)
            return False
        if was_cancelled:
            return False

        self._last_profile = profile
        self._last_previews = previews
        self._selected_ids = set()
        self._removable_ids = gpi.removable_component_ids(profile, previews)

        self._system_lbl.set_text(
            f"{profile.distro_pretty_name} — {profile.package_manager} — "
            f"{profile.architecture} — GPU: {profile.gpu_driver or '—'}"
        )
        self._system_lbl.set_visible(True)

        blocked = gp.gpu_driver_unverified(profile)
        self._blocked_lbl.set_text(T("gaming_pack_gpu_blocked"))
        self._blocked_lbl.set_visible(blocked)

        _clear_box(self._results_box)
        has_optional_gap = False
        any_installable = False
        common_previews = [p for p in previews if p.common]
        extra_previews = [p for p in previews if not p.common]

        if common_previews:
            self._results_box.append(self._section_label("gaming_pack_common_header"))
        for preview in common_previews:
            self._results_box.append(self._preview_row(preview))
            if preview.optional and preview.state in (gp.NOT_AVAILABLE, gp.NOT_VERIFIABLE):
                has_optional_gap = True
            if preview.state == gp.AVAILABLE and preview.suggested_packages:
                any_installable = True
        if extra_previews:
            self._results_box.append(self._section_label("gaming_pack_extra_header"))
        for preview in extra_previews:
            self._results_box.append(self._preview_row(preview))
            if preview.optional and preview.state in (gp.NOT_AVAILABLE, gp.NOT_VERIFIABLE):
                has_optional_gap = True
            if preview.state == gp.AVAILABLE and preview.suggested_packages:
                any_installable = True

        if has_optional_gap:
            note = Gtk.Label(label=T("gaming_pack_optional_note"), xalign=0, wrap=True)
            note.add_css_class("sysinfo-value-sub")
            self._results_box.append(note)

        self._select_hint_lbl.set_visible(any_installable)
        self._install_btn.set_visible(any_installable)
        self._install_btn.set_sensitive(False)
        self._remove_btn.set_visible(bool(self._removable_ids))
        self._remove_btn.set_sensitive(False)

        return False

    def _section_label(self, key):
        lbl = Gtk.Label(label=T(key), xalign=0, wrap=True)
        lbl.add_css_class("sysinfo-value")
        return lbl

    def _preview_row(self, preview) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        if preview.state == gp.AVAILABLE and preview.suggested_packages:
            check = Gtk.CheckButton()
            check.connect("toggled", self._on_component_toggled, preview.component_id)
            row.append(check)

        name_key = f"gaming_pack_comp_{preview.component_id}"
        label_text = T(name_key)
        if preview.optional:
            label_text += f" ({T('gaming_pack_optional_tag')})"
        name_lbl = Gtk.Label(label=label_text, xalign=0, hexpand=True, wrap=True)
        row.append(name_lbl)

        state_lbl = Gtk.Label(label=T(_STATE_LABEL_KEYS.get(preview.state, preview.state)), xalign=1)
        state_lbl.add_css_class(_STATE_CSS.get(preview.state, "sysinfo-value-sub"))
        row.append(state_lbl)
        if preview.component_id in self._removable_ids:
            remove_btn = Gtk.Button(label=T("gaming_pack_remove_component_btn"))
            remove_btn.add_css_class("destructive-action")
            remove_btn.connect("clicked", self._on_remove_single_clicked, preview.component_id)
            row.append(remove_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(row)

        if preview.state == gp.REPO_NEEDED and preview.repo_hint:
            reason = Gtk.Label(label=T("gaming_pack_repo_reason").format(repo=preview.repo_hint),
                                xalign=0, wrap=True)
            reason.add_css_class("sysinfo-value-sub")
            box.append(reason)
        elif preview.component_id in _LIB32_LABEL_COMPONENTS and preview.state != gp.NOT_VERIFIABLE:
            explainer = Gtk.Label(label=T("gaming_pack_lib32_explainer"), xalign=0, wrap=True)
            explainer.add_css_class("sysinfo-value-sub")
            box.append(explainer)

        package_details = (
            (preview.installed_packages, "gaming_pack_present_packages"),
            (preview.suggested_packages, "gaming_pack_suggested_packages"),
            (preview.unavailable_packages, "gaming_pack_unavailable_packages"),
            (preview.repositories, "gaming_pack_repositories"),
        )
        for packages, key in package_details:
            if packages:
                label = Gtk.Label(
                    label=T(key).format(packages=", ".join(packages)),
                    xalign=0, wrap=True,
                )
                label.add_css_class("sysinfo-value-sub")
                box.append(label)

        return box

    # ── Selective install ───────────────────────────────────────────
    def _on_component_toggled(self, check, component_id):
        if check.get_active():
            self._selected_ids.add(component_id)
        else:
            self._selected_ids.discard(component_id)
        self._install_btn.set_sensitive(bool(self._selected_ids) and not self._install_running)
        self._remove_btn.set_sensitive(bool(self._selected_ids & self._removable_ids) and not self._remove_running)

    def _on_install_selected_clicked(self, _btn):
        if self._install_running or not self._selected_ids:
            return
        self._confirm_operation(
            "gaming_pack_install_confirm_title",
            list(self._selected_ids),
            lambda: self._run_install(list(self._selected_ids)),
        )

    def _run_install(self, component_ids):
        self._install_running = True
        self._install_btn.set_sensitive(False)
        self._remove_btn.set_sensitive(False)
        self._install_btn.set_label(T("gaming_pack_installing"))
        self._result_lbl.set_visible(False)
        self._detail_lbl.set_visible(False)

        previews = self._last_previews
        self._install_job = Job()

        def run():
            try:
                result = gpi.install_selected(component_ids, self._last_profile, previews, job=self._install_job)
            except Exception as exc:
                logger.warning("Gaming Pack install failed", exc_info=True)
                result = gpi.InstallSelectionResult(False, friendly_message="gaming_pack_install_failed",
                                                     technical_detail=str(exc))
            GLib.idle_add(self._on_install_done, result)

        threading.Thread(target=run, name="mg-gaming-pack-install", daemon=True).start()

    def _on_install_done(self, result):
        self._install_running = False
        self._install_job = None
        self._install_btn.set_label(T("gaming_pack_install_selected_btn"))
        self._result_lbl.set_text(T(result.friendly_message) if result.friendly_message else "")
        self._result_lbl.remove_css_class("desc-con")
        self._result_lbl.remove_css_class("status-active")
        self._result_lbl.remove_css_class("status-warn")
        self._result_lbl.add_css_class(_result_css_class(result))
        self._result_lbl.set_visible(bool(result.friendly_message))
        self._detail_lbl.set_text(result.technical_detail or "")
        self._detail_lbl.set_visible(bool(result.technical_detail))
        # Re-scan whenever something really landed on disk — including a
        # partial install — so installed/available state reflects
        # reality instead of the stale pre-install preview.
        should_rescan = result.ok or result.friendly_message == "gaming_pack_install_partial"
        if should_rescan and not self._destroyed:
            self._on_scan_button_clicked(None)
        else:
            self._install_btn.set_sensitive(bool(self._selected_ids))
            self._remove_btn.set_sensitive(bool(self._selected_ids & self._removable_ids))
        return False

    def _on_remove_single_clicked(self, _btn, component_id):
        self._confirm_operation(
            "gaming_pack_remove_confirm_title",
            [component_id],
            lambda: self._run_remove([component_id]),
        )

    def _on_remove_selected_clicked(self, _btn):
        removable_selection = sorted(self._selected_ids & self._removable_ids)
        if not removable_selection:
            return
        self._confirm_operation(
            "gaming_pack_remove_confirm_title",
            removable_selection,
            lambda: self._run_remove(removable_selection),
        )

    def _confirm_operation(self, title_key, component_ids, on_confirm):
        component_names = []
        packages = []
        for preview in self._last_previews:
            if preview.component_id not in component_ids:
                continue
            component_names.append(T(f"gaming_pack_comp_{preview.component_id}"))
            record = gps.get_record(preview.component_id)
            if record and record.get("installed_packages"):
                packages.extend(record["installed_packages"])
            else:
                packages.extend(preview.suggested_packages)
        dialog = Adw.MessageDialog(transient_for=self.get_root(), heading=T(title_key))
        dialog.set_body(T("gaming_pack_confirm_body").format(
            components=", ".join(component_names) or "—",
            packages=", ".join(sorted(dict.fromkeys(packages))) or "—",
        ))
        dialog.add_response("cancel", T("sr_cancel_btn"))
        dialog.add_response("confirm", T("sr_confirm_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _d, response: on_confirm() if response == "confirm" else None)
        dialog.present()

    def _run_remove(self, component_ids):
        self._remove_running = True
        self._install_btn.set_sensitive(False)
        self._remove_btn.set_sensitive(False)
        self._result_lbl.set_visible(False)
        self._detail_lbl.set_visible(False)
        self._install_job = Job()

        def run():
            try:
                result = gpi.remove_selected(component_ids, self._last_profile, self._last_previews, job=self._install_job)
            except Exception as exc:
                logger.warning("Gaming Pack remove failed", exc_info=True)
                result = gpi.InstallSelectionResult(False, friendly_message="gaming_pack_remove_failed",
                                                     technical_detail=str(exc))
            GLib.idle_add(self._on_remove_done, result)

        threading.Thread(target=run, name="mg-gaming-pack-remove", daemon=True).start()

    def _on_remove_done(self, result):
        self._remove_running = False
        self._install_job = None
        self._result_lbl.set_text(T(result.friendly_message) if result.friendly_message else "")
        self._result_lbl.remove_css_class("desc-con")
        self._result_lbl.remove_css_class("status-active")
        self._result_lbl.remove_css_class("status-warn")
        self._result_lbl.add_css_class(_result_css_class(result))
        self._result_lbl.set_visible(bool(result.friendly_message))
        self._detail_lbl.set_text(result.technical_detail or "")
        self._detail_lbl.set_visible(bool(result.technical_detail))
        should_rescan = result.ok or result.friendly_message == "gaming_pack_remove_partial"
        if should_rescan and not self._destroyed:
            self._on_scan_button_clicked(None)
        else:
            self._install_btn.set_sensitive(bool(self._selected_ids))
            self._remove_btn.set_sensitive(bool(self._selected_ids & self._removable_ids))
        return False

def _clear_box(box: Gtk.Box):
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


class GamingPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("input-gaming-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        header = PageHeader(
            "input-gaming-symbolic", T("tab_gaming"), T("ds_gaming_header_desc"),
            category="neutral",
        )
        self.add(wrap_in_preferences_group(header))

        g0 = make_group("gaming_readiness_title")
        self.add(g0)
        for row in (ReadinessRow(), GameModeRow(), ControllerRow()):
            row.add_prefix(IconBadge("input-gaming-symbolic", category="neutral"))
            g0.add(row)

        g1 = make_group("grp_gaming_install")
        self.add(g1)

        self.gamemode = InstallRow("gamemode", B.gamemode_installed(), risk="low",
                                   dep_pkg="gamemode",
                                   dep_check=B.gamemode_installed,
                                   dep_install=B.gamemode_install)
        self.gamemode.button.connect("clicked", self._on_gamemode)
        self.gamemode.add_prefix(IconBadge("input-gaming-symbolic", category="neutral"))
        self._add_try_button(self.gamemode, T("gaming_try_gamemode_btn"), self._on_try_gamemode)
        g1.add(self.gamemode)

        self.mango = InstallRow("mango", B.mangohud_installed(), risk="low",
                                dep_pkg="mangohud",
                                dep_check=B.mangohud_installed,
                                dep_install=B.mangohud_install)
        self.mango.button.connect("clicked", self._on_mango)
        self.mango.add_prefix(IconBadge("input-gaming-symbolic", category="neutral"))
        self._add_try_button(self.mango, T("gaming_try_mangohud_btn"), self._on_try_mangohud)
        g1.add(self.mango)

        lib32_blocked_reason = B.lib32_blocked_reason()
        self.lib32 = InstallRow("lib32", B.lib32_installed(), risk="low",
                                dep_pkg="lib32 (mesa:i386)",
                                dep_check=B.lib32_installed,
                                dep_install=B.lib32_install,
                                available=B.lib32_supported() and not lib32_blocked_reason)
        self.lib32.button.connect("clicked", self._on_lib32)
        self.lib32.add_prefix(IconBadge("input-gaming-symbolic", category="neutral"))
        if lib32_blocked_reason:
            # "Not available" alone doesn't explain WHY — this app never
            # guesses a GPU-specific package, so the user needs the real
            # reason (unrecognized GPU / NVIDIA proprietary needs its own
            # repository) instead of a dead end.
            reason_lbl = Gtk.Label(label=T(lib32_blocked_reason), xalign=0, wrap=True)
            reason_lbl.add_css_class("desc-con")
            self.lib32.add_row(reason_lbl)
        g1.add(self.lib32)

        self.vulkan = InstallRow("vulkan", B.vulkan_installed(), risk="low",
                                 dep_pkg="vulkan-tools",
                                 dep_check=lambda: B._cmd_exists("vulkaninfo"),
                                 dep_install=B.vulkan_install)
        self.vulkan.button.connect("clicked", self._on_vulkan)
        self.vulkan.add_prefix(IconBadge("input-gaming-symbolic", category="neutral"))
        g1.add(self.vulkan)

        g2 = make_group("gaming_pack_title")
        self.add(g2)
        self.gaming_pack = GamingPackRow()
        self.gaming_pack.add_prefix(IconBadge("input-gaming-symbolic", category="neutral"))
        g2.add(self.gaming_pack)

    def _add_try_button(self, install_row, label, handler):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(4)
        btn = Gtk.Button(label=label)
        result_lbl = Gtk.Label(xalign=0, wrap=True)
        row.append(btn)
        row.append(result_lbl)
        install_row.add_row(row)
        btn.connect("clicked", handler, btn, result_lbl)

    def _run_try(self, status_fn, btn, result_lbl):
        btn.set_sensitive(False)
        result_lbl.set_text("")

        def run():
            status = status_fn()
            GLib.idle_add(self._on_try_done, btn, result_lbl, status)

        threading.Thread(target=run, daemon=True).start()

    def _on_try_done(self, btn, result_lbl, status):
        btn.set_sensitive(True)
        text = {"ready": T("gaming_try_success"), "installed_not_ready": T("gaming_try_failed"),
                "not_installed": T("gaming_try_failed")}.get(status, "")
        result_lbl.remove_css_class("desc-con")
        result_lbl.remove_css_class("status-active")
        result_lbl.add_css_class("status-active" if status == "ready" else "desc-con")
        result_lbl.set_text(text)
        return False

    def _on_try_gamemode(self, _btn, btn, result_lbl):
        self._run_try(gr.gamemode_real_status, btn, result_lbl)

    def _on_try_mangohud(self, _btn, btn, result_lbl):
        self._run_try(gr.mangohud_real_status, btn, result_lbl)

    def _refresh_title(self):
        self.set_title(T("tab_gaming"))

    def _on_gamemode(self, _):
        run_install_in_background(self.gamemode.button, B.gamemode_install,
                                   B.gamemode_installed, self.gamemode.mark_installed,
                                   on_failure=lambda: report_toggle_result(self.gamemode, "gaming", "gaming.gamemode_install", False))

    def _on_mango(self, _):
        run_install_in_background(self.mango.button, B.mangohud_install,
                                   B.mangohud_installed, self.mango.mark_installed,
                                   on_failure=lambda: report_toggle_result(self.mango, "gaming", "gaming.mangohud_install", False))

    def _on_lib32(self, _):
        run_install_in_background(self.lib32.button, B.lib32_install,
                                   B.lib32_installed, self.lib32.mark_installed,
                                   on_failure=lambda: report_toggle_result(self.lib32, "gaming", "gaming.lib32_install", False))

    def _on_vulkan(self, _):
        run_install_in_background(self.vulkan.button, B.vulkan_install,
                                   B.vulkan_installed, self.vulkan.mark_installed,
                                   on_failure=lambda: report_toggle_result(self.vulkan, "gaming", "gaming.vulkan_install", False))
