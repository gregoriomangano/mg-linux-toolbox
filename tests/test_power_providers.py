"""
Resolver tests for core.power_providers — run entirely against fakes
(monkeypatched _cmd_exists/_service_active/_service_known), so they pass
identically regardless of which machine runs them. Real detection on an
actual Pop!_OS system is exercised separately, manually, not here.
"""
import unittest
from unittest import mock

from core import power_providers as pp


def _fake(cmd_exists=(), active_services=(), known_services=()):
    """Returns a mock.patch.multiple-friendly set of replacement functions."""
    cmd_exists_set = set(cmd_exists)
    active_set = set(active_services)
    known_set = set(known_services)

    def fake_cmd_exists(name):
        return name in cmd_exists_set

    def fake_service_active(name):
        return name in active_set

    def fake_service_known(name):
        return name in known_set

    return fake_cmd_exists, fake_service_active, fake_service_known


class PopOSScenarioTests(unittest.TestCase):
    """Pop!_OS ships system76-power by default; installing
    power-profiles-daemon on top must never be silently offered."""

    def test_system76_power_active_wins_and_blocks_install_offer(self):
        cmd_exists, service_active, service_known = _fake(
            cmd_exists=("system76-power",),
            active_services=("system76-power",),
        )
        with mock.patch.object(pp, "_cmd_exists", side_effect=cmd_exists), \
             mock.patch.object(pp, "_service_active", side_effect=service_active), \
             mock.patch.object(pp, "_service_known", side_effect=service_known):
            result = pp.resolve()
        self.assertEqual(result["active"], "system76-power")
        self.assertFalse(result["should_offer_install"])

    def test_power_profiles_daemon_active_alone(self):
        cmd_exists, service_active, service_known = _fake(
            cmd_exists=("powerprofilesctl",),
            active_services=("power-profiles-daemon",),
        )
        with mock.patch.object(pp, "_cmd_exists", side_effect=cmd_exists), \
             mock.patch.object(pp, "_service_active", side_effect=service_active), \
             mock.patch.object(pp, "_service_known", side_effect=service_known):
            result = pp.resolve()
        self.assertEqual(result["active"], "power-profiles-daemon")
        self.assertFalse(result["should_offer_install"])

    def test_nothing_present_offers_install(self):
        cmd_exists, service_active, service_known = _fake()
        with mock.patch.object(pp, "_cmd_exists", side_effect=cmd_exists), \
             mock.patch.object(pp, "_service_active", side_effect=service_active), \
             mock.patch.object(pp, "_service_known", side_effect=service_known):
            result = pp.resolve()
        self.assertIsNone(result["active"])
        self.assertEqual(result["installed_inactive"], [])
        self.assertTrue(result["should_offer_install"])

    def test_installed_but_inactive_still_blocks_install_offer(self):
        """A provider present-but-stopped must not be silently doubled up
        on — installing a second one could start it and collide with the
        first the moment something re-enables it."""
        cmd_exists, service_active, service_known = _fake(
            cmd_exists=("tlp",),
        )
        with mock.patch.object(pp, "_cmd_exists", side_effect=cmd_exists), \
             mock.patch.object(pp, "_service_active", side_effect=service_active), \
             mock.patch.object(pp, "_service_known", side_effect=service_known):
            result = pp.resolve()
        self.assertIsNone(result["active"])
        self.assertIn("tlp", result["installed_inactive"])
        self.assertFalse(result["should_offer_install"])

    def test_priority_order_system76_power_over_ppd_if_both_somehow_active(self):
        """Shouldn't normally happen (they Conflict= at the systemd level),
        but if detection ever sees both as active, System76 Power must be
        reported first since Pop!_OS treats it as canonical."""
        cmd_exists, service_active, service_known = _fake(
            cmd_exists=("system76-power", "powerprofilesctl"),
            active_services=("system76-power", "power-profiles-daemon"),
        )
        with mock.patch.object(pp, "_cmd_exists", side_effect=cmd_exists), \
             mock.patch.object(pp, "_service_active", side_effect=service_active), \
             mock.patch.object(pp, "_service_known", side_effect=service_known):
            result = pp.resolve()
        self.assertEqual(result["active"], "system76-power")


if __name__ == "__main__":
    unittest.main()
