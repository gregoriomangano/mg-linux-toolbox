import subprocess
import unittest
from unittest import mock

from tests import privilege_guard as guard


class PrivilegeGuardTests(unittest.TestCase):
    def test_popen_blocks_absolute_sudo_and_names_the_test(self):
        with self.assertRaises(guard.PrivilegeCommandBlocked) as caught:
            subprocess.Popen(["/usr/bin/sudo", "true"])
        self.assertIn(self.id(), str(caught.exception))

    def test_run_blocks_pkexec(self):
        with self.assertRaises(guard.PrivilegeCommandBlocked):
            subprocess.run(["pkexec", "true"])

    def test_call_blocks_su(self):
        with self.assertRaises(guard.PrivilegeCommandBlocked):
            subprocess.call(["/bin/su", "-"])

    def test_check_call_blocks_doas_string(self):
        with self.assertRaises(guard.PrivilegeCommandBlocked):
            subprocess.check_call("/usr/bin/doas true", shell=True)

    def test_correct_mock_replaces_guard_without_interference(self):
        with mock.patch("subprocess.run") as run_mock:
            subprocess.run(["sudo", "true"])
        run_mock.assert_called_once_with(["sudo", "true"])

    def test_nested_guard_restores_previous_callables(self):
        before = (subprocess.Popen, subprocess.run,
                  subprocess.call, subprocess.check_call)
        with guard.installed_privilege_guard():
            inside = (subprocess.Popen, subprocess.run,
                      subprocess.call, subprocess.check_call)
            self.assertNotEqual(inside, before)
        after = (subprocess.Popen, subprocess.run,
                 subprocess.call, subprocess.check_call)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
