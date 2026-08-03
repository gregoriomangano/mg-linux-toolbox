"""
Tests for core.version.check_runtime_requirements() — the real (not
assumed) minimum-version gate added in this Beta 4 pass after finding
that Debian 12's Libadwaita 1.2.2 lacks Adw.ExpanderRow.add_suffix(),
Adw.ToolbarView and Adw.Breakpoint/BreakpointCondition (all introduced
in Libadwaita 1.4), verified empirically in a real Debian 12 container.
GTK4 and PyGObject were proven NOT to be the limiting factor there (both
constructed every widget fine once Libadwaita was new enough) — this is
locked in here so a future change can't silently reintroduce an
unverified GTK4/PyGObject-only floor.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import version as v


class MinimumVersionConstantsTests(unittest.TestCase):
    """The actually-enforced floor must match what was empirically
    determined — Libadwaita 1.4, nothing invented like 1.5."""

    def test_libadwaita_minimum_is_1_4_not_a_guessed_1_5(self):
        self.assertEqual(v.MIN_LIBADWAITA_VERSION, (1, 4, 0))

    def test_gtk_minimum_reflects_the_proven_non_limiting_floor(self):
        # 4.8 is the oldest GTK4 this was actually run against
        # (Debian 12) and it worked fine — never silently raised.
        self.assertEqual(v.MIN_GTK_VERSION, (4, 8, 0))

    def test_python_minimum_is_the_oldest_interpreter_actually_tested(self):
        self.assertEqual(v.MIN_PYTHON_VERSION, (3, 11))


class CheckRuntimeRequirementsTests(unittest.TestCase):
    def test_real_host_environment_reports_ok(self):
        """Exercises the real function against whatever GTK4/Libadwaita
        this development machine actually has installed — no mocks."""
        result = v.check_runtime_requirements()
        self.assertTrue(result["ok"], result)
        self.assertIn("libadwaita", result["found"])

    def test_python_too_old_is_reported_before_any_gi_import(self):
        original = v.MIN_PYTHON_VERSION
        try:
            v.MIN_PYTHON_VERSION = (99, 0)
            result = v.check_runtime_requirements()
        finally:
            v.MIN_PYTHON_VERSION = original
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "python_too_old")

    def test_missing_gi_reported_as_import_failed_never_a_traceback(self):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "gi":
                raise ImportError("No module named 'gi'")
            return real_import(name, *args, **kwargs)

        original_import = builtins.__import__
        builtins.__import__ = fake_import
        try:
            result = v.check_runtime_requirements()
        finally:
            builtins.__import__ = original_import
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "import_failed")

    def test_old_libadwaita_correctly_flagged_even_with_new_gtk(self):
        """Simulates exactly the real Debian 12 finding: GTK4 and
        PyGObject fine, only Libadwaita below the floor."""
        original = v.MIN_LIBADWAITA_VERSION
        try:
            v.MIN_LIBADWAITA_VERSION = (99, 0, 0)
            result = v.check_runtime_requirements()
        finally:
            v.MIN_LIBADWAITA_VERSION = original
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "version_too_old")
        self.assertIn("libadwaita", result["found"])

    def test_result_never_includes_a_raw_exception_object(self):
        result = v.check_runtime_requirements()
        for value in result.values():
            self.assertNotIsInstance(value, Exception)


if __name__ == "__main__":
    unittest.main()
