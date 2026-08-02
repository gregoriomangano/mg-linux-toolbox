"""
risk_badge — thin helper around the risk chip already used throughout
the app (badge-low/medium/high CSS classes, risk_low/medium/high i18n
keys). Does not reimplement risk logic — every FeatureRow/
KernelFeatureRow already computes and displays its own risk badge
inside the expanded body; this helper is only for places that need the
SAME look outside of those rows (e.g. a PageHeader-level rollup).
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
from core.i18n import T

_RISK_CSS = {"low": "badge-low", "medium": "badge-medium", "high": "badge-high"}


def risk_badge_label(risk: str) -> Gtk.Label:
    lbl = Gtk.Label(label=T(f"risk_{risk}"))
    lbl.add_css_class(_RISK_CSS.get(risk, "badge-low"))
    return lbl
