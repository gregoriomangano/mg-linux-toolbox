#!/usr/bin/env python3
"""External supervisor for one managed AppImage update."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

CONFIRM_TIMEOUT = 20.0
STABILIZE_SECONDS = 2.0
POLL_SECONDS = 0.1


def _log(path, message):
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")


def _regular_executable(path):
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0 and os.access(path, os.X_OK)
    except OSError:
        return False


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _terminate(process, log_path):
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(log_path, f"new process termination required force: {exc}")
        try:
            process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as kill_exc:
            _log(log_path, f"new process could not be stopped: {kill_exc}")


def _read_confirmation(path, token, version):
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        return data.get("token") == token and data.get("version") == version
    except (OSError, ValueError, TypeError):
        return False


def _rollback(pending, target, log_path):
    if not _regular_executable(pending):
        _log(log_path, "rollback refused: pending backup is missing or invalid")
        return False
    expected = _sha256(pending)
    temporary = f"{target}.rollback-new"
    try:
        shutil.copy2(pending, temporary)
        os.chmod(temporary, 0o755)
        if not _regular_executable(temporary) or _sha256(temporary) != expected:
            raise OSError("rollback candidate verification failed")
        os.replace(temporary, target)
        if not _regular_executable(target) or _sha256(target) != expected:
            _log(log_path, "rollback verification failed")
            return False
        _log(log_path, "rollback completed and verified; pending backup retained")
        return True
    except OSError as exc:
        _log(log_path, f"rollback failed; pending backup retained: {exc}")
        return False
    finally:
        try:
            os.unlink(temporary)
        except OSError as exc:
            _log(log_path, f"rollback temporary cleanup failed: {exc}")


def _finalize_backup(pending, backup_dir, version, log_path):
    final = os.path.join(backup_dir, f"previous-{version}.AppImage")
    temporary = f"{final}.new"
    shutil.copy2(pending, temporary)
    os.chmod(temporary, 0o755)
    if not _regular_executable(temporary) or _sha256(temporary) != _sha256(pending):
        raise OSError("final backup verification failed")
    os.replace(temporary, final)
    os.unlink(pending)
    for name in os.listdir(backup_dir):
        if name.startswith("previous-") and name.endswith(".AppImage") and name != os.path.basename(final):
            try:
                os.unlink(os.path.join(backup_dir, name))
            except OSError as exc:
                _log(log_path, f"old backup cleanup deferred: {exc}")


def _notify(log_path):
    message = "L'aggiornamento non è stato completato. È stata ripristinata la versione precedente."
    try:
        result = subprocess.run(["notify-send", "M.G Linux Toolbox", message],
                                capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            _log(log_path, f"desktop notification failed: {result.stderr.strip()}")
    except (OSError, subprocess.SubprocessError) as exc:
        _log(log_path, f"desktop notification unavailable: {exc}")


def _launch_previous(target, log_path):
    environment = dict(os.environ)
    environment["MG_TOOLBOX_UPDATE_ROLLBACK"] = "1"
    environment["MG_TOOLBOX_UPDATE_ROLLBACK_LOG"] = log_path
    try:
        subprocess.Popen([target], env=environment, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _log(log_path, "previous version launch requested")
        return True
    except OSError as exc:
        _log(log_path, f"previous version launch failed: {exc}")
        return False


def supervise(args):
    os.makedirs(os.path.dirname(args.log), exist_ok=True)
    _log(args.log, f"supervision started for {args.version}")
    environment = dict(os.environ)
    environment["MG_TOOLBOX_UPDATE_CONFIRMATION"] = args.confirmation
    environment["MG_TOOLBOX_UPDATE_CONFIRMATION_TOKEN"] = args.token
    environment["MG_TOOLBOX_UPDATE_EXPECTED_VERSION"] = args.version
    environment.pop("MG_TOOLBOX_UPDATE_ROLLBACK", None)
    environment.pop("MG_TOOLBOX_UPDATE_ROLLBACK_LOG", None)
    try:
        process = subprocess.Popen([args.target], env=environment, start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        _log(args.log, f"new version could not be started: {exc}")
        if _rollback(args.pending, args.target, args.log):
            _launch_previous(args.target, args.log)
            _notify(args.log)
        return 1

    deadline = time.monotonic() + args.timeout
    confirmed = False
    while time.monotonic() < deadline:
        if _read_confirmation(args.confirmation, args.token, args.version):
            confirmed = True
            break
        if process.poll() is not None:
            _log(args.log, f"new version exited with code {process.returncode} before confirmation")
            break
        time.sleep(POLL_SECONDS)

    if not confirmed:
        _log(args.log, "new version did not confirm a valid startup before timeout")
        _terminate(process, args.log)
        if _rollback(args.pending, args.target, args.log):
            _launch_previous(args.target, args.log)
            _notify(args.log)
        return 1

    deadline = time.monotonic() + args.stabilize
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _log(args.log, f"new version exited with code {process.returncode} after confirmation")
            if _rollback(args.pending, args.target, args.log):
                _launch_previous(args.target, args.log)
                _notify(args.log)
            return 1
        time.sleep(POLL_SECONDS)

    try:
        _finalize_backup(args.pending, args.backup_dir, args.previous_version, args.log)
        _log(args.log, "startup confirmed; pending backup finalized")
        return 0
    except (OSError, ValueError) as exc:
        _log(args.log, f"backup finalization failed after startup confirmation: {exc}")
        return 1
    finally:
        try:
            os.unlink(args.confirmation)
        except OSError as exc:
            _log(args.log, f"confirmation cleanup failed: {exc}")


def main(argv=None):
    parser = argparse.ArgumentParser()
    for name in ("target", "pending", "backup_dir", "version", "previous_version", "confirmation", "token", "log"):
        parser.add_argument(f"--{name.replace('_', '-')}", required=True)
    parser.add_argument("--timeout", type=float, default=CONFIRM_TIMEOUT)
    parser.add_argument("--stabilize", type=float, default=STABILIZE_SECONDS)
    args = parser.parse_args(argv)
    try:
        return supervise(args)
    finally:
        try:
            shutil.rmtree(os.path.dirname(__file__), ignore_errors=True)
        except OSError:
            return 1


if __name__ == "__main__":
    sys.exit(main())
