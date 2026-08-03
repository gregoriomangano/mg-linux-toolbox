"""
VFIO passthrough wizard, read side + orchestration.

Beta 4 architecture: this module only READS the system (lspci, /sys) and
builds the human-friendly device/group model for the UI. Every write —
/etc/modprobe.d, /etc/modules-load.d, initramfs — happens inside the
root-owned privileged helper as ONE transaction (feature "virt.vfio" in
core/priv_writer.py): server-side re-validation, backup, atomic writes,
content verification, initramfs regeneration, file rollback on failure.
The Beta 3 bug — writing /etc from the unprivileged GUI process — is
structurally impossible here now: there is no /etc write in this file.

Protection rules (re-checked independently helper-side, never trusted
from the GUI):
  - PCI mass-storage class (0x01xx) — the disk controller the running
    host may be booted from;
  - the boot_vga=1 GPU — the display the desktop is using;
  - bridges/memory/system devices (0x05xx, 0x06xx, 0x08xx) — chipset
    infrastructure a host can't lose;
  - anything whose IOMMU group also contains a protected device — a
    group is the smallest passthrough unit, it moves whole or not at all;
  - anything with no IOMMU group (IOMMU off or not isolated).

Per explicit project policy, the actual vfio-pci binding is only ever
verified after a real reboot the user has confirmed happened
(verify_after_reboot()) — never assumed from a successful config write.
Exercised in tests only via mocks/fixtures, never a real initramfs
rebuild on the host running the suite.
"""
import os

from core.executor import run_command
from core.persistence.priv_client import default_privileged_writer

PCI_DEVICES_DIR = "/sys/bus/pci/devices"
MODPROBE_FILE = "/etc/modprobe.d/90-mg-linux-toolbox-vfio.conf"
MODULES_LOAD_FILE = "/etc/modules-load.d/90-mg-linux-toolbox-vfio.conf"

_PROTECTED_CLASS_PREFIX = "01"  # PCI class 0x01xx = mass storage controller
_ESSENTIAL_CLASS_PREFIXES = ("05", "06", "08")  # memory, bridges, system peripherals

# PCI class prefix -> i18n key for the plain-language device kind shown
# in the wizard (technical ids stay behind "Mostra dettagli tecnici").
_CLASS_KIND_KEYS = {
    "01": "vfio_kind_storage",
    "02": "vfio_kind_network",
    "03": "vfio_kind_gpu",
    "04": "vfio_kind_audio",
    "05": "vfio_kind_system",
    "06": "vfio_kind_system",
    "08": "vfio_kind_system",
    "0c": "vfio_kind_usb",
    "0d": "vfio_kind_network",
}


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _iommu_group(address: str) -> str:
    link = os.path.join(PCI_DEVICES_DIR, address, "iommu_group")
    try:
        return os.path.basename(os.readlink(link))
    except OSError:
        return ""


def _is_boot_vga(address: str) -> bool:
    return _read(os.path.join(PCI_DEVICES_DIR, address, "boot_vga")) == "1"


def _pci_class(address: str) -> str:
    # e.g. "0x030000" -> "03" (display) or "0x010601" -> "01" (storage)
    raw = _read(os.path.join(PCI_DEVICES_DIR, address, "class"))
    return raw[2:4] if raw.startswith("0x") and len(raw) >= 4 else ""


def _protection_reason(address: str) -> "str | None":
    if _pci_class(address) == _PROTECTED_CLASS_PREFIX:
        return "storage_controller"
    if _pci_class(address) in _ESSENTIAL_CLASS_PREFIXES:
        return "essential_device"
    if _is_boot_vga(address):
        return "primary_gpu"
    return None


def _parse_lspci_mm_line(line: str) -> "tuple | None":
    """Parses one `lspci -Dnmm` line, e.g.:
    0000:01:00.0 "0300" "10de" "1234" -ra1 "10de" "5678"
    Field order: address, class, vendor_id, device_id, [rev], [subvendor, subdevice].
    Only address/vendor_id/device_id are needed here."""
    import shlex
    try:
        tokens = shlex.split(line)
    except ValueError:
        return None
    if len(tokens) < 4:
        return None
    address, pci_class, vendor_id, device_id = tokens[0], tokens[1], tokens[2], tokens[3]
    description = f"[{pci_class}] {vendor_id}:{device_id}"
    return address, vendor_id.strip('"'), device_id.strip('"'), description


def _human_names() -> dict:
    """address -> "Vendor Device" from `lspci -Dmm` (same machine-readable
    format, human vendor/device strings instead of hex ids). Best-effort:
    an empty dict just means the UI falls back to the technical text."""
    ok, out, _ = run_command(["lspci", "-Dmm"], timeout=10)
    if not ok:
        return {}
    import shlex
    names = {}
    for line in out.splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        if len(tokens) < 4:
            continue
        address, _cls, vendor, device = tokens[0], tokens[1], tokens[2], tokens[3]
        names[address] = f"{vendor} {device}".strip()
    return names


def list_pci_devices() -> list:
    """Real device list via lspci -Dnmm (machine-readable, one line per
    device, quoted fields — never guessed from human-readable -v output),
    enriched with the human vendor/device name from lspci -Dmm.
    Returns [{"address","vendor_id","device_id","description","name",
    "class_code","kind_key","iommu_group","protected",
    "protection_reason"}, ...]."""
    ok, out, _ = run_command(["lspci", "-Dnmm"], timeout=10)
    if not ok:
        return []
    names = _human_names()
    devices = []
    for line in out.splitlines():
        parts = _parse_lspci_mm_line(line)
        if parts is None:
            continue
        address, vendor_id, device_id, description = parts
        reason = _protection_reason(address)
        class_code = _pci_class(address)
        devices.append({
            "address": address, "vendor_id": vendor_id, "device_id": device_id,
            "description": description,
            "name": names.get(address, ""),
            "class_code": class_code,
            "kind_key": _CLASS_KIND_KEYS.get(class_code, "vfio_kind_other"),
            "iommu_group": _iommu_group(address),
            "protected": reason is not None, "protection_reason": reason,
        })
    return devices


def list_iommu_groups(devices: "list | None" = None) -> dict:
    """Groups list_pci_devices() results by IOMMU group — a passthrough
    device can't be split from others sharing its group without ACS
    override (not something this app enables), so the wizard needs to
    show group-mates, not just the one device the user picked."""
    devices = devices if devices is not None else list_pci_devices()
    groups = {}
    for dev in devices:
        groups.setdefault(dev["iommu_group"], []).append(dev)
    return groups


def passthrough_groups(devices: "list | None" = None, iommu_active: "bool | None" = None) -> list:
    """
    The UI's whole selection model: IOMMU groups, each either selectable
    as a unit or disabled with a plain-language reason. Selection happens
    per GROUP — never per single device — because a group is the smallest
    unit the kernel can hand to a VM.
    Returns [{"group", "devices", "selectable", "reason"}], sorted by
    numeric group id. Reasons: "no_iommu" (IOMMU off), "no_group"
    (device outside any group), "contains_protected".
    """
    if iommu_active is None:
        from core import virt_readiness as vr
        iommu_active = bool(vr.check_iommu().get("active"))
    devices = devices if devices is not None else list_pci_devices()
    result = []
    for group, members in sorted(list_iommu_groups(devices).items(),
                                 key=lambda kv: (kv[0] == "", _numeric(kv[0]))):
        if not iommu_active:
            selectable, reason = False, "no_iommu"
        elif group == "":
            selectable, reason = False, "no_group"
        elif any(d["protected"] for d in members):
            selectable, reason = False, "contains_protected"
        else:
            selectable, reason = True, None
        result.append({"group": group, "devices": members,
                       "selectable": selectable, "reason": reason})
    return result


def _numeric(group: str) -> int:
    try:
        return int(group)
    except ValueError:
        return 1 << 30


def has_passthrough_candidates(groups: "list | None" = None) -> bool:
    groups = groups if groups is not None else passthrough_groups()
    return any(g["selectable"] for g in groups)


def _validate_selection(addresses: list, devices: "list | None" = None) -> "str | None":
    """Re-derives protection status itself — never trusts a
    "not protected" claim coming from the caller/GUI. Returns an error
    reason string, or None if the whole selection is safe. The helper
    re-runs an equivalent check as root before writing anything."""
    devices = devices or list_pci_devices()
    by_address = {d["address"]: d for d in devices}
    if not addresses:
        return "no_devices"
    for address in addresses:
        if address not in by_address:
            return "device_not_found"
        reason = _protection_reason(address)
        if reason is not None:
            return reason
    # Whole-group rule: every selected device's group must be fully
    # selected (a group moves whole or not at all).
    selected = set(addresses)
    for address in addresses:
        group = by_address[address]["iommu_group"]
        if group == "":
            return "no_group"
        for dev in devices:
            if dev["iommu_group"] == group and dev["address"] not in selected:
                return "incomplete_group"
    return None


def configure_vfio(addresses: list, job=None) -> dict:
    """Client-side pre-validation for fast, friendly errors; the real
    write is one helper transaction with its own independent validation,
    backup, verification, initramfs regen and rollback."""
    devices = list_pci_devices()
    reason = _validate_selection(addresses, devices)
    if reason is not None:
        return {"ok": False, "reason": reason}
    result = default_privileged_writer().execute(
        "virt.vfio", "configure", {"addresses": list(addresses)})
    return {
        "ok": result.ok,
        "reboot_required": result.reboot_required,
        "addresses": addresses,
        "reason": None if result.ok else (result.friendly_message or "helper_failed"),
        "detail": result.technical_detail,
    }


def remove_vfio_configuration(job=None) -> dict:
    result = default_privileged_writer().execute("virt.vfio", "disable")
    changed = bool(result.value.get("changed")) if isinstance(result.value, dict) else False
    return {"ok": result.ok, "changed": changed,
            "reboot_required": result.reboot_required, "detail": result.technical_detail}


def restore_original_driver(job=None) -> dict:
    """Mechanically identical to remove_vfio_configuration() — without
    our modprobe.d override, the device binds to its normal in-kernel
    driver again at the next boot. Kept as a distinct entry point
    because it answers a different question in the UI ("undo the
    passthrough I already booted into" vs. "I changed my mind before
    rebooting")."""
    result = default_privileged_writer().execute("virt.vfio", "restore")
    changed = bool(result.value.get("changed")) if isinstance(result.value, dict) else False
    return {"ok": result.ok, "changed": changed,
            "reboot_required": result.reboot_required, "detail": result.technical_detail}


def verify_after_reboot(addresses: list) -> dict:
    """Only honest way to know a device actually bound to vfio-pci —
    reads the real /sys/bus/pci/devices/<addr>/driver symlink."""
    from core.persistence import history_store as hs
    bound = {}
    for address in addresses:
        driver_link = os.path.join(PCI_DEVICES_DIR, address, "driver")
        try:
            bound[address] = os.path.basename(os.readlink(driver_link))
        except OSError:
            bound[address] = None
    all_bound = all(driver == "vfio-pci" for driver in bound.values())
    try:
        hs.record_operation("virt", "virt.vfio", hs.VERIFICATION, all_bound, verified_value=bound)
    except Exception:
        pass
    return {"ok": all_bound, "drivers": bound}
