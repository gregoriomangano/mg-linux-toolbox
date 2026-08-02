"""
Tests for core.apparmor_setup — real AppArmor service/profile actions.
Every pkexec call and real aa-status/systemctl invocation is mocked;
per-profile "previous mode" state is redirected to a temp file so
nothing here ever touches ~/.local/state/mg-linux-toolbox on the
machine running the suite.
"""
import os
import tempfile
import unittest
from unittest import mock

from core import apparmor_setup as aa

SAMPLE_AA_STATUS = """apparmor module is loaded.
50 profiles are loaded.
48 profiles are in enforce mode.
   /usr/sbin/cupsd
   /usr/bin/firefox
2 profiles are in complain mode.
   /usr/bin/evince
0 processes have profiles defined.
"""


class ParseAaStatusTests(unittest.TestCase):
    def test_parses_enforce_and_complain_sections(self):
        profiles = aa.parse_aa_status(SAMPLE_AA_STATUS)
        by_path = {p["path"]: p["mode"] for p in profiles}
        self.assertEqual(by_path["/usr/sbin/cupsd"], "enforce")
        self.assertEqual(by_path["/usr/bin/firefox"], "enforce")
        self.assertEqual(by_path["/usr/bin/evince"], "complain")

    def test_stops_collecting_after_leaving_a_section(self):
        text = "48 profiles are in enforce mode.\n   /a/b\nsome other line\n   /not/collected\n"
        profiles = aa.parse_aa_status(text)
        self.assertEqual([p["path"] for p in profiles], ["/a/b"])

    def test_empty_output_returns_empty_list(self):
        self.assertEqual(aa.parse_aa_status(""), [])


class AppArmorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        patcher = mock.patch.object(aa, "state_path",
                                     return_value=os.path.join(self._tmpdir.name, "apparmor_profiles.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        logp = mock.patch.object(aa.hs, "record_operation")
        logp.start()
        self.addCleanup(logp.stop)


class ServiceTests(AppArmorTestCase):
    def test_enable_service_calls_systemctl_enable_now(self):
        with mock.patch.object(aa, "run_pkexec") as mock_pkexec, \
             mock.patch.object(aa, "service_active", return_value=True):
            result = aa.enable_service()
        self.assertTrue(result)
        mock_pkexec.assert_called_once_with(["systemctl", "enable", "--now", aa.SERVICE_NAME])

    def test_disable_service_calls_systemctl_disable_now(self):
        with mock.patch.object(aa, "run_pkexec") as mock_pkexec, \
             mock.patch.object(aa, "service_active", return_value=False):
            result = aa.disable_service()
        self.assertTrue(result)
        mock_pkexec.assert_called_once_with(["systemctl", "disable", "--now", aa.SERVICE_NAME])

    def test_reload_profiles(self):
        with mock.patch.object(aa, "run_pkexec", return_value=(True, "", "")) as mock_pkexec:
            self.assertTrue(aa.reload_profiles())
        mock_pkexec.assert_called_once_with(["systemctl", "reload", aa.SERVICE_NAME])


class ProfileActionTests(AppArmorTestCase):
    def test_enforce_profile_records_previous_mode_then_runs_aa_enforce(self):
        with mock.patch.object(aa, "list_profiles", return_value=[{"path": "/usr/bin/evince", "mode": "complain"}]), \
             mock.patch.object(aa, "run_pkexec", return_value=(True, "", "")) as mock_pkexec:
            result = aa.enforce_profile("/usr/bin/evince")
        self.assertTrue(result)
        mock_pkexec.assert_called_once_with(["aa-enforce", "/usr/bin/evince"])
        state = aa.read_json(aa.state_path(), default={})
        self.assertEqual(state["/usr/bin/evince"], "complain")

    def test_complain_profile(self):
        with mock.patch.object(aa, "list_profiles", return_value=[{"path": "/usr/bin/firefox", "mode": "enforce"}]), \
             mock.patch.object(aa, "run_pkexec", return_value=(True, "", "")) as mock_pkexec:
            self.assertTrue(aa.complain_profile("/usr/bin/firefox"))
        mock_pkexec.assert_called_once_with(["aa-complain", "/usr/bin/firefox"])

    def test_disable_profile(self):
        with mock.patch.object(aa, "list_profiles", return_value=[{"path": "/usr/bin/firefox", "mode": "enforce"}]), \
             mock.patch.object(aa, "run_pkexec", return_value=(True, "", "")) as mock_pkexec:
            self.assertTrue(aa.disable_profile("/usr/bin/firefox"))
        mock_pkexec.assert_called_once_with(["aa-disable", "/usr/bin/firefox"])

    def test_previous_mode_only_ever_recorded_once(self):
        with mock.patch.object(aa, "list_profiles", side_effect=[
                [{"path": "/x", "mode": "enforce"}], [{"path": "/x", "mode": "complain"}]]), \
             mock.patch.object(aa, "run_pkexec", return_value=(True, "", "")):
            aa.complain_profile("/x")  # first-seen mode: enforce
            aa.enforce_profile("/x")   # should NOT overwrite with "complain"
        state = aa.read_json(aa.state_path(), default={})
        self.assertEqual(state["/x"], "enforce")


class RestoreProfileTests(AppArmorTestCase):
    def test_restores_to_recorded_previous_mode(self):
        aa.write_json_atomic(aa.state_path(), {"/usr/bin/evince": "complain"}, mode=0o600)
        with mock.patch.object(aa, "run_pkexec", return_value=(True, "", "")) as mock_pkexec:
            result = aa.restore_profile("/usr/bin/evince")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "complain")
        mock_pkexec.assert_called_once_with(["aa-complain", "/usr/bin/evince"])

    def test_restore_removes_entry_after_success(self):
        aa.write_json_atomic(aa.state_path(), {"/usr/bin/evince": "enforce"}, mode=0o600)
        with mock.patch.object(aa, "run_pkexec", return_value=(True, "", "")):
            aa.restore_profile("/usr/bin/evince")
        state = aa.read_json(aa.state_path(), default={})
        self.assertNotIn("/usr/bin/evince", state)

    def test_restore_without_prior_change_fails_cleanly(self):
        result = aa.restore_profile("/usr/bin/unknown")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "nothing_to_restore")

    def test_failed_restore_keeps_state_entry(self):
        aa.write_json_atomic(aa.state_path(), {"/usr/bin/evince": "enforce"}, mode=0o600)
        with mock.patch.object(aa, "run_pkexec", return_value=(False, "", "denied")):
            result = aa.restore_profile("/usr/bin/evince")
        self.assertFalse(result["ok"])
        state = aa.read_json(aa.state_path(), default={})
        self.assertIn("/usr/bin/evince", state)


if __name__ == "__main__":
    unittest.main()
