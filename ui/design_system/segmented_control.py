"""
SegmentedControl — a modern [ A | B | C ] picker for a small, fixed set
of values (e.g. the 3 power profiles). Built from plain
Gtk.ToggleButtons in a linked box — no new backend logic, the caller
still reads/writes the exact same technical values as before.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class SegmentedControl(Gtk.Box):
    def __init__(self, options: list[tuple], selected_value=None):
        """options: [(technical_value, display_label), ...]."""
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add_css_class("ds-segmented")

        self._buttons = {}
        self._on_changed = None
        group_leader = None
        for value, label in options:
            btn = Gtk.ToggleButton(label=label)
            btn.add_css_class("ds-segmented-btn")
            if group_leader is not None:
                btn.set_group(group_leader)
            else:
                group_leader = btn
            btn.set_active(value == selected_value)
            btn.connect("toggled", self._on_toggled, value)
            self.append(btn)
            self._buttons[value] = btn

    def _on_toggled(self, btn, value):
        if btn.get_active() and self._on_changed:
            self._on_changed(value)

    def connect_changed(self, callback):
        """callback(value) — called with the technical value of the
        newly-selected segment, only when a real change happens."""
        self._on_changed = callback

    def set_selected(self, value):
        btn = self._buttons.get(value)
        if btn is not None:
            btn.set_active(True)
