"""
Homepage ("Panoramica") built on real data read by page_info.py's helpers and by the
existing KernelFeature backend — no new system access, no invented
numbers. The original page_info.py / InfoPage is untouched.

v2 change from v1: this page no longer extends Adw.PreferencesPage.
That base class wraps its content in a fixed-width Adw.Clamp
(~600-750px) regardless of window size, which was the real cause of
v1's "colonna centrale troppo stretta" — not a CSS issue. v2 is a
plain Gtk.ScrolledWindow with its own much wider Adw.Clamp, so the
dashboard actually uses the window's width.
"""
import os

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from core.i18n import T, on_change
from core import i18n as _i18n_mod

from ui.pages.page_info import (
    _get_distro, _get_kernel, _get_uptime,
    _get_cpu_usage, _get_cpu_cores, _get_ram_info, _get_swap_info,
    _get_mount_usage, _get_disks, _is_removable,
)

from core.kernel_features.base import SupportStatus
from core.kernel_features.monitoring import PSIFeature, PSIHysteresis, PSI_REFRESH_SECONDS
from core.kernel_features.storage import list_real_disks
from ui.kernel.inventory import count_kernel_inventory

from ui.dashboard.dashboard_card import DashboardCard
from ui.dashboard.radial_gauge import RadialGauge, COLOR_CPU, COLOR_RAM, COLOR_DISK, COLOR_SWAP
from ui.dashboard.quick_action import QuickAction

_overview_strings = {
    "ov2_welcome_title":  {"en": "Welcome to M.G Linux Toolbox", "it": "Benvenuto in M.G Linux Toolbox", "es": "Bienvenido a M.G Linux Toolbox", "fr": "Bienvenue dans M.G Linux Toolbox"},
    "ov2_welcome_tagline": {"en": "Check, understand and manage your Linux system.", "it": "Controlla, comprendi e gestisci il tuo sistema Linux.", "es": "Controla, comprende y gestiona tu sistema Linux.", "fr": "Vérifiez, comprenez et gérez votre système Linux."},
    "ov2_state_low":      {"en": "Low load", "it": "Basso carico", "es": "Carga baja", "fr": "Charge faible"},
    "ov2_state_moderate": {"en": "Attention", "it": "Attenzione", "es": "Atención", "fr": "Attention"},
    # 2026-08-03 PSI fix: this badge is driven exclusively by PSI (see
    # _overall_pressure_bucket) — "Check needed" read as a diagnosis
    # ("something is wrong, go look") for what is, in practice, always
    # just a resource being under temporary I/O/CPU/memory pressure.
    "ov2_state_high":     {"en": "A resource is under pressure", "it": "Una risorsa è temporaneamente sotto pressione", "es": "Un recurso está temporalmente bajo presión", "fr": "Une ressource est temporairement sous pression"},
    "ov2_fact_distro":    {"en": "Distribution", "it": "Distribuzione", "es": "Distribución", "fr": "Distribution"},
    "ov2_fact_kernel":    {"en": "Kernel", "it": "Kernel", "es": "Kernel", "fr": "Noyau"},
    "ov2_fact_uptime":    {"en": "Uptime", "it": "Tempo di attività", "es": "Tiempo activo", "fr": "Temps de fonctionnement"},

    "ov2_resources_title": {"en": "Resources", "it": "Risorse", "es": "Recursos", "fr": "Ressources"},
    "ov2_cpu_name":   {"en": "CPU", "it": "CPU", "es": "CPU", "fr": "CPU"},
    "ov2_ram_name":   {"en": "RAM", "it": "RAM", "es": "RAM", "fr": "RAM"},
    "ov2_disk_name":  {"en": "Disk", "it": "Disco", "es": "Disco", "fr": "Disque"},
    "ov2_swap_name":  {"en": "Swap", "it": "Swap", "es": "Swap", "fr": "Swap"},
    "ov2_cpu_caption": {"en": "Current usage", "it": "Uso attuale", "es": "Uso actual", "fr": "Utilisation actuelle"},
    "ov2_swap_available": {"en": "available", "it": "disponibili", "es": "disponibles", "fr": "disponibles"},

    "ov2_pressure_title":  {"en": "System pressure", "it": "Pressione del sistema", "es": "Presión del sistema", "fr": "Pression du système"},
    "ov2_pressure_unsupported": {"en": "Not available on this kernel.", "it": "Non disponibile su questo kernel.", "es": "No disponible en este kernel.", "fr": "Non disponible sur ce noyau."},
    "ov2_pressure_all_low": {"en": "The system is responsive, no significant slowdowns detected.", "it": "Il sistema è reattivo e non risultano rallentamenti significativi.", "es": "El sistema responde bien, no se detectan ralentizaciones significativas.", "fr": "Le système est réactif, aucun ralentissement significatif détecté."},
    "mg_psi_bucket_low":      {"en": "Low", "it": "Bassa", "es": "Baja", "fr": "Faible"},
    "mg_psi_bucket_moderate": {"en": "Moderate", "it": "Moderata", "es": "Moderada", "fr": "Modérée"},
    "mg_psi_bucket_high":     {"en": "High", "it": "Alta", "es": "Alta", "fr": "Élevée"},

    "ov2_kernel_card_title": {"en": "Kernel Functions", "it": "Funzioni kernel", "es": "Funciones del Kernel", "fr": "Fonctions du Noyau"},
    # V6: renamed per the read-only audit — the old labels ("Rilevate",
    # "Attive", "Non supportate") were ambiguous or actively misleading
    # (see ui/kernel/inventory.py docstring and the audit report).
    # "Attive" in particular read as "currently on in the kernel", when
    # the number has always been "how many FeatureRecord this app
    # itself saved and hasn't restored yet" — a different concept.
    "ov2_kernel_detected":   {"en": "Available functions", "it": "Funzioni disponibili", "es": "Funciones disponibles", "fr": "Fonctions disponibles"},
    "ov2_kernel_active":     {"en": "Modified by MG Toolbox", "it": "Modificate da MG Toolbox", "es": "Modificadas por MG Toolbox", "fr": "Modifiées par MG Toolbox"},
    "ov2_kernel_temporary":  {"en": "Temporary changes", "it": "Modifiche temporanee", "es": "Cambios temporales", "fr": "Modifications temporaires"},
    "ov2_kernel_permanent":  {"en": "Permanent changes", "it": "Modifiche permanenti", "es": "Cambios permanentes", "fr": "Modifications permanentes"},
    "ov2_kernel_unsupported": {"en": "Not available on this PC", "it": "Non disponibili su questo PC", "es": "No disponibles en este PC", "fr": "Non disponibles sur ce PC"},
    "ov2_open_kernel":       {"en": "Open Kernel Functions", "it": "Apri Funzioni kernel", "es": "Abrir Funciones del Kernel", "fr": "Ouvrir Fonctions du Noyau"},

    "ov2_disks_title":    {"en": "Disks", "it": "Dischi", "es": "Discos", "fr": "Disques"},
    "ov2_disks_capacity": {"en": "Capacity", "it": "Capacità", "es": "Capacidad", "fr": "Capacité"},
    "ov2_disks_not_mounted": {"en": "Not mounted — usage unknown", "it": "Non montato — utilizzo non noto", "es": "No montado — uso desconocido", "fr": "Non monté — utilisation inconnue"},
    # V7: shown under the used/capacity line whenever more than one
    # mounted filesystem on the same physical disk was summed together —
    # so the number is never read as "the one partition's usage" when
    # it's really several added up. See _summarize_physical_disks().
    "ov2_disks_multi_mount": {
        "en": "Sum of {n} mounted partitions — see System & Disk for details",
        "it": "Somma di {n} partizioni montate — dettagli in Sistema e disco",
        "es": "Suma de {n} particiones montadas — detalles en Sistema y Disco",
        "fr": "Somme de {n} partitions montées — détails dans Système et Disque",
    },
    # v3 audit fix: "Vedi tutti i dischi" read like it just expanded the
    # same card, when it actually navigates to a whole different page —
    # renamed to say exactly where it goes.
    "ov2_disks_open_system": {"en": "Open System & Disk", "it": "Apri Sistema e disco", "es": "Abrir Sistema y Disco", "fr": "Ouvrir Système et Disque"},
    "ov2_open_pressure": {"en": "Open System Pressure", "it": "Apri Pressione del sistema", "es": "Abrir Presión del sistema", "fr": "Ouvrir Pression du système"},
    "ov2_open_disk_activity": {"en": "Open Disk Activity", "it": "Apri Attività del disco", "es": "Abrir Actividad del disco", "fr": "Ouvrir Activité du disque"},

    "ov2_quick_title":    {"en": "Quick actions", "it": "Azioni rapide", "es": "Acciones rápidas", "fr": "Actions rapides"},
    "ov2_quick_kernel_t": {"en": "Kernel Functions", "it": "Funzioni kernel", "es": "Funciones del Kernel", "fr": "Fonctions du Noyau"},
    "ov2_quick_kernel_d": {"en": "Governor, swappiness, ZRAM and other kernel functions.", "it": "Governor, swappiness, ZRAM e altre funzioni del kernel.", "es": "Governor, swappiness, ZRAM y otras funciones del kernel.", "fr": "Governor, swappiness, ZRAM et autres fonctions du noyau."},
    "ov2_quick_system_t": {"en": "System & Disk", "it": "Sistema e disco", "es": "Sistema y Disco", "fr": "Système et Disque"},
    "ov2_quick_system_d": {"en": "Disk space, partitions and system tools.", "it": "Spazio su disco, partizioni e strumenti di sistema.", "es": "Espacio en disco, particiones y herramientas del sistema.", "fr": "Espace disque, partitions et outils système."},
    "ov2_quick_network_t": {"en": "Network & Security", "it": "Rete e sicurezza", "es": "Red y Seguridad", "fr": "Réseau et Sécurité"},
    "ov2_quick_network_d": {"en": "Network status and security settings.", "it": "Stato della rete e impostazioni di sicurezza.", "es": "Estado de la red y ajustes de seguridad.", "fr": "État du réseau et paramètres de sécurité."},
    "ov2_quick_history_t": {"en": "History and restore", "it": "Cronologia e ripristino", "es": "Historial y restauración", "fr": "Historique et restauration"},
    "ov2_quick_history_d": {"en": "Review applied changes and restore saved values.", "it": "Rivedi le modifiche applicate e ripristina i valori.", "es": "Revisa los cambios aplicados y restaura los valores.", "fr": "Passez en revue les modifications et restaurez les valeurs."},

    "ov2_admin_section_title": {"en": "System permissions", "it": "Permessi di sistema", "es": "Permisos del sistema", "fr": "Autorisations système"},
}
for _k, _v in _overview_strings.items():
    _i18n_mod._strings[_k] = _v

_CHIP_CSS = {"low": "mgv2-chip-low", "moderate": "mgv2-chip-moderate", "high": "mgv2-chip-high"}
_BADGE_CSS = {"low": "", "moderate": "moderate", "high": "high"}

# V6: the Home "Funzioni Kernel" card and the Kernel page's own header
# used to compute "rilevate/non supportate" from two different lists
# that could silently drift apart (confirmed by the read-only audit:
# the old list here never saw the 9 kernel-expansion-v1 functions, so
# the header kept saying "16" while the Kernel page really built 20
# cards). Both now read from the exact same inventory the Kernel page
# itself builds its rows from — see ui/kernel/inventory.py. Battery,
# audio, KSM and SELinux are deliberately NOT counted here anymore:
# they live on other pages, not on the Kernel page this card is about.


def _count_feature_state() -> tuple:
    """Returns (active, temporary, permanent) from the REAL rollback
    state store — a record only exists for a feature this app itself
    changed from its default. Never guessed: if the store is empty (no
    change ever applied), all three are honestly 0, never a placeholder.

    A record's mode is one of three real states written by
    core/priv_writer.py: "temporary", "persistent" (both still applied
    right now) or "restored" (core.priv_writer._note_applied(..., "restored")
    — the value was put back and the record is only kept as history, it
    is NOT a currently-active change). "Modificate da MG Toolbox" means
    changes MG Toolbox has applied and NOT yet restored, so a "restored"
    record must not inflate that count — active is deliberately
    temporary+permanent, not len(records)."""
    from core.persistence.rollback_store import default_state_store
    try:
        records = default_state_store().all()
    except Exception:
        return 0, 0, 0
    temporary = sum(1 for rec in records.values() if rec.mode == "temporary")
    permanent = sum(1 for rec in records.values() if rec.mode == "persistent")
    active = temporary + permanent
    return active, temporary, permanent


def _read_int(path: str, fallback: int = 0) -> int:
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return fallback


def _format_gb(value: float) -> str:
    """A GB figure -> its locale-appropriate text (comma for it/es/fr,
    dot for en) — same convention already used for GHz on the Kernel
    page (see page_kernel._format_freq_mhz). The underlying float is
    never touched, only how it's displayed."""
    text = f"{value:.1f}"
    if _i18n_mod._lang in ("it", "es", "fr"):
        text = text.replace(".", ",")
    return text


def _summarize_physical_disks(max_count: int = 3):
    """Real whole-disk summaries (not one row per partition): capacity
    from /sys/block/<dev>/size, "used" is the sum of *mounted*
    partitions on that disk (from the same real reads page_info.py
    uses) — never a guessed number for an unmounted partition, and
    never a fabricated disk-health percentage."""
    partitions = _get_disks()
    disks = []
    for device_id, friendly_name in list_real_disks():
        sectors = _read_int(f"/sys/block/{device_id}/size", 0)
        size_gb = round(sectors * 512 / (1024 ** 3), 1)
        if size_gb < 0.1:
            continue
        used_gb = 0.0
        any_mounted = False
        mounted_count = 0
        for name, _psize, mount, _fstype, _removable in partitions:
            if name.startswith(device_id) and mount != "—":
                _p_total, p_used, _p_pct = _get_mount_usage(mount)
                used_gb += p_used
                any_mounted = True
                mounted_count += 1
        kind = friendly_name.split(" ", 1)[0] if friendly_name else ""
        removable = _is_removable(device_id)
        disks.append({
            "device_id": device_id, "friendly_name": friendly_name, "kind": kind,
            "size_gb": size_gb, "used_gb": round(used_gb, 1),
            "any_mounted": any_mounted, "removable": removable,
            "mounted_count": mounted_count,
        })
    disks.sort(key=lambda d: d["size_gb"], reverse=True)
    return disks[:max_count], len(disks)


class OverviewPage(Gtk.ScrolledWindow):
    """v2 Panoramica: a plain scrolled area (no Adw.PreferencesPage, so
    no hidden narrow clamp) holding a wide Adw.Clamp with the real
    dashboard grid inside."""

    def __init__(self, navigate_callback=None):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._navigate = navigate_callback
        self._responsive_flowboxes = []

        # 2026-08-03 PSI fix: the badge and pressure card used to be
        # computed exactly once, right here in __init__, and never
        # again — this page lives permanently inside the window's
        # Adw.ViewStack (see ui/window.py, pages are built once and
        # only shown/hidden via set_visible_child_name), so a value
        # captured during a real spike stayed on screen forever, long
        # after the spike had passed. Same PSIHysteresis + map/unmap
        # polling pattern as the Kernel page's PSIRow, so both places
        # agree and neither risks a duplicate timer.
        self._psi_feature = PSIFeature()
        self._psi_supported = self._psi_feature.probe() == SupportStatus.SUPPORTED_READ_ONLY
        self._psi_hysteresis = {r: PSIHysteresis() for r in ("cpu", "memory", "io")}
        self._psi_timeout_id = None
        self._psi_chip_labels = {}
        self._psi_phrase_labels = {}
        self._psi_indicators = {}
        self._psi_lead_label = None

        clamp = Adw.Clamp(maximum_size=1600, tightening_threshold=900)
        clamp.set_margin_top(22)
        clamp.set_margin_bottom(32)
        clamp.set_margin_start(22)
        clamp.set_margin_end(22)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        content.append(self._build_welcome_block())
        content.append(self._build_resources_block())
        content.append(self._build_pressure_block())
        content.append(self._build_kernel_block())
        content.append(self._build_disks_block())
        content.append(self._build_quick_actions_block())
        content.append(self._build_admin_component_block())

        clamp.set_child(content)
        self.set_child(clamp)

        if self._psi_supported:
            self._refresh_psi()  # first paint uses the same live path as later refreshes
            self.connect("map", self._on_psi_map)
            self.connect("unmap", self._on_psi_unmap)
            self.connect("destroy", self._on_psi_unmap)

    def responsive_flowboxes(self):
        """FlowBoxes whose column count a window-level Adw.Breakpoint
        should adjust for medium/narrow widths."""
        return self._responsive_flowboxes

    def _navigate_to(self, target: str):
        if self._navigate is not None:
            self._navigate(target)

    # ── PSI live refresh (badge + pressure card) ────────────────
    def _on_psi_map(self, _w):
        # Page became visible: (re)start polling. Guarded so switching
        # tabs back and forth can't ever stack a second timer.
        if self._psi_timeout_id is None:
            self._psi_timeout_id = GLib.timeout_add_seconds(PSI_REFRESH_SECONDS, self._on_psi_timeout)

    def _on_psi_unmap(self, _w):
        # Page hidden (another tab selected): stop polling /proc
        # entirely until it's shown again.
        if self._psi_timeout_id is not None:
            GLib.source_remove(self._psi_timeout_id)
            self._psi_timeout_id = None
        for tracker in self._psi_hysteresis.values():
            tracker.reset_pending()

    def _on_psi_timeout(self):
        self._refresh_psi()
        return True  # keep the timer running

    def _refresh_psi(self):
        """Single read of /proc/pressure/*, run through each resource's
        hysteresis tracker once, then apply the result to both the
        header badge and the pressure card — one source of truth per
        tick, so they can never disagree."""
        result = self._psi_feature.read_current()
        if not result.ok:
            for tracker in self._psi_hysteresis.values():
                tracker.reset_pending()
            return
        buckets = {}
        for resource in ("cpu", "memory", "io"):
            data = result.value.get(resource, {})
            some = data.get("some", {})
            buckets[resource] = self._psi_hysteresis[resource].update(
                some.get("avg10", 0.0), some.get("avg60", 0.0)
            )
        order = {"low": 0, "moderate": 1, "high": 2}
        worst = max(buckets.values(), key=lambda b: order.get(b, 0))
        self._apply_state_badge(worst)
        self._apply_pressure_card(buckets)

    # ── Block 1: welcome / general state ────────────────────────
    def _build_welcome_block(self) -> Gtk.Widget:
        hero = DashboardCard(level=1, spacing=14)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)

        icon_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..",
            "assets", "branding", "mg-icon-64.png"
        )
        icon_wrap = Gtk.Box()
        icon_wrap.add_css_class("mgv2-hero-icon-wrap")
        icon = Gtk.Image.new_from_file(icon_path) if os.path.isfile(icon_path) \
            else Gtk.Image.new_from_icon_name("go-home-symbolic")
        icon.set_pixel_size(40)
        icon_wrap.append(icon)
        top.append(icon_wrap)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title_box.set_valign(Gtk.Align.CENTER)
        title = Gtk.Label(label=T("ov2_welcome_title"), xalign=0, wrap=True)
        title.add_css_class("mgv2-hero-title")
        tagline = Gtk.Label(label=T("ov2_welcome_tagline"), xalign=0, wrap=True)
        tagline.add_css_class("mgv2-hero-tagline")
        title_box.append(title)
        title_box.append(tagline)
        top.append(title_box)

        self._state_badge = Gtk.Label(valign=Gtk.Align.CENTER)
        self._state_badge.add_css_class("mgv2-hero-badge")
        top.append(self._state_badge)
        hero.append(top)

        facts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._fact_distro = self._make_fact_chip("ov2_fact_distro", _get_distro())
        self._fact_kernel = self._make_fact_chip("ov2_fact_kernel", _get_kernel())
        self._fact_uptime = self._make_fact_chip("ov2_fact_uptime", _get_uptime())
        facts.append(self._fact_distro)
        facts.append(self._fact_kernel)
        facts.append(self._fact_uptime)
        hero.append(facts)

        self._refresh_state_badge()
        return hero

    def _make_fact_chip(self, label_key: str, value: str) -> Gtk.Widget:
        chip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        chip.add_css_class("mgv2-hero-fact")
        k = Gtk.Label(label=T(label_key), xalign=0)
        k.add_css_class("mgv2-hero-fact-key")
        v = Gtk.Label(label=value, xalign=0, wrap=True)
        v.add_css_class("mgv2-hero-fact-value")
        chip.append(k)
        chip.append(v)
        return chip

    def _refresh_state_badge(self):
        """One-off initial paint, called from __init__ while the rest
        of the page is still being built. If PSI is supported this
        gets immediately superseded by _refresh_psi()/_apply_state_badge()
        once construction finishes and the live/hysteresis path takes
        over; it only stays authoritative when PSI isn't readable at
        all on this kernel."""
        if not self._psi_supported:
            self._state_badge.set_text("—")
            self._state_badge.remove_css_class("moderate")
            self._state_badge.remove_css_class("high")
            return
        self._apply_state_badge("low")

    def _apply_state_badge(self, bucket: str):
        self._state_badge.set_text(T(f"ov2_state_{bucket}"))
        self._state_badge.remove_css_class("moderate")
        self._state_badge.remove_css_class("high")
        css = _BADGE_CSS.get(bucket, "")
        if css:
            self._state_badge.add_css_class(css)

    # ── Block 2: resources — radial gauges ──────────────────────
    def _build_resources_block(self) -> Gtk.Widget:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        section.append(self._section_label(T("ov2_resources_title")))

        cpu_pct = _get_cpu_usage()
        phys, logical = _get_cpu_cores()
        ram_total, ram_used, ram_pct = _get_ram_info()
        swap_total, swap_used = _get_swap_info()
        root_total, root_used, root_pct = _get_mount_usage("/")

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(1)
        flow.set_column_spacing(16)
        flow.set_row_spacing(16)
        flow.set_homogeneous(True)
        self._responsive_flowboxes.append(flow)

        flow.insert(self._gauge_card("💻", "ov2_cpu_name", COLOR_CPU, cpu_pct / 100,
                                      f"{cpu_pct}%", T("ov2_cpu_caption"),
                                      f"{phys} core / {logical} thread"), -1)
        flow.insert(self._gauge_card("🧠", "ov2_ram_name", COLOR_RAM, ram_pct / 100,
                                      f"{ram_pct}%", T("ov2_ram_name"),
                                      f"{ram_used} / {ram_total} GB"), -1)
        if root_total > 0:
            # 2026-08-03: the Disco card is now the click target that
            # opens the new "Attività del disco" page — an explicit
            # labeled button, not a silently-clickable whole card, per
            # this same file's earlier v3 audit note about the PSI
            # sub-cards ("...now pure read-only info, with one single
            # explicit button that says exactly where it goes").
            flow.insert(self._gauge_card("💽", "ov2_disk_name", COLOR_DISK, root_pct / 100,
                                          f"{root_pct}%", T("ov2_disk_name"),
                                          f"{root_used} / {root_total} GB",
                                          open_action_key="ov2_open_disk_activity",
                                          open_action_target="disk_activity"), -1)
        if swap_total > 0:
            swap_pct = round(swap_used / swap_total * 100, 1) if swap_total else 0
            flow.insert(self._gauge_card("🔄", "ov2_swap_name", COLOR_SWAP, swap_pct / 100,
                                          f"{swap_used} GB", T("ov2_swap_name"),
                                          f"{swap_total} GB {T('ov2_swap_available')}"), -1)

        section.append(flow)
        return section

    def _gauge_card(self, emoji: str, name_key: str, color, fraction: float,
                     center_value: str, center_caption: str, detail_text: str,
                     open_action_key: str = None, open_action_target: str = None) -> Gtk.Widget:
        card = DashboardCard(level=2, spacing=6)
        card.set_halign(Gtk.Align.FILL)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        head.set_halign(Gtk.Align.CENTER)
        emoji_lbl = Gtk.Label(label=emoji)
        emoji_lbl.add_css_class("mgv2-gauge-emoji")
        name_lbl = Gtk.Label(label=T(name_key))
        name_lbl.add_css_class("mgv2-gauge-name")
        head.append(emoji_lbl)
        head.append(name_lbl)
        card.append(head)

        gauge = RadialGauge(diameter=104, thickness=9, arc_rgba=color)
        gauge.set_fraction(fraction)
        gauge.set_center_text(center_value, center_caption)
        gauge.set_halign(Gtk.Align.CENTER)
        card.append(gauge)

        detail = Gtk.Label(label=detail_text, xalign=0.5, halign=Gtk.Align.CENTER, wrap=True)
        detail.add_css_class("mgv2-gauge-detail")
        card.append(detail)

        if open_action_key and open_action_target:
            open_btn = Gtk.Button(label=T(open_action_key))
            open_btn.add_css_class("mgv2-card-action-btn-flat")
            open_btn.set_halign(Gtk.Align.CENTER)
            open_btn.connect("clicked", lambda _b, target=open_action_target: self._navigate_to(target))
            card.append(open_btn)

        return card

    # ── Block 3: PSI pressure ────────────────────────────────────
    def _build_pressure_block(self) -> Gtk.Widget:
        card = DashboardCard(level=2, spacing=12)
        card.add_header(T("ov2_pressure_title"), icon_name="emblem-system-symbolic")

        if not self._psi_supported:
            note = Gtk.Label(label=T("ov2_pressure_unsupported"), xalign=0, wrap=True)
            note.add_css_class("mgv2-card-note")
            card.append(note)
            return card

        # Built once with placeholder ("low") content; _refresh_psi()
        # (called right after the page finishes constructing, and then
        # every PSI_REFRESH_SECONDS while visible) fills in the real
        # values via _apply_pressure_card() — same
        # build-once/update-in-place shape as the Kernel page's PSIRow,
        # so nothing here ever gets rebuilt from scratch on a timer.
        self._psi_lead_label = Gtk.Label(label=T("ov2_pressure_all_low"), xalign=0, wrap=True)
        self._psi_lead_label.add_css_class("mgv2-card-note")
        card.append(self._psi_lead_label)

        sub_flow = Gtk.FlowBox()
        sub_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        sub_flow.set_max_children_per_line(3)
        sub_flow.set_min_children_per_line(1)
        sub_flow.set_column_spacing(12)
        sub_flow.set_row_spacing(12)
        sub_flow.set_homogeneous(True)
        self._responsive_flowboxes.append(sub_flow)

        icons = {"cpu": "🧮", "memory": "🧠", "io": "💽"}
        for resource in ("cpu", "memory", "io"):
            sub_flow.insert(self._psi_subcard(resource, "low", icons[resource]), -1)

        card.append(sub_flow)

        # v3 audit fix: the three sub-cards used to be individually
        # clickable, all pointing at the same generic "kernel" page —
        # that looked like three distinct destinations. They're now
        # pure read-only info, with one single explicit button that
        # says exactly where it goes.
        open_btn = Gtk.Button(label=T("ov2_open_pressure"))
        open_btn.add_css_class("mgv2-card-action-btn-flat")
        open_btn.set_halign(Gtk.Align.START)
        open_btn.connect("clicked", lambda _b: self._navigate_to("kernel"))
        card.append(open_btn)

        return card

    def _psi_subcard(self, resource: str, bucket: str, emoji: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("mgv2-card-sub")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        emoji_lbl = Gtk.Label(label=emoji)
        name_lbl = Gtk.Label(label=T(f"kf_psi_{resource}"), xalign=0, hexpand=True)
        name_lbl.add_css_class("mgv2-psi-sub-name")
        chip = Gtk.Label(label=T(f"mg_psi_bucket_{bucket}"))
        chip.add_css_class(_CHIP_CSS.get(bucket, "mgv2-chip-low"))
        head.append(emoji_lbl)
        head.append(name_lbl)
        head.append(chip)
        box.append(head)
        self._psi_chip_labels[resource] = chip

        phrase = Gtk.Label(label=T(f"kf_psi_{resource}_{bucket}"), xalign=0, wrap=True)
        phrase.add_css_class("mgv2-psi-sub-phrase")
        box.append(phrase)
        self._psi_phrase_labels[resource] = phrase

        indicator = Gtk.ProgressBar()
        fraction = {"low": 0.18, "moderate": 0.55, "high": 0.95}.get(bucket, 0.18)
        indicator.set_fraction(fraction)
        indicator.add_css_class("mgv2-psi-indicator")
        indicator.add_css_class(f"mgv2-psi-indicator-{bucket}")
        box.append(indicator)
        self._psi_indicators[resource] = indicator

        return box

    def _apply_pressure_card(self, buckets: dict):
        """Update the already-built pressure card in place (labels,
        chip/indicator CSS classes) — never rebuilds widgets, so it's
        safe to call every PSI_REFRESH_SECONDS."""
        order = {"low": 0, "moderate": 1, "high": 2}
        worst = max(buckets.values(), key=lambda b: order.get(b, 0))
        self._psi_lead_label.set_text(
            T("ov2_pressure_all_low") if worst == "low" else T("kf_psi_desc")
        )

        for resource, bucket in buckets.items():
            chip = self._psi_chip_labels[resource]
            for css in _CHIP_CSS.values():
                chip.remove_css_class(css)
            chip.add_css_class(_CHIP_CSS.get(bucket, "mgv2-chip-low"))
            chip.set_text(T(f"mg_psi_bucket_{bucket}"))

            # kf_psi_io_high already names the resource ("Attesa del
            # disco elevata") — standalone here is exactly right, same
            # reasoning as PSIRow._refresh_once for the Kernel page.
            self._psi_phrase_labels[resource].set_text(T(f"kf_psi_{resource}_{bucket}"))

            indicator = self._psi_indicators[resource]
            indicator.set_fraction({"low": 0.18, "moderate": 0.55, "high": 0.95}.get(bucket, 0.18))
            for css in ("mgv2-psi-indicator-low", "mgv2-psi-indicator-moderate", "mgv2-psi-indicator-high"):
                indicator.remove_css_class(css)
            indicator.add_css_class(f"mgv2-psi-indicator-{bucket}")

    # ── Block 4: Kernel Functions summary ───────────────────────
    def _build_kernel_block(self) -> Gtk.Widget:
        card = DashboardCard(level=2, spacing=12)
        card.add_header(T("ov2_kernel_card_title"), icon_name="emblem-system-symbolic")

        detected, _available, unsupported = count_kernel_inventory()
        active, temporary, permanent = _count_feature_state()

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(5)
        flow.set_min_children_per_line(1)
        flow.set_column_spacing(10)
        flow.set_row_spacing(10)
        flow.set_homogeneous(True)
        self._responsive_flowboxes.append(flow)

        flow.insert(self._metric_tile(str(detected), "ov2_kernel_detected"), -1)
        flow.insert(self._metric_tile(str(active), "ov2_kernel_active"), -1)
        flow.insert(self._metric_tile(str(temporary), "ov2_kernel_temporary"), -1)
        flow.insert(self._metric_tile(str(permanent), "ov2_kernel_permanent"), -1)
        flow.insert(self._metric_tile(str(unsupported), "ov2_kernel_unsupported"), -1)
        card.append(flow)

        open_btn = Gtk.Button(label=T("ov2_open_kernel"))
        open_btn.add_css_class("mgv2-card-action-btn")
        open_btn.set_halign(Gtk.Align.START)
        open_btn.connect("clicked", lambda _b: self._navigate_to("kernel"))
        card.append(open_btn)

        return card

    def _metric_tile(self, value: str, label_key: str) -> Gtk.Widget:
        tile = DashboardCard(level=3, spacing=2)
        val = Gtk.Label(label=value, xalign=0)
        val.add_css_class("mgv2-metric-value")
        lbl = Gtk.Label(label=T(label_key), xalign=0, wrap=True)
        lbl.add_css_class("mgv2-metric-label")
        tile.append(val)
        tile.append(lbl)
        return tile

    # ── Block 5: disks — real physical disks as cards ───────────
    def _build_disks_block(self) -> Gtk.Widget:
        card = DashboardCard(level=2, spacing=12)

        see_all_btn = Gtk.Button(label=T("ov2_disks_open_system"))
        see_all_btn.add_css_class("mgv2-card-action-btn-flat")
        see_all_btn.connect("clicked", lambda _b: self._navigate_to("system"))
        card.add_header(T("ov2_disks_title"), icon_name="drive-harddisk-symbolic",
                         badge_widget=see_all_btn)

        disks, _total = _summarize_physical_disks(max_count=3)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(3)
        flow.set_min_children_per_line(1)
        flow.set_column_spacing(12)
        flow.set_row_spacing(12)
        flow.set_homogeneous(True)
        self._responsive_flowboxes.append(flow)

        for disk in disks:
            flow.insert(self._disk_card(disk), -1)
        card.append(flow)
        return card

    def _disk_card(self, disk: dict) -> Gtk.Widget:
        sub = DashboardCard(level=3, spacing=6)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = Gtk.Label(label="🔌" if disk["removable"] else "💽")
        title = Gtk.Label(label=disk["friendly_name"] or disk["device_id"],
                           xalign=0, hexpand=True, wrap=True)
        title.add_css_class("mgv2-disk-title")
        head.append(icon)
        head.append(title)
        sub.append(head)

        tech = Gtk.Label(label=f"/dev/{disk['device_id']}", xalign=0)
        tech.add_css_class("mgv2-disk-tech")
        sub.append(tech)

        cap_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cap_lbl = Gtk.Label(label=f"{T('ov2_disks_capacity')}: {_format_gb(disk['size_gb'])} GB", xalign=0, hexpand=True)
        cap_lbl.add_css_class("mgv2-disk-cap")
        cap_row.append(cap_lbl)
        if disk["kind"]:
            kind_chip = Gtk.Label(label=disk["kind"])
            kind_chip.add_css_class("mgv2-disk-kind-chip")
            cap_row.append(kind_chip)
        sub.append(cap_row)

        if disk["any_mounted"] and disk["size_gb"] > 0:
            pct = min(disk["used_gb"] / disk["size_gb"] * 100, 100)
            bar = Gtk.ProgressBar()
            bar.set_fraction(pct / 100)
            bar.add_css_class("sysinfo-bar")
            bar.add_css_class("sysinfo-bar-disk")
            sub.append(bar)
            used_lbl = Gtk.Label(label=f"{_format_gb(disk['used_gb'])} / {_format_gb(disk['size_gb'])} GB", xalign=0)
            used_lbl.add_css_class("mgv2-disk-used")
            sub.append(used_lbl)
            # V7: never let a summed number pass as "the" single
            # filesystem's usage — say plainly how many mounted
            # partitions were added together, with a pointer to the
            # page that can show them individually.
            if disk["mounted_count"] > 1:
                multi_lbl = Gtk.Label(
                    label=T("ov2_disks_multi_mount").format(n=disk["mounted_count"]),
                    xalign=0, wrap=True,
                )
                multi_lbl.add_css_class("mgv2-disk-tech")
                sub.append(multi_lbl)
        else:
            note = Gtk.Label(label=T("ov2_disks_not_mounted"), xalign=0, wrap=True)
            note.add_css_class("mgv2-disk-used")
            sub.append(note)

        return sub

    # ── Block 6: quick actions ───────────────────────────────────
    def _build_quick_actions_block(self) -> Gtk.Widget:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        section.append(self._section_label(T("ov2_quick_title")))

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(1)
        flow.set_column_spacing(12)
        flow.set_row_spacing(12)
        flow.set_homogeneous(True)
        self._responsive_flowboxes.append(flow)

        for icon_name, title_key, desc_key, target in (
            ("emblem-system-symbolic", "ov2_quick_kernel_t", "ov2_quick_kernel_d", "kernel"),
            ("drive-harddisk-symbolic", "ov2_quick_system_t", "ov2_quick_system_d", "system"),
            ("network-wireless-symbolic", "ov2_quick_network_t", "ov2_quick_network_d", "network"),
            ("document-open-recent-symbolic", "ov2_quick_history_t", "ov2_quick_history_d", "history"),
        ):
            flow.insert(QuickAction(icon_name, T(title_key), T(desc_key),
                                     on_click=lambda t=target: self._navigate_to(t)), -1)

        section.append(flow)
        return section

    def _section_label(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.add_css_class("mgv2-section-title")
        return lbl

    # ── Administrative component (privileged helper) status ─────────
    # Lives at the bottom of the Panoramica, after the quick actions —
    # a deliberate, logical spot for a system-capability summary, never
    # a random unrelated page. Built entirely from the same DashboardCard
    # / StatusPill components every other block on this page uses, so it
    # can never again end up with a stray light "card" style class that
    # ignores the app's dark theme.
    _ADMIN_PILL_STATE = {
        "admincomp_state_ready": "installed",
        "admincomp_state_missing": "not_installed",
        "admincomp_state_update": "incomplete",
        "admincomp_state_broken": "failed",
        "admincomp_state_portable": "not_available",
    }

    def _admin_component_state(self) -> "tuple[str, bool]":
        """(i18n key for the state, helper usable). Portable AppImage
        without the helper reads as 'portable', not as an error."""
        from core.persistence import priv_client
        status = priv_client.installed_helper_status()
        if status.state == priv_client.HELPER_READY:
            from core.privileged import helper_meta

            def _t(v):
                try:
                    return tuple(int(p) for p in v.split("."))
                except ValueError:
                    return ()
            if _t(status.version) < _t(helper_meta.HELPER_VERSION):
                return "admincomp_state_update", True
            return "admincomp_state_ready", True
        if status.state == priv_client.HELPER_MISSING:
            if priv_client.running_from_appimage():
                return "admincomp_state_portable", False
            return "admincomp_state_missing", False
        if status.state == priv_client.HELPER_INCOMPATIBLE:
            return "admincomp_state_update", False
        return "admincomp_state_broken", False

    def _build_admin_component_block(self) -> Gtk.Widget:
        from ui.design_system.status_pill import state_pill

        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        section.append(self._section_label(T("ov2_admin_section_title")))

        state_key, usable = self._admin_component_state()
        pill = state_pill(self._ADMIN_PILL_STATE.get(state_key, "unknown"), T(state_key))

        card = DashboardCard(level=2, spacing=10)
        card.add_header(T("admincomp_title"), icon_name="system-lock-screen-symbolic",
                        badge_widget=pill)

        desc_lbl = Gtk.Label(label=T("admincomp_desc"), wrap=True, xalign=0)
        desc_lbl.add_css_class("mgv2-card-note")
        card.append(desc_lbl)

        self._admin_result_lbl = Gtk.Label(wrap=True, xalign=0)
        self._admin_result_lbl.add_css_class("mgv2-card-note")
        self._admin_result_lbl.set_visible(False)

        if usable:
            ready_lbl = Gtk.Label(label=T("admincomp_ready_msg"), xalign=0)
            ready_lbl.add_css_class("mgv2-card-note")
            card.append(ready_lbl)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if not usable:
            # No silent root install from a portable AppImage — a clear
            # explanation of the managed install instead.
            complete_btn = Gtk.Button(label=T("admincomp_complete_btn"))
            complete_btn.add_css_class("mgv2-card-action-btn")
            complete_btn.connect("clicked", self._on_admin_complete_clicked)
            btn_box.append(complete_btn)

        why_btn = Gtk.Button(label=T("admincomp_why_btn"))
        why_btn.add_css_class("mgv2-card-action-btn-flat")
        why_btn.connect("clicked", self._on_admin_why_clicked)
        btn_box.append(why_btn)

        verify_btn = Gtk.Button(label=T("admincomp_verify_btn"))
        verify_btn.add_css_class("mgv2-card-action-btn-flat")
        verify_btn.connect("clicked", self._on_admin_verify_clicked)
        btn_box.append(verify_btn)

        card.append(btn_box)
        card.append(self._admin_result_lbl)
        section.append(card)
        return section

    def _on_admin_verify_clicked(self, btn):
        """Read-only diagnostics: asks the installed helper its version.
        Never applies or changes any setting."""
        import threading
        from core.persistence import priv_client
        btn.set_sensitive(False)

        def run():
            result = priv_client.run_helper_diagnostics()
            GLib.idle_add(self._on_admin_verify_done, btn, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_admin_verify_done(self, btn, result):
        btn.set_sensitive(True)
        self._admin_result_lbl.set_visible(True)
        self._admin_result_lbl.remove_css_class("status-active")
        self._admin_result_lbl.remove_css_class("desc-con")
        if result.ok and isinstance(result.value, dict):
            version = result.value.get("helper_version", "?")
            self._admin_result_lbl.set_text(T("admincomp_verify_ok").format(version=version))
            self._admin_result_lbl.add_css_class("status-active")
        else:
            self._admin_result_lbl.set_text(T(result.friendly_message or "kf_err_helper_missing"))
            self._admin_result_lbl.add_css_class("desc-con")
        return False

    def _on_admin_complete_clicked(self, _btn):
        dialog = Adw.MessageDialog(transient_for=self.get_root(),
                                    heading=T("admincomp_complete_btn"),
                                    body=T("admincomp_complete_body"))
        dialog.add_response("close", T("kf_dialog_cancel"))
        dialog.present()

    def _on_admin_why_clicked(self, _btn):
        dialog = Adw.MessageDialog(transient_for=self.get_root(),
                                    heading=T("admincomp_why_btn"),
                                    body=T("admincomp_why_body"))
        dialog.add_response("close", T("kf_dialog_cancel"))
        dialog.present()
