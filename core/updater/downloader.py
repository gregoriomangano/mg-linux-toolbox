"""
Downloads a release asset to a temporary file. HTTPS only, streamed (so
large AppImages don't sit fully in memory), with progress callback and
cooperative cancellation — never executes or trusts the file before
verifier.py has checked it.
"""
import os
import urllib.error
import urllib.request

from core.updater.models import DownloadResult

CHUNK_SIZE = 256 * 1024
DEFAULT_TIMEOUT = 30


class CancelToken:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def download_asset(url: str, dest_path: str, expected_size: int = 0,
                    on_progress=None, cancel_token: "CancelToken | None" = None,
                    timeout: int = DEFAULT_TIMEOUT) -> DownloadResult:
    """
    on_progress(bytes_downloaded, total_bytes_or_0) is called periodically
    (not on every chunk necessarily, but at least once per chunk here —
    callers wanting UI-safe throttling should debounce on their side).
    """
    if not url.startswith("https://"):
        return DownloadResult(False, friendly_message="updater_insecure_url",
                               technical_detail=f"refused non-HTTPS url: {url}")

    tmp_path = f"{dest_path}.part"
    req = urllib.request.Request(url, headers={"User-Agent": "mg-linux-toolbox-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = expected_size or int(resp.headers.get("Content-Length", 0) or 0)
            downloaded = 0
            with open(tmp_path, "wb") as f:
                while True:
                    if cancel_token is not None and cancel_token.cancelled:
                        f.close()
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
                        return DownloadResult(False, friendly_message="updater_cancelled")
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress is not None:
                        on_progress(downloaded, total)
                f.flush()
                os.fsync(f.fileno())
    except urllib.error.URLError as e:
        _cleanup(tmp_path)
        return DownloadResult(False, friendly_message="updater_no_network", technical_detail=str(e.reason))
    except TimeoutError:
        _cleanup(tmp_path)
        return DownloadResult(False, friendly_message="updater_timeout")
    except OSError as e:
        _cleanup(tmp_path)
        return DownloadResult(False, friendly_message="updater_disk_error", technical_detail=str(e))

    if downloaded == 0:
        _cleanup(tmp_path)
        return DownloadResult(False, friendly_message="updater_empty_download")

    os.replace(tmp_path, dest_path)
    return DownloadResult(True, path=dest_path, size=downloaded)


def _cleanup(path: str):
    try:
        os.remove(path)
    except OSError:
        pass
