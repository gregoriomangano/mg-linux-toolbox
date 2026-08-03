"""
GTK-level tests for DiskActivityPage (ui/pages/page_disk_activity.py):
timer lifecycle (started once on map, never duplicated, stopped when
hidden/paused), live updates while visible, and the "no dominant
process" / "quiet disk" messaging. A fake sampler and fake PSI feature
are swapped in after construction so nothing here depends on the real
machine's live disk activity.
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
from core.kernel_features.base import OpResult
from core.kernel_features.monitoring import PSIHysteresis
from core.kernel_features.disk_activity import DiskActivitySnapshot, DiskSample, ProcessSample
from ui.pages.page_disk_activity import DiskActivityPage, _has_dominant_process


class _FakePSIFeature:
    def __init__(self):
        self.avg10 = 0.0
        self.avg60 = 0.0
        self.avg300 = 0.0

    def read_current(self):
        return OpResult(True, value={"io": {"some": {
            "avg10": self.avg10, "avg60": self.avg60, "avg300": self.avg300,
        }}})


class _FakeSampler:
    def __init__(self):
        self.snapshot = DiskActivitySnapshot()

    def sample(self):
        return self.snapshot


def _fresh_page():
    page = DiskActivityPage(navigate_callback=lambda _t: None)
    if page._timeout_id is not None:
        page._on_unmap(page)
    page._psi_supported = True
    page._psi_feature = _FakePSIFeature()
    page._psi_hysteresis = PSIHysteresis()
    page._sampler = _FakeSampler()
    return page


def _apply_fake_sample(page):
    """Apply the fake snapshot synchronously, as GLib.idle_add would."""
    page._refresh_general()
    page._on_sample_finished(page._sample_generation, page._sampler.snapshot, None)


class DiskActivityPageTimerTests(unittest.TestCase):
    def test_timer_started_once_on_map(self):
        page = _fresh_page()
        self.assertIsNone(page._timeout_id)
        page._on_map(page)
        self.assertIsNotNone(page._timeout_id)

    def test_timer_not_duplicated_on_repeated_map(self):
        page = _fresh_page()
        page._on_map(page)
        first_id = page._timeout_id
        page._on_map(page)
        self.assertEqual(page._timeout_id, first_id)

    def test_timer_stopped_when_page_hidden(self):
        page = _fresh_page()
        page._on_map(page)
        page._on_unmap(page)
        self.assertIsNone(page._timeout_id)

    def test_timer_stopped_when_paused(self):
        page = _fresh_page()
        page._on_map(page)
        self.assertIsNotNone(page._timeout_id)
        page._pause_btn.set_active(True)
        self.assertIsNone(page._timeout_id)

    def test_timer_resumes_after_unpausing_while_visible(self):
        page = _fresh_page()
        page._on_map(page)
        page._pause_btn.set_active(True)
        self.assertIsNone(page._timeout_id)
        # Simulate the page actually being mapped/visible right now —
        # get_mapped() would be True in a real running window.
        page.get_mapped = lambda: True
        page._pause_btn.set_active(False)
        self.assertIsNotNone(page._timeout_id)


class DiskActivityPageRefreshTests(unittest.TestCase):
    def test_proc_walk_is_dispatched_to_a_worker_not_run_inline(self):
        page = _fresh_page()
        created = []

        class FakeThread:
            def __init__(self, **kwargs):
                created.append(kwargs)

            def start(self):
                pass

        with mock.patch("ui.pages.page_disk_activity.threading.Thread", FakeThread), \
             mock.patch.object(page._sampler, "sample") as sample:
            page._request_sample()
            page._request_sample()  # one worker at a time
        sample.assert_not_called()
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0]["daemon"])

    def test_multiple_disks_get_their_own_card(self):
        page = _fresh_page()
        page._sampler.snapshot = DiskActivitySnapshot(disks=[
            DiskSample("nvme0n1", "NVMe Samsung", "NVMe", 1024.0, 0.0, 0),
            DiskSample("sda", "HDD Seagate", "HDD", 0.0, 2048.0, 1),
        ])
        _apply_fake_sample(page)
        self.assertEqual(set(page._disk_rows.keys()), {"nvme0n1", "sda"})
        self.assertIn("1.0 KB/s", page._disk_rows["nvme0n1"]["read"].get_text())
        self.assertIn(T("da_disk_ops_in_progress"), page._disk_rows["sda"]["ops"].get_text())

    def test_removed_disk_drops_its_card(self):
        page = _fresh_page()
        page._sampler.snapshot = DiskActivitySnapshot(disks=[
            DiskSample("sda", "HDD Seagate", "HDD", 100.0, 0.0, 0),
        ])
        _apply_fake_sample(page)
        self.assertIn("sda", page._disk_rows)
        page._sampler.snapshot = DiskActivitySnapshot(disks=[])
        _apply_fake_sample(page)
        self.assertNotIn("sda", page._disk_rows)
        self.assertTrue(page._disks_empty_note.get_visible())

    def test_process_with_reads_only_appears_in_reads_column(self):
        page = _fresh_page()
        page._psi_feature.avg10 = 0.0
        page._sampler.snapshot = DiskActivitySnapshot(
            processes=[ProcessSample(pid=1, name="reader", read_bps=5000.0, write_bps=0.0)]
        )
        _apply_fake_sample(page)
        reads_text = _box_texts(page._reads_list_box)
        writes_text = _box_texts(page._writes_list_box)
        self.assertTrue(any("reader" in t for t in reads_text))
        self.assertFalse(any("reader" in t for t in writes_text))

    def test_process_with_writes_only_appears_in_writes_column(self):
        page = _fresh_page()
        page._sampler.snapshot = DiskActivitySnapshot(
            processes=[ProcessSample(pid=1, name="writer", read_bps=0.0, write_bps=5000.0)]
        )
        _apply_fake_sample(page)
        writes_text = _box_texts(page._writes_list_box)
        self.assertTrue(any("writer" in t for t in writes_text))

    def test_quiet_disk_shows_idle_message(self):
        page = _fresh_page()
        page._psi_feature.avg10 = 0.0
        page._psi_feature.avg60 = 0.0
        page._sampler.snapshot = DiskActivitySnapshot(processes=[])
        _apply_fake_sample(page)
        _apply_fake_sample(page)
        self.assertTrue(page._processes_note.get_visible())
        self.assertEqual(page._processes_note.get_text(), T("da_processes_idle"))

    def test_elevated_pressure_without_dominant_process_shows_note(self):
        page = _fresh_page()
        page._psi_feature.avg10 = 2.0  # "moderate" bucket, no hysteresis gating needed
        page._psi_feature.avg60 = 2.0
        page._sampler.snapshot = DiskActivitySnapshot(processes=[
            ProcessSample(pid=1, name="a", read_bps=100.0, write_bps=0.0),
            ProcessSample(pid=2, name="b", read_bps=90.0, write_bps=0.0),
        ])
        _apply_fake_sample(page)
        _apply_fake_sample(page)
        self.assertTrue(page._processes_note.get_visible())
        self.assertEqual(page._processes_note.get_text(), T("da_processes_no_dominant"))

    def test_elevated_pressure_with_dominant_process_shows_no_note(self):
        page = _fresh_page()
        page._psi_feature.avg10 = 2.0
        page._psi_feature.avg60 = 2.0
        page._sampler.snapshot = DiskActivitySnapshot(processes=[
            ProcessSample(pid=1, name="hog", read_bps=100000.0, write_bps=0.0),
            ProcessSample(pid=2, name="tiny", read_bps=1.0, write_bps=0.0),
        ])
        _apply_fake_sample(page)
        _apply_fake_sample(page)
        self.assertFalse(page._processes_note.get_visible())

    def test_unreadable_processes_note_shown_when_reported(self):
        page = _fresh_page()
        page._sampler.snapshot = DiskActivitySnapshot(unreadable_process_count=5)
        _apply_fake_sample(page)
        self.assertTrue(page._unreadable_note.get_visible())

    def test_unavailable_proc_or_sys_source_is_reported_without_crash(self):
        page = _fresh_page()
        page._sampler.snapshot = DiskActivitySnapshot(
            disk_source_available=False, process_source_available=False,
        )
        _apply_fake_sample(page)
        self.assertTrue(page._sampling_note.get_visible())
        self.assertEqual(page._sampling_note.get_text(), T("da_source_unavailable"))

    def test_stale_worker_result_is_not_applied_after_unmap(self):
        page = _fresh_page()
        stale_generation = page._sample_generation
        page._on_unmap(page)
        snapshot = DiskActivitySnapshot(disks=[
            DiskSample("sda", "HDD Test", "HDD", 1.0, 0.0, 0),
        ])
        page._on_sample_finished(stale_generation, snapshot, None)
        self.assertNotIn("sda", page._disk_rows)

    def test_psi_high_returns_to_low_updates_level_chip(self):
        page = _fresh_page()
        page._psi_feature.avg10 = 77.52
        page._psi_feature.avg60 = 72.65
        page._refresh_general()
        page._refresh_general()  # 2 consecutive high samples -> critical
        self.assertEqual(page._level_chip.get_text(), T("da_level_very_elevated"))

        page._psi_feature.avg10 = 0.0
        page._psi_feature.avg60 = 0.16
        page._refresh_general()
        page._refresh_general()  # 2 consecutive low samples -> exits
        self.assertEqual(page._level_chip.get_text(), T("da_level_none"))


class DominantProcessHelperTests(unittest.TestCase):
    def test_no_processes_is_not_dominant(self):
        self.assertFalse(_has_dominant_process([]))

    def test_single_large_process_is_dominant(self):
        procs = [ProcessSample(1, "big", 100000.0, 0.0), ProcessSample(2, "small", 10.0, 0.0)]
        self.assertTrue(_has_dominant_process(procs))

    def test_similar_sized_processes_are_not_dominant(self):
        procs = [ProcessSample(1, "a", 100.0, 0.0), ProcessSample(2, "b", 90.0, 0.0)]
        self.assertFalse(_has_dominant_process(procs))


def _box_texts(box: Gtk.Box):
    texts = []
    child = box.get_first_child()
    while child is not None:
        collected = []
        _collect_label_text(child, collected)
        texts.append(" ".join(collected))
        child = child.get_next_sibling()
    return texts


def _collect_label_text(widget, out):
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_text())
    child = widget.get_first_child()
    while child is not None:
        _collect_label_text(child, out)
        child = child.get_next_sibling()


if __name__ == "__main__":
    unittest.main()
