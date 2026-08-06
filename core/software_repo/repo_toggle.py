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


def remove_zypper_repo(repo_file: str, section_id: str, job=None) -> ToggleResult:
    """Remove a Zypper repository: either delete the entire .repo file
    (if it contains only one section) or surgically remove just the
    section block (if multiple sections exist). Backup before removal,
    verify after, restore on failure."""
    original = _read_text(repo_file)
    if original is None:
        return ToggleResult(False, tx.UNDETERMINED, friendly_message="repo_toggle_file_unreadable")

    pattern = re.compile(_SECTION_RE_TEMPLATE.format(section=re.escape(section_id)))
    match = pattern.search(original)
    if not match:
        return ToggleResult(False, tx.UNSUPPORTED, friendly_message="repo_toggle_section_not_found")

    other_sections = len(re.findall(r"^\[.*\]$", original, re.MULTILINE)) > 1
    if other_sections:
        new_text = original[:match.start()] + original[match.end():]
        tmp_path = _write_temp(new_text)
        def apply_fn():
            result = run_pkexec_full(["cp", tmp_path, repo_file], timeout=INSTALL_TIMEOUT, job=job)
            return result.ok
        def verify_fn():
            current = _read_text(repo_file)
            if current is None:
                return False
            return not pattern.search(current)
    else:
        tmp_path = None
        def apply_fn():
            result = run_pkexec_full(["rm", repo_file], timeout=INSTALL_TIMEOUT, job=job)
            return result.ok
        def verify_fn():
            import os
            return not os.path.exists(repo_file)

    try:
        result = tx.run_transaction([repo_file], apply_fn, verify_fn, job=job)
    finally:
        import os
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return ToggleResult(
        result.ok, result.state,
        friendly_message="repo_remove_success" if result.ok else (result.friendly_message or "repo_remove_failed"),
        technical_detail=result.technical_detail,
    )


def remove_flatpak_remote(remote_name: str, scope: str, job=None) -> ToggleResult:
    """Delete a Flatpak remote. No file backup — Flatpak remotes are
    stored in Flatpak's own configuration, not as .repo files.
    Verification re-reads the remote list to confirm deletion."""
    from core.software_repo.flatpak_manager import list_remotes, SCOPE_SYSTEM

    cmd = ["flatpak", "remote-delete", f"--{scope}", remote_name]
    runner = run_pkexec_full if scope == SCOPE_SYSTEM else run_command_full
    result = runner(cmd, timeout=INSTALL_TIMEOUT, job=job)
    if not result.ok:
        return ToggleResult(False, tx.VERIFICATION_FAILED, friendly_message="repo_remove_failed",
                             technical_detail=result.technical_detail())

    remotes = list_remotes(scope)
    match = next((r for r in remotes if r.name == remote_name), None)
    verified = match is None
    return ToggleResult(verified, tx.VERIFIED if verified else tx.VERIFICATION_FAILED,
                         friendly_message="repo_remove_success" if verified else "repo_remove_verification_failed")


def add_zypper_repo(alias: str, url: str, job=None) -> ToggleResult:
    """Add a new Zypper repository via `zypper addrepo` — used only for
    a fixed, verified, version-matched URL built from the real detected
    distro (e.g. Packman for the exact openSUSE Tumbleweed release),
    never a free-text URL from the UI. No package is installed and no
    vendor is switched — this only registers metadata. On verification
    failure the just-added repo is removed again (rollback), so a
    failed activation never leaves a half-registered repository
    behind."""
    cmd = ["zypper", "--non-interactive", "addrepo", "--refresh", "--check", url, alias]
    result = run_pkexec_full(cmd, timeout=INSTALL_TIMEOUT, job=job)
    if not result.ok:
        return ToggleResult(False, tx.VERIFICATION_FAILED, friendly_message="repo_add_failed",
                             technical_detail=result.technical_detail())

    run_pkexec_full(["zypper", "--non-interactive", "refresh", alias], timeout=INSTALL_TIMEOUT, job=job)

    from core.software_repo.repo_scanner import scan_zypper
    entries = scan_zypper()
    match = next((e for e in entries if e.alias == alias), None)
    if match is None:
        run_pkexec_full(["zypper", "--non-interactive", "removerepo", alias], timeout=INSTALL_TIMEOUT, job=job)
        return ToggleResult(False, tx.VERIFICATION_FAILED, friendly_message="repo_add_verification_failed")

    return ToggleResult(True, tx.VERIFIED, friendly_message="repo_add_success")
