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
from core.kernel_features.registry import register
from core.kernel_features.ksm import KsmFeature
from ui.pages.page_kernel import BooleanKernelFeatureRow, _widen_preferences_clamp

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
        if status["nested_active"] is not None:
            lines.append(f"{T('kvm_nested_label')}: {T(_bool_key(status['nested_active']))}")
        for line in lines:
            lbl = Gtk.Label(label=line, xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            self._detail_box.append(lbl)

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
        for line in lines:
            lbl = Gtk.Label(label=line, xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            self._detail_box.append(lbl)

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

    def _on_wizard_clicked(self, _btn):
        devices = vfs.list_pci_devices()
        body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body_box.append(Gtk.Label(label=T("vfio_wizard_body"), wrap=True, xalign=0))

        checks = {}
        for dev in devices:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            check = Gtk.CheckButton()
            check.set_sensitive(not dev["protected"])
            label_text = f"{dev['address']}  {dev['description']}  (IOMMU group {dev['iommu_group']})"
            if dev["protected"]:
                reason_key = "vfio_protected_storage" if dev["protection_reason"] == "storage_controller" else "vfio_protected_gpu"
                label_text += f"  — {T(reason_key)}"
            row.append(check)
            row.append(Gtk.Label(label=label_text, wrap=True, xalign=0, hexpand=True))
            body_box.append(row)
            checks[dev["address"]] = check

        scroller = Gtk.ScrolledWindow(min_content_height=200, max_content_height=400)
        scroller.set_child(body_box)

        dialog = Adw.MessageDialog(transient_for=self.get_root(), heading=T("vfio_wizard_title"))
        dialog.set_extra_child(scroller)
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("vfio_configure_confirm_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda d, r: self._on_wizard_response(r, checks))
        dialog.present()

    def _on_wizard_response(self, response, checks):
        if response != "confirm":
            return
        selected = [addr for addr, check in checks.items() if check.get_active()]
        if not selected:
            self._show_result(T("vfio_no_devices_selected"), False)
            return
        self._wizard_btn.set_sensitive(False)

        def run():
            result = vfs.configure_vfio(selected)
            GLib.idle_add(self._on_configure_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_configure_done(self, result):
        self._wizard_btn.set_sensitive(True)
        self._show_result(T("vfio_configure_success" if result["ok"] else "vfio_configure_failed"), result["ok"])
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
        from ui.widgets import run_install_in_background
        run_install_in_background(self.button, B.docker_install, B.docker_installed,
                                   lambda: (self.mark_installed(), self._refresh_detail()))


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
        from ui.widgets import run_install_in_background
        run_install_in_background(self.button, B.podman_install, B.podman_installed,
                                   lambda: (self.mark_installed(), self._refresh_detail()))


class DistroboxRow(InstallRow):
    def __init__(self):
        status = ce.distrobox_status()
        installed = status["state"] != ce.DISTROBOX_STATE_NOT_INSTALLED
        super().__init__("distrobox", installed, risk="low",
                         dep_pkg="distrobox", dep_check=lambda: B._cmd_exists("distrobox"),
                         dep_install=B.distrobox_install,
                         dep_pkg_map={"debian": "distrobox", "arch": "distrobox", "fedora": "distrobox", "default": "distrobox"})
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
        from ui.widgets import run_install_in_background
        run_install_in_background(self.button, B.distrobox_install, B.distrobox_installed,
                                   lambda: (self.mark_installed(), self._refresh_detail()))

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
