import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import FeatureRow, make_group
from core import power_providers
import backend.all as B
import threading

from core.kernel_features.base import SupportStatus
from core.kernel_features.registry import register
from core.kernel_features.battery import (
    BatteryStatusFeature, BatteryThresholdFeature, PlatformProfileFeature, SuspendModeFeature,
)
from core.kernel_features.device_power import list_wakeup_capable_devices, list_pm_controllable_devices
from ui.kernel.feature_row import KernelFeatureRow, handle_restore_click
from ui.pages.page_kernel import ChoiceKernelFeatureRow, _widen_preferences_clamp

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge
from ui.design_system.action_bar import style_kernel_feature_row_buttons
from ui.design_system.section_card import make_section
from ui.design_system.segmented_control import SegmentedControl
from ui.design_system.select_control import SelectControl
from ui.design_system.value_translation import translated_value

_perf_ds_strings = {
    "ds_perf_header_desc": {
        "en": "Check power usage, suspend behaviour and device power saving.",
        "it": "Controlla consumi, sospensione e risparmio dei dispositivi.",
        "es": "Controla el consumo, la suspensión y el ahorro de energía de los dispositivos.",
        "fr": "Vérifiez la consommation, la suspension et l'économie d'énergie des périphériques.",
    },
    "ds_perf_group_profile":  {"en": "Power profile", "it": "Profilo energetico", "es": "Perfil de energía", "fr": "Profil énergétique"},
    "ds_perf_group_battery":  {"en": "Battery", "it": "Batteria", "es": "Batería", "fr": "Batterie"},
    "ds_perf_group_suspend":  {"en": "Suspend", "it": "Sospensione", "es": "Suspensión", "fr": "Suspension"},
    "ds_perf_group_devices":  {"en": "Device management", "it": "Gestione dispositivi", "es": "Gestión de dispositivos", "fr": "Gestion des périphériques"},
    "ds_perf_no_battery_title": {
        "en": "No battery detected", "it": "Nessuna batteria rilevata",
        "es": "No se detectó batería", "fr": "Aucune batterie détectée",
    },
    "ds_perf_no_battery_body": {
        "en": "This computer does not have a detectable battery.",
        "it": "Questo computer non dispone di una batteria rilevabile.",
        "es": "Este equipo no tiene una batería detectable.",
        "fr": "Cet ordinateur n'a pas de batterie détectable.",
    },
}
for _k, _v in _perf_ds_strings.items():
    _i18n_mod._strings[_k] = _v


def _device_power_apply(bus: str, device_id: str, setting: str, on_done):
    """Runs a device_power writer action in a background thread — used by
    the per-device switches below, never on the GTK main thread."""
    from core.persistence.priv_client import default_privileged_writer
    writer = default_privileged_writer()

    def run():
        result = writer.execute("device_power", "apply_temporary", f"{bus}:{device_id}:{setting}")
        GLib.idle_add(on_done, result)

    threading.Thread(target=run, daemon=True).start()


class BatteryStatusRow(KernelFeatureRow):
    """Pure read-only battery snapshot — no Prova/Ripristina, nothing to
    change here. Shows the plain 'no battery' message on desktops
    instead of hiding the row (matches the spec's exact wording)."""
    _STATUS_KEYS = {
        "Charging": "battery_status_charging", "Discharging": "battery_status_discharging",
        "Full": "battery_status_full", "Not charging": "battery_status_not_charging",
    }

    def __init__(self):
        feature = register(BatteryStatusFeature())
        super().__init__(feature, "battery_status")
        self.btn_try.set_visible(False)
        self.btn_permanent.set_visible(False)
        self.btn_restore.set_visible(False)
        self._lines_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.choices_box.append(self._lines_box)
        self._refresh_once()

    def _clear_lines(self):
        child = self._lines_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._lines_box.remove(child)
            child = nxt

    def _add_line(self, label_key, text):
        lbl = Gtk.Label(label=f"{T(label_key)}: {text}", xalign=0, wrap=True)
        lbl.add_css_class("sysinfo-value")
        self._lines_box.append(lbl)

    def _refresh_once(self):
        status = self.feature.probe()
        self.set_support_status(status)
        self.choices_box.set_visible(True)
        self._clear_lines()

        if status == SupportStatus.UNSUPPORTED_HARDWARE:
            lbl = Gtk.Label(label=T("battery_not_present"), xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            self._lines_box.append(lbl)
            self.set_status_line("—")
            self.set_no_action_needed(False)
            return

        current = self.feature.read_current()
        if not current.ok:
            self.show_error(current.friendly_message, current.technical_detail)
            return
        self.clear_error()
        d = current.value

        if "percent" in d:
            self._add_line("battery_percent", f"{d['percent']}%")
        if "status" in d:
            self._add_line("battery_status_state", T(self._STATUS_KEYS.get(d["status"], "battery_status_unknown")))
        if "health_percent" in d:
            self._add_line("battery_health", f"{d['health_percent']}%")
        if "cycle_count" in d:
            self._add_line("battery_cycles", str(d["cycle_count"]))
        if "temperature_c" in d:
            self._add_line("battery_temperature", f"{d['temperature_c']} °C")
        if "estimated_hours_remaining" in d:
            self._add_line("battery_time_remaining", f"~{d['estimated_hours_remaining']} h")

        self.set_status_line(f"{d.get('percent', '—')}%")


class BatteryThresholdRow(KernelFeatureRow):
    PRESET_FULL = (0, 100)
    PRESET_PROTECT = (50, 80)

    def __init__(self):
        feature = register(BatteryThresholdFeature())
        super().__init__(feature, "battery_protection")
        self.btn_permanent.set_visible(False)
        self._current_value = None
        self._selected_value = None

        self._choice_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.choices_box.append(self._choice_box)

        self._custom_expander = Gtk.Expander(label=T("battery_protection_custom"))
        custom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._start_spin = Gtk.SpinButton.new_with_range(0, 99, 5)
        self._end_spin = Gtk.SpinButton.new_with_range(1, 100, 5)
        custom_box.append(Gtk.Label(label=T("battery_protection_start_label")))
        custom_box.append(self._start_spin)
        custom_box.append(Gtk.Label(label=T("battery_protection_end_label")))
        custom_box.append(self._end_spin)
        self._custom_expander.set_child(custom_box)
        self.choices_box.append(self._custom_expander)
        self._start_spin.connect("value-changed", self._on_custom_changed)
        self._end_spin.connect("value-changed", self._on_custom_changed)

        self.btn_try.connect("clicked", self._on_try_clicked)
        self.btn_restore.connect("clicked", self._on_restore)
        self._refresh_once()

    def _on_custom_changed(self, *_a):
        start, end = int(self._start_spin.get_value()), int(self._end_spin.get_value())
        self._selected_value = {"start": start, "end": end}
        self.set_try_sensitive(self._selected_value != self._current_value and start < end, "kf_try_reason_same_value")

    def _select_preset(self, value):
        self._selected_value = {"start": value[0], "end": value[1]}
        self.set_try_sensitive(self._selected_value != self._current_value, "kf_try_reason_same_value")

    def _on_try_clicked(self, _btn):
        self.clear_error()
        result = self.feature.apply_temporary(self._selected_value)
        if not result.ok:
            self.show_error(result.friendly_message, result.technical_detail)
            return
        self._refresh_once()

    def _on_restore(self, _btn):
        handle_restore_click(self, self.feature.restore, self._refresh_once)

    def _refresh_once(self):
        status = self.feature.probe()
        self.set_support_status(status)
        self.choices_box.set_visible(True)

        current = self.feature.read_current()
        if not current.ok:
            self.show_error(current.friendly_message, current.technical_detail)
            return
        self.clear_error()

        self._current_value = current.value
        self._selected_value = current.value
        self.set_status_line(f"{current.value['start']}% – {current.value['end']}%")
        self._start_spin.set_value(current.value["start"])
        self._end_spin.set_value(current.value["end"])

        child = self._choice_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._choice_box.remove(child)
            child = nxt
        full_btn = Gtk.ToggleButton(label=T("battery_protection_full_charge"))
        protect_btn = Gtk.ToggleButton(label=T("battery_protection_preset"))
        protect_btn.set_group(full_btn)
        full_btn.connect("clicked", lambda _b: self._select_preset(self.PRESET_FULL))
        protect_btn.connect("clicked", lambda _b: self._select_preset(self.PRESET_PROTECT))
        self._choice_box.append(full_btn)
        self._choice_box.append(protect_btn)

        self.set_try_sensitive(False, "kf_try_reason_pick_different")
        rec = self.feature.get_record()
        if rec is not None:
            self.set_initial_value(f"{rec.initial_value['start']}% – {rec.initial_value['end']}%")
            self.set_restore_enabled(True)
        else:
            self.set_initial_value("")
            self.set_restore_enabled(False)
        self.set_no_action_needed(rec is None)


class PlatformProfileRow(ChoiceKernelFeatureRow):
    """Read-only if the firmware only exposes one profile — same
    reasoning as EPP."""
    def __init__(self):
        self._single_value_note = Gtk.Label(wrap=True, xalign=0)
        self._single_value_note.add_css_class("desc-what")
        self._single_value_note.set_visible(False)
        feature = register(PlatformProfileFeature())
        super().__init__(feature, "battery_platform_profile")
        self.btn_permanent.set_visible(False)
        self.choices_box.append(self._single_value_note)

    def _refresh_once(self):
        super()._refresh_once()
        available = self.feature.read_available() or []
        single = len(available) <= 1
        self._single_value_note.set_text(T("platform_profile_single_value_note"))
        self._single_value_note.set_visible(single)
        if single:
            self._available_label.set_visible(False)
            self._choice_list.set_visible(False)
            self.btn_try.set_visible(False)
            self.btn_restore.set_visible(False)


class SuspendModeRow(ChoiceKernelFeatureRow):
    def __init__(self):
        feature = register(SuspendModeFeature())
        super().__init__(feature, "battery_suspend_mode")
        self.btn_permanent.set_visible(False)


_WAKEUP_CATEGORY_KEYS = {
    "keyboard": "wakeup_category_keyboard", "mouse": "wakeup_category_mouse",
    "network": "wakeup_category_network", "bluetooth": "wakeup_category_bluetooth",
    "gamepad": "wakeup_category_gamepad", "lid": "wakeup_category_lid",
}


class WakeupDevicesRow(FeatureRow):
    """
    A live list of wake-capable devices, each with its own switch —
    flipping it back is the restore, so this doesn't need a separate
    global Prova/Ripristina pair (which would be awkward for a list that
    can hold a dozen independent devices).
    """
    def __init__(self):
        # Must exist BEFORE super().__init__(): FeatureRow's own __init__
        # already calls self._refresh() once (its OWN _refresh, for i18n
        # labels) — naming this method the same would silently override
        # that and crash on a not-yet-created widget, so this uses a
        # distinct name (_refresh_list) instead.
        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._list_box.set_margin_top(6)
        super().__init__("wakeup_devices", None, risk="low")
        self.add_row(self._list_box)
        self._refresh_list()

    def _refresh_list(self):
        child = self._list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._list_box.remove(child)
            child = nxt

        devices = list_wakeup_capable_devices()
        if not devices:
            lbl = Gtk.Label(label=T("wakeup_no_devices"), xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            self._list_box.append(lbl)
            return

        for dev in devices:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            cat_label = T(_WAKEUP_CATEGORY_KEYS.get(dev["category"], dev["category"]))
            lbl = Gtk.Label(label=f"{cat_label} — {dev['label']}", xalign=0, hexpand=True, wrap=True)
            lbl.add_css_class("sysinfo-value")
            switch = Gtk.Switch(active=dev["enabled"], valign=Gtk.Align.CENTER)
            switch.connect("notify::active", self._on_toggle, dev["bus"], dev["device_id"])
            row.append(lbl)
            row.append(switch)
            self._list_box.append(row)

    def _on_toggle(self, switch, _pspec, bus, device_id):
        switch.set_sensitive(False)
        want = switch.get_active()
        setting = "enabled" if want else "disabled"

        def done(result):
            switch.set_sensitive(True)
            if not result.ok:
                switch.handler_block_by_func(self._on_toggle)
                switch.set_active(not want)
                switch.handler_unblock_by_func(self._on_toggle)
            return False

        _device_power_apply(bus, device_id, setting, done)


_PERIPHERAL_CATEGORY_KEYS = {
    "webcam": "peripheral_category_webcam", "bluetooth": "peripheral_category_bluetooth",
    "card_reader": "peripheral_category_card_reader", "audio": "peripheral_category_audio",
    "gamepad": "peripheral_category_gamepad", "usb_generic": "peripheral_category_usb_generic",
}


class PeripheralPowerRow(FeatureRow):
    """Same live-switch-per-device pattern as WakeupDevicesRow, for USB
    runtime autosuspend — never offered for storage, keyboard, mouse or
    USB host controllers (excluded at the detection layer already)."""
    def __init__(self):
        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._list_box.set_margin_top(6)
        super().__init__("peripheral_power", None, risk="low")
        self.add_row(self._list_box)
        self._refresh_list()

    def _refresh_list(self):
        child = self._list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._list_box.remove(child)
            child = nxt

        devices = list_pm_controllable_devices()
        if not devices:
            lbl = Gtk.Label(label=T("peripheral_no_devices"), xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            self._list_box.append(lbl)
            return

        for dev in devices:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            cat_label = T(_PERIPHERAL_CATEGORY_KEYS.get(dev["category"], dev["category"]))
            lbl = Gtk.Label(label=f"{cat_label} — {dev['label']}", xalign=0, hexpand=True, wrap=True)
            lbl.add_css_class("sysinfo-value")
            switch = Gtk.Switch(active=dev["auto"], valign=Gtk.Align.CENTER)
            switch.connect("notify::active", self._on_toggle, dev["bus"], dev["device_id"])
            row.append(lbl)
            row.append(switch)
            self._list_box.append(row)

    def _on_toggle(self, switch, _pspec, bus, device_id):
        switch.set_sensitive(False)
        want_auto = switch.get_active()
        setting = "auto" if want_auto else "on"

        def done(result):
            switch.set_sensitive(True)
            if not result.ok:
                switch.handler_block_by_func(self._on_toggle)
                switch.set_active(not want_auto)
                switch.handler_unblock_by_func(self._on_toggle)
            return False

        _device_power_apply(bus, device_id, setting, done)


class PerformancePage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("battery-good-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()
        _widen_preferences_clamp(self, maximum_size=900, tightening_threshold=700)

        header = PageHeader(
            "battery-good-symbolic", T("tab_performance"), T("ds_perf_header_desc"),
            category="energy",
        )
        self.add(wrap_in_preferences_group(header))

        # ZRAM, Turbo Boost, Transparent Huge Pages and CPU Governor now
        # live only in "Funzioni Kernel" (ui/pages/page_kernel.py), as
        # full KernelFeature rows with Prova/Ripristina/registro — they
        # used to be duplicated here as plain switches/dropdowns with no
        # rollback and no real verification.

        # ── Power profile ──────────────────────────────────────────
        # Several daemons can own "power profiles" (system76-power,
        # power-profiles-daemon, tuned-ppd, TLP) and they actively conflict
        # with each other (power-profiles-daemon.service itself declares
        # Conflicts=...system76-power.service). So we detect what's really
        # in charge first, and only ever offer to install
        # power-profiles-daemon if nothing compatible is already present —
        # never propose a second, conflicting provider on top of one that
        # already works (this is exactly what used to happen on Pop!_OS,
        # which ships system76-power by default).
        g2 = make_section("ds_perf_group_profile")
        self.add(g2)

        pp_resolution = power_providers.resolve()
        active_provider = pp_resolution["active"]

        pp_kwargs = {}
        if pp_resolution["should_offer_install"]:
            pp_pkg_map = {"debian": "power-profiles-daemon", "arch": "power-profiles-daemon", "fedora": "power-profiles-daemon"}
            pp_kwargs = dict(
                dep_pkg="power-profiles-daemon",
                dep_check=lambda: B._cmd_exists("powerprofilesctl"),
                dep_install=lambda job=None: B._install_pkg(pp_pkg_map, job=job),
                dep_pkg_map=pp_pkg_map,
            )
        self.pp_row = FeatureRow("pprofile", None, risk="low", **pp_kwargs)

        if active_provider == "power-profiles-daemon":
            # v4 fix: this used to be a raw Gtk.DropDown showing the
            # literal technical strings "power-saver"/"balanced"/
            # "performance" in English. Exactly 3 fixed values -> a
            # SegmentedControl, translated for display only; the
            # values read/written to the backend (B.set_power_profile)
            # are the exact same technical strings as before.
            options = [(v, translated_value(v)) for v in ("power-saver", "balanced", "performance")]
            self.pp_segmented = SegmentedControl(options, selected_value=B.get_power_profile())
            self.pp_segmented.connect_changed(self._on_pprofile_segmented)
            self.pp_row.add_suffix(self.pp_segmented)

        elif active_provider == "system76-power":
            # Dynamic/distro-specific value set -> the standardized
            # SelectControl instead (not a fixed 3-way choice).
            profiles = B.SYSTEM76_POWER_PROFILES
            labels = [translated_value(p) for p in profiles]
            current = B.get_system76_power_profile()
            selected_idx = profiles.index(current) if current in profiles else 1
            self.pp_select = SelectControl(labels, selected=selected_idx)
            self._system76_profiles = profiles
            self.pp_select.dropdown.connect("notify::selected", self._on_pprofile_system76)
            self.pp_row.add_suffix(self.pp_select)
            self.pp_row.add_row(self._make_provider_note(active_provider))

        elif active_provider is not None:
            # tuned-ppd / TLP: no safe generic control surface here, so we
            # only show what's already in charge instead of guessing at
            # its (very different) configuration model.
            status_lbl = Gtk.Label(
                label=T("pprofile_readonly_status").format(profile=power_providers.provider_label(active_provider)),
                valign=Gtk.Align.CENTER)
            self.pp_row.add_suffix(status_lbl)
            self.pp_row.add_row(self._make_provider_note(active_provider))

        elif pp_resolution["installed_inactive"]:
            # Present but not currently running — still don't offer a
            # second install on top of it (Fase B: never remove/duplicate
            # automatically on a conflict), just make the situation clear.
            present = pp_resolution["installed_inactive"][0]
            self.pp_row.add_row(self._make_provider_note(present, inactive=True))

        self.pp_row.add_prefix(IconBadge("battery-good-symbolic", category="energy"))
        style_kernel_feature_row_buttons(self.pp_row)
        g2.add(self.pp_row)

        # ── Battery ─────────────────────────────────────────────────
        # Real implementation now (previously just a placeholder note).
        # v3: a desktop with no detectable battery gets one elegant
        # dedicated card instead of BatteryStatusRow showing a bare "—"
        # when collapsed — BatteryStatusRow/BatteryStatusFeature
        # themselves are untouched, this only decides which of the two
        # to add, using the same probe() the rest of this file already
        # calls for every other conditional row below.
        g3 = make_section("ds_perf_group_battery")
        self.add(g3)
        if BatteryStatusFeature().probe() == SupportStatus.UNSUPPORTED_HARDWARE:
            g3.add(self._make_no_battery_card())
        else:
            battery_row = BatteryStatusRow()
            battery_row.add_prefix(IconBadge("battery-good-symbolic", category="energy"))
            style_kernel_feature_row_buttons(battery_row)
            g3.add(battery_row)

        if BatteryThresholdFeature().probe() in (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_READ_ONLY):
            threshold_row = BatteryThresholdRow()
            threshold_row.add_prefix(IconBadge("battery-good-symbolic", category="energy"))
            style_kernel_feature_row_buttons(threshold_row)
            g3.add(threshold_row)

        if PlatformProfileFeature().probe() in (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_READ_ONLY):
            profile_row = PlatformProfileRow()
            profile_row.add_prefix(IconBadge("battery-good-symbolic", category="energy"))
            style_kernel_feature_row_buttons(profile_row)
            g3.add(profile_row)

        # ── Suspend ─────────────────────────────────────────────────
        if SuspendModeFeature().probe() in (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_READ_ONLY):
            g_suspend = make_section("ds_perf_group_suspend")
            self.add(g_suspend)
            suspend_row = SuspendModeRow()
            suspend_row.add_prefix(IconBadge("weather-clear-night-symbolic", category="energy"))
            style_kernel_feature_row_buttons(suspend_row)
            g_suspend.add(suspend_row)

        # ── Device management ───────────────────────────────────────
        g4 = make_section("ds_perf_group_devices")
        self.add(g4)
        wakeup_row = WakeupDevicesRow()
        wakeup_row.add_prefix(IconBadge("battery-good-symbolic", category="energy"))
        g4.add(wakeup_row)
        peripheral_row = PeripheralPowerRow()
        peripheral_row.add_prefix(IconBadge("battery-good-symbolic", category="energy"))
        g4.add(peripheral_row)

    def _make_no_battery_card(self) -> Gtk.Widget:
        """Elegant, deliberately-informative card for desktops — never
        just a lone dash, per the v3 review."""
        row = Adw.ActionRow()
        row.set_activatable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.append(IconBadge("battery-missing-symbolic", category="energy", size="lg"))
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_valign(Gtk.Align.CENTER)
        title = Gtk.Label(label=T("ds_perf_no_battery_title"), xalign=0)
        title.add_css_class("ds-page-header-title")
        body = Gtk.Label(label=T("ds_perf_no_battery_body"), xalign=0, wrap=True)
        body.add_css_class("ds-page-header-desc")
        text_box.append(title)
        text_box.append(body)
        box.append(text_box)
        row.set_child(box)
        return row

    def _make_provider_note(self, provider_id: str, inactive: bool = False) -> Gtk.Widget:
        label = power_providers.provider_label(provider_id)
        note = Gtk.Label(
            label=T("pprofile_already_managed").format(provider=label),
            wrap=True, xalign=0)
        note.add_css_class("desc-pro")
        note.set_margin_top(6)
        note.set_margin_bottom(6)
        note.set_margin_start(14)
        note.set_margin_end(14)
        return note

    def _refresh_title(self):
        self.set_title(T("tab_performance"))

    def _on_pprofile_segmented(self, value):
        B.set_power_profile(value)

    def _on_pprofile_system76(self, dd, _):
        B.set_system76_power_profile(self._system76_profiles[dd.get_selected()])
