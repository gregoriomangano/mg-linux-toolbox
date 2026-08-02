"""
Tests for core.executor — the process-execution layer behind every
install/toggle in the app. These exercise the actual timeout/kill/cancel
behaviour with real (harmless) subprocesses, not just checked in review:
the whole point of the Fase A fix was that "looked correct on paper" was
not enough.
"""
import time
import unittest

from core.executor import run_command, run_command_full, Job


class TimeoutBoundingTests(unittest.TestCase):
    def test_normal_command_completes_and_reports_ok(self):
        r = run_command_full(["echo", "hello"], timeout=5)
        self.assertTrue(r.ok)
        self.assertEqual(r.stdout.strip(), "hello")
        self.assertFalse(r.timed_out)

    def test_failing_command_reports_ok_false_without_raising(self):
        r = run_command_full(["false"], timeout=5)
        self.assertFalse(r.ok)
        self.assertFalse(r.timed_out)

    def test_missing_command_reports_error_without_raising(self):
        r = run_command_full(["this-command-does-not-exist-xyz"], timeout=5)
        self.assertFalse(r.ok)
        self.assertTrue(r.error)

    def test_slow_command_is_bounded_by_timeout(self):
        start = time.monotonic()
        r = run_command_full(["sleep", "5"], timeout=1)
        elapsed = time.monotonic() - start
        self.assertTrue(r.timed_out)
        self.assertLess(elapsed, 3, "a 1s timeout must not let the caller "
                                    "wait anywhere near the full 5s sleep")

    def test_orphaned_background_child_does_not_defeat_the_timeout(self):
        """
        Reproduces the exact failure shape behind the "Installazione…"
        hang: a process that spawns a background child inheriting its
        stdout/stderr and then exits, leaving the child to keep the pipes
        open. Without process-group isolation, draining those pipes after
        a timeout can block far longer than the timeout itself.
        """
        start = time.monotonic()
        r = run_command_full(["bash", "-c", "(sleep 10 &) ; exit 0"], timeout=2)
        elapsed = time.monotonic() - start
        self.assertTrue(r.timed_out)
        self.assertLess(elapsed, 4, "an orphaned grandchild holding the "
                                    "output pipe open must not make the "
                                    "call block past the timeout")

    def test_backward_compatible_tuple_unpacking(self):
        ok, out, err = run_command(["echo", "hi"])
        self.assertTrue(ok)
        self.assertEqual(out, "hi")
        self.assertEqual(err, "")


class CancellationTests(unittest.TestCase):
    def test_cancel_before_start_prevents_the_command_from_running(self):
        job = Job()
        job.cancel()
        start = time.monotonic()
        r = run_command_full(["sleep", "5"], timeout=10, job=job)
        elapsed = time.monotonic() - start
        self.assertTrue(r.cancelled)
        self.assertLess(elapsed, 3)

    def test_cancel_while_running_stops_it_quickly(self):
        import threading
        job = Job()
        result_holder = {}

        def run():
            result_holder["r"] = run_command_full(["sleep", "30"], timeout=60, job=job)

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.3)  # let the process actually start
        start_cancel = time.monotonic()
        job.cancel()
        t.join(timeout=5)
        elapsed = time.monotonic() - start_cancel
        self.assertFalse(t.is_alive(), "cancel() must unblock the worker thread quickly")
        self.assertLess(elapsed, 4)
        self.assertTrue(result_holder["r"].cancelled)


if __name__ == "__main__":
    unittest.main()
