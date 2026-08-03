import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import InstallRow, FeatureRow, make_group, run_install_in_background, _repo_has_package, report_toggle_result
import backend.all as B
import threading

from core.kernel_features.registry import register
from core.kernel_features.audio_power import AudioPowerSaveFeature
from core.kernel_features.device_power import list_pm_controllable_devices
from ui.kernel.feature_row import KernelFeatureRow, handle_restore_click
from core import audio_devices as ad

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge
from ui.design_system.action_bar import style_kernel_feature_row_buttons
from ui.design_system.status_pill import state_pill

_audio_ds_strings = {
    "ds_state_active": {"en": "Active", "it": "Attivo", "es": "Activo", "fr": "Actif"},
    "ds_state_inactive": {"en": "Inactive", "it": "Disattivato", "es": "Inactivo", "fr": "Inactif"},
    "ds_audio_header_desc": {
        "en": "Manage the audio engine, devices and power saving.",
        "it": "Gestisci il motore audio, i dispositivi e il risparmio energetico.",
        "es": "Gestiona el motor de audio, los dispositivos y el ahorro de energía.",
        "fr": "Gérez le moteur audio, les périphériques et l'économie d'énergie.",
    },
}
for _k, _v in _audio_ds_strings.items():
    _i18n_mod._strings[_k] = _v


class AudioPowerRow(KernelFeatureRow):
    PRESET_ALWAYS = 0
    PRESET_5S = 5
    PRESET_10S = 10

    def __init__(self):
        feature = register(AudioPowerSaveFeature())
        super().__init__(feature, "audio_power")
        self.btn_permanent.set_visible(False)
        self._current_value = None
        self._selected_value = None

        self._choice_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.choices_box.append(self._choice_box)

        self._advanced = Gtk.Expander(label=T("audio_power_custom_advanced"))
        adv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        seconds_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        seconds_box.append(Gtk.Label(label=T("audio_power_seconds_label")))
        self._seconds_spin = Gtk.SpinButton.new_with_range(feature.MIN, feature.MAX, 1)
        seconds_box.append(self._seconds_spin)
        adv_box.append(seconds_box)
        self._controller_check = Gtk.CheckButton(label=T("audio_power_controller_label"))
        adv_box.append(self._controller_check)
        self._advanced.set_child(adv_box)
        self.choices_box.append(self._advanced)
        self._seconds_spin.connect("value-changed", self._on_custom_changed)
        self._controller_check.connect("toggled", self._on_custom_changed)

        self.btn_try.connect("clicked", self._on_try_clicked)
        self.btn_restore.connect("clicked", self._on_restore)
        self._refresh_once()

    def _on_custom_changed(self, *_a):
        self._selected_value = {"seconds": int(self._seconds_spin.get_value()),
                                 "controller": self._controller_check.get_active()
                                 if self.feature.has_controller_option() else None}
        self.set_try_sensitive(self._selected_value != self._current_value, "kf_try_reason_same_value")

    def _select_preset(self, seconds):
        self._selected_value = {"seconds": seconds, "controller": self._current_value.get("controller")
                                 if self._current_value else None}
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

    def _friendly(self, value):
        seconds = value["seconds"]
        if seconds == 0:
            return T("audio_power_always_on")
        if seconds == 1:
            return T("audio_power_seconds_one")
        return T("audio_power_seconds_many").format(n=seconds)

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
        self.set_status_line(self._friendly(current.value))
        self._seconds_spin.set_value(current.value["seconds"])
        if self.feature.has_controller_option() and current.value.get("controller") is not None:
            self._controller_check.set_active(current.value["controller"])
        else:
            self._controller_check.set_visible(False)

        child = self._choice_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._choice_box.remove(child)
            child = nxt
        always_btn = Gtk.ToggleButton(label=T("audio_power_preset_always"))
        s5_btn = Gtk.ToggleButton(label=T("audio_power_preset_5s"))
        s10_btn = Gtk.ToggleButton(label=T("audio_power_preset_10s"))
        s5_btn.set_group(always_btn)
        s10_btn.set_group(always_btn)
        always_btn.connect("clicked", lambda _b: self._select_preset(self.PRESET_ALWAYS))
        s5_btn.connect("clicked", lambda _b: self._select_preset(self.PRESET_5S))
        s10_btn.connect("clicked", lambda _b: self._select_preset(self.PRESET_10S))
        self._choice_box.append(always_btn)
        self._choice_box.append(s5_btn)
        self._choice_box.append(s10_btn)

        self.set_try_sensitive(False, "kf_try_reason_pick_different")
        rec = self.feature.get_record()
        if rec is not None:
            self.set_initial_value(self._friendly(rec.initial_value))
            self.set_restore_enabled(True)
        else:
            self.set_initial_value("")
            self.set_restore_enabled(False)
        self.set_no_action_needed(rec is None)


class AudioUsbPowerRow(FeatureRow):
    """Reuses the same device_power 'control' mechanism already built
    for Energia e batteria / Gaming, filtered to the 'audio' category."""
    def __init__(self):
        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._list_box.set_margin_top(6)
        super().__init__("audio_usb", None, risk="low")
        self.add_row(self._list_box)
        self._refresh_list()

    def _refresh_list(self):
        child = self._list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._list_box.remove(child)
            child = nxt

        devices = [d for d in list_pm_controllable_devices() if d["category"] == "audio"]
        if not devices:
            lbl = Gtk.Label(label=T("audio_usb_no_devices"), xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            self._list_box.append(lbl)
            return

        for dev in devices:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=dev["label"], xalign=0, hexpand=True, wrap=True)
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

        def run():
            from core.persistence.priv_client import default_privileged_writer
            result = default_privileged_writer().execute("device_power", "apply_temporary", f"{bus}:{device_id}:{setting}")
            GLib.idle_add(self._on_toggle_done, switch, result, want_auto)

        threading.Thread(target=run, daemon=True).start()

    def _on_toggle_done(self, switch, result, want_auto):
        switch.set_sensitive(True)
        if not result.ok:
            switch.handler_block_by_func(self._on_toggle)
            switch.set_active(not want_auto)
            switch.handler_unblock_by_func(self._on_toggle)
            # Compact per-device row, no expandable area for a details
            # disclosure — friendly message shown via tooltip instead of
            # being silent. History already recorded by
            # PrivilegedWriter.execute() itself.
            switch.set_tooltip_text(T(result.friendly_message or "kf_err_generic"))
        else:
            switch.set_tooltip_text("")
        return False


_AUDIO_CATEGORY_KEYS = {
    "hdmi": "audio_category_hdmi", "usb": "audio_category_usb",
    "bluetooth": "audio_category_bluetooth", "headphones": "audio_category_headphones",
    "speakers": "audio_category_speakers",
}


class AudioDevicesRow(FeatureRow):
    def __init__(self):
        self._output_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._input_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        super().__init__("audio_devices", None, risk="low")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.set_margin_top(6)
        out_label = Gtk.Label(label=T("audio_output_label"), xalign=0)
        out_label.add_css_class("sysinfo-label")
        body.append(out_label)
        body.append(self._output_box)
        in_label = Gtk.Label(label=T("audio_input_label"), xalign=0)
        in_label.add_css_class("sysinfo-label")
        body.append(in_label)
        body.append(self._input_box)
        self.add_row(body)
        self._refresh_list()

    def _label_for(self, dev):
        cat = T(_AUDIO_CATEGORY_KEYS.get(dev["category"], dev["category"]))
        return f"{cat} — {dev['description']}"

    def _build_group(self, box, devices, current_name, on_select):
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt
        if not devices:
            lbl = Gtk.Label(label=T("audio_no_pipewire"), xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            box.append(lbl)
            return
        group_leader = None
        for dev in devices:
            toggle = Gtk.ToggleButton(label=self._label_for(dev), active=(dev["name"] == current_name))
            if group_leader is not None:
                toggle.set_group(group_leader)
            toggle.connect("toggled", on_select, dev["name"])
            box.append(toggle)
            if group_leader is None:
                group_leader = toggle

    def _refresh_list(self):
        outputs = ad.list_outputs()
        inputs = ad.list_inputs()
        self._build_group(self._output_box, outputs, ad.get_default_output(), self._on_output_selected)
        self._build_group(self._input_box, inputs, ad.get_default_input(), self._on_input_selected)

    def _on_output_selected(self, toggle, name):
        if not toggle.get_active():
            return
        toggle.set_sensitive(False)
        threading.Thread(target=self._set_default, args=(ad.set_default_output, name, toggle), daemon=True).start()

    def _on_input_selected(self, toggle, name):
        if not toggle.get_active():
            return
        toggle.set_sensitive(False)
        threading.Thread(target=self._set_default, args=(ad.set_default_input, name, toggle), daemon=True).start()

    def _set_default(self, set_fn, name, toggle):
        set_fn(name)
        GLib.idle_add(self._on_set_default_done, toggle)

    def _on_set_default_done(self, toggle):
        toggle.set_sensitive(True)
        return False


class AudioRestartRow(FeatureRow):
    def __init__(self):
        super().__init__("audio_restart", None, risk="low")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.set_margin_top(6)
        self._btn = Gtk.Button(label=T("audio_restart_btn"))
        self._btn.add_css_class("lt-action-btn")
        self._btn.connect("clicked", self._on_click)
        self._result_lbl = Gtk.Label(wrap=True, xalign=0)
        self._result_lbl.set_visible(False)
        body.append(self._btn)
        body.append(self._result_lbl)
        self.add_row(body)

    def _on_click(self, _btn):
        self._btn.set_sensitive(False)
        self._result_lbl.set_visible(False)

        def run():
            services = ad.detect_audio_services()
            ok = ad.restart_audio_services() if services else False
            GLib.idle_add(self._on_done, ok, bool(services))

        threading.Thread(target=run, daemon=True).start()

    def _on_done(self, ok, had_services):
        self._btn.set_sensitive(True)
        self._result_lbl.set_visible(True)
        self._result_lbl.remove_css_class("desc-con")
        self._result_lbl.remove_css_class("status-active")
        if not had_services:
            self._result_lbl.set_text(T("audio_restart_no_services"))
            self._result_lbl.add_css_class("desc-con")
        elif ok:
            self._result_lbl.set_text(T("audio_restart_success"))
            self._result_lbl.add_css_class("status-active")
        else:
            self._result_lbl.set_text(T("audio_restart_failed"))
            self._result_lbl.add_css_class("desc-con")
        return False


class AudioPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("audio-speakers-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        header = PageHeader(
            "audio-speakers-symbolic", T("tab_audio"), T("ds_audio_header_desc"),
            category="audio",
        )
        self.add(wrap_in_preferences_group(header))

        g1 = make_group("grp_audio_engine")
        self.add(g1)

        # v4 fix: this used to hardcode the raw English "● Active" /
        # "○ Inactive" — same real B.pipewire_active() read, now a
        # translated StatusPill.
        active = B.pipewire_active()
        pw_pill = state_pill("active" if active else "inactive",
                              T("ds_state_active") if active else T("ds_state_inactive"))
        pw_row = FeatureRow("pipewire", pw_pill, risk="low")
        pw_row.add_prefix(IconBadge("audio-speakers-symbolic", category="audio"))
        g1.add(pw_row)

        devices_row = AudioDevicesRow()
        devices_row.add_prefix(IconBadge("audio-speakers-symbolic", category="audio"))
        g1.add(devices_row)
        restart_row = AudioRestartRow()
        restart_row.add_prefix(IconBadge("audio-speakers-symbolic", category="audio"))
        style_kernel_feature_row_buttons(restart_row)
        g1.add(restart_row)

        g2 = make_group("grp_audio_power")
        self.add(g2)
        power_row = AudioPowerRow()
        power_row.add_prefix(IconBadge("audio-speakers-symbolic", category="audio"))
        style_kernel_feature_row_buttons(power_row)
        g2.add(power_row)
        usb_power_row = AudioUsbPowerRow()
        usb_power_row.add_prefix(IconBadge("audio-speakers-symbolic", category="audio"))
        g2.add(usb_power_row)

        g3 = make_group("grp_audio_tools")
        self.add(g3)

        # dep_check/dep_pkg deliberately NOT used here: EasyEffects "not
        # installed yet" is this row's normal, actionable state (that's
        # what the Install button is for) — pointing dep_check at the
        # same package used to disable the button itself, making it
        # look broken right when it should be clickable. `available`
        # instead reflects a genuinely different condition: whether the
        # package exists in any configured repository at all.
        ee_available = _repo_has_package({
            "debian": "easyeffects", "arch": "easyeffects",
            "fedora": "easyeffects", "opensuse": "easyeffects",
        })
        self.ee = InstallRow("easyeffects", B.easyeffects_installed(), risk="low",
                             available=ee_available)
        self.ee.button.connect("clicked", self._on_ee)
        self.ee.add_prefix(IconBadge("audio-speakers-symbolic", category="audio"))
        g3.add(self.ee)

    def _refresh_title(self):
        self.set_title(T("tab_audio"))

    def _on_ee(self, _):
        run_install_in_background(self.ee.button, B.easyeffects_install,
                                   B.easyeffects_installed, self.ee.mark_installed,
                                   on_failure=lambda: report_toggle_result(self.ee, "audio", "audio.easyeffects_install", False))
