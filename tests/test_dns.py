"""
Tests for the DNS subsystem (core/network/). Everything here is mocked
at the run_command/socket level — this suite NEVER touches the real
host's DNS configuration, per explicit instruction: only a final,
separately-confirmed manual test does that.
"""
import socket
import sys
import os
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.network import dns_detector, dns_networkmanager as nm, dns_resolved as resolved
from core.network import dns_manager, dns_providers
from core.network.dns_models import BackendKind, DnsSnapshot
from core.network.dns_validator import validate_servers, split_by_family


# ── Validator (pure, no mocking needed) ──────────────────────────────────
class ValidatorTests(unittest.TestCase):
    def test_valid_ipv4_list(self):
        ok, cleaned = validate_servers(["1.1.1.1", "1.0.0.1"])
        self.assertTrue(ok)
        self.assertEqual(cleaned, ["1.1.1.1", "1.0.0.1"])

    def test_valid_ipv6_list(self):
        ok, cleaned = validate_servers(["2606:4700:4700::1111"])
        self.assertTrue(ok)
        self.assertEqual(cleaned, ["2606:4700:4700::1111"])

    def test_invalid_address_rejected(self):
        ok, _ = validate_servers(["not-an-ip"])
        self.assertFalse(ok)

    def test_shell_metacharacters_rejected(self):
        ok, _ = validate_servers(["1.1.1.1; rm -rf /"])
        self.assertFalse(ok)

    def test_empty_list_is_valid(self):
        ok, cleaned = validate_servers([])
        self.assertTrue(ok)
        self.assertEqual(cleaned, [])

    def test_too_many_servers_rejected(self):
        ok, _ = validate_servers(["1.1.1.1", "1.0.0.1", "8.8.8.8", "9.9.9.9"])
        self.assertFalse(ok)

    def test_duplicate_addresses_deduplicated(self):
        ok, cleaned = validate_servers(["1.1.1.1", "1.1.1.1"])
        self.assertTrue(ok)
        self.assertEqual(cleaned, ["1.1.1.1"])

    def test_split_by_family(self):
        v4, v6 = split_by_family(["1.1.1.1", "2606:4700:4700::1111", "8.8.8.8"])
        self.assertEqual(v4, ["1.1.1.1", "8.8.8.8"])
        self.assertEqual(v6, ["2606:4700:4700::1111"])


# ── Backend / connection detection ───────────────────────────────────────
class DetectorTests(unittest.TestCase):
    def test_networkmanager_detected(self):
        with mock.patch.object(dns_detector, "_cmd_exists", return_value=True), \
             mock.patch.object(dns_detector, "_service_active", return_value=True):
            self.assertEqual(dns_detector.detect_backend(), BackendKind.NETWORKMANAGER)

    def test_resolved_only_detected_when_no_networkmanager(self):
        def fake_active(name):
            return name == "systemd-resolved"
        with mock.patch.object(dns_detector, "_cmd_exists", return_value=True), \
             mock.patch.object(dns_detector, "_service_active", side_effect=fake_active):
            self.assertEqual(dns_detector.detect_backend(), BackendKind.RESOLVED_ONLY)

    def test_unknown_backend_when_neither_present(self):
        with mock.patch.object(dns_detector, "_cmd_exists", return_value=False), \
             mock.patch.object(dns_detector, "_service_active", return_value=False):
            self.assertEqual(dns_detector.detect_backend(), BackendKind.UNKNOWN)

    def test_ethernet_connection_detected_as_primary(self):
        nmcli_output = (
            "Wired connection 1:802-3-ethernet:uuid-eth:eth0\n"
            "docker0:bridge:uuid-docker:docker0\n"
        )
        def fake_run(cmd):
            if cmd[:2] == ["nmcli", "-t"] and "connection" in cmd:
                return True, nmcli_output, ""
            if cmd == ["ip", "route", "show", "default"]:
                return True, "default via 192.168.1.1 dev eth0 proto dhcp metric 100", ""
            return False, "", ""
        with mock.patch("core.network.dns_detector.run_command", side_effect=fake_run):
            conns = dns_detector.list_connections()
            self.assertEqual(len(conns), 1)  # bridge excluded
            self.assertEqual(conns[0].conn_type, "802-3-ethernet")
            primary = dns_detector.primary_connection()
            self.assertEqual(primary.uuid, "uuid-eth")

    def test_wifi_connection_detected(self):
        nmcli_output = "MyWifi:802-11-wireless:uuid-wifi:wlan0\n"
        def fake_run(cmd):
            if "connection" in cmd:
                return True, nmcli_output, ""
            if cmd == ["ip", "route", "show", "default"]:
                return True, "default via 192.168.1.1 dev wlan0", ""
            return False, "", ""
        with mock.patch("core.network.dns_detector.run_command", side_effect=fake_run):
            conns = dns_detector.list_connections()
            self.assertEqual(conns[0].conn_type, "802-11-wireless")
            self.assertTrue(conns[0].is_default_route)

    def test_vpn_flagged_not_excluded(self):
        nmcli_output = (
            "Home:802-3-ethernet:uuid-eth:eth0\n"
            "MyVPN:vpn:uuid-vpn:tun0\n"
        )
        def fake_run(cmd):
            if "connection" in cmd:
                return True, nmcli_output, ""
            if cmd == ["ip", "route", "show", "default"]:
                return True, "default via 192.168.1.1 dev eth0", ""
            return False, "", ""
        with mock.patch("core.network.dns_detector.run_command", side_effect=fake_run):
            conns = dns_detector.list_connections()
            self.assertEqual(len(conns), 2)
            self.assertTrue(dns_detector.has_vpn_active())

    def test_multiple_non_vpn_connections_no_default_route_is_ambiguous(self):
        nmcli_output = (
            "Home:802-3-ethernet:uuid-eth:eth0\n"
            "Office:802-11-wireless:uuid-wifi:wlan0\n"
        )
        def fake_run(cmd):
            if "connection" in cmd:
                return True, nmcli_output, ""
            if cmd == ["ip", "route", "show", "default"]:
                return True, "", ""  # no default route info available
            return False, "", ""
        with mock.patch("core.network.dns_detector.run_command", side_effect=fake_run):
            self.assertIsNone(dns_detector.primary_connection())


# ── NetworkManager backend ────────────────────────────────────────────────
class NetworkManagerBackendTests(unittest.TestCase):
    def test_read_snapshot(self):
        def fake_get(cmd):
            field = cmd[2]
            values = {
                "ipv4.dns": "1.1.1.1,1.0.0.1",
                "ipv4.ignore-auto-dns": "yes",
                "ipv6.dns": "",
                "ipv6.ignore-auto-dns": "no",
            }
            return True, values.get(field, ""), ""
        with mock.patch("core.network.dns_networkmanager.run_command", side_effect=fake_get):
            snap = nm.read_snapshot("uuid-1")
            self.assertEqual(snap.ipv4_dns, ["1.1.1.1", "1.0.0.1"])
            self.assertTrue(snap.ipv4_ignore_auto_dns)
            self.assertEqual(snap.ipv6_dns, [])
            self.assertFalse(snap.ipv6_ignore_auto_dns)

    def test_ipv6_available_true_for_auto_method(self):
        with mock.patch("core.network.dns_networkmanager.run_command", return_value=(True, "auto", "")):
            self.assertTrue(nm.ipv6_available("uuid-1"))

    def test_ipv6_unavailable_when_disabled(self):
        with mock.patch("core.network.dns_networkmanager.run_command", return_value=(True, "disabled", "")):
            self.assertFalse(nm.ipv6_available("uuid-1"))

    def test_apply_dns_calls_modify_then_up(self):
        calls = []
        def fake_pkexec(args, timeout=None):
            calls.append(args)
            return True, "", ""
        with mock.patch("core.network.dns_networkmanager.run_pkexec", side_effect=fake_pkexec), \
             mock.patch.object(nm, "ipv6_available", return_value=True):
            ok = nm.apply_dns("uuid-1", ["1.1.1.1"], ["2606:4700:4700::1111"])
        self.assertTrue(ok)
        self.assertEqual(calls[0][:3], ["nmcli", "connection", "modify"])
        self.assertIn("ipv4.dns", calls[0])
        self.assertEqual(calls[1], ["nmcli", "connection", "up", "uuid-1"])

    def test_apply_dns_failure_does_not_reactivate(self):
        with mock.patch("core.network.dns_networkmanager.run_pkexec", return_value=(False, "", "error")):
            ok = nm.apply_dns("uuid-1", ["1.1.1.1"], [])
        self.assertFalse(ok)


# ── resolved-only backend ────────────────────────────────────────────────
class ResolvedBackendTests(unittest.TestCase):
    def test_list_links_skips_loopback(self):
        status_output = (
            "Link 2 (eth0)\n"
            "      Current Scopes: DNS\n"
            "Link 1 (lo)\n"
        )
        with mock.patch("core.network.dns_resolved.run_command", return_value=(True, status_output, "")):
            links = resolved.list_links()
        self.assertEqual(links, [("2", "eth0")])

    def test_apply_dns_temporary_requires_servers(self):
        with mock.patch("core.network.dns_resolved.run_pkexec") as p:
            ok = resolved.apply_dns_temporary("eth0", [])
        self.assertFalse(ok)
        p.assert_not_called()


# ── Full manager flow ─────────────────────────────────────────────────────
class DnsManagerTests(unittest.TestCase):
    def _fake_connection(self, uuid="uuid-1", conn_type="802-3-ethernet"):
        from core.network.dns_models import NetworkConnection
        return NetworkConnection(uuid=uuid, name="Test", conn_type=conn_type, device="eth0", is_vpn=False)

    def test_successful_apply_and_verify(self):
        with mock.patch.object(dns_manager.dns_detector, "detect_backend", return_value=BackendKind.NETWORKMANAGER), \
             mock.patch.object(dns_manager.dns_detector, "list_connections", return_value=[self._fake_connection()]), \
             mock.patch.object(dns_manager.nm, "read_snapshot", return_value=DnsSnapshot(uuid="uuid-1")), \
             mock.patch.object(dns_manager.nm, "apply_dns", return_value=True), \
             mock.patch.object(dns_manager, "_dns_resolution_works", return_value=True), \
             mock.patch.object(dns_manager.history_log, "append"):
            result = dns_manager.try_provider("uuid-1", dns_providers.CLOUDFLARE)
        self.assertTrue(result.ok)
        self.assertTrue(result.verified)

    def test_resolution_failure_triggers_rollback(self):
        # First check (right after applying the new DNS) fails; second
        # check (right after rolling back) succeeds — proving the
        # rollback itself is verified too, not just assumed.
        with mock.patch.object(dns_manager.dns_detector, "detect_backend", return_value=BackendKind.NETWORKMANAGER), \
             mock.patch.object(dns_manager.dns_detector, "list_connections", return_value=[self._fake_connection()]), \
             mock.patch.object(dns_manager.nm, "read_snapshot", return_value=DnsSnapshot(uuid="uuid-1")), \
             mock.patch.object(dns_manager.nm, "apply_dns", return_value=True), \
             mock.patch.object(dns_manager.nm, "restore_snapshot", return_value=True) as restore, \
             mock.patch.object(dns_manager, "_dns_resolution_works", side_effect=[False, True]), \
             mock.patch.object(dns_manager.history_log, "append"):
            result = dns_manager.try_provider("uuid-1", dns_providers.CLOUDFLARE)
        restore.assert_called_once()
        self.assertFalse(result.ok)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.friendly_message, "dns_verification_failed_restored")

    def test_rollback_itself_failing_is_reported_distinctly(self):
        with mock.patch.object(dns_manager.dns_detector, "detect_backend", return_value=BackendKind.NETWORKMANAGER), \
             mock.patch.object(dns_manager.dns_detector, "list_connections", return_value=[self._fake_connection()]), \
             mock.patch.object(dns_manager.nm, "read_snapshot", return_value=DnsSnapshot(uuid="uuid-1")), \
             mock.patch.object(dns_manager.nm, "apply_dns", return_value=True), \
             mock.patch.object(dns_manager.nm, "restore_snapshot", return_value=False), \
             mock.patch.object(dns_manager, "_dns_resolution_works", return_value=False), \
             mock.patch.object(dns_manager.history_log, "append"):
            result = dns_manager.try_provider("uuid-1", dns_providers.CLOUDFLARE)
        self.assertFalse(result.ok)
        self.assertFalse(result.rolled_back)
        self.assertEqual(result.friendly_message, "dns_rollback_failed")

    def test_apply_itself_failing(self):
        with mock.patch.object(dns_manager.dns_detector, "detect_backend", return_value=BackendKind.NETWORKMANAGER), \
             mock.patch.object(dns_manager.dns_detector, "list_connections", return_value=[self._fake_connection()]), \
             mock.patch.object(dns_manager.nm, "read_snapshot", return_value=DnsSnapshot(uuid="uuid-1")), \
             mock.patch.object(dns_manager.nm, "apply_dns", return_value=False), \
             mock.patch.object(dns_manager.history_log, "append"):
            result = dns_manager.try_provider("uuid-1", dns_providers.CLOUDFLARE)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "dns_apply_failed")

    def test_connection_disappeared(self):
        with mock.patch.object(dns_manager.dns_detector, "detect_backend", return_value=BackendKind.NETWORKMANAGER), \
             mock.patch.object(dns_manager.dns_detector, "list_connections", return_value=[]):
            result = dns_manager.try_provider("uuid-gone", dns_providers.CLOUDFLARE)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "dns_connection_not_found")

    def test_unknown_backend_refuses(self):
        with mock.patch.object(dns_manager.dns_detector, "detect_backend", return_value=BackendKind.UNKNOWN):
            result = dns_manager.try_provider("uuid-1", dns_providers.CLOUDFLARE)
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "dns_backend_not_writable")

    def test_invalid_custom_address_rejected_before_touching_network(self):
        with mock.patch.object(dns_manager.dns_detector, "detect_backend", return_value=BackendKind.NETWORKMANAGER), \
             mock.patch.object(dns_manager.dns_detector, "list_connections", return_value=[self._fake_connection()]), \
             mock.patch.object(dns_manager.nm, "apply_dns") as apply_dns:
            result = dns_manager.try_provider("uuid-1", dns_providers.CUSTOM, custom_ipv4=["not-an-ip"])
        apply_dns.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.friendly_message, "dns_invalid_custom_address")

    def test_verification_uses_real_dns_query_not_ping(self):
        """The whole point of _dns_resolution_works is that it performs
        actual name resolution (socket.getaddrinfo), not an ICMP ping."""
        with mock.patch("socket.getaddrinfo") as getaddrinfo:
            dns_manager._dns_resolution_works()
        getaddrinfo.assert_called_once()

    def test_verification_timeout_counts_as_failure(self):
        with mock.patch("socket.getaddrinfo", side_effect=socket.timeout()):
            self.assertFalse(dns_manager._dns_resolution_works())


# ── Architectural guarantee: no GTK import in the backend ────────────────
class NoGtkCouplingTests(unittest.TestCase):
    def test_network_modules_do_not_import_gi(self):
        import core.network.dns_models, core.network.dns_providers, core.network.dns_validator
        import core.network.dns_detector, core.network.dns_networkmanager, core.network.dns_resolved
        import core.network.dns_manager
        for mod in (core.network.dns_models, core.network.dns_providers, core.network.dns_validator,
                    core.network.dns_detector, core.network.dns_networkmanager, core.network.dns_resolved,
                    core.network.dns_manager):
            self.assertNotIn("gi", dir(mod))


if __name__ == "__main__":
    unittest.main()
