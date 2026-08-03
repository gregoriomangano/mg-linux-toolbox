"""
"Attività del disco" — 2026-08-03. Read-only, no extra tools installed
(no iotop/sysstat/atop/dstat): everything here comes from
core.kernel_features.disk_activity.DiskActivitySampler (itself reading
only /proc/pressure/io, /sys/block/<dev>/stat and /proc/<pid>/io+comm)
and core.kernel_features.monitoring.PSIFeature/PSIHysteresis, both
already used elsewhere in the app.

Reached only from the Panoramica's Disco card ("Apri Attività del
disco" button) — hidden from the sidebar, like the other cross-linked
pages, with an explicit "Torna alla Panoramica" button back.

Widgets are built once. A map/unmap-gated GLib timer requests samples,
while the potentially expensive walk through /proc runs in one daemon
worker at a time and only the finished snapshot is applied on GTK's
main thread.
"""
import gi
import logging
import threading
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from core.i18n import T, on_change
from core import i18n as _i18n_mod

from core.kernel_features.base import SupportStatus
from core.kernel_features.monitoring import PSIFeature, PSIHysteresis
from core.kernel_features.disk_activity import DiskActivitySampler

from ui.dashboard.dashboard_card import DashboardCard

DISK_REFRESH_SECONDS = 3
MAX_PROCESSES_SHOWN = 5
_NONE_AVG10_CEILING = 0.1  # below this, avg10 doesn't even count as "light"
_DOMINANT_SHARE = 0.6      # top process needs >=60% of total activity to count as "the" one

_disk_activity_strings = {
    "da_open_title": {"en": "Disk Activity", "it": "Attività del disco", "es": "Actividad del disco", "fr": "Activité du disque"},
    "da_back_btn": {"en": "Back to Overview", "it": "Torna alla Panoramica", "es": "Volver a la Vista general", "fr": "Retour à l'Aperçu"},
    "da_pause_btn": {"en": "Pause", "it": "Pausa", "es": "Pausa", "fr": "Pause"},
    "da_resume_btn": {"en": "Resume", "it": "Riprendi", "es": "Reanudar", "fr": "Reprendre"},
    "da_refresh_btn": {"en": "Refresh", "it": "Aggiorna", "es": "Actualizar", "fr": "Actualiser"},

    "da_general_title": {"en": "General activity", "it": "Attività generale", "es": "Actividad general", "fr": "Activité générale"},
    "da_level_none":         {"en": "None", "it": "Nessuna", "es": "Ninguna", "fr": "Aucune"},
    "da_level_light":        {"en": "Light", "it": "Leggera", "es": "Ligera", "fr": "Légère"},
    "da_level_elevated":     {"en": "High", "it": "Elevata", "es": "Alta", "fr": "Élevée"},
    "da_level_very_elevated": {"en": "Very high", "it": "Molto elevata", "es": "Muy alta", "fr": "Très élevée"},
    "da_pressure_unsupported": {"en": "Not available on this kernel.", "it": "Non disponibile su questo kernel.", "es": "No disponible en este kernel.", "fr": "Non disponible sur ce noyau."},
    "da_pressure_idle": {"en": "No significant disk wait is visible right now.", "it": "In questo momento non risultano attese significative del disco.", "es": "En este momento no se observan esperas significativas del disco.", "fr": "Aucune attente disque importante n'est visible actuellement."},
    "da_pressure_active": {"en": "Some programs are waiting for disk operations. This can be temporary.", "it": "Alcuni programmi stanno attendendo operazioni del disco. La situazione può essere temporanea.", "es": "Algunos programas están esperando operaciones del disco. La situación puede ser temporal.", "fr": "Certains programmes attendent des opérations disque. La situation peut être temporaire."},
    "da_pressure_high": {"en": "Disk waits are elevated. This is not, by itself, evidence of a disk fault.", "it": "Le attese del disco sono elevate. Questo dato, da solo, non indica un guasto del disco.", "es": "Las esperas del disco son elevadas. Este dato, por sí solo, no indica una avería del disco.", "fr": "Les attentes disque sont élevées. Cette donnée seule n'indique pas une panne du disque."},
    "da_pressure_explainer": {"en": "PSI measures time programs spend waiting for disk operations; it is not disk-utilization percentage or a fault diagnosis.", "it": "PSI misura il tempo in cui i programmi attendono operazioni del disco: non è una percentuale di utilizzo né una diagnosi di guasto.", "es": "PSI mide el tiempo que los programas esperan operaciones del disco: no es un porcentaje de uso ni un diagnóstico de avería.", "fr": "PSI mesure le temps d'attente des programmes pour les opérations disque : ce n'est ni un pourcentage d'utilisation ni un diagnostic de panne."},
    "da_source_unavailable": {"en": "Some live disk data isn't readable with the current permissions.", "it": "Alcuni dati live del disco non sono leggibili con i permessi attuali.", "es": "Algunos datos en directo del disco no se pueden leer con los permisos actuales.", "fr": "Certaines données disque en direct ne sont pas lisibles avec les autorisations actuelles."},
    "da_sampling_failed": {"en": "The live disk sample could not be completed. Try refreshing again.", "it": "Non è stato possibile completare il campionamento del disco. Prova ad aggiornare di nuovo.", "es": "No se pudo completar la muestra del disco. Intenta actualizar de nuevo.", "fr": "L'échantillonnage du disque n'a pas pu être terminé. Essayez d'actualiser à nouveau."},

    "da_disks_title": {"en": "Disks", "it": "Dischi", "es": "Discos", "fr": "Disques"},
    "da_disk_read": {"en": "Read", "it": "Lettura", "es": "Lectura", "fr": "Lecture"},
    "da_disk_write": {"en": "Write", "it": "Scrittura", "es": "Escritura", "fr": "Écriture"},
    "da_disk_ops_in_progress": {"en": "Operations in progress", "it": "Operazioni in corso", "es": "Operaciones en curso", "fr": "Opérations en cours"},
    "da_disk_quiet": {"en": "Quiet", "it": "Tranquillo", "es": "Tranquilo", "fr": "Calme"},
    "da_disks_none": {"en": "No real disk detected.", "it": "Nessun disco reale rilevato.", "es": "No se detectó ningún disco real.", "fr": "Aucun disque réel détecté."},

    "da_processes_title": {"en": "Programs using the disk the most", "it": "Programmi che stanno usando maggiormente il disco", "es": "Programas que más están usando el disco", "fr": "Programmes utilisant le plus le disque"},
    "da_processes_reads_title": {"en": "Most reads", "it": "Maggiori letture", "es": "Mayores lecturas", "fr": "Lectures les plus importantes"},
    "da_processes_writes_title": {"en": "Most writes", "it": "Maggiori scritture", "es": "Mayores escrituras", "fr": "Écritures les plus importantes"},
    "da_processes_idle": {"en": "The disk is quiet right now.", "it": "In questo momento il disco è tranquillo.", "es": "En este momento el disco está tranquilo.", "fr": "Le disque est calme en ce moment."},
    "da_processes_no_dominant": {"en": "Disk waits cannot be attributed with certainty to a single program.", "it": "Le attese del disco non possono essere attribuite con certezza a un singolo programma.", "es": "Las esperas del disco no pueden atribuirse con certeza a un solo programa.", "fr": "Les attentes disque ne peuvent pas être attribuées avec certitude à un seul programme."},
    "da_processes_unreadable_note": {"en": "Some system processes aren't visible with the current permissions.", "it": "Alcuni processi di sistema non sono visibili con i permessi attuali.", "es": "Algunos procesos del sistema no son visibles con los permisos actuales.", "fr": "Certains processus système ne sont pas visibles avec les autorisations actuelles."},
    "da_process_unnamed": {"en": "(unnamed process)", "it": "(processo senza nome)", "es": "(proceso sin nombre)", "fr": "(processus sans nom)"},
    "da_pid_label": {"en": "PID", "it": "PID", "es": "PID", "fr": "PID"},
}
for _k, _v in _disk_activity_strings.items():
    _i18n_mod._strings[_k] = _v

logger = logging.getLogger(__name__)

_LEVEL_KEYS = {
    "none": "da_level_none",
    "light": "da_level_light",
    "elevated": "da_level_elevated",
    "very_elevated": "da_level_very_elevated",
}
_LEVEL_CHIP_CSS = {
    "none": "mgv2-chip-low",
    "light": "mgv2-chip-low",
    "elevated": "mgv2-chip-moderate",
    "very_elevated": "mgv2-chip-high",
}


def _activity_level(bucket: str, avg10: float) -> str:
    """Maps the existing 3-bucket PSI classification (low/moderate/high,
    already hysteresis-gated) onto the 4-word scale this page's spec
    asks for. "high" here always means the SAME confirmed/critical
    state as everywhere else in the app (PSIHysteresis), never a raw
    single high sample."""
    if bucket == "high":
        return "very_elevated"
    if bucket == "moderate":
        return "elevated"
    return "light" if avg10 >= _NONE_AVG10_CEILING else "none"


def _format_rate(bytes_per_second: float) -> str:
    if bytes_per_second <= 0:
        return "0 B/s"
    units = ("B/s", "KB/s", "MB/s", "GB/s")
    value = float(bytes_per_second)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B/s" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} {units[-1]}"


class DiskActivityPage(Gtk.ScrolledWindow):
    def __init__(self, navigate_callback=None):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._navigate = navigate_callback

        self._sampler = DiskActivitySampler()
        self._psi_feature = PSIFeature()
        self._psi_supported = self._psi_feature.probe() == SupportStatus.SUPPORTED_READ_ONLY
        self._psi_hysteresis = PSIHysteresis()

        self._timeout_id = None
        self._paused = False
        self._sample_in_progress = False
        self._sample_generation = 0
        self._destroyed = False

        self._disk_rows = {}       # device_id -> dict of widgets
        self._disk_flow = None
        self._disks_empty_note = None

        clamp = Adw.Clamp(maximum_size=1200, tightening_threshold=800)
        clamp.set_margin_top(22)
        clamp.set_margin_bottom(32)
        clamp.set_margin_start(22)
        clamp.set_margin_end(22)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        content.append(self._build_toolbar())
        self._sampling_note = Gtk.Label(xalign=0, wrap=True)
        self._sampling_note.add_css_class("desc-con")
        self._sampling_note.set_visible(False)
        content.append(self._sampling_note)
        content.append(self._build_general_block())
        content.append(self._build_disks_block())
        content.append(self._build_processes_block())

        clamp.set_child(content)
        self.set_child(clamp)

        on_change(self._refresh_static_labels)

        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("destroy", self._on_destroy)

    # ── Lifecycle: timer only runs while this page is the visible one ──
    def _on_map(self, _w):
        if self._timeout_id is None and not self._paused:
            self._refresh_once()
            self._timeout_id = GLib.timeout_add_seconds(DISK_REFRESH_SECONDS, self._on_timeout)

    def _on_unmap(self, _w):
        self._stop_timer()
        self._sample_generation += 1
        self._psi_hysteresis.reset_pending()

    def _on_destroy(self, _w):
        self._destroyed = True
        self._stop_timer()
        self._sample_generation += 1

    def _stop_timer(self):
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def _on_timeout(self):
        self._refresh_once()
        return True  # keep the timer running

    # ── Toolbar: Torna alla Panoramica / Pausa / Aggiorna ───────────
    def _build_toolbar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        back_btn = Gtk.Button(label=T("da_back_btn"))
        back_btn.add_css_class("mgv2-card-action-btn-flat")
        back_btn.connect("clicked", lambda _b: self._navigate_to("info"))
        bar.append(back_btn)

        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)

        self._refresh_btn = Gtk.Button(label=T("da_refresh_btn"))
        self._refresh_btn.connect("clicked", lambda _b: self._refresh_once())
        bar.append(self._refresh_btn)

        self._pause_btn = Gtk.ToggleButton(label=T("da_pause_btn"))
        self._pause_btn.connect("toggled", self._on_pause_toggled)
        bar.append(self._pause_btn)

        return bar

    def _navigate_to(self, target: str):
        if self._navigate is not None:
            self._navigate(target)

    def _on_pause_toggled(self, btn):
        self._paused = btn.get_active()
        btn.set_label(T("da_resume_btn") if self._paused else T("da_pause_btn"))
        if self._paused:
            self._stop_timer()
            self._sample_generation += 1
            self._psi_hysteresis.reset_pending()
        elif self.get_mapped():
            self._on_map(self)

    # ── Block 1: general I/O-wait activity ──────────────────────────
    def _build_general_block(self) -> Gtk.Widget:
        card = DashboardCard(level=2, spacing=12)
        self._general_header = card.add_header(T("da_general_title"), icon_name="drive-harddisk-symbolic")

        if not self._psi_supported:
            note = Gtk.Label(label=T("da_pressure_unsupported"), xalign=0, wrap=True)
            note.add_css_class("mgv2-card-note")
            card.append(note)
            self._level_chip = None
            return card

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._level_chip = Gtk.Label()
        head.append(self._level_chip)
        card.append(head)

        self._general_phrase = Gtk.Label(xalign=0, wrap=True)
        self._general_phrase.add_css_class("mgv2-card-note")
        card.append(self._general_phrase)

        explainer = Gtk.Label(label=T("da_pressure_explainer"), xalign=0, wrap=True)
        explainer.add_css_class("sysinfo-value-sub")
        card.append(explainer)

        self._details_toggle = Gtk.Button(label=T("kf_show_details_btn"))
        self._details_toggle.add_css_class("flat")
        self._details_toggle.set_halign(Gtk.Align.START)
        self._details_toggle.connect("clicked", self._on_toggle_technical)
        card.append(self._details_toggle)

        self._technical_label = Gtk.Label(xalign=0, wrap=True)
        self._technical_label.add_css_class("sysinfo-value-sub")
        self._technical_label.set_visible(False)
        card.append(self._technical_label)

        return card

    def _on_toggle_technical(self, _btn):
        self._technical_label.set_visible(not self._technical_label.get_visible())

    # ── Block 2: per-disk cards ──────────────────────────────────────
    def _build_disks_block(self) -> Gtk.Widget:
        card = DashboardCard(level=2, spacing=12)
        card.add_header(T("da_disks_title"), icon_name="drive-harddisk-symbolic")

        self._disk_flow = Gtk.FlowBox()
        self._disk_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._disk_flow.set_max_children_per_line(3)
        self._disk_flow.set_min_children_per_line(1)
        self._disk_flow.set_column_spacing(12)
        self._disk_flow.set_row_spacing(12)
        self._disk_flow.set_homogeneous(True)
        card.append(self._disk_flow)

        self._disks_empty_note = Gtk.Label(label=T("da_disks_none"), xalign=0, wrap=True)
        self._disks_empty_note.add_css_class("mgv2-card-note")
        self._disks_empty_note.set_visible(False)
        card.append(self._disks_empty_note)

        return card

    def _disk_card_widgets(self, device_id: str, friendly_name: str, kind: str) -> dict:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("mgv2-card-sub")

        name_lbl = Gtk.Label(label=friendly_name, xalign=0, wrap=True)
        name_lbl.add_css_class("mgv2-psi-sub-name")
        box.append(name_lbl)

        tech_lbl = Gtk.Label(label=device_id, xalign=0)
        tech_lbl.add_css_class("sysinfo-value-sub")
        box.append(tech_lbl)

        read_lbl = Gtk.Label(xalign=0)
        write_lbl = Gtk.Label(xalign=0)
        ops_lbl = Gtk.Label(xalign=0)
        ops_lbl.add_css_class("sysinfo-value-sub")
        box.append(read_lbl)
        box.append(write_lbl)
        box.append(ops_lbl)

        return {
            "widget": box, "read": read_lbl, "write": write_lbl, "ops": ops_lbl,
            "kind": kind,
        }

    # ── Block 3: most active processes ───────────────────────────────
    def _build_processes_block(self) -> Gtk.Widget:
        card = DashboardCard(level=2, spacing=12)
        card.add_header(T("da_processes_title"), icon_name="drive-harddisk-symbolic")

        self._processes_note = Gtk.Label(xalign=0, wrap=True)
        self._processes_note.add_css_class("mgv2-card-note")
        self._processes_note.set_visible(False)
        card.append(self._processes_note)

        self._unreadable_note = Gtk.Label(label=T("da_processes_unreadable_note"), xalign=0, wrap=True)
        self._unreadable_note.add_css_class("desc-what")
        self._unreadable_note.set_visible(False)
        card.append(self._unreadable_note)

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16, homogeneous=True)

        reads_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        reads_title = Gtk.Label(label=T("da_processes_reads_title"), xalign=0)
        reads_title.add_css_class("mgv2-psi-sub-name")
        reads_box.append(reads_title)
        self._reads_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        reads_box.append(self._reads_list_box)
        columns.append(reads_box)

        writes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        writes_title = Gtk.Label(label=T("da_processes_writes_title"), xalign=0)
        writes_title.add_css_class("mgv2-psi-sub-name")
        writes_box.append(writes_title)
        self._writes_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        writes_box.append(self._writes_list_box)
        columns.append(writes_box)

        card.append(columns)
        return card

    def _process_row(self, name: str, pid: int, rate: float) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label_text = name if name else T("da_process_unnamed")
        name_lbl = Gtk.Label(label=label_text, xalign=0, hexpand=True, wrap=True)
        row.append(name_lbl)
        rate_lbl = Gtk.Label(label=_format_rate(rate), xalign=1)
        rate_lbl.add_css_class("sysinfo-value-sub")
        row.append(rate_lbl)
        row.set_tooltip_text(f"{T('da_pid_label')}: {pid}")
        return row

    # ── Live refresh ──────────────────────────────────────────────
    def _refresh_once(self):
        self._refresh_general()
        self._request_sample()

    def _request_sample(self):
        """Start at most one /proc+/sys sampling worker.

        GTK widgets are never touched from the worker thread.  A generation
        number prevents a result collected for a page that has since been
        hidden/destroyed from being applied later.
        """
        if self._destroyed or self._sample_in_progress:
            return
        self._sample_in_progress = True
        self._refresh_btn.set_sensitive(False)
        generation = self._sample_generation

        def collect():
            try:
                snapshot = self._sampler.sample()
                error = None
            except Exception as exc:  # a live monitor must not take down GTK
                snapshot = None
                error = exc
                logger.warning("Disk activity sampling failed", exc_info=True)
            GLib.idle_add(self._on_sample_finished, generation, snapshot, error)

        threading.Thread(
            target=collect,
            name="mg-disk-activity-sampler",
            daemon=True,
        ).start()

    def _on_sample_finished(self, generation, snapshot, error):
        self._sample_in_progress = False
        if self._destroyed:
            return False
        self._refresh_btn.set_sensitive(True)
        if generation != self._sample_generation:
            return False
        if error is not None or snapshot is None:
            self._sampling_note.set_text(T("da_sampling_failed"))
            self._sampling_note.set_visible(True)
            return False

        sources_ok = snapshot.disk_source_available and snapshot.process_source_available
        self._sampling_note.set_text(T("da_source_unavailable"))
        self._sampling_note.set_visible(not sources_ok)
        self._apply_disks(snapshot)
        self._apply_processes(snapshot)
        return False

    def _refresh_general(self):
        if not self._psi_supported:
            return
        result = self._psi_feature.read_current()
        if not result.ok:
            self._psi_hysteresis.reset_pending()
            return
        some = result.value.get("io", {}).get("some", {})
        avg10 = some.get("avg10", 0.0)
        avg60 = some.get("avg60", 0.0)
        avg300 = some.get("avg300", 0.0)
        bucket = self._psi_hysteresis.update(avg10, avg60)
        level = _activity_level(bucket, avg10)

        self._level_chip.set_text(T(_LEVEL_KEYS[level]))
        for css in _LEVEL_CHIP_CSS.values():
            self._level_chip.remove_css_class(css)
        self._level_chip.add_css_class(_LEVEL_CHIP_CSS[level])

        phrase_key = "da_pressure_idle" if level == "none" else \
            "da_pressure_high" if bucket == "high" else "da_pressure_active"
        self._general_phrase.set_text(T(phrase_key))
        self._technical_label.set_text(
            f"{T('kf_psi_avg10_current')}={avg10:.1f}, "
            f"{T('kf_psi_avg60_confirm')}={avg60:.1f}, "
            f"{T('kf_psi_avg300_history')}={avg300:.1f}"
        )
        self._last_io_bucket = bucket

    def _apply_disks(self, snapshot):
        seen_ids = set()
        for disk in snapshot.disks:
            seen_ids.add(disk.device_id)
            row = self._disk_rows.get(disk.device_id)
            if row is None:
                row = self._disk_card_widgets(disk.device_id, disk.friendly_name, disk.kind)
                self._disk_flow.insert(row["widget"], -1)
                self._disk_rows[disk.device_id] = row
            row["read"].set_text(f"{T('da_disk_read')}: {_format_rate(disk.read_bps)}")
            row["write"].set_text(f"{T('da_disk_write')}: {_format_rate(disk.write_bps)}")
            if disk.ops_in_progress > 0:
                row["ops"].set_text(f"{T('da_disk_ops_in_progress')}: {disk.ops_in_progress}")
                row["ops"].set_visible(True)
            else:
                row["ops"].set_text(T("da_disk_quiet"))
                row["ops"].set_visible(True)

        # Devices that disappeared (removed mid-run) drop their card too.
        for stale_id in list(self._disk_rows):
            if stale_id not in seen_ids:
                self._disk_flow.remove(self._disk_rows[stale_id]["widget"])
                del self._disk_rows[stale_id]

        self._disks_empty_note.set_visible(
            snapshot.disk_source_available and len(seen_ids) == 0
        )
        self._last_snapshot = snapshot

    def _apply_processes(self, snapshot):
        reads = sorted((p for p in snapshot.processes if p.read_bps > 0),
                        key=lambda p: p.read_bps, reverse=True)[:MAX_PROCESSES_SHOWN]
        writes = sorted((p for p in snapshot.processes if p.write_bps > 0),
                         key=lambda p: p.write_bps, reverse=True)[:MAX_PROCESSES_SHOWN]

        _clear_box(self._reads_list_box)
        for p in reads:
            self._reads_list_box.append(self._process_row(p.name, p.pid, p.read_bps))

        _clear_box(self._writes_list_box)
        for p in writes:
            self._writes_list_box.append(self._process_row(p.name, p.pid, p.write_bps))

        self._unreadable_note.set_visible(snapshot.unreadable_process_count > 0)

        bucket = getattr(self, "_last_io_bucket", "low")
        if not snapshot.process_source_available:
            self._processes_note.set_visible(False)
        elif not snapshot.processes:
            self._processes_note.set_text(T("da_processes_idle"))
            self._processes_note.set_visible(bucket == "low")
        elif bucket in ("moderate", "high") and not _has_dominant_process(snapshot.processes):
            self._processes_note.set_text(T("da_processes_no_dominant"))
            self._processes_note.set_visible(True)
        else:
            self._processes_note.set_visible(False)

    def _refresh_static_labels(self):
        """Re-apply translated text for widgets that don't otherwise get
        touched by the next PSI/disk tick (labels whose text is pure
        i18n, not derived from a live value)."""
        if self._level_chip is not None:
            self._refresh_general()
        self._refresh_btn.set_label(T("da_refresh_btn"))
        self._pause_btn.set_label(T("da_resume_btn") if self._paused else T("da_pause_btn"))


def _clear_box(box: Gtk.Box):
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def _has_dominant_process(processes) -> bool:
    if not processes:
        return False
    totals = [p.read_bps + p.write_bps for p in processes]
    total = sum(totals)
    if total <= 0:
        return False
    return max(totals) >= _DOMINANT_SHARE * total
