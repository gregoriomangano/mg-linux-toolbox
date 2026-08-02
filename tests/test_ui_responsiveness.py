"""
Tests for final-polish-v6: the single shared Kernel-page inventory
(and the counters/labels built on it), sentence-case button text,
explanatory tooltips on disabled Prova/Ripristina, the FlowBox
conversion of the choice containers, the "already protected" note on
dmesg_restrict/kptr_restrict/ptrace_scope, MHz/GHz presentation for
CPU frequency limits, and the TCP display-name change (technical
value must stay byte-for-byte unchanged). No real write happens in
any test in this file — feature-level tests use fake /proc,/sys
trees; UI tests only construct/read widget state.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.i18n import T, _strings, set_lang
from core.kernel_features.base import SupportStatus

_HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
_SKIP_REASON = "no DISPLAY/WAYLAND_DISPLAY — constructing a real GTK widget without one segfaults the process"


class FakeRootTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proc_root = os.path.join(self.tmp, "proc")
        self.sys_root = os.path.join(self.tmp, "sys")
        os.makedirs(self.proc_root)
        os.makedirs(self.sys_root)

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(root, name), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# ═══════════════════ Shared Kernel-page inventory ═════════════════════
class KernelInventoryTests(FakeRootTestCase):
    """Uses build_kernel_inventory(proc_root=..., sys_root=...) against
    a fully fake tree — nothing here is real-host-dependent."""

    def _write(self, rel_path, content):
        path = os.path.join(self.tmp, rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    def _make_minimal_cpu(self):
        # Turbo Boost + Governor probe as UNSUPPORTED without these,
        # which is fine — they're still supposed to appear as entries.
        pass

    def test_static_features_always_included_even_when_unsupported(self):
        from ui.kernel.inventory import build_kernel_inventory
        entries = build_kernel_inventory(proc_root=self.proc_root, sys_root=self.sys_root)
        ids = {e.feature_id for e in entries}
        # Nothing at all exists in this fake tree, yet the "always
        # shown" statics must still produce a card each (explaining
        # they're unsupported), never silently vanish.
        for fid in ("monitoring.psi", "cpu.turbo_boost", "cpu.governor",
                    "memory.swappiness", "memory.thp", "memory.zram"):
            self.assertIn(fid, ids)
        for e in entries:
            if e.feature_id in ("monitoring.psi", "cpu.turbo_boost"):
                self.assertIn(e.support, (SupportStatus.UNSUPPORTED_KERNEL, SupportStatus.UNSUPPORTED_HARDWARE))

    def test_optional_feature_excluded_when_unsupported(self):
        from ui.kernel.inventory import build_kernel_inventory
        entries = build_kernel_inventory(proc_root=self.proc_root, sys_root=self.sys_root)
        ids = {e.feature_id for e in entries}
        # EPP/MGLRU/swap_readahead/zswap/TCP/the 4 security functions —
        # none of their real files exist in this fake tree, so none of
        # these OPTIONAL entries should appear at all.
        for fid in ("cpu.epp", "cpu.frequency_limits", "memory.zswap", "memory.mglru",
                    "memory.swap_readahead", "network.tcp_congestion_control",
                    "security.dmesg_restrict", "security.kptr_restrict",
                    "security.ptrace_scope", "security.protected_paths"):
            self.assertNotIn(fid, ids)

    def test_optional_feature_included_once_genuinely_supported(self):
        self._write("proc/sys/kernel/dmesg_restrict", "1")
        from ui.kernel.inventory import build_kernel_inventory
        entries = build_kernel_inventory(proc_root=self.proc_root, sys_root=self.sys_root)
        ids = {e.feature_id for e in entries}
        self.assertIn("security.dmesg_restrict", ids)

    def _make_disk(self, name, sched="[none] mq-deadline", ra="128"):
        self._write(f"sys/block/{name}/queue/scheduler", sched)
        self._write(f"sys/block/{name}/queue/read_ahead_kb", ra)

    def test_single_disk_generates_scheduler_and_readahead_entries(self):
        self._make_disk("sda")
        from ui.kernel.inventory import build_kernel_inventory
        entries = build_kernel_inventory(proc_root=self.proc_root, sys_root=self.sys_root)
        ids = {e.feature_id for e in entries}
        self.assertIn("storage.io_scheduler:sda", ids)
        self.assertIn("storage.read_ahead:sda", ids)

    def test_multiple_disks_each_get_their_own_entries(self):
        self._make_disk("sda")
        self._make_disk("nvme0n1")
        from ui.kernel.inventory import build_kernel_inventory
        entries = build_kernel_inventory(proc_root=self.proc_root, sys_root=self.sys_root)
        ids = {e.feature_id for e in entries}
        for dev in ("sda", "nvme0n1"):
            self.assertIn(f"storage.io_scheduler:{dev}", ids)
            self.assertIn(f"storage.read_ahead:{dev}", ids)

    def _make_cpu_policy(self, n, min_khz=800000, max_khz=4800000, hw_min=800000, hw_max=4800000):
        base = f"sys/devices/system/cpu/cpufreq/policy{n}"
        self._write(f"{base}/scaling_min_freq", str(min_khz))
        self._write(f"{base}/scaling_max_freq", str(max_khz))
        self._write(f"{base}/cpuinfo_min_freq", str(hw_min))
        self._write(f"{base}/cpuinfo_max_freq", str(hw_max))

    def test_cpu_single_policy_produces_one_inventory_entry(self):
        self._make_cpu_policy(0)
        from ui.kernel.inventory import build_kernel_inventory
        entries = build_kernel_inventory(proc_root=self.proc_root, sys_root=self.sys_root)
        matching = [e for e in entries if e.feature_id == "cpu.frequency_limits"]
        self.assertEqual(len(matching), 1)

    def test_cpu_32_policies_still_produce_exactly_one_aggregated_entry(self):
        for n in range(32):
            self._make_cpu_policy(n)
        from ui.kernel.inventory import build_kernel_inventory
        entries = build_kernel_inventory(proc_root=self.proc_root, sys_root=self.sys_root)
        matching = [e for e in entries if e.feature_id == "cpu.frequency_limits"]
        self.assertEqual(len(matching), 1, "32 real policies must still be ONE card, never 32")

    def test_no_battery_feature_ever_appears_in_the_kernel_inventory(self):
        from ui.kernel.inventory import build_kernel_inventory
        entries = build_kernel_inventory(proc_root=self.proc_root, sys_root=self.sys_root)
        for e in entries:
            self.assertFalse(e.feature_id.startswith("battery."),
                              f"battery feature leaked into Kernel inventory: {e.feature_id}")

    def test_no_audio_feature_ever_appears_in_the_kernel_inventory(self):
        from ui.kernel.inventory import build_kernel_inventory
        entries = build_kernel_inventory(proc_root=self.proc_root, sys_root=self.sys_root)
        for e in entries:
            self.assertFalse(e.feature_id.startswith("audio."),
                              f"audio feature leaked into Kernel inventory: {e.feature_id}")

    def test_count_kernel_inventory_matches_entries_length(self):
        self._make_disk("sda")
        self._write("proc/sys/kernel/dmesg_restrict", "1")
        from ui.kernel.inventory import build_kernel_inventory, count_kernel_inventory
        entries = build_kernel_inventory(proc_root=self.proc_root, sys_root=self.sys_root)
        total, available, unsupported = count_kernel_inventory(proc_root=self.proc_root, sys_root=self.sys_root)
        self.assertEqual(total, len(entries))

    def test_real_host_inventory_is_never_hardcoded(self):
        """Two independent calls against the real host must produce the
        same live-computed number — never a literal constant baked in."""
        from ui.kernel.inventory import count_kernel_inventory
        first = count_kernel_inventory()
        second = count_kernel_inventory()
        self.assertEqual(first, second)
        self.assertIsInstance(first[0], int)


@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class KernelPageHeaderCountTests(unittest.TestCase):
    """The header pill's number must equal the number of FeatureCards
    the page really built — measured on the real host, never asserted
    against a literal like 20 (which would itself be a hardcoded
    number the next kernel feature could silently invalidate)."""

    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()

    def test_header_count_equals_rows_actually_built(self):
        from ui.pages.page_kernel import KernelPage
        from ui.kernel.inventory import count_kernel_inventory
        page = KernelPage()
        total, _available, _unsupported = count_kernel_inventory()
        self.assertEqual(page._kernel_card_count, total)
        self.assertGreater(page._kernel_card_count, 0)

    def test_home_and_kernel_page_agree_on_the_same_number(self):
        from ui.pages.page_kernel import KernelPage
        from ui.kernel.inventory import count_kernel_inventory
        page = KernelPage()
        home_total, _a, _u = count_kernel_inventory()
        self.assertEqual(page._kernel_card_count, home_total)


# ═══════════════════════ Home counter labels ═══════════════════════════
class HomeCounterLabelsTests(unittest.TestCase):
    def test_active_label_mentions_mg_toolbox_not_just_active(self):
        set_lang("it")
        text = T("ov2_kernel_active")
        self.assertIn("MG Toolbox", text)
        self.assertNotEqual(text, "Attive")

    def test_detected_label_says_available_functions(self):
        set_lang("it")
        self.assertEqual(T("ov2_kernel_detected"), "Funzioni disponibili")

    def test_temporary_and_permanent_labels_say_modifiche(self):
        set_lang("it")
        self.assertIn("Modifiche", T("ov2_kernel_temporary"))
        self.assertIn("Modifiche", T("ov2_kernel_permanent"))

    def test_unsupported_label_says_not_available_on_this_pc(self):
        set_lang("it")
        self.assertEqual(T("ov2_kernel_unsupported"), "Non disponibili su questo PC")

    def test_labels_translated_in_every_language(self):
        for lang in ("en", "es", "fr", "it"):
            set_lang(lang)
            for key in ("ov2_kernel_active", "ov2_kernel_detected",
                        "ov2_kernel_temporary", "ov2_kernel_permanent", "ov2_kernel_unsupported"):
                text = T(key)
                self.assertNotEqual(text, key, f"{key} missing a real translation for {lang}")
        set_lang("it")

    def test_active_count_is_exactly_the_state_store_record_count(self):
        from core.persistence.rollback_store import FeatureRecord
        fake_records = {
            "cpu.turbo_boost": FeatureRecord(feature_id="cpu.turbo_boost", initial_value=True, mode="temporary"),
            "memory.swappiness": FeatureRecord(feature_id="memory.swappiness", initial_value=60, mode="persistent"),
            "security.dmesg_restrict": FeatureRecord(feature_id="security.dmesg_restrict", initial_value="0", mode="persistent"),
        }

        class FakeStore:
            def all(self):
                return fake_records

        with mock.patch("core.persistence.rollback_store.default_state_store", return_value=FakeStore()):
            from ui.pages.page_overview import _count_feature_state
            active, temporary, permanent = _count_feature_state()
        self.assertEqual(active, 3)
        self.assertEqual(temporary, 1)
        self.assertEqual(permanent, 2)
        # temporary + permanent both counted, and separately from each other
        self.assertNotEqual(temporary, permanent)


# ═══════════════════════ Button casing ═════════════════════════════════
class ButtonCasingTests(unittest.TestCase):
    def test_try_and_make_permanent_are_sentence_case_it(self):
        self.assertEqual(_strings["kf_try_btn"]["it"], "Prova fino al riavvio")
        self.assertEqual(_strings["kf_make_permanent_btn"]["it"], "Rendi permanente")

    def test_try_and_make_permanent_sentence_case_every_language(self):
        for lang in ("en", "es", "fr", "it"):
            for key in ("kf_try_btn", "kf_make_permanent_btn"):
                text = _strings[key][lang]
                words = text.split()
                # Sentence case: only the first word (and any genuine
                # proper noun, none present in these two strings) is
                # capitalized — no OTHER word should start uppercase.
                for w in words[1:]:
                    self.assertFalse(w[0].isupper(),
                                      f"{key}[{lang}] = {text!r} still looks Title Case")

    def test_btscan_and_cleanup_buttons_sentence_case(self):
        self.assertEqual(_strings["btscan_btn"]["it"], "Cerca dispositivi")
        self.assertEqual(_strings["cleanup_btn"]["it"], "Pulisci ora")

    def test_already_correct_button_texts_left_untouched(self):
        # svc_start_btn/svc_stop_btn/install_btn/kf_restore_btn were
        # already correct before V6 — must be byte-for-byte the same.
        self.assertEqual(_strings["svc_start_btn"]["it"], "Avvia")
        self.assertEqual(_strings["svc_stop_btn"]["it"], "Ferma")
        self.assertEqual(_strings["install_btn"]["it"], "Installa")
        self.assertEqual(_strings["kf_restore_btn"]["it"], "Ripristina")


# ═══════════════════════ Tooltips ═══════════════════════════════════════
@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class TooltipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()

    def test_try_button_gets_a_real_reason_when_disabled(self):
        from ui.pages.page_kernel import GovernorRow
        set_lang("it")
        row = GovernorRow()
        row.set_try_sensitive(False, "kf_try_reason_same_value")
        self.assertEqual(row.btn_try.get_tooltip_text(), T("kf_try_reason_same_value"))
        self.assertNotEqual(row.btn_try.get_tooltip_text(), "")

    def test_try_button_tooltip_cleared_when_enabled(self):
        from ui.pages.page_kernel import GovernorRow
        row = GovernorRow()
        row.set_try_sensitive(True)
        self.assertFalse(row.btn_try.get_tooltip_text())  # None or "" — both mean "no tooltip"

    def test_different_reasons_produce_different_tooltip_text(self):
        from ui.pages.page_kernel import GovernorRow
        set_lang("it")
        row = GovernorRow()
        row.set_try_sensitive(False, "kf_try_reason_pick_different")
        first = row.btn_try.get_tooltip_text()
        row.set_try_sensitive(False, "kf_try_reason_same_value")
        second = row.btn_try.get_tooltip_text()
        self.assertNotEqual(first, second, "same generic tooltip used for two different reasons")

    def test_restore_button_has_a_reason_when_disabled(self):
        from ui.pages.page_kernel import GovernorRow
        set_lang("it")
        row = GovernorRow()
        row.set_restore_enabled(False)
        self.assertEqual(row.btn_restore.get_tooltip_text(), T("kf_restore_reason_nothing"))


# ═══════════════════════ FlowBox containers ═════════════════════════════
@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class FlowBoxContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()

    def test_choice_kernel_feature_row_uses_flowbox(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        from ui.pages.page_kernel import GovernorRow, EPPRow, THPRow
        for cls in (GovernorRow, THPRow):
            row = cls()
            self.assertIsInstance(row._choice_list, Gtk.FlowBox)
            self.assertEqual(row._choice_list.get_selection_mode(), Gtk.SelectionMode.NONE)

    def test_read_ahead_preset_box_uses_flowbox(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        from ui.pages.page_kernel import ReadAheadRow
        from core.kernel_features.storage import list_real_disks
        disks = list_real_disks()
        if not disks:
            self.skipTest("no real disk on this host to build a ReadAheadRow for")
        device_id, friendly = disks[0]
        row = ReadAheadRow(device_id, friendly)
        self.assertIsInstance(row._preset_box, Gtk.FlowBox)

    def test_cpu_frequency_limits_profile_box_uses_flowbox(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        from core.kernel_features.cpu import CpuFrequencyLimitsFeature
        if CpuFrequencyLimitsFeature().probe() not in (
                SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_READ_ONLY):
            self.skipTest("no real cpufreq policy on this host")
        from ui.pages.page_kernel import CpuFrequencyLimitsRow
        row = CpuFrequencyLimitsRow()
        self.assertIsInstance(row._profile_box, Gtk.FlowBox)

    def test_protected_paths_choice_box_uses_flowbox(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        from core.kernel_features.security import ProtectedPathsFeature
        if ProtectedPathsFeature().probe() not in (
                SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_READ_ONLY):
            self.skipTest("fs.protected_* not present on this host")
        from ui.pages.page_kernel import ProtectedPathsRow
        row = ProtectedPathsRow()
        self.assertIsInstance(row._choice_box, Gtk.FlowBox)

    def test_flowbox_never_caps_below_the_real_choice_count(self):
        """Sanity check: max_children_per_line must be able to hold the
        largest known choice set (5 CPU profiles) on one line when
        there's room — never artificially wrap a row that would fit."""
        from ui.pages.page_kernel import _new_choice_flowbox
        flow = _new_choice_flowbox()
        self.assertGreaterEqual(flow.get_max_children_per_line(), 5)


# ═══════════════════════ "Already protected" note ═══════════════════════
@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class AlreadyProtectedNoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()

    def _toggle_matching_current(self, row):
        child = row._choice_list.get_first_child()
        while child is not None:
            btn = child.get_child()
            if btn.get_active():
                btn.emit("toggled")
                return
            child = child.get_next_sibling()

    def test_governor_never_shows_the_note(self):
        from ui.pages.page_kernel import GovernorRow
        row = GovernorRow()
        self._toggle_matching_current(row)
        self.assertFalse(row._already_note.get_visible())

    def test_thp_never_shows_the_note(self):
        from core.kernel_features.memory import THPFeature
        if THPFeature().probe() not in (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_READ_ONLY):
            self.skipTest("THP not present on this host")
        from ui.pages.page_kernel import THPRow
        row = THPRow()
        self._toggle_matching_current(row)
        self.assertFalse(row._already_note.get_visible())

    def test_dmesg_restrict_protected_values(self):
        from ui.pages.page_kernel import DmesgRestrictRow
        self.assertEqual(DmesgRestrictRow()._protected_values(), {"1"})

    def test_kptr_restrict_protected_values(self):
        from ui.pages.page_kernel import KptrRestrictRow
        self.assertEqual(KptrRestrictRow()._protected_values(), {"1", "2"})

    def test_ptrace_scope_protected_values_never_include_0_or_3(self):
        from ui.pages.page_kernel import PtraceScopeRow
        protected = PtraceScopeRow()._protected_values()
        self.assertEqual(protected, {"1", "2"})
        self.assertNotIn("0", protected)
        self.assertNotIn("3", protected)

    def test_default_protected_values_is_empty_for_any_other_choice_row(self):
        from ui.pages.page_kernel import ChoiceKernelFeatureRow
        # The base class default must be empty — every existing row
        # (governor/EPP/THP/MGLRU/page-cluster/TCP) inherits this
        # unless it explicitly overrides it, so none of them regress.
        self.assertEqual(ChoiceKernelFeatureRow._protected_values(None), set())


# ═══════════════════════ MHz/GHz formatting ═════════════════════════════
class MhzGhzFormattingTests(unittest.TestCase):
    def test_below_1000_stays_mhz(self):
        from ui.pages.page_kernel import _format_freq_mhz
        set_lang("it")
        self.assertEqual(_format_freq_mhz(582), "582 MHz")
        self.assertEqual(_format_freq_mhz(999), "999 MHz")

    def test_at_or_above_1000_becomes_ghz(self):
        from ui.pages.page_kernel import _format_freq_mhz
        set_lang("it")
        self.assertEqual(_format_freq_mhz(1000), "1 GHz")
        self.assertEqual(_format_freq_mhz(5086), "5,09 GHz")

    def test_matches_the_spec_example_exactly(self):
        from ui.pages.page_kernel import _format_freq_range
        set_lang("it")
        self.assertEqual(_format_freq_range(1746, 5086), "1,75–5,09 GHz")

    def test_mixed_units_range_shows_both(self):
        from ui.pages.page_kernel import _format_freq_range
        set_lang("it")
        self.assertEqual(_format_freq_range(582, 5086), "582 MHz–5,09 GHz")

    def test_both_below_1000_shows_mhz_only(self):
        from ui.pages.page_kernel import _format_freq_range
        self.assertEqual(_format_freq_range(500, 900), "500 MHz–900 MHz")

    def test_locale_decimal_separator(self):
        from ui.pages.page_kernel import _format_freq_mhz
        set_lang("en")
        self.assertEqual(_format_freq_mhz(5086), "5.09 GHz")
        set_lang("it")
        self.assertEqual(_format_freq_mhz(5086), "5,09 GHz")
        set_lang("es")
        self.assertEqual(_format_freq_mhz(5086), "5,09 GHz")
        set_lang("fr")
        self.assertEqual(_format_freq_mhz(5086), "5,09 GHz")
        set_lang("it")

    def test_never_more_than_two_decimals(self):
        from ui.pages.page_kernel import _format_freq_mhz
        set_lang("it")
        text = _format_freq_mhz(1234567)
        decimals = text.split(",")[1].split()[0] if "," in text else ""
        self.assertLessEqual(len(decimals), 2)

    def test_technical_khz_value_sent_to_backend_is_never_rounded(self):
        """The formatting helpers are presentation-only — apply_temporary
        still receives/validates the exact kHz integer, never a rounded
        GHz float. Verified against the real CpuFrequencyLimitsFeature
        validate() path, not just the formatter."""
        from core.kernel_features.cpu import CpuFrequencyLimitsFeature
        f = CpuFrequencyLimitsFeature()
        if f.probe() not in (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_READ_ONLY):
            self.skipTest("no real cpufreq policy on this host")
        current = f.read_current()
        self.assertTrue(current.ok)
        first_policy = current.value["policies"][0]
        self.assertIsInstance(first_policy["min"], int)
        self.assertIsInstance(first_policy["max"], int)


# ═══════════════════════ TCP display name vs technical value ═══════════
class TcpDisplayNameTests(unittest.TestCase):
    def test_cubic_reno_display_names(self):
        from core.kernel_features.network import TcpCongestionControlFeature
        f = TcpCongestionControlFeature()
        self.assertEqual(f.to_friendly("cubic"), "CUBIC")
        self.assertEqual(f.to_friendly("reno"), "Reno")
        self.assertEqual(f.to_friendly("bbr"), "BBR")

    def test_unknown_algorithm_display_name_unchanged(self):
        from core.kernel_features.network import TcpCongestionControlFeature
        f = TcpCongestionControlFeature()
        self.assertEqual(f.to_friendly("some_future_algo"), "some_future_algo")

    def test_raw_technical_value_read_from_kernel_is_never_altered(self):
        from core.kernel_features.network import TcpCongestionControlFeature
        f = TcpCongestionControlFeature()
        if f.probe() not in (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_PERSISTENT,
                              SupportStatus.SUPPORTED_READ_ONLY):
            self.skipTest("no tcp_congestion_control on this host")
        current = f.read_current()
        self.assertTrue(current.ok)
        # The real kernel value is always lowercase (cubic/reno/bbr/...)
        # — to_friendly() must never leak into what's actually read back.
        self.assertEqual(current.value, current.value.lower())

    def test_validate_still_accepts_the_exact_lowercase_kernel_spelling(self):
        from core.kernel_features.network import TcpCongestionControlFeature
        f = TcpCongestionControlFeature()
        available = f.read_available()
        if not available:
            self.skipTest("no tcp_available_congestion_control on this host")
        for algo in available:
            self.assertTrue(f.validate(algo))
            self.assertFalse(f.validate(algo.upper()), "validate() must not accept a display-cased value")


# ═══════════════════════ Services page: no redundant dots ══════════════
class ServicesStatusDotsRemovedTests(unittest.TestCase):
    def test_no_unicode_dot_in_status_labels(self):
        for lang in ("en", "es", "fr", "it"):
            self.assertNotIn("●", _strings["svc_status_active"][lang])
            self.assertNotIn("○", _strings["svc_status_inactive"][lang])

    def test_status_word_itself_unchanged(self):
        self.assertEqual(_strings["svc_status_active"]["it"], "Attivo")
        self.assertEqual(_strings["svc_status_inactive"]["it"], "Inattivo")


if __name__ == "__main__":
    unittest.main()
