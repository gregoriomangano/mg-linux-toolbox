"""
Enable/Disable/Re-enable for an existing repository — DNF (.repo INI
sections), Zypper (.repo INI sections) and Flatpak remotes. Built on
repo_transaction's generic backup/apply/verify/rollback engine, so a
toggle that doesn't actually take effect always restores the original
file automatically instead of leaving a half-applied edit.

Text edits are surgical (regex on the exact `[section]` block only) —
never a full configparser rewrite, which would silently drop comments
and reformat every other section in the file.
"""
import re
from dataclasses import dataclass

from core.executor import run_command_full, run_pkexec_full, INSTALL_TIMEOUT
from core.software_repo import repo_transaction as tx

_SECTION_RE_TEMPLATE = r"(\[{section}\]\n)((?:(?!\[).*\n?)*)"


def _read_text(path: str) -> "str | None":
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _set_enabled_in_section(text: str, section_id: str, enabled: bool) -> "str | None":
    """Replaces (or inserts) the `enabled=` line inside exactly one
    `[section_id]` block, leaving every other byte of the file
    untouched. Returns None if the section isn't found."""
    pattern = re.compile(_SECTION_RE_TEMPLATE.format(section=re.escape(section_id)))
    match = pattern.search(text)
    if not match:
        return None
    header, body = match.group(1), match.group(2)
    value = "1" if enabled else "0"
    if re.search(r"^\s*enabled\s*=", body, re.MULTILINE):
        new_body = re.sub(r"^\s*enabled\s*=.*$", f"enabled={value}", body, count=1, flags=re.MULTILINE)
    else:
        new_body = body + f"enabled={value}\n"
    return text[: match.start()] + header + new_body + text[match.end():]


def _write_temp(content: str) -> str:
    import tempfile
    fd, path = tempfile.mkstemp(prefix="mg-repo-toggle-", suffix=".tmp")
    with __import__("os").fdopen(fd, "w") as f:
        f.write(content)
    return path


@dataclass
class ToggleResult:
    ok: bool
    state: str
    friendly_message: str = ""
    technical_detail: str = ""


def _toggle_ini_repo(repo_file: str, section_id: str, enabled: bool,
                       verify_cmd: "list | None", job=None) -> ToggleResult:
    original = _read_text(repo_file)
    if original is None:
        return ToggleResult(False, tx.UNDETERMINED, friendly_message="repo_toggle_file_unreadable")
    new_text = _set_enabled_in_section(original, section_id, enabled)
    if new_text is None:
        return ToggleResult(False, tx.UNSUPPORTED, friendly_message="repo_toggle_section_not_found")

    tmp_path = _write_temp(new_text)

    def apply_fn():
        result = run_pkexec_full(["cp", tmp_path, repo_file], timeout=INSTALL_TIMEOUT, job=job)
        return result.ok

    def verify_fn():
        current = _read_text(repo_file)
        if current is None:
            return False
        match = re.search(_SECTION_RE_TEMPLATE.format(section=re.escape(section_id)), current)
        if not match:
            return False
        expected = "1" if enabled else "0"
        found = re.search(r"^\s*enabled\s*=\s*(\S+)", match.group(2), re.MULTILINE)
        if not found or found.group(1) != expected:
            return False
        if verify_cmd:
            check = run_command_full(verify_cmd, timeout=30, job=job)
            if not check.ok:
                return False
        return True

    try:
        result = tx.run_transaction([repo_file], apply_fn, verify_fn, job=job)
    finally:
        import os
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    return ToggleResult(
        result.ok, result.state,
        friendly_message="repo_toggle_success" if result.ok else (result.friendly_message or "repo_toggle_failed"),
        technical_detail=result.technical_detail,
    )


def set_dnf_repo_enabled(repo_file: str, section_id: str, enabled: bool, job=None) -> ToggleResult:
    return _toggle_ini_repo(repo_file, section_id, enabled,
                             verify_cmd=["dnf", "-C", "repolist", "--all"], job=job)


def set_zypper_repo_enabled(repo_file: str, section_id: str, enabled: bool, job=None) -> ToggleResult:
    return _toggle_ini_repo(repo_file, section_id, enabled,
                             verify_cmd=["zypper", "--non-interactive", "lr"], job=job)


def set_flatpak_remote_enabled(remote_name: str, scope: str, enabled: bool, job=None) -> ToggleResult:
    """Flatpak has a native enable/disable primitive — no file backup
    needed, `flatpak remote-modify` is itself the safe, atomic
    operation; verification re-reads the real remote list."""
    from core.software_repo.flatpak_manager import list_remotes, SCOPE_SYSTEM

    flag = "--enable" if enabled else "--disable"
    cmd = ["flatpak", "remote-modify", f"--{scope}", flag, remote_name]
    runner = run_pkexec_full if scope == SCOPE_SYSTEM else run_command_full
    result = runner(cmd, timeout=INSTALL_TIMEOUT, job=job)
    if not result.ok:
        return ToggleResult(False, tx.VERIFICATION_FAILED, friendly_message="repo_toggle_failed",
                             technical_detail=result.technical_detail())

    remotes = list_remotes(scope)
    match = next((r for r in remotes if r.name == remote_name), None)
    verified = match is not None and match.enabled == enabled
    return ToggleResult(verified, tx.VERIFIED if verified else tx.VERIFICATION_FAILED,
                         friendly_message="repo_toggle_success" if verified else "repo_toggle_verification_failed")
