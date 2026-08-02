"""
Tests for core.audio_devices and core.kernel_features.audio_power.
This machine has real PipeWire/WirePlumber and a real snd_hda_intel
power_save sysfs interface, so a few assertions are genuine real-machine
checks (kept minimal and clearly labeled); everything else is exercised
against fakes/mocks for determinism.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import audio_devices as ad
from core.kernel_features.base import SupportStatus
from core.kernel_features.audio_power import AudioPowerSaveFeature
from core import priv_writer
from core.persistence.rollback_store import JsonStateStore


_SINKS_JSON = json.dumps([
    {"name": "alsa_output.pci-0000_2f_00.1.hdmi-stereo", "description": "Navi 21/23 HDMI Audio",
     "properties": {"device.bus": "pci"}},
    {"name": "bluez_output.AA_BB_CC.a2dp-sink", "description": "YXSM7070BT",
     "properties": {"device.bus": "bluetooth"}},
    {"name": "alsa_output.pci-0000_2f_00.4.analog-stereo", "description": "Starship/Matisse IEC958",
     "properties": {"device.bus": "pci"}},
])

_SOURCES_JSON = json.dumps([
    {"name": "alsa_input.usb-Blue_Snowball.mono-fallback", "description": "Blue Snowball",
     "properties": {"device.bus": "usb"}},
    {"name": "alsa_output.pci-0000_2f_00.4.analog-stereo.monitor", "description": "Monitor of Starship",
     "properties": {"device.bus": "pci"}},
])


class ClassifyTests(unittest.TestCase):
    def test_hdmi_by_name(self):
        self.assertEqual(ad._classify({"name": "x.hdmi-stereo", "description": "", "properties": {}}), "hdmi")

    def test_bluetooth_by_bus(self):
        entry = {"name": "bluez_output.x", "description": "Headset",
                  "properties": {"device.bus": "bluetooth"}}
        self.assertEqual(ad._classify(entry), "bluetooth")

    def test_usb_by_name(self):
        entry = {"name": "alsa_input.usb-Blue_Snowball", "description": "", "properties": {}}
        self.assertEqual(ad._classify(entry), "usb")

    def test_defaults_to_speakers(self):
        entry = {"name": "alsa_output.pci-x.analog-stereo", "description": "", "properties": {}}
        self.assertEqual(ad._classify(entry), "speakers")


class ListNodesTests(unittest.TestCase):
    @mock.patch.object(ad, "run_command")
    def test_list_outputs_parses_json(self, mock_run):
        mock_run.return_value = (True, _SINKS_JSON, "")
        outputs = ad.list_outputs()
        self.assertEqual(len(outputs), 3)
        self.assertEqual(outputs[0]["category"], "hdmi")
        self.assertEqual(outputs[1]["category"], "bluetooth")

    @mock.patch.object(ad, "run_command")
    def test_list_inputs_excludes_monitor_sources(self, mock_run):
        mock_run.return_value = (True, _SOURCES_JSON, "")
        inputs = ad.list_inputs()
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0]["name"], "alsa_input.usb-Blue_Snowball.mono-fallback")

    @mock.patch.object(ad, "run_command")
    def test_list_nodes_returns_empty_on_command_failure(self, mock_run):
        mock_run.return_value = (False, "", "pactl not found")
        self.assertEqual(ad.list_outputs(), [])

    @mock.patch.object(ad, "run_command")
    def test_list_nodes_returns_empty_on_bad_json(self, mock_run):
        mock_run.return_value = (True, "not json", "")
        self.assertEqual(ad.list_outputs(), [])


class DefaultDeviceTests(unittest.TestCase):
    @mock.patch.object(ad, "run_command")
    def test_get_default_output(self, mock_run):
        mock_run.return_value = (True, "bluez_output.AA_BB_CC.a2dp-sink\n", "")
        self.assertEqual(ad.get_default_output(), "bluez_output.AA_BB_CC.a2dp-sink")

    @mock.patch.object(ad, "run_command")
    def test_set_default_output_verifies_by_rereading(self, mock_run):
        mock_run.side_effect = [(True, "", ""), (True, "alsa_output.hdmi\n", "")]
        self.assertTrue(ad.set_default_output("alsa_output.hdmi"))

    @mock.patch.object(ad, "run_command")
    def test_set_default_output_fails_if_reread_mismatches(self, mock_run):
        mock_run.side_effect = [(True, "", ""), (True, "something-else\n", "")]
        self.assertFalse(ad.set_default_output("alsa_output.hdmi"))


class AudioServicesTests(unittest.TestCase):
    @mock.patch.object(ad, "run_command")
    def test_detect_only_known_services(self, mock_run):
        def fake(cmd, *a, **kw):
            if "list-unit-files" in cmd:
                name = cmd[-1]
                if name in ("pipewire.service", "pipewire-pulse.service", "wireplumber.service"):
                    return (True, name, "")
                return (True, "", "")
            return (True, "", "")
        mock_run.side_effect = fake
        detected = ad.detect_audio_services()
        self.assertEqual(set(detected), {"pipewire", "pipewire-pulse", "wireplumber"})

    @mock.patch.object(ad, "run_command")
    def test_restart_returns_false_when_no_services_present(self, mock_run):
        mock_run.return_value = (True, "", "")
        self.assertFalse(ad.restart_audio_services())

    @mock.patch.object(ad, "_user_service_active")
    @mock.patch.object(ad, "detect_audio_services")
    @mock.patch.object(ad, "run_command")
    def test_restart_verifies_all_active_after(self, mock_run, mock_detect, mock_active):
        mock_detect.return_value = ["pipewire", "wireplumber"]
        mock_active.return_value = True
        self.assertTrue(ad.restart_audio_services())

    def test_real_pactl_present_on_this_machine(self):
        # Genuine real-machine check: this dev box has a working PipeWire
        # stack, so pactl must exist and answer.
        ok, out, _ = ad.run_command(["pactl", "--version"])
        self.assertTrue(ok)
        self.assertTrue(out.strip())


class FakeSysRootTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sys_root = os.path.join(self.tmp, "sys")
        self.params_dir = os.path.join(self.sys_root, "module", "snd_hda_intel", "parameters")
        os.makedirs(self.params_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, value):
        with open(os.path.join(self.params_dir, name), "w") as f:
            f.write(value)


class AudioPowerSaveFeatureTests(FakeSysRootTestCase):
    def test_probe_unsupported_when_no_module_param(self):
        feature = AudioPowerSaveFeature(sys_root=self.sys_root)
        self.assertEqual(feature.probe(), SupportStatus.UNSUPPORTED_KERNEL)

    def test_probe_supported_when_present(self):
        self._write("power_save", "1")
        feature = AudioPowerSaveFeature(sys_root=self.sys_root)
        self.assertEqual(feature.probe(), SupportStatus.SUPPORTED_RUNTIME)

    def test_read_current_without_controller_option(self):
        self._write("power_save", "10")
        feature = AudioPowerSaveFeature(sys_root=self.sys_root)
        result = feature.read_current()
        self.assertTrue(result.ok)
        self.assertEqual(result.value, {"seconds": 10, "controller": None})
        self.assertFalse(feature.has_controller_option())

    def test_read_current_with_controller_option(self):
        self._write("power_save", "5")
        self._write("power_save_controller", "Y")
        feature = AudioPowerSaveFeature(sys_root=self.sys_root)
        result = feature.read_current()
        self.assertTrue(result.ok)
        self.assertEqual(result.value, {"seconds": 5, "controller": True})

    def test_to_friendly_zero_is_always_on(self):
        feature = AudioPowerSaveFeature(sys_root=self.sys_root)
        self.assertEqual(feature.to_friendly({"seconds": 0, "controller": None}), "audio_power_always_on")
        self.assertEqual(feature.to_friendly({"seconds": 10, "controller": None}), "10")

    def test_validate_rejects_out_of_range(self):
        feature = AudioPowerSaveFeature(sys_root=self.sys_root)
        self.assertFalse(feature.validate({"seconds": -1}))
        self.assertFalse(feature.validate({"seconds": 999999}))
        self.assertFalse(feature.validate({"seconds": "10"}))
        self.assertTrue(feature.validate({"seconds": 10}))

    def test_real_machine_snd_hda_intel_present_or_absent(self):
        # Genuine real-machine probe — no assumption about the outcome,
        # just that probe() doesn't blow up and returns a known status.
        feature = AudioPowerSaveFeature()
        self.assertIn(feature.probe(), (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.UNAVAILABLE,
                                         SupportStatus.UNSUPPORTED_KERNEL))


class PrivWriterAudioPowerSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "power_save")
        self.controller_path = os.path.join(self.tmp, "power_save_controller")
        with open(self.path, "w") as f:
            f.write("1")
        with open(self.controller_path, "w") as f:
            f.write("Y")
        self.state = JsonStateStore(os.path.join(self.tmp, "state.json"))
        self.writer = priv_writer.AudioPowerSaveWriter()
        self.writer.PATH = self.path
        self.writer.CONTROLLER_PATH = self.controller_path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_and_restore(self):
        payload = json.dumps({"seconds": 10, "controller": False})
        result = self.writer.apply_temporary(payload, None, False, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], {"seconds": 10, "controller": False})

        restored = self.writer.restore(None, None, False, self.state)
        self.assertTrue(restored["ok"])
        self.assertEqual(restored["value"], {"seconds": 1, "controller": True})

    def test_apply_without_controller_leaves_it_untouched(self):
        payload = json.dumps({"seconds": 5})
        result = self.writer.apply_temporary(payload, None, False, self.state)
        self.assertTrue(result["ok"])
        with open(self.controller_path) as f:
            self.assertEqual(f.read().strip(), "Y")

    def test_rejects_out_of_range_seconds(self):
        payload = json.dumps({"seconds": 999999})
        result = self.writer.apply_temporary(payload, None, False, self.state)
        self.assertFalse(result["ok"])

    def test_rejects_invalid_payload(self):
        result = self.writer.apply_temporary("not json", None, False, self.state)
        self.assertFalse(result["ok"])

    def test_restore_without_prior_apply_fails_cleanly(self):
        result = self.writer.restore(None, None, False, self.state)
        self.assertFalse(result["ok"])

    def test_restore_detects_external_change(self):
        payload = json.dumps({"seconds": 10, "controller": False})
        self.writer.apply_temporary(payload, None, False, self.state)
        with open(self.path, "w") as f:
            f.write("42")
        result = self.writer.restore(None, None, False, self.state)
        self.assertFalse(result["ok"])
        self.assertEqual(result["friendly_message"], "kf_external_change_detected")

    def test_restore_forced_ignores_external_change(self):
        payload = json.dumps({"seconds": 10, "controller": False})
        self.writer.apply_temporary(payload, None, False, self.state)
        with open(self.path, "w") as f:
            f.write("42")
        result = self.writer.restore(None, None, True, self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], {"seconds": 1, "controller": True})


if __name__ == "__main__":
    unittest.main()
