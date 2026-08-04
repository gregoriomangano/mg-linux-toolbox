"""
Tests for the 2026-08-03 PSI fix: the Panoramica (OverviewPage) and the
Kernel page's PSIRow both need to actually refresh while visible, stop
polling while hidden, never stack a second timer, and apply the
hysteresis-gated bucket to their widgets (chip CSS class, indicator CSS
class, badge text/CSS, phrase text) — not just compute it internally.

GTK is required to construct these (real Adw/Gtk widgets), but nothing
here touches real /proc data for the scenario-driving reads: a fake PSI
feature is swapped in after construction so every test is deterministic
regardless of the real machine's live pressure numbers. _on_map/_on_unmap/
_on_timeout are called directly as plain methods (not through the actual
GTK "map"/"unmap" signals, which need a realized top-level window) —
that's the same logic GTK would invoke, just without needing a real
window to trigger it.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw
Adw.init()

from core.i18n import T
from core.kernel_features.base import OpResult, SupportStatus
from core.kernel_features.monitoring import PSIHysteresis
from ui.pages.page_overview import OverviewPage
from ui.pages.page_kernel import PSIRow


class _FakePSIFeature:
    """Stands in for PSIFeature: same read_current() shape, fully
    controllable avg10/avg60 per resource, no real /proc access."""

    def __init__(self):
        self.avg10 = {"cpu": 0.0, "memory": 0.0, "io": 0.0}
        self.avg60 = {"cpu": 0.0, "memory": 0.0, "io": 0.0}
        self.avg300 = {"cpu": 99.0, "memory": 99.0, "io": 99.0}  # deliberately stale/high

    def read_current(self):
        value = {
            r: {"some": {"avg10": self.avg10[r], "avg60": self.avg60[r], "avg300": self.avg300[r]}}
            for r in ("cpu", "memory", "io")
        }
        return OpResult(True, value=value)


class _FakeCpuIdleTracker:
    """Always reports 'unknown' (None) unless a test overrides it —
    deterministic stand-in so a confirmed-high io bucket always stays
    'high' here (classify_disk_pressure never downgrades on missing
    corroboration data), regardless of the real host's live CPU load."""
    def __init__(self, idle_pct=None):
        self.idle_pct = idle_pct

    def sample(self):
        return self.idle_pct


def _fresh_overview_page():
    page = OverviewPage()
    # The real construction may have started a real timer against the
    # real /proc if this machine supports PSI — cancel it and take over
    # deterministically for the test.
    if page._psi_timeout_id is not None:
        page._on_psi_unmap(page)
    page._psi_supported = True
    page._psi_feature = _FakePSIFeature()
    page._psi_hysteresis = {r: PSIHysteresis() for r in ("cpu", "memory", "io")}
    page._cpu_idle_tracker = _FakeCpuIdleTracker()
    return page


class OverviewPagePSIRefreshTests(unittest.TestCase):
    def test_timer_started_once_on_map(self):
        page = _fresh_overview_page()
        self.assertIsNone(page._psi_timeout_id)
        page._on_psi_map(page)
        self.assertIsNotNone(page._psi_timeout_id)

    def test_timer_not_duplicated_if_map_fires_twice(self):
        page = _fresh_overview_page()
        page._on_psi_map(page)
        first_id = page._psi_timeout_id
        page._on_psi_map(page)
        self.assertEqual(page._psi_timeout_id, first_id)

    def test_timer_stopped_when_page_not_visible(self):
        page = _fresh_overview_page()
        page._on_psi_map(page)
        self.assertIsNotNone(page._psi_timeout_id)
        page._on_psi_unmap(page)
        self.assertIsNone(page._psi_timeout_id)

    def test_updates_when_ticked_while_visible(self):
        page = _fresh_overview_page()
        page._psi_feature.avg10["io"] = 77.52
        page._psi_feature.avg60["io"] = 72.65
        page._on_psi_timeout()
        page._on_psi_timeout()  # 2nd consecutive high sample -> critical
        self.assertTrue(page._psi_chip_labels["io"].has_css_class("mgv2-chip-high"))
        self.assertEqual(page._psi_chip_labels["io"].get_text(), T("mg_psi_bucket_high"))
        self.assertEqual(page._psi_phrase_labels["io"].get_text(), T("kf_psi_io_high"))

    def test_color_returns_to_normal_after_recovery(self):
        page = _fresh_overview_page()
        page._psi_feature.avg10["io"] = 77.52
        page._psi_feature.avg60["io"] = 72.65
        page._on_psi_timeout()
        page._on_psi_timeout()
        self.assertTrue(page._psi_chip_labels["io"].has_css_class("mgv2-chip-high"))
        self.assertTrue(page._state_badge.has_css_class("high"))

        # System calmed down (matches the bug report's recovered
        # reading: avg10=0.00 avg60=0.16) — avg300 is left deliberately
        # stale/high in the fake feature and must be ignored.
        page._psi_feature.avg10["io"] = 0.0
        page._psi_feature.avg60["io"] = 0.16
        page._on_psi_timeout()
        page._on_psi_timeout()  # 2nd consecutive low sample -> exits critical

        self.assertFalse(page._psi_chip_labels["io"].has_css_class("mgv2-chip-high"))
        self.assertTrue(page._psi_chip_labels["io"].has_css_class("mgv2-chip-low"))
        self.assertFalse(page._state_badge.has_css_class("high"))

    def test_general_message_returns_to_normal_after_recovery(self):
        page = _fresh_overview_page()
        page._psi_feature.avg10["io"] = 77.52
        page._psi_feature.avg60["io"] = 72.65
        page._on_psi_timeout()
        page._on_psi_timeout()
        self.assertEqual(page._state_badge.get_text(), T("ov2_state_high"))

        page._psi_feature.avg10["io"] = 0.0
        page._psi_feature.avg60["io"] = 0.0
        page._on_psi_timeout()
        page._on_psi_timeout()
        self.assertEqual(page._state_badge.get_text(), T("ov2_state_low"))

    def test_lone_idle_blip_does_not_trigger_the_red_general_banner(self):
        """2026-08-05: the exact reported scenario — avg10/avg60 high on
        'io' (a VM doing virtual-disk I/O) while the CPU sits at ~3%
        (97% idle) and only one process is actually blocked. The red
        "Una risorsa è temporaneamente sotto pressione" banner must not
        fire; the io sub-card should read as the softer 'moderate'
        state instead."""
        page = _fresh_overview_page()
        page._cpu_idle_tracker = _FakeCpuIdleTracker(idle_pct=97.0)
        with mock.patch(
            "core.kernel_features.disk_pressure_context.count_blocked_processes",
            return_value=1,
        ):
            page._psi_feature.avg10["io"] = 77.52
            page._psi_feature.avg60["io"] = 72.65
            page._on_psi_timeout()
            page._on_psi_timeout()
        # Downgraded to the softer "moderate" state (still visible,
        # never the "sotto pressione" red banner) — not silently
        # dropped to "low", since avg10/avg60 genuinely are elevated.
        self.assertEqual(page._state_badge.get_text(), T("ov2_state_moderate"))
        self.assertFalse(page._state_badge.has_css_class("high"))
        self.assertTrue(page._psi_chip_labels["io"].has_css_class("mgv2-chip-moderate"))
        self.assertEqual(page._psi_phrase_labels["io"].get_text(), T("kf_psi_io_moderate"))

    def test_real_system_wide_pressure_still_shows_the_red_banner(self):
        """Corroborating signals both point at a genuine, broader
        slowdown (several blocked processes, CPU not idle) — must stay
        red, never softened."""
        page = _fresh_overview_page()
        page._cpu_idle_tracker = _FakeCpuIdleTracker(idle_pct=15.0)
        with mock.patch(
            "core.kernel_features.disk_pressure_context.count_blocked_processes",
            return_value=6,
        ):
            page._psi_feature.avg10["io"] = 77.52
            page._psi_feature.avg60["io"] = 72.65
            page._on_psi_timeout()
            page._on_psi_timeout()
        self.assertEqual(page._state_badge.get_text(), T("ov2_state_high"))
        self.assertTrue(page._state_badge.has_css_class("high"))
        self.assertTrue(page._psi_chip_labels["io"].has_css_class("mgv2-chip-high"))


def _fresh_psi_row():
    row = PSIRow()
    if row._timeout_id is not None:
        row._on_unmap(row)
    # KernelFeatureRow.__init__ subscribes row.refresh_labels to i18n's
    # on_change() forever (no unsubscribe) — swapping out row.feature
    # entirely would leave that closure holding a fake object missing
    # real attributes (technical_name, etc.) for the rest of the test
    # process. Only the two methods this test actually drives are
    # monkeypatched on the real PSIFeature instance instead.
    fake = _FakePSIFeature()
    row.feature.read_current = fake.read_current
    row.feature.probe = lambda: SupportStatus.SUPPORTED_READ_ONLY
    row.feature._fake = fake  # convenience handle for the test body
    row._hysteresis = {r: PSIHysteresis() for r in ("cpu", "memory", "io")}
    row._io_was_critical = False
    row._cpu_idle_tracker = _FakeCpuIdleTracker()
    return row


class PSIRowRefreshTests(unittest.TestCase):
    def test_timer_started_once_on_map_and_not_duplicated(self):
        row = _fresh_psi_row()
        self.assertIsNone(row._timeout_id)
        row._on_map(row)
        first_id = row._timeout_id
        self.assertIsNotNone(first_id)
        row._on_map(row)
        self.assertEqual(row._timeout_id, first_id)

    def test_timer_stopped_when_row_not_visible(self):
        row = _fresh_psi_row()
        row._on_map(row)
        self.assertIsNotNone(row._timeout_id)
        row._on_unmap(row)
        self.assertIsNone(row._timeout_id)

    def test_restored_note_shown_once_after_recovery(self):
        row = _fresh_psi_row()
        row.feature._fake.avg10["io"] = 77.52
        row.feature._fake.avg60["io"] = 72.65
        row._refresh_once()
        row._refresh_once()  # now critical
        self.assertTrue(row._io_spike_note.get_visible())
        self.assertEqual(row._io_spike_note.get_text(), T("kf_psi_io_spike_note"))

        row.feature._fake.avg10["io"] = 0.0
        row.feature._fake.avg60["io"] = 0.0
        row._refresh_once()  # 1st low sample: not yet exited
        self.assertTrue(row._io_spike_note.get_visible())
        row._refresh_once()  # 2nd low sample: exits critical -> restored note, once
        self.assertTrue(row._io_spike_note.get_visible())
        self.assertEqual(row._io_spike_note.get_text(), T("kf_psi_io_restored"))

        row._refresh_once()  # next tick: nothing to announce anymore
        self.assertFalse(row._io_spike_note.get_visible())

    def test_avg300_alone_does_not_keep_disk_line_critical(self):
        row = _fresh_psi_row()
        row.feature._fake.avg300["io"] = 61.16  # exact stale value from the bug report
        row.feature._fake.avg10["io"] = 0.0
        row.feature._fake.avg60["io"] = 0.16
        row._refresh_once()
        row._refresh_once()
        self.assertFalse(row._hysteresis["io"].critical)
        self.assertIn(T("kf_psi_io_low"), row._resource_labels["io"].get_text())


if __name__ == "__main__":
    unittest.main()
