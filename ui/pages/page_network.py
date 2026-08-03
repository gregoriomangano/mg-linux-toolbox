import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import SwitchRow, FeatureRow, make_group, report_toggle_result
import backend.all as B
import threading
from core.network import dns_detector, dns_manager, dns_providers
from core.network.dns_models import BackendKind
from core.network.dns_validator import validate_servers

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge
from ui.design_system.action_bar import style_kernel_feature_row_buttons
from ui.design_system.status_pill import StatusPill
from ui.design_system.value_translation import translated_value
from ui.pages.page_kernel import _widen_preferences_clamp

_network_ds_strings = {
    "ds_network_header_desc": {
        "en": "Manage connections, sharing and network protection.",
        "it": "Gestisci connessioni, condivisione e protezione della rete.",
        "es": "Gestiona conexiones, compartición y protección de la red.",
        "fr": "Gérez les connexions, le partage et la protection du réseau.",
    },
}
for _k, _v in _network_ds_strings.items():
    _i18n_mod._strings[_k] = _v


def _service_state_pill(installed: bool, active: bool) -> StatusPill:
    """A grey switch alone is ambiguous (off? not installed? unknown?) —
    this always spells the real state out in words next to it, per the
    same three/five-state vocabulary already used elsewhere (StatusPill)."""
    if not installed:
        return StatusPill(translated_value("not_installed"), variant="absent")
    if active:
        return StatusPill(translated_value("enabled"), variant="success")
    return StatusPill(translated_value("disabled"), variant="neutral")


def _security_icon_category(available: bool) -> str:
    """The icon's color signals whether this control is really usable
    on THIS system (green = the required tool is installed, grey = it
    isn't) — never a judgement about whether being on/off is "safe"
    for a given service (e.g. SSH on isn't inherently risky, it
    depends entirely on what the user needs), which this app has no
    real basis to assert on its own."""
    return "security-ok" if available else "neutral"


class NetworkPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("network-wireless-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()
        _widen_preferences_clamp(self, maximum_size=900, tightening_threshold=700)

        header = PageHeader(
            "network-wireless-symbolic", T("tab_network"), T("ds_network_header_desc"),
            category="network",
        )
        self.add(wrap_in_preferences_group(header))

        # ── Connectivity ──────────────────────────────────────────
        g1 = make_group("grp_connectivity")
        self.add(g1)

        # Wi-Fi — rfkill is always available (kernel tool)
        self.wifi = SwitchRow("wifi", B.wifi_active(), risk="low",
                              dep_pkg="network-manager",
                              dep_check=lambda: B._cmd_exists("nmcli"),
                              dep_install=lambda job=None: B._install_pkg({"debian": "network-manager", "arch": "networkmanager", "fedora": "NetworkManager", "opensuse": "NetworkManager"}, job=job))
        self.wifi.switch.connect("notify::active", self._on_wifi)
        self.wifi.add_prefix(IconBadge("network-wireless-symbolic", category="network"))
        style_kernel_feature_row_buttons(self.wifi)
        g1.add(self.wifi)

        # Wi-Fi Hotspot — same NetworkManager/nmcli backend as Wi-Fi above,
        # so it works identically cross-distro.
        self._build_hotspot_row(g1)

        # Bluetooth — kernel rfkill + bluez
        self.bt = SwitchRow("bt", B.bluetooth_active(), risk="low",
                            dep_pkg="bluez",
                            dep_check=lambda: B._cmd_exists("hciconfig") or B._cmd_exists("bluetoothctl"),
                            dep_install=lambda job=None: B._install_pkg({"debian": "bluez", "arch": "bluez", "fedora": "bluez", "opensuse": "bluez"}, job=job))
        self.bt.switch.connect("notify::active", self._on_bt)
        self.bt.add_prefix(IconBadge("bluetooth-symbolic", category="network"))
        style_kernel_feature_row_buttons(self.bt)
        g1.add(self.bt)

        # Bluetooth device scan/pairing — bluetoothctl (BlueZ), same on
        # every distro once bluez is installed (checked above already).
        self._build_btscan_row(g1)

        # IPv6 — pure sysctl/kernel, always available.
        # Switch ON must mean "IPv6 is on" (the title says "IPv6", not
        # "Disable IPv6"), so we negate the underlying disabled-flag both
        # when reading the initial state and when applying a change.
        self.ipv6 = SwitchRow("ipv6", not B.ipv6_disabled(), risk="low")
        self.ipv6.switch.connect("notify::active", self._on_ipv6)
        self.ipv6.add_prefix(IconBadge("network-wireless-symbolic", category="network"))
        style_kernel_feature_row_buttons(self.ipv6)
        g1.add(self.ipv6)

        # ── Security & Sharing ────────────────────────────────────
        g2 = make_group("grp_security")
        self.add(g2)

        # Firewall — ufw (debian/arch) or firewalld (fedora/opensuse)
        fw_dep_check = lambda: B._cmd_exists("ufw") or B._cmd_exists("firewall-cmd")
        self.fw = SwitchRow("fw", B.firewall_active(), risk="medium",
                            dep_pkg="ufw / firewalld",
                            dep_check=fw_dep_check,
                            dep_install=lambda job=None: B._install_pkg({"debian": "ufw", "arch": "ufw", "fedora": "firewalld", "opensuse": "firewalld"}, job=job))
        self.fw.switch.connect("notify::active", self._on_fw)
        self.fw.add_prefix(IconBadge("security-high-symbolic", category=_security_icon_category(fw_dep_check())))
        self._wire_status_pill(self.fw, fw_dep_check)
        style_kernel_feature_row_buttons(self.fw)
        g2.add(self.fw)

        # SSH — openssh is in all repos
        ssh_dep_check = lambda: B._service_exists("ssh") or B._service_exists("sshd")
        self.ssh = SwitchRow("ssh", B.ssh_active(), risk="low",
                             dep_pkg="openssh",
                             dep_check=ssh_dep_check,
                             dep_install=lambda job=None: B._install_pkg({"debian": "openssh-server", "arch": "openssh", "fedora": "openssh-server", "opensuse": "openssh"}, job=job))
        self.ssh.switch.connect("notify::active", self._on_ssh)
        self.ssh.add_prefix(IconBadge("network-server-symbolic", category=_security_icon_category(ssh_dep_check())))
        self._wire_status_pill(self.ssh, ssh_dep_check)
        style_kernel_feature_row_buttons(self.ssh)
        g2.add(self.ssh)

        # Samba
        samba_dep_check = lambda: B._cmd_exists("smbd")
        self.samba = SwitchRow("samba", B.samba_active(), risk="medium",
                               dep_pkg="samba",
                               dep_check=samba_dep_check,
                               dep_install=lambda job=None: B._install_pkg({"debian": "samba", "arch": "samba", "fedora": "samba", "opensuse": "samba"}, job=job))
        self.samba.switch.connect("notify::active", self._on_samba)
        self.samba.add_prefix(IconBadge("network-workgroup-symbolic", category=_security_icon_category(samba_dep_check())))
        self._wire_status_pill(self.samba, samba_dep_check)
        style_kernel_feature_row_buttons(self.samba)
        g2.add(self.samba)

        # DNS with one click — separate feature from DNS-over-TLS below:
        # this picks WHICH DNS servers are used, the switch below only
        # adds encryption on top of whichever servers are already chosen.
        self._build_dns_row(g2)

        # DNS over TLS — kernel + systemd-resolved
        dns_dep_check = lambda: B._service_exists("systemd-resolved")
        self.dns = SwitchRow("dns", B.dns_dot_active(), risk="low",
                             dep_pkg="systemd-resolved",
                             dep_check=dns_dep_check,
                             dep_install=None)
        self.dns.switch.connect("notify::active", self._on_dns)
        self.dns.add_prefix(IconBadge("channel-secure-symbolic", category=_security_icon_category(dns_dep_check())))
        self._wire_status_pill(self.dns, dns_dep_check)
        style_kernel_feature_row_buttons(self.dns)
        g2.add(self.dns)

    def _wire_status_pill(self, row, dep_check):
        """Adds an explicit StatusPill next to a SwitchRow's switch, kept
        in sync with the switch and with dep_check() — so a grey switch
        never has to be interpreted on its own (per spec: never rely on
        switch position alone to convey installed/active/unavailable)."""
        try:
            installed = bool(dep_check())
        except Exception:
            row.add_suffix(StatusPill(translated_value("unknown"), variant="neutral"))
            return
        pill = _service_state_pill(installed, row.switch.get_active())
        row.add_suffix(pill)

        def _refresh(*_args):
            try:
                is_installed = bool(dep_check())
            except Exception:
                pill.set_text(translated_value("unknown"))
                pill.set_variant("neutral")
                return
            if not is_installed:
                pill.set_text(translated_value("not_installed"))
                pill.set_variant("absent")
            elif row.switch.get_active():
                pill.set_text(translated_value("enabled"))
                pill.set_variant("success")
            else:
                pill.set_text(translated_value("disabled"))
                pill.set_variant("neutral")

        row.switch.connect("notify::active", _refresh)
        on_change(_refresh)

    def _build_hotspot_row(self, group):
        """
        Wi-Fi Hotspot row: a button that opens a small popover asking for
        SSID/password, then starts the hotspot via nmcli. Kept as a plain
        button (not a Switch) because starting it needs those two extra
        values from the user first.
        """
        self.hotspot_active = B.hotspot_active()
        self.hotspot_btn = Gtk.Button(valign=Gtk.Align.CENTER)
        self.hotspot_btn.add_css_class("lt-action-btn")
        self._update_hotspot_btn()
        self.hotspot_btn.connect("clicked", self._on_hotspot_clicked)

        self.hotspot_row = FeatureRow("hotspot", self.hotspot_btn, risk="low",
                                       dep_pkg="network-manager",
                                       dep_check=lambda: B._cmd_exists("nmcli"),
                                       dep_install=lambda job=None: B._install_pkg(
                                           {"debian": "network-manager", "arch": "networkmanager",
                                            "fedora": "NetworkManager", "opensuse": "NetworkManager"}, job=job))
        self.hotspot_row.add_prefix(IconBadge("network-wireless-hotspot-symbolic", category="network"))
        style_kernel_feature_row_buttons(self.hotspot_row)
        group.add(self.hotspot_row)

        # Popover with SSID/password fields, shown only when starting it
        self.hotspot_popover = Gtk.Popover()
        self.hotspot_popover.set_parent(self.hotspot_btn)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_size_request(240, -1)

        self.hotspot_ssid_entry = Gtk.Entry(placeholder_text=T("hotspot_ssid_placeholder"))
        self.hotspot_pwd_entry = Gtk.PasswordEntry(placeholder_text=T("hotspot_pwd_placeholder"),
                                                     show_peek_icon=True)
        confirm_btn = Gtk.Button(label=T("hotspot_start_btn"))
        confirm_btn.add_css_class("lt-action-btn")
        confirm_btn.connect("clicked", self._on_hotspot_confirm)

        self.hotspot_error_lbl = Gtk.Label(wrap=True, xalign=0)
        self.hotspot_error_lbl.add_css_class("desc-con")
        self.hotspot_error_lbl.set_visible(False)

        box.append(self.hotspot_ssid_entry)
        box.append(self.hotspot_pwd_entry)
        box.append(self.hotspot_error_lbl)
        box.append(confirm_btn)
        self.hotspot_popover.set_child(box)

    def _update_hotspot_btn(self):
        self.hotspot_btn.set_label(T("hotspot_stop_btn") if self.hotspot_active else T("hotspot_start_btn"))

    def _on_hotspot_clicked(self, _btn):
        if self.hotspot_active:
            B.hotspot_stop()
            self.hotspot_active = False
            self._update_hotspot_btn()
        else:
            self.hotspot_error_lbl.set_visible(False)
            self.hotspot_popover.popup()

    def _on_hotspot_confirm(self, _btn):
        ssid = self.hotspot_ssid_entry.get_text().strip()
        pwd = self.hotspot_pwd_entry.get_text().strip()
        if B.hotspot_start(ssid, pwd):
            self.hotspot_active = True
            self._update_hotspot_btn()
            self.hotspot_popover.popdown()
        else:
            self.hotspot_error_lbl.set_text(T("hotspot_error"))
            self.hotspot_error_lbl.set_visible(True)

    def _build_btscan_row(self, group):
        """
        "Find Bluetooth Devices" row: scans in a background thread (BlueZ
        scanning blocks for a few seconds) so the UI never freezes, then
        lists discovered devices in a popover with a Pair button each.
        """
        self.btscan_btn = Gtk.Button(label=T("btscan_btn"), valign=Gtk.Align.CENTER)
        self.btscan_btn.add_css_class("lt-action-btn")
        self.btscan_btn.connect("clicked", self._on_btscan_clicked)
        self.btscan_row = FeatureRow("btscan", self.btscan_btn, risk="low")
        self.btscan_row.add_prefix(IconBadge("bluetooth-symbolic", category="network"))
        style_kernel_feature_row_buttons(self.btscan_row)
        group.add(self.btscan_row)

        self.btscan_popover = Gtk.Popover()
        self.btscan_popover.set_parent(self.btscan_btn)
        self.btscan_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.btscan_list_box.set_margin_top(12)
        self.btscan_list_box.set_margin_bottom(12)
        self.btscan_list_box.set_margin_start(12)
        self.btscan_list_box.set_margin_end(12)
        self.btscan_list_box.set_size_request(260, -1)
        self.btscan_popover.set_child(self.btscan_list_box)

    def _on_btscan_clicked(self, _btn):
        for child in list(self.btscan_list_box):
            self.btscan_list_box.remove(child)
        loading = Gtk.Label(label=T("btscan_scanning"), wrap=True)
        loading.add_css_class("sysinfo-value-sub")
        self.btscan_list_box.append(loading)
        self.btscan_popover.popup()
        self.btscan_btn.set_sensitive(False)

        def run():
            devices = B.bluetooth_scan()
            paired = B.bluetooth_paired_macs()
            GLib.idle_add(self._on_btscan_done, devices, paired)

        threading.Thread(target=run, daemon=True).start()

    def _on_btscan_done(self, devices, paired):
        self.btscan_btn.set_sensitive(True)
        for child in list(self.btscan_list_box):
            self.btscan_list_box.remove(child)

        if not devices:
            empty = Gtk.Label(label=T("btscan_none_found"), wrap=True, xalign=0)
            empty.add_css_class("sysinfo-value-sub")
            self.btscan_list_box.append(empty)
            return False

        for mac, name in devices:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=name or mac, xalign=0, hexpand=True, wrap=True)
            lbl.add_css_class("sysinfo-value")
            row.append(lbl)

            if mac in paired:
                done = Gtk.Label(label=T("btscan_paired"))
                done.add_css_class("status-active")
                row.append(done)
            else:
                pair_btn = Gtk.Button(label=T("btscan_pair_btn"))
                pair_btn.add_css_class("lt-action-btn")
                pair_btn.connect("clicked", self._on_pair_clicked, mac)
                row.append(pair_btn)

            self.btscan_list_box.append(row)
        return False

    def _on_pair_clicked(self, btn, mac):
        btn.set_label("⏳")
        btn.set_sensitive(False)

        def run():
            ok = B.bluetooth_pair(mac)
            GLib.idle_add(self._on_pair_done, btn, ok)

        threading.Thread(target=run, daemon=True).start()

    def _on_pair_done(self, btn, ok):
        if ok:
            btn.set_label(T("btscan_paired"))
        else:
            btn.set_label(T("btscan_pair_btn"))
            btn.set_sensitive(True)
        return False

    # ── DNS with one click ────────────────────────────────────────────
    def _build_dns_row(self, group):
        self.dns_row = FeatureRow("dns_oneclick", None, risk="medium")
        self.dns_row.add_prefix(IconBadge("network-server-symbolic", category="security-ok"))
        style_kernel_feature_row_buttons(self.dns_row)
        group.add(self.dns_row)

        self._dns_provider_toggles = {}
        self._dns_selected_provider = dns_providers.AUTOMATIC
        self._dns_selected_uuid = None
        self._dns_busy = False

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(6)
        body.set_margin_bottom(6)

        backend = dns_detector.detect_backend()
        self._dns_backend = backend

        if backend != BackendKind.NETWORKMANAGER:
            note = Gtk.Label(label=T("dns_backend_unmanageable"), wrap=True, xalign=0)
            note.add_css_class("desc-con")
            body.append(note)
            self.dns_row.add_row(body)
            return

        connections = dns_detector.list_connections()
        primary = dns_detector.primary_connection()

        if not connections:
            note = Gtk.Label(label=T("dns_no_connection_detected"), wrap=True, xalign=0)
            note.add_css_class("desc-con")
            body.append(note)
            self.dns_row.add_row(body)
            return

        # Connection selector — only shown when there's real ambiguity;
        # never silently applies to "all connections at once".
        if len(connections) > 1:
            sel_label = Gtk.Label(label=T("dns_select_connection"), xalign=0)
            sel_label.add_css_class("sysinfo-label")
            body.append(sel_label)
            conn_options = Gtk.StringList.new([c.name for c in connections])
            self._dns_conn_dropdown = Gtk.DropDown(model=conn_options)
            default_idx = connections.index(primary) if primary in connections else 0
            self._dns_conn_dropdown.set_selected(default_idx)
            self._dns_conn_list = connections
            self._dns_conn_dropdown.connect("notify::selected", self._on_dns_connection_changed)
            body.append(self._dns_conn_dropdown)
            self._dns_selected_uuid = connections[default_idx].uuid
        else:
            self._dns_conn_list = connections
            self._dns_selected_uuid = connections[0].uuid
            conn_label = Gtk.Label(label=f"{T('dns_current_connection')}: {connections[0].name}",
                                   xalign=0, wrap=True)
            conn_label.add_css_class("sysinfo-value-sub")
            body.append(conn_label)

        # VPN note — informational only, never blocks anything.
        self._dns_vpn_note = Gtk.Label(label=T("dns_vpn_warning"), wrap=True, xalign=0)
        self._dns_vpn_note.add_css_class("desc-con")
        self._dns_vpn_note.set_visible(dns_detector.has_vpn_active())
        body.append(self._dns_vpn_note)

        # Provider choice
        choice_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        group_leader = None
        for provider in dns_providers.all_builtin():
            toggle = Gtk.ToggleButton(label=T(provider.name_key), active=(provider.id == dns_providers.AUTOMATIC))
            if group_leader is not None:
                toggle.set_group(group_leader)
            toggle.connect("toggled", self._on_dns_provider_toggled, provider.id)
            choice_box.append(toggle)
            self._dns_provider_toggles[provider.id] = toggle
            if group_leader is None:
                group_leader = toggle
        body.append(choice_box)

        self._dns_provider_desc = Gtk.Label(wrap=True, xalign=0)
        self._dns_provider_desc.add_css_class("desc-what")
        body.append(self._dns_provider_desc)

        # Advanced / custom — hidden by default
        self._dns_advanced = Gtk.Expander(label=T("dns_advanced_toggle"))
        custom_toggle = Gtk.ToggleButton(label=T("dns_provider_custom_name"))
        custom_toggle.set_group(group_leader)
        custom_toggle.connect("toggled", self._on_dns_provider_toggled, dns_providers.CUSTOM)
        self._dns_provider_toggles[dns_providers.CUSTOM] = custom_toggle

        custom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        custom_box.append(custom_toggle)
        self._dns_custom_ipv4_entry = Gtk.Entry(placeholder_text=T("dns_custom_ipv4_placeholder"))
        self._dns_custom_ipv6_entry = Gtk.Entry(placeholder_text=T("dns_custom_ipv6_placeholder"))
        self._dns_custom_ipv4_entry.connect("changed", self._on_dns_custom_changed)
        self._dns_custom_ipv6_entry.connect("changed", self._on_dns_custom_changed)
        custom_box.append(self._dns_custom_ipv4_entry)
        custom_box.append(self._dns_custom_ipv6_entry)
        self._dns_custom_error = Gtk.Label(wrap=True, xalign=0)
        self._dns_custom_error.add_css_class("desc-con")
        self._dns_custom_error.set_visible(False)
        custom_box.append(self._dns_custom_error)
        self._dns_advanced.set_child(custom_box)
        body.append(self._dns_advanced)

        # Actions
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._dns_try_btn = Gtk.Button(label=T("dns_try_btn"))
        self._dns_try_btn.add_css_class("lt-action-btn")
        self._dns_try_btn.connect("clicked", self._on_dns_try_clicked)
        self._dns_always_btn = Gtk.Button(label=T("dns_use_always_btn"))
        self._dns_always_btn.connect("clicked", self._on_dns_always_clicked)
        self._dns_restore_btn = Gtk.Button(label=T("dns_restore_automatic_btn"))
        self._dns_restore_btn.connect("clicked", self._on_dns_restore_clicked)
        btn_box.append(self._dns_try_btn)
        btn_box.append(self._dns_always_btn)
        btn_box.append(self._dns_restore_btn)
        body.append(btn_box)

        self._dns_result_lbl = Gtk.Label(wrap=True, xalign=0)
        self._dns_result_lbl.set_visible(False)
        body.append(self._dns_result_lbl)

        self._dns_details_btn = Gtk.Button(label=T("dns_show_details_btn"))
        self._dns_details_btn.add_css_class("flat")
        self._dns_details_btn.set_visible(False)
        self._dns_details_btn.connect("clicked", self._on_dns_toggle_details)
        body.append(self._dns_details_btn)

        self._dns_details_lbl = Gtk.Label(wrap=True, xalign=0, selectable=True)
        self._dns_details_lbl.add_css_class("sysinfo-value-sub")
        self._dns_details_lbl.set_visible(False)
        body.append(self._dns_details_lbl)

        self.dns_row.add_row(body)
        self._update_dns_provider_desc()
        self._update_dns_actions_sensitivity()

    def _on_dns_connection_changed(self, dropdown, _):
        idx = dropdown.get_selected()
        if 0 <= idx < len(self._dns_conn_list):
            self._dns_selected_uuid = self._dns_conn_list[idx].uuid
            conn = self._dns_conn_list[idx]
            self._dns_vpn_note.set_visible(conn.is_vpn or dns_detector.has_vpn_active())

    def _on_dns_provider_toggled(self, toggle, provider_id):
        if not toggle.get_active():
            return
        self._dns_selected_provider = provider_id
        self._dns_advanced.set_expanded(provider_id == dns_providers.CUSTOM)
        self._update_dns_provider_desc()
        self._update_dns_actions_sensitivity()

    def _update_dns_provider_desc(self):
        provider = dns_providers.get(self._dns_selected_provider)
        if provider is not None:
            self._dns_provider_desc.set_text(T(provider.desc_key))
        else:
            self._dns_provider_desc.set_text("")

    def _on_dns_custom_changed(self, _entry):
        self._update_dns_actions_sensitivity()

    def _update_dns_actions_sensitivity(self):
        """Prova/Usa sempre stay disabled until a custom address is
        actually valid — never just at click time."""
        if self._dns_busy:
            return
        if self._dns_selected_provider != dns_providers.CUSTOM:
            self._dns_try_btn.set_sensitive(True)
            self._dns_always_btn.set_sensitive(True)
            self._dns_custom_error.set_visible(False)
            return
        valid, ipv4, ipv6 = self._dns_custom_servers()
        has_any = bool(ipv4) or bool(ipv6)
        ok = valid and has_any
        self._dns_try_btn.set_sensitive(ok)
        self._dns_always_btn.set_sensitive(ok)
        self._dns_custom_error.set_visible(not valid)
        if not valid:
            self._dns_custom_error.set_text(T("dns_custom_invalid"))

    def _dns_custom_servers(self):
        ipv4_raw = self._dns_custom_ipv4_entry.get_text().split()
        ipv6_raw = self._dns_custom_ipv6_entry.get_text().split()
        ok4, ipv4 = validate_servers(ipv4_raw)
        ok6, ipv6 = validate_servers(ipv6_raw)
        return (ok4 and ok6), ipv4, ipv6

    def _dns_set_busy(self, busy: bool):
        self._dns_busy = busy
        for btn in (self._dns_try_btn, self._dns_always_btn, self._dns_restore_btn):
            btn.set_sensitive(not busy)
        if busy:
            self._dns_result_lbl.set_visible(True)
            self._dns_result_lbl.remove_css_class("desc-con")
            self._dns_result_lbl.remove_css_class("status-active")
            self._dns_result_lbl.set_text(T("dns_applying"))

    def _run_dns_action(self, action_fn):
        """Runs a dns_manager call in a background thread — these do a
        real (possibly slow) network reconnect + DNS verification, so
        must never block the GTK main loop."""
        if self._dns_busy or self._dns_selected_uuid is None:
            return
        if self._dns_selected_provider == dns_providers.CUSTOM:
            valid, ipv4, ipv6 = self._dns_custom_servers()
            self._dns_custom_error.set_visible(not valid)
            if not valid:
                self._dns_custom_error.set_text(T("dns_custom_invalid"))
                return
        else:
            ipv4, ipv6 = None, None

        self._dns_set_busy(True)
        uuid = self._dns_selected_uuid
        provider_id = self._dns_selected_provider

        def run():
            result = action_fn(uuid, provider_id, ipv4, ipv6)
            GLib.idle_add(self._on_dns_action_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_dns_try_clicked(self, _btn):
        self._run_dns_action(dns_manager.try_provider)

    def _on_dns_always_clicked(self, _btn):
        self._run_dns_action(dns_manager.use_always)

    def _on_dns_restore_clicked(self, _btn):
        if self._dns_busy or self._dns_selected_uuid is None:
            return
        self._dns_set_busy(True)
        uuid = self._dns_selected_uuid

        def run():
            result = dns_manager.restore_automatic(uuid)
            GLib.idle_add(self._on_dns_action_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_dns_action_done(self, result):
        self._dns_set_busy(False)
        self._dns_result_lbl.set_visible(True)
        if result.ok:
            self._dns_result_lbl.remove_css_class("desc-con")
            self._dns_result_lbl.add_css_class("status-active")
            self._dns_result_lbl.set_text(T("dns_success"))
            self._dns_details_btn.set_visible(False)
        else:
            self._dns_result_lbl.remove_css_class("status-active")
            self._dns_result_lbl.add_css_class("desc-con")
            self._dns_result_lbl.set_text(T(result.friendly_message) if result.friendly_message else T("dns_apply_failed"))
            self._dns_details_btn.set_visible(bool(result.technical_detail))
            self._dns_details_lbl.set_text(result.technical_detail)
            self._dns_details_lbl.set_visible(False)
        return False

    def _on_dns_toggle_details(self, _btn):
        self._dns_details_lbl.set_visible(not self._dns_details_lbl.get_visible())

    def _refresh_title(self):
        self.set_title(T("tab_network"))

    def _on_wifi(self, sw, _):
        result = B.wifi_set(sw.get_active())
        sw.set_active(result.value)
        report_toggle_result(self.wifi, "network", "network.wifi", result.ok, result.technical_detail)
    def _on_bt(self, sw, _):
        result = B.bluetooth_set(sw.get_active())
        sw.set_active(result.value)
        report_toggle_result(self.bt, "network", "network.bluetooth", result.ok, result.technical_detail)
    def _on_ipv6(self, sw, _):
        want_enabled = sw.get_active()
        result = B.ipv6_set_disabled(not want_enabled)
        sw.set_active(not result.value)
        report_toggle_result(self.ipv6, "network", "network.ipv6", result.ok, result.technical_detail)
    def _on_fw(self, sw, _):
        result = B.firewall_set(sw.get_active())
        sw.set_active(result.value)
        report_toggle_result(self.fw, "network", "network.firewall", result.ok, result.technical_detail)
    def _on_ssh(self, sw, _):
        result = B.ssh_set(sw.get_active())
        sw.set_active(result.value)
        report_toggle_result(self.ssh, "network", "network.ssh", result.ok, result.technical_detail)
    def _on_samba(self, sw, _):
        result = B.samba_set(sw.get_active())
        sw.set_active(result.value)
        report_toggle_result(self.samba, "network", "network.samba", result.ok, result.technical_detail)
    def _on_dns(self, sw, _):
        result = B.dns_dot_set(sw.get_active())
        sw.set_active(result.value)
        report_toggle_result(self.dns, "network", "dns.dot", result.ok, result.technical_detail,
                             friendly_key=result.friendly_message or "kf_err_generic")
