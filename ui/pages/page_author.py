"""
"Chi sono" — a short, plain biography with the author's photo and
internal links to Guida/Supporta il progetto/Crediti. Never opens a
browser for these three (that's what the in-app tabs are for); any
truly external link elsewhere in the app goes through
core.uri_launcher, never here.
"""
import os

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from core.i18n import T, on_change
from ui.widgets import load_image_or_placeholder

_PHOTO_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "images", "gregorio-profilo.jpg"
)


class AuthorPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("avatar-default-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        g_bio = Adw.PreferencesGroup()
        self.add(g_bio)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        photo = load_image_or_placeholder(_PHOTO_PATH, "avatar-default-symbolic",
                                           "author_photo_placeholder", size=160)
        photo.set_halign(Gtk.Align.CENTER)
        photo.add_css_class("card")
        content.append(photo)

        self._bio_lbl = Gtk.Label(wrap=True, xalign=0)
        self._bio_lbl.set_justify(Gtk.Justification.FILL)
        content.append(self._bio_lbl)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.CENTER)
        self._guide_btn = Gtk.Button()
        self._guide_btn.add_css_class("lt-action-btn")
        self._guide_btn.connect("clicked", lambda _b: self._navigate("guide"))
        self._donate_btn = Gtk.Button()
        self._donate_btn.add_css_class("lt-action-btn")
        self._donate_btn.connect("clicked", lambda _b: self._navigate("donate"))
        self._credits_btn = Gtk.Button()
        self._credits_btn.connect("clicked", lambda _b: self._navigate("credits"))
        for b in (self._guide_btn, self._donate_btn, self._credits_btn):
            btn_box.append(b)
        content.append(btn_box)

        wrapper = Adw.PreferencesRow(activatable=False, selectable=False)
        wrapper.set_child(content)
        g_bio.add(wrapper)

        on_change(self._refresh_labels)
        self._refresh_labels()

    def _navigate(self, internal_name: str):
        root = self.get_root()
        if root is not None and hasattr(root, "switch_to_page"):
            root.switch_to_page(internal_name)

    def _refresh_labels(self):
        self._bio_lbl.set_text(T("author_bio"))
        self._guide_btn.set_label(T("author_guide_btn"))
        self._donate_btn.set_label(T("author_donate_btn"))
        self._credits_btn.set_label(T("author_credits_btn"))

    def _refresh_title(self):
        self.set_title(T("tab_author"))
