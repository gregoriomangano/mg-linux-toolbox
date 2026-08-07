import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib
import os
import core.i18n as i18n
from core.i18n import T, on_change
from core import release_config
from core.uri_launcher import open_external_url
from core.version import APP_VERSION
from core.updater.startup import write_update_confirmation

from ui.sidebar import Sidebar

from ui.pages.page_overview    import OverviewPage
from ui.pages.page_disk_activity import DiskActivityPage
from ui.pages.page_network     import NetworkPage
from ui.pages.page_system      import SystemPage
from ui.pages.page_performance import PerformancePage
from ui.pages.page_software_repos import SoftwareRepositoriesPage
from ui.pages.page_gaming      import GamingPage
from ui.pages.page_audio       import AudioPage
from ui.pages.page_virt        import VirtPage
from ui.pages.page_security    import SecurityPage
from ui.pages.page_printers    import PrintersPage
from ui.pages.page_services    import ServicesPage
from ui.pages.page_kernel      import KernelPage
from ui.pages.page_history     import HistoryPage
from ui.pages.page_author      import AuthorPage
from ui.pages.page_guide       import GuidePage
from ui.pages.page_credits     import CreditsPage
from ui.pages.page_donate      import DonatePage
from ui.pages.page_about       import AboutWindow
from ui.pages.page_help_support import HelpSupportPage


LANGS = [
    ("🇮🇹  Italiano", "it"),
    ("🇬🇧  English",  "en"),
    ("🇪🇸  Español",  "es"),
    ("🇫🇷  Français", "fr"),
]

# Pages: (name_key, page_class, internal_name, icon_name).
# "info" uses the richer OverviewPage built on the existing data helpers.
PAGES = [
    ("sysinfo_tab",     OverviewPage,    "info",        "go-home-symbolic"),
    ("tab_kernel",      KernelPage,      "kernel",      "emblem-system-symbolic"),
    ("tab_network",     NetworkPage,     "network",     "network-wireless-symbolic"),
    ("tab_system",      SystemPage,      "system",      "drive-harddisk-symbolic"),
    ("tab_performance", PerformancePage, "performance", "battery-good-symbolic"),
    ("tab_software_repos", SoftwareRepositoriesPage, "software_repos", "system-software-install-symbolic"),
    ("tab_gaming",      GamingPage,      "gaming",      "input-gaming-symbolic"),
    ("tab_audio",       AudioPage,       "audio",       "audio-speakers-symbolic"),
    ("tab_printers",    PrintersPage,    "printers",    "printer-symbolic"),
    ("tab_virt",        VirtPage,        "virt",        "computer-symbolic"),
    ("tab_security",    SecurityPage,    "security",    "security-high-symbolic"),
    ("tab_services",    ServicesPage,    "services",    "system-run-symbolic"),
    ("tab_history",     HistoryPage,     "history",     "document-open-recent-symbolic"),
    ("tab_author",      AuthorPage,      "author",      "avatar-default-symbolic"),
    ("tab_guide",       GuidePage,       "guide",       "help-faq-symbolic"),
    ("tab_credits",     CreditsPage,     "credits",     "starred-symbolic"),
    ("tab_donate",      DonatePage,      "donate",      "emblem-favorite-symbolic"),
    ("tab_help_support", HelpSupportPage, "help_support", "system-help-symbolic"),
    # 2026-08-03: reached only from the Panoramica's Disco card ("Apri
    # Attività del disco"), never as its own sidebar entry — see
    # HIDDEN_FROM_SWITCHER below, same pattern as author/guide/credits/donate.
    ("da_open_title",   DiskActivityPage, "disk_activity", "drive-harddisk-symbolic"),
]

# These five live in the same ViewStack as every operational page (so
# switch_to_page() keeps working for internal cross-links), but are
# hidden from both the sidebar and the top-bar's general-purpose nav
# buttons list — reached only from the header's compact buttons or
# from in-page links (Chi sono -> Crediti/Guida/Supporta, Informazioni
# -> Crediti; Panoramica -> Attività del disco), never as a sidebar
# entry among the 11 operational pages.
HIDDEN_FROM_SWITCHER = {"author", "guide", "credits", "donate", "disk_activity", "help_support"}

# (icon_name, i18n_title_key, target_internal_page) — Guida | Chi sono |
# Supporta. "Contatti" is built separately below (opens the real
# release_config.CONTACT_URL instead of switching pages).
_CENTER_NAV_ITEMS = [
    ("help-faq-symbolic", "tab_guide", "guide"),
    ("avatar-default-symbolic", "tab_author", "author"),
    ("emblem-favorite-symbolic", "tab_donate", "donate"),
]

# Below this window width the topbar's nav buttons drop their text
# label and show icon-only (with a tooltip).
_NAV_COMPACT_BREAKPOINT = "max-width: 700sp"

# Below this width the sidebar collapses to an icon-only rail — chosen
# wide enough that the sidebar never fights the content area for space
# on a typical laptop screen.
_SIDEBAR_COMPACT_BREAKPOINT = "max-width: 860sp"

# Optional compact bottom navigation.
USE_LEGACY_BOTTOM_BAR = False

i18n._strings.setdefault("nav_contact", {
    "en": "Contact", "it": "Contatti", "es": "Contacto", "fr": "Contact",
})


class LinuxToolboxWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_default_size(1180, 780)
        # Adw.Breakpoint (used below for the compact header nav buttons
        # and the compact sidebar) needs an explicit minimum size to
        # know the valid range it's watching within.
        self.set_size_request(360, 400)
        self.set_title("M.G Linux Toolbox")

        # ── Load CSS ──────────────────────────────────────────────
        self._css_provider = Gtk.CssProvider()
        css_path = os.path.join(os.path.dirname(__file__), "style.css")
        try:
            self._css_provider.load_from_path(css_path)
        except Exception:
            pass
        self.get_display()  # ensure display is ready
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            self._css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # ── Window icon (MG monogram) ──────────────────────────────
        # Registered only as a runtime icon-theme search path for this
        # process — never copied into a system icon theme directory,
        # never touching the official app's installed icon. This is
        # what taskbar/alt-tab show for this window.
        icon_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..",
            "assets", "branding"
        )
        if os.path.isdir(icon_dir):
            Gtk.IconTheme.get_for_display(self.get_display()).add_search_path(icon_dir)
            self.set_icon_name("mg-icon-simple")

        # ── Outer structure ───────────────────────────────────────
        # Adw.ToolbarView (not a plain Gtk.Box) so libadwaita paints a
        # proper opaque background for the content area — without it,
        # some Wayland compositors (e.g. COSMIC) blur the desktop
        # wallpaper through instead of showing our dark theme.
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_css_class("mg-shell")
        toolbar_view.add_css_class("mgv2-shell")
        self.set_content(toolbar_view)

        # ── Top bar: general functions only, no page tabs ─────────
        header = Adw.HeaderBar()
        header.add_css_class("mg-topbar")
        header.add_css_class("mgv2-topbar")
        header.set_show_end_title_buttons(True)

        # v3: no custom brand button here anymore — the sidebar header
        # (icon + "M.G Linux Toolbox" + subtitle) is now the single
        # in-app identity block, and it's clickable itself (see
        # ui/sidebar.py). Keeping a second clickable "M.G Linux Toolbox"
        # in the top-left was the "doppione" the v3 review flagged.
        # The window's native title/controls (from set_show_end_title_
        # buttons above) are untouched — this only removes our own
        # custom widget, never the window manager's own decorations.

        # ── Center: general-purpose nav buttons only ───────────────
        # Guida | Chi sono | Contatti | Supporta — the 11 operational
        # pages live only in the sidebar now, so this row never
        # duplicates a sidebar entry.
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        nav_box.set_halign(Gtk.Align.CENTER)
        self._nav_buttons = []  # (button, label_widget, i18n_key)
        for icon_name, label_key, target in _CENTER_NAV_ITEMS:
            btn, label = self._build_nav_button(icon_name, label_key, target)
            nav_box.append(btn)
            self._nav_buttons.append((btn, label, label_key))

        contact_btn, contact_label = self._build_contact_button()
        nav_box.append(contact_btn)
        self._nav_buttons.append((contact_btn, contact_label, "nav_contact"))

        # "Aiuto e supporto" — the direct line to Gregorio, deliberately
        # a touch more visible than the other four (soft blue tint via
        # mgv2-topbar-nav-btn-highlight, see style.css) but never a loud
        # ad: same button shape, same size, just a gentle accent.
        help_btn, help_label = self._build_nav_button("system-help-symbolic", "tab_help_support", "help_support")
        help_btn.add_css_class("mgv2-topbar-nav-btn-highlight")
        nav_box.append(help_btn)
        self._nav_buttons.append((help_btn, help_label, "tab_help_support"))

        header.set_title_widget(nav_box)

        # Below _NAV_COMPACT_BREAKPOINT, hide just the text labels —
        # icons + tooltips remain, and the buttons never relocate.
        self._nav_breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(_NAV_COMPACT_BREAKPOINT))
        for _btn, label, _key in self._nav_buttons:
            self._nav_breakpoint.add_setter(label, "visible", False)
        self.add_breakpoint(self._nav_breakpoint)

        # Language dropdown — always visible in header
        lang_names = Gtk.StringList.new([l[0] for l in LANGS])
        self._lang_dd = Gtk.DropDown(model=lang_names, valign=Gtk.Align.CENTER)
        self._lang_dd.set_tooltip_text("Cambia lingua / Change language")
        default_idx = next((i for i, (_, code) in enumerate(LANGS)
                            if code == i18n._lang), 0)
        self._lang_dd.set_selected(default_idx)
        self._lang_dd.connect("notify::selected", self._on_lang_changed)
        header.pack_end(self._lang_dd)

        # "Informazioni" — opens the About window (version, updates,
        # disclaimer, privacy, license). Unchanged from the original.
        about_btn = Gtk.Button(icon_name="help-about-symbolic", valign=Gtk.Align.CENTER)
        about_btn.set_tooltip_text(T("about_title"))
        about_btn.connect("clicked", self._on_about_clicked)
        header.pack_end(about_btn)

        toolbar_view.add_top_bar(header)

        # ── ViewStack (pages) — same stack, same pages, unchanged ──
        self._stack = Adw.ViewStack()
        self._stack.set_hexpand(True)
        self._stack.set_vexpand(True)
        self._pages = {}
        for key, PageClass, internal, icon in PAGES:
            if PageClass in (OverviewPage, DiskActivityPage):
                page_widget = PageClass(navigate_callback=self.switch_to_page)
            else:
                page_widget = PageClass()
            self._stack.add_titled_with_icon(page_widget, internal, T(key), icon)
            self._pages[internal] = (key, page_widget)
            if internal in HIDDEN_FROM_SWITCHER:
                # Stays fully reachable via switch_to_page() — just
                # invisible to the sidebar.
                self._stack.get_page(page_widget).set_visible(False)

        # ── Body: sidebar (new) + content, side by side ────────────
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)

        self._sidebar = Sidebar(self._stack)
        body.append(self._sidebar)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        body.append(self._stack)

        toolbar_view.set_content(body)

        # Below _SIDEBAR_COMPACT_BREAKPOINT, the sidebar shrinks to an
        # icon-only rail: labels hidden, width reduced — restored
        # automatically by Adw.Breakpoint once the window widens again.
        self._sidebar_breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse(_SIDEBAR_COMPACT_BREAKPOINT))
        self._sidebar_breakpoint.add_setter(self._sidebar, "width-request", 68)
        for widget in self._sidebar.collapsible_label_widgets():
            self._sidebar_breakpoint.add_setter(widget, "visible", False)
        self.add_breakpoint(self._sidebar_breakpoint)

        # Dashboard grid responsiveness: the Panoramica's FlowBoxes
        # default to their own "wide" column count (set in
        # page_overview.py); these two breakpoints step them down as
        # the window narrows, restored automatically once it widens
        # again — no card ever overlaps or gets squeezed illegibly.
        overview_page = self._pages["info"][1]
        medium_condition = Adw.BreakpointCondition.new_and(
            Adw.BreakpointCondition.parse("min-width: 900sp"),
            Adw.BreakpointCondition.parse("max-width: 1399sp"),
        )
        self._grid_medium_breakpoint = Adw.Breakpoint.new(medium_condition)
        narrow_condition = Adw.BreakpointCondition.parse("max-width: 899sp")
        self._grid_narrow_breakpoint = Adw.Breakpoint.new(narrow_condition)
        for flow in overview_page.responsive_flowboxes():
            self._grid_medium_breakpoint.add_setter(flow, "max-children-per-line", 2)
            self._grid_narrow_breakpoint.add_setter(flow, "max-children-per-line", 1)
        self.add_breakpoint(self._grid_medium_breakpoint)
        self.add_breakpoint(self._grid_narrow_breakpoint)

        # Bottom-tab navigation used by the compact layout.
        if USE_LEGACY_BOTTOM_BAR:
            vswitcher_bar = Adw.ViewSwitcherBar()
            vswitcher_bar.set_stack(self._stack)
            vswitcher_bar.set_reveal(True)
            toolbar_view.add_bottom_bar(vswitcher_bar)

        on_change(self._refresh_titles)
        on_change(self._refresh_nav_labels)

    def _build_nav_button(self, icon_name: str, label_key: str, target: str):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(Gtk.Image.new_from_icon_name(icon_name))
        label = Gtk.Label(label=T(label_key))
        box.append(label)
        btn = Gtk.Button(valign=Gtk.Align.CENTER)
        btn.add_css_class("mg-topbar-nav-btn")
        btn.add_css_class("mgv2-topbar-nav-btn")
        btn.set_child(box)
        btn.set_tooltip_text(T(label_key))
        btn.connect("clicked", lambda _b: self.switch_to_page(target))
        return btn, label

    def _build_contact_button(self):
        """Contatti: opens the project's real, already-published contact
        page via the same https-only launcher as the brand button."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(Gtk.Image.new_from_icon_name("mail-send-symbolic"))
        label = Gtk.Label(label=T("nav_contact"))
        box.append(label)
        btn = Gtk.Button(valign=Gtk.Align.CENTER)
        btn.add_css_class("mg-topbar-nav-btn")
        btn.add_css_class("mgv2-topbar-nav-btn")
        btn.set_child(box)
        btn.set_tooltip_text(release_config.CONTACT_URL)
        btn.connect("clicked", lambda _b: open_external_url(release_config.CONTACT_URL))
        return btn, label

    def _refresh_nav_labels(self):
        for btn, label, key in self._nav_buttons:
            label.set_text(T(key))
            btn.set_tooltip_text(T(key))

    def _on_lang_changed(self, dd, _):
        _, code = LANGS[dd.get_selected()]
        i18n.set_lang(code)

    def _on_about_clicked(self, _btn):
        AboutWindow(parent=self).present()

    def switch_to_page(self, internal_name: str):
        """Internal navigation used by the sidebar, the header's Guida/
        Chi sono/Supporta buttons, and in-page cross-links (Chi sono ->
        Crediti/Guida/Supporta, Informazioni -> Crediti) — never a new
        window, never a browser, just changes the visible child of the
        same ViewStack every page already lives in, including the four
        hidden from the sidebar."""
        self._stack.set_visible_child_name(internal_name)

    def _refresh_titles(self):
        for internal, (key, page_widget) in self._pages.items():
            pg = self._stack.get_child_by_name(internal)
            if pg:
                self._stack.get_page(pg).set_title(T(key))


class LinuxToolboxApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="io.github.linuxtoolbox",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_quit)
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def _on_quit(self, _action, _parameter):
        self.quit()

    def do_activate(self):
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        win = self.props.active_window
        if not win:
            win = LinuxToolboxWindow(application=self)
        win.present()
        write_update_confirmation(APP_VERSION)
        if os.environ.pop("MG_TOOLBOX_UPDATE_ROLLBACK", "") == "1":
            GLib.idle_add(self._show_update_rollback_notice, win)

    def _show_update_rollback_notice(self, win):
        log_path = os.environ.pop("MG_TOOLBOX_UPDATE_ROLLBACK_LOG", "")
        body = "L'aggiornamento non è stato completato. È stata ripristinata la versione precedente."
        if log_path:
            body += f"\n\nDettagli tecnici disponibili in:\n{log_path}"
        dialog = Adw.MessageDialog(transient_for=win,
                                   heading="Aggiornamento non riuscito",
                                   body=body)
        dialog.add_response("close", "Chiudi")
        dialog.present()
        return False
