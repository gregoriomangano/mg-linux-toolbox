"""
System Info page — reads all hardware data from /proc and /sys (kernel-level).
Works on ALL Linux distributions. Acts as the app's homepage/dashboard.

Note: intentionally never reads or displays any network/IP address — this
page is about local hardware specs only.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib
from core.i18n import T, on_change
import os
import platform


def _read_file(path: str, fallback: str = "—") -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return fallback


def _get_cpu_name() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "Unknown"


def _get_cpu_cores() -> tuple:
    """Returns (physical_cores, logical_cores)."""
    logical = os.cpu_count() or 1
    physical = logical
    try:
        with open("/proc/cpuinfo") as f:
            content = f.read()
            ids = set()
            for line in content.split("\n"):
                if line.startswith("core id"):
                    ids.add(line.split(":")[1].strip())
            if ids:
                # Multiply by number of physical packages
                packages = set()
                for line in content.split("\n"):
                    if line.startswith("physical id"):
                        packages.add(line.split(":")[1].strip())
                physical = len(ids) * max(len(packages), 1)
    except Exception:
        pass
    return physical, logical


def _get_ram_info() -> tuple:
    """Returns (total_gb, used_gb, percent_used)."""
    total = avail = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])  # kB
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1])
    except Exception:
        return (0, 0, 0)
    total_gb = total / 1048576  # kB to GB
    used_gb = (total - avail) / 1048576
    pct = (total - avail) / total * 100 if total else 0
    return round(total_gb, 1), round(used_gb, 1), round(pct, 1)


def _get_swap_info() -> tuple:
    """Returns (total_gb, used_gb)."""
    total = free = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("SwapTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("SwapFree:"):
                    free = int(line.split()[1])
    except Exception:
        return (0, 0)
    return round(total / 1048576, 1), round((total - free) / 1048576, 1)


def _is_removable(dev: str) -> bool:
    """True if the whole-disk device (e.g. 'sdb') is marked removable by the
    kernel — a solid, portable signal for 'this is a USB stick / external
    drive', available on every Linux system via sysfs."""
    return _read_file(f"/sys/block/{dev}/removable", "0").strip() == "1"


def _get_disks() -> list:
    """Returns list of (name, size_gb, mount, fstype, removable) from
    /proc/mounts + /sys/block. Works on every Linux distro since it only
    reads kernel-exposed files, no external tools required."""
    disks = []
    try:
        # Get mount points
        mounts = {}
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if parts[0].startswith("/dev/"):
                    dev = os.path.basename(parts[0])
                    mounts[dev] = (parts[1], parts[2])  # mount, fstype

        # Get block devices
        for dev in sorted(os.listdir("/sys/block")):
            if dev.startswith("loop") or dev.startswith("ram"):
                continue
            size_path = f"/sys/block/{dev}/size"
            sectors = int(_read_file(size_path, "0"))
            size_gb = round(sectors * 512 / (1024**3), 1)
            if size_gb < 0.1:
                continue
            removable = _is_removable(dev)

            # Check partitions
            for part in sorted(os.listdir(f"/sys/block/{dev}")):
                if part.startswith(dev):
                    part_size = int(_read_file(f"/sys/block/{dev}/{part}/size", "0"))
                    part_gb = round(part_size * 512 / (1024**3), 1)
                    mount, fstype = mounts.get(part, ("—", "—"))
                    if part_gb > 0.1:
                        disks.append((part, part_gb, mount, fstype, removable))

            if dev not in [d[0][:len(dev)] for d in disks if len(d[0]) > len(dev)]:
                mount, fstype = mounts.get(dev, ("—", "—"))
                disks.append((dev, size_gb, mount, fstype, removable))
    except Exception:
        pass
    return disks


# GPT partition-type GUIDs → what the partition is *for*, regardless of
# whether it's mounted right now. Covers the common Windows/Linux/EFI
# cases so unmounted partitions can still be identified.
_GPT_TYPE_HINTS = {
    "c12a7328-f81f-11d2-ba4b-00a0c93ec93b": "hint_efi",
    "e3c9e316-0b5c-4db8-817d-f92df00215ae": "hint_msr",
    "de94bba4-06d1-4d40-a16a-bfd50179d6ac": "hint_win_recovery",
    "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7": "hint_windows",
    "0fc63daf-8483-4772-8e79-3d69d8477de4": "hint_linux_other",
    "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f": "hint_swap",
    "933ac7e1-2eb4-4f13-b844-0e14e2aef915": "hint_linux_home",
    "4f68bce3-e8cd-4db1-96e7-fbcaf984b709": "hint_linux_root",
}


def _read_udev_property(name: str, key: str) -> str:
    """
    Read a single property (e.g. ID_FS_TYPE, ID_PART_ENTRY_TYPE) from the
    udev device database for /dev/<name>, WITHOUT needing root or any
    external tool (blkid/lsblk) — just a cache file kept up to date by
    udev. Present on virtually every desktop Linux distro (anything using
    systemd-udevd or eudev). Returns "" if unavailable for any reason
    (older/minimal system, permissions, device not found, etc.) so callers
    can gracefully fall back to the heuristic guess instead.
    """
    try:
        rdev = os.stat(f"/dev/{name}").st_rdev
        path = f"/run/udev/data/b{os.major(rdev)}:{os.minor(rdev)}"
        prefix = f"E:{key}="
        with open(path) as f:
            for line in f:
                if line.startswith(prefix):
                    return line[len(prefix):].strip()
    except Exception:
        pass
    return ""


def _get_active_swap_devices() -> set:
    """Device basenames (e.g. 'zram0', 'sda2') currently used as swap, from /proc/swaps."""
    devices = set()
    try:
        with open("/proc/swaps") as f:
            next(f)  # header line
            for line in f:
                parts = line.split()
                if parts and parts[0].startswith("/dev/"):
                    devices.add(os.path.basename(parts[0]))
    except Exception:
        pass
    return devices


def _guess_partition_hint(name: str, fstype: str, mount: str, size_gb: float,
                           swap_devices: set) -> str:
    """
    Best-effort, plain-language guess of what a disk/partition is for.
    Even when unmounted (fstype/mount unknown from /proc/mounts alone),
    tries the udev device-info cache first — it records the filesystem
    type and the GPT partition-type GUID (Windows data, EFI, Microsoft
    Reserved, Windows Recovery, Linux...) the moment the kernel discovers
    the partition, with no mounting, no root, and no external tool
    required. Falls back to the size/mount heuristic when that cache
    isn't available (works on every Linux system either way).
    Returns "" when we genuinely have no info to guess from.
    """
    name_l = name.lower()
    fs = fstype.lower() if fstype and fstype != "—" else ""

    if name_l.startswith("zram"):
        return T("hint_zram")
    if name in swap_devices or fs == "swap":
        return T("hint_swap")

    # ── udev cache: works even for partitions that aren't mounted ──────
    part_type_guid = _read_udev_property(name, "ID_PART_ENTRY_TYPE").lower()
    if part_type_guid in _GPT_TYPE_HINTS:
        return T(_GPT_TYPE_HINTS[part_type_guid])
    if not fs:
        fs = _read_udev_property(name, "ID_FS_TYPE").lower()

    if mount == "/":
        return T("hint_linux_root")
    if mount == "/home":
        return T("hint_linux_home")
    if mount in ("/boot/efi",):
        return T("hint_efi")
    if mount == "/boot":
        return T("hint_linux_boot")
    if "recovery" in mount.lower():
        return T("hint_win_recovery")
    if fs in ("vfat", "fat32", "fat16", "fat") and size_gb < 1.0:
        return T("hint_efi")
    if fs == "ntfs":
        if mount == "—" and size_gb < 1.5:
            return T("hint_win_recovery")
        return T("hint_windows")
    if fs in ("ext4", "ext3", "ext2", "btrfs", "xfs", "f2fs"):
        return T("hint_linux_other")
    if fs:
        return T("hint_data")
    if mount != "—":
        return T("hint_data")
    return T("hint_unknown")


def _get_mount_usage(mount: str) -> tuple:
    """Returns (total_gb, used_gb, percent_used) for a mounted path via statvfs."""
    try:
        st = os.statvfs(mount)
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bavail
        used = total - free
        if total == 0:
            return (0, 0, 0)
        pct = used / total * 100
        return round(total / (1024**3), 1), round(used / (1024**3), 1), round(pct, 1)
    except Exception:
        return (0, 0, 0)


def _get_kernel() -> str:
    return platform.release()


def _get_hostname() -> str:
    return _read_file("/etc/hostname", platform.node())


def _get_distro() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return "Linux"


def _get_uptime() -> str:
    try:
        secs = float(_read_file("/proc/uptime", "0").split()[0])
        hours = int(secs // 3600)
        mins = int((secs % 3600) // 60)
        if hours > 24:
            days = hours // 24
            hours = hours % 24
            return f"{days}d {hours}h {mins}m"
        return f"{hours}h {mins}m"
    except Exception:
        return "—"


def _get_cpu_usage() -> float:
    """Quick CPU usage estimate from /proc/stat."""
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = line.split()
        idle = int(parts[4])
        total = sum(int(x) for x in parts[1:])
        # Second read after a tiny delay for delta
        import time
        time.sleep(0.1)
        with open("/proc/stat") as f:
            line2 = f.readline()
        parts2 = line2.split()
        idle2 = int(parts2[4])
        total2 = sum(int(x) for x in parts2[1:])
        d_total = total2 - total
        d_idle = idle2 - idle
        if d_total == 0:
            return 0.0
        return round((1 - d_idle / d_total) * 100, 1)
    except Exception:
        return 0.0


def _get_gpu_name() -> str:
    """Try to read GPU from sysfs (works without lspci)."""
    try:
        drm_path = "/sys/class/drm"
        for card in sorted(os.listdir(drm_path)):
            if card.startswith("card") and not "-" in card:
                dev_path = os.path.join(drm_path, card, "device")
                # Read vendor and device IDs
                vendor = _read_file(os.path.join(dev_path, "vendor"), "")
                # Try uevent for driver name
                uevent = _read_file(os.path.join(dev_path, "uevent"), "")
                for line in uevent.split("\n"):
                    if line.startswith("DRIVER="):
                        driver = line.split("=")[1]
                        if "amdgpu" in driver:
                            return f"AMD GPU ({driver})"
                        elif "i915" in driver or "xe" in driver:
                            return f"Intel GPU ({driver})"
                        elif "nvidia" in driver or "nouveau" in driver:
                            return f"NVIDIA GPU ({driver})"
                        return driver
    except Exception:
        pass
    return "—"


# ── I18n strings for system info ──────────────────────────────────
_info_strings = {
    "sysinfo_tab":      {"en": "System Info", "it": "Info sistema", "es": "Info Sistema", "fr": "Info Système"},
    "sysinfo_hero_sub": {"en": "Live overview of this machine", "it": "Panoramica in tempo reale di questo PC", "es": "Resumen en vivo de este equipo", "fr": "Aperçu en direct de cette machine"},
    "sysinfo_overview": {"en": "Overview", "it": "Panoramica", "es": "Resumen", "fr": "Aperçu"},
    "sysinfo_hw":       {"en": "Hardware Details", "it": "Dettagli hardware", "es": "Detalles de Hardware", "fr": "Détails Matériel"},
    "sysinfo_os":       {"en": "Operating System", "it": "Sistema operativo", "es": "Sistema Operativo", "fr": "Système d'Exploitation"},
    "sysinfo_storage":  {"en": "Storage &amp; Partitions", "it": "Archiviazione &amp; partizioni", "es": "Almacenamiento y Particiones", "fr": "Stockage et Partitions"},
    "sysinfo_cpu":      {"en": "Processor", "it": "Processore", "es": "Procesador", "fr": "Processeur"},
    "sysinfo_cores":    {"en": "Cores", "it": "Core", "es": "Núcleos", "fr": "Cœurs"},
    "sysinfo_ram":      {"en": "Memory (RAM)", "it": "Memoria (RAM)", "es": "Memoria (RAM)", "fr": "Mémoire (RAM)"},
    "sysinfo_swap":     {"en": "Swap", "it": "Swap", "es": "Swap", "fr": "Swap"},
    "sysinfo_gpu":      {"en": "Graphics", "it": "Grafica", "es": "Gráficos", "fr": "Graphiques"},
    "sysinfo_kernel":   {"en": "Kernel", "it": "Kernel", "es": "Kernel", "fr": "Noyau"},
    "sysinfo_distro":   {"en": "Distribution", "it": "Distribuzione", "es": "Distribución", "fr": "Distribution"},
    "sysinfo_host":     {"en": "Hostname", "it": "Nome host", "es": "Nombre de Host", "fr": "Nom d'Hôte"},
    "sysinfo_uptime":   {"en": "Uptime", "it": "Tempo di attività", "es": "Tiempo Activo", "fr": "Temps de Fonctionnement"},
    "sysinfo_cpu_usage": {"en": "CPU Usage", "it": "Uso CPU", "es": "Uso de CPU", "fr": "Utilisation CPU"},
    "sysinfo_ram_usage": {"en": "RAM Usage", "it": "Uso RAM", "es": "Uso de RAM", "fr": "Utilisation RAM"},
    "sysinfo_disk_usage": {"en": "Disk Usage (/)", "it": "Uso disco (/)", "es": "Uso de Disco (/)", "fr": "Utilisation Disque (/)"},
    "sysinfo_partition": {"en": "Partition", "it": "Partizione", "es": "Partición", "fr": "Partition"},
    "sysinfo_mounted":   {"en": "Mounted on", "it": "Montata su", "es": "Montada en", "fr": "Montée sur"},
    "sysinfo_free":      {"en": "free", "it": "liberi", "es": "libres", "fr": "libres"},

    # ─── Plain-language partition hints ────────────────────────────
    "hint_zram":        {"en": "💡 Compressed RAM used as extra memory — not a real disk.", "it": "💡 RAM compressa usata come memoria extra — non è un disco reale.", "es": "💡 RAM comprimida usada como memoria extra — no es un disco real.", "fr": "💡 RAM compressée utilisée comme mémoire supplémentaire — pas un vrai disque."},
    "hint_swap":        {"en": "💡 Swap area: virtual memory on disk, used when RAM is full.", "it": "💡 Area di swap: memoria virtuale su disco, usata quando la RAM è piena.", "es": "💡 Área de intercambio: memoria virtual en disco, usada cuando la RAM está llena.", "fr": "💡 Zone d'échange : mémoire virtuelle sur disque, utilisée quand la RAM est pleine."},
    "hint_efi":         {"en": "💡 EFI boot partition — small system files needed to start the PC.", "it": "💡 Partizione di avvio EFI — piccoli file di sistema necessari per accendere il PC.", "es": "💡 Partición de arranque EFI — archivos de sistema necesarios para iniciar el PC.", "fr": "💡 Partition de démarrage EFI — petits fichiers système nécessaires pour démarrer le PC."},
    "hint_win_recovery": {"en": "💡 Likely the Windows Recovery partition (repair tools).", "it": "💡 Probabilmente la partizione di ripristino di Windows (strumenti di riparazione).", "es": "💡 Probablemente la partición de recuperación de Windows (herramientas de reparación).", "fr": "💡 Probablement la partition de récupération Windows (outils de réparation)."},
    "hint_windows":     {"en": "💡 A Windows partition — likely your Windows system or files.", "it": "💡 Una partizione Windows — probabilmente il tuo sistema o i tuoi file Windows.", "es": "💡 Una partición de Windows — probablemente tu sistema o archivos de Windows.", "fr": "💡 Une partition Windows — probablement votre système ou vos fichiers Windows."},
    "hint_linux_root":  {"en": "💡 The main Linux partition — where the operating system lives.", "it": "💡 La partizione principale di Linux — dove vive il sistema operativo.", "es": "💡 La partición principal de Linux — donde vive el sistema operativo.", "fr": "💡 La partition principale de Linux — où vit le système d'exploitation."},
    "hint_linux_home":  {"en": "💡 Your personal files (documents, downloads, desktop...).", "it": "💡 I tuoi file personali (documenti, download, scrivania...).", "es": "💡 Tus archivos personales (documentos, descargas, escritorio...).", "fr": "💡 Vos fichiers personnels (documents, téléchargements, bureau...)."},
    "hint_linux_boot":  {"en": "💡 Linux boot files (kernel and startup files).", "it": "💡 File di avvio di Linux (kernel e file di startup).", "es": "💡 Archivos de arranque de Linux (kernel y archivos de inicio).", "fr": "💡 Fichiers de démarrage Linux (noyau et fichiers de démarrage)."},
    "hint_linux_other": {"en": "💡 A Linux data partition.", "it": "💡 Una partizione dati Linux.", "es": "💡 Una partición de datos Linux.", "fr": "💡 Une partition de données Linux."},
    "hint_data":        {"en": "💡 A mounted data partition.", "it": "💡 Una partizione dati montata.", "es": "💡 Una partición de datos montada.", "fr": "💡 Une partition de données montée."},
    "hint_unknown":     {"en": "💡 Not mounted — exact type unknown without mounting it.", "it": "💡 Non montata — tipo esatto sconosciuto senza montarla.", "es": "💡 No montada — tipo exacto desconocido sin montarla.", "fr": "💡 Non montée — type exact inconnu sans la monter."},
    "hint_msr":         {"en": "💡 Microsoft Reserved partition — used internally by Windows.", "it": "💡 Partizione riservata Microsoft — usata internamente da Windows.", "es": "💡 Partición reservada de Microsoft — uso interno de Windows.", "fr": "💡 Partition réservée Microsoft — utilisée en interne par Windows."},
    "hint_removable":   {"en": "🔌 External / removable drive (USB or similar).", "it": "🔌 Disco esterno / rimovibile (USB o simile).", "es": "🔌 Disco externo / extraíble (USB o similar).", "fr": "🔌 Disque externe / amovible (USB ou similaire)."},
}

# Register info strings with the i18n module
from core import i18n as _i18n_mod
for k, v in _info_strings.items():
    _i18n_mod._strings[k] = v


def _make_info_row(label_key: str, value: str) -> Gtk.Box:
    """Create a horizontal info row: label → value."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    box.set_margin_top(4)
    box.set_margin_bottom(4)
    box.set_margin_start(4)

    lbl = Gtk.Label(label=T(label_key), xalign=0, hexpand=False)
    lbl.add_css_class("sysinfo-label")
    lbl.set_size_request(130, -1)
    on_change(lambda: lbl.set_text(T(label_key)))

    val = Gtk.Label(label=value, xalign=0, hexpand=True, selectable=True, wrap=True)
    val.add_css_class("sysinfo-value")

    box.append(lbl)
    box.append(val)
    return box


def _make_tile(icon: str, label_key: str, value_text: str, sub_text: str = "",
               fraction: float = None, bar_css: str = "sysinfo-bar-cpu") -> Gtk.Box:
    """A compact dashboard stat tile: icon + label, big value, optional bar."""
    tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
    tile.add_css_class("sysinfo-tile")
    tile.set_size_request(160, -1)

    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    icon_lbl = Gtk.Label(label=icon)
    icon_lbl.add_css_class("sysinfo-tile-icon")
    lbl = Gtk.Label(label=T(label_key), xalign=0)
    lbl.add_css_class("sysinfo-label")
    on_change(lambda: lbl.set_text(T(label_key)))
    head.append(icon_lbl)
    head.append(lbl)
    tile.append(head)

    val = Gtk.Label(label=value_text, xalign=0)
    val.add_css_class("sysinfo-value-large")
    tile.append(val)

    if sub_text:
        sub = Gtk.Label(label=sub_text, xalign=0, wrap=True)
        sub.add_css_class("sysinfo-value-sub")
        tile.append(sub)

    if fraction is not None:
        bar = Gtk.ProgressBar()
        bar.set_fraction(min(max(fraction, 0.0), 1.0))
        bar.add_css_class("sysinfo-bar")
        bar.add_css_class(bar_css)
        bar.set_margin_top(2)
        tile.append(bar)

    return tile


class InfoPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("go-home-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        distro = _get_distro()
        hostname = _get_hostname()
        kernel = _get_kernel()
        cpu_name = _get_cpu_name()
        phys, logical = _get_cpu_cores()
        gpu = _get_gpu_name()
        total_gb, used_gb, ram_pct = _get_ram_info()
        swap_total, swap_used = _get_swap_info()
        cpu_pct = _get_cpu_usage()
        root_total, root_used, root_pct = _get_mount_usage("/")

        # ── Hero banner ─────────────────────────────────────────
        g0 = Adw.PreferencesGroup()
        self.add(g0)

        hero_row = Adw.ActionRow()
        hero_row.set_activatable(False)
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hero.add_css_class("sysinfo-hero")

        hero_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hero_icon = Gtk.Label(label="🐧")
        hero_icon.add_css_class("sysinfo-hero-title")
        hero_title = Gtk.Label(label=f"{hostname}", xalign=0)
        hero_title.add_css_class("sysinfo-hero-title")
        hero_top.append(hero_icon)
        hero_top.append(hero_title)
        hero.append(hero_top)

        self._hero_sub = Gtk.Label(
            label=f"{distro}  •  {T('sysinfo_kernel')} {kernel}  •  {T('sysinfo_uptime')}: {_get_uptime()}",
            xalign=0, wrap=True
        )
        self._hero_sub.add_css_class("sysinfo-hero-subtitle")
        hero.append(self._hero_sub)
        on_change(self._refresh_hero_sub)

        hero_row.set_child(hero)
        g0.add(hero_row)

        # ── Overview tiles ────────────────────────────────────────
        g1 = make_group_title("sysinfo_overview")
        self.add(g1)

        tiles_row = Adw.ActionRow()
        tiles_row.set_activatable(False)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(1)
        flow.set_column_spacing(10)
        flow.set_row_spacing(10)
        flow.set_homogeneous(True)
        flow.set_margin_top(6)
        flow.set_margin_bottom(6)

        flow.insert(_make_tile("⚙️", "sysinfo_cpu_usage", f"{cpu_pct}%",
                                f"{phys} core / {logical} thread", cpu_pct / 100, "sysinfo-bar-cpu"), -1)
        flow.insert(_make_tile("🧠", "sysinfo_ram_usage", f"{ram_pct}%",
                                f"{used_gb} / {total_gb} GB", ram_pct / 100, "sysinfo-bar-ram"), -1)
        if root_total > 0:
            flow.insert(_make_tile("💽", "sysinfo_disk_usage", f"{root_pct}%",
                                    f"{root_used} / {root_total} GB", root_pct / 100, "sysinfo-bar-disk"), -1)
        if swap_total > 0:
            swap_pct = round(swap_used / swap_total * 100, 1) if swap_total else 0
            flow.insert(_make_tile("🔄", "sysinfo_swap", f"{swap_used} GB",
                                    f"{T('sysinfo_swap')}: {swap_total} GB", swap_pct / 100, "sysinfo-bar-cpu"), -1)

        tiles_row.set_child(flow)
        g1.add(tiles_row)

        # ── Hardware details ─────────────────────────────────────
        g2 = make_group_title("sysinfo_hw")
        self.add(g2)

        hw_row = Adw.ActionRow()
        hw_row.set_activatable(False)
        hw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        hw_box.set_margin_top(8)
        hw_box.set_margin_bottom(8)

        hw_box.append(_make_info_row("sysinfo_cpu", cpu_name))
        hw_box.append(_make_info_row("sysinfo_cores", f"{phys} physical, {logical} logical"))
        hw_box.append(_make_info_row("sysinfo_gpu", gpu))

        hw_row.set_child(hw_box)
        g2.add(hw_row)

        # ── OS ────────────────────────────────────────────────────
        g3 = make_group_title("sysinfo_os")
        self.add(g3)

        os_row = Adw.ActionRow()
        os_row.set_activatable(False)
        os_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        os_box.set_margin_top(8)
        os_box.set_margin_bottom(8)

        os_box.append(_make_info_row("sysinfo_distro", distro))
        os_box.append(_make_info_row("sysinfo_kernel", kernel))
        os_box.append(_make_info_row("sysinfo_host", hostname))
        os_box.append(_make_info_row("sysinfo_uptime", _get_uptime()))

        os_row.set_child(os_box)
        g3.add(os_row)

        # ── Storage ───────────────────────────────────────────────
        g4 = make_group_title("sysinfo_storage")
        self.add(g4)

        disks = _get_disks()
        swap_devices = _get_active_swap_devices()
        if disks:
            disk_row = Adw.ActionRow()
            disk_row.set_activatable(False)
            disk_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            disk_box.set_margin_top(8)
            disk_box.set_margin_bottom(8)

            for name, size_gb, mount, fstype, removable in disks:
                entry = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

                head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                icon = Gtk.Label(label="🔌" if removable else "💽")
                title = f"/dev/{name}  —  {size_gb} GB"
                if mount != "—":
                    title += f"  →  {mount}  ({fstype})"
                val = Gtk.Label(label=title, xalign=0, wrap=True, selectable=True, hexpand=True)
                val.add_css_class("sysinfo-value")
                head.append(icon)
                head.append(val)
                entry.append(head)

                if mount != "—":
                    p_total, p_used, p_pct = _get_mount_usage(mount)
                    if p_total > 0:
                        bar = Gtk.ProgressBar()
                        bar.set_fraction(min(p_pct / 100, 1.0))
                        bar.add_css_class("sysinfo-bar")
                        bar.add_css_class("sysinfo-bar-disk")
                        bar.set_margin_start(28)
                        entry.append(bar)
                        sub = Gtk.Label(
                            label=f"{p_used} GB / {p_total} GB  ({p_pct}%)  —  {round(p_total - p_used, 1)} GB {T('sysinfo_free')}",
                            xalign=0
                        )
                        sub.add_css_class("sysinfo-value-sub")
                        sub.set_margin_start(28)
                        entry.append(sub)

                hint = _guess_partition_hint(name, fstype, mount, size_gb, swap_devices)
                if hint:
                    hint_lbl = Gtk.Label(label=hint, xalign=0, wrap=True)
                    hint_lbl.add_css_class("sysinfo-value-sub")
                    hint_lbl.set_margin_start(28)
                    entry.append(hint_lbl)

                if removable:
                    rem_lbl = Gtk.Label(label=T("hint_removable"), xalign=0, wrap=True)
                    rem_lbl.add_css_class("sysinfo-value-sub")
                    rem_lbl.set_margin_start(28)
                    entry.append(rem_lbl)

                disk_box.append(entry)

            disk_row.set_child(disk_box)
            g4.add(disk_row)

    def _refresh_hero_sub(self):
        self._hero_sub.set_text(
            f"{_get_distro()}  •  {T('sysinfo_kernel')} {_get_kernel()}  •  {T('sysinfo_uptime')}: {_get_uptime()}"
        )

    def _refresh_title(self):
        self.set_title(T("sysinfo_tab"))


def make_group_title(title_key: str) -> Adw.PreferencesGroup:
    grp = Adw.PreferencesGroup()
    grp.set_title(T(title_key))
    on_change(lambda: grp.set_title(T(title_key)))
    return grp
