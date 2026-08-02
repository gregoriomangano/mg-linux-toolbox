"""
SelectControl — standardized dropdown for choices not suited to a
SegmentedControl (more than ~3 values, or a set that varies at
runtime — e.g. system76-power's profile list). Wraps Gtk.DropDown so
every page's dropdown gets the same height/border/radius/shadow
instead of each page using the raw Adwaita control with whatever
default chrome it happens to have.

Purely presentational: the caller supplies the already-real values and
reads back the selected index exactly as it would from a plain
Gtk.DropDown — no new lookup/validation logic is introduced here.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class SelectControl(Gtk.Box):
    def __init__(self, labels: list[str], selected: int = 0, technical_labels: list[str] = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("ds-select-wrap")

        self.dropdown = Gtk.DropDown(model=Gtk.StringList.new(labels), valign=Gtk.Align.CENTER)
        self.dropdown.add_css_class("ds-select")
        self.dropdown.set_selected(selected)
        self.append(self.dropdown)

        self._technical_labels = technical_labels
        self._technical_lbl = None
        if technical_labels:
            self._technical_lbl = Gtk.Label(xalign=1, wrap=True)
            self._technical_lbl.add_css_class("ds-select-technical")
            self._sync_technical_label()
            self.append(self._technical_lbl)
            self.dropdown.connect("notify::selected", lambda *_a: self._sync_technical_label())

    def _sync_technical_label(self):
        if not self._technical_lbl:
            return
        idx = self.dropdown.get_selected()
        if 0 <= idx < len(self._technical_labels):
            self._technical_lbl.set_text(self._technical_labels[idx])

    def get_selected(self) -> int:
        return self.dropdown.get_selected()

    def set_selected(self, index: int):
        self.dropdown.set_selected(index)
