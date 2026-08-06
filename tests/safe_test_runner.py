"""Compact unittest runner with per-test tracking on top of the package guard."""
import argparse
import json
import os
import sys
import time
import unittest

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.privilege_guard import set_current_test


class TrackingTextTestResult(unittest.TextTestResult):
    def startTest(self, test):
        set_current_test(test.id())
        super().startTest(test)

    def stopTest(self, test):
        try:
            super().stopTest(test)
        finally:
            set_current_test("<between tests>")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)

    loader = unittest.defaultTestLoader
    if args.discover:
        suite = loader.discover("tests", top_level_dir=".")
    else:
        if not args.names:
            parser.error("provide test module names or --discover")
        suite = loader.loadTestsFromNames(args.names)

    discovered = suite.countTestCases()
    started = time.monotonic()
    runner = unittest.TextTestRunner(
        verbosity=1, resultclass=TrackingTextTestResult)
    result = runner.run(suite)
    duration = time.monotonic() - started

    failures = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped)
    expected_failures = len(result.expectedFailures)
    unexpected_successes = len(result.unexpectedSuccesses)
    passed = (result.testsRun - failures - errors - skipped
              - expected_failures - unexpected_successes)
    metrics = {
        "discovered": discovered,
        "executed": result.testsRun,
        "passed": passed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "expected_failures": expected_failures,
        "unexpected_successes": unexpected_successes,
        "duration_seconds": round(duration, 3),
        "exit_code": 0 if result.wasSuccessful() else 1,
        "failure_tests": [test.id() for test, _ in result.failures],
        "error_tests": [test.id() for test, _ in result.errors],
    }
    print("MG_TEST_METRICS=" + json.dumps(metrics, sort_keys=True))
    return metrics["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
