"""
PageHeader — the modern opening card every migrated inner page starts
with: a large IconBadge, a simple title, a short description, and an
optional status/count area. No absolute promises — the count/status is
only ever real data the caller already computed.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ui.design_system.icon_badge import IconBadge


class PageHeader(Gtk.Box):
    def __init__(self, icon_name: str, title: str, description: str,
                 category: str = "neutral", trailing_widget: Gtk.Widget = None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.add_css_class("ds-page-header")

        self.append(IconBadge(icon_name, category=category, size="lg"))

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        text_box.set_valign(Gtk.Align.CENTER)
        title_lbl = Gtk.Label(label=title, xalign=0, wrap=True)
        title_lbl.add_css_class("ds-page-header-title")
        self._desc_lbl = Gtk.Label(label=description, xalign=0, wrap=True)
        self._desc_lbl.add_css_class("ds-page-header-desc")
        text_box.append(title_lbl)
        text_box.append(self._desc_lbl)
        self.append(text_box)

        if trailing_widget is not None:
            trailing_widget.set_valign(Gtk.Align.CENTER)
            self.append(trailing_widget)

    def set_description(self, text: str):
        self._desc_lbl.set_text(text)


def wrap_in_preferences_group(header: Gtk.Widget) -> Adw.PreferencesGroup:
    """Adw.PreferencesPage only accepts Adw.PreferencesGroup children —
    this is the same 'hero row inside a group' pattern already used by
    the original InfoPage, reused here so every migrated page can drop
    a PageHeader in with one call."""
    group = Adw.PreferencesGroup()
    row = Adw.ActionRow()
    row.set_activatable(False)
    row.set_child(header)
    group.add(row)
    return group
