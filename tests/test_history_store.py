"""
Tests for core.persistence.history_store.HistoryStore — the SQLite-backed
central operations log behind the "Cronologia attività" section.
"""
import os
import tempfile
import unittest

from core.persistence.history_store import HistoryEntry, HistoryStore, ERROR, TEMPORARY_CHANGE


class HistoryStoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.store = HistoryStore(path=os.path.join(self._tmpdir.name, "history.db"))

    def _entry(self, **overrides):
        defaults = dict(
            page="kernel", feature_id="cpu.governor", entry_type=TEMPORARY_CHANGE,
            result="ok", previous_value="powersave", new_value="performance",
            verified_value="performance", distro_id="pop", distro_provider="apt",
            mode="temporary", rollback_available=True,
        )
        defaults.update(overrides)
        return HistoryEntry(**defaults)


class RecordAndQueryTests(HistoryStoreTestCase):
    def test_record_returns_transaction_id_and_is_queryable(self):
        txn_id = self.store.record(self._entry())
        self.assertTrue(txn_id)
        entries = self.store.query()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["transaction_id"], txn_id)
        self.assertEqual(entries[0]["previous_value"], "powersave")
        self.assertEqual(entries[0]["new_value"], "performance")

    def test_get_by_transaction_id(self):
        txn_id = self.store.record(self._entry())
        entry = self.store.get(txn_id)
        self.assertEqual(entry["feature_id"], "cpu.governor")

    def test_get_missing_transaction_returns_none(self):
        self.assertIsNone(self.store.get("does-not-exist"))

    def test_invalid_entry_type_rejected_before_hitting_the_db(self):
        with self.assertRaises(ValueError):
            HistoryEntry(page="kernel", feature_id="x", entry_type="not_a_real_type", result="ok")

    def test_structured_values_round_trip_through_json(self):
        txn_id = self.store.record(self._entry(
            previous_value={"start": 40, "end": 80},
            new_value={"start": 40, "end": 90},
        ))
        entry = self.store.get(txn_id)
        self.assertEqual(entry["previous_value"], {"start": 40, "end": 80})
        self.assertEqual(entry["new_value"], {"start": 40, "end": 90})


class SanitizationTests(HistoryStoreTestCase):
    def test_password_and_token_keys_redacted(self):
        txn_id = self.store.record(self._entry(new_value={
            "password": "hunter2", "token": "abc123", "note": "fine",
        }))
        entry = self.store.get(txn_id)
        self.assertEqual(entry["new_value"]["password"], "***")
        self.assertEqual(entry["new_value"]["token"], "***")
        self.assertEqual(entry["new_value"]["note"], "fine")

    def test_ssid_mac_and_serial_redacted_including_nested(self):
        txn_id = self.store.record(self._entry(new_value={
            "ssid": "HomeWifi",
            "device": {"mac_address": "AA:BB:CC:DD:EE:FF", "serial": "SN12345"},
        }))
        entry = self.store.get(txn_id)
        self.assertEqual(entry["new_value"]["ssid"], "***")
        self.assertEqual(entry["new_value"]["device"]["mac_address"], "***")
        self.assertEqual(entry["new_value"]["device"]["serial"], "***")

    def test_lists_of_dicts_are_sanitized_element_by_element(self):
        txn_id = self.store.record(self._entry(new_value=[
            {"password": "x"}, {"note": "y"},
        ]))
        entry = self.store.get(txn_id)
        self.assertEqual(entry["new_value"], [{"password": "***"}, {"note": "y"}])


class FilteringTests(HistoryStoreTestCase):
    def setUp(self):
        super().setUp()
        self.store.record(self._entry(feature_id="cpu.governor", page="kernel", entry_type=TEMPORARY_CHANGE, result="ok"))
        self.store.record(self._entry(feature_id="virt.ksm", page="virt", entry_type=ERROR, result="failed",
                                       friendly_message="kf_err_permission"))
        self.store.record(self._entry(feature_id="audio.power_save", page="audio", entry_type=TEMPORARY_CHANGE, result="ok"))

    def test_filter_by_page(self):
        entries = self.store.query(page="virt")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["feature_id"], "virt.ksm")

    def test_filter_by_result(self):
        entries = self.store.query(result="failed")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["entry_type"], "error")

    def test_search_matches_feature_id(self):
        entries = self.store.query(search="governor")
        self.assertEqual(len(entries), 1)

    def test_search_matches_friendly_message(self):
        entries = self.store.query(search="permission")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["feature_id"], "virt.ksm")

    def test_no_filters_returns_all_newest_first(self):
        entries = self.store.query()
        self.assertEqual(len(entries), 3)
        # All records share the same second in a fast test run, so we can
        # only assert on stable insertion order via rowid, not timestamp
        # strictly increasing — reverse-chronological still means "the
        # most recently inserted row is not last".
        self.assertEqual(entries[0]["feature_id"], "audio.power_save")


class RestoreAndExportTests(HistoryStoreTestCase):
    def test_mark_restored_sets_timestamp(self):
        txn_id = self.store.record(self._entry())
        self.assertTrue(self.store.mark_restored(txn_id))
        entry = self.store.get(txn_id)
        self.assertIsNotNone(entry["restored_at"])

    def test_mark_restored_missing_transaction_returns_false(self):
        self.assertFalse(self.store.mark_restored("does-not-exist"))

    def test_export_json_writes_all_entries(self):
        self.store.record(self._entry(feature_id="cpu.governor"))
        self.store.record(self._entry(feature_id="virt.ksm"))
        export_path = os.path.join(self._tmpdir.name, "export.json")
        self.store.export_json(export_path)
        import json
        with open(export_path) as f:
            data = json.load(f)
        self.assertEqual(len(data["entries"]), 2)

    def test_clear_removes_everything(self):
        self.store.record(self._entry())
        self.store.clear()
        self.assertEqual(self.store.query(), [])


if __name__ == "__main__":
    unittest.main()
