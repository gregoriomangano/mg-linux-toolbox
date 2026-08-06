"""Startup acknowledgement used by the managed update helper."""
import json
import os
import tempfile
import time


def write_update_confirmation(version: str) -> bool:
    path = os.environ.get("MG_TOOLBOX_UPDATE_CONFIRMATION", "")
    token = os.environ.get("MG_TOOLBOX_UPDATE_CONFIRMATION_TOKEN", "")
    expected = os.environ.get("MG_TOOLBOX_UPDATE_EXPECTED_VERSION", "")
    if not path or not token or not expected or version != expected:
        return False
    temporary = ""
    try:
        fd, temporary = tempfile.mkstemp(prefix="confirmation-", dir=os.path.dirname(path))
        with os.fdopen(fd, "w") as stream:
            json.dump({"token": token, "version": version, "pid": os.getpid(),
                       "started_at": time.time()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return True
    except OSError:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                return False
        return False
