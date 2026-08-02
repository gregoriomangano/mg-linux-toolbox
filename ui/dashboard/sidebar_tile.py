"""
SidebarTile — a single sidebar navigation entry styled as a real
block (icon inside a small rounded container + title), not plain text
sitting in a flat list.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class SidebarTile(Gtk.Button):
    def __init__(self, icon_name: str, label_text: str):
        super().__init__()
        self.add_css_class("mgv2-sidebar-tile")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        icon_wrap = Gtk.Box()
        icon_wrap.add_css_class("mgv2-sidebar-tile-icon-wrap")
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.add_css_class("mgv2-sidebar-tile-icon")
        icon_wrap.append(icon)
        box.append(icon_wrap)

        self._label = Gtk.Label(label=label_text, xalign=0, hexpand=True)
        self._label.add_css_class("mgv2-sidebar-tile-label")
        box.append(self._label)

        self.set_child(box)
        self.set_tooltip_text(label_text)

    def set_label_text(self, text: str):
        self._label.set_text(text)
        self.set_tooltip_text(text)

    @property
    def label_widget(self) -> Gtk.Label:
        """Exposed so a responsive breakpoint can hide just the text
        (icon-only compact mode) without touching the button itself."""
        return self._label

    def set_active(self, active: bool):
        if active:
            self.add_css_class("active")
        else:
            self.remove_css_class("active")
