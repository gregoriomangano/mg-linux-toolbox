"""
Per-device power management: which devices can wake the machine
(power/wakeup) and which devices use runtime autosuspend (power/control).

Not a single-value KernelFeature — this is a *list* of independently
toggleable devices discovered dynamically. The security model still
holds: the GUI never supplies a filesystem path, only a short
"bus:device_id" string that the privileged writer re-validates and
re-resolves itself against an allow-listed set of sysfs bus directories
before touching anything.
"""
import os

_ALLOWED_BUSES = {
    "usb": "usb",
    "pci": "pci",
    "acpi": "acpi",
    "serio": "serio",
    "hid": "hid",
}

# ACPI hardware IDs for well-known fixed-function devices.
_ACPI_HID_CATEGORY = {
    "PNP0C0D": "lid",
    "PNP0C0E": "sleep_button",
    "PNP0C0C": "power_button",
}

# PCI class (top byte of class code) -> category.
_PCI_CLASS_CATEGORY = {
    "0x02": "network",  # network controller
}

# Infrastructure, not an end-user peripheral — never shown for wakeup or
# PM control, same reasoning as excluding root hubs.
_USB_INFRASTRUCTURE_WORDS = ("hub", "host controller", "xhci", "ehci", "uhci", "ohci")


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _hid_boot_protocol_category(dev_dir: str, dev_id: str) -> "str | None":
    """
    Checks child USB interfaces (e.g. "3-1.3:1.0") for the standard HID
    boot-protocol convention: bInterfaceClass=03 (HID) with
    bInterfaceProtocol 1=keyboard, 2=mouse. This is the vendor-agnostic
    way to tell a gaming mouse with a brand name like "Razer Naga" (no
    literal "mouse" in its product string) apart from a keyboard —
    verified against a real device on this machine where the product
    name alone would have misclassified it.
    """
    parent = os.path.dirname(dev_dir)
    try:
        siblings = os.listdir(parent)
    except OSError:
        return None
    for name in siblings:
        if not name.startswith(f"{dev_id}:"):
            continue
        iface_dir = os.path.join(parent, name)
        if _read(os.path.join(iface_dir, "bInterfaceClass")) != "03":
            continue
        protocol = _read(os.path.join(iface_dir, "bInterfaceProtocol"))
        if protocol == "01":
            return "keyboard"
        if protocol == "02":
            return "mouse"
    return None


def _classify_usb(dev_dir: str) -> "str | None":
    product = _read(os.path.join(dev_dir, "product")).lower()
    if not product or "root hub" in product:
        return None
    if any(w in product for w in _USB_INFRASTRUCTURE_WORDS):
        return None

    dev_id = os.path.basename(dev_dir)
    hid_category = _hid_boot_protocol_category(dev_dir, dev_id)
    if hid_category is not None:
        return hid_category

    # Fallback: product-name heuristics for everything HID boot-protocol
    # doesn't cover (bluetooth radios, webcams, gamepads aren't boot-HID).
    if "bluetooth" in product:
        return "bluetooth"
    if "keyboard" in product:
        return "keyboard"
    if "mouse" in product or "trackpad" in product or "touchpad" in product:
        return "mouse"
    if any(w in product for w in ("gamepad", "joystick", "xbox", "dualshock", "dualsense")):
        return "gamepad"
    if any(w in product for w in ("webcam", "camera")):
        return "webcam"
    if any(w in product for w in ("microphone", "headset", "headphone")) or product.endswith("audio"):
        return "audio"
    if "card reader" in product or "sd reader" in product:
        return "card_reader"
    return "usb_generic"


def _classify_pci(dev_dir: str) -> "str | None":
    cls = _read(os.path.join(dev_dir, "class"))  # e.g. "0x030000"
    if not cls:
        return None
    return _PCI_CLASS_CATEGORY.get(cls[:4])


def _classify_acpi(dev_dir: str) -> "str | None":
    hid_path = os.path.join(dev_dir, "hid")
    hid = _read(hid_path) if os.path.exists(hid_path) else _read(os.path.join(dev_dir, "modalias"))
    for known_hid, cat in _ACPI_HID_CATEGORY.items():
        if known_hid in hid:
            return cat
    return None


def _classify(bus: str, dev_dir: str) -> "str | None":
    if bus == "usb":
        return _classify_usb(dev_dir)
    if bus == "pci":
        return _classify_pci(dev_dir)
    if bus == "acpi":
        return _classify_acpi(dev_dir)
    if bus == "serio":
        return "keyboard"  # legacy PS/2 keyboard controllers
    return None


def _device_label(dev_dir: str, category: str, dev_id: str) -> str:
    product = _read(os.path.join(dev_dir, "product"))
    return product if product else f"{category} ({dev_id})"


# Wakeup categories a beginner can actually make sense of — anything
# that doesn't classify into one of these is left out entirely, per spec
# ("non mostrare... dispositivi non identificati").
_WAKEUP_CATEGORIES = {"keyboard", "mouse", "network", "bluetooth", "gamepad", "lid"}

# PM (runtime autosuspend) categories — deliberately excludes storage,
# input devices and USB host controllers, so this can never be pointed
# at a disk, keyboard or mouse.
_PM_CATEGORIES = {"webcam", "bluetooth", "card_reader", "audio", "gamepad", "usb_generic"}


def list_wakeup_capable_devices(sys_root: str = "/sys") -> list:
    """Returns [{"bus", "device_id", "category", "label", "enabled"}, ...]."""
    results = []
    for bus in _ALLOWED_BUSES:
        base = os.path.join(sys_root, "bus", bus, "devices")
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for name in names:
            dev_dir = os.path.join(base, name)
            wakeup_path = os.path.join(dev_dir, "power", "wakeup")
            if not os.path.isfile(wakeup_path):
                continue
            category = _classify(bus, dev_dir)
            if category not in _WAKEUP_CATEGORIES:
                continue
            results.append({
                "bus": bus, "device_id": name, "category": category,
                "label": _device_label(dev_dir, category, name),
                "enabled": _read(wakeup_path) == "enabled",
            })
    return results


def list_pm_controllable_devices(sys_root: str = "/sys") -> list:
    results = []
    base = os.path.join(sys_root, "bus", "usb", "devices")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return results
    for name in names:
        dev_dir = os.path.join(base, name)
        control_path = os.path.join(dev_dir, "power", "control")
        if not os.path.isfile(control_path):
            continue
        category = _classify_usb(dev_dir)
        if category not in _PM_CATEGORIES:
            continue
        results.append({
            "bus": "usb", "device_id": name, "category": category,
            "label": _device_label(dev_dir, category, name),
            "auto": _read(control_path) == "auto",
        })
    return results
