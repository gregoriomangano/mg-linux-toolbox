"""
Complete backend module — all features from the PDF.
Kernel-first: every feature operates via sysfs/procfs/modprobe where possible.
Fallback to userspace tools when unavoidable, with dep-check helpers.
"""
from core.executor import command_exists, run_command, run_command_full, run_pkexec, run_pkexec_full, INSTALL_TIMEOUT
from core.distro import distro
from core.kernel_features.base import OpResult
from core import gpu_vendor
import os


# ─── Universal Helper Functions ───────────────────────────────────────────────
def _cmd_exists(cmd: str) -> bool:
    return command_exists(cmd)

def _module_loaded(name: str) -> bool:
    """Check if a kernel module is currently loaded."""
    ok, out, _ = run_command(["lsmod"])
    return ok and name in out

def _module_exists(name: str) -> bool:
    """Check if a kernel module exists (even if not loaded)."""
    ok, _, _ = run_command(["modinfo", name])
    return ok

def _path_exists(path: str) -> bool:
    """Check if a sysfs/procfs path exists (kernel feature available)."""
    return os.path.exists(path)

def _service_exists(name: str) -> bool:
    """Check if a systemd service unit file is known (installed)."""
    ok, out, _ = run_command(["systemctl", "list-unit-files", f"{name}.service"])
    return ok and name in out

def _install_pkg(packages: dict, job=None):
    """
    Install a package using the appropriate package manager for this
    distro. Returns a CommandResult (truthy on success) rather than a
    plain bool, so the UI can show real diagnostics ("Mostra dettagli")
    instead of a bare pass/fail — and uses INSTALL_TIMEOUT (not the
    default 10s), because a real install can legitimately take a while
    (dependency resolution, download, or — as with power-profiles-daemon
    vs system76-power — a systemd Conflicts= handoff with another unit).
    `job`, if given, lets the UI's Annulla button actually reach and kill
    this specific subprocess.
    """
    cmd = distro.install_cmd(packages)
    if not cmd:
        from core.executor import CommandResult
        return CommandResult(cmd or [], False, None, "", "", 0.0,
                              error="no package known for this distribution")
    return run_pkexec_full(cmd, timeout=INSTALL_TIMEOUT, job=job)


# ─── Helper ──────────────────────────────────────────────────────
def _service_active(name: str) -> bool:
    ok, out, _ = run_command(["systemctl", "is-active", name])
    return out == "active"


def _service_set(name: str, enabled: bool) -> OpResult:
    state = "start" if enabled else "stop"
    enable = "enable" if enabled else "disable"
    r1 = run_pkexec_full(["systemctl", state, name])
    r2 = run_pkexec_full(["systemctl", enable, name])
    active = _service_active(name)
    ok = active == enabled
    detail = "" if ok else "\n".join(r.technical_detail() for r in (r1, r2) if not r.ok)
    return OpResult(ok, value=active, technical_detail=detail)


def _service_enabled(name: str) -> bool:
    ok, out, _ = run_command(["systemctl", "is-enabled", name])
    return out == "enabled"


def service_start(name: str) -> OpResult:
    result = run_pkexec_full(["systemctl", "start", name])
    active = _service_active(name)
    return OpResult(active, value=active, technical_detail="" if active else result.technical_detail())


def service_stop(name: str) -> OpResult:
    result = run_pkexec_full(["systemctl", "stop", name])
    active = _service_active(name)
    ok = not active
    return OpResult(ok, value=active, technical_detail="" if ok else result.technical_detail())


def service_set_enabled(name: str, enabled: bool) -> OpResult:
    result = run_pkexec_full(["systemctl", "enable" if enabled else "disable", name])
    real = _service_enabled(name)
    ok = real == enabled
    return OpResult(ok, value=real, technical_detail="" if ok else result.technical_detail())


# Curated list of common services shown in the "Services" tab — deliberately
# short (per the original spec: "lista base con avvio/stop servizi critici"),
# not a dump of the 100+ units systemd always has. The unit name differs
# between distros for a few of these, hence the per-distro dict.
SERVICES = [
    ("networkmanager", {"default": "NetworkManager"}),
    ("cups_svc",       {"default": "cups"}),
    ("sshd_svc",       {"debian": "ssh", "arch": "sshd", "fedora": "sshd", "opensuse": "sshd", "default": "ssh"}),
    ("bluetooth_svc",  {"default": "bluetooth"}),
    ("cron_svc",       {"debian": "cron", "arch": "cronie", "fedora": "crond", "opensuse": "cron", "default": "cron"}),
    ("avahi_svc",      {"default": "avahi-daemon"}),
    ("timesync_svc",   {"default": "systemd-timesyncd"}),
]


def service_unit_name(names: dict) -> str:
    if distro.is_arch:
        return names.get("arch", names.get("default", ""))
    elif distro.is_fedora:
        return names.get("fedora", names.get("default", ""))
    elif distro.is_opensuse:
        return names.get("opensuse", names.get("default", ""))
    return names.get("debian", names.get("default", ""))


# ─── Network ─────────────────────────────────────────────────────
def wifi_active() -> bool:
    ok, out, _ = run_command(["nmcli", "radio", "wifi"])
    return "enabled" in out

def wifi_set(on: bool) -> OpResult:
    result = run_command_full(["nmcli", "radio", "wifi", "on" if on else "off"])
    active = wifi_active()
    ok = active == on
    return OpResult(ok, value=active, technical_detail="" if ok else result.technical_detail())

def hotspot_active() -> bool:
    """NetworkManager-based (nmcli) — identical on Debian/Arch/Fedora/openSUSE."""
    ok, out, _ = run_command(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"])
    if not ok:
        return False
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 1 and parts[0] == "Hotspot":
            return True
    return False

def hotspot_start(ssid: str, password: str) -> bool:
    if not ssid:
        return False
    args = ["nmcli", "device", "wifi", "hotspot", "con-name", "Hotspot", "ssid", ssid]
    if password:
        args += ["password", password]
    ok, _, _ = run_command(args)
    return ok and hotspot_active()

def hotspot_stop() -> bool:
    run_command(["nmcli", "connection", "down", "Hotspot"])
    return not hotspot_active()

def bluetooth_active() -> bool:
    return _service_active("bluetooth")

def bluetooth_set(on: bool) -> OpResult:
    mask = "unmask" if on else "mask"
    results = [
        run_pkexec_full(["systemctl", mask, "bluetooth"]),
        run_pkexec_full(["systemctl", "start" if on else "stop", "bluetooth"]),
        run_pkexec_full(["rfkill", "unblock" if on else "block", "bluetooth"]),
    ]
    active = bluetooth_active()
    ok = active == on
    detail = "" if ok else "\n".join(r.technical_detail() for r in results if not r.ok)
    return OpResult(ok, value=active, technical_detail=detail)

def bluetooth_scan(timeout: int = 6) -> list:
    """
    Returns [(mac, name), ...] discovered nearby. Pure BlueZ (bluetoothctl),
    identical on every distro — no root needed, same as pairing a device
    from any desktop's own Bluetooth settings.
    """
    run_command(["bluetoothctl", "power", "on"])
    run_command(["bluetoothctl", "--timeout", str(timeout), "scan", "on"])
    ok, out, _ = run_command(["bluetoothctl", "devices"])
    devices = []
    if ok:
        for line in out.splitlines():
            parts = line.split(" ", 2)
            if len(parts) == 3 and parts[0] == "Device":
                devices.append((parts[1], parts[2]))
    return devices

def bluetooth_paired_macs() -> set:
    ok, out, _ = run_command(["bluetoothctl", "paired-devices"])
    macs = set()
    if ok:
        for line in out.splitlines():
            parts = line.split(" ", 2)
            if len(parts) >= 2 and parts[0] == "Device":
                macs.add(parts[1])
    return macs

def bluetooth_pair(mac: str) -> bool:
    run_command(["bluetoothctl", "pair", mac])
    run_command(["bluetoothctl", "trust", mac])
    ok, _, _ = run_command(["bluetoothctl", "connect", mac])
    return ok

IPV6_DISABLE_PATH = "/proc/sys/net/ipv6/conf/all/disable_ipv6"


def ipv6_disabled() -> bool:
    """Reads /proc directly rather than shelling out to `sysctl` — on
    openSUSE (and any distro that keeps /usr/sbin out of a regular
    user's $PATH) the bare `sysctl` call silently failed with "command
    not found", which made this always return False and left the IPv6
    switch showing "enabled" even when it was actually disabled."""
    try:
        with open(IPV6_DISABLE_PATH) as f:
            return f.read().strip() == "1"
    except OSError:
        return False

def ipv6_set_disabled(disabled: bool) -> OpResult:
    val = "1" if disabled else "0"
    results = [
        run_pkexec_full(["sysctl", "-w", f"net.ipv6.conf.all.disable_ipv6={val}"]),
        run_pkexec_full(["sysctl", "-w", f"net.ipv6.conf.default.disable_ipv6={val}"]),
    ]
    active = ipv6_disabled()
    ok = active == disabled
    detail = "" if ok else "\n".join(r.technical_detail() for r in results if not r.ok)
    return OpResult(ok, value=active, technical_detail=detail)

def firewall_state() -> str:
    """One of firewall_detect.STATE_* — see that module for why this
    can no longer just shell out to `ufw status` (needs root, so it
    used to fail silently as a normal user and get misreported as
    'not installed')."""
    from core.firewall_detect import detect_firewall
    return detect_firewall().state

def firewall_active() -> bool:
    from core.firewall_detect import STATE_UFW_ACTIVE, STATE_FIREWALLD_ACTIVE, STATE_NFTABLES_RULES
    return firewall_state() in (STATE_UFW_ACTIVE, STATE_FIREWALLD_ACTIVE, STATE_NFTABLES_RULES)

def firewall_set(on: bool) -> OpResult:
    # firewalld is the default on Fedora AND openSUSE; ufw elsewhere
    # (Debian/Ubuntu/Arch, where it's the common choice).
    if distro.is_fedora or distro.is_opensuse:
        results = [
            run_pkexec_full(["systemctl", "start" if on else "stop", "firewalld"]),
            run_pkexec_full(["systemctl", "enable" if on else "disable", "firewalld"]),
        ]
    else:
        results = [run_pkexec_full(["ufw", "--force", "enable" if on else "disable"])]
    active = firewall_active()
    ok = active == on
    detail = "" if ok else "\n".join(r.technical_detail() for r in results if not r.ok)
    return OpResult(ok, value=active, technical_detail=detail)

def ssh_active() -> bool:
    # service name differs: ssh (debian) vs sshd (arch/fedora)
    name = "sshd" if (distro.is_arch or distro.is_fedora or distro.is_opensuse) else "ssh"
    return _service_active(name)

def ssh_set(on: bool) -> bool:
    name = "sshd" if (distro.is_arch or distro.is_fedora or distro.is_opensuse) else "ssh"
    return _service_set(name, on)

def samba_active() -> bool:
    name = "smb" if (distro.is_arch or distro.is_fedora or distro.is_opensuse) else "smbd"
    return _service_active(name)

def samba_set(on: bool) -> bool:
    name = "smb" if (distro.is_arch or distro.is_fedora or distro.is_opensuse) else "smbd"
    return _service_set(name, on)

def dns_dot_active() -> bool:
    try:
        with open("/etc/systemd/resolved.conf") as f:
            return "DNSOverTLS=yes" in f.read()
    except Exception:
        return False

def dns_dot_set(on: bool) -> OpResult:
    # Through the privileged helper (feature dns.dot) — fixed file, one
    # directive, backup + atomic write + verification, service restarted
    # root-side. Never `pkexec python3 -c <script>` again. The helper's
    # own OpResult (friendly_message already distinguishes "component
    # missing" from a plain write failure) is propagated as-is, not
    # discarded — real state is always re-read regardless of ok/failed.
    from core.persistence.priv_client import default_privileged_writer
    result = default_privileged_writer().execute("dns.dot", "apply_temporary", on)
    active = dns_dot_active()
    ok = result.ok and active == on
    return OpResult(ok, value=active, friendly_message="" if ok else (result.friendly_message or "kf_err_generic"),
                    technical_detail="" if ok else result.technical_detail)


# ─── Disk ────────────────────────────────────────────────────────
def trim_active() -> bool:
    return _service_active("fstrim.timer")

def trim_set(on: bool) -> bool:
    return _service_set("fstrim.timer", on)

def smart_active() -> bool:
    return _service_active("smartd")

def smart_installed() -> bool:
    pkg = {"debian": "smartmontools", "arch": "smartmontools", "fedora": "smartmontools",
           "opensuse": "smartmontools", "default": "smartmontools"}
    return distro.is_installed(pkg)

def smart_set(on: bool) -> bool:
    pkg = {"debian": "smartmontools", "arch": "smartmontools", "fedora": "smartmontools",
           "opensuse": "smartmontools", "default": "smartmontools"}
    if on and not distro.is_installed(pkg):
        _install_pkg(pkg)
    return _service_set("smartd", on)

def samba_installed() -> bool:
    pkg = {"debian": "samba", "arch": "samba", "fedora": "samba", "opensuse": "samba", "default": "samba"}
    return distro.is_installed(pkg)


def _dir_size_bytes(path: str) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except Exception:
        pass
    return total


def _human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def cache_dir() -> str:
    """Package-cache directory for this distro's package manager."""
    if distro.is_arch:
        return "/var/cache/pacman/pkg"
    elif distro.is_fedora:
        return "/var/cache/dnf"
    elif distro.is_opensuse:
        return "/var/cache/zypp/packages"
    return "/var/cache/apt/archives"


def cache_size_human() -> str:
    return _human_size(_dir_size_bytes(cache_dir()))


def clean_cache() -> bool:
    """
    Clears the package-manager cache and removes orphaned/unneeded
    packages, using each distro's own official tool — never a raw `rm`,
    so it can't accidentally take anything else with it.
    """
    if distro.is_arch:
        run_pkexec(["pacman", "-Sc", "--noconfirm"])
    elif distro.is_fedora:
        run_pkexec(["dnf", "clean", "all"])
        run_pkexec(["dnf", "autoremove", "-y"])
    elif distro.is_opensuse:
        run_pkexec(["zypper", "clean", "--all"])
    else:
        run_pkexec(["apt-get", "clean"])
        run_pkexec(["apt-get", "autoremove", "-y"])
    return True


# ─── Performance ─────────────────────────────────────────────────
SWAPS_PATH = "/proc/swaps"


def zram_active() -> bool:
    """Reads /proc/swaps directly rather than shelling out to `swapon`
    — same PATH issue and same fix as ipv6_disabled() above. Mirrors
    core.priv_writer's own zram detection (_active_devices())."""
    try:
        with open(SWAPS_PATH) as f:
            lines = f.read().splitlines()
    except OSError:
        return False
    return any("zram" in line.split()[0] for line in lines[1:] if line.split())

# ZRAM enable/disable, Turbo Boost, THP and CPU Governor now live in
# core/kernel_features/ (cpu.py, memory.py) + core/priv_writer.py, as
# real KernelFeature rows in "Funzioni Kernel" with Prova/Ripristina and
# an operations log — replacing the plain toggles that used to live here
# with no rollback and no real post-change verification.

def get_power_profile() -> str:
    ok, out, _ = run_command(["powerprofilesctl", "get"])
    return out if ok else "balanced"

def set_power_profile(profile: str):
    run_pkexec(["powerprofilesctl", "set", profile])


# system76-power control — Pop!_OS's own power-profile manager, and the
# reason we must never blindly propose installing power-profiles-daemon:
# the two conflict (power-profiles-daemon.service declares
# `Conflicts=system76-power.service`). Only relied on when
# core.power_providers reports it as the active provider.
# NOTE: the CLI shape below (`system76-power profile [get|<name>]`) is the
# long-stable documented interface, but this specific code path has not
# been exercised against a real running system76-power instance (not
# installed on the machine this was written on) — treat as unverified
# until confirmed on real System76/Pop!_OS hardware with it active.
SYSTEM76_POWER_PROFILES = ["battery", "balanced", "performance"]

def system76_power_installed() -> bool:
    return _cmd_exists("system76-power")

def get_system76_power_profile() -> str:
    ok, out, _ = run_command(["system76-power", "profile"])
    if not ok:
        return ""
    return out.strip().lower()

def set_system76_power_profile(name: str) -> bool:
    ok, _, _ = run_pkexec(["system76-power", "profile", name])
    return ok


# ─── Gaming ──────────────────────────────────────────────────────
def gamemode_installed() -> bool:
    return distro.is_installed({"default": "gamemode"})

def gamemode_install(job=None):
    # Real package installs go through _install_pkg (INSTALL_TIMEOUT=180s)
    # rather than the plain run_pkexec default of 10s — a real dependency
    # resolution + download routinely takes longer than that, and the old
    # 10s timeout used to SIGKILL the whole apt/dnf/pacman process group
    # mid-transaction, potentially leaving the package manager needing a
    # manual repair.
    pkg = {"debian": "gamemode", "arch": "gamemode", "fedora": "gamemode", "default": "gamemode"}
    _install_pkg(pkg, job=job)
    return gamemode_installed()

def mangohud_installed() -> bool:
    return distro.is_installed({"default": "mangohud"})

def mangohud_install(job=None):
    pkg = {"debian": "mangohud", "arch": "mangohud", "fedora": "mangohud", "default": "mangohud"}
    _install_pkg(pkg, job=job)
    return mangohud_installed()

# openSUSE 32-bit Vulkan ICD (Installable Client Driver) package per GPU
# vendor — confirmed real, present in the default "Repository principale
# (OSS)" via `zypper search -s`/`zypper info` on a real Tumbleweed
# machine. The loader (libvulkan1-32bit) alone does nothing without one
# of these; there is deliberately no entry for NVIDIA_PROPRIETARY or
# UNKNOWN — that package lives in a repository this app never adds on
# its own, and guessing a GPU package is exactly what must not happen.
_OPENSUSE_VULKAN_ICD_32BIT = {
    gpu_vendor.AMD: "libvulkan_radeon-32bit",
    gpu_vendor.INTEL: "libvulkan_intel-32bit",
    gpu_vendor.NOUVEAU: "libvulkan_nouveau-32bit",
}


def lib32_blocked_reason() -> str:
    """Empty string if a real, non-guessed 32-bit Vulkan ICD is known
    for this machine's GPU; otherwise an i18n key explaining why lib32
    install must not proceed. Only meaningful on openSUSE — the other
    three families install a single, vendor-neutral loader package and
    rely on whatever 64-bit ICD is already in use."""
    if not distro.is_opensuse:
        return ""
    vendor = gpu_vendor.detect_gpu_vendor()
    if vendor == gpu_vendor.NVIDIA_PROPRIETARY:
        return "lib32_blocked_nvidia_proprietary"
    if vendor == gpu_vendor.UNKNOWN:
        return "lib32_blocked_unknown_gpu"
    return ""


def lib32_installed() -> bool:
    if distro.is_debian:
        ok, out, _ = run_command(["dpkg", "-l", "libgl1-mesa-dri:i386"])
        return ok and "ii" in out
    elif distro.is_arch:
        ok, _, _ = run_command(["pacman", "-Qi", "lib32-mesa"])
        return ok
    elif distro.is_fedora:
        # Fedora ships 32-bit mesa/vulkan as .i686 RPMs (no separate
        # "multiarch enable" step needed, unlike Debian) — this used to
        # always return False here, so the row never showed "Installed"
        # even right after a successful install.
        ok, _, _ = run_command(["rpm", "-q", "mesa-libGL.i686"])
        return ok
    elif distro.is_opensuse:
        # openSUSE ships 32-bit/biarch RPMs with a "-32bit" suffix from the
        # same main OSS repository as the 64-bit packages (no separate
        # multiarch-enable step, unlike Debian). Verified for real on an
        # openSUSE Tumbleweed machine: `Mesa-libGL1-32bit` is the package
        # that actually exists (there is no "mesa-libGL1-32bit" or
        # ".i686" variant on this family). The real Vulkan ICD for this
        # GPU (not just the loader) is also required when the vendor is
        # known — checking libvulkan1-32bit alone used to report
        # "installed" even with zero working 32-bit Vulkan drivers.
        packages = ["Mesa-libGL1-32bit", "libvulkan1-32bit"]
        icd = _OPENSUSE_VULKAN_ICD_32BIT.get(gpu_vendor.detect_gpu_vendor())
        if icd:
            packages.append(icd)
        ok, _, _ = run_command(["rpm", "-q", *packages])
        return ok
    return False

def lib32_install(job=None):
    # run_pkexec_full(..., timeout=INSTALL_TIMEOUT, ...) here, not the
    # bare run_pkexec (10s default) — same reasoning as gamemode_install
    # above: these are real multi-package installs (plus an apt update),
    # not a quick sysctl/systemctl toggle.
    if distro.is_debian:
        run_pkexec_full(["dpkg", "--add-architecture", "i386"], timeout=INSTALL_TIMEOUT, job=job)
        run_pkexec_full(["apt-get", "update"], timeout=INSTALL_TIMEOUT, job=job)
        run_pkexec_full(["apt-get", "install", "-y", "libgl1-mesa-dri:i386", "libvulkan1:i386"], timeout=INSTALL_TIMEOUT, job=job)
    elif distro.is_arch:
        run_pkexec_full(["pacman", "-S", "--noconfirm", "lib32-mesa", "lib32-vulkan-icd-loader"], timeout=INSTALL_TIMEOUT, job=job)
    elif distro.is_fedora:
        run_pkexec_full(["dnf", "install", "-y", "mesa-libGL.i686", "vulkan-loader.i686"], timeout=INSTALL_TIMEOUT, job=job)
    elif distro.is_opensuse:
        # Defense in depth: the UI already hides/gates this button when
        # lib32_blocked_reason() is non-empty, but this function must
        # never install a guessed GPU package even if reached some other
        # way (DepBanner, a future caller, ...).
        reason = lib32_blocked_reason()
        if reason:
            from core.executor import CommandResult
            return CommandResult([], False, None, "", "", 0.0, error=reason)
        icd = _OPENSUSE_VULKAN_ICD_32BIT[gpu_vendor.detect_gpu_vendor()]
        # Package names confirmed against a real openSUSE Tumbleweed
        # machine (`zypper search -s`/`zypper info`): all three exist in
        # the default, already-enabled "Repository principale (OSS)" —
        # no Packman/extra repo needed for this part of lib32.
        result = run_pkexec_full(["zypper", "--non-interactive", "install",
                                   "Mesa-libGL1-32bit", "libvulkan1-32bit", icd], timeout=INSTALL_TIMEOUT, job=job)
        if not result.ok:
            # Real exit code / stdout / stderr (e.g. "System management
            # is locked", a network error, ...) — previously discarded
            # here same as the old docker_install() systemctl bug, so a
            # real Zypper failure was indistinguishable from "not
            # installed yet" with no diagnostic at all.
            return result
    return lib32_installed()

def lib32_supported() -> bool:
    return distro.is_debian or distro.is_arch or distro.is_fedora or distro.is_opensuse

def vulkan_installed() -> bool:
    ok, out, _ = run_command(["vulkaninfo", "--summary"])
    return ok

def vulkan_install(job=None):
    if distro.is_debian:
        run_pkexec_full(["apt-get", "install", "-y", "libvulkan1", "mesa-vulkan-drivers", "vulkan-tools"], timeout=INSTALL_TIMEOUT, job=job)
    elif distro.is_arch:
        run_pkexec_full(["pacman", "-S", "--noconfirm", "vulkan-icd-loader", "vulkan-tools"], timeout=INSTALL_TIMEOUT, job=job)
    elif distro.is_fedora:
        run_pkexec_full(["dnf", "install", "-y", "vulkan-loader", "vulkan-tools"], timeout=INSTALL_TIMEOUT, job=job)
    elif distro.is_opensuse:
        # NOTE: package names are the openSUSE-documented ones but this
        # path is unverified on a real openSUSE machine.
        run_pkexec_full(["zypper", "--non-interactive", "install", "libvulkan1", "vulkan-tools"], timeout=INSTALL_TIMEOUT, job=job)
    return vulkan_installed()


# ─── Audio ───────────────────────────────────────────────────────
def pipewire_active() -> bool:
    ok, out, _ = run_command(["systemctl", "--user", "is-active", "pipewire"])
    return out == "active"

def easyeffects_installed() -> bool:
    return _cmd_exists("easyeffects")

def easyeffects_install(job=None):
    # 2026-08-05: was missing an openSUSE branch entirely — on that
    # family this used to silently do nothing at all (no command ever
    # ran) and still report "installation failed" afterwards. Routed
    # through distro.install_cmd() like every other single-package
    # install, so a new family only ever needs adding once, centrally.
    _install_pkg({"debian": "easyeffects", "arch": "easyeffects",
                  "fedora": "easyeffects", "opensuse": "easyeffects"}, job=job)
    return easyeffects_installed()


# ─── Virtualization ──────────────────────────────────────────────
def kvm_supported() -> str:
    """Returns 'intel', 'amd', or ''"""
    ok, out, _ = run_command(["grep", "-E", "(vmx|svm)", "/proc/cpuinfo"])
    if not ok or not out:
        return ""
    return "intel" if "vmx" in out else "amd"

def kvm_loaded() -> bool:
    ok, out, _ = run_command(["lsmod"])
    return "kvm_" in out

def kvm_load():
    cpu = kvm_supported()
    if cpu == "intel":
        run_pkexec(["modprobe", "kvm_intel"])
    elif cpu == "amd":
        run_pkexec(["modprobe", "kvm_amd"])
    return kvm_loaded()

def nested_virt_active() -> bool:
    for path in ["/sys/module/kvm_intel/parameters/nested",
                 "/sys/module/kvm_amd/parameters/nested"]:
        try:
            with open(path) as f:
                return f.read().strip() in ("1", "Y")
        except FileNotFoundError:
            continue
    return False

def nested_virt_set(on: bool):
    # Through the privileged helper (feature virt.nested) — fixed sysfs
    # paths, value validated and re-read root-side; never `sh -c` again.
    from core.persistence.priv_client import default_privileged_writer
    default_privileged_writer().execute("virt.nested", "apply_temporary", on)
    return nested_virt_active()

def docker_installed() -> bool:
    return _cmd_exists("docker")

def docker_ready() -> bool:
    """Real final verification: the package being on disk doesn't mean
    the daemon actually came up (systemd unit name mismatch, a masked
    unit, a cgroup/driver conflict — all real ways `systemctl enable
    --now docker` can fail silently if its result is discarded)."""
    from core import container_engines as ce
    return ce.docker_status()["daemon_active"]

def docker_install(job=None):
    install_result = _install_pkg({"debian": "docker.io", "arch": "docker",
                                    "fedora": "docker", "opensuse": "docker"}, job=job)
    if not install_result.ok:
        return install_result
    # Real command, real exit code, real stdout/stderr — previously
    # discarded (bare run_pkexec call with no captured result), so a
    # failed `systemctl enable --now docker` (masked unit, cgroup
    # driver conflict, wrong unit name...) was indistinguishable from
    # success: the row only ever re-checked the docker *binary*.
    return run_pkexec_full(["systemctl", "enable", "--now", "docker"], timeout=INSTALL_TIMEOUT, job=job)

def podman_installed() -> bool:
    return _cmd_exists("podman")

def podman_install(job=None):
    pkg = {"default": "podman"}
    _install_pkg(pkg, job=job)
    return podman_installed()


# ─── Security ────────────────────────────────────────────────────
_ARCH_UPDATE_CHECK_SERVICE = """[Unit]
Description=M.G Linux Toolbox - controlla aggiornamenti pacman disponibili

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'command -v checkupdates >/dev/null 2>&1 || exit 0; N=$(checkupdates 2>/dev/null | wc -l); if [ "$N" -gt 0 ]; then notify-send "Aggiornamenti disponibili" "$N pacchetti da aggiornare (pacman -Syu)"; fi'
"""

_ARCH_UPDATE_CHECK_TIMER = """[Unit]
Description=M.G Linux Toolbox - timer controllo aggiornamenti pacman

[Timer]
OnBootSec=10min
OnUnitActiveSec=1d
Persistent=true

[Install]
WantedBy=timers.target
"""

_ARCH_UPDATE_UNIT_NAME = "mg-linux-toolbox-pacman-check"


def _write_user_unit(name: str, content: str) -> str:
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(unit_dir, exist_ok=True)
    path = os.path.join(unit_dir, name)
    with open(path, "w") as f:
        f.write(content)
    return path


def dnf5_active() -> bool:
    """True if this Fedora install uses DNF5 (the `dnf5` binary really
    responds), false for classic DNF4 — the automatic-update plugin
    package and unit name differ between the two."""
    return _cmd_exists("dnf5")


def _fedora_automatic_unit() -> str:
    return "dnf5-automatic.timer" if dnf5_active() else "dnf-automatic.timer"


def _fedora_automatic_package() -> dict:
    if dnf5_active():
        return {"fedora": "dnf5-plugin-automatic", "default": "dnf5-plugin-automatic"}
    return {"fedora": "dnf-automatic", "default": "dnf-automatic"}


def _opensuse_uses_transactional_update() -> bool:
    """transactional-update.timer is the real, full unattended-update
    mechanism (snapshot + update + reboot-ready) on openSUSE — used
    when the tool is actually present (MicroOS/Aeon, or manually
    installed on Tumbleweed/Leap). zypper-refresh.timer only refreshes
    repository metadata, it never installs anything, so it's kept only
    as the fallback when transactional-update genuinely isn't there."""
    return _cmd_exists("transactional-update")


def auto_updates_active() -> bool:
    """
    Whether automatic-update checking is enabled.
    NOTE: on Arch this only means "check for updates and notify" — Arch is
    a rolling release, and silently running `pacman -Syu` unattended is a
    well-known way to break a system while you're not looking. Debian,
    Fedora and openSUSE's own tools (unattended-upgrades / dnf(5)-automatic
    / transactional-update) really do install updates automatically; Arch
    intentionally does not, to match how the distro is meant to be used.
    """
    if distro.is_fedora:
        return _service_active(_fedora_automatic_unit())
    elif distro.is_arch:
        ok, out, _ = run_command(["systemctl", "--user", "is-active", f"{_ARCH_UPDATE_UNIT_NAME}.timer"])
        return out == "active"
    elif distro.is_opensuse:
        if _opensuse_uses_transactional_update():
            return _service_active("transactional-update.timer")
        return _service_active("zypper-refresh.timer")
    return _service_active("unattended-upgrades")


def auto_updates_dep_ok() -> bool:
    """Whether the right tool for this distro's auto-update mechanism is present."""
    if distro.is_fedora:
        return _cmd_exists("dnf5-automatic" if dnf5_active() else "dnf-automatic")
    elif distro.is_arch:
        return _cmd_exists("checkupdates")
    elif distro.is_opensuse:
        if _opensuse_uses_transactional_update():
            return True
        return _service_exists("zypper-refresh")  # ships with zypper itself
    return _cmd_exists("unattended-upgrade")


def auto_updates_dep_install(job=None):
    if distro.is_fedora:
        return _install_pkg(_fedora_automatic_package(), job=job)
    elif distro.is_arch:
        return _install_pkg({"arch": "pacman-contrib", "default": "pacman-contrib"}, job=job)
    elif distro.is_opensuse:
        if _opensuse_uses_transactional_update():
            return True  # transactional-update.timer ships with the package itself
        return True  # zypper-refresh.timer already ships with zypper
    return _install_pkg({"debian": "unattended-upgrades", "default": "unattended-upgrades"}, job=job)


def auto_updates_set(on: bool) -> bool:
    if distro.is_fedora:
        pkg = _fedora_automatic_package()
        unit = _fedora_automatic_unit()
        if on and not distro.is_installed(pkg):
            _install_pkg(pkg)
        return _service_set(unit, on)

    elif distro.is_arch:
        # Safe-by-design: only checks + desktop-notifies, never auto-applies.
        # Runs as a --user timer (no root needed for the timer itself, and
        # notify-send then reaches the user's own desktop session). Never
        # runs `pacman -Sy` on its own and never installs a package without
        # this same explicit toggle.
        pkg = {"arch": "pacman-contrib", "default": "pacman-contrib"}
        if on:
            if not distro.is_installed(pkg):
                _install_pkg(pkg)
            _write_user_unit(f"{_ARCH_UPDATE_UNIT_NAME}.service", _ARCH_UPDATE_CHECK_SERVICE)
            _write_user_unit(f"{_ARCH_UPDATE_UNIT_NAME}.timer", _ARCH_UPDATE_CHECK_TIMER)
            run_command(["systemctl", "--user", "daemon-reload"])
            run_command(["systemctl", "--user", "enable", "--now", f"{_ARCH_UPDATE_UNIT_NAME}.timer"])
        else:
            run_command(["systemctl", "--user", "disable", "--now", f"{_ARCH_UPDATE_UNIT_NAME}.timer"])
        return auto_updates_active()

    elif distro.is_opensuse:
        unit = "transactional-update.timer" if _opensuse_uses_transactional_update() else "zypper-refresh.timer"
        return _service_set(unit, on)

    else:
        pkg = {"debian": "unattended-upgrades", "default": "unattended-upgrades"}
        if on and not distro.is_installed(pkg):
            _install_pkg(pkg)
        return _service_set("unattended-upgrades", on)

def ssh_server_installed() -> bool:
    from core.ssh_detect import openssh_server_installed
    return openssh_server_installed()

def root_ssh_state() -> str:
    """One of core.ssh_detect.STATE_* — 'not_installed' and
    'undetermined' are both distinct from 'allowed', so the UI never
    has to guess at a config that doesn't exist or can't be read."""
    from core.ssh_detect import root_ssh_state as _state
    return _state()

def root_ssh_disabled() -> bool:
    from core.ssh_detect import STATE_DISABLED
    return root_ssh_state() == STATE_DISABLED

def root_ssh_set_disabled(disabled: bool) -> OpResult:
    # Through the privileged helper (feature security.root_ssh) — fixed
    # file, one directive, backup + atomic write + verification, service
    # reloaded root-side. Never `pkexec python3 -c <script>` again. The
    # helper's own OpResult is propagated, not discarded.
    from core.persistence.priv_client import default_privileged_writer
    result = default_privileged_writer().execute("security.root_ssh", "apply_temporary", disabled)
    active_disabled = root_ssh_disabled()
    ok = result.ok and active_disabled == disabled
    return OpResult(ok, value=active_disabled,
                    friendly_message="" if ok else (result.friendly_message or "kf_err_generic"),
                    technical_detail="" if ok else result.technical_detail)

def cups_active() -> bool:
    return _service_active("cups")

def cups_set(on: bool) -> bool:
    return _service_set("cups", on)


# ─── Printer drivers ──────────────────────────────────────────────
# Curated per the "most important / safe to show" subset for each distro
# family (rather than the full exhaustive package lists), to keep install
# commands reliable across versions.
PRINTER_DRIVER_SETS = {
    "printer_base": {
        "debian": ["cups", "cups-filters", "cups-browsed", "ipp-usb", "avahi-daemon", "system-config-printer", "ghostscript"],
        "arch": ["cups", "cups-filters", "cups-browsed", "ipp-usb", "avahi", "system-config-printer", "ghostscript"],
        "fedora": ["cups", "cups-filters", "cups-browsed", "ipp-usb", "avahi", "system-config-printer", "ghostscript"],
        "opensuse": ["cups", "cups-filters", "ipp-usb", "avahi", "system-config-printer", "ghostscript"],
    },
    "printer_universal": {
        "debian": ["printer-driver-all"],
        "arch": ["gutenprint", "foomatic-db-ppds"],
        "fedora": ["gutenprint-cups", "foomatic-db-ppds"],
        # "foomatic-filters" does not exist as a package on openSUSE
        # (verified with `zypper search`/`zypper info` on a real
        # Tumbleweed machine — it 404s outright). The real equivalent
        # "all manufacturer PPDs" package there is "manufacturer-PPDs".
        "opensuse": ["gutenprint", "manufacturer-PPDs"],
    },
    "printer_hp": {
        "debian": ["hplip", "hplip-gui"],
        "arch": ["hplip"],
        "fedora": ["hplip", "hplip-gui"],
        "opensuse": ["hplip"],
    },
}


def _pkg_list_for_distro(pkg_map: dict) -> list:
    if distro.is_arch:
        return pkg_map.get("arch", [])
    elif distro.is_fedora:
        return pkg_map.get("fedora", [])
    elif distro.is_opensuse:
        return pkg_map.get("opensuse", [])
    return pkg_map.get("debian", [])


def _install_pkg_set(pkg_map: dict, job=None):
    """Installs every package in the distro-appropriate list with a single
    password request, instead of one pkexec call per package. Returns a
    CommandResult (truthy on success) with real diagnostics, using
    INSTALL_TIMEOUT since multi-package installs are slower still."""
    pkgs = _pkg_list_for_distro(pkg_map)
    if not pkgs:
        from core.executor import CommandResult
        return CommandResult([], False, None, "", "", 0.0,
                              error="no packages known for this distribution")
    if distro.is_arch:
        cmd = ["pacman", "-S", "--noconfirm"] + pkgs
    elif distro.is_opensuse:
        cmd = ["zypper", "--non-interactive", "install"] + pkgs
    elif distro.is_fedora:
        cmd = ["dnf", "install", "-y"] + pkgs
    else:
        cmd = ["apt-get", "install", "-y"] + pkgs
    return run_pkexec_full(cmd, timeout=INSTALL_TIMEOUT, job=job)


def printer_set_installed(key: str) -> bool:
    """Best-effort check using the first (most representative) package
    in this distro's list for that set."""
    pkgs = _pkg_list_for_distro(PRINTER_DRIVER_SETS[key])
    if not pkgs:
        return False
    first = pkgs[0]
    if distro.is_arch:
        return distro.is_installed({"arch": first})
    elif distro.is_fedora:
        return distro.is_installed({"fedora": first})
    elif distro.is_opensuse:
        return distro.is_installed({"opensuse": first})
    return distro.is_installed({"debian": first})


def printer_set_install(key: str, job=None):
    return _install_pkg_set(PRINTER_DRIVER_SETS[key], job=job)

def apparmor_active() -> bool:
    ok, out, _ = run_command(["aa-status", "--enabled"])
    return ok

def selinux_enforcing() -> bool:
    ok, out, _ = run_command(["getenforce"])
    return out == "Enforcing"

def secureboot_active() -> bool:
    ok, out, _ = run_command(["mokutil", "--sb-state"])
    return "enabled" in out.lower() if ok else False


def secureboot_state() -> str:
    """Like secureboot_active(), but keeps "the command itself failed
    (mokutil missing, no UEFI, permission error...)" distinct from
    "really disabled" instead of collapsing both into False — the UI
    can then honestly show "Unknown status" instead of a fabricated
    "Disabled" for a state we never actually read."""
    ok, out, _ = run_command(["mokutil", "--sb-state"])
    if not ok:
        return "unknown"
    return "active" if "enabled" in out.lower() else "inactive"


def secureboot_unknown_reason() -> str:
    """Only meaningful when secureboot_state() == "unknown" — classifies
    WHY the real state couldn't be read, from read-only signals alone
    (no new privileged access), so the UI can explain the real cause
    instead of just saying "unknown" with no context. Never used to turn
    "unknown" into a guessed active/inactive — it only explains it.

    Checked in order, from the most fundamental cause down:
    1. no /sys/firmware/efi at all -> the machine booted BIOS/Legacy,
       Secure Boot doesn't apply.
    2. /sys/firmware/efi but no efivars -> UEFI variables aren't exposed
       (efivarfs not mounted/available).
    3. mokutil isn't installed -> the detection command itself is missing.
    4. mokutil ran but failed with a permission-shaped error.
    5. anything else -> a genuine, unclassified read error.
    """
    if not os.path.isdir("/sys/firmware/efi"):
        return "no_efi"
    if not os.path.isdir("/sys/firmware/efi/efivars"):
        return "no_efivarfs"
    if not _cmd_exists("mokutil"):
        return "tool_missing"
    ok, _out, err = run_command(["mokutil", "--sb-state"])
    if not ok:
        low = err.lower()
        if "permission" in low or "denied" in low or "not permitted" in low:
            return "permission_denied"
    return "read_error"
