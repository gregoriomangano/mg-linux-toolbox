"""
Tests for core.software_repo.package_engine — the fixed operation
allow-list used by the "Software e repository" page. Verifies the
closed vocabulary (unknown operation refused, never dispatched) and
that every successful/failed run is logged to History without leaking
secrets.
"""
import tempfile
import unittest
from unittest import mock

from core.software_repo import package_engine as engine
from core.software_repo.distro_profile import DistroProfile, FAMILY_DEBIAN, SYSTEM_TRADITIONAL
from core.persistence.history_store import HistoryStore


def _profile():
    return DistroProfile(id="debian", family=FAMILY_DEBIAN, system_type=SYSTEM_TRADITIONAL, confident=True)


class RunOperationTests(unittest.TestCase):
    def test_unknown_operation_is_refused(self):
        result = engine.run_operation("delete_everything", profile=_profile(), record_history=False)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "engine_operation_unknown")

    def test_every_operation_key_is_wired_to_a_real_callable(self):
        for key, (fn_name, feature_id, entry_type) in engine.OPERATIONS.items():
            self.assertTrue(callable(getattr(engine, fn_name, None)), key)
            self.assertTrue(feature_id.startswith("software_repo."), key)

    def test_operations_are_resolved_dynamically_so_mock_patch_actually_intercepts(self):
        """Regression guard for the real bug this fixed: OPERATIONS used
        to bind the function object at import time, so patching
        package_engine._op_x by name silently missed the dispatch table
        and the ORIGINAL (possibly privileged) function still ran."""
        sentinel = mock.Mock(ok=True, friendly_message="", technical_detail="",
                              reboot_required=False, logout_recommended=False)
        with mock.patch("core.software_repo.package_engine._op_update_indexes",
                         return_value=sentinel) as op_mock:
            engine.run_operation("update_indexes", profile=_profile(), record_history=False)
        op_mock.assert_called_once()

    def test_configure_flatpak_dispatches_and_records_history(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp_db:
            store = HistoryStore(path=tmp_db.name)
            with mock.patch("core.software_repo.package_engine._op_configure_flatpak",
                             return_value=mock.Mock(ok=True, friendly_message="flathub_added_success",
                                                     technical_detail="", reboot_required=False,
                                                     logout_recommended=True)), \
                 mock.patch("core.software_repo.package_engine.record_operation") as rec_mock:
                result = engine.run_operation("configure_flatpak", profile=_profile(), scope="user")
        self.assertTrue(result.ok)
        rec_mock.assert_called_once()
        kwargs = rec_mock.call_args.kwargs
        self.assertEqual(kwargs["page"], "software_repos")
        self.assertNotIn("password", str(kwargs))

    def test_history_recording_failure_never_breaks_the_real_result(self):
        with mock.patch("core.software_repo.package_engine._op_clean_cache",
                         return_value=mock.Mock(ok=True, friendly_message="ok", technical_detail="",
                                                 reboot_required=False, logout_recommended=False)), \
             mock.patch("core.software_repo.package_engine.record_operation", side_effect=RuntimeError("boom")):
            result = engine.run_operation("clean_cache", profile=_profile())
        self.assertTrue(result.ok)

    def test_enable_recipe_passes_scope_as_recipe_id(self):
        with mock.patch("core.software_repo.repo_recipes.enable_recipe") as recipe_mock:
            recipe_mock.return_value = mock.Mock(ok=True, friendly_message="", technical_detail="")
            engine.run_operation("enable_recipe", profile=_profile(), scope="ubuntu_universe", record_history=False)
        recipe_mock.assert_called_once()
        self.assertEqual(recipe_mock.call_args[0][0], "ubuntu_universe")


if __name__ == "__main__":
    unittest.main()
