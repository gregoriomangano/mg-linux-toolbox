import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import SwitchRow, FeatureRow, make_group
import backend.all as B
import threading

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge
from ui.design_system.action_bar import style_kernel_feature_row_buttons
from ui.design_system.section_card import make_section
from ui.design_system.status_pill import StatusPill

_system_ds_strings = {
    "ds_system_header_desc": {
        "en": "Check storage, maintenance and device status.",
        "it": "Controlla archiviazione, manutenzione e stato dei dispositivi.",
        "es": "Comprueba almacenamiento, mantenimiento y estado de los dispositivos.",
        "fr": "Vérifiez le stockage, la maintenance et l'état des périphériques.",
    },
    "ds_system_group_maintenance": {
        "en": "Disk maintenance", "it": "Manutenzione disco",
        "es": "Mantenimiento del disco", "fr": "Maintenance du disque",
    },
}
for _k, _v in _system_ds_strings.items():
    _i18n_mod._strings[_k] = _v


class SystemPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("drive-harddisk-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        header = PageHeader(
            "drive-harddisk-symbolic", T("tab_system"), T("ds_system_header_desc"),
            category="disk",
        )
        self.add(wrap_in_preferences_group(header))

        g1 = make_section("ds_system_group_maintenance")
        self.add(g1)

        # TRIM — kernel fstrim (util-linux, always present on modern linux)
        self.trim = SwitchRow("trim", B.trim_active(), risk="low",
                              dep_pkg="util-linux (fstrim)",
                              dep_check=lambda: B._cmd_exists("fstrim"),
                              dep_install=None)  # always present
        self.trim.switch.connect("notify::active", self._on_trim)
        self.trim.add_prefix(IconBadge("drive-harddisk-symbolic", category="disk"))
        style_kernel_feature_row_buttons(self.trim)
        g1.add(self.trim)

        # SMART — needs smartmontools
        self.smart = SwitchRow("smart", B.smart_active(), risk="low",
                               dep_pkg="smartmontools",
                               dep_check=lambda: B._cmd_exists("smartctl"),
                               dep_install=lambda job=None: B._install_pkg({"default": "smartmontools"}, job=job))
        self.smart.switch.connect("notify::active", self._on_smart)
        self.smart.add_prefix(IconBadge("drive-harddisk-symbolic", category="disk"))
        style_kernel_feature_row_buttons(self.smart)
        g1.add(self.smart)

        # Cache cleanup — apt/dnf/pacman/zypper, whichever this distro uses
        self.cleanup_btn = Gtk.Button(label=T("cleanup_btn"), valign=Gtk.Align.CENTER)
        self.cleanup_btn.add_css_class("ds-btn-primary")
        self.cleanup_btn.connect("clicked", self._on_cleanup)
        self.cleanup_row = FeatureRow("cleanup", self.cleanup_btn, risk="low")
        self.cleanup_row.add_prefix(IconBadge("user-trash-symbolic", category="disk"))
        self._cleanup_size_pill = StatusPill("", variant="neutral")
        self.cleanup_row.add_suffix(self._cleanup_size_pill)
        self._refresh_cleanup_size()
        g1.add(self.cleanup_row)

    def _refresh_title(self):
        self.set_title(T("tab_system"))

    def _refresh_cleanup_size(self):
        # v4: the reclaimable-size value used to be dimmed subtitle text
        # under the row title — same real B.cache_size_human() read,
        # now also a legible standalone pill next to the action button.
        text = f"{T('cleanup_size_label')}: {B.cache_size_human()}"
        self.cleanup_row.set_subtitle(text)
        self._cleanup_size_pill.set_text(B.cache_size_human())

    def _on_trim(self, sw, _):
        sw.set_active(B.trim_set(sw.get_active()))

    def _on_smart(self, sw, _):
        sw.set_active(B.smart_set(sw.get_active()))

    def _on_cleanup(self, _btn):
        self.cleanup_btn.set_label("⏳")
        self.cleanup_btn.set_sensitive(False)

        def run():
            B.clean_cache()
            GLib.idle_add(self._on_cleanup_done)

        threading.Thread(target=run, daemon=True).start()

    def _on_cleanup_done(self):
        self.cleanup_btn.set_label(T("cleanup_done"))
        self.cleanup_btn.set_sensitive(True)
        self._refresh_cleanup_size()
        return False
