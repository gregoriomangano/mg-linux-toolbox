"""
QuickAction — a clickable dashboard block: icon + title + short
description + arrow. Only ever wired to navigate to an existing real
page (via a plain callback); never applies any change by itself.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class QuickAction(Gtk.Button):
    def __init__(self, icon_name: str, title: str, description: str, on_click=None):
        super().__init__()
        self.add_css_class("mgv2-quick-action")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        icon_wrap = Gtk.Box()
        icon_wrap.add_css_class("mgv2-quick-action-icon-wrap")
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.add_css_class("mgv2-quick-action-icon")
        icon_wrap.append(icon)
        box.append(icon_wrap)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        title_lbl = Gtk.Label(label=title, xalign=0)
        title_lbl.add_css_class("mgv2-quick-action-title")
        desc_lbl = Gtk.Label(label=description, xalign=0, wrap=True)
        desc_lbl.add_css_class("mgv2-quick-action-desc")
        text_box.append(title_lbl)
        text_box.append(desc_lbl)
        box.append(text_box)

        arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
        arrow.add_css_class("mgv2-quick-action-arrow")
        box.append(arrow)

        self.set_child(box)
        if on_click is not None:
            self.connect("clicked", lambda _b: on_click())
