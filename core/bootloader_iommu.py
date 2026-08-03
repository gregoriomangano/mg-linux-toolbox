"""
IOMMU configuration — "Configura IOMMU", "Disattiva", "Ripristina".

This is the highest-risk feature in M.G Linux Toolbox: it edits a
bootloader's kernel command line and regenerates its config, and the
change can only be verified after a reboot.

Beta 4 architecture: the GUI side (this module) only performs readiness
checks and sends enable/disable/restore to the root-owned privileged
helper (feature "virt.iommu" in core/priv_writer.py). CPU vendor and
bootloader are re-detected root-side — the helper never trusts them
from the GUI. Every file edit there is backed up first, applied
atomically, and rolled back if the bootloader's own regeneration tool
fails. The Beta 3 bug — writing /etc/default/grub or
/etc/kernel/cmdline from the unprivileged GUI process — is structurally
impossible now: there is no /etc write in this file.

The pure string transforms live in core/privileged/cmdline_edit.py
(embedded verbatim in the installed helper) so the unit tests exercise
the exact code the root-side transaction runs.

Per explicit project policy, this module is exercised only through
mocks/fixtures in the automated test suite — never a real write against
this development machine's actual /etc/default/grub or
/etc/kernel/cmdline.
"""
import os

from core.distro import get_context
from core.persistence import history_store as hs
from core.persistence.priv_client import default_privileged_writer
from core.privileged import cmdline_edit

GRUB_DEFAULT_FILE = "/etc/default/grub"
KERNEL_CMDLINE_FILE = "/etc/kernel/cmdline"

_SUPPORTED_BOOTLOADERS = ("grub", "kernelstub", "systemd-boot")


# ── Pure string transforms (delegates kept for the unit tests and any
#    other caller; the single implementation is cmdline_edit) ──────────
def _iommu_params(vendor: str) -> list:
    return cmdline_edit.iommu_params(vendor)


def _apply_params_to_cmdline(current: str, params: list, remove: bool) -> str:
    return cmdline_edit.apply_params_to_cmdline(current, params, remove)


def _update_grub_default_content(content: str, params: list, remove: bool) -> str:
    return cmdline_edit.update_grub_default_content(content, params, remove)


def _log(entry_type: str, ok: bool, **kwargs):
    try:
        hs.record_operation("virt", "virt.iommu", entry_type, ok, **kwargs)
    except Exception:
        pass


def _readiness() -> "dict | None":
    """Client-side pre-checks for a fast, friendly refusal. The helper
    re-detects both facts itself before writing anything."""
    from core import virt_readiness as vr
    vendor = vr._cpu_vendor()
    if vendor not in ("amd", "intel"):
        return {"ok": False, "reason": "cpu_vendor_unknown"}
    ctx = get_context()
    if ctx.bootloader not in _SUPPORTED_BOOTLOADERS:
        return {"ok": False, "reason": "unsupported_bootloader", "bootloader": ctx.bootloader}
    return None


def _run_helper_action(action: str) -> dict:
    result = default_privileged_writer().execute("virt.iommu", action, record_history=False)
    changed = bool(result.value.get("changed")) if isinstance(result.value, dict) else result.ok
    return {"ok": result.ok, "changed": changed,
            "reboot_required": result.reboot_required,
            "reason": None if result.ok else (result.friendly_message or "helper_failed"),
            "detail": result.technical_detail}


def configure_iommu(job=None) -> dict:
    refusal = _readiness()
    if refusal is not None:
        _log(hs.ERROR, False, technical_detail=refusal["reason"])
        return refusal
    result = _run_helper_action("enable")
    ok = result["ok"]
    _log(hs.REBOOT_REQUIRED if ok else hs.ERROR, ok, reboot_required=result["reboot_required"])
    return result


def deactivate_iommu(job=None) -> dict:
    refusal = _readiness()
    if refusal is not None:
        _log(hs.ERROR, False, technical_detail=refusal["reason"])
        return refusal
    result = _run_helper_action("disable")
    ok = result["ok"]
    _log(hs.DEACTIVATION if ok else hs.ERROR, ok, reboot_required=result["reboot_required"])
    return result


def restore_iommu_configuration() -> dict:
    result = _run_helper_action("restore")
    ok = result["ok"]
    _log(hs.RESTORE if ok else hs.ERROR, ok, reboot_required=result["reboot_required"])
    return result


def verify_after_reboot() -> bool:
    """Called by the UI after the user confirms they've rebooted —
    the only honest way to know a kernel cmdline change took effect."""
    from core import virt_readiness as vr
    active = vr.check_iommu()["active"]
    _log(hs.VERIFICATION, active, verified_value=active)
    return active
