"""
IconBadge — a symbolic icon inside a small colored rounded container.
Purely presentational: used to give every category (kernel/CPU,
memory, disk, network, energy, security, audio, virtualization) the
same container shape/size with a category-coordinated color, so pages
stop mixing emoji/Adwaita icons/plain text at random.

Color is never the ONLY signal — the icon shape and the page/section
title still carry the meaning; the color is a coordinated accent.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

# Category -> CSS class, matching the central palette in style.css.
CATEGORY_CSS = {
    "kernel":  "ds-icon-badge-kernel",   # gold/orange
    "cpu":     "ds-icon-badge-kernel",
    "memory":  "ds-icon-badge-memory",   # blue
    "disk":    "ds-icon-badge-disk",     # purple/gold
    "network": "ds-icon-badge-network",  # cyan
    "energy":  "ds-icon-badge-energy",   # yellow
    "security-ok":   "ds-icon-badge-security-ok",    # green
    "security-risk": "ds-icon-badge-security-risk",  # red
    "audio":   "ds-icon-badge-audio",    # violet
    "virt":    "ds-icon-badge-virt",     # blue
    "software": "ds-icon-badge-software",  # teal — Software e repository
    "neutral": "ds-icon-badge-neutral",  # grey
}


class IconBadge(Gtk.Box):
    def __init__(self, icon_name: str, category: str = "neutral", size: str = "md"):
        """size: "sm" (used inline, e.g. row prefix) or "lg" (PageHeader)."""
        super().__init__()
        self.add_css_class("ds-icon-badge")
        self.add_css_class("ds-icon-badge-lg" if size == "lg" else "ds-icon-badge-sm")
        self.add_css_class(CATEGORY_CSS.get(category, "ds-icon-badge-neutral"))
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(26 if size == "lg" else 16)
        self.append(icon)
