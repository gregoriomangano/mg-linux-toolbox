"""
"Aiuto e supporto" — the app's direct, in-app point of contact with
Gregorio: a short intro with his photo, three cards explaining what to
write about (assistance / bug reports / collaborations), and the real
contact channels. Every external link goes through core.uri_launcher
(https-only), exactly like every other page — never a raw
Gio.AppInfo.launch_default_for_uri call, never a mailto: link (that
scheme is deliberately rejected by the launcher).
"""
import os

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from core.i18n import T, on_change
from core import i18n as _i18n_mod
from core import release_config
from core.uri_launcher import open_external_url
from ui.widgets import load_image_or_placeholder

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge
from ui.design_system.section_card import make_section

_PHOTO_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "images", "gregorio-supporto.jpg"
)

_help_ds_strings = {
    "tab_help_support": {
        "en": "Help & Support", "it": "Aiuto e supporto",
        "es": "Ayuda y soporte", "fr": "Aide et support",
    },
    "help_header_desc": {
        "en": "Get in touch directly with Gregorio.",
        "it": "Mettiti in contatto direttamente con Gregorio.",
        "es": "Ponte en contacto directamente con Gregorio.",
        "fr": "Contactez directement Gregorio.",
    },
    "help_intro_text": {
        "en": ("Hi, I'm Gregorio. I make YouTube content and develop M.G Linux "
               "Toolbox.\n\nIf you want to report a problem, ask for a hand with "
               "Linux or Windows, talk about a collaboration, or just get in "
               "touch, here are the main ways to reach me."),
        "it": ("Ciao, sono Gregorio. Creo contenuti su YouTube e sviluppo M.G "
               "Linux Toolbox.\n\nSe vuoi segnalare un problema, chiedere una "
               "mano con Linux o Windows, parlare di una collaborazione oppure "
               "contattarmi direttamente, qui trovi i riferimenti principali."),
        "es": ("Hola, soy Gregorio. Creo contenido en YouTube y desarrollo M.G "
               "Linux Toolbox.\n\nSi quieres reportar un problema, pedir una "
               "mano con Linux o Windows, hablar de una colaboración o "
               "simplemente contactarme, aquí tienes las formas principales de "
               "hacerlo."),
        "fr": ("Bonjour, je suis Gregorio. Je crée du contenu sur YouTube et je "
               "développe M.G Linux Toolbox.\n\nSi vous voulez signaler un "
               "problème, demander un coup de main avec Linux ou Windows, "
               "parler d'une collaboration ou simplement me contacter, voici "
               "les principaux moyens de le faire."),
    },
    "help_photo_placeholder": {
        "en": "Photo", "it": "Foto", "es": "Foto", "fr": "Photo",
    },
    "help_assist_title": {
        "en": "Assistance", "it": "Assistenza", "es": "Asistencia", "fr": "Assistance",
    },
    "help_assist_body": {
        "en": "If you need a hand with Linux or Windows, you can write to me directly explaining what you need.",
        "it": "Se hai bisogno di una mano con Linux o Windows, puoi scrivermi direttamente spiegandomi cosa ti serve.",
        "es": "Si necesitas una mano con Linux o Windows, puedes escribirme directamente explicando qué necesitas.",
        "fr": "Si vous avez besoin d'un coup de main avec Linux ou Windows, vous pouvez m'écrire directement en expliquant ce dont vous avez besoin.",
    },
    "help_bug_title": {
        "en": "Bug report", "it": "Segnalazione bug", "es": "Reporte de errores", "fr": "Signalement de bug",
    },
    "help_bug_body": {
        "en": "Found a bug, unclear text, or strange behavior in the Toolbox? Write to me describing the problem: the more detail, the easier it is to understand what's happening.",
        "it": "Hai trovato un bug, un testo poco chiaro o un comportamento strano del Toolbox? Scrivimi descrivendo il problema: più dettagli ci sono, più sarà facile capire cosa succede.",
        "es": "¿Encontraste un error, un texto poco claro o un comportamiento extraño en el Toolbox? Escríbeme describiendo el problema: cuantos más detalles haya, más fácil será entender qué sucede.",
        "fr": "Vous avez trouvé un bug, un texte peu clair ou un comportement étrange du Toolbox ? Écrivez-moi en décrivant le problème : plus il y a de détails, plus il sera facile de comprendre ce qui se passe.",
    },
    "help_collab_title": {
        "en": "Collaborations", "it": "Collaborazioni", "es": "Colaboraciones", "fr": "Collaborations",
    },
    "help_collab_body": {
        "en": "For collaborations, professional proposals, sponsorships or initiatives related to the project, you can contact me directly. I'll gladly consider the request.",
        "it": "Per collaborazioni, proposte professionali, sponsorizzazioni o iniziative legate al progetto puoi contattarmi direttamente. Valuterò volentieri la richiesta.",
        "es": "Para colaboraciones, propuestas profesionales, patrocinios o iniciativas relacionadas con el proyecto, puedes contactarme directamente. Evaluaré con gusto la solicitud.",
        "fr": "Pour les collaborations, propositions professionnelles, parrainages ou initiatives liées au projet, vous pouvez me contacter directement. J'examinerai volontiers la demande.",
    },
    "help_contact_title": {
        "en": "Contacts", "it": "Contatti", "es": "Contactos", "fr": "Contacts",
    },
    "help_contact_email_label": {
        "en": "Email", "it": "Email", "es": "Correo electrónico", "fr": "E-mail",
    },
    "help_contact_website_label": {
        "en": "Website", "it": "Sito web", "es": "Sitio web", "fr": "Site web",
    },
    "help_contact_youtube_label": {
        "en": "YouTube channel", "it": "Canale YouTube", "es": "Canal de YouTube", "fr": "Chaîne YouTube",
    },
    "help_write_btn": {
        "en": "Write to me", "it": "Scrivimi", "es": "Escríbeme", "fr": "Écrivez-moi",
    },
    "help_visit_site_btn": {
        "en": "Visit the website", "it": "Visita il sito", "es": "Visitar el sitio", "fr": "Visiter le site",
    },
    "help_youtube_btn": {
        "en": "YouTube channel", "it": "Canale YouTube", "es": "Canal de YouTube", "fr": "Chaîne YouTube",
    },
}
for _k, _v in _help_ds_strings.items():
    _i18n_mod._strings[_k] = _v


class HelpSupportPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("system-help-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        header = PageHeader(
            "system-help-symbolic", T("tab_help_support"), T("help_header_desc"),
            category="network",
        )
        self.add(wrap_in_preferences_group(header))

        # ── Intro card: photo + greeting ─────────────────────────────
        g_intro = Adw.PreferencesGroup()
        self.add(g_intro)
        intro_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        intro_box.set_margin_top(14)
        intro_box.set_margin_bottom(14)
        intro_box.set_margin_start(6)
        intro_box.set_margin_end(6)

        photo = load_image_or_placeholder(_PHOTO_PATH, "avatar-default-symbolic",
                                           "help_photo_placeholder", size=110)
        photo.set_halign(Gtk.Align.CENTER)
        photo.set_valign(Gtk.Align.START)
        photo.add_css_class("mg-help-photo")
        photo.set_overflow(Gtk.Overflow.HIDDEN)
        intro_box.append(photo)

        self._intro_lbl = Gtk.Label(wrap=True, xalign=0, hexpand=True,
                                     justify=Gtk.Justification.FILL, valign=Gtk.Align.CENTER)
        intro_box.append(self._intro_lbl)

        intro_row = Adw.PreferencesRow(activatable=False, selectable=False)
        intro_row.set_child(intro_box)
        g_intro.add(intro_row)

        # ── Sections: Assistenza / Segnalazione bug / Collaborazioni ─
        g_sections = Adw.PreferencesGroup()
        self.add(g_sections)
        self._assist_row, self._assist_title, self._assist_body = self._build_info_row(
            "preferences-desktop-remote-desktop-symbolic", "network")
        g_sections.add(self._assist_row)
        self._bug_row, self._bug_title, self._bug_body = self._build_info_row(
            "tools-check-spelling-symbolic", "energy")
        g_sections.add(self._bug_row)
        self._collab_row, self._collab_title, self._collab_body = self._build_info_row(
            "emblem-favorite-symbolic", "audio")
        g_sections.add(self._collab_row)

        # ── Contatti ───────────────────────────────────────────────
        g_contact = make_section("help_contact_title")
        self.add(g_contact)

        self._email_row = Adw.ActionRow()
        self._email_row.set_activatable(False)
        self._email_row.add_prefix(IconBadge("mail-unread-symbolic", category="network"))
        self._email_row.set_subtitle(release_config.SUPPORT_EMAIL)
        # Hidden entirely while no real mailbox is configured — never a
        # guessed/placeholder address shown as if it were real.
        self._email_row.set_visible(bool(release_config.SUPPORT_EMAIL))
        g_contact.add(self._email_row)

        self._website_row = Adw.ActionRow()
        self._website_row.set_activatable(False)
        self._website_row.add_prefix(IconBadge("network-wireless-symbolic", category="network"))
        self._website_row.set_subtitle(release_config.WEBSITE_URL)
        g_contact.add(self._website_row)

        self._youtube_row = Adw.ActionRow()
        self._youtube_row.set_activatable(False)
        self._youtube_row.add_prefix(IconBadge("video-x-generic-symbolic", category="network"))
        self._youtube_row.set_subtitle(release_config.YOUTUBE_URL)
        self._youtube_row.set_visible(bool(release_config.YOUTUBE_URL))
        g_contact.add(self._youtube_row)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.CENTER)
        btn_box.set_margin_top(4)
        btn_box.set_margin_bottom(10)
        self._write_btn = Gtk.Button()
        self._write_btn.add_css_class("lt-action-btn")
        self._write_btn.connect("clicked", lambda _b: open_external_url(release_config.CONTACT_URL))
        btn_box.append(self._write_btn)

        self._site_btn = Gtk.Button()
        self._site_btn.connect("clicked", lambda _b: open_external_url(release_config.WEBSITE_URL))
        btn_box.append(self._site_btn)

        self._yt_btn = Gtk.Button()
        self._yt_btn.set_visible(bool(release_config.YOUTUBE_URL))
        self._yt_btn.connect("clicked", lambda _b: open_external_url(release_config.YOUTUBE_URL))
        btn_box.append(self._yt_btn)

        btn_wrapper = Adw.PreferencesRow(activatable=False, selectable=False)
        btn_wrapper.set_child(btn_box)
        g_contact.add(btn_wrapper)

        on_change(self._refresh_labels)
        self._refresh_labels()

    def _build_info_row(self, icon_name: str, category: str):
        """Plain, non-activatable card: icon + title + wrapped body
        text — same IconBadge + vertical-text-box shape already used
        elsewhere for informational rows (e.g. the no-battery card on
        Energia e batteria), never a control that does nothing."""
        row = Adw.ActionRow()
        row.set_activatable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.append(IconBadge(icon_name, category=category, size="lg"))
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text_box.set_valign(Gtk.Align.CENTER)
        title_lbl = Gtk.Label(xalign=0)
        title_lbl.add_css_class("lt-service-title")
        body_lbl = Gtk.Label(xalign=0, wrap=True)
        body_lbl.add_css_class("sysinfo-value-sub")
        text_box.append(title_lbl)
        text_box.append(body_lbl)
        box.append(text_box)
        row.set_child(box)
        return row, title_lbl, body_lbl

    def _refresh_labels(self):
        self._intro_lbl.set_text(T("help_intro_text"))
        self._assist_title.set_text(T("help_assist_title"))
        self._assist_body.set_text(T("help_assist_body"))
        self._bug_title.set_text(T("help_bug_title"))
        self._bug_body.set_text(T("help_bug_body"))
        self._collab_title.set_text(T("help_collab_title"))
        self._collab_body.set_text(T("help_collab_body"))
        self._email_row.set_title(T("help_contact_email_label"))
        self._website_row.set_title(T("help_contact_website_label"))
        self._youtube_row.set_title(T("help_contact_youtube_label"))
        self._write_btn.set_label(T("help_write_btn"))
        self._site_btn.set_label(T("help_visit_site_btn"))
        self._yt_btn.set_label(T("help_youtube_btn"))

    def _refresh_title(self):
        self.set_title(T("tab_help_support"))
