import logging
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import FeatureRow, InstallRow, make_group
import backend.all as B

from core import virt_readiness as vr
from core import virt_setup as vs
from core import bootloader_iommu as bi
from core import vfio_setup as vfs
from core import container_engines as ce
from core.executor import Job
from core.kernel_features.registry import register
from core.kernel_features.ksm import KsmFeature
from ui.pages.page_kernel import BooleanKernelFeatureRow, _widen_preferences_clamp

logger = logging.getLogger(__name__)

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge
from ui.design_system.action_bar import style_kernel_feature_row_buttons
from ui.design_system.section_card import make_section

_virt_ds_strings = {
    "ds_virt_header_desc": {
        "en": "Prepare this computer for virtual machines, passthrough and containers.",
        "it": "Prepara il computer per macchine virtuali, passthrough e container.",
        "es": "Prepara este equipo para máquinas virtuales, passthrough y contenedores.",
        "fr": "Préparez cet ordinateur pour les machines virtuelles, le passthrough et les conteneurs.",
    },
    "ds_virt_group_hw":  {"en": "Hardware virtualization", "it": "Virtualizzazione hardware", "es": "Virtualización por hardware", "fr": "Virtualisation matérielle"},
    "ds_virt_group_mem": {"en": "Memory saving", "it": "Risparmio memoria", "es": "Ahorro de memoria", "fr": "Économie de mémoire"},
    "ds_virt_group_containers": {"en": "Containers", "it": "Container", "es": "Contenedores", "fr": "Conteneurs"},
}
for _k, _v in _virt_ds_strings.items():
    _i18n_mod._strings[_k] = _v


def _bool_key(value: bool) -> str:
    return "kvm_present_yes" if value else "kvm_present_no"


class KvmRow(FeatureRow):
    def __init__(self):
        self._detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        super().__init__("kvm", None, risk="low")
        self.add_row(self._detail_box)

        self._fix_btn = Gtk.Button()
        self._fix_btn.add_css_class("lt-action-btn")
        self._fix_btn.connect("clicked", self._on_fix_clicked)
        self._fix_result = Gtk.Label(wrap=True, xalign=0)
        self._fix_result.set_visible(False)
        self.add_row(self._fix_btn)
        self.add_row(self._fix_result)

        setup_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._configure_btn = Gtk.Button(label=T("kvm_configure_btn"))
        self._configure_btn.add_css_class("lt-action-btn")
        self._configure_btn.connect("clicked", self._on_configure_clicked)
        self._deactivate_btn = Gtk.Button(label=T("kvm_deactivate_btn"))
        self._deactivate_btn.add_css_class("destructive-action")
        self._deactivate_btn.connect("clicked", self._on_deactivate_clicked)
        self._restore_config_btn = Gtk.Button(label=T("kvm_restore_config_btn"))
        self._restore_config_btn.connect("clicked", self._on_restore_config_clicked)
        setup_box.append(self._configure_btn)
        setup_box.append(self._deactivate_btn)
        setup_box.append(self._restore_config_btn)
        self.add_row(setup_box)

        vm_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._vm_btn = Gtk.Button()
        self._vm_btn.add_css_class("lt-action-btn")
        self._vm_btn.connect("clicked", self._on_virt_manager_clicked)
        vm_box.append(self._vm_btn)
        self.add_row(vm_box)

        self._setup_result = Gtk.Label(wrap=True, xalign=0)
        self._setup_result.set_visible(False)
        self.add_row(self._setup_result)

        self._refresh_setup_state()
        self._refresh_detail()

    _STATE_KEYS = {
        "ready": "kvm_state_ready",
        "missing_components": "kvm_state_missing_components",
        "missing_permissions": "kvm_state_missing_permissions",
        "unavailable": "kvm_state_unavailable",
    }

    def _refresh_detail(self):
        status = vr.check_kvm()
        child = self._detail_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._detail_box.remove(child)
            child = nxt

        state_lbl = Gtk.Label(xalign=0, wrap=True)
        state_lbl.set_text(T(self._STATE_KEYS[status["state"]]))
        state_lbl.add_css_class("status-active" if status["state"] == "ready" else "sysinfo-value")
        self._detail_box.append(state_lbl)

        module_text = status["module_loaded"] or T("kvm_module_none")
        lines = [
            f"{T('kvm_cpu_label')}: {status['cpu_vendor'].upper() if status['cpu_vendor'] else T('kvm_state_unavailable')}",
            f"{T('kvm_module_label')}: {module_text}",
            f"{T('kvm_device_label')}: {T(_bool_key(status['device_exists']))}",
            f"{T('kvm_group_label')}: {T(_bool_key(status['in_kvm_group'] or status['device_writable']))}",
        ]
        if status.get("virtual_machine") is not None:
            lines.append(
                f"{T('kvm_vm_label')}: {T('kvm_vm_yes' if status['virtual_machine'] else 'kvm_vm_no')}"
            )
        if status["nested_active"] is not None:
            lines.append(f"{T('kvm_nested_label')}: {T(_bool_key(status['nested_active']))}")
        for line in lines:
            lbl = Gtk.Label(label=line, xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            self._detail_box.append(lbl)

        if status.get("virtual_machine") and status["state"] != "ready":
            note = Gtk.Label(label=T("kvm_vm_note"), xalign=0, wrap=True)
            note.add_css_class("desc-con")
            self._detail_box.append(note)

        show_fix = status["state"] == "missing_permissions"
        self._fix_btn.set_visible(show_fix)
        self._fix_btn.set_label(T("kvm_fix_group_btn"))
        self._fix_btn.set_sensitive(True)

    def _on_fix_clicked(self, _btn):
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=T("kvm_fix_group_confirm_title"),
            body=T("kvm_fix_group_confirm_body"),
        )
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("kvm_fix_group_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_fix_dialog_response)
        dialog.present()

    def _on_fix_dialog_response(self, _dialog, response):
        if response != "confirm":
            return
        self._fix_btn.set_sensitive(False)

        def run():
            result = vr.fix_kvm_group_membership()
            GLib.idle_add(self._on_fix_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_fix_done(self, result):
        self._fix_result.set_visible(True)
        self._fix_result.remove_css_class("desc-con")
        self._fix_result.remove_css_class("status-active")
        if result["ok"]:
            self._fix_result.set_text(T("kvm_fix_group_success"))
            self._fix_result.add_css_class("status-active")
            self._fix_btn.set_visible(False)
        else:
            self._fix_result.set_text(T("kvm_fix_group_failed"))
            self._fix_result.add_css_class("desc-con")
            self._fix_btn.set_sensitive(True)
        return False

    def _refresh_setup_state(self):
        self._vm_btn.set_label(T("virt_manager_open_btn") if vs.virt_manager_installed()
                                else f"{T('install_btn')} {T('virt_manager_label')}")

    def _show_setup_result(self, text: str, ok: bool):
        self._setup_result.set_visible(True)
        self._setup_result.remove_css_class("desc-con")
        self._setup_result.remove_css_class("status-active")
        self._setup_result.set_text(text)
        self._setup_result.add_css_class("status-active" if ok else "desc-con")

    def _on_configure_clicked(self, _btn):
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=T("kvm_configure_confirm_title"), body=T("kvm_configure_confirm_body"),
        )
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("kvm_configure_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_configure_dialog_response)
        dialog.present()

    def _on_configure_dialog_response(self, _dialog, response):
        if response != "confirm":
            return
        self._configure_btn.set_sensitive(False)
        self._configure_btn.set_label("⏳")

        def run():
            result = vs.configure_kvm()
            GLib.idle_add(self._on_configure_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_configure_done(self, result):
        self._configure_btn.set_sensitive(True)
        self._configure_btn.set_label(T("kvm_configure_btn"))
        self._show_setup_result(T("kvm_configure_success" if result["ok"] else "kvm_configure_failed"), result["ok"])
        self._refresh_detail()
        return False

    def _on_deactivate_clicked(self, _btn):
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=T("kvm_deactivate_confirm_title"), body=T("kvm_deactivate_confirm_body"),
        )
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("kvm_deactivate_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_deactivate_dialog_response)
        dialog.present()

    def _on_deactivate_dialog_response(self, _dialog, response):
        if response != "confirm":
            return
        self._deactivate_btn.set_sensitive(False)

        def run():
            result = vs.deactivate_kvm_services()
            GLib.idle_add(self._on_deactivate_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_deactivate_done(self, result):
        self._deactivate_btn.set_sensitive(True)
        self._show_setup_result(T("kvm_configure_success" if result["ok"] else "kvm_configure_failed"), result["ok"])
        self._refresh_detail()
        return False

    def _on_restore_config_clicked(self, _btn):
        self._restore_config_btn.set_sensitive(False)

        def run():
            result = vs.restore_kvm_configuration()
            GLib.idle_add(self._on_restore_config_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_restore_config_done(self, result):
        self._restore_config_btn.set_sensitive(True)
        if result.get("reason") == "nothing_to_restore":
            self._show_setup_result(T("kvm_restore_config_nothing"), True)
        else:
            self._show_setup_result(T("kvm_configure_success" if result["ok"] else "kvm_configure_failed"), result["ok"])
        self._refresh_detail()
        return False

    def _on_virt_manager_clicked(self, _btn):
        if vs.virt_manager_installed():
            vs.open_virt_manager()
            return
        self._vm_btn.set_sensitive(False)
        self._vm_btn.set_label("⏳")

        def run():
            installed = vs.install_virt_manager()
            GLib.idle_add(self._on_virt_manager_install_done, installed)

        threading.Thread(target=run, daemon=True).start()

    def _on_virt_manager_install_done(self, installed):
        self._vm_btn.set_sensitive(True)
        self._refresh_setup_state()
        if not installed:
            self._show_setup_result(T("kvm_configure_failed"), False)
        return False


class IommuRow(FeatureRow):
    def __init__(self):
        self._detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        super().__init__("iommu", None, risk="high")
        self.add_row(self._detail_box)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._configure_btn = Gtk.Button(label=T("iommu_configure_btn"))
        self._configure_btn.add_css_class("lt-action-btn")
        self._configure_btn.connect("clicked", self._on_configure_clicked)
        self._deactivate_btn = Gtk.Button(label=T("iommu_deactivate_btn"))
        self._deactivate_btn.add_css_class("destructive-action")
        self._deactivate_btn.connect("clicked", self._on_deactivate_clicked)
        self._restore_btn = Gtk.Button(label=T("iommu_restore_btn"))
        self._restore_btn.connect("clicked", self._on_restore_clicked)
        self._verify_btn = Gtk.Button(label=T("iommu_verify_btn"))
        self._verify_btn.connect("clicked", self._on_verify_clicked)
        for b in (self._configure_btn, self._deactivate_btn, self._restore_btn, self._verify_btn):
            btn_box.append(b)
        self.add_row(btn_box)

        self._result_lbl = Gtk.Label(wrap=True, xalign=0)
        self._result_lbl.set_visible(False)
        self.add_row(self._result_lbl)

        self._refresh_detail()

    def _refresh_detail(self):
        status = vr.check_iommu()
        child = self._detail_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._detail_box.remove(child)
            child = nxt

        active_lbl = Gtk.Label(xalign=0, wrap=True)
        active_lbl.set_text(T("iommu_active_yes") if status["active"] else T("iommu_active_no"))
        active_lbl.add_css_class("status-active" if status["active"] else "sysinfo-value")
        self._detail_box.append(active_lbl)

        lines = []
        if status["technology"]:
            lines.append(f"{T('iommu_technology_label')}: {status['technology']}")
        if status["active"]:
            lines.append(f"{T('iommu_groups_label')}: {status['group_count']}")
        if status.get("virtual_machine") is not None:
            lines.append(
                f"{T('kvm_vm_label')}: {T('kvm_vm_yes' if status['virtual_machine'] else 'kvm_vm_no')}"
            )
        for line in lines:
            lbl = Gtk.Label(label=line, xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            self._detail_box.append(lbl)

        if status.get("virtual_machine"):
            note = Gtk.Label(label=T("iommu_vm_note"), xalign=0, wrap=True)
            note.add_css_class("desc-con" if not status["active"] else "sysinfo-value-sub")
            self._detail_box.append(note)

        self._configure_btn.set_visible(not status["active"])
        self._deactivate_btn.set_visible(status["active"])

    def _show_result(self, text: str, ok: bool):
        self._result_lbl.set_visible(True)
        self._result_lbl.remove_css_class("desc-con")
        self._result_lbl.remove_css_class("status-active")
        self._result_lbl.set_text(text)
        self._result_lbl.add_css_class("status-active" if ok else "desc-con")

    def _confirm(self, title_key, body_key, confirm_label_key, on_confirm):
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(), heading=T(title_key), body=T(body_key),
        )
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T(confirm_label_key))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda d, r: on_confirm() if r == "confirm" else None)
        dialog.present()

    def _run_bg(self, btn, fn, on_done):
        btn.set_sensitive(False)

        def run():
            result = fn()
            GLib.idle_add(on_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_configure_clicked(self, _btn):
        self._confirm("iommu_configure_confirm_title", "iommu_configure_confirm_body",
                       "iommu_configure_btn",
                       lambda: self._run_bg(self._configure_btn, bi.configure_iommu, self._on_action_done))

    def _on_deactivate_clicked(self, _btn):
        self._confirm("iommu_deactivate_confirm_title", "iommu_deactivate_confirm_body",
                       "iommu_deactivate_btn",
                       lambda: self._run_bg(self._deactivate_btn, bi.deactivate_iommu, self._on_action_done))

    def _on_restore_clicked(self, _btn):
        self._run_bg(self._restore_btn, bi.restore_iommu_configuration, self._on_action_done)

    def _on_action_done(self, result):
        self._configure_btn.set_sensitive(True)
        self._deactivate_btn.set_sensitive(True)
        self._restore_btn.set_sensitive(True)
        if result.get("reason") == "unsupported_bootloader":
            self._show_result(T("iommu_unsupported_bootloader"), False)
        elif result.get("reboot_required"):
            self._show_result(f"{T('iommu_configure_success')} {T('iommu_reboot_required_note')}", True)
        else:
            self._show_result(T("iommu_configure_success" if result.get("ok") else "iommu_configure_failed"),
                               result.get("ok", False))
        self._refresh_detail()
        return False

    def _on_verify_clicked(self, _btn):
        self._run_bg(self._verify_btn, bi.verify_after_reboot, self._on_verify_done)

    def _on_verify_done(self, active: bool):
        self._verify_btn.set_sensitive(True)
        self._show_result(T("iommu_verify_active" if active else "iommu_verify_inactive"), active)
        self._refresh_detail()
        return False


class VfioRow(FeatureRow):
    def __init__(self):
        self._detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        super().__init__("vfio", None, risk="high")
        self.add_row(self._detail_box)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._wizard_btn = Gtk.Button(label=T("vfio_wizard_btn"))
        self._wizard_btn.add_css_class("lt-action-btn")
        self._wizard_btn.connect("clicked", self._on_wizard_clicked)
        self._remove_btn = Gtk.Button(label=T("vfio_remove_btn"))
        self._remove_btn.add_css_class("destructive-action")
        self._remove_btn.connect("clicked", self._on_remove_clicked)
        self._restore_driver_btn = Gtk.Button(label=T("vfio_restore_driver_btn"))
        self._restore_driver_btn.connect("clicked", self._on_restore_driver_clicked)
        for b in (self._wizard_btn, self._remove_btn, self._restore_driver_btn):
            btn_box.append(b)
        self.add_row(btn_box)

        self._result_lbl = Gtk.Label(wrap=True, xalign=0)
        self._result_lbl.set_visible(False)
        self.add_row(self._result_lbl)

        self._refresh_detail()

    def _refresh_detail(self):
        status = vr.check_vfio()
        child = self._detail_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._detail_box.remove(child)
            child = nxt

        modules_text = ", ".join(status["modules_loaded"]) if status["modules_loaded"] else T("vfio_none")
        devices_text = ", ".join(status["devices"]) if status["devices"] else T("vfio_none")
        lines = [
            f"{T('vfio_modules_label')}: {modules_text}",
            f"{T('vfio_devices_label')}: {devices_text}",
        ]
        for line in lines:
            lbl = Gtk.Label(label=line, xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            self._detail_box.append(lbl)

        if not status["iommu_active"]:
            note = Gtk.Label(label=T("vfio_iommu_required_note"), xalign=0, wrap=True)
            note.add_css_class("desc-con")
            self._detail_box.append(note)

    def _show_result(self, text: str, ok: bool):
        self._result_lbl.set_visible(True)
        self._result_lbl.remove_css_class("desc-con")
        self._result_lbl.remove_css_class("status-active")
        self._result_lbl.set_text(text)
        self._result_lbl.add_css_class("status-active" if ok else "desc-con")

    # ── Wizard, step 1: pick IOMMU groups (never single devices) ──────
    @staticmethod
    def _device_display_name(dev) -> str:
        """Plain-language line for a device: kind + human vendor/device
        name. Technical ids only ever appear under 'Mostra dettagli
        tecnici'."""
        kind = T(dev.get("kind_key", "vfio_kind_other"))
        name = dev.get("name") or ""
        return f"{kind} — {name}" if name else kind

    @staticmethod
    def _protection_label(dev) -> str:
        reason = dev.get("protection_reason")
        if reason == "storage_controller":
            return T("vfio_protected_storage_boot")
        if reason == "primary_gpu":
            return T("vfio_protected_gpu_desktop")
        if reason == "essential_device":
            return T("vfio_protected_essential")
        return T("vfio_not_recommended")

    def _on_wizard_clicked(self, _btn):
        status = vr.check_vfio()
        devices = vfs.list_pci_devices()
        groups = vfs.passthrough_groups(devices, iommu_active=status["iommu_active"])
        selectable_groups = [g for g in groups if g["selectable"]]

        # No safe candidate (or IOMMU off): a clear explanation, no list
        # of codes that looks selectable, no privileged operation, and no
        # "Seleziona almeno un dispositivo" dead end.
        if not selectable_groups:
            body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            title = Gtk.Label(label=T("vfio_no_candidates_title"), wrap=True, xalign=0)
            hint = Gtk.Label(label=T("vfio_no_candidates_hint"), wrap=True, xalign=0)
            hint.add_css_class("dim-label")
            body_box.append(title)
            body_box.append(hint)
            if not status["iommu_active"]:
                iommu_note = Gtk.Label(label=T("vfio_iommu_required_note"), wrap=True, xalign=0)
                iommu_note.add_css_class("desc-con")
                body_box.append(iommu_note)
            dialog = Adw.MessageDialog(transient_for=self.get_root(), heading=T("vfio_wizard_title"))
            dialog.set_extra_child(body_box)
            dialog.add_response("close", T("kf_dialog_cancel"))
            dialog.present()
            return

        body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body_box.append(Gtk.Label(label=T("vfio_wizard_body"), wrap=True, xalign=0))
        hint = Gtk.Label(label=T("vfio_select_group_hint"), wrap=True, xalign=0)
        hint.add_css_class("dim-label")
        body_box.append(hint)

        group_checks = {}
        for group_info in groups:
            group_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            check = Gtk.CheckButton()
            check.set_sensitive(group_info["selectable"])
            header.append(check)
            group_lbl = Gtk.Label(
                label=T("vfio_group_label").format(group=group_info["group"] or "—"),
                xalign=0, hexpand=True)
            group_lbl.add_css_class("heading")
            header.append(group_lbl)
            group_box.append(header)

            if not group_info["selectable"]:
                reason_key = {
                    "contains_protected": "vfio_group_reason_contains_protected",
                    "no_group": "vfio_group_reason_no_group",
                    "no_iommu": "vfio_group_reason_no_iommu",
                }.get(group_info["reason"], "vfio_not_recommended")
                reason_lbl = Gtk.Label(label=T(reason_key), wrap=True, xalign=0)
                reason_lbl.add_css_class("desc-con")
                reason_lbl.set_margin_start(30)
                group_box.append(reason_lbl)

            for dev in group_info["devices"]:
                line = self._device_display_name(dev)
                if dev["protected"]:
                    line += f"  — {self._protection_label(dev)}"
                dev_lbl = Gtk.Label(label=line, wrap=True, xalign=0)
                dev_lbl.set_margin_start(30)
                if dev["protected"] or not group_info["selectable"]:
                    dev_lbl.add_css_class("dim-label")
                group_box.append(dev_lbl)

            # Technical ids behind an expander, per group.
            tech_lines = "\n".join(
                f"{d['address']}  {d['description']}" for d in group_info["devices"])
            expander = Gtk.Expander(label=T("vfio_show_tech_details"))
            tech_lbl = Gtk.Label(label=tech_lines, xalign=0, selectable=True)
            tech_lbl.add_css_class("dim-label")
            tech_lbl.set_margin_start(30)
            expander.set_child(tech_lbl)
            expander.set_margin_start(30)
            group_box.append(expander)

            body_box.append(group_box)
            if group_info["selectable"]:
                group_checks[group_info["group"]] = (check, group_info)

        scroller = Gtk.ScrolledWindow(min_content_height=240, max_content_height=420)
        scroller.set_child(body_box)

        dialog = Adw.MessageDialog(transient_for=self.get_root(), heading=T("vfio_wizard_title"))
        dialog.set_extra_child(scroller)
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("vfio_configure_confirm_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        # "Configura" only becomes active once at least one group is
        # ticked — the "Seleziona almeno un dispositivo" message can no
        # longer happen at all.
        dialog.set_response_enabled("confirm", False)

        def on_toggled(_check):
            any_active = any(c.get_active() for c, _g in group_checks.values())
            dialog.set_response_enabled("confirm", any_active)

        for check, _g in group_checks.values():
            check.connect("toggled", on_toggled)

        dialog.connect("response", lambda d, r: self._on_wizard_response(r, group_checks))
        dialog.present()

    def _on_wizard_response(self, response, group_checks):
        if response != "confirm":
            return
        selected_groups = [g for c, g in group_checks.values() if c.get_active()]
        if not selected_groups:
            return
        self._present_summary(selected_groups)

    # ── Wizard, step 2: full summary + explicit high-risk confirm ─────
    def _current_driver(self, address: str) -> str:
        import os
        try:
            return os.path.basename(os.readlink(f"/sys/bus/pci/devices/{address}/driver"))
        except OSError:
            return "—"

    def _present_summary(self, selected_groups):
        addresses = [d["address"] for g in selected_groups for d in g["devices"]]
        devices = [d for g in selected_groups for d in g["devices"]]

        body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body_box.append(Gtk.Label(label=T("vfio_summary_devices"), wrap=True, xalign=0))
        for dev in devices:
            line = (f"• {self._device_display_name(dev)}"
                    f"  ({T('vfio_group_label').format(group=dev['iommu_group'])})\n"
                    f"   {T('vfio_summary_driver_now')}: {self._current_driver(dev['address'])}"
                    f" → {T('vfio_summary_driver_after')}: vfio-pci")
            lbl = Gtk.Label(label=line, wrap=True, xalign=0)
            body_box.append(lbl)

        files_lbl = Gtk.Label(
            label=f"{T('vfio_summary_files')}:\n  {vfs.MODPROBE_FILE}\n  {vfs.MODULES_LOAD_FILE}",
            wrap=True, xalign=0)
        files_lbl.add_css_class("dim-label")
        body_box.append(files_lbl)

        from core.distro import distro as _distro
        initramfs_cmd = ("mkinitcpio -P" if _distro.is_arch
                          else "update-initramfs -u" if _distro.is_debian
                          else "dracut -f")
        initramfs_lbl = Gtk.Label(label=f"{T('vfio_summary_initramfs')}: {initramfs_cmd}",
                                   wrap=True, xalign=0)
        initramfs_lbl.add_css_class("dim-label")
        body_box.append(initramfs_lbl)

        body_box.append(Gtk.Label(label=T("vfio_summary_reboot"), wrap=True, xalign=0))
        undo_lbl = Gtk.Label(label=T("vfio_summary_undo"), wrap=True, xalign=0)
        undo_lbl.add_css_class("dim-label")
        body_box.append(undo_lbl)

        risk_check = Gtk.CheckButton(label=T("vfio_confirm_high_risk"))
        body_box.append(risk_check)

        scroller = Gtk.ScrolledWindow(min_content_height=220, max_content_height=420)
        scroller.set_child(body_box)

        dialog = Adw.MessageDialog(transient_for=self.get_root(), heading=T("vfio_summary_title"))
        dialog.set_extra_child(scroller)
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("vfio_configure_confirm_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_enabled("confirm", False)
        risk_check.connect("toggled",
                           lambda c: dialog.set_response_enabled("confirm", c.get_active()))
        dialog.connect("response", lambda d, r: self._on_summary_response(r, addresses))
        dialog.present()

    def _on_summary_response(self, response, addresses):
        if response != "confirm":
            return
        self._wizard_btn.set_sensitive(False)

        def run():
            result = vfs.configure_vfio(addresses)
            GLib.idle_add(self._on_configure_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_configure_done(self, result):
        self._wizard_btn.set_sensitive(True)
        if result["ok"]:
            self._show_result(T("vfio_configure_success"), True)
        else:
            # Show the specific friendly message when there is one (e.g.
            # missing admin component, initramfs rollback) instead of a
            # generic failure; raw detail stays out of the main UI.
            reason = result.get("reason") or ""
            text = T(reason) if reason and T(reason) != reason else T("vfio_configure_failed")
            self._show_result(text, False)
        self._refresh_detail()
        return False

    def _run_bg(self, btn, fn):
        btn.set_sensitive(False)

        def run():
            result = fn()
            GLib.idle_add(self._on_remove_or_restore_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_remove_clicked(self, _btn):
        self._run_bg(self._remove_btn, vfs.remove_vfio_configuration)

    def _on_restore_driver_clicked(self, _btn):
        self._run_bg(self._restore_driver_btn, vfs.restore_original_driver)

    def _on_remove_or_restore_done(self, result):
        self._remove_btn.set_sensitive(True)
        self._restore_driver_btn.set_sensitive(True)
        self._show_result(T("vfio_configure_success" if result["ok"] else "vfio_configure_failed"), result["ok"])
        self._refresh_detail()
        return False


_DOCKER_STATE_KEYS = {
    ce.DOCKER_STATE_NOT_INSTALLED: "docker_state_not_installed",
    ce.DOCKER_STATE_NOT_STARTED: "docker_state_not_started",
    ce.DOCKER_STATE_MISSING_PERMISSIONS: "docker_state_missing_permissions",
    ce.DOCKER_STATE_READY: "docker_state_ready",
}

_PODMAN_STATE_KEYS = {
    ce.PODMAN_STATE_NOT_INSTALLED: "podman_state_not_installed",
    ce.PODMAN_STATE_NOT_READY: "podman_state_not_ready",
    ce.PODMAN_STATE_READY: "podman_state_ready",
}

_DISTROBOX_STATE_KEYS = {
    ce.DISTROBOX_STATE_NOT_INSTALLED: "distrobox_state_not_installed",
    ce.DISTROBOX_STATE_NO_BACKEND: "distrobox_state_no_backend",
    ce.DISTROBOX_STATE_READY: "distrobox_state_ready",
}


class DockerRow(InstallRow):
    def __init__(self):
        status = ce.docker_status()
        installed = status["state"] != ce.DOCKER_STATE_NOT_INSTALLED
        super().__init__("docker", installed, risk="medium",
                         dep_pkg="docker", dep_check=lambda: B._cmd_exists("docker"),
                         dep_install=B.docker_install,
                         dep_pkg_map={"debian": "docker.io", "arch": "docker", "fedora": "docker"})
        self.button.connect("clicked", self._on_install)
        self._detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_row(self._detail_box)
        self._refresh_detail()

    def _refresh_detail(self):
        status = ce.docker_status()
        child = self._detail_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._detail_box.remove(child)
            child = nxt
        if status["state"] == ce.DOCKER_STATE_NOT_INSTALLED:
            return
        state_lbl = Gtk.Label(label=T(_DOCKER_STATE_KEYS[status["state"]]), xalign=0, wrap=True)
        state_lbl.add_css_class("status-active" if status["state"] == ce.DOCKER_STATE_READY else "sysinfo-value")
        self._detail_box.append(state_lbl)
        note = Gtk.Label(label=T("docker_privilege_note"), xalign=0, wrap=True)
        note.add_css_class("sysinfo-value-sub")
        self._detail_box.append(note)

    def _on_install(self, _btn):
        from ui.widgets import run_install_in_background, report_toggle_result
        # Real final verification: the daemon actually being active, not
        # just the binary being on disk (see backend.all.docker_ready).
        run_install_in_background(self.button, B.docker_install, B.docker_ready,
                                   lambda: (self.mark_installed(), self._refresh_detail()),
                                   on_failure=lambda: (self._refresh_detail(),
                                                        report_toggle_result(self, "virt", "virt.docker_install", False)))


class PodmanRow(InstallRow):
    def __init__(self):
        status = ce.podman_status()
        installed = status["state"] != ce.PODMAN_STATE_NOT_INSTALLED
        super().__init__("podman", installed, risk="low",
                         dep_pkg="podman", dep_check=lambda: B._cmd_exists("podman"),
                         dep_install=B.podman_install, dep_pkg_map={"default": "podman"})
        self.button.connect("clicked", self._on_install)
        self._detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_row(self._detail_box)
        self._refresh_detail()

    def _refresh_detail(self):
        status = ce.podman_status()
        child = self._detail_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._detail_box.remove(child)
            child = nxt
        if status["state"] == ce.PODMAN_STATE_NOT_INSTALLED:
            return
        state_lbl = Gtk.Label(label=T(_PODMAN_STATE_KEYS[status["state"]]), xalign=0, wrap=True)
        state_lbl.add_css_class("status-active" if status["state"] == ce.PODMAN_STATE_READY else "sysinfo-value")
        self._detail_box.append(state_lbl)
        if status["rootless"] is not None:
            r_lbl = Gtk.Label(label=f"{T('podman_rootless_label')}: {T(_bool_key(status['rootless']))}",
                              xalign=0, wrap=True)
            r_lbl.add_css_class("sysinfo-value-sub")
            self._detail_box.append(r_lbl)
        if status["rootless"] and not (status["subuid_configured"] and status["subgid_configured"]):
            note = Gtk.Label(label=T("podman_subid_missing_note"), xalign=0, wrap=True)
            note.add_css_class("desc-con")
            self._detail_box.append(note)

    def _on_install(self, _btn):
        from ui.widgets import run_install_in_background, report_toggle_result
        run_install_in_background(self.button, B.podman_install, B.podman_installed,
                                   lambda: (self.mark_installed(), self._refresh_detail()),
                                   on_failure=lambda: report_toggle_result(self, "virt", "virt.podman_install", False))


class DistroboxRow(InstallRow):
    def __init__(self):
        status = ce.distrobox_status()
        # Real readiness, not just "the binary is on disk" — a distrobox
        # install with no working backend must not show as done (see
        # ce.distrobox_install / the "Installed" pill would otherwise
        # hide the button on a machine that can't actually run anything).
        installed = status["state"] == ce.DISTROBOX_STATE_READY
        super().__init__("distrobox", installed, risk="low",
                         dep_pkg="distrobox",
                         dep_check=lambda: ce.distrobox_status()["state"] == ce.DISTROBOX_STATE_READY,
                         dep_install=lambda job=None: ce.distrobox_install(job=job))
        self._install_job = None
        self.button.connect("clicked", self._on_install)
        self._detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_row(self._detail_box)

        self._try_btn = Gtk.Button(label=T("distrobox_try_btn"))
        self._try_btn.add_css_class("lt-action-btn")
        self._try_btn.connect("clicked", self._on_try_clicked)
        self.add_row(self._try_btn)

        self._try_result = Gtk.Label(wrap=True, xalign=0)
        self._try_result.set_visible(False)
        self.add_row(self._try_result)

        self._refresh_detail()

    def _refresh_detail(self):
        status = ce.distrobox_status()
        child = self._detail_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._detail_box.remove(child)
            child = nxt
        self._try_btn.set_visible(status["state"] == ce.DISTROBOX_STATE_READY)
        if status["state"] == ce.DISTROBOX_STATE_NOT_INSTALLED:
            return
        state_lbl = Gtk.Label(label=T(_DISTROBOX_STATE_KEYS[status["state"]]), xalign=0, wrap=True)
        state_lbl.add_css_class("status-active" if status["state"] == ce.DISTROBOX_STATE_READY else "sysinfo-value")
        self._detail_box.append(state_lbl)
        if status["backend"]:
            b_lbl = Gtk.Label(label=f"{T('distrobox_backend_label')}: {status['backend']}", xalign=0, wrap=True)
            b_lbl.add_css_class("sysinfo-value-sub")
            self._detail_box.append(b_lbl)

    def _on_install(self, _btn):
        if not self.button.get_sensitive():
            return  # guard against a double-click starting a second install
        plan = ce.distrobox_install_plan()
        if not plan["packages"]:
            # Distrobox binary and a ready backend already both exist —
            # nothing to confirm, just re-verify and reflect reality.
            self._run_install()
            return
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=T("distrobox_install_confirm_title"),
            body=T("distrobox_install_confirm_body").format(packages=", ".join(plan["packages"])),
        )
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("install_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_install_confirm_response)
        dialog.present()

    def _on_install_confirm_response(self, _dialog, response):
        if response == "confirm":
            self._run_install()

    def _run_install(self):
        from ui.widgets import report_toggle_result
        self.button.set_label("⏳")
        self.button.set_sensitive(False)
        self._install_job = Job()

        def run():
            try:
                result = ce.distrobox_install(job=self._install_job)
            except Exception as exc:
                logger.exception("Distrobox install failed")
                result = ce.DistroboxInstallResult(False, [], None, "install_generic_error")
            GLib.idle_add(self._on_install_done, result, report_toggle_result)

        threading.Thread(target=run, name="mg-distrobox-install", daemon=True).start()

    def _on_install_done(self, result, report_toggle_result):
        self._install_job = None
        self._refresh_detail()
        if result.ok:
            self.mark_installed()
        else:
            self.button.set_label(T("install_btn"))
            self.button.set_sensitive(True)
        report_toggle_result(self, "virt", "virt.distrobox_install", result.ok,
                              result.technical_detail(), friendly_key=result.friendly_message or "kf_err_generic")
        return False

    def _on_try_clicked(self, _btn):
        plan = ce.distrobox_test_plan()
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=T("distrobox_try_confirm_title"),
            body=f"{T('distrobox_try_confirm_body')}\n\n{plan['image']} → {plan['container_name']} → {plan['command']}",
        )
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("distrobox_try_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_try_dialog_response)
        dialog.present()

    def _on_try_dialog_response(self, _dialog, response):
        if response != "confirm":
            return
        self._try_btn.set_sensitive(False)
        self._try_btn.set_label("⏳")

        def run():
            result = ce.run_distrobox_test()
            GLib.idle_add(self._on_try_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_try_done(self, result):
        self._try_btn.set_sensitive(True)
        self._try_btn.set_label(T("distrobox_try_btn"))
        self._try_result.set_visible(True)
        self._try_result.remove_css_class("desc-con")
        self._try_result.remove_css_class("status-active")
        if result["ok"]:
            self._try_result.set_text(T("distrobox_try_success"))
            self._try_result.add_css_class("status-active")
        else:
            self._try_result.set_text(T("distrobox_try_failed"))
            self._try_result.add_css_class("desc-con")
        return False


class VirtPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("computer-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()
        _widen_preferences_clamp(self, maximum_size=900, tightening_threshold=700)

        header = PageHeader(
            "computer-symbolic", T("tab_virt"), T("ds_virt_header_desc"),
            category="virt",
        )
        self.add(wrap_in_preferences_group(header))

        g1 = make_section("ds_virt_group_hw")
        self.add(g1)
        for row in (KvmRow(), IommuRow(), VfioRow()):
            row.add_prefix(IconBadge("computer-symbolic", category="virt"))
            style_kernel_feature_row_buttons(row)
            g1.add(row)

        g2 = make_section("ds_virt_group_mem")
        self.add(g2)
        ksm_row = BooleanKernelFeatureRow(register(KsmFeature()), "virt_ksm")
        ksm_row.add_prefix(IconBadge("computer-symbolic", category="memory"))
        style_kernel_feature_row_buttons(ksm_row)
        g2.add(ksm_row)

        g3 = make_section("ds_virt_group_containers")
        self.add(g3)
        for row in (DockerRow(), PodmanRow(), DistroboxRow()):
            row.add_prefix(IconBadge("computer-symbolic", category="virt"))
            g3.add(row)

    def _refresh_title(self):
        self.set_title(T("tab_virt"))
