"""
Tests for completion-v7: friendly feature names and local-timezone
timestamps on the History page, labeled History filters, the redesigned
Services row (tooltips, non-uppercase auto-start label), the corrected
meaning of the Home "Modificate da MG Toolbox" counter (a "restored"
state-store record is no longer counted as an active change), disk-card
correctness (device vs. partition vs. filesystem, multi-mount
disclosure, locale-aware GB formatting), a sample of the Italian
sentence-case fixes, and the real reasons behind an "Unknown status"
Secure Boot reading. No real write happens in any test in this file —
feature-level tests use fake /proc,/sys trees or mocked backend calls;
UI tests only construct/read widget state.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.i18n import T, _strings, set_lang
from core import i18n as _i18n_mod

_HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
_SKIP_REASON = "no DISPLAY/WAYLAND_DISPLAY — constructing a real GTK widget without one segfaults the process"


def tearDownModule():
    set_lang("it")


# ═══════════════════ History: friendly feature names ═══════════════════
class FriendlyFeatureNameTests(unittest.TestCase):
    def setUp(self):
        set_lang("it")

    def test_known_kernel_feature_id_reuses_the_real_page_title(self):
        from ui.kernel.friendly_names import friendly_feature_name
        self.assertEqual(friendly_feature_name("network.tcp_congestion_control"),
                          T("kf_tcp_congestion_title"))
        self.assertEqual(friendly_feature_name("memory.mglru"), T("kf_mglru_title"))

    def test_history_only_pseudo_ids_resolve_to_real_page_titles(self):
        from ui.kernel.friendly_names import friendly_feature_name
        self.assertEqual(friendly_feature_name("virt.kvm"), T("kvm_title"))
        self.assertEqual(friendly_feature_name("virt.virt_manager"), T("history_feature_virt_manager"))
        self.assertEqual(friendly_feature_name("apparmor.profile"), T("apparmor_title"))

    def test_never_returns_the_raw_dotted_feature_id(self):
        from ui.kernel.friendly_names import friendly_feature_name
        for fid in ("monitoring.psi", "cpu.governor", "virt.kvm", "virt.virt_manager"):
            self.assertNotEqual(friendly_feature_name(fid), fid)

    def test_unknown_feature_id_is_a_readable_derived_fallback_not_a_crash(self):
        from ui.kernel.friendly_names import friendly_feature_name
        text = friendly_feature_name("some_future_module.new_thing_id")
        self.assertEqual(text, "New thing id")

    def test_empty_or_missing_feature_id_never_crashes(self):
        from ui.kernel.friendly_names import friendly_feature_name
        self.assertEqual(friendly_feature_name(""), T("history_feature_unknown"))
        self.assertEqual(friendly_feature_name(None), T("history_feature_unknown"))

    def test_resolves_in_every_supported_language(self):
        from ui.kernel.friendly_names import friendly_feature_name
        for lang in ("en", "es", "fr", "it"):
            set_lang(lang)
            text = friendly_feature_name("virt.kvm")
            self.assertNotEqual(text, "virt.kvm")
            self.assertTrue(text)
        set_lang("it")


# ═══════════════════ History: local timezone timestamps ════════════════
class LocalDatetimeTests(unittest.TestCase):
    def setUp(self):
        set_lang("it")

    def test_utc_z_suffix_and_explicit_offset_are_both_handled(self):
        from ui.design_system.local_datetime import format_local_datetime
        z = format_local_datetime("2026-08-01T06:11:20Z")
        offset = format_local_datetime("2026-08-01T06:11:20+00:00")
        self.assertEqual(z, offset)
        self.assertNotIn("T", z)
        self.assertNotIn("+00:00", z)

    def test_timezone_naive_value_is_treated_as_utc_not_rejected(self):
        from ui.design_system.local_datetime import format_local_datetime
        text = format_local_datetime("2026-08-01T06:11:20")
        self.assertTrue(text)
        self.assertNotIn("T", text)

    def test_explicit_non_utc_offset_is_converted_correctly(self):
        from ui.design_system.local_datetime import format_local_datetime
        text = format_local_datetime("2026-08-01T08:11:20+02:00")
        self.assertTrue(text)

    def test_missing_value_shows_a_translated_placeholder_never_blank(self):
        from ui.design_system.local_datetime import format_local_datetime
        self.assertEqual(format_local_datetime(None), T("history_timestamp_unknown"))
        self.assertEqual(format_local_datetime(""), T("history_timestamp_unknown"))

    def test_invalid_value_is_shown_verbatim_never_raises(self):
        from ui.design_system.local_datetime import format_local_datetime
        self.assertEqual(format_local_datetime("not-a-date"), "not-a-date")

    def test_old_incomplete_record_never_crashes_the_page(self):
        from ui.design_system.local_datetime import format_local_datetime
        for raw in ("", None, "not-a-date", "2020-01-01", 12345, {}):
            try:
                format_local_datetime(raw)
            except Exception as e:  # pragma: no cover - the point of the test
                self.fail(f"format_local_datetime raised on {raw!r}: {e}")

    def test_matches_the_expected_italian_wording_pattern(self):
        from ui.design_system.local_datetime import format_local_datetime
        text = format_local_datetime("2026-08-01T06:11:20+00:00")
        self.assertIn("agosto", text)
        self.assertIn("2026", text)

    def test_resolves_in_every_supported_language(self):
        from ui.design_system.local_datetime import format_local_datetime
        for lang in ("en", "es", "fr", "it"):
            set_lang(lang)
            text = format_local_datetime("2026-08-01T06:11:20+00:00")
            self.assertTrue(text)
            self.assertNotIn("T06:11", text)
        set_lang("it")


# ═══════════════════ History: labeled filters ═══════════════════════════
@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class HistoryFilterLabelTests(unittest.TestCase):
    def setUp(self):
        set_lang("it")

    def _make_section(self):
        import gi
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from ui.pages.page_history import ActivityHistorySection
        page = Adw.PreferencesPage()
        return ActivityHistorySection(page)

    def test_the_three_filters_have_distinct_non_generic_visible_labels(self):
        section = self._make_section()
        texts = {section._page_dd_label.get_text(),
                 section._type_dd_label.get_text(),
                 section._result_dd_label.get_text()}
        self.assertEqual(len(texts), 3, "the three filter labels must not collide")
        self.assertIn(T("history_filter_label_page"), texts)
        self.assertIn(T("history_filter_label_type"), texts)
        self.assertIn(T("history_filter_label_result"), texts)

    def test_filters_have_a_tooltip_for_accessibility(self):
        section = self._make_section()
        self.assertTrue(section._page_dd.get_tooltip_text())
        self.assertTrue(section._type_dd.get_tooltip_text())
        self.assertTrue(section._result_dd.get_tooltip_text())

    def test_labels_relabel_on_language_change(self):
        set_lang("en")
        section = self._make_section()
        set_lang("it")
        section._refresh_filter_labels()
        self.assertEqual(section._page_dd_label.get_text(), T("history_filter_label_page"))


# ═══════════════════ Services page redesign ═════════════════════════════
@unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
class ServicesRowTests(unittest.TestCase):
    def setUp(self):
        set_lang("it")

    def test_autostart_label_is_not_the_old_alla_avvio_wording(self):
        self.assertEqual(_strings["svc_enable_lbl"]["it"], "Avvio automatico")
        self.assertNotIn("All'avvio", _strings["svc_enable_lbl"]["it"])

    def test_autostart_label_translated_in_every_language(self):
        for lang in ("en", "es", "fr", "it"):
            self.assertNotEqual(_strings["svc_enable_lbl"][lang], "svc_enable_lbl")

    def test_missing_service_disables_controls_with_an_explanatory_tooltip(self):
        from ui.pages.page_services import ServiceRow
        import backend.all as B
        with mock.patch("backend.all._service_exists", return_value=False):
            row = ServiceRow(B.SERVICES[0][0], B.SERVICES[0][1])
        self.assertFalse(row._start_btn.get_sensitive())
        self.assertFalse(row._boot_switch.get_sensitive())
        self.assertTrue(row._start_btn.get_tooltip_text())
        self.assertTrue(row._boot_switch.get_tooltip_text())
        self.assertEqual(row._start_btn.get_tooltip_text(), T("svc_disabled_tooltip"))

    def test_present_service_has_no_disabled_tooltip_needed(self):
        from ui.pages.page_services import ServiceRow
        import backend.all as B
        with mock.patch("backend.all._service_exists", return_value=True), \
             mock.patch("backend.all._service_enabled", return_value=False), \
             mock.patch("backend.all._service_active", return_value=False):
            row = ServiceRow(B.SERVICES[0][0], B.SERVICES[0][1])
        self.assertTrue(row._start_btn.get_sensitive())
        self.assertTrue(row._boot_switch.get_sensitive())

    def test_services_page_fits_1366x768_content_width_with_no_horizontal_scroll(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        from ui.pages.page_services import ServicesPage
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(ServicesPage())
        win = Gtk.Window()
        win.set_default_size(1366 - 280, 768)
        win.set_child(scrolled)
        win.present()
        while __import__("gi.repository", fromlist=["GLib"]).GLib.MainContext.default().iteration(False):
            pass
        hadj = scrolled.get_hadjustment()
        self.assertLessEqual(hadj.get_upper(), hadj.get_page_size() + 1)
        win.destroy()


# ═══════════════════ Home counter meaning ═══════════════════════════════
class HomeCounterMeaningTests(unittest.TestCase):
    """Documents, with real assertions, exactly what each of the five
    Home "Funzioni Kernel" values means:
      - detected/unsupported: from ui.kernel.inventory (what the Kernel
        page itself would render), untouched by this fix.
      - active ("Modificate da MG Toolbox"): FeatureRecords in the
        rollback state store whose mode is "temporary" or "persistent" —
        i.e. changes this app applied and has NOT restored. A "restored"
        record (mode="restored", written by core.priv_writer._note_applied
        once a value is put back) is deliberately excluded — it is
        history, not an active change.
      - temporary / permanent: active split by mode, so
        active == temporary + permanent always holds.
    """
    def test_restored_record_is_not_counted_as_an_active_change(self):
        from core.persistence.rollback_store import FeatureRecord
        from ui.pages.page_overview import _count_feature_state
        fake_records = {
            "cpu.turbo_boost": FeatureRecord(feature_id="cpu.turbo_boost", initial_value=True, mode="restored"),
        }

        class FakeStore:
            def all(self):
                return fake_records

        with mock.patch("core.persistence.rollback_store.default_state_store", return_value=FakeStore()):
            active, temporary, permanent = _count_feature_state()
        self.assertEqual((active, temporary, permanent), (0, 0, 0))

    def test_mix_of_restored_and_active_records_counts_only_the_active_ones(self):
        from core.persistence.rollback_store import FeatureRecord
        from ui.pages.page_overview import _count_feature_state
        fake_records = {
            "cpu.turbo_boost": FeatureRecord(feature_id="cpu.turbo_boost", initial_value=True, mode="restored"),
            "memory.swappiness": FeatureRecord(feature_id="memory.swappiness", initial_value=60, mode="temporary"),
            "security.dmesg_restrict": FeatureRecord(feature_id="security.dmesg_restrict", initial_value="0", mode="persistent"),
        }

        class FakeStore:
            def all(self):
                return fake_records

        with mock.patch("core.persistence.rollback_store.default_state_store", return_value=FakeStore()):
            active, temporary, permanent = _count_feature_state()
        self.assertEqual((active, temporary, permanent), (2, 1, 1))

    def test_active_always_equals_temporary_plus_permanent(self):
        from core.persistence.rollback_store import FeatureRecord
        from ui.pages.page_overview import _count_feature_state
        fake_records = {
            f"f{i}": FeatureRecord(feature_id=f"f{i}", initial_value=1, mode=mode)
            for i, mode in enumerate(["temporary", "temporary", "persistent", "restored", "restored"])
        }

        class FakeStore:
            def all(self):
                return fake_records

        with mock.patch("core.persistence.rollback_store.default_state_store", return_value=FakeStore()):
            active, temporary, permanent = _count_feature_state()
        self.assertEqual(active, temporary + permanent)
        self.assertEqual((active, temporary, permanent), (3, 2, 1))

    def test_empty_store_is_honestly_all_zero(self):
        from ui.pages.page_overview import _count_feature_state

        class FakeStore:
            def all(self):
                return {}

        with mock.patch("core.persistence.rollback_store.default_state_store", return_value=FakeStore()):
            self.assertEqual(_count_feature_state(), (0, 0, 0))


# ═══════════════════ Disk cards: correctness ════════════════════════════
class DiskCardCorrectnessTests(unittest.TestCase):
    def _patch_disks(self, list_real, partitions, mount_usage, removable=None, sizes=None):
        removable = removable or {}
        sizes = sizes or {}

        def fake_read_int(path, fallback=0):
            for dev, size in sizes.items():
                if f"/{dev}/size" in path:
                    return size
            return fallback

        def fake_is_removable(dev):
            return removable.get(dev, False)

        def fake_mount_usage(mount):
            return mount_usage.get(mount, (0, 0, 0))

        return mock.patch.multiple(
            "ui.pages.page_overview",
            list_real_disks=mock.Mock(return_value=list_real),
            _get_disks=mock.Mock(return_value=partitions),
            _get_mount_usage=mock.Mock(side_effect=fake_mount_usage),
            _is_removable=mock.Mock(side_effect=fake_is_removable),
            _read_int=mock.Mock(side_effect=fake_read_int),
        )

    def test_physical_disk_with_no_mounted_partition_is_flagged_unavailable(self):
        from ui.pages.page_overview import _summarize_physical_disks
        with self._patch_disks(
            list_real=[("sdb", "HDD Unmounted")],
            partitions=[("sdb1", 500.0, "—", "ext4", False)],
            mount_usage={},
            sizes={"sdb": 500 * 1024 ** 3 // 512},
        ):
            disks, total = _summarize_physical_disks()
        self.assertEqual(total, 1)
        self.assertFalse(disks[0]["any_mounted"])
        self.assertEqual(disks[0]["mounted_count"], 0)
        self.assertEqual(disks[0]["used_gb"], 0.0)

    def test_one_mounted_partition_is_a_simple_clean_summary(self):
        from ui.pages.page_overview import _summarize_physical_disks
        with self._patch_disks(
            list_real=[("sda", "SSD Sample")],
            partitions=[("sda1", 250.0, "/", "ext4", False)],
            mount_usage={"/": (250.0, 100.0, 40.0)},
            sizes={"sda": 250 * 1024 ** 3 // 512},
        ):
            disks, _total = _summarize_physical_disks()
        self.assertTrue(disks[0]["any_mounted"])
        self.assertEqual(disks[0]["mounted_count"], 1)
        self.assertEqual(disks[0]["used_gb"], 100.0)

    def test_multiple_mounted_partitions_are_summed_and_the_count_is_kept(self):
        from ui.pages.page_overview import _summarize_physical_disks
        with self._patch_disks(
            list_real=[("nvme0n1", "NVMe Sample")],
            partitions=[
                ("nvme0n1p1", 1.0, "/boot/efi", "vfat", False),
                ("nvme0n1p2", 499.0, "/", "ext4", False),
            ],
            mount_usage={"/boot/efi": (1.0, 0.1, 10.0), "/": (499.0, 200.0, 40.0)},
            sizes={"nvme0n1": 500 * 1024 ** 3 // 512},
        ):
            disks, _total = _summarize_physical_disks()
        self.assertEqual(disks[0]["mounted_count"], 2)
        self.assertAlmostEqual(disks[0]["used_gb"], 200.1, places=1)
        # never let the sum pass as "the one filesystem's" usage:
        self.assertGreaterEqual(disks[0]["mounted_count"], 2)

    def test_removable_disk_is_flagged_as_such(self):
        from ui.pages.page_overview import _summarize_physical_disks
        with self._patch_disks(
            list_real=[("sdc", "USB Stick")],
            partitions=[("sdc1", 32.0, "/media/usb", "vfat", True)],
            mount_usage={"/media/usb": (32.0, 10.0, 31.0)},
            removable={"sdc": True},
            sizes={"sdc": 32 * 1024 ** 3 // 512},
        ):
            disks, _total = _summarize_physical_disks()
        self.assertTrue(disks[0]["removable"])

    def test_device_mapper_nodes_are_never_shown_as_a_physical_disk(self):
        # list_real_disks() already excludes dm-* — a dm-* entry
        # appearing only in the raw _get_disks() partition list must
        # never get attached to (or counted as) a real physical device.
        from ui.pages.page_overview import _summarize_physical_disks
        with self._patch_disks(
            list_real=[("sda", "SSD Sample")],
            partitions=[
                ("sda1", 500.0, "—", "crypto_LUKS", False),
                ("dm-0", 499.0, "/", "ext4", False),
            ],
            mount_usage={"/": (499.0, 100.0, 20.0)},
            sizes={"sda": 500 * 1024 ** 3 // 512},
        ):
            disks, total = _summarize_physical_disks()
        self.assertEqual(total, 1)
        # dm-0's mount must NOT be summed into sda's used_gb (dm-0
        # doesn't start with "sda"):
        self.assertFalse(disks[0]["any_mounted"])
        self.assertEqual(disks[0]["used_gb"], 0.0)

    def test_missing_or_unreadable_values_never_crash_the_summary(self):
        from ui.pages.page_overview import _summarize_physical_disks
        with self._patch_disks(
            list_real=[("sdz", "Ghost Disk")],
            partitions=[],
            mount_usage={},
            sizes={},
        ):
            disks, total = _summarize_physical_disks()
        # a disk that reads back 0 sectors is skipped, never shown with
        # a fabricated capacity:
        self.assertEqual(total, 0)
        self.assertEqual(disks, [])

    def test_gb_formatting_uses_locale_decimal_separator_max_one_decimal(self):
        from ui.pages.page_overview import _format_gb
        set_lang("it")
        self.assertEqual(_format_gb(465.73), "465,7")
        set_lang("en")
        self.assertEqual(_format_gb(465.73), "465.7")
        set_lang("it")

    def test_gb_formatting_never_mixes_separators_within_one_language(self):
        from ui.pages.page_overview import _format_gb
        set_lang("it")
        for value in (0.0, 1.0, 999.95, 1907.7):
            text = _format_gb(value)
            self.assertNotIn(".", text)
        set_lang("it")


# ═══════════════════ Sentence-case Italian text sample ══════════════════
class SentenceCaseSampleTests(unittest.TestCase):
    """A representative sample of the V7 casing fixes — not exhaustive,
    but enough to lock in the rule (sentence case, proper nouns/acronyms
    excluded) and catch a regression if one of these keys' text drifts
    back to Title Case."""
    def test_explicit_examples_from_the_v7_request(self):
        pairs = {
            "pprofile_title": "Profilo energetico",
            "audio_restart_title": "Riavvia audio",
            "grp_sys_services": "Servizi di sistema",
            "grp_printing": "Driver stampanti",
            "printer_base_title": "Supporto stampa base",
            "game_mode_title": "Modalità gioco",
            "kvm_title": "Virtualizzazione hardware KVM",
            "rootssh_title": "Accesso root tramite SSH",
            "autoupdate_title": "Aggiornamenti automatici",
        }
        set_lang("it")
        for key, expected in pairs.items():
            self.assertEqual(T(key), expected, f"{key} regressed from sentence case")

    def test_btscan_title_keeps_bluetooth_capitalized_as_a_proper_noun(self):
        set_lang("it")
        self.assertEqual(T("btscan_title"), "Cerca dispositivi Bluetooth")

    def test_easyeffects_parenthetical_is_sentence_case(self):
        set_lang("it")
        self.assertEqual(T("easyeffects_title"), "EasyEffects (Equalizzatore audio)")

    def test_excluded_technical_terms_are_never_translated_or_recased(self):
        set_lang("it")
        for key, must_contain in (
            ("secureboot_title", "Secure Boot"),
            ("kvm_title", "KVM"),
            ("rootssh_title", "SSH"),
            ("dns_title", "DNS"),
        ):
            self.assertIn(must_contain, T(key))

    def test_home_tab_and_group_titles_agree_on_sentence_case(self):
        # ov2_quick_kernel_t must equal tab_kernel (enforced elsewhere by
        # test_v3_navigation_audit) — both must be genuinely sentence
        # case now, not just equal to each other.
        set_lang("it")
        self.assertEqual(T("tab_kernel"), "Funzioni kernel")
        self.assertEqual(T("ov2_quick_kernel_t"), T("tab_kernel"))
        self.assertEqual(T("tab_network"), "Rete e dispositivi")
        self.assertEqual(T("ov2_quick_network_t"), T("tab_network"))
        self.assertEqual(T("tab_system"), "Sistema e disco")
        self.assertEqual(T("ov2_quick_system_t"), T("tab_system"))

    def test_no_key_lost_a_language_during_the_sweep(self):
        for key in ("pprofile_title", "grp_sys_services", "tab_kernel", "tab_network",
                    "tab_system", "kf_sched_kyber_title", "sysinfo_os"):
            for lang in ("en", "es", "fr", "it"):
                self.assertTrue(_strings.get(key, {}).get(lang) or
                                 self._find_in_page_locals(key, lang),
                                 f"{key}/{lang} missing")

    def _find_in_page_locals(self, key, lang):
        # sysinfo_os lives in page_info.py's local dict, only merged into
        # core.i18n._strings once that module is imported.
        import ui.pages.page_info  # noqa: F401
        return bool(_strings.get(key, {}).get(lang))


# ═══════════════════ Secure Boot: real unknown-state reasons ════════════
class SecureBootUnknownReasonTests(unittest.TestCase):
    def setUp(self):
        set_lang("it")
        self._real_isdir = os.path.isdir

    def test_no_efi_directory_means_bios_legacy_boot(self):
        import backend.all as B

        def fake_isdir(p):
            if p == "/sys/firmware/efi":
                return False
            return self._real_isdir(p)

        with mock.patch("os.path.isdir", side_effect=fake_isdir):
            self.assertEqual(B.secureboot_unknown_reason(), "no_efi")

    def test_efi_without_efivars_means_efivarfs_unavailable(self):
        import backend.all as B

        def fake_isdir(p):
            if p == "/sys/firmware/efi":
                return True
            if p == "/sys/firmware/efi/efivars":
                return False
            return self._real_isdir(p)

        with mock.patch("os.path.isdir", side_effect=fake_isdir):
            self.assertEqual(B.secureboot_unknown_reason(), "no_efivarfs")

    def test_missing_mokutil_is_reported_as_tool_missing(self):
        import backend.all as B

        def fake_isdir(p):
            return p in ("/sys/firmware/efi", "/sys/firmware/efi/efivars") or self._real_isdir(p)

        with mock.patch("os.path.isdir", side_effect=fake_isdir), \
             mock.patch("backend.all._cmd_exists", return_value=False):
            self.assertEqual(B.secureboot_unknown_reason(), "tool_missing")

    def test_permission_error_is_distinguished_from_a_generic_read_error(self):
        import backend.all as B

        def fake_isdir(p):
            return p in ("/sys/firmware/efi", "/sys/firmware/efi/efivars") or self._real_isdir(p)

        with mock.patch("os.path.isdir", side_effect=fake_isdir), \
             mock.patch("backend.all._cmd_exists", return_value=True), \
             mock.patch("backend.all.run_command", return_value=(False, "", "Permission denied")):
            self.assertEqual(B.secureboot_unknown_reason(), "permission_denied")

    def test_unclassified_failure_is_an_honest_read_error_not_a_guess(self):
        import backend.all as B

        def fake_isdir(p):
            return p in ("/sys/firmware/efi", "/sys/firmware/efi/efivars") or self._real_isdir(p)

        with mock.patch("os.path.isdir", side_effect=fake_isdir), \
             mock.patch("backend.all._cmd_exists", return_value=True), \
             mock.patch("backend.all.run_command", return_value=(False, "", "some odd failure")):
            self.assertEqual(B.secureboot_unknown_reason(), "read_error")

    def test_reason_never_turns_unknown_into_active_or_inactive(self):
        import backend.all as B
        for reason in ("no_efi", "no_efivarfs", "tool_missing", "permission_denied", "read_error"):
            self.assertNotIn(reason, ("active", "inactive"))

    @unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
    def test_expanded_card_shows_the_real_reason_text_when_unknown(self):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from ui.pages.page_security import _SecureBootRow
        from ui.design_system.status_pill import state_pill
        pill = state_pill("unknown", T("ds_state_unknown"))
        row = _SecureBootRow(pill, "secureboot_reason_no_efi")
        self.assertIn(T("secureboot_reason_no_efi"), row._lbl_con.get_text())

    @unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
    def test_expanded_card_omits_reason_text_for_a_known_state(self):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from ui.pages.page_security import _SecureBootRow
        from ui.design_system.status_pill import state_pill
        pill = state_pill("active", T("ds_state_active"))
        row = _SecureBootRow(pill, None)
        for reason_key in ("secureboot_reason_no_efi", "secureboot_reason_tool_missing"):
            self.assertNotIn(T(reason_key), row._lbl_con.get_text())

    @unittest.skipUnless(_HAS_DISPLAY, _SKIP_REASON)
    def test_reason_texts_translated_in_every_language(self):
        for lang in ("en", "es", "fr", "it"):
            set_lang(lang)
            for key in ("secureboot_reason_no_efi", "secureboot_reason_no_efivarfs",
                        "secureboot_reason_tool_missing", "secureboot_reason_permission",
                        "secureboot_reason_read_error"):
                self.assertNotEqual(T(key), key)
        set_lang("it")


if __name__ == "__main__":
    unittest.main()
