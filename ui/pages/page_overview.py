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
from gi.repository import Adw, Gtk

from core.i18n import T, on_change
from core import i18n as _i18n_mod

from ui.pages.page_info import (
    _get_distro, _get_kernel, _get_uptime,
    _get_cpu_usage, _get_cpu_cores, _get_ram_info, _get_swap_info,
    _get_mount_usage, _get_disks, _is_removable,
)

from core.kernel_features.base import SupportStatus
from core.kernel_features.monitoring import PSIFeature
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
    "ov2_state_high":     {"en": "Check needed", "it": "Controllo necessario", "es": "Revisión necesaria", "fr": "Vérification nécessaire"},
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

    "ov2_quick_title":    {"en": "Quick actions", "it": "Azioni rapide", "es": "Acciones rápidas", "fr": "Actions rapides"},
    "ov2_quick_kernel_t": {"en": "Kernel Functions", "it": "Funzioni kernel", "es": "Funciones del Kernel", "fr": "Fonctions du Noyau"},
    "ov2_quick_kernel_d": {"en": "Governor, swappiness, ZRAM and other kernel functions.", "it": "Governor, swappiness, ZRAM e altre funzioni del kernel.", "es": "Governor, swappiness, ZRAM y otras funciones del kernel.", "fr": "Governor, swappiness, ZRAM et autres fonctions du noyau."},
    "ov2_quick_system_t": {"en": "System & Disk", "it": "Sistema e disco", "es": "Sistema y Disco", "fr": "Système et Disque"},
    "ov2_quick_system_d": {"en": "Disk space, partitions and system tools.", "it": "Spazio su disco, partizioni e strumenti di sistema.", "es": "Espacio en disco, particiones y herramientas del sistema.", "fr": "Espace disque, partitions et outils système."},
    "ov2_quick_network_t": {"en": "Network & Security", "it": "Rete e sicurezza", "es": "Red y Seguridad", "fr": "Réseau et Sécurité"},
    "ov2_quick_network_d": {"en": "Network status and security settings.", "it": "Stato della rete e impostazioni di sicurezza.", "es": "Estado de la red y ajustes de seguridad.", "fr": "État du réseau et paramètres de sécurité."},
    "ov2_quick_history_t": {"en": "History and restore", "it": "Cronologia e ripristino", "es": "Historial y restauración", "fr": "Historique et restauration"},
    "ov2_quick_history_d": {"en": "Review applied changes and restore saved values.", "it": "Rivedi le modifiche applicate e ripristina i valori.", "es": "Revisa los cambios aplicados y restaura los valores.", "fr": "Passez en revue les modifications et restaurez les valeurs."},
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

        clamp.set_child(content)
        self.set_child(clamp)

    def responsive_flowboxes(self):
        """FlowBoxes whose column count a window-level Adw.Breakpoint
        should adjust for medium/narrow widths."""
        return self._responsive_flowboxes

    def _navigate_to(self, target: str):
        if self._navigate is not None:
            self._navigate(target)

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
        bucket = self._overall_pressure_bucket()
        if bucket is None:
            self._state_badge.set_text("—")
            self._state_badge.remove_css_class("moderate")
            self._state_badge.remove_css_class("high")
            return
        self._state_badge.set_text(T(f"ov2_state_{bucket}"))
        self._state_badge.remove_css_class("moderate")
        self._state_badge.remove_css_class("high")
        css = _BADGE_CSS.get(bucket, "")
        if css:
            self._state_badge.add_css_class(css)

    def _overall_pressure_bucket(self):
        """Worst PSI bucket across cpu/memory/io, or None if PSI isn't
        readable on this kernel — used only for the small header badge,
        never a fabricated health score."""
        feature = PSIFeature()
        if feature.probe() != SupportStatus.SUPPORTED_READ_ONLY:
            return None
        result = feature.read_current()
        if not result.ok:
            return None
        order = {"low": 0, "moderate": 1, "high": 2}
        worst = "low"
        for _resource, data in result.value.items():
            bucket = feature.to_friendly(data)
            if order.get(bucket, 0) > order.get(worst, 0):
                worst = bucket
        return worst

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
            flow.insert(self._gauge_card("💽", "ov2_disk_name", COLOR_DISK, root_pct / 100,
                                          f"{root_pct}%", T("ov2_disk_name"),
                                          f"{root_used} / {root_total} GB"), -1)
        if swap_total > 0:
            swap_pct = round(swap_used / swap_total * 100, 1) if swap_total else 0
            flow.insert(self._gauge_card("🔄", "ov2_swap_name", COLOR_SWAP, swap_pct / 100,
                                          f"{swap_used} GB", T("ov2_swap_name"),
                                          f"{swap_total} GB {T('ov2_swap_available')}"), -1)

        section.append(flow)
        return section

    def _gauge_card(self, emoji: str, name_key: str, color, fraction: float,
                     center_value: str, center_caption: str, detail_text: str) -> Gtk.Widget:
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

        return card

    # ── Block 3: PSI pressure ────────────────────────────────────
    def _build_pressure_block(self) -> Gtk.Widget:
        card = DashboardCard(level=2, spacing=12)
        card.add_header(T("ov2_pressure_title"), icon_name="emblem-system-symbolic")

        feature = PSIFeature()
        status = feature.probe()
        if status != SupportStatus.SUPPORTED_READ_ONLY:
            note = Gtk.Label(label=T("ov2_pressure_unsupported"), xalign=0, wrap=True)
            note.add_css_class("mgv2-card-note")
            card.append(note)
            return card

        result = feature.read_current()
        if not result.ok:
            return card

        buckets = {r: feature.to_friendly(result.value.get(r, {})) for r in ("cpu", "memory", "io")}
        worst = max(buckets.values(), key=lambda b: {"low": 0, "moderate": 1, "high": 2}.get(b, 0))

        lead_text = T("ov2_pressure_all_low") if worst == "low" else T("kf_psi_desc")
        lead = Gtk.Label(label=lead_text, xalign=0, wrap=True)
        lead.add_css_class("mgv2-card-note")
        card.append(lead)

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
            sub_flow.insert(self._psi_subcard(resource, buckets[resource], icons[resource]), -1)

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

        phrase = Gtk.Label(label=T(f"kf_psi_{resource}_{bucket}"), xalign=0, wrap=True)
        phrase.add_css_class("mgv2-psi-sub-phrase")
        box.append(phrase)

        indicator = Gtk.ProgressBar()
        fraction = {"low": 0.18, "moderate": 0.55, "high": 0.95}.get(bucket, 0.18)
        indicator.set_fraction(fraction)
        indicator.add_css_class("mgv2-psi-indicator")
        indicator.add_css_class(f"mgv2-psi-indicator-{bucket}")
        box.append(indicator)

        return box

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
