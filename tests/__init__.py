"""Test package bootstrap.

Installs the suite-wide privilege guard as soon as the ``tests``
package is imported, so a plain ``python3 -m unittest discover -s
tests`` is protected too, not only ``tests.safe_test_runner``.
"""
import atexit
import unittest

from tests import privilege_guard as _privilege_guard

_ACTIVE_GUARD = _privilege_guard.installed_privilege_guard()
_ACTIVE_GUARD.__enter__()
atexit.register(_ACTIVE_GUARD.__exit__, None, None, None)

_REAL_START_TEST = unittest.result.TestResult.startTest
_REAL_STOP_TEST = unittest.result.TestResult.stopTest


def _guarded_start_test(self, test):
    _privilege_guard.set_current_test(test.id())
    return _REAL_START_TEST(self, test)


def _guarded_stop_test(self, test):
    try:
        return _REAL_STOP_TEST(self, test)
    finally:
        _privilege_guard.set_current_test("<between tests>")


unittest.result.TestResult.startTest = _guarded_start_test
unittest.result.TestResult.stopTest = _guarded_stop_test
