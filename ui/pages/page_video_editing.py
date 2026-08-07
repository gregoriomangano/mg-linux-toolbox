import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import FeatureRow, make_group
import logging
import threading

from core import video_editing_pack as vep
from core.executor import Job
from core.package_pack_installer import PackInstaller, InstallSelectionResult

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge

logger = logging.getLogger(__name__)

video_pack_installer = PackInstaller(vep, "video_editing_pack", "video_pack")

# ── Video Editing Pack V1 (2026-08-07) ──────────────────────────────
_video_editing_strings = {
    "tab_video_editing": {
        "en": "Video Editing", "it": "Video editing", "es": "Edición de vídeo", "fr": "Montage vidéo",
    },
    "ds_video_editing_header_desc": {
        "en": "Check readiness for video editing, screen recording and encoding.",
        "it": "Controlla la preparazione per editing video, registrazione schermo ed encoding.",
        "es": "Comprueba la preparación para edición de vídeo, grabación de pantalla y codificación.",
        "fr": "Vérifiez la préparation pour le montage vidéo, l'enregistrement d'écran et l'encodage.",
    },
    "video_pack_title": {
        "en": "Video Editing Pack", "it": "Video Editing Pack",
        "es": "Video Editing Pack", "fr": "Video Editing Pack",
    },
    "video_pack_desc": {
        "en": "Checks which video editing, recording and encoding components are already present and which can be installed from the configured repositories.",
        "it": "Controlla quali componenti per l'editing video, la registrazione e l'encoding sono già presenti e quali possono essere installati dai repository configurati.",
        "es": "Comprueba qué componentes de edición de vídeo, grabación y codificación ya están presentes y cuáles se pueden instalar desde los repositorios configurados.",
        "fr": "Vérifie quels composants de montage vidéo, d'enregistrement et d'encodage sont déjà présents et lesquels peuvent être installés depuis les dépôts configurés.",
    },
    "video_pack_pro": {
        "en": "Shows only components that are actually available for the current distribution, plus the real hardware encoders your ffmpeg supports.",
        "it": "Mostra soltanto componenti realmente disponibili per la distribuzione in uso, più gli accelleratori hardware realmente supportati dal tuo ffmpeg.",
        "es": "Muestra solo componentes realmente disponibles para la distribución actual, además de los aceleradores de hardware que realmente admite tu ffmpeg.",
        "fr": "Affiche uniquement les composants réellement disponibles pour la distribution actuelle, ainsi que les accélérateurs matériels réellement pris en charge par votre ffmpeg.",
    },
    "video_pack_con": {
        "en": "Some programs may require extra repositories (e.g. RPM Fusion, Packman). The Toolbox never enables them automatically.",
        "it": "Alcuni programmi potrebbero richiedere repository aggiuntivi (es. RPM Fusion, Packman). Il Toolbox non li attiva automaticamente.",
        "es": "Algunos programas pueden necesitar repositorios adicionales (p. ej. RPM Fusion, Packman). Toolbox nunca los activa automáticamente.",
        "fr": "Certains programmes peuvent nécessiter des dépôts supplémentaires (ex. RPM Fusion, Packman). Le Toolbox ne les active jamais automatiquement.",
    },
    "video_pack_scan_btn": {"en": "Check the system", "it": "Controlla il sistema", "es": "Comprobar el sistema", "fr": "Vérifier le système"},
    "video_pack_cancel_btn": {"en": "Cancel check", "it": "Annulla controllo", "es": "Cancelar comprobación", "fr": "Annuler la vérification"},
    "video_pack_scan_note": {
        "en": "Read-only check: no password, installation, removal, repository or driver operation is performed here.",
        "it": "Controllo in sola lettura: non esegue operazioni su password, installazioni, rimozioni, repository o driver.",
        "es": "Esta comprobación no modifica nada y no pide contraseña.",
        "fr": "Cette vérification ne modifie rien et ne demande jamais de mot de passe.",
    },
    "video_pack_scan_failed": {
        "en": "The analysis could not be completed.", "it": "Non è stato possibile completare l'analisi.",
        "es": "No se pudo completar el análisis.", "fr": "L'analyse n'a pas pu être terminée.",
    },
    "video_pack_state_already_installed": {"en": "Already installed", "it": "Già installato", "es": "Ya instalado", "fr": "Déjà installé"},
    "video_pack_state_available": {"en": "Available", "it": "Disponibile", "es": "Disponible", "fr": "Disponible"},
    "video_pack_state_not_available": {"en": "Not available in the currently configured repositories", "it": "Non disponibile nei repository attualmente configurati", "es": "No disponible en los repositorios configurados actualmente", "fr": "Non disponible dans les dépôts actuellement configurés"},
    "video_pack_state_not_verifiable": {"en": "Not verifiable yet", "it": "Non verificabile", "es": "No verificable todavía", "fr": "Non vérifiable"},
    "video_pack_comp_ffmpeg": {"en": "ffmpeg", "it": "ffmpeg", "es": "ffmpeg", "fr": "ffmpeg"},
    "video_pack_comp_obs_studio": {"en": "OBS Studio", "it": "OBS Studio", "es": "OBS Studio", "fr": "OBS Studio"},
    "video_pack_comp_kdenlive": {"en": "Kdenlive", "it": "Kdenlive", "es": "Kdenlive", "fr": "Kdenlive"},
    "video_pack_present_packages": {"en": "Present: {packages}", "it": "Presenti: {packages}", "es": "Presentes: {packages}", "fr": "Présents : {packages}"},
    "video_pack_suggested_packages": {"en": "Installable now: {packages}", "it": "Installabili ora: {packages}", "es": "Instalables ahora: {packages}", "fr": "Installables maintenant : {packages}"},
    "video_pack_unavailable_packages": {"en": "Not found in configured repositories: {packages}", "it": "Non trovati nei repository configurati: {packages}", "es": "No encontrados en los repositorios configurados: {packages}", "fr": "Introuvables dans les dépôts configurés : {packages}"},
    "video_pack_repositories": {"en": "Repositories: {packages}", "it": "Repository: {packages}", "es": "Repositorios: {packages}", "fr": "Dépôts : {packages}"},
    "video_pack_hwaccel_label": {
        "en": "ffmpeg hardware acceleration: {accels}", "it": "Accelerazione hardware di ffmpeg: {accels}",
        "es": "Aceleración por hardware de ffmpeg: {accels}", "fr": "Accélération matérielle de ffmpeg : {accels}",
    },
    "video_pack_hwaccel_none": {
        "en": "none detected", "it": "nessuna rilevata", "es": "ninguna detectada", "fr": "aucune détectée",
    },
    "video_pack_select_hint": {
        "en": "Tick the components you want, then install only those.",
        "it": "Seleziona i componenti desiderati, poi installa solo quelli.",
        "es": "Marca los componentes que quieres y luego instala solo esos.",
        "fr": "Cochez les composants souhaités, puis installez uniquement ceux-ci.",
    },
    "video_pack_select_checkbox_tooltip": {
        "en": "Select to install this component", "it": "Seleziona per installare questo componente",
        "es": "Selecciona para instalar este componente", "fr": "Sélectionner pour installer ce composant",
    },
    "video_pack_install_selected_btn": {
        "en": "Install selected components", "it": "Installa i componenti selezionati",
        "es": "Instalar los componentes seleccionados", "fr": "Installer les composants sélectionnés",
    },
    "video_pack_installing": {"en": "Installing…", "it": "Installazione in corso…", "es": "Instalando…", "fr": "Installation en cours…"},
    "video_pack_install_nothing_selected": {
        "en": "No selected component is actually installable right now.",
        "it": "Nessun componente selezionato è realmente installabile in questo momento.",
        "es": "Ningún componente seleccionado se puede instalar ahora mismo.",
        "fr": "Aucun composant sélectionné n'est réellement installable pour le moment.",
    },
    "video_pack_install_unsupported_family": {
        "en": "Installation isn't implemented for this distribution yet.",
        "it": "L'installazione non è ancora implementata per questa distribuzione.",
        "es": "La instalación aún no está implementada para esta distribución.",
        "fr": "L'installation n'est pas encore implémentée pour cette distribution.",
    },
    "video_pack_install_done": {
        "en": "Selected components installed.", "it": "Componenti selezionati installati.",
        "es": "Componentes seleccionados instalados.", "fr": "Composants sélectionnés installés.",
    },
    "video_pack_install_done_with_warning": {
        "en": "Components installed and verified, but a repository reported a problem during the transaction — see details.",
        "it": "Componenti installati e verificati, ma un repository ha segnalato un problema durante l'operazione — vedi dettagli.",
        "es": "Componentes instalados y verificados, pero un repositorio informó un problema durante la operación — ver detalles.",
        "fr": "Composants installés et vérifiés, mais un dépôt a signalé un problème pendant l'opération — voir les détails.",
    },
    "video_pack_install_precheck_failed": {
        "en": "One component is no longer available in the configured repositories.",
        "it": "Un componente non è più disponibile nei repository attualmente configurati.",
        "es": "Un componente ya no está disponible en los repositorios configurados.",
        "fr": "Un composant n'est plus disponible dans les dépôts configurés.",
    },
    "video_pack_install_failed": {
        "en": "Installation failed.", "it": "Installazione non riuscita.",
        "es": "La instalación ha fallado.", "fr": "Échec de l'installation.",
    },
    "video_pack_install_partial": {
        "en": "Partial installation: some packages were verified installed, others were not — see details.",
        "it": "Installazione parziale: alcuni pacchetti risultano installati e verificati, altri no — vedi dettagli.",
        "es": "Instalación parcial: algunos paquetes se instalaron y verificaron, otros no — ver detalles.",
        "fr": "Installation partielle : certains paquets ont été installés et vérifiés, d'autres non — voir les détails.",
    },
    "video_pack_remove_selected_btn": {
        "en": "Remove components installed by the Toolbox", "it": "Rimuovi i componenti installati dal Toolbox",
        "es": "Eliminar componentes instalados por Toolbox", "fr": "Supprimer les composants installés par Toolbox",
    },
    "video_pack_remove_done": {
        "en": "Selected components removed.", "it": "Componenti selezionati rimossi.",
        "es": "Componentes seleccionados eliminados.", "fr": "Composants sélectionnés supprimés.",
    },
    "video_pack_remove_done_with_warning": {
        "en": "Components removed and verified, but a repository reported a problem during the transaction — see details.",
        "it": "Componenti rimossi e verificati, ma un repository ha segnalato un problema durante l'operazione — vedi dettagli.",
        "es": "Componentes eliminados y verificados, pero un repositorio informó un problema durante la operación — ver detalles.",
        "fr": "Composants supprimés et vérifiés, mais un dépôt a signalé un problème pendant l'opération — voir les détails.",
    },
    "video_pack_remove_failed": {
        "en": "Removal failed.", "it": "Rimozione non riuscita.",
        "es": "La eliminación ha fallado.", "fr": "Échec de la suppression.",
    },
    "video_pack_remove_partial": {
        "en": "Partial removal: some packages were confirmed removed, others are still installed — see details.",
        "it": "Rimozione parziale: alcuni pacchetti risultano rimossi e verificati, altri sono ancora installati — vedi dettagli.",
        "es": "Eliminación parcial: algunos paquetes se confirmaron eliminados, otros siguen instalados — ver detalles.",
        "fr": "Suppression partielle : certains paquets ont été supprimés et vérifiés, d'autres non — voir les détails.",
    },
    "video_pack_remove_precheck_failed": {
        "en": "Removal was blocked because the recorded state no longer matches the system.",
        "it": "La rimozione è stata bloccata perché lo stato registrato non corrisponde più al sistema.",
        "es": "La eliminación se bloqueó porque el estado registrado ya no coincide con el sistema.",
        "fr": "La suppression a été bloquée, car l'état enregistré ne correspond plus au système.",
    },
    "video_pack_remove_nothing_selected": {
        "en": "No removable component is selected.", "it": "Nessun componente rimovibile è selezionato.",
        "es": "No hay ningún componente extraíble seleccionado.", "fr": "Aucun composant supprimable n'est sélectionné.",
    },
    "video_pack_remove_unsupported_family": {
        "en": "Removal isn't implemented for this distribution yet.", "it": "La rimozione non è ancora implementata per questa distribuzione.",
        "es": "La eliminación aún no está implementada para esta distribución.", "fr": "La suppression n'est pas encore implémentée pour cette distribution.",
    },
    "video_pack_install_confirm_title": {
        "en": "Install selected components", "it": "Installa i componenti selezionati",
        "es": "Instalar componentes seleccionados", "fr": "Installer les composants sélectionnés",
    },
    "video_pack_remove_confirm_title": {
        "en": "Remove selected components", "it": "Rimuovi i componenti selezionati",
        "es": "Eliminar componentes seleccionados", "fr": "Supprimer les composants sélectionnés",
    },
    "video_pack_confirm_body": {
        "en": "Components: {components}\nPackages: {packages}\n\nOnly one package-manager transaction will be started.",
        "it": "Componenti: {components}\nPacchetti: {packages}\n\nVerrà avviata una sola transazione del gestore pacchetti.",
        "es": "Componentes: {components}\nPaquetes: {packages}\n\nSolo se iniciará una transacción del gestor de paquetes.",
        "fr": "Composants: {components}\nPaquets: {packages}\n\nUne seule transaction du gestionnaire de paquets sera lancée.",
    },
    "video_pack_remove_component_btn": {"en": "Remove", "it": "Rimuovi", "es": "Eliminar", "fr": "Supprimer"},
}
for _k, _v in _video_editing_strings.items():
    _i18n_mod._strings[_k] = _v


_STATE_LABEL_KEYS = {
    vep.ALREADY_INSTALLED: "video_pack_state_already_installed",
    vep.AVAILABLE: "video_pack_state_available",
    vep.NOT_AVAILABLE: "video_pack_state_not_available",
    vep.NOT_VERIFIABLE: "video_pack_state_not_verifiable",
}
_STATE_CSS = {
    vep.ALREADY_INSTALLED: "status-active",
    vep.AVAILABLE: "badge-low",
    vep.NOT_AVAILABLE: "desc-con",
    vep.NOT_VERIFIABLE: "sysinfo-value-sub",
}


def _result_css_class(result) -> str:
    if result.friendly_message in ("video_pack_install_done_with_warning", "video_pack_remove_done_with_warning",
                                    "video_pack_install_partial", "video_pack_remove_partial"):
        return "status-warn"
    return "status-active" if result.ok else "desc-con"


class VideoEditingPackRow(FeatureRow):
    """Read-only analysis plus selective install/remove, mirroring
    GamingPackRow — the actual privileged work is done by
    video_pack_installer (core/package_pack_installer.PackInstaller),
    never by this widget directly."""

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

        self._scan_btn = Gtk.Button(label=T("video_pack_scan_btn"))
        self._scan_btn.add_css_class("lt-action-btn")
        self._scan_btn.connect("clicked", self._on_scan_button_clicked)

        self._scan_note = Gtk.Label(label=T("video_pack_scan_note"), xalign=0, wrap=True)
        self._scan_note.add_css_class("sysinfo-value-sub")

        self._system_lbl = Gtk.Label(wrap=True, xalign=0)
        self._system_lbl.add_css_class("sysinfo-value-sub")
        self._system_lbl.set_visible(False)

        self._hwaccel_lbl = Gtk.Label(wrap=True, xalign=0)
        self._hwaccel_lbl.add_css_class("sysinfo-value-sub")
        self._hwaccel_lbl.set_visible(False)

        self._select_hint_lbl = Gtk.Label(label=T("video_pack_select_hint"), xalign=0, wrap=True)
        self._select_hint_lbl.add_css_class("sysinfo-value-sub")
        self._select_hint_lbl.set_visible(False)

        self._results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self._install_btn = Gtk.Button(label=T("video_pack_install_selected_btn"))
        self._install_btn.add_css_class("lt-action-btn")
        self._install_btn.set_visible(False)
        self._install_btn.set_sensitive(False)
        self._install_btn.connect("clicked", self._on_install_selected_clicked)
        self._remove_btn = Gtk.Button(label=T("video_pack_remove_selected_btn"))
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

        super().__init__("video_pack", None, risk="low")
        self.connect("destroy", self._on_destroy)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(6)
        body.append(self._scan_btn)
        body.append(self._scan_note)
        body.append(self._system_lbl)
        body.append(self._hwaccel_lbl)
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
        self._scan_btn.set_label(T("video_pack_cancel_btn"))
        self._result_lbl.set_visible(False)
        self._detail_lbl.set_visible(False)

        def run():
            try:
                profile = vep.detect_system(job=self._scan_job)
                previews = vep.scan(
                    profile,
                    cancel_check=self._scan_cancel_event.is_set,
                    job=self._scan_job,
                )
                error = None
            except Exception as exc:
                profile = None
                previews = []
                error = exc
                logger.warning("Video Editing Pack analysis failed", exc_info=True)
            GLib.idle_add(
                self._on_scan_done, generation, profile, previews,
                self._scan_cancel_event.is_set(), error,
            )

        threading.Thread(target=run, name="mg-video-editing-pack-scan", daemon=True).start()

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
        self._scan_btn.set_label(T("video_pack_scan_btn"))

    def _on_scan_done(self, generation, profile, previews, was_cancelled=False, error=None):
        if self._destroyed or generation != self._scan_generation:
            return False
        self._reset_scan_button()
        if error is not None:
            self._result_lbl.set_text(T("video_pack_scan_failed"))
            self._result_lbl.set_visible(True)
            return False
        if was_cancelled:
            return False

        self._last_profile = profile
        self._last_previews = previews
        self._selected_ids = set()
        self._removable_ids = video_pack_installer.removable_component_ids(profile, previews)

        self._system_lbl.set_text(
            f"{profile.distro_pretty_name} — {profile.package_manager} — {profile.architecture}"
        )
        self._system_lbl.set_visible(True)

        if profile.ffmpeg_present:
            accels = ", ".join(profile.hwaccels) if profile.hwaccels else T("video_pack_hwaccel_none")
            self._hwaccel_lbl.set_text(T("video_pack_hwaccel_label").format(accels=accels))
            self._hwaccel_lbl.set_visible(True)
        else:
            self._hwaccel_lbl.set_visible(False)

        _clear_box(self._results_box)
        any_installable = False
        for preview in previews:
            self._results_box.append(self._preview_row(preview))
            if preview.state == vep.AVAILABLE and preview.suggested_packages:
                any_installable = True

        self._select_hint_lbl.set_visible(any_installable)
        self._install_btn.set_visible(any_installable)
        self._install_btn.set_sensitive(False)
        self._remove_btn.set_visible(bool(self._removable_ids))
        self._remove_btn.set_sensitive(False)

        return False

    def _preview_row(self, preview) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        if preview.state == vep.AVAILABLE and preview.suggested_packages:
            check = Gtk.CheckButton()
            check.set_valign(Gtk.Align.CENTER)
            check.set_tooltip_text(T("video_pack_select_checkbox_tooltip"))
            check.connect("toggled", self._on_component_toggled, preview.component_id)
            row.append(check)

        name_lbl = Gtk.Label(label=T(f"video_pack_comp_{preview.component_id}"), xalign=0, hexpand=True, wrap=True)
        row.append(name_lbl)

        state_lbl = Gtk.Label(label=T(_STATE_LABEL_KEYS.get(preview.state, preview.state)), xalign=1)
        state_lbl.add_css_class(_STATE_CSS.get(preview.state, "sysinfo-value-sub"))
        row.append(state_lbl)
        if preview.component_id in self._removable_ids:
            remove_btn = Gtk.Button(label=T("video_pack_remove_component_btn"))
            remove_btn.add_css_class("destructive-action")
            remove_btn.connect("clicked", self._on_remove_single_clicked, preview.component_id)
            row.append(remove_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(row)

        package_details = (
            (preview.installed_packages, "video_pack_present_packages"),
            (preview.suggested_packages, "video_pack_suggested_packages"),
            (preview.unavailable_packages, "video_pack_unavailable_packages"),
            (preview.repositories, "video_pack_repositories"),
        )
        for packages, key in package_details:
            if packages:
                label = Gtk.Label(label=T(key).format(packages=", ".join(packages)), xalign=0, wrap=True)
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
            "video_pack_install_confirm_title",
            list(self._selected_ids),
            lambda: self._run_install(list(self._selected_ids)),
        )

    def _run_install(self, component_ids):
        self._install_running = True
        self._install_btn.set_sensitive(False)
        self._remove_btn.set_sensitive(False)
        self._install_btn.set_label(T("video_pack_installing"))
        self._result_lbl.set_visible(False)
        self._detail_lbl.set_visible(False)

        previews = self._last_previews
        self._install_job = Job()

        def run():
            try:
                result = video_pack_installer.install_selected(component_ids, self._last_profile, previews, job=self._install_job)
            except Exception as exc:
                logger.warning("Video Editing Pack install failed", exc_info=True)
                result = InstallSelectionResult(False, friendly_message="video_pack_install_failed", technical_detail=str(exc))
            GLib.idle_add(self._on_install_done, result)

        threading.Thread(target=run, name="mg-video-editing-pack-install", daemon=True).start()

    def _on_install_done(self, result):
        self._install_running = False
        self._install_job = None
        self._install_btn.set_label(T("video_pack_install_selected_btn"))
        self._result_lbl.set_text(T(result.friendly_message) if result.friendly_message else "")
        self._result_lbl.remove_css_class("desc-con")
        self._result_lbl.remove_css_class("status-active")
        self._result_lbl.remove_css_class("status-warn")
        self._result_lbl.add_css_class(_result_css_class(result))
        self._result_lbl.set_visible(bool(result.friendly_message))
        self._detail_lbl.set_text(result.technical_detail or "")
        self._detail_lbl.set_visible(bool(result.technical_detail))
        should_rescan = result.ok or result.friendly_message == "video_pack_install_partial"
        if should_rescan and not self._destroyed:
            self._on_scan_button_clicked(None)
        else:
            self._install_btn.set_sensitive(bool(self._selected_ids))
            self._remove_btn.set_sensitive(bool(self._selected_ids & self._removable_ids))
        return False

    def _on_remove_single_clicked(self, _btn, component_id):
        self._confirm_operation(
            "video_pack_remove_confirm_title",
            [component_id],
            lambda: self._run_remove([component_id]),
        )

    def _on_remove_selected_clicked(self, _btn):
        removable_selection = sorted(self._selected_ids & self._removable_ids)
        if not removable_selection:
            return
        self._confirm_operation(
            "video_pack_remove_confirm_title",
            removable_selection,
            lambda: self._run_remove(removable_selection),
        )

    def _confirm_operation(self, title_key, component_ids, on_confirm):
        component_names = [T(f"video_pack_comp_{cid}") for cid in component_ids]
        packages = []
        for preview in self._last_previews:
            if preview.component_id not in component_ids:
                continue
            packages.extend(preview.suggested_packages or preview.installed_packages)
        dialog = Adw.MessageDialog(transient_for=self.get_root(), heading=T(title_key))
        dialog.set_body(T("video_pack_confirm_body").format(
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
                result = video_pack_installer.remove_selected(component_ids, self._last_profile, self._last_previews, job=self._install_job)
            except Exception as exc:
                logger.warning("Video Editing Pack remove failed", exc_info=True)
                result = InstallSelectionResult(False, friendly_message="video_pack_remove_failed", technical_detail=str(exc))
            GLib.idle_add(self._on_remove_done, result)

        threading.Thread(target=run, name="mg-video-editing-pack-remove", daemon=True).start()

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
        should_rescan = result.ok or result.friendly_message == "video_pack_remove_partial"
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


class VideoEditingPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("camera-video-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        header = PageHeader(
            "camera-video-symbolic", T("tab_video_editing"), T("ds_video_editing_header_desc"),
            category="neutral",
        )
        self.add(wrap_in_preferences_group(header))

        g1 = make_group("video_pack_title")
        self.add(g1)
        self.video_pack = VideoEditingPackRow()
        self.video_pack.add_prefix(IconBadge("camera-video-symbolic", category="neutral"))
        g1.add(self.video_pack)

    def _refresh_title(self):
        self.set_title(T("tab_video_editing"))
