"""
Tests for core.gaming_readiness and core.game_mode. Real, non-mocked
checks against this machine's actual GPU/Vulkan/GameMode/MangoHud are
done separately (see final report) — this suite uses mocks so it stays
fast and passes on any machine, including ones without a GPU.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import gaming_readiness as gr
from core import game_mode


class GpuDriverTests(unittest.TestCase):
    def test_known_good_driver_is_ready(self):
        with mock.patch.object(gr, "_gpu_driver", return_value="amdgpu"):
            item = gr.check_gpu_driver()
        self.assertEqual(item.state, gr.READY)

    def test_unknown_driver_is_almost_ready(self):
        with mock.patch.object(gr, "_gpu_driver", return_value="some_obscure_driver"):
            item = gr.check_gpu_driver()
        self.assertEqual(item.state, gr.ALMOST_READY)

    def test_no_driver_is_unavailable(self):
        with mock.patch.object(gr, "_gpu_driver", return_value=""):
            item = gr.check_gpu_driver()
        self.assertEqual(item.state, gr.UNAVAILABLE)


class VulkanTests(unittest.TestCase):
    def test_vulkaninfo_missing(self):
        with mock.patch("shutil.which", return_value=None):
            item = gr.check_vulkan()
        self.assertEqual(item.state, gr.MISSING_COMPONENTS)

    def test_vulkaninfo_succeeds(self):
        result = mock.Mock(returncode=0, stdout="Vulkan Instance Version: 1.3.280\n")
        with mock.patch("shutil.which", return_value="/usr/bin/vulkaninfo"), \
             mock.patch("subprocess.run", return_value=result):
            item = gr.check_vulkan()
        self.assertEqual(item.state, gr.READY)

    def test_vulkaninfo_present_but_fails(self):
        result = mock.Mock(returncode=1, stdout="")
        with mock.patch("shutil.which", return_value="/usr/bin/vulkaninfo"), \
             mock.patch("subprocess.run", return_value=result):
            item = gr.check_vulkan()
        self.assertEqual(item.state, gr.MISSING_COMPONENTS)


class GamemodeRealStatusTests(unittest.TestCase):
    def test_not_installed(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(gr.gamemode_real_status(), "not_installed")

    def test_installed_and_real_test_succeeds(self):
        ok = mock.Mock(returncode=0)
        with mock.patch("shutil.which", return_value="/usr/bin/gamemoded"), \
             mock.patch("subprocess.run", return_value=ok):
            self.assertEqual(gr.gamemode_real_status(), "ready")

    def test_installed_but_real_test_fails(self):
        fail = mock.Mock(returncode=1)
        with mock.patch("shutil.which", return_value="/usr/bin/gamemoded"), \
             mock.patch("subprocess.run", return_value=fail):
            self.assertEqual(gr.gamemode_real_status(), "installed_not_ready")


class MangohudRealStatusTests(unittest.TestCase):
    def test_not_installed(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(gr.mangohud_real_status(), "not_installed")

    def test_layer_missing_from_vulkaninfo(self):
        no_layer = mock.Mock(returncode=0, stdout="some other layer\n")
        with mock.patch("shutil.which", return_value="/usr/bin/mangohud"), \
             mock.patch("subprocess.run", return_value=no_layer):
            self.assertEqual(gr.mangohud_real_status(), "installed_not_ready")

    def test_full_success(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd == ["vulkaninfo"]:
                return mock.Mock(returncode=0, stdout="VK_LAYER_MANGOHUD_overlay\n")
            return mock.Mock(returncode=0, stdout="")

        with mock.patch("shutil.which", return_value="/usr/bin/mangohud"), \
             mock.patch("subprocess.run", side_effect=fake_run):
            self.assertEqual(gr.mangohud_real_status(), "ready")
        self.assertIn(["mangohud", "vulkaninfo", "--summary"], calls)


class OverallStateTests(unittest.TestCase):
    def _item(self, item_id, state):
        return gr.ReadinessItem(item_id, "x", state)

    def test_all_ready(self):
        items = [self._item(i, gr.READY) for i in gr._CORE_ITEM_IDS]
        self.assertEqual(gr.overall_state(items), gr.READY)

    def test_gpu_unavailable_forces_unavailable(self):
        items = [self._item("gpu_driver", gr.UNAVAILABLE)] + \
                [self._item(i, gr.READY) for i in gr._CORE_ITEM_IDS if i != "gpu_driver"]
        self.assertEqual(gr.overall_state(items), gr.UNAVAILABLE)

    def test_one_missing_is_almost_ready(self):
        items = [self._item("gamemode", gr.MISSING_COMPONENTS)] + \
                [self._item(i, gr.READY) for i in gr._CORE_ITEM_IDS if i != "gamemode"]
        self.assertEqual(gr.overall_state(items), gr.ALMOST_READY)

    def test_several_missing_is_missing_components(self):
        items = [self._item(i, gr.MISSING_COMPONENTS) for i in list(gr._CORE_ITEM_IDS)[:3]] + \
                [self._item(i, gr.READY) for i in list(gr._CORE_ITEM_IDS)[3:]]
        self.assertEqual(gr.overall_state(items), gr.MISSING_COMPONENTS)


class GameModePlanTests(unittest.TestCase):
    """plan() must only include changes that are both supported AND not
    already at the gaming target — verified with a fully mocked feature
    layer so it never touches the real machine."""

    def _fake_feature(self, supported=True, current_ok=True, current_value=None, available=None):
        from core.kernel_features.base import SupportStatus, OpResult
        f = mock.Mock()
        f.probe.return_value = SupportStatus.SUPPORTED_RUNTIME if supported else SupportStatus.UNSUPPORTED_HARDWARE
        f.read_current.return_value = OpResult(current_ok, value=current_value)
        f.read_available.return_value = available
        return f

    def test_turbo_already_on_excluded(self):
        with mock.patch.object(game_mode, "_feature_for") as feature_for, \
             mock.patch("core.power_providers.resolve", return_value={"active": None}):
            feature_for.side_effect = lambda fid: {
                "cpu.turbo_boost": self._fake_feature(current_value=True),
                "cpu.governor": self._fake_feature(supported=False),
                "cpu.epp": self._fake_feature(supported=False),
                "battery.platform_profile": self._fake_feature(supported=False),
            }[fid]
            changes = game_mode.plan()
        self.assertEqual(changes, [])

    def test_turbo_off_included(self):
        with mock.patch.object(game_mode, "_feature_for") as feature_for, \
             mock.patch("core.power_providers.resolve", return_value={"active": None}):
            feature_for.side_effect = lambda fid: {
                "cpu.turbo_boost": self._fake_feature(current_value=False),
                "cpu.governor": self._fake_feature(supported=False),
                "cpu.epp": self._fake_feature(supported=False),
                "battery.platform_profile": self._fake_feature(supported=False),
            }[fid]
            changes = game_mode.plan()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["id"], "cpu.turbo_boost")
        self.assertEqual(changes[0]["target"], True)

    def test_governor_not_performance_included(self):
        with mock.patch.object(game_mode, "_feature_for") as feature_for, \
             mock.patch("core.power_providers.resolve", return_value={"active": None}):
            feature_for.side_effect = lambda fid: {
                "cpu.turbo_boost": self._fake_feature(current_value=True),
                "cpu.governor": self._fake_feature(current_value="powersave", available=["performance", "powersave"]),
                "cpu.epp": self._fake_feature(supported=False),
                "battery.platform_profile": self._fake_feature(supported=False),
            }[fid]
            changes = game_mode.plan()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["target"], "performance")


class GameModeActivateRollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state_path = os.path.join(self.tmp, "game_mode.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_all_succeed_records_state(self):
        changes = [
            {"kind": "kernel_feature", "id": "cpu.turbo_boost", "target": True, "label_key": "x"},
        ]
        with mock.patch.object(game_mode, "state_path", return_value=self.state_path), \
             mock.patch.object(game_mode, "_apply_one", return_value=True) as apply_one:
            ok, failed = game_mode.activate(changes)
            self.assertTrue(ok)
            self.assertIsNone(failed)
            apply_one.assert_called_once()
            self.assertTrue(game_mode.is_active())

    def test_failure_rolls_back_everything_already_applied(self):
        changes = [
            {"kind": "kernel_feature", "id": "cpu.turbo_boost", "target": True, "label_key": "a"},
            {"kind": "kernel_feature", "id": "cpu.governor", "target": "performance", "label_key": "b"},
        ]
        with mock.patch.object(game_mode, "state_path", return_value=self.state_path), \
             mock.patch.object(game_mode, "_apply_one", side_effect=[True, False]), \
             mock.patch.object(game_mode, "_restore_one") as restore_one:
            ok, failed = game_mode.activate(changes)
            self.assertFalse(ok)
            self.assertEqual(failed["id"], "cpu.governor")
            restore_one.assert_called_once_with(changes[0])
            self.assertFalse(game_mode.is_active())

    def test_deactivate_restores_in_reverse_order(self):
        changes = [
            {"kind": "kernel_feature", "id": "cpu.turbo_boost", "target": True, "label_key": "a"},
            {"kind": "kernel_feature", "id": "cpu.governor", "target": "performance", "label_key": "b"},
        ]
        with mock.patch.object(game_mode, "state_path", return_value=self.state_path), \
             mock.patch.object(game_mode, "_apply_one", return_value=True):
            game_mode.activate(changes)

        restored_order = []
        with mock.patch.object(game_mode, "state_path", return_value=self.state_path), \
             mock.patch.object(game_mode, "_restore_one", side_effect=lambda c: restored_order.append(c["id"])):
            game_mode.deactivate()
            self.assertEqual(restored_order, ["cpu.governor", "cpu.turbo_boost"])
            self.assertFalse(game_mode.is_active())


if __name__ == "__main__":
    unittest.main()
