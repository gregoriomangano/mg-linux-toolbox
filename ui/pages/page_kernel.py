"""
"Funzioni Kernel" tab — real kernel/hardware capabilities, explained in
plain language. Starts with PSI (read-only) in Fase 1; swappiness and the
I/O scheduler are added by their own tasks in this same phase.

v3: PageHeader + IconBadge + modern button classes layered on top of
the SAME KernelFeatureRow instances/callbacks — nothing in
ui/kernel/feature_row.py or the KernelFeature backend changes. Every
add_prefix()/CSS-class call below uses only public attributes those
row classes already exposed for this purpose.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
from core import i18n as _i18n_mod
from ui.widgets import make_group
from ui.kernel.feature_row import KernelFeatureRow, handle_restore_click
from core.kernel_features.base import SupportStatus
from core.kernel_features.registry import register
from core.kernel_features.monitoring import PSIFeature, PSIHysteresis, PSI_REFRESH_SECONDS
from core.kernel_features.memory import (
    SwappinessFeature, PRESETS, THPFeature, ZramFeature, ZswapFeature,
    MGLRUFeature, SwapReadaheadFeature,
)
from core.kernel_features.storage import IOSchedulerFeature, list_real_disks
from core.kernel_features.cpu import TurboBoostFeature, GovernorFeature, EPPFeature
from core.kernel_features.network import TcpCongestionControlFeature
from core.kernel_features.security import (
    DmesgRestrictFeature, KptrRestrictFeature, PtraceScopeFeature, ProtectedPathsFeature,
)
import backend.all as B

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group
from ui.design_system.icon_badge import IconBadge
from ui.design_system.action_bar import style_kernel_feature_row_buttons
from ui.design_system.section_card import make_section
from ui.design_system.info_tile import InfoTile
from ui.kernel.inventory import build_kernel_inventory

_kernel_ds_strings = {
    "ds_kernel_header_desc": {
        "en": "Discover and try the functions your kernel offers.",
        "it": "Scopri e prova le funzioni offerte dal kernel del tuo computer.",
        "es": "Descubre y prueba las funciones que ofrece tu kernel.",
        "fr": "Découvrez et essayez les fonctions offertes par votre noyau.",
    },
    "ds_kernel_group_pressure": {
        "en": "Pressure and status", "it": "Pressione e stato",
        "es": "Presión y estado", "fr": "Pression et état",
    },
    "ds_kernel_header_count": {
        "en": "{n} functions detected", "it": "{n} funzioni rilevate",
        "es": "{n} funciones detectadas", "fr": "{n} fonctions détectées",
    },
}
for _k, _v in _kernel_ds_strings.items():
    _i18n_mod._strings[_k] = _v

# Custom swappiness values beyond these are flagged for confirmation —
# presets (10/60/100) are pre-vetted and never trigger this, only the
# free-form "advanced" entry does.
SWAPPINESS_EXTREME_LOW = 5
SWAPPINESS_EXTREME_HIGH = 150

class PSIRow(KernelFeatureRow):
    def __init__(self):
        self.feature = register(PSIFeature())
        super().__init__(self.feature, "kf_psi")
        self.set_status_pill_style(False)

        # 2026-08-03 PSI fix: one hysteresis tracker per resource, so a
        # single elevated (or single recovered) sample never flips the
        # displayed bucket on its own — see PSIHysteresis for the rules.
        self._hysteresis = {r: PSIHysteresis() for r in ("cpu", "memory", "io")}
        self._io_was_critical = False
        from core.kernel_features.disk_pressure_context import CpuIdleTracker
        self._cpu_idle_tracker = CpuIdleTracker()

        self._resource_labels = {}
        for resource in ("cpu", "memory", "io"):
            lbl = Gtk.Label(xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value")
            self.choices_box.append(lbl)
            self._resource_labels[resource] = lbl

        self._io_spike_note = Gtk.Label(label=T("kf_psi_io_spike_note"), wrap=True, xalign=0)
        self._io_spike_note.add_css_class("desc-what")
        self._io_spike_note.set_visible(False)
        self.choices_box.append(self._io_spike_note)

        # Raw avg10/avg60/avg300 numbers stay hidden behind a details
        # toggle — a beginner reads the plain-language phrase above, not
        # PSI internals, by default.
        self._details_toggle = Gtk.Button(label=T("kf_show_details_btn"))
        self._details_toggle.add_css_class("flat")
        self._details_toggle.connect("clicked", self._on_toggle_technical)
        self.choices_box.append(self._details_toggle)

        self._technical_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._technical_box.set_visible(False)
        self._technical_labels = {}
        for resource in ("cpu", "memory", "io"):
            lbl = Gtk.Label(xalign=0, wrap=True)
            lbl.add_css_class("sysinfo-value-sub")
            self._technical_box.append(lbl)
            self._technical_labels[resource] = lbl
        self.choices_box.append(self._technical_box)

        self._timeout_id = None
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("destroy", self._on_unmap)

        self._refresh_once()

    def _on_map(self, _w):
        # Row became visible: start the periodic refresh. Guard against
        # creating a second timer if map fires again while one is active.
        if self._timeout_id is None:
            self._timeout_id = GLib.timeout_add_seconds(PSI_REFRESH_SECONDS, self._on_timeout)

    def _on_unmap(self, _w):
        # Row is no longer visible (different tab selected, or destroyed):
        # stop polling /proc entirely until it's shown again.
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None
        for tracker in self._hysteresis.values():
            tracker.reset_pending()

    def _on_timeout(self):
        self._refresh_once()
        return True  # keep the timer running

    def _on_toggle_technical(self, _btn):
        self._technical_box.set_visible(not self._technical_box.get_visible())

    def _refresh_once(self):
        status = self.feature.probe()
        self.set_support_status(status)

        if status != SupportStatus.SUPPORTED_READ_ONLY:
            self.clear_error()
            return

        result = self.feature.read_current()
        if not result.ok:
            for tracker in self._hysteresis.values():
                tracker.reset_pending()
            self.show_error(result.friendly_message, result.technical_detail)
            return
        self.clear_error()

        per_resource = result.value
        io_is_critical = False
        elevated_labels = []
        cpu_idle_pct = self._cpu_idle_tracker.sample()
        for resource in ("cpu", "memory", "io"):
            data = per_resource.get(resource, {})
            some = data.get("some", {})
            avg10 = some.get("avg10", 0.0)
            avg60 = some.get("avg60", 0.0)
            # Hysteresis, not the raw single-sample bucket: needs >=2
            # consecutive high avg10 readings to enter "high", and >=2
            # consecutive readings with avg10 AND avg60 both back down
            # to leave it — avg300 never enters this decision at all.
            bucket = self._hysteresis[resource].update(avg10, avg60)
            if resource == "io" and bucket == "high":
                # 2026-08-05: same corroboration as the Panoramica badge
                # and "Attività del disco" — a confirmed-high io bucket
                # explained by one blocked process on an otherwise idle
                # CPU reads as "moderate" here too, so this row and the
                # Panoramica can never disagree about the same machine.
                from core.kernel_features.disk_pressure_context import (
                    count_blocked_processes, classify_disk_pressure, CRITICAL,
                )
                blocked = count_blocked_processes()
                if classify_disk_pressure("high", blocked, cpu_idle_pct) != CRITICAL:
                    bucket = "moderate"
            label = T(f"kf_psi_{resource}")
            # Gender-correct plain-language phrase per resource — never
            # just "Bassa/Moderata/Alta" on their own. Full per-resource
            # breakdown always stays visible in the open card.
            phrase = T(f"kf_psi_{resource}_{bucket}")
            if resource == "io" and bucket == "high":
                # kf_psi_io_high already names the resource ("Attesa del
                # disco elevata") — repeating "Disco: " in front of it
                # would be redundant, unlike the cpu/memory adjectives.
                self._resource_labels[resource].set_text(phrase)
            else:
                self._resource_labels[resource].set_text(f"{label}: {phrase}")
            self._technical_labels[resource].set_text(
                f"{label} — {T('kf_psi_avg10_current')}={avg10:.1f}, "
                f"{T('kf_psi_avg60_confirm')}={avg60:.1f}, "
                f"{T('kf_psi_avg300_history')}={some.get('avg300', 0.0):.1f}"
            )
            if bucket != "low":
                elevated_labels.append(label)
            if resource == "io" and bucket == "high":
                io_is_critical = True

        if io_is_critical:
            self._io_spike_note.set_text(T("kf_psi_io_spike_note"))
            self._io_spike_note.set_visible(True)
        elif self._io_was_critical:
            # Just left the critical state this refresh: say so once,
            # explicitly, instead of the note just silently vanishing.
            self._io_spike_note.set_text(T("kf_psi_io_restored"))
            self._io_spike_note.set_visible(True)
        else:
            self._io_spike_note.set_visible(False)
        self._io_was_critical = io_is_critical

        # Collapsed pill: one compact phrase, never the full per-resource
        # sentence — the breakdown above already covers that when open.
        if elevated_labels:
            compact = T("kf_psi_elevated_summary").format(resources=", ".join(elevated_labels))
        else:
            compact = T("kf_psi_all_low")
        self.set_status_line(compact)
        self.choices_box.set_visible(True)


class SwappinessRow(KernelFeatureRow):
    def __init__(self):
        self.feature = register(SwappinessFeature())
        super().__init__(self.feature, "kf_swappiness")
        self.set_status_pill_style(False)

        # This feature's "try" interaction IS the preset/custom picker
        # below, so the generic single "Try" button from the base row
        # isn't used here.
        self.btn_try.set_visible(False)
        self._current_value = None

        self._zram_note = Gtk.Label(label=T("kf_swappiness_zram_note"), wrap=True, xalign=0)
        self._zram_note.add_css_class("desc-what")
        self._zram_note.set_visible(False)
        self.choices_box.append(self._zram_note)

        intro = Gtk.Label(label=T("kf_swappiness_optional_profile"), wrap=True, xalign=0)
        intro.add_css_class("sysinfo-label")
        self.choices_box.append(intro)

        preset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for key, value in PRESETS:
            btn = Gtk.Button(label=T(f"kf_swappiness_preset_{key}"))
            btn.connect("clicked", self._on_preset_clicked, value)
            preset_box.append(btn)
        self.choices_box.append(preset_box)

        # Advanced/custom value — collapsed by default, so it doesn't read
        # as "the" thing to do; starts at the CURRENT value (never 0), and
        # its apply button stays off until the user actually changes it.
        self._advanced_expander = Gtk.Expander(label=T("kf_swappiness_advanced_toggle"))
        custom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        custom_box.set_margin_top(6)
        self._custom_spin = Gtk.SpinButton.new_with_range(
            SwappinessFeature.MIN, SwappinessFeature.MAX, 1)
        self._custom_spin.connect("value-changed", self._on_custom_value_changed)
        self._custom_apply_btn = Gtk.Button(label=T("kf_swappiness_custom_apply_btn"))
        self._custom_apply_btn.add_css_class("lt-action-btn")
        self._custom_apply_btn.set_sensitive(False)
        self._custom_apply_btn.connect("clicked", self._on_custom_clicked)
        custom_box.append(self._custom_spin)
        custom_box.append(self._custom_apply_btn)
        self._advanced_expander.set_child(custom_box)
        self.choices_box.append(self._advanced_expander)

        self.btn_permanent.connect("clicked", self._on_make_permanent)
        self.btn_restore.connect("clicked", self._on_restore)

        self._refresh_once()

    def _refresh_once(self):
        status = self.feature.probe()
        self.set_support_status(status)
        self.choices_box.set_visible(True)

        current = self.feature.read_current()
        if not current.ok:
            self.show_error(current.friendly_message, current.technical_detail)
            return
        self.clear_error()

        self._current_value = current.value
        friendly = T(self.feature.to_friendly(current.value))
        self.set_status_line(friendly, str(current.value))

        # Initialize the custom field to the CURRENT value (never 0), and
        # keep its apply button disabled until it's actually changed.
        self._custom_spin.set_value(current.value)
        self._custom_apply_btn.set_sensitive(False)

        self._zram_note.set_visible(B.zram_active())

        rec = self.feature.get_record()
        if rec is not None:
            initial_friendly = T(self.feature.to_friendly(rec.initial_value))
            self.set_initial_value(f"{initial_friendly} ({rec.initial_value})")
            self.set_restore_enabled(True)
        else:
            self.set_initial_value("")
            self.set_restore_enabled(False)

        # Never claim a universal recommendation — only say this if the
        # value hasn't been touched by us or anyone else at all.
        self.set_no_action_needed(rec is None)

    def _on_custom_value_changed(self, spin):
        changed = int(spin.get_value()) != self._current_value
        self._custom_apply_btn.set_sensitive(changed)

    def _apply_value(self, value):
        self.clear_error()
        result = self.feature.apply_temporary(value)
        if not result.ok:
            self.show_error(result.friendly_message, result.technical_detail)
            return
        self._refresh_once()

    def _on_preset_clicked(self, _btn, value):
        self._apply_value(value)

    def _on_custom_clicked(self, _btn):
        value = int(self._custom_spin.get_value())
        if value <= SWAPPINESS_EXTREME_LOW or value >= SWAPPINESS_EXTREME_HIGH:
            self._confirm_extreme_value(value)
            return
        self._apply_value(value)

    def _confirm_extreme_value(self, value):
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=T("kf_swappiness_custom_apply_btn"),
            body=T("kf_swappiness_extreme_confirm"),
        )
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("kf_swappiness_custom_apply_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_dialog, response):
            if response == "confirm":
                self._apply_value(value)

        dialog.connect("response", on_response)
        dialog.present()

    def _on_make_permanent(self, _btn):
        current = self.feature.read_current()
        if not current.ok:
            self.show_error(current.friendly_message, current.technical_detail)
            return
        result = self.feature.apply_persistent(current.value)
        if not result.ok:
            self.show_error(result.friendly_message, result.technical_detail)
            return
        self._refresh_once()

    def _on_restore(self, _btn):
        handle_restore_click(self, self.feature.restore, self._refresh_once)


# Friendly title/description per scheduler algorithm — only for the ones
# we can honestly explain. Anything else the kernel exposes is still shown
# (never hidden), just without the extra explanation text. Per spec: never
# declare one scheduler universally best, only describe its behaviour.
SCHED_INFO = {
    "none": ("kf_sched_none_title", "kf_sched_none_desc"),
    "mq-deadline": ("kf_sched_deadline_title", "kf_sched_deadline_desc"),
    "kyber": ("kf_sched_kyber_title", "kf_sched_kyber_desc"),
    "bfq": ("kf_sched_bfq_title", "kf_sched_bfq_desc"),
}


class IOSchedulerRow(KernelFeatureRow):
    def __init__(self, device_id: str, friendly_disk_name: str):
        self.feature = register(IOSchedulerFeature(device_id))
        # Must be set BEFORE super().__init__(): the base class's own
        # __init__ already calls self.refresh_labels() once, and our
        # override of refresh_labels() below needs this attribute to exist.
        self._friendly_disk_name = friendly_disk_name
        self._device_id = device_id
        self._current_scheduler = None
        self._selected_scheduler = None
        super().__init__(self.feature, "kf_io_scheduler")
        self.set_status_pill_style(False)

        # No permanence in this phase for the I/O scheduler.
        self.btn_permanent.set_visible(False)

        self._current_use_lbl = Gtk.Label(xalign=0, wrap=True)
        self._current_use_lbl.add_css_class("sysinfo-value")
        self.choices_box.append(self._current_use_lbl)

        label = Gtk.Label(label=T("kf_io_scheduler_available"), xalign=0)
        label.add_css_class("sysinfo-label")
        self.choices_box.append(label)

        # Rebuilt from scratch on every refresh (see _refresh_once) since
        # the set of available schedulers and the active one can change.
        self._choice_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.choices_box.append(self._choice_list)

        self.btn_try.connect("clicked", self._on_try_clicked)
        self.btn_restore.connect("clicked", self._on_restore)

        self._refresh_once()

    def refresh_labels(self):
        super().refresh_labels()
        # Multiple disks can exist, so identify which one this row is
        # about — re-applied on every call (including language changes),
        # overriding the generic technical-name subtitle from the base row.
        self.set_title(f"{T('kf_io_scheduler_title')} — {self._friendly_disk_name}")
        self.set_subtitle(f"{T('kf_technical_name_device')}: {self._device_id}")

    def _friendly_scheduler_name(self, name: str) -> str:
        info = SCHED_INFO.get(name)
        return f"{T(info[0])} ({name})" if info else name

    def _build_choice_row(self, name: str, group_leader, is_current: bool):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        # active= is set at construction time, BEFORE connect("toggled"),
        # so building the initial radio state never fires our handler —
        # the same defensive ordering used elsewhere to avoid spurious
        # apply-on-construction bugs.
        toggle = Gtk.ToggleButton(label=self._friendly_scheduler_name(name), active=is_current)
        if group_leader is not None:
            toggle.set_group(group_leader)
        box.append(toggle)

        info = SCHED_INFO.get(name)
        if info:
            desc = Gtk.Label(label=T(info[1]), wrap=True, xalign=0)
            desc.add_css_class("sysinfo-value-sub")
            box.append(desc)

        toggle.connect("toggled", self._on_choice_toggled, name)
        return box, toggle

    def _on_choice_toggled(self, toggle, name):
        if not toggle.get_active():
            return
        self._selected_scheduler = name
        # "Prova" only makes sense once the user has picked something
        # different from what's already active — otherwise there is
        # nothing to try.
        self.set_try_sensitive(name != self._current_scheduler, "kf_try_reason_same_value")

    def _on_try_clicked(self, _btn):
        self.clear_error()
        result = self.feature.apply_temporary(self._selected_scheduler)
        if not result.ok:
            self.show_error(result.friendly_message, result.technical_detail)
            return
        self._refresh_once()

    def _on_restore(self, _btn):
        handle_restore_click(self, self.feature.restore, self._refresh_once)

    def _refresh_once(self):
        status = self.feature.probe()
        self.set_support_status(status)
        self.choices_box.set_visible(True)

        current = self.feature.read_current()
        if not current.ok:
            self.show_error(current.friendly_message, current.technical_detail)
            return
        self.clear_error()

        available = current.value["available"]
        active = current.value["current"]
        self._current_scheduler = active
        self._selected_scheduler = active

        friendly_active = self._friendly_scheduler_name(active) if active else "—"
        self._current_use_lbl.set_text(f"{T('kf_io_scheduler_current_use')}: {friendly_active}")
        self.set_status_line(friendly_active)

        child = self._choice_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._choice_list.remove(child)
            child = nxt

        group_leader = None
        for name in available:
            row, toggle = self._build_choice_row(name, group_leader, is_current=(name == active))
            self._choice_list.append(row)
            if group_leader is None:
                group_leader = toggle

        # Nothing to try until the user picks a scheduler different from
        # the one currently active — matches "Prova" being disabled by
        # default right after a refresh.
        self.set_try_sensitive(False, "kf_try_reason_pick_different")

        rec = self.feature.get_record(device_id=self.feature.device_id)
        if rec is not None:
            self.set_initial_value(rec.initial_value or "—")
            self.set_restore_enabled(True)
        else:
            self.set_initial_value("")
            self.set_restore_enabled(False)

        self.set_no_action_needed(rec is None)


def _widen_preferences_clamp(page: Adw.PreferencesPage, maximum_size: int, tightening_threshold: int):
    """
    AdwPreferencesPage clamps its content to 600px by default (fine for
    plain switches, too narrow for this page's longer explanations). There
    is no public API to change that, so we reach into the page's own
    internal ScrolledWindow > Viewport > Clamp — a real, always-present
    part of the widget tree, just not exposed as a property. Guarded so a
    future libadwaita layout change degrades to "stays at 600px" instead
    of crashing.
    """
    try:
        clamp = page.get_first_child().get_first_child().get_first_child()
        if isinstance(clamp, Adw.Clamp):
            clamp.set_maximum_size(maximum_size)
            clamp.set_tightening_threshold(tightening_threshold)
    except (AttributeError, TypeError):
        pass


class BooleanKernelFeatureRow(KernelFeatureRow):
    """
    Shared UI for a plain on/off KernelFeature (Turbo Boost, ZRAM, Zswap):
    two staged choices, "Prova fino al riavvio" only enabled once you've
    picked something different from what's active — same interaction
    already used for the I/O scheduler, just with exactly two options.
    """
    def __init__(self, feature, i18n_key_base):
        super().__init__(feature, i18n_key_base)
        self._current_value = None
        self._selected_value = None

        self._choice_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.choices_box.append(self._choice_box)

        # Secondary info for features that track boot persistence
        # separately from runtime state (currently only KSM's
        # autostart_state()) — a plain line in the expanded body, never
        # appended into the collapsed pill's own text (that turned a
        # short "Disattivata" into a long "Disattivata · Avvio
        # automatico: Non configurato" oval).
        self._autostart_lbl = Gtk.Label(wrap=True, xalign=0)
        self._autostart_lbl.add_css_class("sysinfo-value-sub")
        self._autostart_lbl.set_visible(False)
        self.choices_box.append(self._autostart_lbl)

        self.btn_try.connect("clicked", self._on_try_clicked)
        self.btn_permanent.connect("clicked", self._on_make_permanent)
        self.btn_restore.connect("clicked", self._on_restore)

        self._refresh_once()

    def _build_toggle(self, value, group_leader, is_current):
        toggle = Gtk.ToggleButton(label=T(self.feature.to_friendly(value)), active=is_current)
        if group_leader is not None:
            toggle.set_group(group_leader)
        toggle.connect("toggled", self._on_choice_toggled, value)
        return toggle

    def _on_choice_toggled(self, toggle, value):
        if not toggle.get_active():
            return
        self._selected_value = value
        self.set_try_sensitive(value != self._current_value, "kf_try_reason_same_value")

    def _on_try_clicked(self, _btn):
        self.clear_error()
        result = self.feature.apply_temporary(self._selected_value)
        if not result.ok:
            self.show_error(result.friendly_message, result.technical_detail)
            return
        self._refresh_once()

    def _on_make_permanent(self, _btn):
        self.clear_error()
        result = self.feature.apply_persistent(self._selected_value)
        if not result.ok:
            self.show_error(result.friendly_message, result.technical_detail)
            return
        self._refresh_once()

    def _on_restore(self, _btn):
        handle_restore_click(self, self.feature.restore, self._refresh_once)

    def _refresh_once(self):
        status = self.feature.probe()
        self.set_support_status(status)
        self.choices_box.set_visible(True)

        current = self.feature.read_current()
        if not current.ok:
            self.show_error(current.friendly_message, current.technical_detail)
            return
        self.clear_error()

        self._current_value = current.value
        self._selected_value = current.value
        status_text = T(self.feature.to_friendly(current.value))
        self.set_status_line(status_text)
        # Runtime state and boot persistence are different facts: a
        # feature that exposes autostart_state() (KSM) gets both shown
        # honestly — "Attiva adesso" never silently implies "sempre
        # attiva", and "Avvio automatico" reflects the real config file.
        # Shown as its own secondary line, never folded into the
        # collapsed-row pill text above.
        autostart_probe = getattr(self.feature, "autostart_state", None)
        autostart_text = ""
        if callable(autostart_probe):
            autostart = autostart_probe()
            if autostart is not None:
                autostart_text = (f"{T('ksm_autostart_label')}: "
                                  f"{T('ksm_autostart_configured' if autostart else 'ksm_autostart_not_configured')}")
        self._autostart_lbl.set_text(autostart_text)
        self._autostart_lbl.set_visible(bool(autostart_text))
        # v4: every boolean kernel feature (Turbo Boost, ZRAM, Zswap,
        # KSM...) gets the same rule — "on" is a real, positive state
        # worth showing in green, "off" is neutral, never a fabricated
        # judgement about a value the app can't classify (see
        # ChoiceKernelFeatureRow below, which does NOT get this).
        self.set_status_variant("success" if current.value else "neutral")

        child = self._choice_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._choice_box.remove(child)
            child = nxt

        t_on = self._build_toggle(True, None, current.value is True)
        t_off = self._build_toggle(False, t_on, current.value is False)
        self._choice_box.append(t_on)
        self._choice_box.append(t_off)

        self.set_try_sensitive(False, "kf_try_reason_pick_different")

        rec = self.feature.get_record()
        if rec is not None:
            self.set_initial_value(T(self.feature.to_friendly(rec.initial_value)))
            self.set_restore_enabled(True)
        else:
            self.set_initial_value("")
            self.set_restore_enabled(False)
        self.set_no_action_needed(rec is None)


def _new_choice_flowbox() -> Gtk.FlowBox:
    """Shared responsive container for a row of choice buttons (toggle
    groups, presets, profiles...) — wraps to a new line automatically
    when the row is too narrow instead of running off the window edge
    or forcing a horizontal scrollbar, same approach already used on
    the Services page. Selection itself is NOT handled by the FlowBox
    (selection_mode NONE): each ToggleButton's own active/group state
    is still the single source of truth for what's selected, so
    wrapping never introduces a second, competing selection concept."""
    flow = Gtk.FlowBox()
    flow.set_selection_mode(Gtk.SelectionMode.NONE)
    flow.set_min_children_per_line(1)
    flow.set_max_children_per_line(8)
    flow.set_column_spacing(8)
    flow.set_row_spacing(8)
    flow.set_homogeneous(False)
    return flow


def _flowbox_clear(flow: Gtk.FlowBox) -> None:
    child = flow.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        flow.remove(child)
        child = nxt


class ChoiceKernelFeatureRow(KernelFeatureRow):
    """
    Shared UI for a KernelFeature exposing a small set of kernel-reported
    string values (CPU Governor, EPP, THP) — same staged-choice pattern,
    values shown exactly as the kernel names them (never a made-up
    friendly translation, and never presented as one being "the best").
    """
    def __init__(self, feature, i18n_key_base):
        super().__init__(feature, i18n_key_base)
        # Values here are whatever the kernel itself names them
        # (governor/EPP/THP/scheduler...) — never a short fixed set of
        # state words, so the collapsed suffix stays plain compact text
        # instead of a pill that would stretch into a big oval.
        self.set_status_pill_style(False)
        self._current_value = None
        self._selected_value = None

        self._available_label = Gtk.Label(label=T("kf_choice_available"), xalign=0)
        self._available_label.add_css_class("sysinfo-label")
        self.choices_box.append(self._available_label)

        self._choice_list = _new_choice_flowbox()
        self.choices_box.append(self._choice_list)

        # "Already protected" note — hidden and inert for every existing
        # row (governor/EPP/THP/MGLRU/page-cluster/TCP): _protected_values()
        # defaults to an empty set below, so this note can never appear
        # for them, exactly as before. Only the hardening rows that
        # override _protected_values() (dmesg_restrict/kptr_restrict/
        # ptrace_scope) ever populate and show it.
        self._already_note = Gtk.Label(wrap=True, xalign=0)
        self._already_note.add_css_class("desc-pro")
        self._already_note.set_visible(False)
        self.choices_box.append(self._already_note)

        self.btn_try.connect("clicked", self._on_try_clicked)
        self.btn_permanent.connect("clicked", self._on_make_permanent)
        self.btn_restore.connect("clicked", self._on_restore)

        self._refresh_once()

    def _protected_values(self) -> set:
        """Raw values considered 'this protection is already active' —
        empty for every row by default (governor/EPP/THP/MGLRU/page-
        cluster/TCP never show the note). Only overridden by the
        hardening rows (dmesg_restrict/kptr_restrict/ptrace_scope)."""
        return set()

    def _on_choice_toggled(self, toggle, value):
        if not toggle.get_active():
            return
        self._selected_value = value
        self.set_try_sensitive(value != self._current_value, "kf_try_reason_same_value")
        # Shown only when the selected value both matches what's
        # currently active AND is itself a genuinely protected state —
        # never for an unrestricted/less-restrictive value, never for
        # an unrelated row (see _protected_values()).
        already_protected = (
            value == self._current_value and value in self._protected_values()
        )
        self._already_note.set_text(T("kf_already_protected_note"))
        self._already_note.set_visible(already_protected)

    def _on_try_clicked(self, _btn):
        self.clear_error()
        result = self.feature.apply_temporary(self._selected_value)
        if not result.ok:
            self.show_error(result.friendly_message, result.technical_detail)
            return
        self._refresh_once()

    def _on_make_permanent(self, _btn):
        self.clear_error()
        result = self.feature.apply_persistent(self._selected_value)
        if not result.ok:
            self.show_error(result.friendly_message, result.technical_detail)
            return
        self._refresh_once()

    def _on_restore(self, _btn):
        handle_restore_click(self, self.feature.restore, self._refresh_once)

    def _display_value(self, value: str):
        """Returns (friendly_text, technical_text_or_None). Technical text
        is only given when it genuinely differs from the friendly text —
        Governor/EPP values pass through to_friendly() unchanged (never an
        invented translation), so they never show a redundant "X (X)";
        THP's three values do have a real translation, and keep the raw
        kernel value alongside in parentheses."""
        if value == "mixed":
            return T("kf_mixed_value"), None
        friendly = T(self.feature.to_friendly(value))
        return (friendly, value) if friendly != value else (friendly, None)

    def _label_for(self, value: str) -> str:
        friendly, technical = self._display_value(value)
        return f"{friendly} ({technical})" if technical else friendly

    def _refresh_once(self):
        status = self.feature.probe()
        self.set_support_status(status)
        self.choices_box.set_visible(True)

        current = self.feature.read_current()
        if not current.ok:
            self.show_error(current.friendly_message, current.technical_detail)
            return
        self.clear_error()
        self._already_note.set_visible(False)

        # THP's read_current() returns {"available": [...], "current": ...}
        # (bracket-style sysfs, like the I/O scheduler); Governor/EPP
        # return the plain active value directly with read_available()
        # as a separate call. Handling both shapes here keeps this one
        # row class usable for all three instead of forcing THP to a
        # different internal shape than the I/O scheduler it mirrors.
        raw = current.value
        if isinstance(raw, dict):
            active = raw.get("current")
            available = raw.get("available", [])
        else:
            active = raw
            available = self.feature.read_available() or []

        self._current_value = active
        self._selected_value = active
        if active:
            friendly, technical = self._display_value(active)
            self.set_status_line(friendly, technical or "")
        else:
            self.set_status_line("—")

        _flowbox_clear(self._choice_list)

        group_leader = None
        for name in available:
            toggle = Gtk.ToggleButton(label=self._label_for(name), active=(name == active))
            if group_leader is not None:
                toggle.set_group(group_leader)
            toggle.connect("toggled", self._on_choice_toggled, name)
            self._choice_list.insert(toggle, -1)
            if group_leader is None:
                group_leader = toggle

        self.set_try_sensitive(False, "kf_try_reason_pick_different")

        rec = self.feature.get_record()
        if rec is not None:
            self.set_initial_value(self._label_for(rec.initial_value) if rec.initial_value else "—")
            self.set_restore_enabled(True)
        else:
            self.set_initial_value("")
            self.set_restore_enabled(False)
        self.set_no_action_needed(rec is None)


_ZRAM_OWNER_KEYS = {
    ZramFeature.OWNER_SYSTEMD_GENERATOR: "kf_zram_owner_systemd",
    ZramFeature.OWNER_ZRAM_TOOLS: "kf_zram_owner_zramtools",
    ZramFeature.OWNER_EXTERNAL: "kf_zram_owner_external",
}


class ZramRow(BooleanKernelFeatureRow):
    """
    Absolute rule: if this system's active ZRAM wasn't created by M.G
    Linux Toolbox, this row becomes pure status display — no on/off
    choice, no Prova, no Ripristina, no size/algorithm/priority control.
    """
    def __init__(self):
        # Must exist BEFORE super().__init__(): its own __init__ already
        # calls _refresh_once() once, and our override below needs this.
        self._owner_note = Gtk.Label(wrap=True, xalign=0)
        self._owner_note.add_css_class("desc-what")
        self._owner_note.set_visible(False)

        feature = register(ZramFeature())
        super().__init__(feature, "zram")
        self.btn_permanent.set_visible(False)
        self.choices_box.append(self._owner_note)

    def _refresh_once(self):
        owner = self.feature.owner()
        if owner is not None and owner != self.feature.OWNER_TOOLBOX:
            # Externally owned: read-only, no controls of any kind.
            self.set_support_status(SupportStatus.SUPPORTED_READ_ONLY)
            self.choices_box.set_visible(True)
            self._choice_box.set_visible(False)
            owner_key = _ZRAM_OWNER_KEYS.get(owner, "kf_zram_owner_external")
            self.set_status_line(T("kf_zram_managed_by_system"), T(owner_key))
            self.set_status_variant("success")  # it IS active, just not managed by us
            self._owner_note.set_text(T("kf_zram_owner_note"))
            self._owner_note.set_visible(True)
            self.set_initial_value("")
            self.set_restore_enabled(False)
            self.set_no_action_needed(False)
            self.clear_error()
            return
        self._choice_box.set_visible(True)
        self._owner_note.set_visible(False)
        super()._refresh_once()


class ZswapRow(BooleanKernelFeatureRow):
    def __init__(self):
        # Must exist BEFORE super().__init__(): the base class's own
        # __init__ already calls self._refresh_once() once, and our
        # override below needs this attribute to exist.
        self._zram_note = Gtk.Label(wrap=True, xalign=0)
        self._zram_note.add_css_class("desc-what")
        self._zram_note.set_visible(False)

        feature = register(ZswapFeature())
        super().__init__(feature, "zswap")
        self.btn_permanent.set_visible(False)
        self.choices_box.append(self._zram_note)
        self._refresh_zram_note()

    def _refresh_zram_note(self):
        self._zram_note.set_text(T("zswap_zram_note"))
        self._zram_note.set_visible(B.zram_active())

    def _refresh_once(self):
        super()._refresh_once()
        self._refresh_zram_note()


class TurboBoostRow(BooleanKernelFeatureRow):
    def __init__(self):
        feature = register(TurboBoostFeature())
        super().__init__(feature, "turbo")
        self.btn_permanent.set_visible(False)


class GovernorRow(ChoiceKernelFeatureRow):
    def __init__(self):
        feature = register(GovernorFeature())
        super().__init__(feature, "governor")
        self.btn_permanent.set_visible(False)


class EPPRow(ChoiceKernelFeatureRow):
    """
    If energy_performance_available_preferences has exactly one entry,
    there is nothing a user could meaningfully pick — this becomes a
    pure read-only display instead of a fake single-item menu.
    """
    def __init__(self):
        # Must exist BEFORE super().__init__(): its own __init__ already
        # calls _refresh_once() once, and our override below needs this.
        self._single_value_note = Gtk.Label(wrap=True, xalign=0)
        self._single_value_note.add_css_class("desc-what")
        self._single_value_note.set_visible(False)

        feature = register(EPPFeature())
        super().__init__(feature, "epp")
        self.btn_permanent.set_visible(False)
        self.choices_box.append(self._single_value_note)

    def _refresh_once(self):
        super()._refresh_once()
        available = self.feature.read_available() or []
        single = len(available) <= 1
        self._single_value_note.set_text(T("epp_single_value_note"))
        self._single_value_note.set_visible(single)
        if single:
            self._available_label.set_visible(False)
            self._choice_list.set_visible(False)
            self.btn_try.set_visible(False)
            self.btn_restore.set_visible(False)


class THPRow(ChoiceKernelFeatureRow):
    def __init__(self):
        feature = register(THPFeature())
        super().__init__(feature, "thp")
        self.btn_permanent.set_visible(False)


class MGLRURow(ChoiceKernelFeatureRow):
    """
    Only two curated choices are offered ("enable everything supported"
    / "disable"), not the kernel's raw bitmask — see MGLRUFeature. No
    persistence yet in this phase (per spec: only add it once a oneshot
    service can be shown to handle it reliably).
    """
    def __init__(self):
        feature = register(MGLRUFeature())
        super().__init__(feature, "kf_mglru")
        self.btn_permanent.set_visible(False)


class SwapReadaheadRow(ChoiceKernelFeatureRow):
    def __init__(self):
        feature = register(SwapReadaheadFeature())
        super().__init__(feature, "kf_swap_readahead")


class TcpCongestionRow(ChoiceKernelFeatureRow):
    """
    Only algorithms the kernel itself reports via
    tcp_available_congestion_control are ever shown — never a static
    list, never BBR if this kernel doesn't have it. Each choice gets a
    short, verified plain-language description underneath (or the
    honest "no description yet" text for one this app hasn't
    documented) instead of promising "faster internet" for any of them.
    """
    def __init__(self):
        self._algo_desc_lbl = Gtk.Label(wrap=True, xalign=0)
        self._algo_desc_lbl.add_css_class("desc-what")
        feature = register(TcpCongestionControlFeature())
        super().__init__(feature, "kf_tcp_congestion")
        self.choices_box.append(self._algo_desc_lbl)

    def _refresh_once(self):
        super()._refresh_once()
        if self._current_value:
            self._algo_desc_lbl.set_text(T(self.feature.description_key(self._current_value)))
            self._algo_desc_lbl.set_visible(True)
        else:
            self._algo_desc_lbl.set_visible(False)


class ReadAheadRow(KernelFeatureRow):
    """
    One row per real disk (same device enumeration as the I/O
    scheduler). Curated KB presets plus a free numeric field for a
    custom value — never applies the same value to every disk
    automatically, each row is independent.
    """
    def __init__(self, device_id: str, friendly_disk_name: str):
        from core.kernel_features.storage import ReadAheadFeature, READ_AHEAD_PRESETS
        self._presets = READ_AHEAD_PRESETS
        self.feature = register(ReadAheadFeature(device_id))
        self._friendly_disk_name = friendly_disk_name
        self._device_id = device_id
        self._current_value = None
        self._selected_value = None
        # Must exist BEFORE super().__init__(): its own __init__ already
        # calls refresh_labels() once, and our override below needs it.
        self._custom_lbl = Gtk.Label()
        super().__init__(self.feature, "kf_read_ahead")
        self.set_status_pill_style(False)
        self.btn_permanent.set_visible(False)

        self._preset_box = _new_choice_flowbox()
        self.choices_box.append(self._preset_box)

        custom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._custom_lbl.set_text(T("kf_read_ahead_custom"))
        custom_box.append(self._custom_lbl)
        self._custom_spin = Gtk.SpinButton.new_with_range(self.feature.MIN_KB, self.feature.MAX_KB, 32)
        custom_box.append(self._custom_spin)
        self.choices_box.append(custom_box)
        self._custom_spin.connect("value-changed", self._on_custom_changed)

        self.btn_try.connect("clicked", self._on_try_clicked)
        self.btn_restore.connect("clicked", self._on_restore)
        self._refresh_once()

    def refresh_labels(self):
        super().refresh_labels()
        self.set_title(f"{T('kf_read_ahead_title')} — {self._friendly_disk_name}")
        self.set_subtitle(f"{T('kf_technical_name_device')}: {self._device_id}")
        self._custom_lbl.set_text(T("kf_read_ahead_custom"))

    def _on_custom_changed(self, *_a):
        self._selected_value = int(self._custom_spin.get_value())
        self.set_try_sensitive(self._selected_value != self._current_value, "kf_try_reason_same_value")

    def _select_preset(self, kb):
        self._selected_value = kb
        self._custom_spin.set_value(kb)
        self.set_try_sensitive(self._selected_value != self._current_value, "kf_try_reason_same_value")

    def _on_try_clicked(self, _btn):
        self.clear_error()
        result = self.feature.apply_temporary(self._selected_value)
        if not result.ok:
            self.show_error(result.friendly_message, result.technical_detail)
            return
        self._refresh_once()

    def _on_restore(self, _btn):
        handle_restore_click(self, self.feature.restore, self._refresh_once)

    def _refresh_once(self):
        status = self.feature.probe()
        self.set_support_status(status)
        self.choices_box.set_visible(True)

        current = self.feature.read_current()
        if not current.ok:
            self.show_error(current.friendly_message, current.technical_detail)
            return
        self.clear_error()

        self._current_value = current.value
        self._selected_value = current.value
        friendly = T(self.feature.to_friendly(current.value))
        self.set_status_line(friendly, f"{current.value} KB")
        self._custom_spin.set_value(current.value)

        _flowbox_clear(self._preset_box)
        group_leader = None
        for key, kb in self._presets.items():
            toggle = Gtk.ToggleButton(label=T(f"kf_read_ahead_{key}"), active=(kb == current.value))
            if group_leader is not None:
                toggle.set_group(group_leader)
            toggle.connect("toggled", self._on_preset_toggled, kb)
            self._preset_box.insert(toggle, -1)
            if group_leader is None:
                group_leader = toggle

        self.set_try_sensitive(False, "kf_try_reason_pick_different")

        rec = self.feature.get_record(device_id=self._device_id)
        if rec is not None:
            self.set_initial_value(f"{rec.initial_value} KB" if rec.initial_value is not None else "—")
            self.set_restore_enabled(True)
        else:
            self.set_initial_value("")
            self.set_restore_enabled(False)
        self.set_no_action_needed(rec is None)

    def _on_preset_toggled(self, toggle, kb):
        if toggle.get_active():
            self._select_preset(kb)


def _khz_to_mhz(khz: int) -> int:
    return round(khz / 1000)


def _mhz_to_khz(mhz: int) -> int:
    return int(mhz) * 1000


def _format_freq_mhz(mhz: int) -> str:
    """One value, presentation only — the exact MHz integer stays the
    real technical value everywhere else (SpinButtons, priv_writer,
    state store); this only decides what a friendly summary shows for
    it. >=1000 MHz becomes GHz (up to 2 decimals, locale-appropriate
    decimal separator), below that stays plain MHz."""
    if mhz < 1000:
        return f"{mhz} MHz"
    ghz = mhz / 1000
    text = f"{ghz:.2f}".rstrip("0").rstrip(".")
    if _i18n_mod._lang in ("it", "es", "fr"):
        text = text.replace(".", ",")
    return f"{text} GHz"


def _format_freq_range(mhz_min: int, mhz_max: int) -> str:
    """A range where each end independently gets MHz or GHz — if both
    ends land in GHz the unit is shown only once, at the end, matching
    normal range typography ("1,75–5,09 GHz" not "1,75 GHz–5,09 GHz")."""
    lo = _format_freq_mhz(mhz_min)
    hi = _format_freq_mhz(mhz_max)
    if lo.endswith("GHz") and hi.endswith("GHz"):
        return f"{lo[:-4]}–{hi}"
    return f"{lo}–{hi}"


class CpuFrequencyLimitsRow(KernelFeatureRow):
    """
    Every real cpufreq policy at once — never just cpu0. Profiles are
    computed from THIS machine's own real cpuinfo_min_freq/
    cpuinfo_max_freq (compute_profile_range in cpu.py), never a
    hardcoded MHz number. Deliberately never called "overclock": every
    value stays within what the hardware itself already allows.
    """
    def __init__(self):
        from core.kernel_features.cpu import CpuFrequencyLimitsFeature, compute_profile_range, CPU_FREQ_PROFILES
        self._compute_profile_range = compute_profile_range
        self._profile_names = list(CPU_FREQ_PROFILES.keys())
        self._current_value = None  # {"min": khz, "max": khz} — the chosen range
        self._selected_value = None
        self._hw_bounds = None
        # Must exist BEFORE super().__init__(): its own __init__ already
        # calls refresh_labels() once, and our override below needs them.
        self._min_lbl = Gtk.Label()
        self._max_lbl = Gtk.Label()

        feature = register(CpuFrequencyLimitsFeature())
        super().__init__(feature, "kf_cpu_freq_limits")
        self.set_status_pill_style(False)
        self.btn_permanent.set_visible(False)

        self._range_lbl = Gtk.Label(wrap=True, xalign=0)
        self._range_lbl.add_css_class("sysinfo-value")
        self.choices_box.append(self._range_lbl)

        self._hw_lbl = Gtk.Label(wrap=True, xalign=0)
        self._hw_lbl.add_css_class("sysinfo-value-sub")
        self.choices_box.append(self._hw_lbl)

        self._mixed_lbl = Gtk.Label(wrap=True, xalign=0)
        self._mixed_lbl.add_css_class("desc-con")
        self._mixed_lbl.set_visible(False)
        self.choices_box.append(self._mixed_lbl)

        self._profile_box = _new_choice_flowbox()
        self.choices_box.append(self._profile_box)

        custom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._min_lbl.set_text(T("kf_cpu_freq_min_label"))
        self._max_lbl.set_text(T("kf_cpu_freq_max_label"))
        self._min_spin = Gtk.SpinButton.new_with_range(0, 10000, 100)
        self._max_spin = Gtk.SpinButton.new_with_range(0, 10000, 100)
        self._min_spin.set_digits(0)
        self._max_spin.set_digits(0)
        custom_box.append(self._min_lbl)
        custom_box.append(self._min_spin)
        custom_box.append(self._max_lbl)
        custom_box.append(self._max_spin)
        self.choices_box.append(custom_box)
        self._min_spin.connect("value-changed", self._on_custom_changed)
        self._max_spin.connect("value-changed", self._on_custom_changed)

        self.btn_try.connect("clicked", self._on_try_clicked)
        self.btn_restore.connect("clicked", self._on_restore)
        self._refresh_once()

    def refresh_labels(self):
        super().refresh_labels()
        self._min_lbl.set_text(T("kf_cpu_freq_min_label"))
        self._max_lbl.set_text(T("kf_cpu_freq_max_label"))

    def _on_custom_changed(self, *_a):
        self._selected_value = {
            "min": _mhz_to_khz(self._min_spin.get_value()),
            "max": _mhz_to_khz(self._max_spin.get_value()),
        }
        self.set_try_sensitive(self._selected_value != self._current_value, "kf_try_reason_same_value")

    def _select_profile(self, profile: str):
        if self._hw_bounds is None:
            return
        hw_min, hw_max = self._hw_bounds
        if profile == "keep":
            self._selected_value = dict(self._current_value) if self._current_value else None
        else:
            rng = self._compute_profile_range(profile, hw_min, hw_max)
            self._selected_value = {"min": rng[0], "max": rng[1]} if rng else None
        if self._selected_value:
            self._min_spin.set_value(_khz_to_mhz(self._selected_value["min"]))
            self._max_spin.set_value(_khz_to_mhz(self._selected_value["max"]))
        self.set_try_sensitive(self._selected_value != self._current_value, "kf_try_reason_same_value")

    def _on_try_clicked(self, _btn):
        self.clear_error()
        result = self.feature.apply_temporary(self._selected_value)
        if not result.ok:
            self.show_error(result.friendly_message, result.technical_detail)
            return
        self._refresh_once()

    def _on_restore(self, _btn):
        handle_restore_click(self, self.feature.restore, self._refresh_once)

    def _refresh_once(self):
        status = self.feature.probe()
        self.set_support_status(status)
        self.choices_box.set_visible(True)

        current = self.feature.read_current()
        if not current.ok:
            self.show_error(current.friendly_message, current.technical_detail)
            return
        self.clear_error()

        policies = current.value["policies"]
        self._hw_bounds = self.feature.hw_bounds(policies)
        distinct = {(p["min"], p["max"]) for p in policies}
        mixed = len(distinct) > 1
        self._mixed_lbl.set_text(T("kf_cpu_freq_mixed_note"))
        self._mixed_lbl.set_visible(mixed)

        if mixed:
            rep_min = min(p["min"] for p in policies)
            rep_max = max(p["max"] for p in policies)
        else:
            rep_min, rep_max = next(iter(distinct))
        self._current_value = {"min": rep_min, "max": rep_max}
        self._selected_value = dict(self._current_value)

        min_mhz, max_mhz = _khz_to_mhz(rep_min), _khz_to_mhz(rep_max)
        range_text = _format_freq_range(min_mhz, max_mhz)
        # Friendly summary can say "1,75–5,09 GHz"; the technical value
        # alongside it (shown behind "valore tecnico:") always stays the
        # exact MHz figures, never rounded into GHz.
        self.set_status_line(range_text, f"{min_mhz}–{max_mhz} MHz")
        self._range_lbl.set_text(f"{T('kf_cpu_freq_current_range')}: {range_text}")
        if self._hw_bounds:
            hw_min, hw_max = self._hw_bounds
            self._hw_lbl.set_text(
                f"{T('kf_cpu_freq_hw_range')}: {_format_freq_range(_khz_to_mhz(hw_min), _khz_to_mhz(hw_max))}")
        self._min_spin.set_value(_khz_to_mhz(rep_min))
        self._max_spin.set_value(_khz_to_mhz(rep_max))
        if self._hw_bounds:
            hw_min, hw_max = self._hw_bounds
            self._min_spin.set_range(_khz_to_mhz(hw_min), _khz_to_mhz(hw_max))
            self._max_spin.set_range(_khz_to_mhz(hw_min), _khz_to_mhz(hw_max))

        _flowbox_clear(self._profile_box)
        group_leader = None
        for name in ["keep"] + self._profile_names:
            key = f"kf_cpu_freq_profile_{name}"
            toggle = Gtk.ToggleButton(label=T(key))
            if group_leader is not None:
                toggle.set_group(group_leader)
            toggle.connect("toggled", self._on_profile_toggled, name)
            self._profile_box.insert(toggle, -1)
            if group_leader is None:
                group_leader = toggle
                toggle.set_active(True)

        self.set_try_sensitive(False, "kf_try_reason_pick_different")

        rec = self.feature.get_record()
        if rec is not None and isinstance(rec.initial_value, dict):
            first = next(iter(rec.initial_value.values()), None)
            if first:
                self.set_initial_value(_format_freq_range(_khz_to_mhz(first['min']), _khz_to_mhz(first['max'])))
            self.set_restore_enabled(True)
        else:
            self.set_initial_value("")
            self.set_restore_enabled(False)
        self.set_no_action_needed(rec is None)

    def _on_profile_toggled(self, toggle, profile):
        if toggle.get_active():
            self._select_profile(profile)


class DmesgRestrictRow(ChoiceKernelFeatureRow):
    def __init__(self):
        feature = register(DmesgRestrictFeature())
        super().__init__(feature, "kf_dmesg_restrict")

    def _protected_values(self) -> set:
        # "0" (readable by anyone) is never "already protected".
        return {"1"}


class KptrRestrictRow(ChoiceKernelFeatureRow):
    def __init__(self):
        feature = register(KptrRestrictFeature())
        super().__init__(feature, "kf_kptr_restrict")

    def _protected_values(self) -> set:
        # Both "1" (hidden from unprivileged users) and "2" (hidden from
        # everyone) are real protection — only "0" is unprotected.
        return {"1", "2"}


class PtraceScopeRow(ChoiceKernelFeatureRow):
    def __init__(self):
        feature = register(PtraceScopeFeature())
        super().__init__(feature, "kf_ptrace_scope")

    def _protected_values(self) -> set:
        # 1 = standard desktop protection, 2 = more restrictive — both
        # are genuinely protected states. 0 (unrestricted) never shows
        # the note, and 3 is never exposed as a choice at all.
        return {"1", "2"}


class ProtectedPathsRow(KernelFeatureRow):
    """
    fs.protected_symlinks/hardlinks/fifos/regular — applied as ONE
    atomic group (see ProtectedPathsFeature/ProtectedPathsWriter), never
    four independent switches. Exactly two write actions are offered
    ("full protection" / "disable"), never a per-key toggle — the four
    keys always move together.
    """
    def __init__(self):
        self._current_state = None
        self._selected_state = None
        self._already_note = Gtk.Label(wrap=True, xalign=0)
        self._already_note.add_css_class("desc-pro")
        self._already_note.set_visible(False)
        self._detail_lbl = Gtk.Label(wrap=True, xalign=0)
        self._detail_lbl.add_css_class("sysinfo-value-sub")

        feature = register(ProtectedPathsFeature())
        super().__init__(feature, "kf_protected_paths")
        self.btn_permanent.set_visible(False)

        self.choices_box.append(self._detail_lbl)
        self.choices_box.append(self._already_note)

        self._choice_box = _new_choice_flowbox()
        self.choices_box.append(self._choice_box)

        self.btn_try.connect("clicked", self._on_try_clicked)
        self.btn_restore.connect("clicked", self._on_restore)
        self._refresh_once()

    def _select_state(self, target: str):
        self._selected_state = target
        self._already_note.set_text(T("kf_already_protected_note"))
        self._already_note.set_visible(target == self._current_state)
        self.set_try_sensitive(target != self._current_state, "kf_try_reason_same_value")

    def _on_try_clicked(self, _btn):
        self.clear_error()
        result = self.feature.apply_temporary(self._selected_state)
        if not result.ok:
            self.show_error(result.friendly_message, result.technical_detail)
            return
        self._refresh_once()

    def _on_restore(self, _btn):
        handle_restore_click(self, self.feature.restore, self._refresh_once)

    def _refresh_once(self):
        status = self.feature.probe()
        self.set_support_status(status)
        self.choices_box.set_visible(True)

        current = self.feature.read_current()
        if not current.ok:
            self.show_error(current.friendly_message, current.technical_detail)
            return
        self.clear_error()

        state = self.feature.state(current.value)
        self._current_state = state if state in ("full", "off") else None
        self._selected_state = self._current_state
        self.set_status_line(T(self.feature.to_friendly(state)))
        self.set_status_variant("success" if state == "full" else "neutral")

        detail_bits = []
        for key, val in current.value.items():
            label_key = f"kf_protected_paths_key_{key.replace('protected_', '')}"
            detail_bits.append(f"{T(label_key)}: {val}")
        self._detail_lbl.set_text(" · ".join(detail_bits))
        self._already_note.set_visible(False)

        _flowbox_clear(self._choice_box)
        t_full = Gtk.ToggleButton(label=T("kf_protected_paths_enable_btn"), active=(state == "full"))
        t_off = Gtk.ToggleButton(label=T("kf_protected_paths_disable_btn"), active=(state == "off"))
        t_off.set_group(t_full)
        t_full.connect("toggled", self._on_choice_toggled, "full")
        t_off.connect("toggled", self._on_choice_toggled, "off")
        self._choice_box.insert(t_full, -1)
        self._choice_box.insert(t_off, -1)

        self.set_try_sensitive(False, "kf_try_reason_pick_different")

        rec = self.feature.get_record()
        if rec is not None:
            initial_state = self.feature.state(rec.initial_value) if isinstance(rec.initial_value, dict) else "—"
            self.set_initial_value(T(self.feature.to_friendly(initial_state)) if initial_state != "—" else "—")
            self.set_restore_enabled(True)
        else:
            self.set_initial_value("")
            self.set_restore_enabled(False)
        self.set_no_action_needed(rec is None)

    def _on_choice_toggled(self, toggle, target):
        if toggle.get_active():
            self._select_state(target)


def _add_kernel_row(group, row, category: str):
    """Shared per-row finishing touch for the v3 redesign: a category
    IconBadge as a real Adw.ExpanderRow prefix (public API, no row
    subclass touched) plus the modern button hierarchy on the row's
    own already-existing btn_try/btn_permanent/btn_restore."""
    row.add_prefix(IconBadge("emblem-system-symbolic", category=category))
    style_kernel_feature_row_buttons(row)
    group.add(row)


class KernelPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("emblem-system-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()
        # Wider than the default 600px: this page's explanations need
        # more breathing room. Still responsive — below the tightening
        # threshold it just uses the full available width.
        _widen_preferences_clamp(self, maximum_size=900, tightening_threshold=700)

        # Single source of truth for "what does this page actually
        # build right now" — the same inventory the Home page's
        # "Funzioni Kernel" card reads, so the two can never again
        # disagree about what's on this page. Every conditional row
        # below is gated on membership in this same set instead of a
        # second, independently-maintained probe() check — one place
        # decides, this page and Home both just read the decision.
        inv_ids = {e.feature_id for e in build_kernel_inventory()}

        header = PageHeader(
            "emblem-system-symbolic", T("tab_kernel"), T("ds_kernel_header_desc"),
            category="kernel",
        )
        header_count = Gtk.Label()
        header_count.add_css_class("ds-pill")
        header_count.add_css_class("ds-pill-info")
        header.append(header_count)
        self.add(wrap_in_preferences_group(header))

        # v3: dedicated title ("Pressione e stato") instead of the
        # generic "Panoramica" — a new additive i18n key, the original
        # grp_kernel_overview string is untouched and still correct
        # wherever else it might be used.
        g1 = make_section("ds_kernel_group_pressure")
        self.add(g1)

        intro = InfoTile(T("kf_intro_title"), T("kf_intro"))
        intro.set_margin_bottom(6)
        on_change(lambda: (intro.set_title(T("kf_intro_title")), intro.set_body(T("kf_intro"))))
        g1.add(intro)

        # Every FeatureCard actually added to the page in this
        # __init__ is tracked here — the header count below is simply
        # this list's length, never a separately-computed number that
        # could drift from what's really on screen.
        built_rows = []

        def add_row(group, row, category):
            _add_kernel_row(group, row, category)
            built_rows.append(row)

        add_row(g1, PSIRow(), "kernel")

        g_cpu = make_group("grp_kernel_cpu")
        self.add(g_cpu)
        add_row(g_cpu, TurboBoostRow(), "kernel")
        add_row(g_cpu, GovernorRow(), "kernel")
        # EPP only exists at all on drivers that expose it (intel_pstate
        # active mode, amd-pstate) — per spec, shown only if truly
        # supported, not as a permanently-greyed-out row.
        if "cpu.epp" in inv_ids:
            add_row(g_cpu, EPPRow(), "kernel")
        # cpufreq min/max limits — only if at least one real policy
        # exposes scaling_min_freq/scaling_max_freq. One card no matter
        # how many real policies this machine has underneath it.
        if "cpu.frequency_limits" in inv_ids:
            add_row(g_cpu, CpuFrequencyLimitsRow(), "kernel")

        g2 = make_group("grp_kernel_memory")
        self.add(g2)
        add_row(g2, SwappinessRow(), "memory")
        add_row(g2, THPRow(), "memory")
        add_row(g2, ZramRow(), "memory")
        # Same reasoning as EPP: only shown if the kernel really has zswap.
        if "memory.zswap" in inv_ids:
            add_row(g2, ZswapRow(), "memory")
        # MGLRU — only if /sys/kernel/mm/lru_gen/enabled really exists,
        # never deduced from the kernel version alone.
        if "memory.mglru" in inv_ids:
            add_row(g2, MGLRURow(), "memory")
        if "memory.swap_readahead" in inv_ids:
            add_row(g2, SwapReadaheadRow(), "memory")

        disks = list_real_disks()
        if disks:
            g3 = make_group("grp_kernel_storage")
            self.add(g3)
            for device_id, friendly_name in disks:
                add_row(g3, IOSchedulerRow(device_id, friendly_name), "disk")
            for device_id, friendly_name in disks:
                if f"storage.read_ahead:{device_id}" in inv_ids:
                    add_row(g3, ReadAheadRow(device_id, friendly_name), "disk")

        if "network.tcp_congestion_control" in inv_ids:
            g4 = make_group("kf_group_network")
            self.add(g4)
            add_row(g4, TcpCongestionRow(), "network")

        # Second block, added only once the first five functions above
        # are stable — dmesg_restrict/kptr_restrict/ptrace_scope/
        # protected_paths. Never applied automatically; the group only
        # appears at all if at least one of the four is really usable.
        security_checks = [
            ("security.dmesg_restrict", DmesgRestrictRow),
            ("security.kptr_restrict", KptrRestrictRow),
            ("security.ptrace_scope", PtraceScopeRow),
            ("security.protected_paths", ProtectedPathsRow),
        ]
        if any(feature_id in inv_ids for feature_id, _cls in security_checks):
            g5 = make_group("grp_kernel_security")
            self.add(g5)
            for feature_id, row_cls in security_checks:
                if feature_id in inv_ids:
                    add_row(g5, row_cls(), "security-ok")

        # The header count is set only now, from the rows really built
        # above — never a static number, never a parallel computation.
        header_count.set_text(T("ds_kernel_header_count").format(n=len(built_rows)))
        self._kernel_card_count = len(built_rows)

    def _refresh_title(self):
        self.set_title(T("tab_kernel"))
