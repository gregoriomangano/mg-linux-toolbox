"""
"Software e repository" — universal distro detection, Flatpak/Flathub
setup, read-only repository inventory, guarded additional-repository
recipes, and "Salute pacchetti". Built entirely on core.software_repo.*
(package_engine.run_operation is the only path anything here uses to
touch the system) and on the existing design system (PageHeader,
make_section, StatusPill, IconBadge) — no new visual language.
"""
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from core.i18n import T, on_change
from core import i18n as _i18n_mod
from core.software_repo import distro_profile as dp
from core.software_repo import flatpak_manager as fpm
from core.software_repo import repo_scanner as rsc
from core.software_repo import repo_recipes as rr
from core.software_repo import package_health as health
from core.software_repo import package_engine as engine

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge
from ui.design_system.section_card import make_section
from ui.design_system.status_pill import StatusPill, state_pill
from ui.pages.page_kernel import _widen_preferences_clamp

CATEGORY = "software"

_page_strings = {
    "tab_software_repos": {"en": "Software and Repositories", "it": "Software e repository",
                             "es": "Software y repositorios", "fr": "Logiciels et dépôts"},
    "sr_header_desc": {
        "en": "Configure Flatpak, check software sources and verify package status.",
        "it": "Configura Flatpak, controlla le sorgenti software e verifica lo stato dei pacchetti.",
        "es": "Configura Flatpak, comprueba las fuentes de software y verifica el estado de los paquetes.",
        "fr": "Configurez Flatpak, vérifiez les sources logicielles et l'état des paquets.",
    },

    # ── Section A ──────────────────────────────────────────────────
    "sr_section_a_title": {"en": "Recognized system", "it": "Sistema riconosciuto",
                             "es": "Sistema reconocido", "fr": "Système reconnu"},
    "sr_distro": {"en": "Distribution", "it": "Distribuzione", "es": "Distribución", "fr": "Distribution"},
    "sr_version": {"en": "Version", "it": "Versione", "es": "Versión", "fr": "Version"},
    "sr_family": {"en": "Family", "it": "Famiglia rilevata", "es": "Familia", "fr": "Famille"},
    "sr_codename": {"en": "Codename", "it": "Nome in codice", "es": "Nombre en clave", "fr": "Nom de code"},
    "sr_pkg_manager": {"en": "Package manager", "it": "Gestore pacchetti", "es": "Gestor de paquetes", "fr": "Gestionnaire de paquets"},
    "sr_system_type": {"en": "System type", "it": "Tipo di sistema", "es": "Tipo de sistema", "fr": "Type de système"},
    "sr_system_type_traditional": {"en": "traditional", "it": "tradizionale", "es": "tradicional", "fr": "traditionnel"},
    "sr_system_type_immutable": {"en": "immutable", "it": "immutabile", "es": "inmutable", "fr": "immuable"},
    "sr_system_type_transactional": {"en": "transactional", "it": "transazionale", "es": "transaccional", "fr": "transactionnel"},
    "sr_system_type_unknown": {"en": "unknown", "it": "sconosciuto", "es": "desconocido", "fr": "inconnu"},
    "sr_confident_yes": {"en": "Detection verified", "it": "Rilevamento verificato", "es": "Detección verificada", "fr": "Détection vérifiée"},
    "sr_confident_no": {"en": "Detection to verify", "it": "Rilevamento da verificare", "es": "Detección por verificar", "fr": "Détection à vérifier"},

    # ── Section B ──────────────────────────────────────────────────
    "sr_section_b_title": {"en": "Flatpak and Flathub", "it": "Flatpak e Flathub", "es": "Flatpak y Flathub", "fr": "Flatpak et Flathub"},
    "sr_flatpak_installed": {"en": "Flatpak", "it": "Flatpak", "es": "Flatpak", "fr": "Flatpak"},
    "sr_flatpak_state_installed": {"en": "Installed", "it": "Installato", "es": "Instalado", "fr": "Installé"},
    "sr_flatpak_state_not_installed": {"en": "Not installed", "it": "Non installato", "es": "No instalado", "fr": "Non installé"},
    "sr_flathub_system": {"en": "System-wide Flathub", "it": "Flathub di sistema", "es": "Flathub del sistema", "fr": "Flathub système"},
    "sr_flathub_user": {"en": "Personal Flathub", "it": "Flathub personale", "es": "Flathub personal", "fr": "Flathub personnel"},
    "sr_flathub_active": {"en": "Active", "it": "Attivo", "es": "Activo", "fr": "Actif"},
    "sr_flathub_not_configured": {"en": "Not configured", "it": "Non configurato", "es": "No configurado", "fr": "Non configuré"},
    "sr_integration": {"en": "Desktop integration", "it": "Integrazione desktop", "es": "Integración de escritorio", "fr": "Intégration au bureau"},
    "sr_integration_complete": {"en": "Complete", "it": "Completa", "es": "Completa", "fr": "Complète"},
    "sr_integration_incomplete": {"en": "To complete", "it": "Da completare", "es": "Por completar", "fr": "À compléter"},
    "sr_session_logout_recommended": {"en": "Session — logout recommended", "it": "Sessione — logout consigliato",
                                        "es": "Sesión — se recomienda cerrar sesión", "fr": "Session — déconnexion recommandée"},
    "sr_configure_flatpak_btn": {"en": "Configure Flatpak and Flathub", "it": "Configura Flatpak e Flathub",
                                   "es": "Configurar Flatpak y Flathub", "fr": "Configurer Flatpak et Flathub"},
    "sr_configure_flatpak_dialog_title": {"en": "Configure Flatpak and Flathub", "it": "Configura Flatpak e Flathub",
                                            "es": "Configurar Flatpak y Flathub", "fr": "Configurer Flatpak et Flathub"},
    "sr_configure_flatpak_preview": {
        "en": "Flatpak will be installed using your system's package manager, if it isn't already.\n"
              "The official Flathub repository will be added.\nNo application will be installed.\n"
              "A logout/login may be required for full desktop integration.",
        "it": "Verrà installato Flatpak tramite il gestore pacchetti del tuo sistema, se non è già presente.\n"
              "Verrà aggiunto il repository ufficiale Flathub.\nNon verrà installata alcuna applicazione.\n"
              "Potrebbe essere necessario uscire e rientrare nella sessione per l'integrazione completa.",
        "es": "Se instalará Flatpak mediante el gestor de paquetes de tu sistema, si aún no está presente.\n"
              "Se añadirá el repositorio oficial Flathub.\nNo se instalará ninguna aplicación.\n"
              "Puede ser necesario cerrar e iniciar sesión de nuevo.",
        "fr": "Flatpak sera installé via le gestionnaire de paquets de votre système, s'il ne l'est pas déjà.\n"
              "Le dépôt officiel Flathub sera ajouté.\nAucune application ne sera installée.\n"
              "Une déconnexion/reconnexion peut être nécessaire pour une intégration complète.",
    },
    "sr_scope_system": {"en": "For every user", "it": "Per tutti gli utenti", "es": "Para todos los usuarios", "fr": "Pour tous les utilisateurs"},
    "sr_scope_system_desc": {
        "en": "Flatpak and Flathub will be available to every account on this computer.",
        "it": "Flatpak e Flathub saranno disponibili per tutti gli account del computer.",
        "es": "Flatpak y Flathub estarán disponibles para todas las cuentas del equipo.",
        "fr": "Flatpak et Flathub seront disponibles pour tous les comptes de cet ordinateur.",
    },
    "sr_scope_user": {"en": "Only for me", "it": "Solo per me", "es": "Solo para mí", "fr": "Seulement pour moi"},
    "sr_scope_user_desc": {
        "en": "Flatpak applications and data will be kept in the current user's Home.",
        "it": "Le applicazioni e i dati Flatpak saranno conservati nella Home dell'utente corrente.",
        "es": "Las aplicaciones y los datos de Flatpak se guardarán en la Home del usuario actual.",
        "fr": "Les applications et données Flatpak seront conservées dans le dossier personnel de l'utilisateur actuel.",
    },
    "sr_confirm_btn": {"en": "Confirm", "it": "Conferma", "es": "Confirmar", "fr": "Confirmer"},
    "sr_cancel_btn": {"en": "Cancel", "it": "Annulla", "es": "Cancelar", "fr": "Annuler"},
    "sr_install_flatseal_btn": {"en": "Install Flatseal", "it": "Installa Flatseal", "es": "Instalar Flatseal", "fr": "Installer Flatseal"},
    "sr_check_updates_btn": {"en": "Check Flatpak updates", "it": "Controlla aggiornamenti Flatpak",
                               "es": "Buscar actualizaciones de Flatpak", "fr": "Vérifier les mises à jour Flatpak"},
    "sr_remove_unused_btn": {"en": "Remove unused runtimes", "it": "Rimuovi runtime inutilizzati",
                               "es": "Eliminar runtimes sin usar", "fr": "Supprimer les runtimes inutilisés"},
    "sr_repair_flatpak_user_btn": {"en": "Repair Flatpak (personal)", "it": "Ripara Flatpak (personale)",
                                     "es": "Reparar Flatpak (personal)", "fr": "Réparer Flatpak (personnel)"},
    "sr_repair_flatpak_system_btn": {"en": "Repair Flatpak (system)", "it": "Ripara Flatpak (sistema)",
                                       "es": "Reparar Flatpak (sistema)", "fr": "Réparer Flatpak (système)"},
    "sr_updates_available": {"en": "{n} Flatpak apps have an update available", "it": "{n} app Flatpak hanno un aggiornamento disponibile",
                               "es": "{n} apps Flatpak tienen una actualización disponible", "fr": "{n} applications Flatpak ont une mise à jour disponible"},
    "sr_updates_none": {"en": "Everything is up to date.", "it": "Tutto è già aggiornato.", "es": "Todo está actualizado.", "fr": "Tout est déjà à jour."},
    "sr_apply_updates_btn": {"en": "Update", "it": "Aggiorna", "es": "Actualizar", "fr": "Mettre à jour"},

    # ── Flatseal contextual row (Phase 3, 2026-08-05) ────────────────
    "sr_flatseal_label": {"en": "Flatseal", "it": "Flatseal", "es": "Flatseal", "fr": "Flatseal"},
    "sr_flatseal_state_not_installed": {"en": "Not installed", "it": "Non installato",
                                          "es": "No instalado", "fr": "Non installé"},
    "sr_flatseal_state_installed_user": {"en": "Installed for your user", "it": "Installato per il tuo utente",
                                           "es": "Instalado para tu usuario", "fr": "Installé pour votre utilisateur"},
    "sr_flatseal_state_installed_system": {"en": "Installed for every user", "it": "Installato per tutti gli utenti",
                                             "es": "Instalado para todos los usuarios", "fr": "Installé pour tous les utilisateurs"},
    "sr_flatseal_state_installed_both": {"en": "Installed for both the user and the system",
                                           "it": "Installato per utente e sistema",
                                           "es": "Instalado para el usuario y el sistema",
                                           "fr": "Installé pour l'utilisateur et le système"},
    "sr_flatseal_state_flatpak_unavailable": {"en": "Flatpak not installed", "it": "Flatpak non installato",
                                                "es": "Flatpak no instalado", "fr": "Flatpak non installé"},
    "sr_flatseal_state_undetermined": {"en": "State could not be determined", "it": "Stato non determinabile",
                                         "es": "No se pudo determinar el estado", "fr": "État impossible à déterminer"},
    "sr_open_flatseal_btn": {"en": "Open Flatseal", "it": "Apri Flatseal", "es": "Abrir Flatseal", "fr": "Ouvrir Flatseal"},
    "sr_choose_scope_title": {"en": "Choose where to install", "it": "Scegli dove installare",
                                "es": "Elige dónde instalar", "fr": "Choisissez où installer"},
    "sr_flatseal_install_preview_system": {
        "en": "Flatseal will be installed for every user of this computer (only Flathub is set up system-wide).",
        "it": "Flatseal verrà installato per tutti gli utenti di questo computer (è configurato soltanto il Flathub di sistema).",
        "es": "Flatseal se instalará para todos los usuarios de este equipo (solo está configurado el Flathub del sistema).",
        "fr": "Flatseal sera installé pour tous les utilisateurs de cet ordinateur (seul Flathub système est configuré).",
    },
    "sr_flatseal_tooltip_needs_flatpak": {"en": "Install Flatpak first.", "it": "Installa prima Flatpak.",
                                            "es": "Instala primero Flatpak.", "fr": "Installez d'abord Flatpak."},
    "sr_flatseal_tooltip_needs_flathub": {"en": "Configure Flathub first.", "it": "Configura prima Flathub.",
                                            "es": "Configura primero Flathub.", "fr": "Configurez d'abord Flathub."},
    "sr_flatseal_tooltip_undetermined": {"en": "Current state could not be determined.", "it": "Stato attuale non determinabile.",
                                           "es": "No se pudo determinar el estado actual.", "fr": "État actuel indéterminable."},

    # ── Contextual main Flatpak button (Phase 5, 2026-08-05) ─────────
    "sr_add_flathub_btn": {"en": "Add Flathub", "it": "Aggiungi Flathub", "es": "Añadir Flathub", "fr": "Ajouter Flathub"},
    "sr_extend_system_btn": {"en": "Configure for every user too", "it": "Configura anche per tutti gli utenti",
                               "es": "Configurar también para todos los usuarios", "fr": "Configurer aussi pour tous les utilisateurs"},
    "sr_extend_system_preview": {
        "en": "Flathub will also be added system-wide, so it's available to every account on this computer. "
              "What you already have configured for your user stays unchanged.",
        "it": "Flathub verrà aggiunto anche a livello di sistema, così sarà disponibile per tutti gli account di questo computer. "
              "Quello che hai già configurato per il tuo utente resta invariato.",
        "es": "Flathub también se añadirá a nivel de sistema, para que esté disponible para todas las cuentas de este equipo. "
              "Lo que ya tienes configurado para tu usuario permanece sin cambios.",
        "fr": "Flathub sera aussi ajouté au niveau du système, pour qu'il soit disponible pour tous les comptes de cet ordinateur. "
              "Ce qui est déjà configuré pour votre utilisateur reste inchangé.",
    },
    "sr_complete_integration_btn": {"en": "Complete desktop integration", "it": "Completa integrazione desktop",
                                      "es": "Completar integración de escritorio", "fr": "Compléter l'intégration au bureau"},
    "sr_configuration_complete_badge": {"en": "Configuration complete", "it": "Configurazione completa",
                                          "es": "Configuración completa", "fr": "Configuration terminée"},
    "sr_integration_info_dialog_title": {"en": "Desktop integration", "it": "Integrazione desktop",
                                           "es": "Integración de escritorio", "fr": "Intégration au bureau"},
    "sr_integration_gap_portal_missing": {
        "en": "The desktop portal (xdg-desktop-portal) isn't installed. Flatpak apps may not be able to open file "
              "dialogs, show notifications or share the screen correctly.",
        "it": "Il portale desktop (xdg-desktop-portal) non è installato. Le app Flatpak potrebbero non riuscire ad "
              "aprire finestre di selezione file, mostrare notifiche o condividere correttamente lo schermo.",
        "es": "El portal de escritorio (xdg-desktop-portal) no está instalado. Las apps Flatpak podrían no poder "
              "abrir diálogos de archivos, mostrar notificaciones o compartir la pantalla correctamente.",
        "fr": "Le portail de bureau (xdg-desktop-portal) n'est pas installé. Les applications Flatpak pourraient ne pas "
              "pouvoir ouvrir des boîtes de dialogue de fichiers, afficher des notifications ou partager l'écran correctement.",
    },
    "sr_integration_gap_backend_missing": {
        "en": "The desktop portal is present, but the backend for your desktop environment (GTK or KDE) is missing. "
              "Some Flatpak apps may look or behave inconsistently.",
        "it": "Il portale desktop è presente, ma manca il backend per il tuo ambiente grafico (GTK o KDE). "
              "Alcune app Flatpak potrebbero avere un aspetto o un comportamento incoerente.",
        "es": "El portal de escritorio está presente, pero falta el backend para tu entorno de escritorio (GTK o KDE). "
              "Algunas apps Flatpak podrían verse o comportarse de forma incoherente.",
        "fr": "Le portail de bureau est présent, mais le backend pour votre environnement de bureau (GTK ou KDE) manque. "
              "Certaines applications Flatpak peuvent avoir un aspect ou un comportement incohérent.",
    },
    "sr_integration_gap_undetermined": {
        "en": "It wasn't possible to determine with confidence whether desktop integration is complete.",
        "it": "Non è stato possibile determinare con sicurezza se l'integrazione desktop è completa.",
        "es": "No fue posible determinar con seguridad si la integración de escritorio está completa.",
        "fr": "Il n'a pas été possible de déterminer avec certitude si l'intégration au bureau est complète.",
    },

    # ── Section C ──────────────────────────────────────────────────
    "sr_section_c_title": {"en": "Software repositories", "it": "Repository software",
                             "es": "Repositorios de software", "fr": "Dépôts logiciels"},
    "sr_rescan_btn": {"en": "Rescan", "it": "Aggiorna rilevamento", "es": "Volver a analizar", "fr": "Réanalyser"},
    "sr_summary_official": {"en": "Active official repositories", "it": "Repository ufficiali attivi",
                              "es": "Repositorios oficiales activos", "fr": "Dépôts officiels actifs"},
    "sr_summary_external": {"en": "Active external repositories", "it": "Repository esterni attivi",
                              "es": "Repositorios externos activos", "fr": "Dépôts externes actifs"},
    "sr_summary_disabled": {"en": "Disabled repositories", "it": "Repository disattivati",
                              "es": "Repositorios desactivados", "fr": "Dépôts désactivés"},
    "sr_summary_review": {"en": "Repositories to check", "it": "Repository da controllare",
                            "es": "Repositorios por revisar", "fr": "Dépôts à vérifier"},
    "sr_kind_official": {"en": "Official", "it": "Ufficiale", "es": "Oficial", "fr": "Officiel"},
    "sr_kind_universal": {"en": "Universal", "it": "Universale", "es": "Universal", "fr": "Universel"},
    "sr_kind_community": {"en": "Community", "it": "Comunitario", "es": "Comunitario", "fr": "Communautaire"},
    "sr_kind_external": {"en": "External", "it": "Esterno", "es": "Externo", "fr": "Externe"},
    "sr_kind_needs_review": {"en": "To check", "it": "Da controllare", "es": "Por revisar", "fr": "À vérifier"},
    "sr_kind_unknown": {"en": "Unknown", "it": "Sconosciuto", "es": "Desconocido", "fr": "Inconnu"},
    "sr_repo_source_file": {"en": "Configuration file", "it": "File di configurazione", "es": "Archivo de configuración", "fr": "Fichier de configuration"},
    "sr_repo_address": {"en": "Address", "it": "Indirizzo", "es": "Dirección", "fr": "Adresse"},
    "sr_repo_signed_yes": {"en": "Signature present", "it": "Firma presente", "es": "Firma presente", "fr": "Signature présente"},
    "sr_repo_signed_no": {"en": "No signature check", "it": "Nessun controllo firma", "es": "Sin comprobación de firma", "fr": "Aucune vérification de signature"},
    "sr_repo_disabled": {"en": "Disabled", "it": "Disattivato", "es": "Desactivado", "fr": "Désactivé"},
    "sr_no_repos_found": {"en": "No repositories detected.", "it": "Nessun repository rilevato.", "es": "No se detectaron repositorios.", "fr": "Aucun dépôt détecté."},
    "sr_repo_suites": {"en": "Suites", "it": "Suite", "es": "Suites", "fr": "Suites"},
    "sr_repo_components": {"en": "Components", "it": "Componenti", "es": "Componentes", "fr": "Composants"},
    "sr_repo_duplicate_files": {"en": "Also defined in", "it": "Definito anche in",
                                  "es": "También definido en", "fr": "Aussi défini dans"},
    # 2026-08-05: warning codes were being rendered as raw internal
    # strings (e.g. literally "gpgcheck_disabled") — every code this
    # module can produce is mapped to a real sentence here, and
    # anything unmapped still falls back to a translated generic one
    # instead of ever showing the bare code.
    "sr_warning_no_host": {"en": "The address could not be read.", "it": "Non è stato possibile leggere l'indirizzo.",
                             "es": "No se pudo leer la dirección.", "fr": "L'adresse n'a pas pu être lue."},
    "sr_warning_no_uri": {"en": "No address is configured for this repository.",
                            "it": "Non è configurato alcun indirizzo per questo repository.",
                            "es": "No hay ninguna dirección configurada para este repositorio.",
                            "fr": "Aucune adresse n'est configurée pour ce dépôt."},
    "sr_warning_gpgcheck_disabled": {"en": "Signature verification is disabled for this repository.",
                                       "it": "Il controllo della firma è disattivato per questo repository.",
                                       "es": "La verificación de firma está desactivada para este repositorio.",
                                       "fr": "La vérification de signature est désactivée pour ce dépôt."},
    "sr_warning_signature_unspecified": {"en": "No signing key is specified.", "it": "Non è specificata alcuna chiave di firma.",
                                           "es": "No se especifica ninguna clave de firma.", "fr": "Aucune clé de signature n'est spécifiée."},
    "sr_warning_aur_not_a_repo": {"en": "AUR is not a regular binary repository — treat it separately.",
                                    "it": "AUR non è un normale repository di pacchetti binari — va trattato separatamente.",
                                    "es": "AUR no es un repositorio binario normal — debe tratarse por separado.",
                                    "fr": "AUR n'est pas un dépôt binaire classique — à traiter séparément."},
    "sr_warning_duplicate_config": {"en": "Duplicate configuration — the same repository is defined more than once.",
                                      "it": "Configurazione duplicata — lo stesso repository è definito più di una volta.",
                                      "es": "Configuración duplicada — el mismo repositorio está definido más de una vez.",
                                      "fr": "Configuration dupliquée — le même dépôt est défini plusieurs fois."},
    "sr_warning_unknown": {"en": "This repository needs a manual check.", "it": "Questo repository richiede un controllo manuale.",
                             "es": "Este repositorio requiere una comprobación manual.", "fr": "Ce dépôt nécessite une vérification manuelle."},

    "sr_additional_title": {"en": "Additional repositories", "it": "Repository aggiuntivi",
                              "es": "Repositorios adicionales", "fr": "Dépôts supplémentaires"},
    "sr_additional_guided_desc": {
        "en": "These are verified and can be enabled directly.",
        "it": "Questi sono verificati e possono essere abilitati direttamente.",
        "es": "Estos están verificados y se pueden habilitar directamente.",
        "fr": "Ceux-ci sont vérifiés et peuvent être activés directement.",
    },
    "sr_additional_advanced_desc": {
        "en": "Advanced level — information only for now. Enabling these manually carries more risk.",
        "it": "Livello avanzato — solo informativo per ora. Abilitarli manualmente comporta più rischio.",
        "es": "Nivel avanzado — solo informativo por ahora. Habilitarlos manualmente implica más riesgo.",
        "fr": "Niveau avancé — informatif seulement pour l'instant. Les activer manuellement comporte plus de risques.",
    },
    "sr_enable_btn": {"en": "Enable", "it": "Abilita", "es": "Habilitar", "fr": "Activer"},
    "sr_disable_btn": {"en": "Disable", "it": "Disabilita", "es": "Deshabilitar", "fr": "Désactiver"},
    "sr_recipe_conflict_note": {"en": "Conflicts with an already-enabled repository.", "it": "In conflitto con un repository già abilitato.",
                                  "es": "Entra en conflicto con un repositorio ya habilitado.", "fr": "Entre en conflit avec un dépôt déjà activé."},
    "sr_recipe_enable_preview_title": {"en": "Preview", "it": "Anteprima", "es": "Vista previa", "fr": "Aperçu"},
    "sr_recipe_files_involved": {"en": "Files involved", "it": "File coinvolti", "es": "Archivos implicados", "fr": "Fichiers concernés"},
    "sr_recipe_risk": {"en": "Risk level", "it": "Livello di rischio", "es": "Nivel de riesgo", "fr": "Niveau de risque"},
    "sr_recipe_source": {"en": "Source", "it": "Fonte", "es": "Fuente", "fr": "Source"},

    # ── Repository recipe catalogue (Phase 7, 2026-08-05) ────────────
    # 2026-08-05: these 24 keys were referenced by
    # core.software_repo.repo_recipes.RECIPES (name_key/description_key/
    # source_key/verify_method_key) but were never actually added here
    # — the raw key names (e.g. "recipe_ubuntu_universe_name") were
    # rendering directly in "Repository aggiuntivi" instead of real
    # text. See _recipe_text() below for the safety-net fallback that
    # now also covers any future recipe added without translations.
    "recipe_ubuntu_universe_name": {"en": "Ubuntu Universe", "it": "Ubuntu Universe", "es": "Ubuntu Universe", "fr": "Ubuntu Universe"},
    "recipe_ubuntu_universe_desc": {
        "en": "Community-maintained software, still hosted by Ubuntu but without official support.",
        "it": "Software mantenuto dalla comunità, ospitato comunque da Ubuntu ma senza supporto ufficiale.",
        "es": "Software mantenido por la comunidad, alojado por Ubuntu pero sin soporte oficial.",
        "fr": "Logiciels maintenus par la communauté, hébergés par Ubuntu mais sans support officiel.",
    },
    "recipe_ubuntu_multiverse_name": {"en": "Ubuntu Multiverse", "it": "Ubuntu Multiverse", "es": "Ubuntu Multiverse", "fr": "Ubuntu Multiverse"},
    "recipe_ubuntu_multiverse_desc": {
        "en": "Software restricted by copyright or legal reasons in some places — not officially supported.",
        "it": "Software con restrizioni legali o di copyright in alcuni paesi — non supportato ufficialmente.",
        "es": "Software con restricciones legales o de derechos de autor en algunos lugares — sin soporte oficial.",
        "fr": "Logiciels soumis à des restrictions légales ou de droits d'auteur dans certains pays — non pris en charge officiellement.",
    },
    "recipe_debian_backports_name": {"en": "Debian Backports", "it": "Debian Backports", "es": "Debian Backports", "fr": "Debian Backports"},
    "recipe_debian_backports_desc": {
        "en": "Newer package versions, rebuilt for your current Debian release. Less tested than the stable ones.",
        "it": "Versioni più recenti dei pacchetti, ricompilate per la tua versione Debian attuale. Meno testate di quelle stabili.",
        "es": "Versiones más recientes de los paquetes, recompiladas para tu versión actual de Debian. Menos probadas que las estables.",
        "fr": "Versions plus récentes des paquets, recompilées pour votre version Debian actuelle. Moins testées que les versions stables.",
    },
    "recipe_ubuntu_backports_name": {"en": "Ubuntu Backports", "it": "Ubuntu Backports", "es": "Ubuntu Backports", "fr": "Ubuntu Backports"},
    "recipe_ubuntu_backports_desc": {
        "en": "Newer package versions, rebuilt for your current Ubuntu release. Less tested than the stable ones.",
        "it": "Versioni più recenti dei pacchetti, ricompilate per la tua versione Ubuntu attuale. Meno testate di quelle stabili.",
        "es": "Versiones más recientes de los paquetes, recompiladas para tu versión actual de Ubuntu. Menos probadas que las estables.",
        "fr": "Versions plus récentes des paquets, recompilées pour votre version Ubuntu actuelle. Moins testées que les versions stables.",
    },
    "recipe_rpmfusion_name": {"en": "RPM Fusion", "it": "RPM Fusion", "es": "RPM Fusion", "fr": "RPM Fusion"},
    "recipe_rpmfusion_desc": {
        "en": "The community repository Fedora uses for multimedia codecs and other software it can't ship itself.",
        "it": "Il repository comunitario che Fedora usa per codec multimediali e altro software che non può distribuire direttamente.",
        "es": "El repositorio comunitario que Fedora usa para códecs multimedia y otro software que no puede distribuir directamente.",
        "fr": "Le dépôt communautaire utilisé par Fedora pour les codecs multimédias et d'autres logiciels qu'il ne peut pas distribuer lui-même.",
    },
    "recipe_negativo17_name": {"en": "Negativo17", "it": "Negativo17", "es": "Negativo17", "fr": "Negativo17"},
    "recipe_negativo17_desc": {
        "en": "An independent, community-run repository for Fedora, mainly for multimedia and driver packages.",
        "it": "Un repository indipendente e comunitario per Fedora, soprattutto per pacchetti multimediali e driver.",
        "es": "Un repositorio independiente y comunitario para Fedora, sobre todo para paquetes multimedia y controladores.",
        "fr": "Un dépôt indépendant et communautaire pour Fedora, surtout pour les paquets multimédias et les pilotes.",
    },
    "recipe_packman_name": {"en": "Packman", "it": "Packman", "es": "Packman", "fr": "Packman"},
    "recipe_packman_desc": {
        "en": "The community repository openSUSE users rely on for multimedia codecs and similar software.",
        "it": "Il repository comunitario a cui gli utenti openSUSE si affidano per codec multimediali e software simile.",
        "es": "El repositorio comunitario en el que confían los usuarios de openSUSE para códecs multimedia y software similar.",
        "fr": "Le dépôt communautaire utilisé par les utilisateurs d'openSUSE pour les codecs multimédias et logiciels similaires.",
    },
    "recipe_debian_multimedia_name": {"en": "Debian Multimedia", "it": "Debian Multimedia", "es": "Debian Multimedia", "fr": "Debian Multimedia"},
    "recipe_debian_multimedia_desc": {
        "en": "A third-party repository for multimedia codecs Debian can't ship itself.",
        "it": "Un repository di terze parti per codec multimediali che Debian non può distribuire direttamente.",
        "es": "Un repositorio de terceros para códecs multimedia que Debian no puede distribuir directamente.",
        "fr": "Un dépôt tiers pour les codecs multimédias que Debian ne peut pas distribuer lui-même.",
    },
    "recipe_ppa_name": {"en": "Personal Package Archive (PPA)", "it": "Personal Package Archive (PPA)",
                          "es": "Personal Package Archive (PPA)", "fr": "Personal Package Archive (PPA)"},
    "recipe_ppa_desc": {
        "en": "A repository published by an individual developer, not reviewed by Ubuntu — quality and safety vary widely.",
        "it": "Un repository pubblicato da un singolo sviluppatore, non verificato da Ubuntu — qualità e sicurezza variano molto.",
        "es": "Un repositorio publicado por un desarrollador individual, no revisado por Ubuntu — la calidad y seguridad varían mucho.",
        "fr": "Un dépôt publié par un développeur individuel, non vérifié par Ubuntu — la qualité et la sécurité varient beaucoup.",
    },
    "recipe_chaotic_aur_name": {"en": "Chaotic-AUR", "it": "Chaotic-AUR", "es": "Chaotic-AUR", "fr": "Chaotic-AUR"},
    "recipe_chaotic_aur_desc": {
        "en": "A community repository of prebuilt AUR packages for Arch — convenient, but outside Arch's own review process.",
        "it": "Un repository comunitario di pacchetti AUR precompilati per Arch — comodo, ma fuori dal processo di revisione di Arch.",
        "es": "Un repositorio comunitario de paquetes AUR precompilados para Arch — cómodo, pero fuera del proceso de revisión de Arch.",
        "fr": "Un dépôt communautaire de paquets AUR précompilés pour Arch — pratique, mais hors du processus de vérification d'Arch.",
    },
    "recipe_obs_name": {"en": "openSUSE Build Service (OBS)", "it": "openSUSE Build Service (OBS)",
                          "es": "openSUSE Build Service (OBS)", "fr": "openSUSE Build Service (OBS)"},
    "recipe_obs_desc": {
        "en": "A platform where anyone can publish openSUSE packages — quality and safety vary by project.",
        "it": "Una piattaforma dove chiunque può pubblicare pacchetti openSUSE — qualità e sicurezza variano da progetto a progetto.",
        "es": "Una plataforma donde cualquiera puede publicar paquetes de openSUSE — la calidad y seguridad varían según el proyecto.",
        "fr": "Une plateforme où chacun peut publier des paquets openSUSE — la qualité et la sécurité varient selon le projet.",
    },

    "recipe_source_ubuntu_official": {"en": "Official Ubuntu component", "it": "Componente ufficiale Ubuntu",
                                        "es": "Componente oficial de Ubuntu", "fr": "Composant officiel Ubuntu"},
    "recipe_source_debian_official": {"en": "Official Debian archive", "it": "Archivio ufficiale Debian",
                                        "es": "Archivo oficial de Debian", "fr": "Archive officielle Debian"},
    "recipe_source_rpmfusion": {"en": "rpmfusion.org", "it": "rpmfusion.org", "es": "rpmfusion.org", "fr": "rpmfusion.org"},
    "recipe_source_negativo17": {"en": "negativo17.org", "it": "negativo17.org", "es": "negativo17.org", "fr": "negativo17.org"},
    "recipe_source_packman": {"en": "packman.links2linux.de", "it": "packman.links2linux.de",
                                "es": "packman.links2linux.de", "fr": "packman.links2linux.de"},
    "recipe_source_debian_multimedia": {"en": "deb-multimedia.org", "it": "deb-multimedia.org",
                                          "es": "deb-multimedia.org", "fr": "deb-multimedia.org"},
    "recipe_source_ppa": {"en": "launchpad.net (individual developer)", "it": "launchpad.net (sviluppatore individuale)",
                            "es": "launchpad.net (desarrollador individual)", "fr": "launchpad.net (développeur individuel)"},
    "recipe_source_chaotic_aur": {"en": "chaotic.cx", "it": "chaotic.cx", "es": "chaotic.cx", "fr": "chaotic.cx"},
    "recipe_source_obs": {"en": "build.opensuse.org", "it": "build.opensuse.org",
                            "es": "build.opensuse.org", "fr": "build.opensuse.org"},

    "recipe_verify_apt_update": {"en": "Verified by updating the package index (apt update).",
                                   "it": "Verificato aggiornando l'indice dei pacchetti (apt update).",
                                   "es": "Verificado actualizando el índice de paquetes (apt update).",
                                   "fr": "Vérifié en mettant à jour l'index des paquets (apt update)."},
    "recipe_verify_dnf_repolist": {"en": "Verified by checking the repository list (dnf repolist).",
                                     "it": "Verificato controllando l'elenco dei repository (dnf repolist).",
                                     "es": "Verificado comprobando la lista de repositorios (dnf repolist).",
                                     "fr": "Vérifié en consultant la liste des dépôts (dnf repolist)."},
    "recipe_verify_zypper_lr": {"en": "Verified by checking the repository list (zypper lr).",
                                  "it": "Verificato controllando l'elenco dei repository (zypper lr).",
                                  "es": "Verificado comprobando la lista de repositorios (zypper lr).",
                                  "fr": "Vérifié en consultant la liste des dépôts (zypper lr)."},
    "recipe_verify_pacman_conf": {"en": "Verified by checking the Pacman configuration.",
                                    "it": "Verificato controllando la configurazione di Pacman.",
                                    "es": "Verificado comprobando la configuración de Pacman.",
                                    "fr": "Vérifié en consultant la configuration de Pacman."},

    # ── Section D ─────────────────────────────────────────────────
    "sr_section_d_title": {"en": "Package health", "it": "Salute pacchetti", "es": "Salud de los paquetes", "fr": "Santé des paquets"},
    "sr_section_d_desc": {
        "en": "Check dependencies, incomplete operations and unused components.",
        "it": "Controlla dipendenze, operazioni incomplete e componenti inutilizzati.",
        "es": "Comprueba dependencias, operaciones incompletas y componentes sin usar.",
        "fr": "Vérifiez les dépendances, les opérations incomplètes et les composants inutilisés.",
    },
    "sr_scan_system_btn": {"en": "Check the system", "it": "Controlla il sistema", "es": "Comprobar el sistema", "fr": "Vérifier le système"},
    "sr_health_broken": {"en": "Broken packages", "it": "Pacchetti interrotti", "es": "Paquetes rotos", "fr": "Paquets cassés"},
    "sr_health_orphans": {"en": "Orphan packages", "it": "Pacchetti orfani", "es": "Paquetes huérfanos", "fr": "Paquets orphelins"},
    "sr_health_cache": {"en": "Reclaimable cache", "it": "Cache recuperabile", "es": "Caché recuperable", "fr": "Cache récupérable"},
    "sr_health_unused_flatpak": {"en": "Unused Flatpak runtimes", "it": "Runtime Flatpak inutilizzati",
                                    "es": "Runtimes de Flatpak sin usar", "fr": "Runtimes Flatpak inutilisés"},
    "sr_health_all_good": {"en": "No problems detected.", "it": "Nessun problema rilevato.", "es": "No se detectaron problemas.", "fr": "Aucun problème détecté."},
    "sr_health_prescan_hint": {"en": "Run the check first to see which actions are available.",
                                 "it": "Esegui prima il controllo per vedere le azioni disponibili.",
                                 "es": "Ejecuta primero la comprobación para ver las acciones disponibles.",
                                 "fr": "Lancez d'abord la vérification pour voir les actions disponibles."},
    "sr_health_action_needs_scan_tooltip": {"en": "Run «Check the system» first.",
                                              "it": "Esegui prima «Controlla il sistema».",
                                              "es": "Ejecuta primero «Comprobar el sistema».",
                                              "fr": "Lancez d'abord «Vérifier le système»."},
    "sr_health_no_broken_tooltip": {"en": "No broken packages were found — nothing to repair.",
                                      "it": "Non sono stati trovati pacchetti interrotti — niente da riparare.",
                                      "es": "No se encontraron paquetes rotos — nada que reparar.",
                                      "fr": "Aucun paquet cassé trouvé — rien à réparer."},
    "sr_health_no_orphans_tooltip": {"en": "No unused components were found.",
                                       "it": "Non sono stati trovati componenti inutilizzati.",
                                       "es": "No se encontraron componentes sin usar.",
                                       "fr": "Aucun composant inutilisé trouvé."},
    "sr_repair_deps_btn": {"en": "Repair", "it": "Ripara", "es": "Reparar", "fr": "Réparer"},
    "sr_remove_orphans_btn": {"en": "Remove unused components", "it": "Rimuovi componenti inutilizzati",
                                "es": "Eliminar componentes sin usar", "fr": "Supprimer les composants inutilisés"},
    "sr_update_indexes_btn": {"en": "Update package indexes", "it": "Aggiorna gli indici", "es": "Actualizar índices", "fr": "Mettre à jour les index"},
    "sr_clean_cache_btn": {"en": "Clean package cache", "it": "Pulisci cache pacchetti", "es": "Limpiar caché de paquetes", "fr": "Nettoyer le cache des paquets"},
    "sr_repair_flatpak_btn": {"en": "Repair Flatpak", "it": "Ripara Flatpak", "es": "Reparar Flatpak", "fr": "Réparer Flatpak"},
    "sr_action_preview_title": {"en": "Before continuing", "it": "Prima di continuare", "es": "Antes de continuar", "fr": "Avant de continuer"},
    "sr_orphans_preview": {
        "en": "{n} packages that no longer have a dependent will be removed.\nNo program you use directly will be removed.",
        "it": "Verranno rimossi {n} pacchetti che non hanno più nulla che li richieda.\nNon verrà rimosso alcun programma che usi direttamente.",
        "es": "Se eliminarán {n} paquetes que ya no tienen nada que los requiera.\nNo se eliminará ningún programa que uses directamente.",
        "fr": "{n} paquets qui ne sont plus requis par rien seront supprimés.\nAucun programme que vous utilisez directement ne sera supprimé.",
    },
    "sr_repair_preview": {
        "en": "The system's own package manager will try to fix incomplete installations and missing dependencies.\nNo repository will be modified.",
        "it": "Verrà usato il gestore pacchetti del sistema per correggere installazioni incomplete e dipendenze mancanti.\nNessun repository verrà modificato.",
        "es": "Se usará el propio gestor de paquetes del sistema para corregir instalaciones incompletas y dependencias faltantes.\nNo se modificará ningún repositorio.",
        "fr": "Le gestionnaire de paquets du système tentera de corriger les installations incomplètes et les dépendances manquantes.\nAucun dépôt ne sera modifié.",
    },
    "sr_cache_preview": {
        "en": "Downloaded package files kept in the local cache will be deleted.\nNo installed program will be removed.",
        "it": "Verranno eliminati i file dei pacchetti scaricati conservati nella cache locale.\nNon verrà rimosso alcun programma installato.",
        "es": "Se eliminarán los archivos de paquetes descargados que se conservan en la caché local.\nNo se eliminará ningún programa instalado.",
        "fr": "Les fichiers de paquets téléchargés conservés dans le cache local seront supprimés.\nAucun programme installé ne sera supprimé.",
    },

    "sr_result_ok": {"en": "Done.", "it": "Operazione completata.", "es": "Operación completada.", "fr": "Opération terminée."},
    "sr_result_failed": {"en": "The operation could not be completed.", "it": "Non è stato possibile completare l'operazione.",
                           "es": "No se pudo completar la operación.", "fr": "L'opération n'a pas pu être terminée."},
    "sr_reboot_note": {"en": "A restart is recommended.", "it": "Si consiglia il riavvio.", "es": "Se recomienda reiniciar.", "fr": "Un redémarrage est recommandé."},
}
for _k, _v in _page_strings.items():
    _i18n_mod._strings[_k] = _v

# core.software_repo.flatpak_manager / repo_recipes / package_health
# friendly_message keys — never referenced by T() above, added here so
# they show up in the same translation table instead of a second file.
_backend_strings = {
    "flatpak_already_installed": {"en": "Flatpak was already installed.", "it": "Flatpak era già installato.",
                                    "es": "Flatpak ya estaba instalado.", "fr": "Flatpak était déjà installé."},
    "flatpak_manual_procedure_required": {
        "en": "This system is immutable/transactional — automatic installation isn't supported yet here.",
        "it": "Questo sistema è immutabile/transazionale — l'installazione automatica non è ancora supportata qui.",
        "es": "Este sistema es inmutable/transaccional — la instalación automática aún no está soportada aquí.",
        "fr": "Ce système est immuable/transactionnel — l'installation automatique n'est pas encore prise en charge ici.",
    },
    "flatpak_family_unresolved": {"en": "Operation not yet supported safely on this system.",
                                    "it": "Operazione non ancora supportata in sicurezza su questo sistema.",
                                    "es": "Operación aún no soportada de forma segura en este sistema.",
                                    "fr": "Opération pas encore prise en charge en toute sécurité sur ce système."},
    "flatpak_install_success": {"en": "Flatpak was installed.", "it": "Flatpak è stato installato.",
                                  "es": "Flatpak fue instalado.", "fr": "Flatpak a été installé."},
    "flatpak_install_failed": {"en": "Flatpak could not be installed.", "it": "Non è stato possibile installare Flatpak.",
                                 "es": "No se pudo instalar Flatpak.", "fr": "Flatpak n'a pas pu être installé."},
    "flathub_added_success": {"en": "Flathub was configured.", "it": "Flathub è stato configurato.",
                                "es": "Flathub fue configurado.", "fr": "Flathub a été configuré."},
    "flathub_added_failed": {"en": "Flathub could not be configured.", "it": "Non è stato possibile configurare Flathub.",
                               "es": "No se pudo configurar Flathub.", "fr": "Flathub n'a pas pu être configuré."},
    "flatseal_install_success": {"en": "Flatseal was installed.", "it": "Flatseal è stato installato.",
                                   "es": "Flatseal fue instalado.", "fr": "Flatseal a été installé."},
    "flatseal_already_installed": {"en": "Flatseal is already installed.", "it": "Flatseal è già installato.",
                                     "es": "Flatseal ya está instalado.", "fr": "Flatseal est déjà installé."},
    "app_already_installed": {"en": "The requested component is already installed.",
                                "it": "Il componente richiesto è già installato.",
                                "es": "El componente solicitado ya está instalado.",
                                "fr": "Le composant demandé est déjà installé."},
    "app_install_success": {"en": "Installation completed.", "it": "Installazione completata.",
                              "es": "Instalación completada.", "fr": "Installation terminée."},
    # 2026-08-05: structured error taxonomy for every flatpak write
    # action (install/update/repair/remote-add) — see
    # flatpak_manager._classify_flatpak_error(). Replaces one generic
    # "operation failed" text for every possible cause.
    "flatpak_err_not_installed_yet": {"en": "Install Flatpak first.", "it": "Installa prima Flatpak.",
                                        "es": "Instala primero Flatpak.", "fr": "Installez d'abord Flatpak."},
    "flatpak_err_flathub_not_configured": {"en": "Configure Flathub first.", "it": "Configura prima Flathub.",
                                             "es": "Configura primero Flathub.", "fr": "Configurez d'abord Flathub."},
    "flatpak_err_install_failed": {"en": "The installation could not be completed.",
                                     "it": "Non è stato possibile completare l'installazione.",
                                     "es": "No se pudo completar la instalación.",
                                     "fr": "L'installation n'a pas pu être terminée."},
    "flatpak_err_auth_cancelled": {"en": "Authentication was cancelled.", "it": "L'autenticazione è stata annullata.",
                                     "es": "Se canceló la autenticación.", "fr": "L'authentification a été annulée."},
    "flatpak_err_permission_denied": {"en": "Authorization was denied.", "it": "L'autorizzazione è stata negata.",
                                        "es": "Se denegó la autorización.", "fr": "L'autorisation a été refusée."},
    "flatpak_err_no_connection": {"en": "No connection available, or the connection is unstable.",
                                    "it": "Connessione assente o instabile.",
                                    "es": "Conexión ausente o inestable.",
                                    "fr": "Connexion absente ou instable."},
    "flatpak_err_package_not_found": {"en": "The requested package was not found in the repository.",
                                        "it": "Il pacchetto richiesto non è stato trovato nel repository.",
                                        "es": "No se encontró el paquete solicitado en el repositorio.",
                                        "fr": "Le paquet demandé est introuvable dans le dépôt."},
    "flatpak_err_operation_cancelled": {"en": "Operation cancelled.", "it": "Operazione annullata.",
                                          "es": "Operación cancelada.", "fr": "Opération annulée."},
    "flatpak_err_verification_failed": {
        "en": "The installation seemed to succeed, but the follow-up check did not confirm the expected state.",
        "it": "L'installazione sembrava riuscita, ma la verifica successiva non ha confermato lo stato atteso.",
        "es": "La instalación parecía haber funcionado, pero la comprobación posterior no confirmó el estado esperado.",
        "fr": "L'installation semblait réussie, mais la vérification suivante n'a pas confirmé l'état attendu.",
    },
    "flatpak_update_success": {"en": "Flatpak apps were updated.", "it": "Le app Flatpak sono state aggiornate.",
                                 "es": "Las apps Flatpak fueron actualizadas.", "fr": "Les applications Flatpak ont été mises à jour."},
    "flatpak_update_failed": {"en": "The update could not be completed.", "it": "Non è stato possibile completare l'aggiornamento.",
                                "es": "No se pudo completar la actualización.", "fr": "La mise à jour n'a pas pu être terminée."},
    "flatpak_unused_removed_success": {"en": "Unused runtimes were removed.", "it": "I runtime inutilizzati sono stati rimossi.",
                                         "es": "Se eliminaron los runtimes sin usar.", "fr": "Les runtimes inutilisés ont été supprimés."},
    "flatpak_unused_removed_failed": {"en": "Removal could not be completed.", "it": "Non è stato possibile completare la rimozione.",
                                        "es": "No se pudo completar la eliminación.", "fr": "La suppression n'a pas pu être terminée."},
    "flatpak_repair_success": {"en": "Flatpak was repaired.", "it": "Flatpak è stato riparato.",
                                 "es": "Flatpak fue reparado.", "fr": "Flatpak a été réparé."},
    "flatpak_repair_failed": {"en": "Repair could not be completed.", "it": "Non è stato possibile completare la riparazione.",
                                "es": "No se pudo completar la reparación.", "fr": "La réparation n'a pas pu être terminée."},

    "recipe_advanced_info_only": {"en": "Information only for now — not yet enabled automatically.",
                                    "it": "Solo informativo per ora — non ancora abilitabile automaticamente.",
                                    "es": "Solo informativo por ahora — aún no se puede habilitar automáticamente.",
                                    "fr": "Informatif seulement pour l'instant — pas encore activable automatiquement."},
    "recipe_distro_unverified": {"en": "The system couldn't be verified with confidence — no repository will be changed.",
                                   "it": "Il sistema non è stato verificato con sicurezza — nessun repository verrà modificato.",
                                   "es": "El sistema no pudo verificarse con confianza — no se modificará ningún repositorio.",
                                   "fr": "Le système n'a pas pu être vérifié avec certitude — aucun dépôt ne sera modifié."},
    "recipe_not_compatible": {"en": "Not compatible with this system.", "it": "Non compatibile con questo sistema.",
                                "es": "No compatible con este sistema.", "fr": "Non compatible avec ce système."},
    "recipe_conflict_detected": {"en": "A conflict with an already-enabled repository was detected.",
                                   "it": "È stato rilevato un conflitto con un repository già abilitato.",
                                   "es": "Se detectó un conflicto con un repositorio ya habilitado.",
                                   "fr": "Un conflit avec un dépôt déjà activé a été détecté."},
    "recipe_codename_unresolved": {"en": "The release codename could not be determined safely.",
                                     "it": "Non è stato possibile determinare in sicurezza il nome in codice della release.",
                                     "es": "No se pudo determinar de forma segura el nombre en clave de la versión.",
                                     "fr": "Le nom de code de la version n'a pas pu être déterminé en toute sécurité."},
    "recipe_version_unresolved": {"en": "The system version could not be determined safely.",
                                    "it": "Non è stato possibile determinare in sicurezza la versione del sistema.",
                                    "es": "No se pudo determinar de forma segura la versión del sistema.",
                                    "fr": "La version du système n'a pas pu être déterminée en toute sécurité."},
    "recipe_not_implemented": {"en": "Not yet supported safely.", "it": "Non ancora supportato in sicurezza.",
                                 "es": "Aún no soportado de forma segura.", "fr": "Pas encore pris en charge en toute sécurité."},
    "recipe_unknown": {"en": "Unknown repository.", "it": "Repository sconosciuto.", "es": "Repositorio desconocido.", "fr": "Dépôt inconnu."},
    "recipe_enable_success": {"en": "The repository was enabled.", "it": "Il repository è stato abilitato.",
                                "es": "El repositorio fue habilitado.", "fr": "Le dépôt a été activé."},
    "recipe_enable_failed": {"en": "The repository could not be enabled.", "it": "Non è stato possibile abilitare il repository.",
                               "es": "No se pudo habilitar el repositorio.", "fr": "Le dépôt n'a pas pu être activé."},
    "recipe_disable_success": {"en": "The repository was disabled.", "it": "Il repository è stato disabilitato.",
                                 "es": "El repositorio fue deshabilitado.", "fr": "Le dépôt a été désactivé."},
    "recipe_disable_failed": {"en": "The repository could not be disabled.", "it": "Non è stato possibile disabilitare il repository.",
                                "es": "No se pudo deshabilitar el repositorio.", "fr": "Le dépôt n'a pas pu être désactivé."},

    "health_scan_family_unsupported": {"en": "Scan not yet supported on this system.", "it": "Scansione non ancora supportata su questo sistema.",
                                         "es": "Análisis aún no soportado en este sistema.", "fr": "Analyse pas encore prise en charge sur ce système."},
    "health_action_family_unsupported": {"en": "Action not yet supported on this system.", "it": "Azione non ancora supportata su questo sistema.",
                                           "es": "Acción aún no soportada en este sistema.", "fr": "Action pas encore prise en charge sur ce système."},
    "health_repair_success": {"en": "Dependencies were repaired.", "it": "Le dipendenze sono state riparate.",
                                "es": "Las dependencias fueron reparadas.", "fr": "Les dépendances ont été réparées."},
    "health_repair_failed": {"en": "Repair could not be completed.", "it": "Non è stato possibile completare la riparazione.",
                               "es": "No se pudo completar la reparación.", "fr": "La réparation n'a pas pu être terminée."},
    "health_orphans_removed_success": {"en": "Unused components were removed.", "it": "I componenti inutilizzati sono stati rimossi.",
                                         "es": "Se eliminaron los componentes sin usar.", "fr": "Les composants inutilisés ont été supprimés."},
    "health_orphans_removed_failed": {"en": "Removal could not be completed.", "it": "Non è stato possibile completare la rimozione.",
                                        "es": "No se pudo completar la eliminación.", "fr": "La suppression n'a pas pu être terminée."},
    # 2026-08-04: `zypper packages --orphaned` only ever LISTS candidates
    # — there is no official, safe one-step zypper removal command like
    # apt-get/dnf autoremove or pacman -Rns. Running it and reporting
    # "success" was a false success (nothing was actually removed).
    # Until a safe procedure exists, openSUSE gets this honest message
    # instead of a fabricated removal.
    "health_orphans_opensuse_not_supported": {
        "en": "Automatic removal is not supported.", "it": "Rimozione automatica non supportata.",
        "es": "La eliminación automática no es compatible.", "fr": "La suppression automatique n'est pas prise en charge."},
    "health_indexes_updated_success": {"en": "Package indexes were updated.", "it": "Gli indici dei pacchetti sono stati aggiornati.",
                                         "es": "Se actualizaron los índices de paquetes.", "fr": "Les index de paquets ont été mis à jour."},
    "health_indexes_updated_failed": {"en": "The update could not be completed.", "it": "Non è stato possibile completare l'aggiornamento.",
                                        "es": "No se pudo completar la actualización.", "fr": "La mise à jour n'a pas pu être terminée."},
    "health_cache_cleaned_success": {"en": "The package cache was cleaned.", "it": "La cache dei pacchetti è stata pulita.",
                                       "es": "Se limpió la caché de paquetes.", "fr": "Le cache des paquets a été nettoyé."},
    "health_cache_cleaned_failed": {"en": "Cleanup could not be completed.", "it": "Non è stato possibile completare la pulizia.",
                                      "es": "No se pudo completar la limpieza.", "fr": "Le nettoyage n'a pas pu être terminé."},
    "engine_operation_unknown": {"en": "Unrecognized operation.", "it": "Operazione non riconosciuta.",
                                   "es": "Operación no reconocida.", "fr": "Opération non reconnue."},
}
for _k, _v in _backend_strings.items():
    _i18n_mod._strings[_k] = _v


_SYSTEM_TYPE_KEYS = {
    dp.SYSTEM_TRADITIONAL: "sr_system_type_traditional",
    dp.SYSTEM_IMMUTABLE: "sr_system_type_immutable",
    dp.SYSTEM_TRANSACTIONAL: "sr_system_type_transactional",
    dp.SYSTEM_UNKNOWN: "sr_system_type_unknown",
}

_RECIPE_KIND_KEYS = {
    rsc.KIND_OFFICIAL: "sr_kind_official", rsc.KIND_UNIVERSAL: "sr_kind_universal",
    rsc.KIND_COMMUNITY: "sr_kind_community", rsc.KIND_EXTERNAL: "sr_kind_external",
    rsc.KIND_NEEDS_REVIEW: "sr_kind_needs_review", rsc.KIND_UNKNOWN: "sr_kind_unknown",
}

_KIND_PILL_VARIANT = {
    rsc.KIND_OFFICIAL: "success", rsc.KIND_UNIVERSAL: "success", rsc.KIND_COMMUNITY: "neutral",
    rsc.KIND_EXTERNAL: "warning", rsc.KIND_NEEDS_REVIEW: "danger", rsc.KIND_UNKNOWN: "neutral",
}


def _recipe_text(key: str) -> str:
    """Safety net for the repository-recipe catalogue's dynamically
    referenced keys (RepoRecipe.name_key/description_key/source_key —
    T() is always called with a variable here, not a string literal,
    so the repo-wide static "every T(...) call has a defined key"
    check can't see these at all). If a key is ever missing — e.g. a
    future recipe added without full translations — this shows a
    readable derived label instead of the raw internal key name."""
    text = T(key)
    if text != key:
        return text
    return key.replace("recipe_", "").replace("_name", "").replace("_desc", "").replace("_", " ").strip().capitalize()


def _info_row(label_key: str, value: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=T(label_key))
    row.set_activatable(False)
    value_lbl = Gtk.Label(label=value, valign=Gtk.Align.CENTER)
    value_lbl.add_css_class("sysinfo-value")
    row.add_suffix(value_lbl)
    return row


class _SectionA:
    """Sistema riconosciuto — read-only."""

    def __init__(self, page):
        self.group = make_section("sr_section_a_title")
        self.rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.refresh()

    def refresh(self):
        child = self.group.get_first_child()
        # Adw.PreferencesGroup manages its own rows; rebuild by tracking
        # what we added.
        for row in getattr(self, "_rows", []):
            self.group.remove(row)
        self.profile = dp.detect_distro_profile()
        p = self.profile
        rows = []

        confidence_row = Adw.ActionRow(title=T("sr_distro"))
        confidence_row.set_activatable(False)
        pill = state_pill("active" if p.confident else "check_needed",
                           T("sr_confident_yes") if p.confident else T("sr_confident_no"))
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        value_lbl = Gtk.Label(label=p.pretty_name or p.id or "—", valign=Gtk.Align.CENTER)
        value_lbl.add_css_class("sysinfo-value")
        box.append(value_lbl)
        box.append(pill)
        confidence_row.add_suffix(box)
        rows.append(confidence_row)

        rows.append(_info_row("sr_version", p.version_id or "—"))
        family_label = {"debian": "Debian/Ubuntu", "fedora": "Fedora/RHEL", "arch": "Arch",
                          "opensuse": "openSUSE", "unknown": "—"}.get(p.family, p.family)
        rows.append(_info_row("sr_family", family_label))
        rows.append(_info_row("sr_codename", p.version_codename or p.ubuntu_codename or "—"))
        rows.append(_info_row("sr_pkg_manager", p.package_manager))
        rows.append(_info_row("sr_system_type", T(_SYSTEM_TYPE_KEYS.get(p.system_type, "sr_system_type_unknown"))))

        for row in rows:
            self.group.add(row)
        self._rows = rows


class _SectionB:
    """Flatpak e Flathub."""

    def __init__(self, page):
        self.page = page
        self.group = make_section("sr_section_b_title")
        self._rows = []
        self._detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        btn_row = Adw.ActionRow()
        btn_row.set_activatable(False)
        self.configure_btn = Gtk.Button(label=T("sr_configure_flatpak_btn"))
        self.configure_btn.add_css_class("ds-btn-primary")
        self.configure_btn.connect("clicked", self._on_configure_clicked)
        self._configure_badge = StatusPill(T("sr_configuration_complete_badge"), variant="success")
        self._configure_badge.set_visible(False)
        badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        badge_box.append(self.configure_btn)
        badge_box.append(self._configure_badge)
        btn_row.add_suffix(badge_box)
        self.group.add(btn_row)

        self._integration_note_lbl = Gtk.Label(wrap=True, xalign=0)
        self._integration_note_lbl.add_css_class("dim-label")
        self._integration_note_lbl.set_visible(False)
        note_row = Adw.ActionRow()
        note_row.set_activatable(False)
        note_row.set_child(self._integration_note_lbl)
        self.group.add(note_row)

        extra_row = Adw.ActionRow()
        extra_row.set_activatable(False)
        extra_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.updates_btn = Gtk.Button(label=T("sr_check_updates_btn"))
        self.updates_btn.connect("clicked", self._on_check_updates)
        self.unused_btn = Gtk.Button(label=T("sr_remove_unused_btn"))
        self.unused_btn.connect("clicked", lambda _b: self._confirm_and_run(
            "remove_unused_flatpak", "sr_action_preview_title", "sr_orphans_preview", trigger=self.unused_btn))
        self.repair_user_btn = Gtk.Button(label=T("sr_repair_flatpak_user_btn"))
        self.repair_user_btn.connect("clicked", lambda _b: self._run_simple(
            "repair_flatpak", scope=fpm.SCOPE_USER, trigger=self.repair_user_btn))
        self.repair_system_btn = Gtk.Button(label=T("sr_repair_flatpak_system_btn"))
        self.repair_system_btn.connect("clicked", lambda _b: self._run_simple(
            "repair_flatpak", scope=fpm.SCOPE_SYSTEM, trigger=self.repair_system_btn))
        for b in (self.updates_btn, self.unused_btn, self.repair_user_btn, self.repair_system_btn):
            extra_box.append(b)
        extra_row.set_child(extra_box)
        self.group.add(extra_row)

        detail_row = Adw.ActionRow()
        detail_row.set_activatable(False)
        detail_row.set_child(self._detail_box)
        self.group.add(detail_row)

        # Flatseal has its own contextual row: state + action button/badge,
        # never a single static "Install Flatseal" that ignores what's
        # actually already there (the bug this block fixes).
        self._flatseal_state_lbl = Gtk.Label(xalign=0, hexpand=True)
        self.flatseal_btn = Gtk.Button(label=T("sr_install_flatseal_btn"))
        self.flatseal_btn.connect("clicked", self._on_flatseal_clicked)
        self._flatseal_pill = StatusPill(T("installed_badge"), variant="success")
        self._flatseal_pill.set_visible(False)
        flatseal_row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        flatseal_row_box.append(self._flatseal_state_lbl)
        flatseal_row_box.append(self._flatseal_pill)
        flatseal_row_box.append(self.flatseal_btn)
        flatseal_row = Adw.ActionRow(title=T("sr_flatseal_label"))
        flatseal_row.set_activatable(False)
        flatseal_row.add_suffix(flatseal_row_box)
        self.group.add(flatseal_row)

        self._result_lbl = Gtk.Label(wrap=True, xalign=0)
        self._result_lbl.set_visible(False)
        result_row = Adw.ActionRow()
        result_row.set_activatable(False)
        result_row.set_child(self._result_lbl)
        self.group.add(result_row)

        self.refresh()

    def refresh(self):
        state = fpm.detect_flatpak_state()
        self._state = state
        child = self._detail_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._detail_box.remove(child)
            child = nxt

        def line(label_key, ok_variant, text):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=T(label_key), xalign=0, hexpand=True)
            row.append(lbl)
            row.append(StatusPill(text, variant=ok_variant))
            return row

        action_buttons = (self.updates_btn, self.unused_btn, self.repair_user_btn, self.repair_system_btn)
        if not state.installed:
            self._detail_box.append(line("sr_flatpak_installed", "absent", T("sr_flatpak_state_not_installed")))
            for b in action_buttons:
                b.set_sensitive(False)
            self._refresh_configure_button(state)
            self._refresh_flatseal_row()
            return

        for b in action_buttons:
            b.set_sensitive(True)

        self._detail_box.append(line("sr_flatpak_installed", "success",
                                       f"{T('sr_flatpak_state_installed')} ({state.version})" if state.version else T("sr_flatpak_state_installed")))
        self._detail_box.append(line("sr_flathub_system", "success" if state.flathub_system else "neutral",
                                       T("sr_flathub_active") if state.flathub_system else T("sr_flathub_not_configured")))
        self._detail_box.append(line("sr_flathub_user", "success" if state.flathub_user else "neutral",
                                       T("sr_flathub_active") if state.flathub_user else T("sr_flathub_not_configured")))
        self._detail_box.append(line("sr_integration", "success" if state.integration_complete else "warning",
                                       T("sr_integration_complete") if state.integration_complete else T("sr_integration_incomplete")))

        self._refresh_configure_button(state)
        self._refresh_flatseal_row()

    # ── Phase 5: the main button is contextual, never one generic label ──
    def _integration_gap_reason(self, state: fpm.FlatpakState) -> "str | None":
        """None means integration is genuinely complete — never guessed."""
        if state.integration_complete:
            return None
        if not state.portal_present:
            return "sr_integration_gap_portal_missing"
        if not state.portal_backend:
            return "sr_integration_gap_backend_missing"
        return "sr_integration_gap_undetermined"

    def _refresh_configure_button(self, state: fpm.FlatpakState):
        if not state.installed:
            self._set_configure_mode("sr_configure_flatpak_btn", self._on_configure_clicked, visible=True)
            self._integration_note_lbl.set_visible(False)
            return

        if not state.flathub_system and not state.flathub_user:
            self._set_configure_mode("sr_add_flathub_btn", self._on_configure_clicked, visible=True)
            self._integration_note_lbl.set_visible(False)
            return

        if state.flathub_user and not state.flathub_system:
            self._set_configure_mode("sr_extend_system_btn", self._on_extend_system_clicked, visible=True)
        elif not self._integration_gap_reason(state):
            self._set_configure_mode(None, None, visible=False)
        else:
            self._set_configure_mode("sr_complete_integration_btn", self._on_complete_integration_clicked, visible=True)

        gap = self._integration_gap_reason(state)
        # "Logout consigliato" is surfaced only as an immediate one-off
        # note after an action (_on_op_done) — never folded into this
        # persistent gap reasoning, and never phrased as an error.
        if gap:
            self._integration_note_lbl.set_text(T(gap))
            self._integration_note_lbl.set_visible(True)
        else:
            self._integration_note_lbl.set_visible(False)

    def _set_configure_mode(self, label_key, handler, visible: bool):
        if self._configure_click_handler_id is not None:
            self.configure_btn.disconnect(self._configure_click_handler_id)
            self._configure_click_handler_id = None
        self.configure_btn.set_visible(visible)
        self._configure_badge.set_visible(not visible)
        if visible and label_key:
            self.configure_btn.set_label(T(label_key))
            self._configure_click_handler_id = self.configure_btn.connect("clicked", handler)

    _configure_click_handler_id = None

    # ── Configure Flatpak + Flathub (with scope choice + preview) ──
    def _on_configure_clicked(self, _btn):
        body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body_box.append(Gtk.Label(label=T("sr_configure_flatpak_preview"), wrap=True, xalign=0))
        self._append_scope_choice(body_box, default_system=False)

        dialog = Adw.MessageDialog(transient_for=self.page.get_root(),
                                    heading=T("sr_configure_flatpak_dialog_title"))
        dialog.set_extra_child(body_box)
        dialog.add_response("cancel", T("sr_cancel_btn"))
        dialog.add_response("confirm", T("sr_confirm_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: self._on_configure_response(r, self._pending_system_check.get_active()))
        dialog.present()

    def _append_scope_choice(self, body_box, default_system: bool):
        system_check = Gtk.CheckButton(label=T("sr_scope_system"))
        system_desc = Gtk.Label(label=T("sr_scope_system_desc"), wrap=True, xalign=0)
        system_desc.add_css_class("dim-label")
        user_check = Gtk.CheckButton(label=T("sr_scope_user"))
        user_check.set_group(system_check)
        system_check.set_active(default_system)
        user_check.set_active(not default_system)
        user_desc = Gtk.Label(label=T("sr_scope_user_desc"), wrap=True, xalign=0)
        user_desc.add_css_class("dim-label")
        body_box.append(system_check)
        body_box.append(system_desc)
        body_box.append(user_check)
        body_box.append(user_desc)
        self._pending_system_check = system_check

    def _on_configure_response(self, response, system_scope: bool):
        if response != "confirm":
            return
        scope = fpm.SCOPE_SYSTEM if system_scope else fpm.SCOPE_USER
        self._run_simple("configure_flatpak", scope=scope, trigger=self.configure_btn)

    def _on_extend_system_clicked(self, _btn):
        dialog = Adw.MessageDialog(transient_for=self.page.get_root(),
                                    heading=T("sr_extend_system_btn"),
                                    body=T("sr_extend_system_preview"))
        dialog.add_response("cancel", T("sr_cancel_btn"))
        dialog.add_response("confirm", T("sr_confirm_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: self._run_simple(
            "add_flathub", scope=fpm.SCOPE_SYSTEM, trigger=self.configure_btn) if r == "confirm" else None)
        dialog.present()

    def _on_complete_integration_clicked(self, _btn):
        gap = self._integration_gap_reason(self._state)
        dialog = Adw.MessageDialog(transient_for=self.page.get_root(),
                                    heading=T("sr_integration_info_dialog_title"),
                                    body=T(gap) if gap else "")
        dialog.add_response("close", T("dialog_close_btn"))
        dialog.present()

    # ── Flatseal (Phase 3) ──────────────────────────────────────────
    _FLATSEAL_STATE_KEYS = {
        fpm.APP_NOT_INSTALLED: "sr_flatseal_state_not_installed",
        fpm.APP_INSTALLED_USER: "sr_flatseal_state_installed_user",
        fpm.APP_INSTALLED_SYSTEM: "sr_flatseal_state_installed_system",
        fpm.APP_INSTALLED_BOTH: "sr_flatseal_state_installed_both",
        fpm.APP_FLATPAK_UNAVAILABLE: "sr_flatseal_state_flatpak_unavailable",
        fpm.APP_FLATHUB_USER_UNAVAILABLE: "sr_flatseal_state_not_installed",
        fpm.APP_FLATHUB_SYSTEM_UNAVAILABLE: "sr_flatseal_state_not_installed",
        fpm.APP_UNDETERMINED: "sr_flatseal_state_undetermined",
    }

    def _refresh_flatseal_row(self):
        status = fpm.flatpak_app_status(fpm.FLATSEAL_APP_ID)
        self._flatseal_status = status
        self._flatseal_state_lbl.set_text(T(self._FLATSEAL_STATE_KEYS.get(status.state, "sr_flatseal_state_undetermined")))

        if status.installed:
            self._flatseal_pill.set_visible(True)
            self.flatseal_btn.set_label(T("sr_open_flatseal_btn"))
            self.flatseal_btn.set_sensitive(True)
            return

        self._flatseal_pill.set_visible(False)
        self.flatseal_btn.set_label(T("sr_install_flatseal_btn"))
        can_install = (status.flatpak_installed and status.determined
                       and (status.flathub_user_available or status.flathub_system_available))
        self.flatseal_btn.set_sensitive(can_install)
        if can_install:
            self.flatseal_btn.set_tooltip_text("")
        elif not status.flatpak_installed:
            self.flatseal_btn.set_tooltip_text(T("sr_flatseal_tooltip_needs_flatpak"))
        elif not status.determined:
            self.flatseal_btn.set_tooltip_text(T("sr_flatseal_tooltip_undetermined"))
        else:
            self.flatseal_btn.set_tooltip_text(T("sr_flatseal_tooltip_needs_flathub"))

    def _on_flatseal_clicked(self, _btn):
        status = getattr(self, "_flatseal_status", None)
        if status is None or not status.flatpak_installed:
            return
        if status.installed:
            fpm.open_flatpak_app(fpm.FLATSEAL_APP_ID)
            return

        both_available = status.flathub_user_available and status.flathub_system_available
        if both_available:
            body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            self._append_scope_choice(body_box, default_system=False)
            dialog = Adw.MessageDialog(transient_for=self.page.get_root(), heading=T("sr_choose_scope_title"))
            dialog.set_extra_child(body_box)
            dialog.add_response("cancel", T("sr_cancel_btn"))
            dialog.add_response("confirm", T("sr_confirm_btn"))
            dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
            dialog.connect("response", lambda d, r: self._run_simple(
                "install_flatseal", scope=(fpm.SCOPE_SYSTEM if self._pending_system_check.get_active() else fpm.SCOPE_USER),
                trigger=self.flatseal_btn) if r == "confirm" else None)
            dialog.present()
            return

        if status.flathub_system_available:
            # Only the system scope is usable — ask for privileges only
            # after an explicit confirmation, never silently.
            dialog = Adw.MessageDialog(transient_for=self.page.get_root(),
                                        heading=T("sr_install_flatseal_btn"),
                                        body=T("sr_flatseal_install_preview_system"))
            dialog.add_response("cancel", T("sr_cancel_btn"))
            dialog.add_response("confirm", T("sr_confirm_btn"))
            dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
            dialog.connect("response", lambda d, r: self._run_simple(
                "install_flatseal", scope=fpm.SCOPE_SYSTEM, trigger=self.flatseal_btn) if r == "confirm" else None)
            dialog.present()
            return

        if status.flathub_user_available:
            # User scope needs no privileges at all — proceed directly,
            # per spec ("non usare privilegi... non provare
            # automaticamente lo scope di sistema").
            self._run_simple("install_flatseal", scope=fpm.SCOPE_USER, trigger=self.flatseal_btn)

    def _on_check_updates(self, _btn):
        self.updates_btn.set_sensitive(False)

        def run():
            ok, items = fpm.check_flatpak_updates()
            GLib.idle_add(self._on_updates_checked, ok, items)

        threading.Thread(target=run, daemon=True).start()

    def _on_updates_checked(self, ok, items):
        self.updates_btn.set_sensitive(True)
        if not ok:
            self._show_result(T("sr_result_failed"), False)
            return False
        if not items:
            self._show_result(T("sr_updates_none"), True)
            return False
        text = T("sr_updates_available").format(n=len(items))
        self._show_result(text, True, offer_apply=True)
        return False

    def _show_result(self, text, ok, offer_apply=False):
        self._result_lbl.set_visible(True)
        self._result_lbl.remove_css_class("desc-con")
        self._result_lbl.remove_css_class("status-active")
        self._result_lbl.set_text(text)
        self._result_lbl.add_css_class("status-active" if ok else "desc-con")

    def _confirm_and_run(self, op_key, title_key, body_key, scope="", trigger=None):
        dialog = Adw.MessageDialog(transient_for=self.page.get_root(), heading=T(title_key), body=T(body_key))
        dialog.add_response("cancel", T("sr_cancel_btn"))
        dialog.add_response("confirm", T("sr_confirm_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: self._run_simple(op_key, scope=scope, trigger=trigger) if r == "confirm" else None)
        dialog.present()

    def _run_simple(self, op_key, scope="", trigger=None):
        button = trigger or self.configure_btn
        button.set_sensitive(False)

        def run():
            result = engine.run_operation(op_key, profile=self.page.profile, scope=scope)
            GLib.idle_add(self._on_op_done, result, button)

        threading.Thread(target=run, daemon=True).start()

    def _on_op_done(self, result, button=None):
        if button is not None:
            button.set_sensitive(True)
        text = T(result.friendly_message) if result.friendly_message and T(result.friendly_message) != result.friendly_message \
            else (T("sr_result_ok") if result.ok else T("sr_result_failed"))
        if result.logout_recommended:
            text = f"{text} {T('sr_session_logout_recommended')}"
        self._show_result(text, result.ok)
        self.refresh()
        return False


class _SectionC:
    """Repository software — read-only scan + guarded recipes."""

    def __init__(self, page):
        self.page = page
        self.group = make_section("sr_section_c_title")
        self._rows = []

        header_row = Adw.ActionRow()
        header_row.set_activatable(False)
        self.rescan_btn = Gtk.Button(label=T("sr_rescan_btn"))
        self.rescan_btn.connect("clicked", lambda _b: self.refresh())
        header_row.add_suffix(self.rescan_btn)
        self.group.add(header_row)
        self._rows.append(header_row)

        self._summary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        summary_row = Adw.ActionRow()
        summary_row.set_activatable(False)
        summary_row.set_child(self._summary_box)
        self.group.add(summary_row)
        self._rows.append(summary_row)

        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        list_row = Adw.ActionRow()
        list_row.set_activatable(False)
        list_row.set_child(self._list_box)
        self.group.add(list_row)
        self._rows.append(list_row)

        self._additional_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        additional_row = Adw.ActionRow()
        additional_row.set_activatable(False)
        additional_row.set_child(self._additional_box)
        self.group.add(additional_row)
        self._rows.append(additional_row)

        self.refresh()

    def refresh(self):
        profile = self.page.profile
        result = rsc.scan_all(profile.family)
        entries = result["entries"]
        summary = result["summary"]

        for box in (self._summary_box, self._list_box, self._additional_box):
            child = box.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                box.remove(child)
                child = nxt

        for key, count_key in (
            ("official_active", "sr_summary_official"), ("external_active", "sr_summary_external"),
            ("disabled", "sr_summary_disabled"), ("needs_review", "sr_summary_review"),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(Gtk.Label(label=T(count_key), xalign=0, hexpand=True))
            row.append(Gtk.Label(label=str(summary[key]), xalign=1))
            self._summary_box.append(row)

        if not entries:
            self._list_box.append(Gtk.Label(label=T("sr_no_repos_found"), xalign=0, wrap=True))
        for entry in entries:
            self._list_box.append(self._build_repo_row(entry))

        self._build_additional_section(profile)

    _WARNING_KEYS = {
        rsc.WARNING_NO_HOST: "sr_warning_no_host",
        rsc.WARNING_NO_URI: "sr_warning_no_uri",
        rsc.WARNING_GPGCHECK_DISABLED: "sr_warning_gpgcheck_disabled",
        rsc.WARNING_SIGNATURE_UNSPECIFIED: "sr_warning_signature_unspecified",
        rsc.WARNING_AUR_NOT_A_REPO: "sr_warning_aur_not_a_repo",
        rsc.WARNING_DUPLICATE_CONFIG: "sr_warning_duplicate_config",
    }

    def _build_repo_row(self, entry: dict) -> Gtk.Widget:
        expander = Adw.ExpanderRow(title=entry["name"] or entry["uri"] or "—")
        pill = StatusPill(T(_RECIPE_KIND_KEYS.get(entry["kind"], "sr_kind_unknown")),
                            variant=_KIND_PILL_VARIANT.get(entry["kind"], "neutral"))
        expander.add_suffix(pill)
        if not entry["enabled"]:
            expander.add_suffix(StatusPill(T("sr_repo_disabled"), variant="neutral"))

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        detail.set_margin_top(6)
        detail.set_margin_bottom(10)
        detail.set_margin_start(14)
        detail.set_margin_end(14)
        if entry.get("uri"):
            detail.append(Gtk.Label(label=f"{T('sr_repo_address')}: {entry['uri']}", xalign=0, wrap=True))
        suites = entry.get("suites") or []
        if suites:
            detail.append(Gtk.Label(label=f"{T('sr_repo_suites')}: {', '.join(suites)}", xalign=0, wrap=True))
        if entry.get("components"):
            detail.append(Gtk.Label(label=f"{T('sr_repo_components')}: {entry['components']}", xalign=0, wrap=True))
        detail.append(Gtk.Label(label=f"{T('sr_repo_source_file')}: {entry['source_file']}", xalign=0, wrap=True))
        if entry.get("signed") is not None:
            sig_lbl = Gtk.Label(label=T("sr_repo_signed_yes") if entry["signed"] else T("sr_repo_signed_no"),
                                  xalign=0, wrap=True)
            if not entry["signed"]:
                sig_lbl.add_css_class("desc-con")
            detail.append(sig_lbl)
        for warning in entry.get("warnings", []):
            # Never render the raw internal code — an unmapped one
            # still gets a real, generic translated sentence instead.
            warn_key = self._WARNING_KEYS.get(warning, "sr_warning_unknown")
            warn_lbl = Gtk.Label(label=T(warn_key), xalign=0, wrap=True)
            warn_lbl.add_css_class("desc-con")
            detail.append(warn_lbl)
        if entry.get("duplicate_files"):
            dup_lbl = Gtk.Label(label=f"{T('sr_repo_duplicate_files')}: {', '.join(entry['duplicate_files'])}",
                                  xalign=0, wrap=True)
            dup_lbl.add_css_class("desc-con")
            detail.append(dup_lbl)
        expander.add_row(detail)
        return expander

    def _build_additional_section(self, profile):
        self._additional_box.append(Gtk.Label(label=T("sr_additional_title"), xalign=0))
        self._additional_box.get_first_child().add_css_class("heading")

        guided = [r for r in rr.recipes_for_profile(profile) if r.level == rr.LEVEL_GUIDED]
        advanced = [r for r in rr.recipes_for_profile(profile) if r.level == rr.LEVEL_ADVANCED]

        if guided:
            self._additional_box.append(Gtk.Label(label=T("sr_additional_guided_desc"), xalign=0, wrap=True))
        for recipe in guided:
            self._additional_box.append(self._build_recipe_row(recipe, profile))

        if advanced:
            adv_lbl = Gtk.Label(label=T("sr_additional_advanced_desc"), xalign=0, wrap=True)
            adv_lbl.add_css_class("desc-con")
            self._additional_box.append(adv_lbl)
        for recipe in advanced:
            self._additional_box.append(self._build_recipe_row(recipe, profile, actionable=False))

    def _build_recipe_row(self, recipe, profile, actionable=True) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        name_box.append(Gtk.Label(label=_recipe_text(recipe.name_key), xalign=0))
        desc_lbl = Gtk.Label(label=_recipe_text(recipe.description_key), xalign=0, wrap=True)
        desc_lbl.add_css_class("dim-label")
        name_box.append(desc_lbl)
        row.append(name_box)

        if actionable:
            enable_btn = Gtk.Button(label=T("sr_enable_btn"))
            enable_btn.connect("clicked", lambda _b, rid=recipe.id: self._on_enable_recipe(rid, profile))
            row.append(enable_btn)
        else:
            pill = StatusPill(T("sr_kind_needs_review"), variant="warning")
            row.append(pill)
        return row

    def _on_enable_recipe(self, recipe_id: str, profile):
        recipe = rr.RECIPES_BY_ID[recipe_id]
        body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body_box.append(Gtk.Label(label=_recipe_text(recipe.description_key), xalign=0, wrap=True))
        body_box.append(Gtk.Label(label=f"{T('sr_recipe_source')}: {_recipe_text(recipe.source_key)}", xalign=0, wrap=True))
        body_box.append(Gtk.Label(label=f"{T('sr_recipe_risk')}: {T('risk_' + recipe.risk)}", xalign=0, wrap=True))
        files_lbl = Gtk.Label(label=f"{T('sr_recipe_files_involved')}: {', '.join(recipe.files_involved)}",
                                xalign=0, wrap=True)
        files_lbl.add_css_class("dim-label")
        body_box.append(files_lbl)

        dialog = Adw.MessageDialog(transient_for=self.page.get_root(), heading=T("sr_recipe_enable_preview_title"))
        dialog.set_extra_child(body_box)
        dialog.add_response("cancel", T("sr_cancel_btn"))
        dialog.add_response("confirm", T("sr_enable_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: self._run_enable(recipe_id, profile) if r == "confirm" else None)
        dialog.present()

    def _run_enable(self, recipe_id, profile):
        def run():
            result = engine.run_operation("enable_recipe", profile=profile, scope=recipe_id)
            GLib.idle_add(self._on_enable_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_enable_done(self, result):
        self.refresh()
        return False


class _SectionD:
    """Salute pacchetti."""

    def __init__(self, page):
        self.page = page
        self.group = make_section("sr_section_d_title", "sr_section_d_desc")

        btn_row = Adw.ActionRow()
        btn_row.set_activatable(False)
        self.scan_btn = Gtk.Button(label=T("sr_scan_system_btn"))
        self.scan_btn.add_css_class("ds-btn-primary")
        self.scan_btn.connect("clicked", self._on_scan)
        btn_row.add_suffix(self.scan_btn)
        self.group.add(btn_row)

        self._report_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        report_row = Adw.ActionRow()
        report_row.set_activatable(False)
        report_row.set_child(self._report_box)
        self.group.add(report_row)

        self._prescan_hint_lbl = Gtk.Label(label=T("sr_health_prescan_hint"), xalign=0, wrap=True)
        self._prescan_hint_lbl.add_css_class("dim-label")
        hint_row = Adw.ActionRow()
        hint_row.set_activatable(False)
        hint_row.set_child(self._prescan_hint_lbl)
        self.group.add(hint_row)

        actions_row = Adw.ActionRow()
        actions_row.set_activatable(False)
        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.repair_btn = Gtk.Button(label=T("sr_repair_deps_btn"))
        self.repair_btn.connect("clicked", lambda _b: self._confirm_and_run(
            "repair_dependencies", "sr_repair_preview"))
        self.orphans_btn = Gtk.Button(label=T("sr_remove_orphans_btn"))
        self.orphans_btn.connect("clicked", self._on_remove_orphans)
        self.indexes_btn = Gtk.Button(label=T("sr_update_indexes_btn"))
        self.indexes_btn.connect("clicked", lambda _b: self._run_simple("update_indexes"))
        self.cache_btn = Gtk.Button(label=T("sr_clean_cache_btn"))
        self.cache_btn.connect("clicked", lambda _b: self._confirm_and_run("clean_cache", "sr_cache_preview"))
        for b in (self.repair_btn, self.orphans_btn, self.indexes_btn, self.cache_btn):
            b.set_sensitive(False)
            b.set_tooltip_text(T("sr_health_action_needs_scan_tooltip"))
            actions_box.append(b)
        actions_row.set_child(actions_box)
        self.group.add(actions_row)

        self._result_lbl = Gtk.Label(wrap=True, xalign=0)
        self._result_lbl.set_visible(False)
        result_row = Adw.ActionRow()
        result_row.set_activatable(False)
        result_row.set_child(self._result_lbl)
        self.group.add(result_row)

        self._last_report = None

    def _on_scan(self, _btn):
        self.scan_btn.set_sensitive(False)
        self.scan_btn.set_label("⏳")

        def run():
            report = health.scan_system_health(self.page.profile.family)
            GLib.idle_add(self._on_scan_done, report)

        threading.Thread(target=run, daemon=True).start()

    def _on_scan_done(self, report):
        self.scan_btn.set_sensitive(True)
        self.scan_btn.set_label(T("sr_scan_system_btn"))
        self._last_report = report
        self._prescan_hint_lbl.set_visible(False)

        child = self._report_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._report_box.remove(child)
            child = nxt

        has_findings = report.broken_packages or report.orphan_packages or report.unused_flatpak_runtimes
        if not has_findings:
            self._report_box.append(Gtk.Label(label=T("sr_health_all_good"), xalign=0, wrap=True))
        else:
            for label_key, values in (
                ("sr_health_broken", report.broken_packages),
                ("sr_health_orphans", report.orphan_packages),
                ("sr_health_unused_flatpak", report.unused_flatpak_runtimes),
            ):
                if values:
                    line = Gtk.Label(label=f"{T(label_key)}: {len(values)}", xalign=0, wrap=True)
                    self._report_box.append(line)
        cache_line = Gtk.Label(label=f"{T('sr_health_cache')}: {report.cache_reclaimable_human}", xalign=0)
        self._report_box.append(cache_line)

        self.repair_btn.set_sensitive(bool(report.broken_packages))
        self.repair_btn.set_tooltip_text("" if report.broken_packages else T("sr_health_no_broken_tooltip"))
        self.orphans_btn.set_sensitive(bool(report.orphan_packages))
        self.orphans_btn.set_tooltip_text("" if report.orphan_packages else T("sr_health_no_orphans_tooltip"))
        self.indexes_btn.set_sensitive(True)
        self.indexes_btn.set_tooltip_text("")
        self.cache_btn.set_sensitive(True)
        self.cache_btn.set_tooltip_text("")
        return False

    def _on_remove_orphans(self, _btn):
        n = len(self._last_report.orphan_packages) if self._last_report else 0
        body = T("sr_orphans_preview").format(n=n)
        dialog = Adw.MessageDialog(transient_for=self.page.get_root(), heading=T("sr_action_preview_title"), body=body)
        dialog.add_response("cancel", T("sr_cancel_btn"))
        dialog.add_response("confirm", T("sr_confirm_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: self._run_simple("remove_orphans") if r == "confirm" else None)
        dialog.present()

    def _confirm_and_run(self, op_key, body_key):
        dialog = Adw.MessageDialog(transient_for=self.page.get_root(), heading=T("sr_action_preview_title"),
                                     body=T(body_key))
        dialog.add_response("cancel", T("sr_cancel_btn"))
        dialog.add_response("confirm", T("sr_confirm_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: self._run_simple(op_key) if r == "confirm" else None)
        dialog.present()

    def _run_simple(self, op_key):
        def run():
            result = engine.run_operation(op_key, profile=self.page.profile)
            GLib.idle_add(self._on_op_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_op_done(self, result):
        self._result_lbl.set_visible(True)
        self._result_lbl.remove_css_class("desc-con")
        self._result_lbl.remove_css_class("status-active")
        text = T(result.friendly_message) if result.friendly_message and T(result.friendly_message) != result.friendly_message \
            else (T("sr_result_ok") if result.ok else T("sr_result_failed"))
        if result.reboot_required:
            text = f"{text} {T('sr_reboot_note')}"
        self._result_lbl.set_text(text)
        self._result_lbl.add_css_class("status-active" if result.ok else "desc-con")
        self._on_scan(None)
        return False


class SoftwareRepositoriesPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("system-software-install-symbolic")
        self.profile = dp.detect_distro_profile()
        on_change(self._refresh_title)
        self._refresh_title()
        _widen_preferences_clamp(self, maximum_size=900, tightening_threshold=700)

        header = PageHeader(
            "system-software-install-symbolic", T("tab_software_repos"), T("sr_header_desc"),
            category=CATEGORY,
        )
        self.add(wrap_in_preferences_group(header))

        self._section_a = _SectionA(self)
        self.add(self._section_a.group)

        self._section_b = _SectionB(self)
        self.add(self._section_b.group)

        self._section_c = _SectionC(self)
        self.add(self._section_c.group)

        self._section_d = _SectionD(self)
        self.add(self._section_d.group)

    def _refresh_title(self):
        self.set_title(T("tab_software_repos"))
