"""
Reusable row for one KernelFeature. Built on Adw.ExpanderRow, same visual
language as the rest of the app (badge chips, desc-what/pro/con classes
already in style.css), but driven by structured feature data instead of a
bare key_prefix string — kernel features need more state than a switch.

Mandatory layout inside the expanded body (per the master spec, corrected):

    [Risk badge]
    ❓ Cos'è?
    ✅ Vantaggio
    ⚠️ Quando evitare
    Stato attuale: <friendly> (valore tecnico: <raw>)
    [optional: choices box — presets/available values, feature-specific]
    Valore prima della modifica: <friendly>
    [Prova fino al riavvio] [Rendi permanente + caption] [Ripristina]
    [Mostra dettagli] (only appears after a failed operation)

Never claims a value is universally "recommended" — see set_no_action_
needed() / choices are always presented as optional.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
from core.i18n import T, on_change
from core.kernel_features.base import SupportStatus

RISK_CSS = {"low": "badge-low", "medium": "badge-medium", "high": "badge-high"}


class KernelFeatureRow(Adw.ExpanderRow):
    def __init__(self, feature, i18n_key_base: str):
        """
        feature: a KernelFeature instance.
        i18n_key_base: prefix for this feature's own text, e.g. "kf_swappiness"
                       -> looks up kf_swappiness_title/_desc/_pro/_con.
        """
        super().__init__()
        self.feature = feature
        self._key = i18n_key_base
        self.set_enable_expansion(True)

        # ── Collapsed-state suffix: compact current status ──────────
        # v4: shown as a StatusPill (neutral by default) instead of
        # plain grey text — set_status_variant() lets a subclass that
        # actually knows the semantics (e.g. "this boolean is on")
        # recolor it, without changing what text is displayed or any
        # apply/restore logic.
        self._status_suffix = Gtk.Label(xalign=1)
        self._status_suffix.add_css_class("ds-pill")
        self._status_suffix.add_css_class("ds-pill-neutral")
        self.add_suffix(self._status_suffix)

        # ── Expanded body ────────────────────────────────────────────
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(10)
        body.set_margin_bottom(14)
        body.set_margin_start(14)
        body.set_margin_end(14)

        badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._risk_badge = Gtk.Label()
        self._risk_badge.add_css_class(RISK_CSS.get(feature.risk, "badge-low"))
        badge_box.append(self._risk_badge)
        body.append(badge_box)

        self._lbl_what = Gtk.Label(wrap=True, xalign=0)
        self._lbl_pro = Gtk.Label(wrap=True, xalign=0)
        self._lbl_con = Gtk.Label(wrap=True, xalign=0)
        self._lbl_what.add_css_class("desc-what")
        self._lbl_pro.add_css_class("desc-pro")
        self._lbl_con.add_css_class("desc-con")
        body.append(self._lbl_what)
        body.append(self._lbl_pro)
        body.append(self._lbl_con)

        # Current / initial value block
        self._lbl_current = Gtk.Label(wrap=True, xalign=0, selectable=True)
        self._lbl_current.add_css_class("sysinfo-value")
        body.append(self._lbl_current)

        self._lbl_no_action = Gtk.Label(wrap=True, xalign=0)
        self._lbl_no_action.add_css_class("desc-pro")
        self._lbl_no_action.set_visible(False)
        body.append(self._lbl_no_action)

        # Extension point: feature-specific optional presets / choices
        self.choices_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.append(self.choices_box)

        self._lbl_initial = Gtk.Label(wrap=True, xalign=0)
        self._lbl_initial.add_css_class("sysinfo-value-sub")
        body.append(self._lbl_initial)

        # Action buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_try = Gtk.Button()
        self.btn_try.add_css_class("lt-action-btn")
        btn_box.append(self.btn_try)

        permanent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.btn_permanent = Gtk.Button()
        permanent_box.append(self.btn_permanent)
        self._lbl_permanent_caption = Gtk.Label(wrap=True, xalign=0)
        self._lbl_permanent_caption.add_css_class("sysinfo-value-sub")
        permanent_box.append(self._lbl_permanent_caption)
        btn_box.append(permanent_box)

        self.btn_restore = Gtk.Button()
        btn_box.append(self.btn_restore)
        body.append(btn_box)

        # Error disclosure — never show raw errors directly
        self._lbl_error = Gtk.Label(wrap=True, xalign=0)
        self._lbl_error.add_css_class("desc-con")
        self._lbl_error.set_visible(False)
        body.append(self._lbl_error)

        self._details_btn = Gtk.Button()
        self._details_btn.add_css_class("flat")
        self._details_btn.set_visible(False)
        self._details_btn.connect("clicked", self._on_toggle_details)
        body.append(self._details_btn)

        self._lbl_details = Gtk.Label(wrap=True, xalign=0, selectable=True)
        self._lbl_details.add_css_class("sysinfo-value-sub")
        self._lbl_details.set_visible(False)
        body.append(self._lbl_details)

        self.add_row(body)

        on_change(self.refresh_labels)
        self.refresh_labels()

    # ── Static text (translated) ─────────────────────────────────────
    def refresh_labels(self):
        self.set_title(T(f"{self._key}_title"))
        self.set_subtitle(f"{T('kf_technical_name')}: {self.feature.technical_name}")
        self._risk_badge.set_text(T(f"risk_{self.feature.risk}"))
        self._lbl_what.set_text(f"❓ {T('what_is')}: {T(f'{self._key}_desc')}")
        self._lbl_pro.set_text(f"✅ {T('advantage')}: {T(f'{self._key}_pro')}")
        self._lbl_con.set_text(f"⚠️ {T('when_avoid')}: {T(f'{self._key}_con')}")
        self.btn_try.set_label(T("kf_try_btn"))
        self.btn_permanent.set_label(T("kf_make_permanent_btn"))
        self._lbl_permanent_caption.set_text(T("kf_permanent_caption"))
        self.btn_restore.set_label(T("kf_restore_btn"))
        self._details_btn.set_label(T("kf_show_details_btn"))

    # ── Dynamic state, called by the page after every read/apply ─────
    def set_status_line(self, friendly: str, technical: str = ""):
        text = friendly if not technical else f"{friendly}  ({T('kf_technical_value')}: {technical})"
        self._status_suffix.set_text(friendly)
        self._lbl_current.set_text(f"{T('kf_current_state')}: {text}")

    _STATUS_VARIANT_CSS = {
        "success": "ds-pill-success", "neutral": "ds-pill-neutral",
        "warning": "ds-pill-warning", "absent": "ds-pill-absent", "danger": "ds-pill-danger",
    }

    def set_status_variant(self, variant: str):
        """Recolors the collapsed-state StatusPill only — never touches
        the text set_status_line() already put there, never touches
        any callback. Subclasses call this once they know whether the
        current value is a "good/on" state or not."""
        for css in self._STATUS_VARIANT_CSS.values():
            self._status_suffix.remove_css_class(css)
        self._status_suffix.add_css_class(self._STATUS_VARIANT_CSS.get(variant, "ds-pill-neutral"))

    def set_initial_value(self, friendly: str):
        self._lbl_initial.set_text(f"{T('kf_initial_value')}: {friendly}" if friendly else "")
        self._lbl_initial.set_visible(bool(friendly))

    def set_no_action_needed(self, visible: bool):
        """Per spec: never invent a universally-best value — only say this
        when we've genuinely found nothing worth changing."""
        self._lbl_no_action.set_text(T("kf_no_action_needed"))
        self._lbl_no_action.set_visible(visible)

    def set_support_status(self, status: SupportStatus):
        """Shows/hides the action buttons based on what's really possible."""
        can_try = status in (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_PERSISTENT)
        can_persist = status == SupportStatus.SUPPORTED_PERSISTENT
        self.btn_try.set_visible(can_try)
        self.btn_permanent.set_visible(can_persist)
        self._lbl_permanent_caption.set_visible(can_persist)
        self.btn_restore.set_visible(can_try)
        # choices_box visibility is NOT tied to try-ability: read-only
        # features (e.g. PSI) also use it, just to display a breakdown
        # rather than pick a value. The page-level code controls it.

        # Explanatory tooltip even while hidden — harmless (GTK never
        # shows a tooltip on an invisible widget), but correct and
        # future-proof if a row ever shows-but-disables instead of
        # hiding for one of these statuses.
        if status == SupportStatus.SUPPORTED_READ_ONLY:
            self.btn_try.set_tooltip_text(T("kf_try_reason_read_only"))
        elif status in (SupportStatus.UNSUPPORTED_KERNEL, SupportStatus.UNSUPPORTED_HARDWARE, SupportStatus.UNAVAILABLE):
            self.btn_try.set_tooltip_text(T("kf_try_reason_unsupported"))

        if status == SupportStatus.UNSUPPORTED_KERNEL:
            self._lbl_no_action.set_text(T("kf_unsupported_kernel"))
            self._lbl_no_action.set_visible(True)
        elif status == SupportStatus.UNSUPPORTED_HARDWARE:
            self._lbl_no_action.set_text(T("kf_unsupported_hardware"))
            self._lbl_no_action.set_visible(True)
        elif status == SupportStatus.UNAVAILABLE:
            self._lbl_no_action.set_text(T("kf_unavailable"))
            self._lbl_no_action.set_visible(True)

    def set_try_sensitive(self, enabled: bool, reason_key: str = "kf_try_reason_pick_different"):
        """Sets btn_try's enabled state AND an explanatory tooltip for
        why it's disabled — per spec, never a single generic tooltip
        when the row already knows the specific reason. `reason_key` is
        only used when enabled=False; the tooltip is cleared otherwise."""
        self.btn_try.set_sensitive(enabled)
        self.btn_try.set_tooltip_text("" if enabled else T(reason_key))

    def set_restore_enabled(self, enabled: bool, reason_key: str = "kf_restore_reason_nothing"):
        self.btn_restore.set_sensitive(enabled)
        self.btn_restore.set_tooltip_text("" if enabled else T(reason_key))

    def show_error(self, friendly_key: str, technical_detail: str = ""):
        self._lbl_error.set_text(T(friendly_key) if friendly_key else T("kf_err_generic"))
        self._lbl_error.set_visible(True)
        self._details_btn.set_visible(bool(technical_detail))
        self._lbl_details.set_text(technical_detail)
        self._lbl_details.set_visible(False)

    def clear_error(self):
        self._lbl_error.set_visible(False)
        self._details_btn.set_visible(False)
        self._lbl_details.set_visible(False)

    def _on_toggle_details(self, _btn):
        self._lbl_details.set_visible(not self._lbl_details.get_visible())


def handle_restore_click(row: KernelFeatureRow, restore_fn, on_done):
    """
    Shared "Restore" flow for any modifiable feature: try a plain
    restore(); if it reports an external change, ask for confirmation
    before forcing it. `restore_fn(force)` must call feature.restore(force=force).
    `on_done()` is called after any successful restore to let the row
    refresh its displayed state.
    """
    result = restore_fn(force=False)
    if result.friendly_message == "kf_external_change_detected":
        dialog = Adw.MessageDialog(
            transient_for=row.get_root(),
            heading=T("kf_restore_btn"),
            body=T("kf_external_change_detected"),
        )
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("kf_restore_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_dialog, response):
            if response == "confirm":
                forced = restore_fn(force=True)
                if not forced.ok:
                    row.show_error(forced.friendly_message, forced.technical_detail)
                else:
                    on_done()

        dialog.connect("response", on_response)
        dialog.present()
        return

    if not result.ok:
        row.show_error(result.friendly_message, result.technical_detail)
        return
    on_done()
