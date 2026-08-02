"""
"Stampanti e driver" tab — v4: printing moved out of Sicurezza (it was
never a security function) into its own page. Every row here is the
SAME SwitchRow/InstallRow instance construction and the SAME
backend.all callbacks that used to live in page_security.py — nothing
about CUPS/driver detection, install commands or state reading changed,
only which page shows them.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import SwitchRow, InstallRow, make_group, run_install_in_background
import backend.all as B

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge
from ui.design_system.action_bar import style_kernel_feature_row_buttons

_printers_ds_strings = {
    "tab_printers": {
        "en": "Printers and drivers", "it": "Stampanti e driver",
        "es": "Impresoras y controladores", "fr": "Imprimantes et pilotes",
    },
    "ds_printers_header_desc": {
        "en": "Set up the printing service and check available components.",
        "it": "Configura il servizio di stampa e verifica i componenti disponibili.",
        "es": "Configura el servicio de impresión y comprueba los componentes disponibles.",
        "fr": "Configurez le service d'impression et vérifiez les composants disponibles.",
    },
}
for _k, _v in _printers_ds_strings.items():
    _i18n_mod._strings[_k] = _v


class PrintersPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("printer-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        header = PageHeader(
            "printer-symbolic", T("tab_printers"), T("ds_printers_header_desc"),
            category="network",
        )
        self.add(wrap_in_preferences_group(header))

        g_service = make_group("grp_sys_services")
        self.add(g_service)

        # Moved verbatim from SecurityPage: same SwitchRow, same
        # backend.all.cups_active()/cups_set() calls.
        self.cups = SwitchRow("cups", B.cups_active(), risk="low",
                              dep_pkg="cups",
                              dep_check=lambda: B._service_exists("cups"),
                              dep_install=lambda job=None: B._install_pkg({"default": "cups"}, job=job))
        self.cups.switch.connect("notify::active", self._on_cups)
        self.cups.add_prefix(IconBadge("printer-symbolic", category="network"))
        style_kernel_feature_row_buttons(self.cups)
        g_service.add(self.cups)

        # ── Printer drivers ────────────────────────────────────────
        # Moved verbatim from SecurityPage: same curated per-distro
        # package sets (backend.all.PRINTER_DRIVER_SETS), same install
        # commands, same detection.
        g_drivers = make_group("grp_printing")
        self.add(g_drivers)

        self.printer_base = InstallRow("printer_base", B.printer_set_installed("printer_base"), risk="low")
        self.printer_base.button.connect("clicked", self._on_printer_base)
        self.printer_base.add_prefix(IconBadge("printer-symbolic", category="network"))
        g_drivers.add(self.printer_base)

        self.printer_universal = InstallRow("printer_universal", B.printer_set_installed("printer_universal"), risk="low")
        self.printer_universal.button.connect("clicked", self._on_printer_universal)
        self.printer_universal.add_prefix(IconBadge("printer-symbolic", category="network"))
        g_drivers.add(self.printer_universal)

        self.printer_hp = InstallRow("printer_hp", B.printer_set_installed("printer_hp"), risk="low")
        self.printer_hp.button.connect("clicked", self._on_printer_hp)
        self.printer_hp.add_prefix(IconBadge("printer-symbolic", category="network"))
        g_drivers.add(self.printer_hp)

    def _refresh_title(self):
        self.set_title(T("tab_printers"))

    def _on_cups(self, sw, _):
        sw.set_active(B.cups_set(sw.get_active()))

    def _install_printer_set(self, row, key):
        run_install_in_background(
            row.button,
            lambda: B.printer_set_install(key),
            lambda: B.printer_set_installed(key),
            row.mark_installed)

    def _on_printer_base(self, _btn):
        self._install_printer_set(self.printer_base, "printer_base")

    def _on_printer_universal(self, _btn):
        self._install_printer_set(self.printer_universal, "printer_universal")

    def _on_printer_hp(self, _btn):
        self._install_printer_set(self.printer_hp, "printer_hp")
