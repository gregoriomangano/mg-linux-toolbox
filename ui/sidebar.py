"""
Grouped block-style sidebar navigation for wide windows. Only
navigates between pages that already exist in the app's ViewStack —
never invents a page. Each entry is a real SidebarTile block, not a
plain list row.
"""
import os

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from core.i18n import T, on_change
from core import i18n as _i18n_mod
from core import release_config
from core.uri_launcher import open_external_url
from ui.dashboard.sidebar_tile import SidebarTile

_sidebar_strings = {
    "nav_group_overview": {"en": "OVERVIEW", "it": "PANORAMICA", "es": "RESUMEN", "fr": "APERÇU"},
    "nav_group_system":   {"en": "SYSTEM", "it": "SISTEMA", "es": "SISTEMA", "fr": "SYSTÈME"},
    "nav_group_network":  {"en": "NETWORK & DEVICES", "it": "RETE E DISPOSITIVI", "es": "RED Y DISPOSITIVOS", "fr": "RÉSEAU ET PÉRIPHÉRIQUES"},
    "nav_group_usage":    {"en": "SOFTWARE & SERVICES", "it": "SOFTWARE E SERVIZI", "es": "SOFTWARE Y SERVICIOS", "fr": "LOGICIELS ET SERVICES"},
    "nav_group_protection": {"en": "PROTECTION", "it": "PROTEZIONE", "es": "PROTECCIÓN", "fr": "PROTECTION"},
    "nav_overview_item":  {"en": "Overview", "it": "Panoramica", "es": "Resumen", "fr": "Aperçu"},
    "sidebar_subtitle":   {"en": "Linux control center", "it": "Centro di controllo Linux", "es": "Centro de control Linux", "fr": "Centre de contrôle Linux"},
    "sidebar_footer":     {"en": "Linux · simplicity · control", "it": "Linux · semplicità · controllo", "es": "Linux · simplicidad · control", "fr": "Linux · simplicité · contrôle"},
}
for _k, _v in _sidebar_strings.items():
    _i18n_mod._strings[_k] = _v

# (i18n_title_key, internal_page_name, icon_name) per group. Every
# internal_page_name here MUST already exist as a key in window.PAGES —
# this module never creates a page, only links to real ones.
SIDEBAR_GROUPS = [
    ("nav_group_overview", [
        ("nav_overview_item", "info", "go-home-symbolic"),
    ]),
    ("nav_group_system", [
        ("tab_kernel", "kernel", "emblem-system-symbolic"),
        ("tab_system", "system", "drive-harddisk-symbolic"),
        ("tab_performance", "performance", "battery-good-symbolic"),
    ]),
    ("nav_group_network", [
        ("tab_network", "network", "network-wireless-symbolic"),
        ("tab_audio", "audio", "audio-speakers-symbolic"),
        ("tab_printers", "printers", "printer-symbolic"),
    ]),
    ("nav_group_usage", [
        ("tab_software_repos", "software_repos", "system-software-install-symbolic"),
        ("tab_gaming", "gaming", "input-gaming-symbolic"),
        ("tab_virt", "virt", "computer-symbolic"),
        ("tab_services", "services", "system-run-symbolic"),
    ]),
    ("nav_group_protection", [
        ("tab_security", "security", "security-high-symbolic"),
        ("tab_history", "history", "document-open-recent-symbolic"),
    ]),
]

# Give each entry enough height and padding to remain readable.
# 2026-08-04: trimmed from 280 -> 256 (~8.6%) to give the content area a
# little more room, per spec (max ~8-10%, never so narrow a label wraps
# or an icon shrinks — font/icon sizes and row padding are untouched,
# this only reduces the empty margin either side of them).
SIDEBAR_WIDE_WIDTH = 256
SIDEBAR_COMPACT_WIDTH = 72


class Sidebar(Gtk.Box):
    def __init__(self, stack: Adw.ViewStack, on_navigate=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("mgv2-sidebar")
        self.set_size_request(SIDEBAR_WIDE_WIDTH, -1)
        self._stack = stack
        self._on_navigate = on_navigate
        self._tiles = {}          # internal_name -> SidebarTile
        self._tile_labels = []    # (SidebarTile, i18n_key) for compact hide + i18n refresh
        self._group_titles = []   # widgets hidden in compact mode

        self.append(self._build_header())

        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add_css_class("mgv2-sidebar-scroll")
        groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for title_key, items in SIDEBAR_GROUPS:
            groups_box.append(self._build_group(title_key, items))
        scroller.set_child(groups_box)
        self.append(scroller)

        self.append(self._build_footer())

        stack.connect("notify::visible-child-name", self._on_stack_changed)
        self._on_stack_changed(stack, None)
        on_change(self._refresh_labels)

    # ── header ───────────────────────────────────────────────────
    def _build_header(self) -> Gtk.Widget:
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.add_css_class("mgv2-sidebar-header")

        # v3: this is now the single in-app identity block (the old
        # duplicate icon+name button in the top-left of the window was
        # removed) — the whole row is clickable and opens the real,
        # already-published project page, same URL the old topbar
        # button used.
        brand_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        icon_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..",
            "assets", "branding", "mg-icon-64.png"
        )
        icon_wrap = Gtk.Box()
        icon_wrap.add_css_class("mgv2-sidebar-brand-icon-wrap")
        icon = Gtk.Image.new_from_file(icon_path) if os.path.isfile(icon_path) \
            else Gtk.Image.new_from_icon_name("go-home-symbolic")
        # v2: the v1 icon (30px) had "insufficient presence" per review —
        # doubled and given its own rounded/elevated container so it
        # reads immediately, not as a small favicon-sized afterthought.
        icon.set_pixel_size(38)
        icon_wrap.append(icon)
        brand_row.append(icon_wrap)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title_lbl = Gtk.Label(label="M.G Linux Toolbox", xalign=0, wrap=True)
        title_lbl.add_css_class("mgv2-sidebar-title")
        self._subtitle_lbl = Gtk.Label(label=T("sidebar_subtitle"), xalign=0)
        self._subtitle_lbl.add_css_class("mgv2-sidebar-subtitle")
        title_box.append(title_lbl)
        title_box.append(self._subtitle_lbl)
        brand_row.append(title_box)
        self._title_box = title_box

        brand_btn = Gtk.Button(valign=Gtk.Align.CENTER)
        brand_btn.add_css_class("mgv2-sidebar-brand-row")
        brand_btn.add_css_class("mgv2-sidebar-brand-btn")
        brand_btn.set_child(brand_row)
        brand_btn.set_tooltip_text(release_config.PROJECT_PAGE_URL)
        brand_btn.connect("clicked", lambda _b: open_external_url(release_config.PROJECT_PAGE_URL))
        self._brand_btn = brand_btn

        header.append(brand_btn)
        return header

    def _build_footer(self) -> Gtk.Widget:
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        footer.add_css_class("mgv2-sidebar-footer")
        self._footer_lbl = Gtk.Label(label=T("sidebar_footer"), xalign=0, wrap=True)
        self._footer_lbl.add_css_class("mgv2-sidebar-footer-label")
        footer.append(self._footer_lbl)
        self._footer_widget = footer
        return footer

    # ── groups / tiles ──────────────────────────────────────────
    def _build_group(self, title_key: str, items) -> Gtk.Widget:
        group_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        group_box.add_css_class("mgv2-sidebar-group")

        title_lbl = Gtk.Label(label=T(title_key), xalign=0)
        title_lbl.add_css_class("mgv2-sidebar-group-title")
        group_box.append(title_lbl)
        self._group_titles.append((title_lbl, title_key))

        for label_key, internal_name, icon_name in items:
            group_box.append(self._build_tile(label_key, internal_name, icon_name))

        return group_box

    def _build_tile(self, label_key: str, internal_name: str, icon_name: str) -> Gtk.Widget:
        tile = SidebarTile(icon_name, T(label_key))
        tile.connect("clicked", lambda _b, name=internal_name: self._navigate(name))

        self._tiles[internal_name] = tile
        self._tile_labels.append((tile, label_key))
        return tile

    def _navigate(self, internal_name: str):
        self._stack.set_visible_child_name(internal_name)
        if self._on_navigate is not None:
            self._on_navigate(internal_name)

    def _on_stack_changed(self, stack, _pspec):
        current = stack.get_visible_child_name()
        for name, tile in self._tiles.items():
            tile.set_active(name == current)

    # ── i18n live refresh ────────────────────────────────────────
    def _refresh_labels(self):
        self._subtitle_lbl.set_text(T("sidebar_subtitle"))
        self._footer_lbl.set_text(T("sidebar_footer"))
        for lbl, key in self._group_titles:
            lbl.set_text(T(key))
        for tile, key in self._tile_labels:
            tile.set_label_text(T(key))

    # ── responsive compact mode (icon-only, used by a breakpoint) ──
    def collapsible_label_widgets(self):
        """Every text widget that should disappear in compact mode."""
        widgets = [self._subtitle_lbl, self._footer_widget]
        widgets += [lbl for lbl, _ in self._group_titles]
        widgets += [tile.label_widget for tile, _ in self._tile_labels]
        return widgets
