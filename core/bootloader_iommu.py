"""
Real IOMMU configuration — "Configura IOMMU", "Disattiva", "Ripristina".

This is the highest-risk feature in M.G Linux Toolbox: it edits a
bootloader's kernel command line and regenerates its config, and the
change can only be verified after a reboot. Every write here:
  1. backs up the real file it's about to touch (atomic_io.backup_file)
     before changing anything;
  2. only ever adds/removes the exact "<vendor>_iommu=on iommu=pt"
     tokens it itself manages — never touches any other kernel
     parameter already on that line;
  3. regenerates the bootloader config through the distro's own real
     tool (update-grub / grub-mkconfig / grub2-mkconfig / kernelstub),
     never hand-writes a boot entry;
  4. always reports reboot_required=True on success — nothing here is
     effective until the next boot, and verify_after_reboot() is the
     only honest way to confirm it actually took effect.

Per explicit project policy, this module is exercised only through
mocks/fixtures in the automated test suite — never a real write against
this development machine's actual /etc/default/grub or
/etc/kernel/cmdline.
"""
import os
import re

from core.distro import distro, get_context
from core.executor import run_pkexec
from core.persistence import history_store as hs
from core.persistence.atomic_io import atomic_write_text, backup_file

GRUB_DEFAULT_FILE = "/etc/default/grub"
KERNEL_CMDLINE_FILE = "/etc/kernel/cmdline"
_GRUB_KEY = "GRUB_CMDLINE_LINUX_DEFAULT"


def _iommu_params(vendor: str) -> list:
    prefix = "amd_iommu" if vendor == "amd" else "intel_iommu"
    return [f"{prefix}=on", "iommu=pt"]


def _log(entry_type: str, ok: bool, **kwargs):
    try:
        hs.record_operation("virt", "virt.iommu", entry_type, ok, **kwargs)
    except Exception:
        pass


# ── Pure string transforms (this is what's actually unit-tested) ──────
def _grub_line_keys(tokens: list) -> set:
    return {t.split("=")[0] for t in tokens}


def _apply_params_to_cmdline(current: str, params: list, remove: bool) -> str:
    tokens = current.split()
    keys_to_touch = _grub_line_keys(params)
    tokens = [t for t in tokens if t.split("=")[0] not in keys_to_touch]
    if not remove:
        tokens.extend(params)
    return " ".join(tokens)


def _update_grub_default_content(content: str, params: list, remove: bool) -> str:
    pattern = re.compile(rf'^{_GRUB_KEY}="([^"]*)"', re.MULTILINE)
    match = pattern.search(content)
    if match is None:
        if remove:
            return content  # nothing to remove
        new_line = f'{_GRUB_KEY}="{" ".join(params)}"\n'
        return content.rstrip("\n") + "\n" + new_line if content.strip() else new_line
    new_value = _apply_params_to_cmdline(match.group(1), params, remove)
    new_line = f'{_GRUB_KEY}="{new_value}"'
    return content[:match.start()] + new_line + content[match.end():]


def _grub_regen_cmd() -> list:
    if distro.is_debian:
        return ["update-grub"]
    if distro.is_arch:
        return ["grub-mkconfig", "-o", "/boot/grub/grub.cfg"]
    if distro.is_opensuse:
        return ["grub2-mkconfig", "-o", "/boot/grub2/grub.cfg"]
    return ["grub2-mkconfig", "-o", "/boot/grub2/grub.cfg"]  # fedora


def _configure_grub(params: list, remove: bool, job=None) -> dict:
    try:
        with open(GRUB_DEFAULT_FILE) as f:
            content = f.read()
    except OSError as e:
        return {"ok": False, "reason": "read_failed", "detail": str(e)}
    new_content = _update_grub_default_content(content, params, remove)
    if new_content == content:
        return {"ok": True, "changed": False}
    backup_file(GRUB_DEFAULT_FILE)
    try:
        atomic_write_text(GRUB_DEFAULT_FILE, new_content, mode=0o644)
    except OSError as e:
        return {"ok": False, "reason": "write_failed", "detail": str(e)}
    result = run_pkexec(_grub_regen_cmd(), timeout=60, job=job)
    return {"ok": bool(result[0]), "changed": True}


def _configure_kernelstub(params: list, remove: bool, job=None) -> dict:
    flag = "-d" if remove else "-a"
    result = run_pkexec(["kernelstub", flag, " ".join(params)], timeout=60, job=job)
    return {"ok": bool(result[0]), "changed": True}


def _configure_systemd_boot(params: list, remove: bool, job=None) -> dict:
    try:
        with open(KERNEL_CMDLINE_FILE) as f:
            content = f.read().strip()
    except FileNotFoundError:
        content = ""
    except OSError as e:
        return {"ok": False, "reason": "read_failed", "detail": str(e)}
    new_content = _apply_params_to_cmdline(content, params, remove)
    if new_content == content:
        return {"ok": True, "changed": False}
    backup_file(KERNEL_CMDLINE_FILE)
    try:
        atomic_write_text(KERNEL_CMDLINE_FILE, new_content + "\n", mode=0o644)
    except OSError as e:
        return {"ok": False, "reason": "write_failed", "detail": str(e)}
    result = run_pkexec(["bootctl", "update"], timeout=60, job=job)
    return {"ok": bool(result[0]), "changed": True}


_CONFIGURE_BY_BOOTLOADER = {
    "grub": _configure_grub,
    "kernelstub": _configure_kernelstub,
    "systemd-boot": _configure_systemd_boot,
}


def configure_iommu(job=None) -> dict:
    from core import virt_readiness as vr

    ctx = get_context()
    vendor = vr._cpu_vendor()
    if vendor not in ("amd", "intel"):
        return {"ok": False, "reason": "cpu_vendor_unknown"}

    configure_fn = _CONFIGURE_BY_BOOTLOADER.get(ctx.bootloader)
    if configure_fn is None:
        return {"ok": False, "reason": "unsupported_bootloader", "bootloader": ctx.bootloader}

    result = configure_fn(_iommu_params(vendor), remove=False, job=job)
    ok = result.get("ok", False)
    result["reboot_required"] = ok
    _log(hs.REBOOT_REQUIRED if ok else hs.ERROR, ok,
         new_value={"vendor": vendor, "bootloader": ctx.bootloader}, reboot_required=ok)
    return result


def deactivate_iommu(job=None) -> dict:
    from core import virt_readiness as vr

    ctx = get_context()
    vendor = vr._cpu_vendor()
    if vendor not in ("amd", "intel"):
        return {"ok": False, "reason": "cpu_vendor_unknown"}

    configure_fn = _CONFIGURE_BY_BOOTLOADER.get(ctx.bootloader)
    if configure_fn is None:
        return {"ok": False, "reason": "unsupported_bootloader", "bootloader": ctx.bootloader}

    result = configure_fn(_iommu_params(vendor), remove=True, job=job)
    ok = result.get("ok", False)
    result["reboot_required"] = ok
    _log(hs.DEACTIVATION if ok else hs.ERROR, ok, reboot_required=ok)
    return result


def restore_iommu_configuration() -> dict:
    """Restores the exact file backup_file() made before the last
    configure/deactivate call — never re-derives a "best guess" cmdline."""
    ctx = get_context()
    backup_by_bootloader = {
        "grub": GRUB_DEFAULT_FILE,
        "systemd-boot": KERNEL_CMDLINE_FILE,
    }
    target = backup_by_bootloader.get(ctx.bootloader)
    if target is None or not os.path.exists(f"{target}.bak"):
        return {"ok": False, "reason": "nothing_to_restore"}
    try:
        with open(f"{target}.bak") as f:
            content = f.read()
        atomic_write_text(target, content, mode=0o644)
    except OSError as e:
        return {"ok": False, "reason": "restore_failed", "detail": str(e)}
    if ctx.bootloader == "grub":
        result = run_pkexec(_grub_regen_cmd(), timeout=60)
    else:
        result = run_pkexec(["bootctl", "update"], timeout=60)
    ok = bool(result[0])
    _log(hs.RESTORE if ok else hs.ERROR, ok, reboot_required=ok)
    return {"ok": ok, "reboot_required": ok}


def verify_after_reboot() -> bool:
    """Called by the UI after the user confirms they've rebooted —
    the only honest way to know a kernel cmdline change took effect."""
    from core import virt_readiness as vr
    active = vr.check_iommu()["active"]
    _log(hs.VERIFICATION, active, verified_value=active)
    return active
