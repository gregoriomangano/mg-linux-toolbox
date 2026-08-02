import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import InstallRow, FeatureRow, make_group, run_install_in_background
import backend.all as B
import threading

from core import gaming_readiness as gr

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge

_gaming_ds_strings = {
    "ds_gaming_header_desc": {
        "en": "Check readiness and install common gaming tools.",
        "it": "Controlla la preparazione e installa gli strumenti gaming più comuni.",
        "es": "Comprueba la preparación e instala las herramientas de juego más comunes.",
        "fr": "Vérifiez la préparation et installez les outils de jeu courants.",
    },
}
for _k, _v in _gaming_ds_strings.items():
    _i18n_mod._strings[_k] = _v
from core import game_mode
from core.kernel_features.device_power import list_pm_controllable_devices

_READINESS_STATE_KEYS = {
    gr.READY: "gaming_state_ready",
    gr.ALMOST_READY: "gaming_state_almost_ready",
    gr.MISSING_COMPONENTS: "gaming_state_missing_components",
    gr.UNAVAILABLE: "gaming_state_unavailable",
}


class ReadinessRow(FeatureRow):
    """Real, re-checked-on-demand summary of what's actually ready for
    gaming — never trusts installed-package status alone (see
    core/gaming_readiness.py, each check runs the real tool)."""
    def __init__(self):
        self._lines_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._lines_box.set_margin_top(6)
        super().__init__("gaming_readiness", None, risk="low")
        self.add_row(self._lines_box)
        self._refresh_list()

    def _refresh_list(self):
        child = self._lines_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._lines_box.remove(child)
            child = nxt

        items, overall = gr.full_report()
        overall_lbl = Gtk.Label(label=T(_READINESS_STATE_KEYS[overall]), xalign=0)
        overall_lbl.add_css_class("sysinfo-value-large")
        self._lines_box.append(overall_lbl)

        for item in items:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            label = Gtk.Label(label=T(item.label_key), xalign=0, hexpand=True)
            label.add_css_class("sysinfo-value")
            state_lbl = Gtk.Label(label=T(_READINESS_STATE_KEYS[item.state]), xalign=1)
            state_lbl.add_css_class("sysinfo-value-sub")
            row.append(label)
            row.append(state_lbl)
            self._lines_box.append(row)


class GameModeRow(FeatureRow):
    """
    Not a KernelFeature (it stacks several of them together), but still
    follows the same "show what will really change, never promise more
    FPS" spirit. plan() and activate()/deactivate() live in
    core/game_mode.py.
    """
    def __init__(self):
        self._status_lbl = Gtk.Label(wrap=True, xalign=0)
        self._status_lbl.add_css_class("sysinfo-value")
        self._plan_lbl = Gtk.Label(wrap=True, xalign=0)
        self._plan_lbl.add_css_class("sysinfo-value-sub")
        self._toggle_btn = Gtk.Button()
        self._toggle_btn.add_css_class("lt-action-btn")
        self._toggle_btn.connect("clicked", self._on_toggle_clicked)
        self._error_lbl = Gtk.Label(wrap=True, xalign=0)
        self._error_lbl.add_css_class("desc-con")
        self._error_lbl.set_visible(False)

        super().__init__("game_mode", None, risk="medium")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.set_margin_top(6)
        body.append(self._status_lbl)
        body.append(self._plan_lbl)
        body.append(self._toggle_btn)
        body.append(self._error_lbl)
        self.add_row(body)
        self._refresh_view()

    def _refresh_view(self):
        active = game_mode.is_active()
        self._status_lbl.set_text(T("game_mode_active_status") if active else T("game_mode_inactive_status"))
        if active:
            self._toggle_btn.set_label(T("game_mode_deactivate_btn"))
            self._plan_lbl.set_visible(False)
        else:
            self._toggle_btn.set_label(T("game_mode_activate_btn"))
            changes = game_mode.plan()
            if changes:
                self._plan_lbl.set_text(T("game_mode_changes_count").format(n=len(changes)))
            else:
                self._plan_lbl.set_text(T("game_mode_no_changes"))
            self._plan_lbl.set_visible(True)

    def _on_toggle_clicked(self, _btn):
        self._toggle_btn.set_sensitive(False)
        self._error_lbl.set_visible(False)
        going_active = not game_mode.is_active()

        def run():
            if going_active:
                changes = game_mode.plan()
                ok, failed = game_mode.activate(changes) if changes else (True, None)
            else:
                ok, failed = game_mode.deactivate(), None
            GLib.idle_add(self._on_toggle_done, ok, failed)

        threading.Thread(target=run, daemon=True).start()

    def _on_toggle_done(self, ok, failed):
        self._toggle_btn.set_sensitive(True)
        if not ok:
            self._error_lbl.set_text(T("game_mode_failed"))
            self._error_lbl.set_visible(True)
        self._refresh_view()
        return False


class ControllerRow(FeatureRow):
    """Reuses the same device_power 'control' mechanism as the Energia e
    batteria peripheral-power row, filtered to gamepad-category devices
    only, with a beginner-facing 'keep active' framing."""
    def __init__(self):
        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._list_box.set_margin_top(6)
        super().__init__("controller_keep_active", None, risk="low")
        self.add_row(self._list_box)
        self._refresh_list()

    def _refresh_list(self):
        child = self._list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._list_box.remove(child)
            child = nxt

        controllers = [d for d in list_pm_controllable_devices() if d["category"] == "gamepad"]
        if not controllers:
            lbl = Gtk.Label(label=T("controller_none_found"), xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            self._list_box.append(lbl)
            return

        for dev in controllers:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=dev["label"], xalign=0, hexpand=True, wrap=True)
            lbl.add_css_class("sysinfo-value")
            switch = Gtk.Switch(active=not dev["auto"], valign=Gtk.Align.CENTER)  # "keep active" = NOT auto-suspend
            switch.connect("notify::active", self._on_toggle, dev["bus"], dev["device_id"])
            row.append(lbl)
            row.append(switch)
            self._list_box.append(row)

    def _on_toggle(self, switch, _pspec, bus, device_id):
        switch.set_sensitive(False)
        keep_active = switch.get_active()
        setting = "on" if keep_active else "auto"

        def run():
            from core.persistence.priv_client import default_privileged_writer
            writer = default_privileged_writer()
            result = writer.execute("device_power", "apply_temporary", f"{bus}:{device_id}:{setting}")
            GLib.idle_add(self._on_toggle_done, switch, result, keep_active)

        threading.Thread(target=run, daemon=True).start()

    def _on_toggle_done(self, switch, result, want_active):
        switch.set_sensitive(True)
        if not result.ok:
            switch.handler_block_by_func(self._on_toggle)
            switch.set_active(not want_active)
            switch.handler_unblock_by_func(self._on_toggle)
        return False


class GamingPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("input-gaming-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        header = PageHeader(
            "input-gaming-symbolic", T("tab_gaming"), T("ds_gaming_header_desc"),
            category="neutral",
        )
        self.add(wrap_in_preferences_group(header))

        g0 = make_group("gaming_readiness_title")
        self.add(g0)
        for row in (ReadinessRow(), GameModeRow(), ControllerRow()):
            row.add_prefix(IconBadge("input-gaming-symbolic", category="neutral"))
            g0.add(row)

        g1 = make_group("grp_gaming_install")
        self.add(g1)

        self.gamemode = InstallRow("gamemode", B.gamemode_installed(), risk="low",
                                   dep_pkg="gamemode",
                                   dep_check=B.gamemode_installed,
                                   dep_install=B.gamemode_install)
        self.gamemode.button.connect("clicked", self._on_gamemode)
        self.gamemode.add_prefix(IconBadge("input-gaming-symbolic", category="neutral"))
        self._add_try_button(self.gamemode, T("gaming_try_gamemode_btn"), self._on_try_gamemode)
        g1.add(self.gamemode)

        self.mango = InstallRow("mango", B.mangohud_installed(), risk="low",
                                dep_pkg="mangohud",
                                dep_check=B.mangohud_installed,
                                dep_install=B.mangohud_install)
        self.mango.button.connect("clicked", self._on_mango)
        self.mango.add_prefix(IconBadge("input-gaming-symbolic", category="neutral"))
        self._add_try_button(self.mango, T("gaming_try_mangohud_btn"), self._on_try_mangohud)
        g1.add(self.mango)

        self.lib32 = InstallRow("lib32", B.lib32_installed(), risk="low",
                                dep_pkg="lib32 (mesa:i386)",
                                dep_check=B.lib32_installed,
                                dep_install=B.lib32_install)
        self.lib32.button.connect("clicked", self._on_lib32)
        self.lib32.add_prefix(IconBadge("input-gaming-symbolic", category="neutral"))
        g1.add(self.lib32)

        self.vulkan = InstallRow("vulkan", B.vulkan_installed(), risk="low",
                                 dep_pkg="vulkan-tools",
                                 dep_check=lambda: B._cmd_exists("vulkaninfo"),
                                 dep_install=B.vulkan_install)
        self.vulkan.button.connect("clicked", self._on_vulkan)
        self.vulkan.add_prefix(IconBadge("input-gaming-symbolic", category="neutral"))
        g1.add(self.vulkan)

    def _add_try_button(self, install_row, label, handler):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(4)
        btn = Gtk.Button(label=label)
        result_lbl = Gtk.Label(xalign=0, wrap=True)
        row.append(btn)
        row.append(result_lbl)
        install_row.add_row(row)
        btn.connect("clicked", handler, btn, result_lbl)

    def _run_try(self, status_fn, btn, result_lbl):
        btn.set_sensitive(False)
        result_lbl.set_text("")

        def run():
            status = status_fn()
            GLib.idle_add(self._on_try_done, btn, result_lbl, status)

        threading.Thread(target=run, daemon=True).start()

    def _on_try_done(self, btn, result_lbl, status):
        btn.set_sensitive(True)
        text = {"ready": T("gaming_try_success"), "installed_not_ready": T("gaming_try_failed"),
                "not_installed": T("gaming_try_failed")}.get(status, "")
        result_lbl.remove_css_class("desc-con")
        result_lbl.remove_css_class("status-active")
        result_lbl.add_css_class("status-active" if status == "ready" else "desc-con")
        result_lbl.set_text(text)
        return False

    def _on_try_gamemode(self, _btn, btn, result_lbl):
        self._run_try(gr.gamemode_real_status, btn, result_lbl)

    def _on_try_mangohud(self, _btn, btn, result_lbl):
        self._run_try(gr.mangohud_real_status, btn, result_lbl)

    def _refresh_title(self):
        self.set_title(T("tab_gaming"))

    def _on_gamemode(self, _):
        run_install_in_background(self.gamemode.button, B.gamemode_install,
                                   B.gamemode_installed, self.gamemode.mark_installed)

    def _on_mango(self, _):
        run_install_in_background(self.mango.button, B.mangohud_install,
                                   B.mangohud_installed, self.mango.mark_installed)

    def _on_lib32(self, _):
        run_install_in_background(self.lib32.button, B.lib32_install,
                                   B.lib32_installed, self.lib32.mark_installed)

    def _on_vulkan(self, _):
        run_install_in_background(self.vulkan.button, B.vulkan_install,
                                   B.vulkan_installed, self.vulkan.mark_installed)
