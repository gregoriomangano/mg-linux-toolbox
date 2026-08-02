"""
Tests for core.uri_launcher — the single safe external-link opener used
by every page (Chi sono, Guida, Crediti, Supporta il progetto). Only a
well-formed https:// URL may ever reach Gio.AppInfo.launch_default_for_uri();
everything else must be rejected without calling it at all.
"""
import unittest
from unittest import mock

from core import uri_launcher as ul


class IsSafeHttpsUrlTests(unittest.TestCase):
    def test_accepts_plain_https_url(self):
        self.assertTrue(ul.is_safe_https_url("https://www.manganogregorio.it/contatti"))

    def test_rejects_http(self):
        self.assertFalse(ul.is_safe_https_url("http://www.manganogregorio.it"))

    def test_rejects_javascript_scheme(self):
        self.assertFalse(ul.is_safe_https_url("javascript:alert(1)"))

    def test_rejects_file_scheme(self):
        self.assertFalse(ul.is_safe_https_url("file:///etc/passwd"))

    def test_rejects_mailto(self):
        self.assertFalse(ul.is_safe_https_url("mailto:test@example.com"))

    def test_rejects_scheme_without_netloc(self):
        self.assertFalse(ul.is_safe_https_url("https:example.com"))

    def test_rejects_non_string(self):
        self.assertFalse(ul.is_safe_https_url(None))
        self.assertFalse(ul.is_safe_https_url(1234))

    def test_rejects_empty_string(self):
        self.assertFalse(ul.is_safe_https_url(""))

    def test_rejects_malformed_url_without_raising(self):
        self.assertFalse(ul.is_safe_https_url("https://[::1"))


class OpenExternalUrlTests(unittest.TestCase):
    def test_launches_safe_https_url(self):
        with mock.patch.object(ul.Gio.AppInfo, "launch_default_for_uri", return_value=True) as mock_launch:
            result = ul.open_external_url("https://www.manganogregorio.it/m-g-linux-toolbox/")
        self.assertTrue(result)
        mock_launch.assert_called_once_with("https://www.manganogregorio.it/m-g-linux-toolbox/", None)

    def test_refuses_http_without_ever_calling_launcher(self):
        with mock.patch.object(ul.Gio.AppInfo, "launch_default_for_uri") as mock_launch:
            result = ul.open_external_url("http://www.manganogregorio.it")
        self.assertFalse(result)
        mock_launch.assert_not_called()

    def test_refuses_unsafe_scheme_without_ever_calling_launcher(self):
        with mock.patch.object(ul.Gio.AppInfo, "launch_default_for_uri") as mock_launch:
            result = ul.open_external_url("javascript:alert(1)")
        self.assertFalse(result)
        mock_launch.assert_not_called()

    def test_launcher_exception_is_reported_as_failure_not_raised(self):
        with mock.patch.object(ul.Gio.AppInfo, "launch_default_for_uri", side_effect=RuntimeError("no browser")):
            result = ul.open_external_url("https://www.manganogregorio.it")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
