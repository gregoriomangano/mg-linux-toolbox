"""
RadialGauge — reusable circular progress indicator (Gtk.DrawingArea +
Cairo for the ring, Gtk.Label overlay for the text). Text is rendered
by Pango via normal GTK labels, never drawn by hand with Cairo
show_text, so HiDPI scaling and font metrics stay correct automatically.

The widget only holds display state (fraction, center text) — it never
reads /proc or /sys itself, so it's fully testable without any real
system data: `RadialGauge(); g.set_fraction(0.5); g.set_center_text("50%", "test")`.
"""
import math

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
import cairo

# Coordinated but muted per-resource colors (not fluorescent), (r, g, b, a) 0..1.
COLOR_CPU   = (0.42, 0.78, 0.65, 1.0)   # green/teal
COLOR_RAM   = (0.45, 0.62, 0.86, 1.0)   # blue
COLOR_DISK  = (0.85, 0.68, 0.32, 1.0)   # gold
COLOR_SWAP  = (0.87, 0.56, 0.35, 1.0)   # orange

DEFAULT_TRACK_RGBA = (1, 1, 1, 0.08)


class RadialGauge(Gtk.Overlay):
    def __init__(self, diameter: int = 108, thickness: int = 10,
                 arc_rgba=COLOR_CPU, icon_name: str = None):
        super().__init__()
        self._thickness = thickness
        self._fraction = 0.0
        self._arc_rgba = arc_rgba

        self._area = Gtk.DrawingArea()
        self._area.set_content_width(diameter)
        self._area.set_content_height(diameter)
        self._area.set_draw_func(self._on_draw)
        self.set_child(self._area)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text_box.set_halign(Gtk.Align.CENTER)
        text_box.set_valign(Gtk.Align.CENTER)
        text_box.set_can_target(False)

        if icon_name:
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.add_css_class("mgv2-gauge-icon")
            text_box.append(icon)

        self._value_lbl = Gtk.Label()
        self._value_lbl.add_css_class("mgv2-gauge-value")
        text_box.append(self._value_lbl)

        self._caption_lbl = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._caption_lbl.add_css_class("mgv2-gauge-caption")
        self._caption_lbl.set_max_width_chars(13)
        text_box.append(self._caption_lbl)

        self.add_overlay(text_box)

    def set_fraction(self, fraction: float):
        self._fraction = min(max(float(fraction), 0.0), 1.0)
        self._area.queue_draw()

    def get_fraction(self) -> float:
        return self._fraction

    def set_center_text(self, value_text: str, caption_text: str = ""):
        self._value_lbl.set_text(value_text)
        self._caption_lbl.set_text(caption_text)

    def _on_draw(self, _area, cr, width, height):
        cx, cy = width / 2, height / 2
        radius = (min(width, height) - self._thickness) / 2
        start_angle = -math.pi / 2
        end_angle = start_angle + 2 * math.pi * self._fraction

        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_width(self._thickness)

        cr.set_source_rgba(*DEFAULT_TRACK_RGBA)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        if self._fraction > 0:
            cr.set_source_rgba(*self._arc_rgba)
            cr.arc(cx, cy, radius, start_angle, end_angle)
            cr.stroke()
