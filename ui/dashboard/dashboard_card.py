"""
DashboardCard — reusable depth-tiered card container for the redesigned
Panoramica. Three levels map to the three visual layers above the
window background: 1 = hero (most elevated), 2 = normal dashboard
card, 3 = nested sub-card.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

_LEVEL_CSS = {
    1: "mgv2-card-hero",
    2: "mgv2-card",
    3: "mgv2-card-sub",
}


class DashboardCard(Gtk.Box):
    def __init__(self, level: int = 2, orientation=Gtk.Orientation.VERTICAL, spacing: int = 10):
        super().__init__(orientation=orientation, spacing=spacing)
        self.add_css_class(_LEVEL_CSS.get(level, "mgv2-card"))

    def add_header(self, title: str, icon_name: str = None,
                   badge_widget: Gtk.Widget = None) -> Gtk.Box:
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if icon_name:
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.add_css_class("mgv2-card-title-icon")
            head.append(icon)
        title_lbl = Gtk.Label(label=title, xalign=0, hexpand=True)
        title_lbl.add_css_class("mgv2-card-title")
        head.append(title_lbl)
        if badge_widget is not None:
            head.append(badge_widget)
        self.append(head)
        return head
