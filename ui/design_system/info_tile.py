"""
InfoTile — small labeled mini-card (title + body text), used to give
"Cos'è / Vantaggio / Quando evitare"-style content its own visually
boxed block instead of a plain paragraph. Purely presentational: takes
already-translated text, never fetches or interprets anything itself.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

VARIANT_CSS = {
    "neutral": "ds-info-tile-neutral",
    "success": "ds-info-tile-success",
    "warning": "ds-info-tile-warning",
}


class InfoTile(Gtk.Box):
    def __init__(self, title: str, body: str, variant: str = "neutral"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("ds-info-tile")
        self.add_css_class(VARIANT_CSS.get(variant, "ds-info-tile-neutral"))

        self._title_lbl = Gtk.Label(label=title, xalign=0)
        self._title_lbl.add_css_class("ds-info-tile-title")
        self.append(self._title_lbl)

        self._body_lbl = Gtk.Label(label=body, xalign=0, wrap=True)
        self._body_lbl.add_css_class("ds-info-tile-body")
        self.append(self._body_lbl)

    def set_title(self, title: str):
        self._title_lbl.set_text(title)

    def set_body(self, body: str):
        self._body_lbl.set_text(body)
