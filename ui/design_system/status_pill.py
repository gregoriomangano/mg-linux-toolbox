"""
StatusPill — small rounded label for a state word ("Attivo",
"Disattivato", "Installato", "Non installato", "Non disponibile"...).
Five canonical semantic states, matching the central palette:

  success  — Attivo / Installato / Sempre attiva / Disponibile   (verde)
  neutral  — Disattivato / Non configurato / Sola lettura         (grigio-blu)
  warning  — Richiede riavvio / Configurazione incompleta         (arancione)
  absent   — Non installato / Non disponibile / Non supportato    (grigio, leggibile)
  danger   — Errore / Operazione fallita                          (rosso)

Never the only way a state is conveyed — the text itself always says
the state in words, and color is a coordinated supplement.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

VARIANT_CSS = {
    "success": "ds-pill-success",
    "neutral": "ds-pill-neutral",
    "warning": "ds-pill-warning",
    "absent":  "ds-pill-absent",
    "danger":  "ds-pill-danger",
    # legacy aliases kept so earlier v2/v3 call sites keep working
    "info":    "ds-pill-neutral",
}

_CHECK_ICON = "object-select-symbolic"


class StatusPill(Gtk.Box):
    def __init__(self, text: str, variant: str = "neutral", show_check: bool = False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.add_css_class("ds-pill")
        self._variant = variant
        self.add_css_class(VARIANT_CSS.get(variant, "ds-pill-neutral"))
        self.set_valign(Gtk.Align.CENTER)

        self._icon = Gtk.Image.new_from_icon_name(_CHECK_ICON)
        self._icon.add_css_class("ds-pill-icon")
        self._icon.set_visible(show_check)
        self.append(self._icon)

        self._label = Gtk.Label(label=text)
        self.append(self._label)

    def set_variant(self, variant: str):
        self.remove_css_class(VARIANT_CSS.get(self._variant, "ds-pill-neutral"))
        self._variant = variant
        self.add_css_class(VARIANT_CSS.get(variant, "ds-pill-neutral"))

    def set_text(self, text: str):
        self._label.set_text(text)

    def set_show_check(self, show: bool):
        self._icon.set_visible(show)


# Canonical (variant, show_check) for the state words named in the
# design-system spec — pass the already-translated text separately so
# this stays i18n-agnostic.
_CANONICAL_STATES = {
    "active":        ("success", True),
    "installed":     ("success", True),
    "always_on":     ("success", True),
    "configured":    ("success", True),
    "available":     ("success", True),
    "inactive":      ("neutral", False),
    "not_configured": ("neutral", False),
    "readonly":      ("neutral", False),
    "unknown":       ("neutral", False),
    "needs_reboot":  ("warning", False),
    "incomplete":    ("warning", False),
    "check_needed":  ("warning", False),
    "not_installed": ("absent", False),
    "not_available":  ("absent", False),
    "not_supported":  ("absent", False),
    "error":         ("danger", False),
    "failed":        ("danger", False),
}


def state_pill(state: str, text: str) -> StatusPill:
    """state: one of _CANONICAL_STATES' keys — picks the right
    variant/check-icon combo automatically. Falls back to a plain
    neutral pill for anything not in the table (never crashes on an
    unexpected state name)."""
    variant, show_check = _CANONICAL_STATES.get(state, ("neutral", False))
    return StatusPill(text, variant=variant, show_check=show_check)
