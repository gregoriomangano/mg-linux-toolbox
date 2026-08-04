import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import FeatureRow, SwitchRow, make_group, report_toggle_result
import backend.all as B
from core import apparmor_setup as aa
from core.kernel_features.base import SupportStatus
from core.kernel_features.registry import register
from core.kernel_features.security import SELinuxFeature
from ui.pages.page_kernel import ChoiceKernelFeatureRow

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge
from ui.design_system.action_bar import style_kernel_feature_row_buttons
from ui.design_system.status_pill import state_pill

_security_ds_strings = {
    "ds_security_header_desc": {
        "en": "Check active protections and system security settings.",
        "it": "Controlla le protezioni attive e le impostazioni di sicurezza del sistema.",
        "es": "Comprueba las protecciones activas y la configuración de seguridad del sistema.",
        "fr": "Vérifiez les protections actives et les paramètres de sécurité du système.",
    },
    "ds_secureboot_title": {
        "en": "Secure Boot", "it": "Secure Boot", "es": "Secure Boot", "fr": "Secure Boot",
    },
    "ds_secureboot_desc": {
        "en": "This protection can be turned on from the computer's UEFI settings.",
        "it": "Questa protezione può essere attivata dalle impostazioni UEFI del computer.",
        "es": "Esta protección se puede activar desde la configuración UEFI del equipo.",
        "fr": "Cette protection peut être activée depuis les paramètres UEFI de l'ordinateur.",
    },
    "ds_state_active": {"en": "Active", "it": "Attivo", "es": "Activo", "fr": "Actif"},
    "ds_state_inactive": {"en": "Inactive", "it": "Disattivato", "es": "Inactivo", "fr": "Inactif"},
    "ds_state_unknown": {"en": "Unknown status", "it": "Stato sconosciuto", "es": "Estado desconocido", "fr": "État inconnu"},
    # 2026-08-04: SSH/root-login consistency fix — the toggle is now
    # gated on the same real state shown here, never clickable against
    # a config that doesn't exist or can't be read.
    "rootssh_state_not_installed": {"en": "Not applicable — SSH server not installed",
                                      "it": "Non applicabile — Server SSH non installato",
                                      "es": "No aplicable — Servidor SSH no instalado",
                                      "fr": "Non applicable — Serveur SSH non installé"},
    "rootssh_state_undetermined": {"en": "State could not be determined", "it": "Stato non determinabile",
                                     "es": "No se pudo determinar el estado", "fr": "État impossible à déterminer"},
    "rootssh_state_disabled": {"en": "Root login is currently disabled.", "it": "L'accesso root è attualmente disattivato.",
                                 "es": "El acceso root está actualmente desactivado.", "fr": "La connexion root est actuellement désactivée."},
    "rootssh_state_allowed": {"en": "Root login is currently allowed.", "it": "L'accesso root è attualmente consentito.",
                                "es": "El acceso root está actualmente permitido.", "fr": "La connexion root est actuellement autorisée."},
}
for _k, _v in _security_ds_strings.items():
    _i18n_mod._strings[_k] = _v


_APPARMOR_PILL = {
    "active_configured":        ("success", True,  "apparmor_state_active_configured"),
    "supported_not_configured": ("warning", False, "apparmor_state_supported_not_configured"),
    "inactive":                 ("neutral", False, "apparmor_state_inactive"),
    "not_available":            ("absent",  False, "apparmor_state_not_available"),
    "unknown":                  ("neutral", False, "apparmor_state_unknown"),
}


def _apparmor_state() -> str:
    """One of 5 real, detected states — never guessed. 'configured' means
    AppArmor is installed, its service is active, AND at least one
    profile is actually loaded (aa-status reports it) — being merely
    installed+running with zero profiles protects nothing yet, so it's
    kept distinct from "active_configured"."""
    try:
        if not aa.is_installed():
            return "not_available"
        if not aa.service_active():
            return "inactive"
        return "active_configured" if aa.list_profiles() else "supported_not_configured"
    except Exception:
        return "unknown"


class AppArmorRow(FeatureRow):
    def __init__(self):
        self._detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._status_pill = state_pill("unknown", "")
        super().__init__("apparmor", None, risk="medium")
        self.add_suffix(self._status_pill)
        self.add_row(self._detail_box)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._enable_btn = Gtk.Button(label=T("apparmor_enable_btn"))
        self._enable_btn.add_css_class("lt-action-btn")
        self._enable_btn.connect("clicked", self._on_enable)
        self._disable_btn = Gtk.Button(label=T("apparmor_disable_btn"))
        self._disable_btn.add_css_class("destructive-action")
        self._disable_btn.connect("clicked", self._on_disable)
        self._reload_btn = Gtk.Button(label=T("apparmor_reload_btn"))
        self._reload_btn.connect("clicked", self._on_reload)
        self._profiles_btn = Gtk.Button(label=T("apparmor_show_profiles_btn"))
        self._profiles_btn.connect("clicked", self._on_show_profiles)
        for b in (self._enable_btn, self._disable_btn, self._reload_btn, self._profiles_btn):
            btn_box.append(b)
        self.add_row(btn_box)

        self._refresh_detail()

    def _refresh_detail(self):
        state = _apparmor_state()
        variant, show_check, text_key = _APPARMOR_PILL[state]
        self._status_pill.set_text(T(text_key))
        self._status_pill.set_variant(variant)
        self._status_pill.set_show_check(show_check)

        child = self._detail_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._detail_box.remove(child)
            child = nxt

        if not aa.is_installed():
            note = Gtk.Label(label=T("apparmor_not_installed_note"), xalign=0, wrap=True)
            note.add_css_class("desc-con")
            self._detail_box.append(note)
            for b in (self._enable_btn, self._disable_btn, self._reload_btn, self._profiles_btn):
                b.set_sensitive(False)
            return

        active = aa.service_active()
        lbl = Gtk.Label(label=T("apparmor_service_active" if active else "apparmor_service_inactive"),
                         xalign=0, wrap=True)
        lbl.add_css_class("status-active" if active else "sysinfo-value")
        self._detail_box.append(lbl)
        self._enable_btn.set_visible(not active)
        self._disable_btn.set_visible(active)

    def _run_bg(self, btn, fn):
        btn.set_sensitive(False)

        def run():
            result = fn()
            GLib.idle_add(self._on_action_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_enable(self, _btn):
        self._run_bg(self._enable_btn, aa.enable_service)

    def _on_disable(self, _btn):
        self._run_bg(self._disable_btn, aa.disable_service)

    def _on_reload(self, _btn):
        self._run_bg(self._reload_btn, aa.reload_profiles)

    def _on_action_done(self, ok):
        self._enable_btn.set_sensitive(True)
        self._disable_btn.set_sensitive(True)
        self._reload_btn.set_sensitive(True)
        self._refresh_detail()
        return False

    def _on_show_profiles(self, _btn):
        profiles = aa.list_profiles()
        body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if not profiles:
            body_box.append(Gtk.Label(label=T("apparmor_no_profiles"), xalign=0, wrap=True))
        for profile in profiles:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.append(Gtk.Label(label=f"{profile['path']} ({profile['mode']})", xalign=0, hexpand=True, wrap=True))
            enforce_btn = Gtk.Button(label=T("apparmor_enforce_btn"))
            enforce_btn.connect("clicked", self._make_profile_action(aa.enforce_profile, profile["path"]))
            complain_btn = Gtk.Button(label=T("apparmor_complain_btn"))
            complain_btn.connect("clicked", self._make_profile_action(aa.complain_profile, profile["path"]))
            disable_btn = Gtk.Button(label=T("apparmor_disable_profile_btn"))
            disable_btn.add_css_class("destructive-action")
            disable_btn.connect("clicked", self._make_profile_action(aa.disable_profile, profile["path"]))
            restore_btn = Gtk.Button(label=T("apparmor_restore_profile_btn"))
            restore_btn.connect("clicked", self._make_profile_restore_action(profile["path"]))
            for b in (enforce_btn, complain_btn, disable_btn, restore_btn):
                row.append(b)
            body_box.append(row)

        scroller = Gtk.ScrolledWindow(min_content_height=200, max_content_height=450)
        scroller.set_child(body_box)
        dialog = Adw.MessageDialog(transient_for=self.get_root(), heading=T("apparmor_profiles_dialog_title"))
        dialog.set_extra_child(scroller)
        dialog.add_response("close", T("dialog_close_btn"))
        dialog.present()

    def _make_profile_action(self, fn, path):
        def handler(_btn):
            threading.Thread(target=fn, args=(path,), daemon=True).start()
        return handler

    def _make_profile_restore_action(self, path):
        def handler(_btn):
            threading.Thread(target=aa.restore_profile, args=(path,), daemon=True).start()
        return handler


class _SecureBootRow(FeatureRow):
    """FeatureRow for the read-only Secure Boot status row. Same title/
    desc/pro text as any other "secureboot"-prefixed FeatureRow, but the
    "when to avoid" line grows a real, detected reason appended after it
    whenever the state is "unknown" — never a second guess at
    active/inactive, only an honest explanation of why detection failed
    this time (see backend.all.secureboot_unknown_reason)."""
    def __init__(self, control, reason_key: "str | None"):
        self._reason_key = reason_key
        super().__init__("secureboot", control, risk="low")

    def _refresh(self):
        super()._refresh()
        if self._reason_key:
            self._lbl_con.set_text(
                f"⚠️  {T('when_avoid')}: {T('secureboot_con')} {T(self._reason_key)}"
            )


class SecurityPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("security-high-symbolic")
        on_change(self._refresh_title)
        on_change(lambda: self._refresh_rootssh_state(B.root_ssh_state()))
        self._refresh_title()

        header = PageHeader(
            "security-high-symbolic", T("tab_security"), T("ds_security_header_desc"),
            category="security-ok",
        )
        self.add(wrap_in_preferences_group(header))

        # Firewall, SSH and DNS-over-TLS live only in "Rete & Sicurezza"
        # now (they used to be duplicated here with their own separate
        # switch instances, which could show out-of-sync state).
        # v4: printing (CUPS + drivers) moved to its own "Stampanti e
        # driver" page — it was never a security function.

        # ── Hardening ─────────────────────────────────────────────
        g2 = make_group("grp_hardening")
        self.add(g2)

        # Switch ON must mean "root login via SSH is allowed" (title is
        # "Root Login via SSH", not "Disable Root SSH Login"), so negate
        # the underlying disabled-flag. Beta 4 fix: this used to stay
        # clickable even when SSH wasn't installed at all, because
        # root_ssh_disabled() silently read a missing sshd_config as
        # "not disabled" — now gated on the same root_ssh_state() every
        # other page agrees on (not_installed / undetermined / disabled
        # / allowed), so it's never toggled against a file that doesn't
        # exist or can't be verified.
        rootssh_state = B.root_ssh_state()
        self.rootssh = SwitchRow("rootssh", rootssh_state == "allowed", risk="low",
                                 dep_pkg="openssh",
                                 dep_check=lambda: B.ssh_server_installed(),
                                 dep_install=None)
        self.rootssh.switch.connect("notify::active", self._on_rootssh)
        self.rootssh.add_prefix(IconBadge("security-high-symbolic", category="security-ok"))
        self._rootssh_state_lbl = Gtk.Label(xalign=0, wrap=True)
        self._rootssh_state_lbl.add_css_class("sysinfo-value-sub")
        self.rootssh.add_row(self._rootssh_state_lbl)
        self._refresh_rootssh_state(rootssh_state)
        style_kernel_feature_row_buttons(self.rootssh)
        g2.add(self.rootssh)

        self.autoupd = SwitchRow("autoupdate", B.auto_updates_active(), risk="low",
                                 dep_pkg="unattended-upgrades / dnf-automatic / pacman-contrib",
                                 dep_check=B.auto_updates_dep_ok,
                                 dep_install=B.auto_updates_dep_install)
        self.autoupd.switch.connect("notify::active", self._on_autoupd)
        self.autoupd.add_prefix(IconBadge("software-update-available-symbolic", category="security-ok"))
        style_kernel_feature_row_buttons(self.autoupd)
        g2.add(self.autoupd)

        # ── AppArmor / SELinux ────────────────────────────────────
        g_sec = make_group("grp_apparmor_selinux")
        self.add(g_sec)
        if aa.is_installed():
            apparmor_row = AppArmorRow()
            apparmor_row.add_prefix(IconBadge("security-high-symbolic", category="security-ok"))
            style_kernel_feature_row_buttons(apparmor_row)
            g_sec.add(apparmor_row)
        selinux_feature = register(SELinuxFeature())
        if selinux_feature.probe() != SupportStatus.UNSUPPORTED_KERNEL:
            selinux_row = ChoiceKernelFeatureRow(selinux_feature, "selinux")
            selinux_row.add_prefix(IconBadge("security-high-symbolic", category="security-ok"))
            style_kernel_feature_row_buttons(selinux_row)
            g_sec.add(selinux_row)

        # ── Info-only ─────────────────────────────────────────────
        g3 = make_group("grp_sys_services")
        self.add(g3)

        # v5: secureboot_state() keeps "couldn't detect" (mokutil missing,
        # no UEFI, ...) distinct from "really disabled" — three real,
        # detected states, never a fabricated "Disabled" for one we
        # actually couldn't read. Still fully read-only, no switch.
        sb_state = B.secureboot_state()
        sb_text = {"active": T("ds_state_active"), "inactive": T("ds_state_inactive")}.get(
            sb_state, T("ds_state_unknown"))
        sb_pill = state_pill(sb_state if sb_state in ("active", "inactive") else "unknown", sb_text)
        # V7: "Unknown status" alone never explains itself — when it
        # happens, the expanded card must say WHY (BIOS/Legacy boot,
        # missing efivarfs, missing mokutil, permissions, read error),
        # never silently, and never turned into a guessed active/inactive.
        sb_reason_key = None
        if sb_state == "unknown":
            reason = B.secureboot_unknown_reason()
            sb_reason_key = {
                "no_efi": "secureboot_reason_no_efi",
                "no_efivarfs": "secureboot_reason_no_efivarfs",
                "tool_missing": "secureboot_reason_tool_missing",
                "permission_denied": "secureboot_reason_permission",
            }.get(reason, "secureboot_reason_read_error")
        secureboot_row = _SecureBootRow(sb_pill, sb_reason_key)
        secureboot_row.add_prefix(IconBadge("security-high-symbolic", category="security-ok" if sb_state == "active" else "neutral"))
        g3.add(secureboot_row)

    def _refresh_title(self):
        self.set_title(T("tab_security"))

    _ROOTSSH_STATE_KEYS = {
        "not_installed": "rootssh_state_not_installed",
        "undetermined": "rootssh_state_undetermined",
        "disabled": "rootssh_state_disabled",
        "allowed": "rootssh_state_allowed",
    }

    def _refresh_rootssh_state(self, state: str):
        actionable = state in ("disabled", "allowed")
        self.rootssh.switch.set_sensitive(actionable)
        self._rootssh_state_lbl.set_text(T(self._ROOTSSH_STATE_KEYS.get(state, "rootssh_state_undetermined")))

    def _on_rootssh(self, sw, _):
        want_allowed = sw.get_active()
        result = B.root_ssh_set_disabled(not want_allowed)
        sw.set_active(not result.value)
        report_toggle_result(self.rootssh, "security", "security.root_ssh", result.ok,
                             result.technical_detail, friendly_key=result.friendly_message or "kf_err_generic")
        self._refresh_rootssh_state(B.root_ssh_state())
    def _on_autoupd(self, sw, _):
        sw.set_active(B.auto_updates_set(sw.get_active()))
