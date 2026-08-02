"""
Shared "Leggi la licenza" window — a plain read-only text view showing
the real LICENSE file content, reused by both the "Informazioni" window
and the "Crediti" page so the license text lives in exactly one place.

Bug found and fixed in this pass: Adw.Window's `transient_for` requires
a real Gtk.Window, but the "Crediti" page passed itself (an
Adw.PreferencesPage, not a window) — that raised a TypeError inside the
button's "clicked" handler, which GTK swallows into a stderr traceback
instead of surfacing it, so the button looked like it "did nothing".
Fixed by resolving the real top-level window via get_root() whenever
the caller isn't already one.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from core import release_config
from core.i18n import T
from core.uri_launcher import open_external_url

GPL_OFFICIAL_URL = "https://www.gnu.org/licenses/gpl-3.0.html"


def _read_license_text() -> "str | None":
    """None (not a placeholder string) when the file is genuinely
    missing, so the caller can tell "real text" apart from "missing"
    and still offer the official-page link either way."""
    try:
        with open(release_config.license_file_path(), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _resolve_window(widget):
    """Accepts either a real Gtk.Window or any child widget already
    attached to one — never crashes transient_for with the wrong type."""
    if isinstance(widget, Gtk.Window):
        return widget
    return widget.get_root() if widget is not None else None


def show_license_window(parent):
    root = _resolve_window(parent)
    window = Adw.Window(modal=True, transient_for=root)
    window.set_default_size(640, 720)
    window.set_title(T("license_window_title"))

    toolbar_view = Adw.ToolbarView()
    window.set_content(toolbar_view)
    header = Adw.HeaderBar()
    toolbar_view.add_top_bar(header)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

    license_text = _read_license_text()
    if license_text is None:
        missing_lbl = Gtk.Label(label=T("license_file_missing"), wrap=True, xalign=0)
        missing_lbl.set_margin_top(24)
        missing_lbl.set_margin_bottom(12)
        missing_lbl.set_margin_start(16)
        missing_lbl.set_margin_end(16)
        content.append(missing_lbl)
    else:
        text_view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
        text_view.add_css_class("monospace")
        text_view.set_top_margin(12)
        text_view.set_bottom_margin(12)
        text_view.set_left_margin(12)
        text_view.set_right_margin(12)
        text_view.get_buffer().set_text(license_text)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(text_view)
        content.append(scroller)

    official_btn = Gtk.Button(label=T("license_official_page_btn"))
    official_btn.set_margin_top(8)
    official_btn.set_margin_bottom(12)
    official_btn.set_margin_start(16)
    official_btn.set_margin_end(16)
    official_btn.set_halign(Gtk.Align.START)
    official_btn.connect("clicked", lambda _b: open_external_url(GPL_OFFICIAL_URL))
    content.append(official_btn)

    toolbar_view.set_content(content)
    window.present()
