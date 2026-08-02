"""
Real VFIO passthrough wizard — lists PCI devices and their IOMMU
groups, protects the boot disk controller and the primary GPU from
ever being selectable, configures vfio-pci via the standard modprobe.d
+ modules-load.d mechanism, regenerates the initramfs, and reports
reboot_required (a driver binding at boot can only be verified after
that reboot).

Protection rules (never overridable from the GUI, re-checked server-side
here regardless of what the caller passes in):
  - the entire PCI mass-storage class (0x01xx: SATA/NVMe/RAID/IDE
    controllers) is excluded — passing through the controller a running
    host might be booted from is exactly the kind of mistake this app
    exists to prevent;
  - the PCI device with boot_vga=1 (the GPU the firmware actually used
    to boot, real sysfs attribute) is excluded — losing your only GPU
    output makes recovery need a second machine.

Per explicit project policy, the actual vfio-pci binding is only ever
verified after a real reboot the user has confirmed happened
(verify_after_reboot()) — never assumed from a successful config write.
Exercised in tests only via mocks/fixtures, never a real initramfs
rebuild on the host running the suite.
"""
import os

from core.distro import distro
from core.executor import run_command, run_pkexec
from core.persistence import history_store as hs
from core.persistence.atomic_io import atomic_write_text, backup_file

PCI_DEVICES_DIR = "/sys/bus/pci/devices"
MODPROBE_FILE = "/etc/modprobe.d/90-mg-linux-toolbox-vfio.conf"
MODULES_LOAD_FILE = "/etc/modules-load.d/90-mg-linux-toolbox-vfio.conf"

_PROTECTED_CLASS_PREFIX = "01"  # PCI class 0x01xx = mass storage controller


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _log(entry_type: str, ok: bool, **kwargs):
    try:
        hs.record_operation("virt", "virt.vfio", entry_type, ok, **kwargs)
    except Exception:
        pass


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
    if _is_boot_vga(address):
        return "primary_gpu"
    return None


def list_pci_devices() -> list:
    """Real device list via lspci -Dnmm (machine-readable, one line per
    device, quoted fields — never guessed from human-readable -v output).
    Returns [{"address","vendor_id","device_id","description",
    "iommu_group","protected","protection_reason"}, ...]."""
    ok, out, _ = run_command(["lspci", "-Dnmm"], timeout=10)
    if not ok:
        return []
    devices = []
    for line in out.splitlines():
        parts = _parse_lspci_mm_line(line)
        if parts is None:
            continue
        address, vendor_id, device_id, description = parts
        reason = _protection_reason(address)
        devices.append({
            "address": address, "vendor_id": vendor_id, "device_id": device_id,
            "description": description, "iommu_group": _iommu_group(address),
            "protected": reason is not None, "protection_reason": reason,
        })
    return devices


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


def _validate_selection(addresses: list, devices: "list | None" = None) -> "str | None":
    """Re-derives protection status itself — never trusts a
    "not protected" claim coming from the caller/GUI. Returns an error
    reason string, or None if the whole selection is safe."""
    by_address = {d["address"]: d for d in (devices or list_pci_devices())}
    for address in addresses:
        reason = _protection_reason(address)
        if reason is not None:
            return reason
        if address not in by_address:
            return "device_not_found"
    return None


def _initramfs_regen_cmd() -> list:
    if distro.is_arch:
        return ["mkinitcpio", "-P"]
    if distro.is_debian:
        return ["update-initramfs", "-u"]
    return ["dracut", "-f"]  # fedora and openSUSE are both dracut-based


def configure_vfio(addresses: list, job=None) -> dict:
    devices = list_pci_devices()
    reason = _validate_selection(addresses, devices)
    if reason is not None:
        _log(hs.ERROR, False, technical_detail=reason)
        return {"ok": False, "reason": reason}

    by_address = {d["address"]: d for d in devices}
    ids = ",".join(f"{by_address[a]['vendor_id']}:{by_address[a]['device_id']}" for a in addresses)

    backup_file(MODPROBE_FILE)
    backup_file(MODULES_LOAD_FILE)
    try:
        atomic_write_text(MODPROBE_FILE, f"options vfio-pci ids={ids}\n", mode=0o644)
        atomic_write_text(MODULES_LOAD_FILE, "vfio\nvfio_pci\nvfio_iommu_type1\n", mode=0o644)
    except OSError as e:
        _log(hs.ERROR, False, technical_detail=str(e))
        return {"ok": False, "reason": "write_failed", "detail": str(e)}

    result = run_pkexec(_initramfs_regen_cmd(), timeout=180, job=job)
    ok = bool(result[0])
    _log(hs.REBOOT_REQUIRED if ok else hs.ERROR, ok,
         new_value={"addresses": addresses, "ids": ids}, reboot_required=ok)
    return {"ok": ok, "reboot_required": ok, "addresses": addresses}


def _remove_configuration(job=None) -> dict:
    removed_any = False
    for path in (MODPROBE_FILE, MODULES_LOAD_FILE):
        if os.path.exists(path):
            backup_file(path)
            os.remove(path)
            removed_any = True
    if not removed_any:
        return {"ok": True, "changed": False, "reboot_required": False}
    result = run_pkexec(_initramfs_regen_cmd(), timeout=180, job=job)
    ok = bool(result[0])
    return {"ok": ok, "changed": True, "reboot_required": ok}


def remove_vfio_configuration(job=None) -> dict:
    result = _remove_configuration(job=job)
    _log(hs.DEACTIVATION if result["ok"] else hs.ERROR, result["ok"], reboot_required=result["reboot_required"])
    return result


def restore_original_driver(job=None) -> dict:
    """Mechanically identical to remove_vfio_configuration() — without
    our modprobe.d override, the device binds to its normal in-kernel
    driver again at the next boot. Kept as a distinct entry point
    because it answers a different question in the UI ("undo the
    passthrough I already booted into" vs. "I changed my mind before
    rebooting")."""
    result = _remove_configuration(job=job)
    _log(hs.RESTORE if result["ok"] else hs.ERROR, result["ok"], reboot_required=result["reboot_required"])
    return result


def verify_after_reboot(addresses: list) -> dict:
    """Only honest way to know a device actually bound to vfio-pci —
    reads the real /sys/bus/pci/devices/<addr>/driver symlink."""
    bound = {}
    for address in addresses:
        driver_link = os.path.join(PCI_DEVICES_DIR, address, "driver")
        try:
            bound[address] = os.path.basename(os.readlink(driver_link))
        except OSError:
            bound[address] = None
    all_bound = all(driver == "vfio-pci" for driver in bound.values())
    _log(hs.VERIFICATION, all_bound, verified_value=bound)
    return {"ok": all_bound, "drivers": bound}
