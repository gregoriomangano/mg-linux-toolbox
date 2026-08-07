import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import FeatureRow, SwitchRow, make_group, report_toggle_result, run_install_in_background
import backend.all as B
from core import apparmor_setup as aa
from core.kernel_features.base import SupportStatus
from core.kernel_features.registry import register
from core.kernel_features.security import SELinuxFeature
from ui.pages.page_kernel import ChoiceKernelFeatureRow
import core.clamav as clamav

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge
from ui.design_system.action_bar import style_kernel_feature_row_buttons
from ui.design_system.status_pill import state_pill, StatusPill
from ui.design_system.value_translation import translated_value

_security_ds_strings = {
    "ds_security_header_desc": {
        "en": "Check active protections and system security settings.",
        "it": "Controlla le protezioni attive e le impostazioni di sicurezza del sistema.",
        "es": "Comprueba las protecciones activas y la configuración de seguridad del sistema.",
        "fr": "Vérifiez les protections actives et les paramètres de sécurité du système.",
    },
    "ds_secureboot_title": {
        "en": "Secure Boot", "it": "Secure Boot", "es": "Secure Boot", "fr": "Secure Boot",
    },
    "ds_secureboot_desc": {
        "en": "This protection can be turned on from the computer's UEFI settings.",
        "it": "Questa protezione può essere attivata dalle impostazioni UEFI del computer.",
        "es": "Esta protección se puede activar desde la configuración UEFI del equipo.",
        "fr": "Cette protection peut être activée depuis les paramètres UEFI de l'ordinateur.",
    },
    "ds_state_active": {"en": "Active", "it": "Attivo", "es": "Activo", "fr": "Actif"},
    "ds_state_inactive": {"en": "Inactive", "it": "Disattivato", "es": "Inactivo", "fr": "Inactif"},
    "ds_state_unknown": {"en": "Unknown status", "it": "Stato sconosciuto", "es": "Estado desconocido", "fr": "État inconnu"},
    # 2026-08-04: SSH/root-login consistency fix — the toggle is now
    # gated on the same real state shown here, never clickable against
    # a config that doesn't exist or can't be read.
    "rootssh_state_not_installed": {"en": "Not applicable — SSH server not installed",
                                      "it": "Non applicabile — Server SSH non installato",
                                      "es": "No aplicable — Servidor SSH no instalado",
                                      "fr": "Non applicable — Serveur SSH non installé"},
    "rootssh_state_undetermined": {"en": "State could not be determined", "it": "Stato non determinabile",
                                     "es": "No se pudo determinar el estado", "fr": "État impossible à déterminer"},
    "rootssh_state_disabled": {"en": "Root login is currently disabled.", "it": "L'accesso root è attualmente disattivato.",
                                 "es": "El acceso root está actualmente desactivado.", "fr": "La connexion root est actuellement désactivée."},
    "rootssh_state_allowed": {"en": "Root login is currently allowed.", "it": "L'accesso root è attualmente consentito.",
                                "es": "El acceso root está actualmente permitido.", "fr": "La connexion root est actuellement autorisée."},
    # 2026-08-07: Rete e dispositivi / Sicurezza split — firewall's
    # granular state label, moved here with the row itself.
    "fw_state_ufw_active": {"en": "UFW — Active", "it": "UFW — Attivo", "es": "UFW — Activo", "fr": "UFW — Actif"},
    "fw_state_ufw_inactive": {"en": "UFW — Inactive", "it": "UFW — Inattivo", "es": "UFW — Inactivo", "fr": "UFW — Inactif"},
    "fw_state_ufw_not_configured": {"en": "UFW — Installed but not configured", "it": "UFW — Installato ma non configurato",
                                      "es": "UFW — Instalado pero no configurado", "fr": "UFW — Installé mais non configuré"},
    "fw_state_firewalld_active": {"en": "Firewalld — Active", "it": "Firewalld — Attivo", "es": "Firewalld — Activo", "fr": "Firewalld — Actif"},
    "fw_state_firewalld_inactive": {"en": "Firewalld — Inactive", "it": "Firewalld — Inattivo", "es": "Firewalld — Inactivo", "fr": "Firewalld — Inactif"},
    "fw_state_nftables_rules": {"en": "nftables — Rules detected", "it": "nftables — Regole rilevate",
                                  "es": "nftables — Reglas detectadas", "fr": "nftables — Règles détectées"},
    "fw_state_none_detected": {"en": "No supported firewall detected", "it": "Nessun firewall supportato rilevato",
                                 "es": "No se detectó ningún firewall compatible", "fr": "Aucun pare-feu pris en charge détecté"},
    "fw_state_undetermined": {"en": "State could not be determined", "it": "Stato non determinabile",
                                "es": "No se pudo determinar el estado", "fr": "État impossible à déterminer"},
}
for _k, _v in _security_ds_strings.items():
    _i18n_mod._strings[_k] = _v

_clamav_ds_strings = {
    "grp_malware_protection": {
        "en": "Malware Protection", "it": "Protezione malware",
        "es": "Protección contra malware", "fr": "Protection contre les logiciels malveillants",
    },
    "clamav_title": {
        "en": "ClamAV Antivirus", "it": "Antivirus ClamAV",
        "es": "Antivirus ClamAV", "fr": "Antivirus ClamAV",
    },
    "clamav_subtitle": {
        "en": "Cross-platform malware scanner", "it": "Scanner malware multipiattaforma",
        "es": "Escáner de malware multiplataforma", "fr": "Scanner de logiciels malveillants multiplateforme",
    },
    # FeatureRow always resolves {key}_desc/_pro/_con internally (the
    # trio is hidden for this row, see ClamAVRow — its own explanation/
    # COS'È/UTILE PER/LIMITAZIONE body replaces them), but they're kept
    # real and translated rather than left to fall back to the raw key.
    "clamav_desc": {
        "en": "Scans files and folders for known malware using ClamAV.",
        "it": "Analizza file e cartelle alla ricerca di malware conosciuto con ClamAV.",
        "es": "Analiza archivos y carpetas en busca de malware conocido con ClamAV.",
        "fr": "Analyse les fichiers et dossiers à la recherche de logiciels malveillants connus avec ClamAV.",
    },
    "clamav_pro": {
        "en": "Useful before opening files downloaded from the Internet.",
        "it": "Utile prima di aprire file scaricati da Internet.",
        "es": "Útil antes de abrir archivos descargados de Internet.",
        "fr": "Utile avant d'ouvrir des fichiers téléchargés depuis Internet.",
    },
    "clamav_con": {
        "en": "Does not guarantee a file is safe just because nothing was found.",
        "it": "Non garantisce che un file sia sicuro solo perché non è stato trovato nulla.",
        "es": "No garantiza que un archivo sea seguro solo porque no se haya encontrado nada.",
        "fr": "Ne garantit pas qu'un fichier est sûr simplement parce que rien n'a été trouvé.",
    },
    "clamav_technical_name_value": {
        "en": "ClamAV Malware Scanner", "it": "ClamAV Malware Scanner",
        "es": "ClamAV Malware Scanner", "fr": "ClamAV Malware Scanner",
    },
    "clamav_explain": {
        "en": ("ClamAV is an open-source antivirus scanner. It can check Linux "
               "and Windows files, archives, scripts and other content for "
               "known malware.\n\nThe check is mainly based on updated "
               "signatures. No threat found does not guarantee that a file "
               "is safe."),
        "it": ("ClamAV è uno scanner antivirus open source. Può analizzare "
               "file Linux e Windows, archivi, script e altri contenuti "
               "alla ricerca di malware conosciuto.\n\nIl controllo si "
               "basa principalmente su firme aggiornate. Nessuna minaccia "
               "rilevata non garantisce che un file sia sicuro."),
        "es": ("ClamAV es un escáner antivirus de código abierto. Puede "
               "analizar archivos de Linux y Windows, archivos comprimidos, "
               "scripts y otros contenidos en busca de malware conocido.\n\n"
               "El control se basa principalmente en firmas actualizadas. "
               "Que no se detecte ninguna amenaza no garantiza que un "
               "archivo sea seguro."),
        "fr": ("ClamAV est un scanner antivirus open source. Il peut "
               "analyser des fichiers Linux et Windows, des archives, des "
               "scripts et d'autres contenus à la recherche de logiciels "
               "malveillants connus.\n\nLe contrôle repose principalement "
               "sur des signatures à jour. L'absence de menace détectée ne "
               "garantit pas qu'un fichier soit sûr."),
    },
    "clamav_what_label": {"en": "WHAT IT DOES", "it": "COS'È", "es": "QUÉ ES", "fr": "QU'EST-CE QUE C'EST"},
    "clamav_what_body": {
        "en": "Checks files and folders for known malware.",
        "it": "Controlla file e cartelle alla ricerca di malware conosciuto.",
        "es": "Comprueba archivos y carpetas en busca de malware conocido.",
        "fr": "Vérifie les fichiers et dossiers à la recherche de logiciels malveillants connus.",
    },
    "clamav_usefor_label": {"en": "USEFUL FOR", "it": "UTILE PER", "es": "ÚTIL PARA", "fr": "UTILE POUR"},
    "clamav_usefor_body": {
        "en": "Files downloaded from the Internet, attachments, archives, AppImages, scripts and files from other computers.",
        "it": "File scaricati da Internet, allegati, archivi, AppImage, script e file provenienti da altri computer.",
        "es": "Archivos descargados de Internet, adjuntos, archivos comprimidos, AppImages, scripts y archivos de otros equipos.",
        "fr": "Fichiers téléchargés depuis Internet, pièces jointes, archives, AppImages, scripts et fichiers provenant d'autres ordinateurs.",
    },
    "clamav_limit_label": {"en": "LIMITATION", "it": "LIMITAZIONE", "es": "LIMITACIÓN", "fr": "LIMITE"},
    "clamav_limit_body": {
        "en": "It does not replace common sense, nor guarantee that a new or unknown program is safe.",
        "it": "Non sostituisce il buon senso né garantisce che un programma nuovo o sconosciuto sia sicuro.",
        "es": "No sustituye al sentido común ni garantiza que un programa nuevo o desconocido sea seguro.",
        "fr": "Il ne remplace pas le bon sens et ne garantit pas qu'un programme nouveau ou inconnu soit sûr.",
    },
    "clamav_state_not_installed": {"en": "Not installed", "it": "Non installato", "es": "No instalado", "fr": "Non installé"},
    "clamav_state_installed": {"en": "Installed", "it": "Installato", "es": "Instalado", "fr": "Installé"},
    "clamav_state_ready": {"en": "Ready", "it": "Pronto", "es": "Listo", "fr": "Prêt"},
    "clamav_state_outdated": {"en": "Signatures need updating", "it": "Firme da aggiornare", "es": "Firmas por actualizar", "fr": "Signatures à mettre à jour"},
    "clamav_state_unknown": {"en": "Unknown status", "it": "Stato sconosciuto", "es": "Estado desconocido", "fr": "État inconnu"},
    "clamav_install_btn": {"en": "Install", "it": "Installa", "es": "Instalar", "fr": "Installer"},
    "clamav_not_available_repo": {
        "en": "ClamAV is not available in the repositories configured on your system.",
        "it": "ClamAV non è disponibile nei repository configurati sul tuo sistema.",
        "es": "ClamAV no está disponible en los repositorios configurados en tu sistema.",
        "fr": "ClamAV n'est pas disponible dans les dépôts configurés sur votre système.",
    },
    "clamav_service_label": {"en": "Scan service", "it": "Servizio di scansione", "es": "Servicio de análisis", "fr": "Service d'analyse"},
    "clamav_service_active": {"en": "active", "it": "attivo", "es": "activo", "fr": "actif"},
    "clamav_service_inactive": {"en": "inactive", "it": "inattivo", "es": "inactivo", "fr": "inactif"},
    "clamav_service_not_detected": {
        "en": "No scan service detected on this system.",
        "it": "Nessun servizio di scansione rilevato su questo sistema.",
        "es": "No se detectó ningún servicio de análisis en este sistema.",
        "fr": "Aucun service d'analyse détecté sur ce système.",
    },
    "clamav_update_defs_btn": {"en": "Update definitions", "it": "Aggiorna definizioni", "es": "Actualizar definiciones", "fr": "Mettre à jour les définitions"},
    "clamav_update_defs_in_progress": {"en": "Updating definitions…", "it": "Aggiornamento definizioni…", "es": "Actualizando definiciones…", "fr": "Mise à jour des définitions…"},
    "clamav_update_defs_ok": {"en": "Definitions updated.", "it": "Definizioni aggiornate.", "es": "Definiciones actualizadas.", "fr": "Définitions mises à jour."},
    "clamav_update_defs_failed": {
        "en": "Could not update the signature definitions.",
        "it": "Non è stato possibile aggiornare le definizioni delle firme.",
        "es": "No se pudieron actualizar las definiciones de firmas.",
        "fr": "Impossible de mettre à jour les définitions des signatures.",
    },
    "clamav_scan_header": {"en": "SCAN A FILE OR FOLDER", "it": "SCANSIONA FILE O CARTELLA", "es": "ANALIZAR UN ARCHIVO O CARPETA", "fr": "ANALYSER UN FICHIER OU UN DOSSIER"},
    "clamav_scan_file_btn": {"en": "Choose a file…", "it": "Scegli un file…", "es": "Elegir un archivo…", "fr": "Choisir un fichier…"},
    "clamav_scan_folder_btn": {"en": "Choose a folder…", "it": "Scegli una cartella…", "es": "Elegir una carpeta…", "fr": "Choisir un dossier…"},
    "clamav_scan_in_progress": {"en": "Scanning…", "it": "Scansione in corso…", "es": "Analizando…", "fr": "Analyse en cours…"},
    "clamav_scan_clean": {"en": "No threats found", "it": "Nessuna minaccia rilevata", "es": "No se encontraron amenazas", "fr": "Aucune menace détectée"},
    "clamav_scan_threats_found": {
        "en": "{n} possible threat(s) found", "it": "Rilevate {n} possibili minacce",
        "es": "Se detectaron {n} posibles amenazas", "fr": "{n} menace(s) potentielle(s) détectée(s)",
    },
    "clamav_scan_error": {
        "en": "The scan could not be completed.", "it": "Non è stato possibile completare la scansione.",
        "es": "No se pudo completar el análisis.", "fr": "L'analyse n'a pas pu être terminée.",
    },
    "clamav_scan_path_not_found": {
        "en": "The chosen path no longer exists.", "it": "Il percorso scelto non esiste più.",
        "es": "La ruta elegida ya no existe.", "fr": "Le chemin choisi n'existe plus.",
    },
    "clamav_service_start_btn": {
        "en": "Start scan service", "it": "Avvia servizio di scansione",
        "es": "Iniciar servicio de análisis", "fr": "Démarrer le service d'analyse",
    },
    "clamav_service_stop_btn": {
        "en": "Stop scan service", "it": "Ferma servizio di scansione",
        "es": "Detener servicio de análisis", "fr": "Arrêter le service d'analyse",
    },
    "clamav_service_toggle_failed": {
        "en": "Could not change the scan service state.",
        "it": "Non è stato possibile cambiare lo stato del servizio di scansione.",
        "es": "No se pudo cambiar el estado del servicio de análisis.",
        "fr": "Impossible de modifier l'état du service d'analyse.",
    },
    "clamav_uninstall_btn": {
        "en": "Uninstall ClamAV", "it": "Disinstalla ClamAV",
        "es": "Desinstalar ClamAV", "fr": "Désinstaller ClamAV",
    },
    "clamav_uninstall_confirm_title": {
        "en": "Uninstall ClamAV?", "it": "Disinstallare ClamAV?",
        "es": "¿Desinstalar ClamAV?", "fr": "Désinstaller ClamAV ?",
    },
    "clamav_uninstall_confirm_body": {
        "en": ("Only the ClamAV packages actually detected as installed will be "
               "removed, using the distribution's own package manager. No "
               "repository will be changed."),
        "it": ("Verranno rimossi solo i pacchetti ClamAV rilevati come "
               "realmente installati, usando il gestore pacchetti della "
               "distribuzione. Nessun repository verrà modificato."),
        "es": ("Solo se eliminarán los paquetes de ClamAV detectados como "
               "realmente instalados, usando el gestor de paquetes de la "
               "distribución. No se modificará ningún repositorio."),
        "fr": ("Seuls les paquets ClamAV réellement détectés comme installés "
               "seront supprimés, via le gestionnaire de paquets de la "
               "distribution. Aucun dépôt ne sera modifié."),
    },
    "clamav_uninstall_in_progress": {
        "en": "Uninstalling…", "it": "Disinstallazione in corso…",
        "es": "Desinstalando…", "fr": "Désinstallation en cours…",
    },
    "clamav_uninstall_failed": {
        "en": "Could not complete the uninstall.", "it": "Non è stato possibile completare la disinstallazione.",
        "es": "No se pudo completar la desinstalación.", "fr": "Impossible de terminer la désinstallation.",
    },
}
for _k, _v in _clamav_ds_strings.items():
    _i18n_mod._strings[_k] = _v


def _service_state_pill(installed: bool, active: bool) -> StatusPill:
    """A grey switch alone is ambiguous (off? not installed? unknown?) —
    this always spells the real state out in words next to it, per the
    same three/five-state vocabulary already used elsewhere (StatusPill)."""
    if not installed:
        return StatusPill(translated_value("not_installed"), variant="absent")
    if active:
        return StatusPill(translated_value("enabled"), variant="success")
    return StatusPill(translated_value("disabled"), variant="neutral")


def _security_icon_category(available: bool) -> str:
    """The icon's color signals whether this control is really usable
    on THIS system (green = the required tool is installed, grey = it
    isn't) — never a judgement about whether being on/off is "safe"
    for a given service (e.g. SSH on isn't inherently risky, it
    depends entirely on what the user needs), which this app has no
    real basis to assert on its own."""
    return "security-ok" if available else "neutral"


_CLAMAV_STATE_PILL = {
    clamav.STATE_NOT_INSTALLED:      ("clamav_state_not_installed", "absent"),
    clamav.STATE_INSTALLED:          ("clamav_state_installed", "neutral"),
    clamav.STATE_READY:              ("clamav_state_ready", "success"),
    clamav.STATE_SIGNATURES_OUTDATED: ("clamav_state_outdated", "warning"),
    clamav.STATE_UNKNOWN:            ("clamav_state_unknown", "neutral"),
}


class ClamAVRow(FeatureRow):
    """
    Antivirus ClamAV — detection, install (official repos only), signature
    updates via freshclam, and a single on-demand scan of one file/folder.
    Built on FeatureRow for the shared risk badge + operation-error/details
    machinery, but the auto-rendered Cos'è/Vantaggio/Quando-evitare trio
    doesn't fit this row's content (a general explanation paragraph plus
    three short COS'È/UTILE PER/LIMITAZIONE lines, per spec) — those three
    labels are hidden right after construction and replaced with this
    row's own body content below.

    Never claims real-time protection: the collapsed pill and every label
    here describe exactly what was actually detected (installed / signature
    database state / clamd service state), never an inferred "protetto".
    """
    def __init__(self):
        self._status_pill = StatusPill("", variant="neutral")
        self._install_btn = Gtk.Button(valign=Gtk.Align.CENTER)
        self._install_btn.add_css_class("lt-action-btn")
        self._install_btn.connect("clicked", self._on_install_clicked)
        self._install_btn.set_visible(False)

        suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, valign=Gtk.Align.CENTER)
        suffix.append(self._status_pill)
        suffix.append(self._install_btn)

        super().__init__("clamav", suffix, risk="low")
        self.add_prefix(IconBadge("security-high-symbolic", category="security-risk"))
        style_kernel_feature_row_buttons(self)
        # This row's body doesn't use the generic Cos'è/Vantaggio/Quando
        # evitare trio — see class docstring.
        self._lbl_what.set_visible(False)
        self._lbl_pro.set_visible(False)
        self._lbl_con.set_visible(False)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self._tech_name_lbl = Gtk.Label(wrap=True, xalign=0)
        self._tech_name_lbl.add_css_class("sysinfo-value-sub")
        body.append(self._tech_name_lbl)

        self._explain_lbl = Gtk.Label(wrap=True, xalign=0)
        self._explain_lbl.add_css_class("desc-what")
        body.append(self._explain_lbl)

        self._what_lbl = Gtk.Label(wrap=True, xalign=0)
        self._what_lbl.add_css_class("sysinfo-value")
        body.append(self._what_lbl)
        self._usefor_lbl = Gtk.Label(wrap=True, xalign=0)
        self._usefor_lbl.add_css_class("sysinfo-value")
        body.append(self._usefor_lbl)
        self._limit_lbl = Gtk.Label(wrap=True, xalign=0)
        self._limit_lbl.add_css_class("desc-con")
        body.append(self._limit_lbl)

        self._not_available_lbl = Gtk.Label(wrap=True, xalign=0)
        self._not_available_lbl.add_css_class("desc-con")
        self._not_available_lbl.set_visible(False)
        body.append(self._not_available_lbl)

        self._service_lbl = Gtk.Label(wrap=True, xalign=0)
        self._service_lbl.add_css_class("sysinfo-value-sub")
        body.append(self._service_lbl)

        defs_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._update_defs_btn = Gtk.Button()
        self._update_defs_btn.connect("clicked", self._on_update_definitions)
        defs_box.append(self._update_defs_btn)
        # "Avvia"/"Ferma servizio di scansione" — only ever shown when a
        # real clamd unit was detected (clamd_manageable()); never
        # offered as a fake control on a system with on-demand-only
        # scanning. Toggling this never means "antivirus on/off" — the
        # service label above always spells out what it really is.
        self._service_toggle_btn = Gtk.Button()
        self._service_toggle_btn.connect("clicked", self._on_service_toggle_clicked)
        self._service_toggle_btn.set_visible(False)
        defs_box.append(self._service_toggle_btn)
        body.append(defs_box)
        self._defs_result_lbl = Gtk.Label(wrap=True, xalign=0)
        self._defs_result_lbl.set_visible(False)
        body.append(self._defs_result_lbl)

        self._scan_header_lbl = Gtk.Label(xalign=0)
        self._scan_header_lbl.add_css_class("sysinfo-label")
        self._scan_header_lbl.set_margin_top(6)
        body.append(self._scan_header_lbl)

        scan_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._scan_file_btn = Gtk.Button()
        self._scan_file_btn.connect("clicked", lambda _b: self._on_pick_scan_target(Gtk.FileChooserAction.OPEN))
        self._scan_folder_btn = Gtk.Button()
        self._scan_folder_btn.connect("clicked", lambda _b: self._on_pick_scan_target(Gtk.FileChooserAction.SELECT_FOLDER))
        scan_box.append(self._scan_file_btn)
        scan_box.append(self._scan_folder_btn)
        body.append(scan_box)

        self._scan_status_lbl = Gtk.Label(wrap=True, xalign=0)
        self._scan_status_lbl.set_visible(False)
        body.append(self._scan_status_lbl)

        self._scan_details_btn = Gtk.Button()
        self._scan_details_btn.add_css_class("flat")
        self._scan_details_btn.set_visible(False)
        self._scan_details_btn.connect("clicked", self._on_toggle_scan_details)
        body.append(self._scan_details_btn)

        self._scan_details_lbl = Gtk.Label(wrap=True, xalign=0, selectable=True)
        self._scan_details_lbl.set_visible(False)
        body.append(self._scan_details_lbl)

        uninstall_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        uninstall_box.set_margin_top(6)
        self._uninstall_btn = Gtk.Button()
        self._uninstall_btn.add_css_class("destructive-action")
        self._uninstall_btn.set_visible(False)
        self._uninstall_btn.connect("clicked", self._on_uninstall_clicked)
        uninstall_box.append(self._uninstall_btn)
        body.append(uninstall_box)
        self._uninstall_result_lbl = Gtk.Label(wrap=True, xalign=0)
        self._uninstall_result_lbl.set_visible(False)
        body.append(self._uninstall_result_lbl)

        self.add_row(body)

        self._scan_busy = False
        self._service_busy = False
        self._uninstall_busy = False
        on_change(self._refresh_labels)
        self._refresh_labels()
        self._refresh_state()

    # ── Labels (i18n) ────────────────────────────────────────────────
    def _refresh_labels(self):
        self.set_subtitle(T("clamav_subtitle"))
        self._tech_name_lbl.set_text(f"{T('kf_technical_name')}: {T('clamav_technical_name_value')}")
        self._explain_lbl.set_text(T("clamav_explain"))
        self._what_lbl.set_text(f"{T('clamav_what_label')}: {T('clamav_what_body')}")
        self._usefor_lbl.set_text(f"{T('clamav_usefor_label')}: {T('clamav_usefor_body')}")
        self._limit_lbl.set_text(f"{T('clamav_limit_label')}: {T('clamav_limit_body')}")
        self._not_available_lbl.set_text(T("clamav_not_available_repo"))
        self._install_btn.set_label(T("clamav_install_btn"))
        self._update_defs_btn.set_label(T("clamav_update_defs_btn"))
        self._scan_header_lbl.set_text(T("clamav_scan_header"))
        self._scan_file_btn.set_label(T("clamav_scan_file_btn"))
        self._scan_folder_btn.set_label(T("clamav_scan_folder_btn"))
        self._scan_details_btn.set_label(T("kf_show_details_btn"))
        self._uninstall_btn.set_label(T("clamav_uninstall_btn"))
        self._refresh_service_label()

    def _refresh_service_label(self):
        installed = clamav.is_installed()
        active = clamav.clamd_active()
        if active is None:
            self._service_lbl.set_text(T("clamav_service_not_detected"))
        else:
            state_word = T("clamav_service_active") if active else T("clamav_service_inactive")
            self._service_lbl.set_text(f"{T('clamav_service_label')}: {state_word}")

        # Avvia/Ferma is only ever offered when a real clamd unit was
        # detected — never a fake control for on-demand-only systems.
        manageable = installed and clamav.clamd_manageable()
        self._service_toggle_btn.set_visible(manageable)
        if manageable:
            self._service_toggle_btn.set_label(
                T("clamav_service_stop_btn") if active else T("clamav_service_start_btn"))
            self._service_toggle_btn.set_sensitive(not self._service_busy)

    # ── State ───────────────────────────────────────────────────────
    def _refresh_state(self):
        installed = clamav.is_installed()
        key, variant = _CLAMAV_STATE_PILL.get(clamav.state(), _CLAMAV_STATE_PILL[clamav.STATE_UNKNOWN])
        self._status_pill.set_text(T(key))
        self._status_pill.set_variant(variant)

        self._install_btn.set_visible(not installed)
        if not installed:
            available = clamav.is_available_in_repos()
            self._install_btn.set_sensitive(available)
            self._not_available_lbl.set_visible(not available)
        else:
            self._not_available_lbl.set_visible(False)

        self._update_defs_btn.set_sensitive(installed and clamav.freshclam_present())
        self._scan_file_btn.set_sensitive(installed and not self._scan_busy)
        self._scan_folder_btn.set_sensitive(installed and not self._scan_busy)
        self._uninstall_btn.set_visible(installed)
        self._uninstall_btn.set_sensitive(not self._uninstall_busy)
        self._refresh_service_label()

    # ── Install ─────────────────────────────────────────────────────
    def _on_install_clicked(self, _btn):
        run_install_in_background(self._install_btn, self._do_install, clamav.is_installed,
                                    self._on_install_success, self._on_install_failure)

    def _do_install(self):
        result = clamav.install()
        clamav.log_install(bool(result), result.technical_detail() if hasattr(result, "technical_detail") else "")

    def _on_install_success(self):
        self._refresh_state()

    def _on_install_failure(self):
        self._refresh_state()

    # ── Signature updates ──────────────────────────────────────────
    def _on_update_definitions(self, _btn):
        if not self._update_defs_btn.get_sensitive():
            return
        self._update_defs_btn.set_sensitive(False)
        self._defs_result_lbl.set_visible(True)
        self._defs_result_lbl.remove_css_class("desc-con")
        self._defs_result_lbl.remove_css_class("status-active")
        self._defs_result_lbl.set_text(T("clamav_update_defs_in_progress"))

        def run():
            result = clamav.update_definitions()
            clamav.log_definitions_update(bool(result), result.technical_detail() if hasattr(result, "technical_detail") else "")
            GLib.idle_add(self._on_update_definitions_done, bool(result))

        threading.Thread(target=run, daemon=True).start()

    def _on_update_definitions_done(self, ok: bool):
        self._update_defs_btn.set_sensitive(True)
        if ok:
            self._defs_result_lbl.add_css_class("status-active")
            self._defs_result_lbl.set_text(T("clamav_update_defs_ok"))
        else:
            self._defs_result_lbl.add_css_class("desc-con")
            self._defs_result_lbl.set_text(T("clamav_update_defs_failed"))
        self._refresh_state()
        return False

    # ── Scan service (clamd) — Avvia/Ferma ───────────────────────────
    def _on_service_toggle_clicked(self, _btn):
        if self._service_busy or not clamav.clamd_manageable():
            return
        want_enabled = not clamav.clamd_active()
        self._service_busy = True
        self._service_toggle_btn.set_sensitive(False)
        self._defs_result_lbl.set_visible(False)

        def run():
            result = clamav.clamd_start() if want_enabled else clamav.clamd_stop()
            clamav.log_service_toggle(bool(result), want_enabled,
                                       result.technical_detail() if hasattr(result, "technical_detail") else "")
            GLib.idle_add(self._on_service_toggle_done, bool(result))

        threading.Thread(target=run, daemon=True).start()

    def _on_service_toggle_done(self, ok: bool):
        self._service_busy = False
        if not ok:
            self._defs_result_lbl.set_visible(True)
            self._defs_result_lbl.remove_css_class("status-active")
            self._defs_result_lbl.add_css_class("desc-con")
            self._defs_result_lbl.set_text(T("clamav_service_toggle_failed"))
        self._refresh_state()
        return False

    # ── Uninstall ─────────────────────────────────────────────────────
    def _on_uninstall_clicked(self, _btn):
        if self._uninstall_busy:
            return
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=T("clamav_uninstall_confirm_title"),
            body=T("clamav_uninstall_confirm_body"),
        )
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("clamav_uninstall_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_uninstall_confirm_response)
        dialog.present()

    def _on_uninstall_confirm_response(self, _dialog, response):
        if response != "confirm":
            return  # user cancelled — nothing happens
        self._uninstall_busy = True
        self._uninstall_btn.set_sensitive(False)
        self._uninstall_result_lbl.set_visible(True)
        self._uninstall_result_lbl.remove_css_class("desc-con")
        self._uninstall_result_lbl.set_text(T("clamav_uninstall_in_progress"))

        def run():
            result = clamav.uninstall()
            clamav.log_uninstall(bool(result), result.technical_detail() if hasattr(result, "technical_detail") else "")
            GLib.idle_add(self._on_uninstall_done, bool(result))

        threading.Thread(target=run, daemon=True).start()

    def _on_uninstall_done(self, ok: bool):
        self._uninstall_busy = False
        if ok:
            # Card returns to "Non installato" + Installa immediately —
            # the pill/buttons below are rebuilt from the real re-read
            # state, never left showing stale "Installato" controls.
            self._uninstall_result_lbl.set_visible(False)
        else:
            self._uninstall_result_lbl.set_visible(True)
            self._uninstall_result_lbl.add_css_class("desc-con")
            self._uninstall_result_lbl.set_text(T("clamav_uninstall_failed"))
        self._refresh_state()
        return False

    # ── Scan ────────────────────────────────────────────────────────
    def _on_pick_scan_target(self, action):
        if self._scan_busy:
            return
        title = T("clamav_scan_file_btn") if action == Gtk.FileChooserAction.OPEN else T("clamav_scan_folder_btn")
        dialog = Gtk.FileChooserNative.new(title, self.get_root(), action, None, None)

        def on_response(d, response):
            if response == Gtk.ResponseType.ACCEPT:
                target = d.get_file()
                if target is not None and target.get_path():
                    self._start_scan(target.get_path())
            d.destroy()

        dialog.connect("response", on_response)
        dialog.show()

    def _start_scan(self, path: str):
        self._scan_busy = True
        self._scan_file_btn.set_sensitive(False)
        self._scan_folder_btn.set_sensitive(False)
        self._scan_details_btn.set_visible(False)
        self._scan_details_lbl.set_visible(False)
        self._scan_status_lbl.set_visible(True)
        self._scan_status_lbl.remove_css_class("desc-con")
        self._scan_status_lbl.remove_css_class("status-active")
        self._scan_status_lbl.set_text(T("clamav_scan_in_progress"))

        def run():
            result = clamav.scan_path(path)
            clamav.log_scan(result.ok, infected_count=result.infected_count,
                             technical_detail=result.technical_detail)
            GLib.idle_add(self._on_scan_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_scan_done(self, result: "clamav.ScanResult"):
        self._scan_busy = False
        self._refresh_state()
        if result.ok and result.infected_count == 0:
            self._scan_status_lbl.add_css_class("status-active")
            self._scan_status_lbl.set_text(T("clamav_scan_clean"))
        elif result.ok:
            self._scan_status_lbl.add_css_class("desc-con")
            self._scan_status_lbl.set_text(T("clamav_scan_threats_found").format(n=result.infected_count))
            self._scan_details_btn.set_visible(bool(result.infected_files))
            self._scan_details_lbl.set_text("\n".join(result.infected_files))
        else:
            self._scan_status_lbl.add_css_class("desc-con")
            friendly = T("clamav_scan_path_not_found") if result.error == "path_not_found" else T("clamav_scan_error")
            self._scan_status_lbl.set_text(friendly)
            self._scan_details_btn.set_visible(bool(result.technical_detail))
            self._scan_details_lbl.set_text(result.technical_detail)
        return False

    def _on_toggle_scan_details(self, _btn):
        self._scan_details_lbl.set_visible(not self._scan_details_lbl.get_visible())


_APPARMOR_PILL = {
    "active_configured":        ("success", True,  "apparmor_state_active_configured"),
    "supported_not_configured": ("warning", False, "apparmor_state_supported_not_configured"),
    "inactive":                 ("neutral", False, "apparmor_state_inactive"),
    "not_available":            ("absent",  False, "apparmor_state_not_available"),
    "unknown":                  ("neutral", False, "apparmor_state_unknown"),
}


def _apparmor_state() -> str:
    """One of 5 real, detected states — never guessed. 'configured' means
    AppArmor is installed, its service is active, AND at least one
    profile is actually loaded (aa-status reports it) — being merely
    installed+running with zero profiles protects nothing yet, so it's
    kept distinct from "active_configured"."""
    try:
        if not aa.is_installed():
            return "not_available"
        if not aa.service_active():
            return "inactive"
        return "active_configured" if aa.list_profiles() else "supported_not_configured"
    except Exception:
        return "unknown"


class AppArmorRow(FeatureRow):
    def __init__(self):
        self._detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._status_pill = state_pill("unknown", "")
        super().__init__("apparmor", None, risk="medium")
        self.add_suffix(self._status_pill)
        self.add_row(self._detail_box)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._enable_btn = Gtk.Button(label=T("apparmor_enable_btn"))
        self._enable_btn.add_css_class("lt-action-btn")
        self._enable_btn.connect("clicked", self._on_enable)
        self._disable_btn = Gtk.Button(label=T("apparmor_disable_btn"))
        self._disable_btn.add_css_class("destructive-action")
        self._disable_btn.connect("clicked", self._on_disable)
        self._reload_btn = Gtk.Button(label=T("apparmor_reload_btn"))
        self._reload_btn.connect("clicked", self._on_reload)
        self._profiles_btn = Gtk.Button(label=T("apparmor_show_profiles_btn"))
        self._profiles_btn.connect("clicked", self._on_show_profiles)
        for b in (self._enable_btn, self._disable_btn, self._reload_btn, self._profiles_btn):
            btn_box.append(b)
        self.add_row(btn_box)

        self._refresh_detail()

    def _refresh_detail(self):
        state = _apparmor_state()
        variant, show_check, text_key = _APPARMOR_PILL[state]
        self._status_pill.set_text(T(text_key))
        self._status_pill.set_variant(variant)
        self._status_pill.set_show_check(show_check)

        child = self._detail_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._detail_box.remove(child)
            child = nxt

        if not aa.is_installed():
            note = Gtk.Label(label=T("apparmor_not_installed_note"), xalign=0, wrap=True)
            note.add_css_class("desc-con")
            self._detail_box.append(note)
            for b in (self._enable_btn, self._disable_btn, self._reload_btn, self._profiles_btn):
                b.set_sensitive(False)
            return

        active = aa.service_active()
        lbl = Gtk.Label(label=T("apparmor_service_active" if active else "apparmor_service_inactive"),
                         xalign=0, wrap=True)
        lbl.add_css_class("status-active" if active else "sysinfo-value")
        self._detail_box.append(lbl)
        self._enable_btn.set_visible(not active)
        self._disable_btn.set_visible(active)

    def _run_bg(self, btn, fn):
        btn.set_sensitive(False)

        def run():
            result = fn()
            GLib.idle_add(self._on_action_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_enable(self, _btn):
        self._run_bg(self._enable_btn, aa.enable_service)

    def _on_disable(self, _btn):
        self._run_bg(self._disable_btn, aa.disable_service)

    def _on_reload(self, _btn):
        self._run_bg(self._reload_btn, aa.reload_profiles)

    def _on_action_done(self, ok):
        self._enable_btn.set_sensitive(True)
        self._disable_btn.set_sensitive(True)
        self._reload_btn.set_sensitive(True)
        self._refresh_detail()
        return False

    def _on_show_profiles(self, _btn):
        profiles = aa.list_profiles()
        body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if not profiles:
            body_box.append(Gtk.Label(label=T("apparmor_no_profiles"), xalign=0, wrap=True))
        for profile in profiles:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.append(Gtk.Label(label=f"{profile['path']} ({profile['mode']})", xalign=0, hexpand=True, wrap=True))
            enforce_btn = Gtk.Button(label=T("apparmor_enforce_btn"))
            enforce_btn.connect("clicked", self._make_profile_action(aa.enforce_profile, profile["path"]))
            complain_btn = Gtk.Button(label=T("apparmor_complain_btn"))
            complain_btn.connect("clicked", self._make_profile_action(aa.complain_profile, profile["path"]))
            disable_btn = Gtk.Button(label=T("apparmor_disable_profile_btn"))
            disable_btn.add_css_class("destructive-action")
            disable_btn.connect("clicked", self._make_profile_action(aa.disable_profile, profile["path"]))
            restore_btn = Gtk.Button(label=T("apparmor_restore_profile_btn"))
            restore_btn.connect("clicked", self._make_profile_restore_action(profile["path"]))
            for b in (enforce_btn, complain_btn, disable_btn, restore_btn):
                row.append(b)
            body_box.append(row)

        scroller = Gtk.ScrolledWindow(min_content_height=200, max_content_height=450)
        scroller.set_child(body_box)
        dialog = Adw.MessageDialog(transient_for=self.get_root(), heading=T("apparmor_profiles_dialog_title"))
        dialog.set_extra_child(scroller)
        dialog.add_response("close", T("dialog_close_btn"))
        dialog.present()

    def _make_profile_action(self, fn, path):
        def handler(_btn):
            threading.Thread(target=fn, args=(path,), daemon=True).start()
        return handler

    def _make_profile_restore_action(self, path):
        def handler(_btn):
            threading.Thread(target=aa.restore_profile, args=(path,), daemon=True).start()
        return handler


class _SecureBootRow(FeatureRow):
    """FeatureRow for the read-only Secure Boot status row. Same title/
    desc/pro text as any other "secureboot"-prefixed FeatureRow, but the
    "when to avoid" line grows a real, detected reason appended after it
    whenever the state is "unknown" — never a second guess at
    active/inactive, only an honest explanation of why detection failed
    this time (see backend.all.secureboot_unknown_reason)."""
    def __init__(self, control, reason_key: "str | None"):
        self._reason_key = reason_key
        super().__init__("secureboot", control, risk="low")

    def _refresh(self):
        super()._refresh()
        if self._reason_key:
            self._lbl_con.set_text(
                f"⚠️  {T('when_avoid')}: {T('secureboot_con')} {T(self._reason_key)}"
            )


class SecurityPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("security-high-symbolic")
        on_change(self._refresh_title)
        on_change(lambda: self._refresh_rootssh_state(B.root_ssh_state()))
        on_change(self._refresh_fw_state_label)
        self._refresh_title()

        header = PageHeader(
            "security-high-symbolic", T("tab_security"), T("ds_security_header_desc"),
            category="security-ok",
        )
        self.add(wrap_in_preferences_group(header))

        # ── Protezione del sistema ───────────────────────────────────
        g_system = make_group("grp_system_protection")
        self.add(g_system)

        self.autoupd = SwitchRow("autoupdate", B.auto_updates_active(), risk="low",
                                 dep_pkg="unattended-upgrades / dnf-automatic / pacman-contrib",
                                 dep_check=B.auto_updates_dep_ok,
                                 dep_install=B.auto_updates_dep_install)
        self.autoupd.switch.connect("notify::active", self._on_autoupd)
        self.autoupd.add_prefix(IconBadge("software-update-available-symbolic", category="security-ok"))
        style_kernel_feature_row_buttons(self.autoupd)
        g_system.add(self.autoupd)

        if aa.is_installed():
            apparmor_row = AppArmorRow()
            apparmor_row.add_prefix(IconBadge("security-high-symbolic", category="security-ok"))
            style_kernel_feature_row_buttons(apparmor_row)
            g_system.add(apparmor_row)
        selinux_feature = register(SELinuxFeature())
        if selinux_feature.probe() != SupportStatus.UNSUPPORTED_KERNEL:
            selinux_row = ChoiceKernelFeatureRow(selinux_feature, "selinux")
            selinux_row.add_prefix(IconBadge("security-high-symbolic", category="security-ok"))
            style_kernel_feature_row_buttons(selinux_row)
            g_system.add(selinux_row)

        sb_state = B.secureboot_state()
        sb_text = {"active": T("ds_state_active"), "inactive": T("ds_state_inactive")}.get(
            sb_state, T("ds_state_unknown"))
        sb_pill = state_pill(sb_state if sb_state in ("active", "inactive") else "unknown", sb_text)
        # V7: "Unknown status" alone never explains itself — when it
        # happens, the expanded card must say WHY (BIOS/Legacy boot,
        # missing efivarfs, missing mokutil, permissions, read error),
        # never silently, and never turned into a guessed active/inactive.
        sb_reason_key = None
        if sb_state == "unknown":
            reason = B.secureboot_unknown_reason()
            sb_reason_key = {
                "no_efi": "secureboot_reason_no_efi",
                "no_efivarfs": "secureboot_reason_no_efivarfs",
                "tool_missing": "secureboot_reason_tool_missing",
                "permission_denied": "secureboot_reason_permission",
            }.get(reason, "secureboot_reason_read_error")
        secureboot_row = _SecureBootRow(sb_pill, sb_reason_key)
        secureboot_row.add_prefix(IconBadge("security-high-symbolic", category="security-ok" if sb_state == "active" else "neutral"))
        g_system.add(secureboot_row)

        # ── Accesso e rete ────────────────────────────────────────────
        g_access = make_group("grp_access_network")
        self.add(g_access)

        # Firewall — ufw (debian/arch) or firewalld (fedora/opensuse).
        # dep_check no longer just checks the binary: `firewall_state()`
        # combines the binary, the package, /etc/ufw/ufw.conf and the
        # systemd unit (see core/firewall_detect.py) so a GUFW-only
        # install (GUFW is just a front-end, never the firewall itself)
        # doesn't get reported as "not installed".
        from core.firewall_detect import STATE_NONE_DETECTED
        fw_dep_check = lambda: B.firewall_state() not in (STATE_NONE_DETECTED,)
        self.fw = SwitchRow("fw", B.firewall_active(), risk="medium",
                            dep_pkg="ufw / firewalld",
                            dep_check=fw_dep_check,
                            dep_install=lambda job=None: B._install_pkg({"debian": "ufw", "arch": "ufw", "fedora": "firewalld", "opensuse": "firewalld"}, job=job))
        self.fw.switch.connect("notify::active", self._on_fw)
        self.fw.add_prefix(IconBadge("security-high-symbolic", category=_security_icon_category(fw_dep_check())))
        self._wire_status_pill(self.fw, fw_dep_check)
        self._fw_state_lbl = Gtk.Label(xalign=0, wrap=True)
        self._fw_state_lbl.add_css_class("sysinfo-value-sub")
        self.fw.add_row(self._fw_state_lbl)
        self._refresh_fw_state_label()
        style_kernel_feature_row_buttons(self.fw)
        g_access.add(self.fw)

        # SSH — openssh is in all repos
        ssh_dep_check = lambda: B._service_exists("ssh") or B._service_exists("sshd")
        self.ssh = SwitchRow("ssh", B.ssh_active(), risk="low",
                             dep_pkg="openssh",
                             dep_check=ssh_dep_check,
                             dep_install=lambda job=None: B._install_pkg({"debian": "openssh-server", "arch": "openssh", "fedora": "openssh-server", "opensuse": "openssh"}, job=job))
        self.ssh.switch.connect("notify::active", self._on_ssh)
        self.ssh.add_prefix(IconBadge("network-server-symbolic", category=_security_icon_category(ssh_dep_check())))
        self._wire_status_pill(self.ssh, ssh_dep_check)
        style_kernel_feature_row_buttons(self.ssh)
        g_access.add(self.ssh)

        rootssh_state = B.root_ssh_state()
        self.rootssh = SwitchRow("rootssh", rootssh_state == "allowed", risk="low",
                                 dep_pkg="openssh",
                                 dep_check=lambda: B.ssh_server_installed(),
                                 dep_install=None)
        self.rootssh.switch.connect("notify::active", self._on_rootssh)
        self.rootssh.add_prefix(IconBadge("security-high-symbolic", category="security-ok"))
        self._rootssh_state_lbl = Gtk.Label(xalign=0, wrap=True)
        self._rootssh_state_lbl.add_css_class("sysinfo-value-sub")
        self.rootssh.add_row(self._rootssh_state_lbl)
        self._refresh_rootssh_state(rootssh_state)
        style_kernel_feature_row_buttons(self.rootssh)
        g_access.add(self.rootssh)

        # ── Protezione malware ────────────────────────────────────────
        g_malware = make_group("grp_malware_protection")
        self.add(g_malware)
        self.clamav = ClamAVRow()
        g_malware.add(self.clamav)

    def _wire_status_pill(self, row, dep_check):
        """Adds an explicit StatusPill next to a SwitchRow's switch, kept
        in sync with the switch and with dep_check() — so a grey switch
        never has to be interpreted on its own (per spec: never rely on
        switch position alone to convey installed/active/unavailable)."""
        try:
            installed = bool(dep_check())
        except Exception:
            row.add_suffix(StatusPill(translated_value("unknown"), variant="neutral"))
            return
        pill = _service_state_pill(installed, row.switch.get_active())
        row.add_suffix(pill)

        def _refresh(*_args):
            try:
                is_installed = bool(dep_check())
            except Exception:
                pill.set_text(translated_value("unknown"))
                pill.set_variant("neutral")
                return
            if not is_installed:
                pill.set_text(translated_value("not_installed"))
                pill.set_variant("absent")
            elif row.switch.get_active():
                pill.set_text(translated_value("enabled"))
                pill.set_variant("success")
            else:
                pill.set_text(translated_value("disabled"))
                pill.set_variant("neutral")

        row.switch.connect("notify::active", _refresh)
        on_change(_refresh)

    def _refresh_title(self):
        self.set_title(T("tab_security"))

    _ROOTSSH_STATE_KEYS = {
        "not_installed": "rootssh_state_not_installed",
        "undetermined": "rootssh_state_undetermined",
        "disabled": "rootssh_state_disabled",
        "allowed": "rootssh_state_allowed",
    }

    def _refresh_rootssh_state(self, state: str):
        actionable = state in ("disabled", "allowed")
        self.rootssh.switch.set_sensitive(actionable)
        self._rootssh_state_lbl.set_text(T(self._ROOTSSH_STATE_KEYS.get(state, "rootssh_state_undetermined")))

    def _on_rootssh(self, sw, _):
        want_allowed = sw.get_active()
        result = B.root_ssh_set_disabled(not want_allowed)
        sw.set_active(not result.value)
        report_toggle_result(self.rootssh, "security", "security.root_ssh", result.ok,
                             result.technical_detail, friendly_key=result.friendly_message or "kf_err_generic")
        self._refresh_rootssh_state(B.root_ssh_state())

    def _on_autoupd(self, sw, _):
        sw.set_active(B.auto_updates_set(sw.get_active()))

    _FW_STATE_KEYS = {
        "ufw_active": "fw_state_ufw_active",
        "ufw_inactive": "fw_state_ufw_inactive",
        "ufw_installed_not_configured": "fw_state_ufw_not_configured",
        "firewalld_active": "fw_state_firewalld_active",
        "firewalld_inactive": "fw_state_firewalld_inactive",
        "nftables_rules": "fw_state_nftables_rules",
        "none_detected": "fw_state_none_detected",
        "undetermined": "fw_state_undetermined",
    }

    def _refresh_fw_state_label(self):
        state = B.firewall_state()
        self._fw_state_lbl.set_text(T(self._FW_STATE_KEYS.get(state, "fw_state_undetermined")))

    def _on_fw(self, sw, _):
        result = B.firewall_set(sw.get_active())
        sw.set_active(result.value)
        report_toggle_result(self.fw, "security", "network.firewall", result.ok, result.technical_detail)
        self._refresh_fw_state_label()

    def _on_ssh(self, sw, _):
        result = B.ssh_set(sw.get_active())
        sw.set_active(result.value)
        report_toggle_result(self.ssh, "security", "network.ssh", result.ok, result.technical_detail)
