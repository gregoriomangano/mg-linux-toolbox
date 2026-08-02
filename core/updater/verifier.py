"""
Checksum verification — the file is never chmod'd executable or run
until this passes. Signature support is left as a clear extension point
for later (no invented keys).
"""
import hashlib
import os


def sha256_of(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_checksum_file(content: str) -> str:
    """Standard `sha256sum` format: "<hex>  <filename>" (or bare hex)."""
    first_line = content.strip().splitlines()[0] if content.strip() else ""
    return first_line.split()[0].lower() if first_line else ""


def verify_file(path: str, expected_sha256: str) -> bool:
    if not expected_sha256:
        return False
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False
    return sha256_of(path).lower() == expected_sha256.strip().lower()


# Extension point for a future signature check (e.g. minisign/signify) —
# deliberately unimplemented rather than inventing a key/scheme now.
def verify_signature(path: str, signature_path: str, public_key: str = "") -> bool:
    raise NotImplementedError("Signature verification is not implemented; checksum verification is required.")
