"""
"Guida" — a short native explanation of the app's core concepts
(Stato attuale, Prova fino al riavvio, Rendi permanente, Ripristina,
Cronologia, checkpoint vs. snapshot), plus a link to the full online
guide. Never embeds a browser — the online link opens the real system
browser via core.uri_launcher.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from core.i18n import T, on_change
from core.uri_launcher import open_external_url
from ui.widgets import make_group

GUIDE_URL = "https://www.manganogregorio.it/m-g-linux-toolbox/"

_SECTIONS = [
    ("guide_intro_title", "guide_intro_body"),
    ("guide_open_feature_title", "guide_open_feature_body"),
    ("guide_current_state_title", "guide_current_state_body"),
    ("guide_try_title", "guide_try_body"),
    ("guide_permanent_title", "guide_permanent_body"),
    ("guide_restore_title", "guide_restore_body"),
    ("guide_missing_features_title", "guide_missing_features_body"),
    ("guide_password_title", "guide_password_body"),
    ("guide_history_title", "guide_history_body"),
    ("guide_checkpoint_vs_snapshot_title", "guide_checkpoint_vs_snapshot_body"),
]


class GuidePage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("help-faq-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        g = make_group("tab_guide")
        self.add(g)
        self._rows = []
        for title_key, body_key in _SECTIONS:
            row = Adw.ExpanderRow()
            body_lbl = Gtk.Label(wrap=True, xalign=0)
            body_lbl.set_margin_top(6)
            body_lbl.set_margin_bottom(10)
            body_lbl.set_margin_start(14)
            body_lbl.set_margin_end(14)
            row.add_row(body_lbl)
            g.add(row)
            self._rows.append((row, title_key, body_lbl, body_key))

        online_btn = Gtk.Button()
        online_btn.add_css_class("lt-action-btn")
        online_btn.connect("clicked", lambda _b: open_external_url(GUIDE_URL))
        wrapper = Adw.PreferencesRow(activatable=False, selectable=False)
        wrapper.set_child(online_btn)
        g.add(wrapper)
        self._online_btn = online_btn

        on_change(self._refresh_labels)
        self._refresh_labels()

    def _refresh_labels(self):
        for row, title_key, body_lbl, body_key in self._rows:
            row.set_title(T(title_key))
            body_lbl.set_text(T(body_key))
        self._online_btn.set_label(T("guide_open_online_btn"))

    def _refresh_title(self):
        self.set_title(T("tab_guide"))
