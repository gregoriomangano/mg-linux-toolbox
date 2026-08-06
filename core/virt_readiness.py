"""
Read-only virtualization readiness checks: KVM, IOMMU, VFIO. Nothing here
ever touches the bootloader, kernel command line, or any config file —
IOMMU/VFIO are reported exactly as the running kernel currently exposes
them. The only real write this module offers is fixing group membership
for KVM (adding the user to the "kvm" group), and only after explicit
confirmation from the caller — this module never does it on its own.
"""
import os
import pwd
import grp

from core.executor import command_exists, run_command

_KVM_DEV = "/dev/kvm"


def _cpu_vendor() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            text = f.read()
    except OSError:
        return ""
    if "AuthenticAMD" in text:
        return "amd"
    if "GenuineIntel" in text:
        return "intel"
    return ""


def _cpu_virt_flag_present() -> bool:
    try:
        with open("/proc/cpuinfo") as f:
            text = f.read()
    except OSError:
        return False
    flags = set()
    for line in text.splitlines():
        if line.startswith("flags") or line.startswith("Features"):
            flags.update(line.split(":", 1)[1].split())
    return "vmx" in flags or "svm" in flags


def _running_in_vm() -> bool | None:
    if os.path.exists("/.dockerenv"):
        return False
    if not command_exists("systemd-detect-virt"):
        return None
    try:
        ok, out, _ = run_command(["systemd-detect-virt", "--vm"])
    except OSError:
        return None
    if ok:
        return bool(out.strip())
    return None


def kvm_module_loaded() -> str:
    """Returns 'kvm_intel', 'kvm_amd', or '' if neither is loaded."""
    ok, out, _ = run_command(["lsmod"])
    if not ok:
        return ""
    for line in out.splitlines():
        name = line.split()[0] if line.split() else ""
        if name in ("kvm_intel", "kvm_amd"):
            return name
    return ""


def _user_in_group(group_name: str) -> bool:
    try:
        target = grp.getgrnam(group_name)
    except KeyError:
        return False
    user = pwd.getpwuid(os.getuid()).pw_name
    if user in target.gr_mem:
        return True
    return os.getgid() == target.gr_gid or target.gr_gid in os.getgroups()


def kvm_nested_active() -> "bool | None":
    for path in ("/sys/module/kvm_intel/parameters/nested",
                 "/sys/module/kvm_amd/parameters/nested"):
        try:
            with open(path) as f:
                return f.read().strip() in ("1", "Y")
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return None


def check_kvm() -> dict:
    """Full KVM readiness snapshot — never assumes: checks CPU support,
    the actual loaded module, device node presence, and real group
    membership (not just "package installed")."""
    vendor = _cpu_vendor()
    cpu_ok = _cpu_virt_flag_present()
    module = kvm_module_loaded()
    dev_exists = os.path.exists(_KVM_DEV)
    dev_writable = os.access(_KVM_DEV, os.W_OK) if dev_exists else False
    in_kvm_group = _user_in_group("kvm")
    nested = kvm_nested_active()
    virtual_machine = _running_in_vm()

    if not cpu_ok:
        state = "unavailable"
    elif not module or not dev_exists:
        state = "missing_components"
    elif not dev_writable and not in_kvm_group:
        state = "missing_permissions"
    else:
        state = "ready"

    return {
        "cpu_vendor": vendor,
        "cpu_supported": cpu_ok,
        "module_loaded": module,
        "device_exists": dev_exists,
        "device_writable": dev_writable,
        "in_kvm_group": in_kvm_group,
        "nested_active": nested,
        "virtual_machine": virtual_machine,
        "state": state,
    }


def fix_kvm_group_membership() -> dict:
    """Adds the current user to the 'kvm' group. Only ever called after
    the caller has obtained explicit user confirmation — this module has
    no auto-apply path. Requires logout/login (or a fresh shell) to take
    effect, which is disclosed by the caller, not assumed here."""
    user = pwd.getpwuid(os.getuid()).pw_name
    ok, out, err = run_command(["pkexec", "usermod", "-aG", "kvm", user])
    return {"ok": ok, "detail": err or out}


# ─── IOMMU (read-only) ───────────────────────────────────────────────
_IOMMU_GROUPS_DIR = "/sys/kernel/iommu_groups"
_AMD_IOMMU_MARKER = "/sys/class/iommu/ivhd0"


def check_iommu() -> dict:
    vendor = _cpu_vendor()
    virtual_machine = _running_in_vm()
    groups = []
    if os.path.isdir(_IOMMU_GROUPS_DIR):
        try:
            groups = sorted(os.listdir(_IOMMU_GROUPS_DIR), key=lambda x: int(x) if x.isdigit() else 0)
        except OSError:
            groups = []
    active = len(groups) > 0

    tech = ""
    if active:
        if vendor == "amd" or os.path.isdir("/sys/class/iommu") and any(
                n.startswith("ivhd") for n in os.listdir("/sys/class/iommu")):
            tech = "AMD-Vi"
        elif vendor == "intel":
            tech = "Intel VT-d"

    return {
        "active": active,
        "technology": tech,
        "group_count": len(groups),
        "virtual_machine": virtual_machine,
    }


# ─── VFIO (read-only) ─────────────────────────────────────────────────
_VFIO_MODULE_NAMES = ("vfio", "vfio_pci", "vfio_iommu_type1")


def check_vfio() -> dict:
    ok, out, _ = run_command(["lsmod"])
    loaded_names = set()
    if ok:
        for line in out.splitlines():
            name = line.split()[0] if line.split() else ""
            if name in _VFIO_MODULE_NAMES:
                loaded_names.add(name)

    devices = []
    dev_dir = "/dev/vfio"
    if os.path.isdir(dev_dir):
        try:
            devices = [n for n in os.listdir(dev_dir) if n != "vfio"]
        except OSError:
            devices = []

    iommu = check_iommu()

    return {
        "modules_loaded": sorted(loaded_names),
        "devices": devices,
        "iommu_active": iommu["active"],
    }
