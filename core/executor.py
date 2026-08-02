"""
Shared process-execution helpers.

Every command that can install/change something on the system goes
through here, so the process-group and timeout handling only needs to be
correct in one place.

Why start_new_session=True matters: a command like "apt-get install
power-profiles-daemon" isn't a single process — apt-get forks dpkg, dpkg
forks the package's postinst maintainer script, which forks systemctl,
which may block waiting for a conflicting systemd unit to stop (this is
exactly what happened with power-profiles-daemon vs system76-power: the
service's own unit declares Conflicts=system76-power.service, so
starting one has to stop the other first). If the *direct* child
(pkexec) is killed on timeout but its descendants are left running, they
keep the captured stdout/stderr pipes open, and the follow-up
communicate() call used to drain them blocks until those orphans exit on
their own — which can take far longer than the intended timeout, and
looks to the user like the app is hung forever. Spawning in a fresh
session/process group lets us kill the *entire* tree at once via
killpg(), so a timeout always actually terminates the operation.
"""
import os
import signal
import subprocess
import threading
import time
import logging
from dataclasses import dataclass, field

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
# Package installs legitimately can take longer than a quick sysctl/systemctl
# toggle — dependency resolution, download, a conflicting-service handoff
# (see module docstring). Still bounded, so a genuine hang always surfaces.
INSTALL_TIMEOUT = 180


@dataclass
class CommandResult:
    cmd: list
    ok: bool
    returncode: "int | None"
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    cancelled: bool = False
    error: str = ""

    def __iter__(self):
        # Back-compat with the `ok, out, err = run_command(...)` pattern
        # used across the whole codebase.
        return iter((self.ok, self.stdout, self.stderr))

    def __bool__(self):
        # So `if _install_pkg(...):` and DepBanner's `bool(result)` reflect
        # actual success instead of "any object is truthy by default".
        return self.ok

    def technical_detail(self) -> str:
        """Everything needed for a "Mostra dettagli" disclosure — never
        shown to the user unless they explicitly ask for it."""
        lines = [
            f"$ {' '.join(self.cmd)}",
            f"exit code: {self.returncode}",
            f"duration: {self.duration:.1f}s",
        ]
        if self.timed_out:
            lines.append("timed out and was terminated")
        if self.cancelled:
            lines.append("cancelled by user")
        if self.error:
            lines.append(f"error: {self.error}")
        if self.stdout:
            lines.append(f"stdout:\n{self.stdout}")
        if self.stderr:
            lines.append(f"stderr:\n{self.stderr}")
        return "\n".join(lines)


class Job:
    """
    Handle for a possibly-long-running command, so the GTK main thread can
    cancel it while a worker thread is blocked inside run_command_full().
    Cancelling kills the whole process group (see module docstring for
    why that's necessary, not just the immediate process).
    """
    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self.cancelled = False

    def _attach(self, proc):
        with self._lock:
            self._proc = proc
            if self.cancelled:
                _kill_process_group(proc)

    def cancel(self):
        with self._lock:
            self.cancelled = True
            if self._proc is not None:
                _kill_process_group(self._proc)


def _kill_process_group(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def run_command_full(cmd_list, timeout=DEFAULT_TIMEOUT, job: "Job | None" = None) -> CommandResult:
    """Runs cmd_list and always returns within `timeout` (+ a small grace
    period to drain output after a forced kill), with full diagnostics."""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        return CommandResult(cmd_list, False, None, "", "", time.monotonic() - start, error=str(e))

    if job is not None:
        job._attach(proc)

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        duration = time.monotonic() - start
        if job is not None and job.cancelled:
            return CommandResult(cmd_list, False, proc.returncode, stdout, stderr, duration, cancelled=True)
        return CommandResult(cmd_list, proc.returncode == 0, proc.returncode, stdout, stderr, duration)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            stdout, stderr = "", ""
        duration = time.monotonic() - start
        cancelled = job is not None and job.cancelled
        return CommandResult(cmd_list, False, proc.returncode, stdout, stderr, duration,
                              timed_out=not cancelled, cancelled=cancelled)


def run_command(cmd_list, timeout=DEFAULT_TIMEOUT, job: "Job | None" = None):
    """Run a command, returns (success, stdout, stderr) — back-compat shape
    used everywhere else in this codebase. Accepts an optional Job so
    callers that want a working "Annulla" button can opt in without any
    other caller needing to change."""
    r = run_command_full(cmd_list, timeout=timeout, job=job)
    if r.error and not r.stdout and not r.stderr:
        logger.warning("run_command failed: %s (%s)", cmd_list, r.error)
    return r.ok, r.stdout.strip(), r.stderr.strip()


def run_pkexec(cmd_list, timeout=DEFAULT_TIMEOUT, job: "Job | None" = None):
    """Run a command with pkexec for privilege escalation."""
    return run_command(["pkexec"] + cmd_list, timeout=timeout, job=job)


def run_pkexec_full(cmd_list, timeout=INSTALL_TIMEOUT, job: "Job | None" = None) -> CommandResult:
    """Same as run_pkexec but returns full diagnostics (exit code, stdout,
    stderr, duration, timed_out/cancelled flags) and accepts a Job for
    cancellation — used by the installer flow, which needs to show real
    error detail and a working "Annulla" button instead of hanging."""
    return run_command_full(["pkexec"] + cmd_list, timeout=timeout, job=job)
