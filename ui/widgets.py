"""
Reusable UI widgets.
- FeatureRow: expandable row with i18n body, risk badge, dep-check banner
- SwitchRow:  FeatureRow + Switch
- InstallRow: FeatureRow + Install button
- DepBanner:  yellow warning shown when a required tool is missing
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib
from core.i18n import T, on_change
from core.executor import Job
import inspect
import logging
import os
import threading

logger = logging.getLogger(__name__)


def load_image_or_placeholder(path: str, placeholder_icon_name: str, placeholder_text_key: str,
                               size: int = 128) -> Gtk.Widget:
    """
    Real image if the file exists and actually loads; a plain icon +
    text placeholder otherwise — never a crash, never a broken-image
    icon, for a resource (a downloaded photo/QR code) that might be
    missing from a given install.
    """
    if os.path.isfile(path):
        try:
            picture = Gtk.Picture.new_for_filename(path)
            if picture.get_paintable() is not None:
                picture.set_content_fit(Gtk.ContentFit.COVER)
                picture.set_size_request(size, size)
                picture.set_can_shrink(True)
                return picture
        except GLib.Error:
            pass
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
    box.set_size_request(size, size)
    icon = Gtk.Image.new_from_icon_name(placeholder_icon_name)
    icon.set_pixel_size(min(size, 64))
    label = Gtk.Label(label=T(placeholder_text_key), wrap=True, xalign=0.5)
    label.add_css_class("dim-label")
    box.append(icon)
    box.append(label)
    return box

# After this many seconds still running, tell the user it's taking longer
# than expected and offer a real way out instead of a silent spinner.
SLOW_INSTALL_THRESHOLD_SECONDS = 15


def report_toggle_result(row, page: str, feature_id: str, ok: bool, technical_detail: str = "",
                          friendly_key: str = "kf_err_generic", device_id: str = None):
    """
    Shared failure path for the bespoke (non-KernelFeature-registry)
    privileged toggles — Wi-Fi, Bluetooth, IPv6, Firewall, SSH, Samba,
    CUPS, TRIM, SMART, KVM, package installs. The switch/button itself
    is ALWAYS already snapped back to the real re-read state by the
    caller before this runs (never an optimistic guess) — this only
    adds the missing half: a plain-language message on `row` (a
    FeatureRow, so "Mostra dettagli" is already wired) and a history
    entry, so a failed pkexec/systemctl call is never silent again.
    `friendly_key` should be "kf_err_helper_missing" when the failure is
    known to be caused by the privileged helper being absent/untrusted;
    "kf_err_generic" otherwise ("Non è stato possibile applicare questa
    modifica...").
    """
    if ok:
        row.clear_operation_error()
        return
    row.show_operation_error(friendly_key, technical_detail)
    try:
        from core.persistence import history_store as hs
        hs.record_operation(page, feature_id, hs.ERROR, False,
                            technical_detail=technical_detail, device_id=device_id)
    except Exception:
        logger.exception("Failed to record toggle failure to history")


def run_install_in_background(button: Gtk.Button, install_fn: callable,
                               verify_fn: callable, on_success: callable,
                               on_failure: callable = None):
    """
    Shared safe pattern for a plain InstallRow button that isn't wired
    through FeatureRow's dep_check/dep_install/DepBanner mechanism (Gaming,
    Audio and Virtualization pages call the backend install function
    directly from the button's "clicked" handler).

    Before this helper existed, those handlers called e.g.
    B.gamemode_install() straight from the signal handler — synchronously,
    on the GTK main thread. Since installs go through pkexec and a real
    package manager, that froze the *entire application window*
    (unresponsive, no repaints) for as long as the install took, with no
    exception handling at all. This mirrors the same fix already applied
    to DepBanner: run in a background thread, never trust the raw
    completion alone (verify_fn is the real check), and never let an
    unexpected exception leave the button stuck.
    """
    if not button.get_sensitive():
        return  # guard against a double-click starting a second install
    button.set_label("⏳")
    button.set_sensitive(False)

    def run():
        try:
            install_fn()
        except Exception:
            logger.exception("Unexpected error during install")
        try:
            installed = bool(verify_fn())
        except Exception:
            logger.exception("Unexpected error during post-install verification")
            installed = False
        GLib.idle_add(_finish_install_button, button, installed, on_success, on_failure)

    threading.Thread(target=run, daemon=True).start()


def _finish_install_button(button, installed, on_success, on_failure):
    if installed:
        on_success()
    else:
        button.set_label(T("install_btn"))
        button.set_sensitive(True)
        if on_failure is not None:
            on_failure()
    return False  # GLib.idle_add single shot


# ── Emoji icon map per scheda ────────────────────────────────────
TAB_ICONS = {
    "network": "🌐",
    "system":  "💾",
    "performance": "⚡",
    "gaming":  "🎮",
    "audio":   "🎵",
    "virt":    "📦",
    "security": "🔒",
}

RISK_CSS = {"low": "badge-low", "medium": "badge-medium", "high": "badge-high"}


# ── Dependency check banner ──────────────────────────────────────
class DepBanner(Gtk.Box):
    """
    Shown inside a FeatureRow when a required tool/module is missing.
    Displays an install command and a button.

    Never gets stuck on "Installazione…": the worker thread always
    reaches _done() through a try/except/finally-equivalent, a slow
    operation gets a "taking longer than expected" notice with a real
    Cancel, and a failure shows a generic message with a "Mostra
    dettagli" disclosure instead of raw technical text.
    """
    def __init__(self, pkg_label: str, install_callback, verify_callback=None, control=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._verify_callback = verify_callback
        self._control = control
        self.add_css_class("install-banner")
        self.set_margin_top(6)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = Gtk.Label(label="📦")
        msg  = Gtk.Label(
            label=f"Richiede: {pkg_label}",
            xalign=0, hexpand=True, wrap=True
        )
        msg.add_css_class("install-banner")

        btn = Gtk.Button(label=T("install_btn") + " ora")
        btn.add_css_class("lt-action-btn")
        btn.connect("clicked", lambda _: self._do_install(install_callback))

        top_row.append(icon)
        top_row.append(msg)
        top_row.append(btn)
        self.append(top_row)

        self._slow_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._slow_row.set_visible(False)
        self._slow_lbl = Gtk.Label(label=T("install_taking_longer"), xalign=0, hexpand=True, wrap=True)
        self._cancel_btn = Gtk.Button(label=T("install_cancel_btn"))
        self._cancel_btn.connect("clicked", self._on_cancel)
        self._slow_row.append(self._slow_lbl)
        self._slow_row.append(self._cancel_btn)
        self.append(self._slow_row)

        self._details_btn = Gtk.Button(label=T("install_show_details_btn"))
        self._details_btn.add_css_class("flat")
        self._details_btn.set_visible(False)
        self._details_btn.connect("clicked", self._on_toggle_details)
        self.append(self._details_btn)

        self._details_lbl = Gtk.Label(wrap=True, xalign=0, selectable=True)
        self._details_lbl.add_css_class("sysinfo-value-sub")
        self._details_lbl.set_visible(False)
        self.append(self._details_lbl)

        self._msg = msg
        self._btn = btn
        self._job = None
        self._slow_timer_id = None
        self._busy = False

    def _do_install(self, callback):
        if self._busy:
            return  # guard against a double-click starting a second install
        self._busy = True
        self._hide_details()
        self._slow_row.set_visible(False)
        self._btn.set_label(f"⏳ {T('install_in_progress')}")
        self._btn.set_sensitive(False)
        self._job = Job()
        self._slow_timer_id = GLib.timeout_add_seconds(
            SLOW_INSTALL_THRESHOLD_SECONDS, self._on_slow_timeout)

        # If the callback knows how to accept a Job (checked once, up
        # front, never by calling and retrying — a real install must
        # never risk running twice), give it one so Annulla can actually
        # reach the underlying subprocess instead of just hiding the
        # "taking longer" notice while the command keeps running.
        wants_job = False
        try:
            wants_job = "job" in inspect.signature(callback).parameters
        except (TypeError, ValueError):
            pass

        def run():
            try:
                result = callback(job=self._job) if wants_job else callback()
            except Exception as e:
                logger.exception("Unexpected error during install")
                result = _FailedResult(str(e))
            GLib.idle_add(self._done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_slow_timeout(self):
        self._slow_row.set_visible(True)
        return False  # one-shot

    def _on_cancel(self, _btn):
        if self._job is not None:
            self._job.cancel()
        self._slow_row.set_visible(False)

    def _clear_slow_timer(self):
        if self._slow_timer_id is not None:
            GLib.source_remove(self._slow_timer_id)
            self._slow_timer_id = None
        self._slow_row.set_visible(False)

    def _done(self, result):
        # Always leaves the "Installazione…" state, whatever the outcome —
        # this runs unconditionally, unlike the exception-vulnerable code
        # it replaces.
        try:
            self._busy = False
            # Trust real verification over the raw exit code whenever we
            # have a way to check — a command can exit 0 while the actual
            # package/service/command still isn't usable (or, the other
            # way round, still be fine after a command that "failed").
            if self._verify_callback is not None:
                success = bool(self._verify_callback())
            else:
                success = bool(result)
            if success:
                self._msg.set_label("✅ Installato — riavvia l'app per attivare")
                self._btn.set_visible(False)
                self._hide_details()
                if self._control is not None:
                    self._control.set_sensitive(True)
            else:
                self._msg.set_label(f"❌ {T('install_generic_error')}")
                self._btn.set_label(T("install_retry_btn"))
                self._btn.set_sensitive(True)
                detail = getattr(result, "technical_detail", None)
                if callable(detail):
                    self._details_btn.set_visible(True)
                    self._details_lbl.set_text(detail())
        finally:
            self._clear_slow_timer()
        return False  # GLib.idle_add single shot

    def _on_toggle_details(self, _btn):
        self._details_lbl.set_visible(not self._details_lbl.get_visible())

    def _hide_details(self):
        self._details_btn.set_visible(False)
        self._details_lbl.set_visible(False)


def _repo_has_package(pkg_map: dict) -> bool:
    from core.repo_check import is_available
    try:
        return is_available(pkg_map)
    except Exception:
        logger.exception("Repo-availability check failed, assuming available")
        return True  # never block a legitimate install because our own check broke


def _not_available_notice() -> Gtk.Widget:
    lbl = Gtk.Label(label=T("install_not_available_repo"), wrap=True, xalign=0)
    lbl.add_css_class("desc-con")
    lbl.set_margin_top(6)
    return lbl


class _FailedResult:
    """Wraps an unexpected exception so _done() can treat it exactly like
    a failed CommandResult — falsy, with a technical_detail() for "Mostra
    dettagli" instead of silently losing the traceback."""
    def __init__(self, error: str):
        self._error = error

    def __bool__(self):
        return False

    def technical_detail(self) -> str:
        return f"Unexpected error: {self._error}"


# ── Base FeatureRow ──────────────────────────────────────────────
class FeatureRow(Adw.ExpanderRow):
    """
    An Adw.ExpanderRow that:
    - auto-translates title, desc, pro, con via i18n
    - shows a coloured risk badge chip
    - optionally shows a reboot warning chip
    - optionally shows a DepBanner if a dep_check callable returns False
    """

    def __init__(self,
                 key_prefix:   str,
                 control:      "Gtk.Widget | None",
                 risk:         str  = "low",
                 reboot:       bool = False,
                 dep_pkg:      str  | None = None,
                 dep_check:    callable   = None,
                 dep_install:  callable   = None,
                 dep_pkg_map:  dict  | None = None):
        """
        dep_pkg_map, if given (same shape as distro.install_cmd()'s
        argument, e.g. {"debian": "pkg", "arch": "pkg", ...}), is used to
        check real repository availability before ever showing an
        Install button — if the package isn't there, we say so plainly
        instead of letting an install attempt fail with a raw
        package-manager error. Optional and separate from dep_pkg (the
        human-readable label) because not every dep_install callable
        maps to a single simple per-distro package name.
        """
        super().__init__()
        self.set_enable_expansion(True)
        self._key   = key_prefix
        self._risk  = risk
        self._reboot = reboot

        # ── Control widget ───────────────────────────────────────
        if control is not None:
            self.add_suffix(control)

        # ── Expanded body ────────────────────────────────────────
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(10)
        body.set_margin_bottom(14)
        body.set_margin_start(14)
        body.set_margin_end(14)

        # Badge row
        badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._risk_badge   = Gtk.Label(use_markup=False)
        self._risk_badge.add_css_class(RISK_CSS.get(risk, "badge-low"))
        badge_box.append(self._risk_badge)

        self._reboot_lbl = None
        if reboot:
            self._reboot_lbl = Gtk.Label(label=T("requires_reboot"))
            self._reboot_lbl.add_css_class("badge-reboot")
            badge_box.append(self._reboot_lbl)
        body.append(badge_box)

        # Description labels
        self._lbl_what = Gtk.Label(wrap=True, xalign=0, use_markup=False)
        self._lbl_pro  = Gtk.Label(wrap=True, xalign=0, use_markup=False)
        self._lbl_con  = Gtk.Label(wrap=True, xalign=0, use_markup=False)
        self._lbl_what.add_css_class("desc-what")
        self._lbl_pro.add_css_class("desc-pro")
        self._lbl_con.add_css_class("desc-con")
        body.append(self._lbl_what)
        body.append(self._lbl_pro)
        body.append(self._lbl_con)

        # Dep banner (shown if tool missing)
        if dep_check is not None and dep_pkg is not None:
            if not dep_check():
                if dep_pkg_map is not None and not _repo_has_package(dep_pkg_map):
                    body.append(_not_available_notice())
                else:
                    self._banner = DepBanner(dep_pkg, dep_install or (lambda: False),
                                             verify_callback=dep_check, control=control)
                    body.append(self._banner)
                # Disable control if dep missing
                if control is not None:
                    control.set_sensitive(False)

        # ── Operation error (hidden until a privileged action fails) ──
        # Same shape as KernelFeatureRow.show_error()/clear_error(): one
        # short friendly sentence, raw technical_detail only ever behind
        # "Mostra dettagli". Every FeatureRow subclass (SwitchRow,
        # InstallRow — wifi/bluetooth/ipv6/firewall/ssh/samba/cups/trim/
        # smart/KVM/Docker/Podman/Distrobox/printer drivers/GameMode/
        # MangoHud/Vulkan/lib32/EasyEffects) gets this for free instead
        # of each page silently reverting the control with no feedback.
        self._lbl_op_error = Gtk.Label(wrap=True, xalign=0, use_markup=False)
        self._lbl_op_error.add_css_class("desc-con")
        self._lbl_op_error.set_visible(False)
        body.append(self._lbl_op_error)

        self._op_details_btn = Gtk.Button(label=T("kf_show_details_btn"))
        self._op_details_btn.add_css_class("flat")
        self._op_details_btn.set_halign(Gtk.Align.START)
        self._op_details_btn.set_visible(False)
        self._op_details_btn.connect("clicked", self._on_toggle_op_details)
        body.append(self._op_details_btn)

        self._lbl_op_details = Gtk.Label(wrap=True, xalign=0, selectable=True, use_markup=False)
        self._lbl_op_details.add_css_class("sysinfo-value-sub")
        self._lbl_op_details.set_visible(False)
        body.append(self._lbl_op_details)

        self.add_row(body)

        on_change(self._refresh)
        self._refresh()

    def _refresh(self):
        self.set_title(T(f"{self._key}_title"))
        self._risk_badge.set_text(T(f"risk_{self._risk}"))
        if self._reboot_lbl is not None:
            self._reboot_lbl.set_text(T("requires_reboot"))
        self._lbl_what.set_text(f"❓ {T('what_is')}: {T(f'{self._key}_desc')}")
        self._lbl_pro.set_text(f"✅ {T('advantage')}: {T(f'{self._key}_pro')}")
        self._lbl_con.set_text(f"⚠️  {T('when_avoid')}: {T(f'{self._key}_con')}")
        self._op_details_btn.set_label(T("kf_show_details_btn"))

    def show_operation_error(self, friendly_key: str = "kf_err_generic", technical_detail: str = ""):
        self._lbl_op_error.set_text(T(friendly_key) if friendly_key else T("kf_err_generic"))
        self._lbl_op_error.set_visible(True)
        self._op_details_btn.set_visible(bool(technical_detail))
        self._lbl_op_details.set_text(technical_detail)
        self._lbl_op_details.set_visible(False)
        self.set_expanded(True)

    def clear_operation_error(self):
        self._lbl_op_error.set_visible(False)
        self._op_details_btn.set_visible(False)
        self._lbl_op_details.set_visible(False)

    def _on_toggle_op_details(self, _btn):
        self._lbl_op_details.set_visible(not self._lbl_op_details.get_visible())


# ── SwitchRow ────────────────────────────────────────────────────
class SwitchRow(FeatureRow):
    """FeatureRow with an embedded Gtk.Switch. Read .switch to connect signals."""
    def __init__(self, key_prefix, initial=False, risk="low", reboot=False,
                 dep_pkg=None, dep_check=None, dep_install=None):
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=initial)
        super().__init__(key_prefix, self.switch, risk=risk, reboot=reboot,
                         dep_pkg=dep_pkg, dep_check=dep_check, dep_install=dep_install)


# ── InstallRow ───────────────────────────────────────────────────
class InstallRow(FeatureRow):
    """FeatureRow with an Install button, or an "Installato ✓" pill once
    installed. The pill is a plain Label, not a disabled button — a
    disabled Gtk.Button gets desaturated by GTK's own insensitive-state
    rendering no matter what colors CSS asks for, which is exactly why
    the old "Installato" badge always looked washed out. self.button
    keeps working as before for every existing caller (still the same
    widget, same .connect("clicked", ...) target) — it's simply hidden
    once installed instead of disabled-and-recolored."""
    def __init__(self, key_prefix, installed=False, risk="low", reboot=False,
                 dep_pkg=None, dep_check=None, dep_install=None, dep_pkg_map=None,
                 available=True):
        """
        available=False means "genuinely cannot be installed on this
        system" (e.g. not in any configured repository) — NOT "not
        installed yet", which is the normal, actionable state this row
        exists for. Conflating the two used to disable the Install
        button itself (via dep_check pointing at the same package),
        making it look broken right when it should be most clickable.
        When unavailable, the button is replaced by a StatusPill
        instead of being shown greyed-out.
        """
        self._is_installed = installed
        self._is_available = available
        self.button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.button.add_css_class("lt-action-btn")

        self._installed_pill = Gtk.Label(valign=Gtk.Align.CENTER)
        self._installed_pill.add_css_class("ds-pill")
        self._installed_pill.add_css_class("ds-pill-success")

        self._unavailable_pill = Gtk.Label(valign=Gtk.Align.CENTER)
        self._unavailable_pill.add_css_class("ds-pill")
        self._unavailable_pill.add_css_class("ds-pill-absent")

        control = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        control.append(self.button)
        control.append(self._installed_pill)
        control.append(self._unavailable_pill)

        self._update_btn()
        super().__init__(key_prefix, control, risk=risk, reboot=reboot,
                         dep_pkg=dep_pkg, dep_check=dep_check, dep_install=dep_install,
                         dep_pkg_map=dep_pkg_map)
        on_change(self._update_btn)

    def _update_btn(self):
        self._installed_pill.set_label(T("installed_badge"))
        self._unavailable_pill.set_label(T("not_available_badge"))
        self.button.set_visible(self._is_available and not self._is_installed)
        self.button.set_label(T("install_btn"))
        self._installed_pill.set_visible(self._is_installed)
        self._unavailable_pill.set_visible(not self._is_available and not self._is_installed)

    def mark_installed(self):
        self._is_installed = True
        self._update_btn()


# ── InfoRow ──────────────────────────────────────────────────────
class InfoRow(FeatureRow):
    """FeatureRow with a status label (read-only)."""
    def __init__(self, key_prefix, status_text, status_ok=True, risk="low"):
        lbl = Gtk.Label(label=status_text, valign=Gtk.Align.CENTER)
        lbl.add_css_class("status-active" if status_ok else "status-inactive")
        super().__init__(key_prefix, lbl, risk=risk)


# ── make_group ───────────────────────────────────────────────────
def make_group(title_key: str) -> Adw.PreferencesGroup:
    grp = Adw.PreferencesGroup()
    grp.set_title(T(title_key))
    on_change(lambda: grp.set_title(T(title_key)))
    return grp
