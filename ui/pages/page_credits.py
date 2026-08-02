"""
"Crediti" — app identity, the technologies M.G Linux Toolbox is built
with, the real system tools/projects its code actually calls out to,
a non-affiliation disclaimer, and the license (shares the same
"Leggi la licenza" window as the "Informazioni" page).
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from core.i18n import T, on_change
from core import version as app_version
from core import release_config
from ui.widgets import make_group
from ui.license_dialog import show_license_window, GPL_OFFICIAL_URL
from core.uri_launcher import open_external_url

# Proper names — not translated, listed once, representative of what
# the codebase genuinely calls or manages (not an exhaustive man-page
# dump): backend/all.py, core/virt_setup.py, core/apparmor_setup.py,
# core/bootloader_iommu.py, core/vfio_setup.py, core/container_engines.py,
# core/snapshot_tools.py, core/audio_devices.py.
_REAL_TOOLS = (
    "systemd, Polkit (pkexec), NetworkManager, UFW/firewalld, Samba, OpenSSH, CUPS, "
    "Gutenprint, HPLIP, PipeWire/PulseAudio, Docker, Podman, Distrobox, QEMU/KVM, "
    "libvirt, Virt-Manager, AppArmor, SELinux, GRUB, systemd-boot, GameMode, "
    "MangoHud, Vulkan, EasyEffects, Timeshift, Snapper, Btrfs, transactional-update, "
    "rpm-ostree, TLP, power-profiles-daemon, system76-power, TuneD"
)


class CreditsPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("emblem-favorite-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        g_id = Adw.PreferencesGroup()
        self.add(g_id)
        id_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)
        id_box.set_margin_top(12)
        id_box.set_margin_bottom(12)
        name_lbl = Gtk.Label(label=app_version.display_version())
        name_lbl.add_css_class("title-1")
        author_lbl = Gtk.Label(label="Gregorio Mangano")
        id_box.append(name_lbl)
        id_box.append(author_lbl)
        id_row = Adw.PreferencesRow(activatable=False, selectable=False)
        id_row.set_child(id_box)
        g_id.add(id_row)

        g_tech = make_group("credits_group_technologies")
        self.add(g_tech)
        for name in ("Python", "GTK4", "Libadwaita", "PyGObject", "Linux kernel"):
            g_tech.add(Adw.ActionRow(title=name, activatable=False, selectable=False))

        g_tools = make_group("credits_group_tools")
        self.add(g_tools)
        tools_lbl = Gtk.Label(label=_REAL_TOOLS, wrap=True, xalign=0)
        tools_lbl.set_margin_top(8)
        tools_lbl.set_margin_bottom(8)
        tools_lbl.set_margin_start(14)
        tools_lbl.set_margin_end(14)
        tools_row = Adw.PreferencesRow(activatable=False, selectable=False)
        tools_row.set_child(tools_lbl)
        g_tools.add(tools_row)

        g_legal = Adw.PreferencesGroup()
        self.add(g_legal)
        self._disclaimer_lbl = Gtk.Label(wrap=True, xalign=0)
        self._disclaimer_lbl.set_margin_top(8)
        self._disclaimer_lbl.set_margin_bottom(4)
        self._disclaimer_lbl.set_margin_start(14)
        self._disclaimer_lbl.set_margin_end(14)
        disclaimer_row = Adw.PreferencesRow(activatable=False, selectable=False)
        disclaimer_row.set_child(self._disclaimer_lbl)
        g_legal.add(disclaimer_row)

        self._license_row = Adw.ActionRow(title=release_config.LICENSE_NAME, activatable=False)
        license_btn = Gtk.Button()
        license_btn.connect("clicked", lambda _b: show_license_window(self))
        self._license_row.add_suffix(license_btn)
        self._license_btn = license_btn
        g_legal.add(self._license_row)

        self._gpl_page_row = Adw.ActionRow(activatable=False)
        gpl_page_btn = Gtk.Button()
        gpl_page_btn.connect("clicked", lambda _b: open_external_url(GPL_OFFICIAL_URL))
        self._gpl_page_row.add_suffix(gpl_page_btn)
        self._gpl_page_btn = gpl_page_btn
        g_legal.add(self._gpl_page_row)

        on_change(self._refresh_labels)
        self._refresh_labels()

    def _refresh_labels(self):
        self._disclaimer_lbl.set_text(T("credits_disclaimer"))
        self._license_btn.set_label(T("license_read_btn"))
        self._gpl_page_row.set_title(T("license_official_page_btn"))
        self._gpl_page_btn.set_label(T("license_official_page_btn"))

    def _refresh_title(self):
        self.set_title(T("tab_credits"))
