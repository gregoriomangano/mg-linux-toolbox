"""
action_bar — applies the modern button hierarchy (primary/secondary/
restore/danger) to buttons that ALREADY exist on a KernelFeatureRow or
FeatureRow instance (btn_try, btn_permanent, btn_restore — all public
attributes those classes already expose for exactly this purpose).

This never creates a new button, never rewires a callback, never
changes what a button does — it only adds a CSS class so the existing
"Prova fino al riavvio" / "Rendi permanente" / "Ripristina" buttons
read as a clear hierarchy instead of three same-looking buttons.
"""

PRIMARY = "ds-btn-primary"      # "Prova fino al riavvio" — the main action
SECONDARY = "ds-btn-secondary"  # "Rendi permanente" — deliberate, less frequent
RESTORE = "ds-btn-restore"      # "Ripristina" — neutral/amber, not destructive-looking
DANGER = "ds-btn-danger"        # reserved for genuinely destructive actions only


def style_kernel_feature_row_buttons(row) -> None:
    """row: any KernelFeatureRow instance (or subclass). Safe to call
    even when a given button is hidden/unused by that row."""
    if hasattr(row, "btn_try"):
        row.btn_try.add_css_class(PRIMARY)
    if hasattr(row, "btn_permanent"):
        row.btn_permanent.add_css_class(SECONDARY)
    if hasattr(row, "btn_restore"):
        row.btn_restore.add_css_class(RESTORE)
