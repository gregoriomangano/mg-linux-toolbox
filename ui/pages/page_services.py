"""
Services tab — a short, curated list of common systemd services (not a
dump of everything systemd knows about), each with Start/Stop and an
"at boot" enable/disable switch. Unit names are resolved per-distro via
backend.all.service_unit_name(), so this works identically on
Debian/Ubuntu, Arch, Fedora and openSUSE.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import make_group
import backend.all as B

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge
from ui.design_system.status_pill import StatusPill

_services_ds_strings = {
    "ds_services_header_desc": {
        "en": "Start, stop and enable common system services at boot.",
        "it": "Avvia, arresta e abilita all'avvio i servizi di sistema più comuni.",
        "es": "Inicia, detén y habilita al arranque los servicios del sistema más comunes.",
        "fr": "Démarrez, arrêtez et activez au démarrage les services système courants.",
    },
}
for _k, _v in _services_ds_strings.items():
    _i18n_mod._strings[_k] = _v


class ServiceRow(Gtk.Box):
    """
    One card per service, always ONE visual block (never two separate
    list rows with a divider between name and actions):

      [IconBadge] Name                              [StatusPill]
                  Plain-language description
      [Start/Stop]  [At-boot switch]

    The actions live in a Gtk.FlowBox instead of a fixed horizontal
    Gtk.Box: on a wide window both actions sit on one line to the right
    of nothing in particular (full row width), on a narrow window the
    FlowBox wraps the second action below the first automatically —
    without ever shrinking the name/description above, which stay in
    their own always-full-width header row.
    """
    def __init__(self, key: str, unit_names: dict):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("lt-service-row")
        self._key = key
        self._unit = B.service_unit_name(unit_names)

        self._exists = bool(self._unit) and B._service_exists(self._unit)
        enabled_now = B._service_enabled(self._unit) if self._exists else False

        # ── Header: icon + name/description (always full row width) + pill ──
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.append(IconBadge("system-run-symbolic", category="neutral"))

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text_box.set_valign(Gtk.Align.CENTER)
        self._title_lbl = Gtk.Label(xalign=0, wrap=True)
        self._title_lbl.add_css_class("lt-service-title")
        self._desc_lbl = Gtk.Label(xalign=0, wrap=True)
        self._desc_lbl.add_css_class("sysinfo-value-sub")
        text_box.append(self._title_lbl)
        text_box.append(self._desc_lbl)
        header.append(text_box)

        self._status_lbl = StatusPill("", variant="neutral")
        self._status_lbl.set_valign(Gtk.Align.START)
        header.append(self._status_lbl)
        self.append(header)

        # ── Actions: reflow via FlowBox, never crammed against the header ──
        actions = Gtk.FlowBox()
        actions.set_selection_mode(Gtk.SelectionMode.NONE)
        actions.set_min_children_per_line(1)
        actions.set_max_children_per_line(2)
        actions.set_column_spacing(10)
        actions.set_row_spacing(6)
        actions.set_homogeneous(False)

        self._start_btn = Gtk.Button(valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
        self._start_btn.add_css_class("ds-btn-secondary")
        self._start_btn.connect("clicked", self._on_start_stop)
        actions.insert(self._start_btn, -1)

        self._boot_box = boot_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
        self._boot_lbl = Gtk.Label(label=T("svc_enable_lbl"))
        self._boot_lbl.add_css_class("lt-service-switch-label")
        # IMPORTANT: set the initial state via the constructor kwarg, and
        # connect the "toggled" handler only afterwards. Calling
        # set_active() *after* connecting would fire the handler with the
        # value we just read from the system, making the row silently
        # re-apply (and request a pkexec password for) a change that was never
        # requested — that was the bug that asked for the password once
        # per existing service just from opening this tab.
        self._boot_switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=enabled_now)
        boot_box.append(self._boot_lbl)
        boot_box.append(self._boot_switch)
        actions.insert(boot_box, -1)
        self.append(actions)

        if not self._exists:
            self._start_btn.set_sensitive(False)
            self._boot_switch.set_sensitive(False)
            self._start_btn.set_tooltip_text(T("svc_disabled_tooltip"))
            self._boot_switch.set_tooltip_text(T("svc_disabled_tooltip"))
            boot_box.set_tooltip_text(T("svc_disabled_tooltip"))

        on_change(self._refresh_labels)
        self._refresh_labels()
        self._refresh_status()

        # Connected last, and only reacts to real user clicks from here on.
        self._boot_switch.connect("notify::active", self._on_boot_toggle)

    def _refresh_labels(self):
        self._title_lbl.set_text(T(f"svc_{self._key}_title"))
        self._desc_lbl.set_text(T(f"svc_{self._key}_desc") if self._exists else T("svc_not_found"))
        self._boot_lbl.set_text(T("svc_enable_lbl"))
        if self._exists:
            active = B._service_active(self._unit)
            self._start_btn.set_label(T("svc_stop_btn") if active else T("svc_start_btn"))
        else:
            self._start_btn.set_label(T("svc_start_btn"))
            tooltip = T("svc_disabled_tooltip")
            self._start_btn.set_tooltip_text(tooltip)
            self._boot_switch.set_tooltip_text(tooltip)
            self._boot_box.set_tooltip_text(tooltip)

    def _refresh_status(self):
        if self._exists:
            active = B._service_active(self._unit)
            self._status_lbl.set_text(T("svc_status_active") if active else T("svc_status_inactive"))
            self._status_lbl.set_variant("success" if active else "neutral")
        else:
            self._status_lbl.set_text(T("svc_status_not_installed"))
            self._status_lbl.set_variant("absent")

    def _on_start_stop(self, _btn):
        if B._service_active(self._unit):
            B.service_stop(self._unit)
        else:
            B.service_start(self._unit)
        self._refresh_labels()
        self._refresh_status()

    def _on_boot_toggle(self, sw, _):
        B.service_set_enabled(self._unit, sw.get_active())


class ServicesPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("system-run-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        header = PageHeader(
            "system-run-symbolic", T("tab_services"), T("ds_services_header_desc"),
            category="neutral",
        )
        self.add(wrap_in_preferences_group(header))

        g1 = make_group("tab_services")
        self.add(g1)

        for key, unit_names in B.SERVICES:
            g1.add(ServiceRow(key, unit_names))

    def _refresh_title(self):
        self.set_title(T("tab_services"))
