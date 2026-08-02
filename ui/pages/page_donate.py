"""
"Supporta il progetto" — QR code, PayPal link, bank details with
copy-to-clipboard, a contact link, and a link to the project page.
Every external link is a plain https:// URL opened via
core.uri_launcher (never a browser embedded in the app). Nothing on
this page ever touches core.persistence.history_store — donation data
must never end up in the operations history.
"""
import os

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from core.i18n import T, on_change
from core.uri_launcher import open_external_url
from ui.widgets import make_group, load_image_or_placeholder

_QR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "images", "qr-donazione.png"
)

PAYPAL_URL = "https://www.paypal.com/donate/?hosted_button_id=7LCEUTKBTB6HW"
PROJECT_PAGE_URL = "https://www.manganogregorio.it/m-g-linux-toolbox/"
CONTACT_URL = "https://www.manganogregorio.it/contatti-gregorio-mangano-mondovi/"

IBAN = "IT16 S035 7601 6010 1000 9121 601"
ACCOUNT_HOLDER = "Mangano Gregorio"
SWIFT = "BBVAITM2XXX"

_COPY_CONFIRMATION_SECONDS = 2


class DonatePage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("emblem-favorite-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        g_intro = Adw.PreferencesGroup()
        self.add(g_intro)
        self._intro_lbl = Gtk.Label(wrap=True, xalign=0, justify=Gtk.Justification.FILL)
        self._intro_lbl.set_margin_top(12)
        self._intro_lbl.set_margin_bottom(4)
        self._intro_lbl.set_margin_start(14)
        self._intro_lbl.set_margin_end(14)
        intro_row = Adw.PreferencesRow(activatable=False, selectable=False)
        intro_row.set_child(self._intro_lbl)
        g_intro.add(intro_row)

        qr = load_image_or_placeholder(_QR_PATH, "view-app-grid-symbolic", "donate_qr_missing", size=180)
        qr.set_halign(Gtk.Align.CENTER)
        qr.set_margin_top(6)
        qr.set_margin_bottom(6)
        qr.add_css_class("card")
        qr_row = Adw.PreferencesRow(activatable=False, selectable=False)
        qr_row.set_child(qr)
        g_intro.add(qr_row)

        self._paypal_btn = Gtk.Button()
        self._paypal_btn.add_css_class("lt-action-btn")
        self._paypal_btn.connect("clicked", lambda _b: open_external_url(PAYPAL_URL))
        paypal_row = Adw.PreferencesRow(activatable=False, selectable=False)
        paypal_row.set_child(self._paypal_btn)
        g_intro.add(paypal_row)

        g_bank = make_group("donate_bank_group")
        self.add(g_bank)
        self._iban_row = self._make_copy_row(g_bank, IBAN, "donate_iban_label", "donate_copy_iban_btn", "donate_copied_iban")
        self._holder_row = self._make_copy_row(g_bank, ACCOUNT_HOLDER, "donate_holder_label", "donate_copy_holder_btn", "donate_copied_holder")
        self._swift_row = self._make_copy_row(g_bank, SWIFT, "donate_swift_label", "donate_copy_swift_btn", "donate_copied_swift")

        g_links = Adw.PreferencesGroup()
        self.add(g_links)
        link_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.CENTER)
        self._contact_btn = Gtk.Button()
        self._contact_btn.connect("clicked", lambda _b: open_external_url(CONTACT_URL))
        self._project_btn = Gtk.Button()
        self._project_btn.connect("clicked", lambda _b: open_external_url(PROJECT_PAGE_URL))
        link_box.append(self._contact_btn)
        link_box.append(self._project_btn)
        link_wrapper = Adw.PreferencesRow(activatable=False, selectable=False)
        link_wrapper.set_child(link_box)
        g_links.add(link_wrapper)

        on_change(self._refresh_labels)
        self._refresh_labels()

    def _make_copy_row(self, group, value, label_key, btn_key, confirm_key):
        row = Adw.ActionRow(subtitle=value)
        row.set_activatable(False)
        btn = Gtk.Button()
        btn.connect("clicked", lambda _b: self._on_copy(value, btn, btn_key, confirm_key))
        row.add_suffix(btn)
        group.add(row)
        return {"row": row, "btn": btn, "label_key": label_key, "btn_key": btn_key}

    def _on_copy(self, value, btn, btn_key, confirm_key):
        self.get_clipboard().set(value)
        original_label = T(btn_key)
        btn.set_label(T(confirm_key))
        btn.set_sensitive(False)

        def restore():
            btn.set_label(original_label)
            btn.set_sensitive(True)
            return False

        GLib.timeout_add_seconds(_COPY_CONFIRMATION_SECONDS, restore)

    def _refresh_labels(self):
        self._intro_lbl.set_text(T("donate_intro"))
        self._paypal_btn.set_label(T("donate_paypal_btn"))
        for entry in (self._iban_row, self._holder_row, self._swift_row):
            entry["row"].set_title(T(entry["label_key"]))
            entry["btn"].set_label(T(entry["btn_key"]))
        self._contact_btn.set_label(T("donate_contact_btn"))
        self._project_btn.set_label(T("donate_project_page_btn"))

    def _refresh_title(self):
        self.set_title(T("tab_donate"))
