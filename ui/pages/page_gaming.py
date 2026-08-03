import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import InstallRow, FeatureRow, make_group, run_install_in_background
import backend.all as B
import logging
import threading

from core import gaming_readiness as gr
from core import gaming_pack as gp
from core.executor import Job

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge

logger = logging.getLogger(__name__)

_gaming_ds_strings = {
    "ds_gaming_header_desc": {
        "en": "Check readiness and install common gaming tools.",
        "it": "Controlla la preparazione e installa gli strumenti gaming più comuni.",
        "es": "Comprueba la preparación e instala las herramientas de juego más comunes.",
        "fr": "Vérifiez la préparation et installez les outils de jeu courants.",
    },
}
for _k, _v in _gaming_ds_strings.items():
    _i18n_mod._strings[_k] = _v

# ── Gaming Pack V1 (2026-08-03) ──────────────────────────────────────
_gaming_pack_strings = {
    "gaming_pack_title": {"en": "Gaming Pack", "it": "Gaming Pack", "es": "Gaming Pack", "fr": "Gaming Pack"},
    "gaming_pack_desc": {
        "en": "Analyses gaming readiness and previews packages that may be useful. It doesn't install anything.",
        "it": "Analizza la preparazione al gaming e mostra un'anteprima dei pacchetti eventualmente utili. Non installa nulla.",
        "es": "Analiza la preparación para jugar y muestra una vista previa de los paquetes que podrían ser útiles. No instala nada.",
        "fr": "Analyse la préparation au jeu et affiche un aperçu des paquets éventuellement utiles. N'installe rien.",
    },
    "gaming_pack_pro": {
        "en": "Detects the distribution, GPU and package status using read-only checks.",
        "it": "Rileva distribuzione, GPU e stato dei pacchetti con controlli in sola lettura.",
        "es": "Detecta la distribución, la GPU y el estado de los paquetes con comprobaciones de solo lectura.",
        "fr": "Détecte la distribution, le GPU et l'état des paquets avec des vérifications en lecture seule.",
    },
    "gaming_pack_con": {
        "en": "This first version is analysis/preview only and makes no system changes.",
        "it": "Questa prima versione offre soltanto analisi e anteprima e non modifica il sistema.",
        "es": "Esta primera versión solo ofrece análisis y vista previa y no modifica el sistema.",
        "fr": "Cette première version propose uniquement une analyse et un aperçu, sans modifier le système.",
    },
    "gaming_pack_scan_btn": {"en": "Check the system", "it": "Controlla il sistema", "es": "Comprobar el sistema", "fr": "Vérifier le système"},
    "gaming_pack_scanning": {"en": "Checking…", "it": "Controllo in corso…", "es": "Comprobando…", "fr": "Vérification…"},
    "gaming_pack_scan_note": {
        "en": "Read-only check: no password, installation, removal, repository, driver, kernel or full-system update operation is performed.",
        "it": "Controllo in sola lettura: non esegue operazioni su password, installazioni, rimozioni, repository, driver, kernel o aggiornamenti completi del sistema.",
        "es": "Esta comprobación no modifica nada y no pide contraseña.",
        "fr": "Cette vérification ne modifie rien et ne demande jamais de mot de passe.",
    },
    "gaming_pack_testing_note": {
        "en": "The Debian-family mapping was tested on this Pop!_OS machine. Fedora, Arch-family and openSUSE still require testing on real machines.",
        "it": "La mappatura Debian è stata provata su questa macchina Pop!_OS. Fedora, famiglia Arch e openSUSE richiedono ancora prove su macchine reali.",
        "es": "La asignación Debian se probó en esta máquina Pop!_OS. Fedora, la familia Arch y openSUSE aún requieren pruebas en máquinas reales.",
        "fr": "Le mappage Debian a été testé sur cette machine Pop!_OS. Fedora, la famille Arch et openSUSE doivent encore être testés sur des machines réelles.",
    },
    "gaming_pack_gpu_blocked": {
        "en": "The graphics driver could not be verified. Package analysis remains informational and no change is made.",
        "it": "Non è stato possibile verificare il driver grafico. L'analisi dei pacchetti resta informativa e non viene effettuata alcuna modifica.",
        "es": "Antes de preparar el sistema para jugar es necesario comprobar el controlador de la tarjeta gráfica. No se ha realizado ningún cambio.",
        "fr": "Avant de préparer le système pour le jeu, il faut vérifier le pilote de la carte graphique. Aucune modification n'a été effectuée.",
    },
    "gaming_pack_state_already_installed": {"en": "Already installed", "it": "Già installato", "es": "Ya instalado", "fr": "Déjà installé"},
    "gaming_pack_state_available": {"en": "Available", "it": "Disponibile", "es": "Disponible", "fr": "Disponible"},
    "gaming_pack_state_not_available": {"en": "Not available", "it": "Non disponibile", "es": "No disponible", "fr": "Non disponible"},
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
    "gaming_pack_suggested_packages": {"en": "Preview only, for manual evaluation: {packages}", "it": "Solo anteprima, da valutare manualmente: {packages}", "es": "Solo vista previa, para evaluación manual: {packages}", "fr": "Aperçu uniquement, à évaluer manuellement : {packages}"},
    "gaming_pack_unavailable_packages": {"en": "Not found in configured repositories: {packages}", "it": "Non trovati nei repository configurati: {packages}", "es": "No encontrados en los repositorios configurados: {packages}", "fr": "Introuvables dans les dépôts configurés : {packages}"},

    "gaming_pack_comp_steam": {"en": "Steam", "it": "Steam", "es": "Steam", "fr": "Steam"},
    "gaming_pack_comp_gamemode": {"en": "GameMode", "it": "GameMode", "es": "GameMode", "fr": "GameMode"},
    "gaming_pack_comp_mangohud": {"en": "MangoHud", "it": "MangoHud", "es": "MangoHud", "fr": "MangoHud"},
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

        self._results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self._result_lbl = Gtk.Label(wrap=True, xalign=0)
        self._result_lbl.add_css_class("desc-con")
        self._result_lbl.set_visible(False)

        super().__init__("gaming_pack", None, risk="low")
        self.connect("destroy", self._on_destroy)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(6)
        body.append(self._scan_btn)
        body.append(self._scan_note)
        body.append(self._testing_note)
        body.append(self._blocked_lbl)
        body.append(self._system_lbl)
        body.append(self._results_box)
        body.append(self._result_lbl)

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
        for preview in previews:
            self._results_box.append(self._preview_row(preview))
            if preview.optional and preview.state in (gp.NOT_AVAILABLE, gp.NOT_VERIFIABLE):
                has_optional_gap = True

        if has_optional_gap:
            note = Gtk.Label(label=T("gaming_pack_optional_note"), xalign=0, wrap=True)
            note.add_css_class("sysinfo-value-sub")
            self._results_box.append(note)

        return False

    def _preview_row(self, preview) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        name_key = f"gaming_pack_comp_{preview.component_id}"
        label_text = T(name_key)
        if preview.optional:
            label_text += f" ({T('gaming_pack_optional_tag')})"
        name_lbl = Gtk.Label(label=label_text, xalign=0, hexpand=True, wrap=True)
        row.append(name_lbl)

        state_lbl = Gtk.Label(label=T(_STATE_LABEL_KEYS.get(preview.state, preview.state)), xalign=1)
        state_lbl.add_css_class(_STATE_CSS.get(preview.state, "sysinfo-value-sub"))
        row.append(state_lbl)

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

        self.lib32 = InstallRow("lib32", B.lib32_installed(), risk="low",
                                dep_pkg="lib32 (mesa:i386)",
                                dep_check=B.lib32_installed,
                                dep_install=B.lib32_install)
        self.lib32.button.connect("clicked", self._on_lib32)
        self.lib32.add_prefix(IconBadge("input-gaming-symbolic", category="neutral"))
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
                                   B.gamemode_installed, self.gamemode.mark_installed)

    def _on_mango(self, _):
        run_install_in_background(self.mango.button, B.mangohud_install,
                                   B.mangohud_installed, self.mango.mark_installed)

    def _on_lib32(self, _):
        run_install_in_background(self.lib32.button, B.lib32_install,
                                   B.lib32_installed, self.lib32.mark_installed)

    def _on_vulkan(self, _):
        run_install_in_background(self.vulkan.button, B.vulkan_install,
                                   B.vulkan_installed, self.vulkan.mark_installed)
