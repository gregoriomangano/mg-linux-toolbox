"""Suite-wide guard against real privilege-escalation processes."""
import os
import shlex
import subprocess
from contextlib import contextmanager
from unittest import mock

FORBIDDEN_PROGRAMS = frozenset({"sudo", "pkexec", "su", "doas"})

_current_test = "<test discovery>"
_REAL_POPEN = subprocess.Popen
_REAL_RUN = subprocess.run
_REAL_CALL = subprocess.call
_REAL_CHECK_CALL = subprocess.check_call


class PrivilegeCommandBlocked(AssertionError):
    pass


def set_current_test(test_id: str) -> None:
    global _current_test
    _current_test = test_id


def _program_from_command(command) -> str:
    if isinstance(command, (list, tuple)):
        value = command[0] if command else ""
    else:
        try:
            parts = shlex.split(str(command))
        except ValueError:
            parts = []
        value = parts[0] if parts else ""
    return os.path.basename(os.fspath(value)) if value else ""


def _check_command(command) -> None:
    program = _program_from_command(command)
    if program in FORBIDDEN_PROGRAMS:
        raise PrivilegeCommandBlocked(
            f"test {_current_test} attempted real privileged command: {command!r}")


def _command_arg(args, kwargs):
    return args[0] if args else kwargs.get("args", "")


def _guarded_popen(*args, **kwargs):
    _check_command(_command_arg(args, kwargs))
    return _REAL_POPEN(*args, **kwargs)


def _guarded_run(*args, **kwargs):
    _check_command(_command_arg(args, kwargs))
    return _REAL_RUN(*args, **kwargs)


def _guarded_call(*args, **kwargs):
    _check_command(_command_arg(args, kwargs))
    return _REAL_CALL(*args, **kwargs)


def _guarded_check_call(*args, **kwargs):
    _check_command(_command_arg(args, kwargs))
    return _REAL_CHECK_CALL(*args, **kwargs)


@contextmanager
def installed_privilege_guard():
    """Install all guards and restore the exact prior callables."""
    patchers = [
        mock.patch.object(subprocess, "Popen", side_effect=_guarded_popen),
        mock.patch.object(subprocess, "run", side_effect=_guarded_run),
        mock.patch.object(subprocess, "call", side_effect=_guarded_call),
        mock.patch.object(subprocess, "check_call", side_effect=_guarded_check_call),
    ]
    try:
        for patcher in patchers:
            patcher.start()
        yield
    finally:
        for patcher in reversed(patchers):
            patcher.stop()
        set_current_test("<test discovery>")
