"""
Tests for core.persistence.checkpoint_store — "Punto di ripristino
Toolbox". Uses small in-memory fake features (not real /proc or /sys)
so the create/plan/restore orchestration is verified independently of
any one real kernel tunable; each fake behaves exactly like a real
KernelFeature from the checkpoint code's point of view: probe(),
read_current(), apply_temporary()/apply_persistent().
"""
import os
import tempfile
import unittest
from unittest import mock

from core.kernel_features.base import OpResult, SupportStatus
from core.persistence import checkpoint_store as cp


class FakeFeature:
    """Minimal stand-in for a KernelFeature. `value` is the "real system
    state" — apply_temporary/apply_persistent mutate it, exactly like a
    real sysfs write would change what the next read_current() sees."""

    def __init__(self, feature_id, value, status=SupportStatus.SUPPORTED_RUNTIME,
                 device_id=None, choices=None, fail_apply=False):
        self.id = feature_id
        self.device_id = device_id
        self.value = value
        self._status = status
        self._choices = choices
        self.fail_apply = fail_apply
        self.apply_calls = []

    def probe(self):
        return self._status

    def read_current(self):
        if self._choices is not None:
            return OpResult(True, value={"available": self._choices, "current": self.value})
        return OpResult(True, value=self.value)

    def apply_temporary(self, value):
        self.apply_calls.append(("temporary", value))
        if self.fail_apply:
            return OpResult(False, friendly_message="kf_err_generic")
        self.value = value
        return OpResult(True, value=value)

    def apply_persistent(self, value):
        self.apply_calls.append(("persistent", value))
        if self.fail_apply:
            return OpResult(False, friendly_message="kf_err_generic")
        self.value = value
        return OpResult(True, value=value)


class CheckpointStoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        patcher = mock.patch.object(cp, "checkpoints_dir", return_value=self._tmpdir.name)
        patcher.start()
        self.addCleanup(patcher.stop)


class CreateTests(CheckpointStoreTestCase):
    def test_creates_entries_only_for_runtime_or_persistent_features(self):
        features = [
            FakeFeature("cpu.governor", "performance"),
            FakeFeature("cpu.psi", "some info", status=SupportStatus.SUPPORTED_READ_ONLY),
            FakeFeature("virt.iommu", "amd_iommu=on"),  # excluded by prefix even if passed explicitly
        ]
        checkpoint = cp.create("Prima di virtualizzazione", features=features)
        feature_ids = {e.feature_id for e in checkpoint.entries}
        self.assertEqual(feature_ids, {"cpu.governor"})

    def test_bracket_notation_values_are_flattened_to_the_scalar(self):
        features = [FakeFeature("memory.thp", "madvise", choices=["always", "madvise", "never"])]
        checkpoint = cp.create("test", features=features)
        self.assertEqual(checkpoint.entries[0].value, "madvise")

    def test_persists_and_can_be_read_back(self):
        features = [FakeFeature("cpu.governor", "performance")]
        checkpoint = cp.create("My checkpoint", features=features)
        reloaded = cp.get(checkpoint.id)
        self.assertEqual(reloaded.name, "My checkpoint")
        self.assertEqual(reloaded.entries[0].feature_id, "cpu.governor")
        self.assertEqual(reloaded.entries[0].value, "performance")

    def test_unreadable_feature_is_skipped_not_fabricated(self):
        broken = FakeFeature("cpu.governor", "performance")
        broken.read_current = lambda: OpResult(False, friendly_message="kf_unavailable")
        checkpoint = cp.create("test", features=[broken])
        self.assertEqual(checkpoint.entries, [])


class ListGetDeleteExportTests(CheckpointStoreTestCase):
    def test_list_checkpoints_summarizes_without_full_entries(self):
        cp.create("A", features=[FakeFeature("cpu.governor", "performance")])
        cp.create("B", features=[FakeFeature("cpu.governor", "powersave")])
        summaries = cp.list_checkpoints()
        self.assertEqual(len(summaries), 2)
        self.assertEqual({s["name"] for s in summaries}, {"A", "B"})
        self.assertEqual(summaries[0]["entry_count"], 1)

    def test_delete_removes_checkpoint(self):
        checkpoint = cp.create("A", features=[FakeFeature("cpu.governor", "performance")])
        self.assertTrue(cp.delete(checkpoint.id))
        self.assertIsNone(cp.get(checkpoint.id))

    def test_delete_missing_returns_false(self):
        self.assertFalse(cp.delete("does-not-exist"))

    def test_export_writes_full_checkpoint_json(self):
        checkpoint = cp.create("A", features=[FakeFeature("cpu.governor", "performance")])
        dest = os.path.join(self._tmpdir.name, "exported.json")
        self.assertTrue(cp.export_checkpoint(checkpoint.id, dest))
        import json
        with open(dest) as f:
            data = json.load(f)
        self.assertEqual(data["name"], "A")


class PlanRestoreTests(CheckpointStoreTestCase):
    def test_plan_only_includes_actually_different_values(self):
        saved = FakeFeature("cpu.governor", "performance")
        checkpoint = cp.create("A", features=[saved])
        current = [FakeFeature("cpu.governor", "powersave")]
        plan = cp.plan_restore(checkpoint.id, features=current)
        self.assertEqual(plan, [{"feature_id": "cpu.governor", "device_id": None,
                                  "mode": "temporary", "current": "powersave", "target": "performance"}])

    def test_plan_is_empty_when_nothing_changed(self):
        checkpoint = cp.create("A", features=[FakeFeature("cpu.governor", "performance")])
        current = [FakeFeature("cpu.governor", "performance")]
        self.assertEqual(cp.plan_restore(checkpoint.id, features=current), [])

    def test_plan_skips_features_no_longer_present(self):
        checkpoint = cp.create("A", features=[FakeFeature("memory.zram", True)])
        plan = cp.plan_restore(checkpoint.id, features=[])
        self.assertEqual(plan, [])


class RestoreTests(CheckpointStoreTestCase):
    def test_full_restore_reports_success_and_actually_applies(self):
        checkpoint = cp.create("A", features=[FakeFeature("cpu.governor", "performance")])
        live = FakeFeature("cpu.governor", "powersave")
        report = cp.restore(checkpoint.id, features=[live], auto_checkpoint=False)
        self.assertEqual(report.status, "success")
        self.assertEqual(live.value, "performance")
        self.assertEqual(live.apply_calls, [("temporary", "performance")])

    def test_permanent_mode_entries_use_apply_persistent(self):
        saved = FakeFeature("memory.swappiness", 10, status=SupportStatus.SUPPORTED_PERSISTENT)
        checkpoint = cp.create("A", features=[saved])
        live = FakeFeature("memory.swappiness", 60, status=SupportStatus.SUPPORTED_PERSISTENT)
        report = cp.restore(checkpoint.id, features=[live], auto_checkpoint=False)
        self.assertEqual(report.status, "success")
        self.assertEqual(live.apply_calls, [("persistent", 10)])

    def test_failed_step_is_never_reported_as_success(self):
        checkpoint = cp.create("A", features=[FakeFeature("cpu.governor", "performance")])
        live = FakeFeature("cpu.governor", "powersave", fail_apply=True)
        report = cp.restore(checkpoint.id, features=[live], auto_checkpoint=False)
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.steps[0].outcome, cp.FAILED)

    def test_partial_restore_across_multiple_features_is_reported_as_partial(self):
        checkpoint = cp.create("A", features=[
            FakeFeature("cpu.governor", "performance"),
            FakeFeature("memory.zram", True),
        ])
        good = FakeFeature("cpu.governor", "powersave")
        bad = FakeFeature("memory.zram", False, fail_apply=True)
        report = cp.restore(checkpoint.id, features=[good, bad], auto_checkpoint=False)
        self.assertEqual(report.status, "partial")
        outcomes = {s.feature_id: s.outcome for s in report.steps}
        self.assertEqual(outcomes["cpu.governor"], cp.APPLIED)
        self.assertEqual(outcomes["memory.zram"], cp.FAILED)

    def test_restore_creates_an_automatic_pre_restore_checkpoint(self):
        checkpoint = cp.create("A", features=[FakeFeature("cpu.governor", "performance")])
        live = FakeFeature("cpu.governor", "powersave")
        report = cp.restore(checkpoint.id, features=[live], auto_checkpoint=True)
        self.assertIsNotNone(report.pre_restore_checkpoint_id)
        pre = cp.get(report.pre_restore_checkpoint_id)
        # the automatic checkpoint must have captured the *pre-restore*
        # value (powersave), not the post-restore one.
        self.assertEqual(pre.entries[0].value, "powersave")

    def test_verification_mismatch_after_apply_is_reported_as_failed(self):
        checkpoint = cp.create("A", features=[FakeFeature("cpu.governor", "performance")])
        live = FakeFeature("cpu.governor", "powersave")
        # Simulate a write that reports success but the kernel silently
        # kept the old value — read_current() must be trusted over the
        # apply() return value.
        real_apply = live.apply_temporary

        def lying_apply(value):
            result = real_apply(value)
            live.value = "powersave"  # kernel didn't actually take it
            return result

        live.apply_temporary = lying_apply
        report = cp.restore(checkpoint.id, features=[live], auto_checkpoint=False)
        self.assertEqual(report.status, "failed")


if __name__ == "__main__":
    unittest.main()
