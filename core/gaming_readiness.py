"""
Real gaming-readiness checks — never trusts "package installed" alone.
Each check either runs the real command/tool non-destructively or reads
the real kernel/D-Bus state.
"""
import glob
import os
import shutil
import subprocess
from dataclasses import dataclass

from core.executor import command_exists

READY = "ready"
ALMOST_READY = "almost_ready"
MISSING_COMPONENTS = "missing_components"
UNAVAILABLE = "unavailable"

_KNOWN_GOOD_DRIVERS = {"amdgpu", "i915", "xe", "nvidia", "nouveau", "radeon"}


@dataclass
class ReadinessItem:
    id: str
    label_key: str
    state: str
    detail: str = ""


def _gpu_driver(sys_root: str = "/sys") -> str:
    pattern = os.path.join(sys_root, "class", "drm", "card*", "device", "uevent")
    for uevent in glob.glob(pattern):
        try:
            with open(uevent) as f:
                for line in f:
                    if line.startswith("DRIVER="):
                        return line.strip().split("=", 1)[1]
        except OSError:
            continue
    return ""


def check_gpu_driver(sys_root: str = "/sys") -> ReadinessItem:
    driver = _gpu_driver(sys_root=sys_root)
    if driver in _KNOWN_GOOD_DRIVERS:
        return ReadinessItem("gpu_driver", "gaming_check_gpu_driver", READY, driver)
    if driver:
        return ReadinessItem("gpu_driver", "gaming_check_gpu_driver", ALMOST_READY, driver)
    return ReadinessItem("gpu_driver", "gaming_check_gpu_driver", UNAVAILABLE)


def check_vulkan() -> ReadinessItem:
    if not shutil.which("vulkaninfo"):
        return ReadinessItem("vulkan", "gaming_check_vulkan", MISSING_COMPONENTS)
    try:
        r = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return ReadinessItem("vulkan", "gaming_check_vulkan", MISSING_COMPONENTS)
    if r.returncode == 0 and "Vulkan Instance Version" in r.stdout:
        return ReadinessItem("vulkan", "gaming_check_vulkan", READY)
    return ReadinessItem("vulkan", "gaming_check_vulkan", MISSING_COMPONENTS)


def check_lib32() -> ReadinessItem:
    import backend.all as B
    if not B.lib32_supported():
        return ReadinessItem("lib32", "gaming_check_lib32", UNAVAILABLE)
    return ReadinessItem("lib32", "gaming_check_lib32", READY if B.lib32_installed() else MISSING_COMPONENTS)


def gamemode_real_status() -> str:
    """"not_installed" | "installed_not_ready" | "ready" — actually runs
    `gamemoderun true` (a real, harmless no-op command) rather than
    trusting the package database. GameMode is a daemon/lib combo, but
    the daemon is normally request-driven: we only require that the
    wrapper and daemon binaries exist and that a harmless request can be
    completed, not that a long-lived service is already active."""
    if not command_exists("gamemoded") or not command_exists("gamemoderun"):
        return "not_installed"
    try:
        r = subprocess.run(["gamemoderun", "true"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return "installed_not_ready"
    if r.returncode != 0:
        return "installed_not_ready"
    return "ready"


def check_gamemode() -> ReadinessItem:
    status = gamemode_real_status()
    state = {"not_installed": MISSING_COMPONENTS, "installed_not_ready": ALMOST_READY, "ready": READY}[status]
    return ReadinessItem("gamemode", "gaming_check_gamemode", state)


def mangohud_real_status() -> str:
    """Confirms the MangoHud Vulkan implicit layer is actually
    discoverable AND that wrapping a real (harmless, read-only)
    vulkaninfo call with it succeeds — not just that the package is
    installed."""
    if not shutil.which("mangohud") or not shutil.which("vulkaninfo"):
        return "not_installed"
    try:
        layers = subprocess.run(["vulkaninfo"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return "installed_not_ready"
    if "MANGOHUD" not in layers.stdout.upper():
        return "installed_not_ready"
    try:
        wrapped = subprocess.run(["mangohud", "vulkaninfo", "--summary"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return "installed_not_ready"
    return "ready" if wrapped.returncode == 0 else "installed_not_ready"


def check_mangohud() -> ReadinessItem:
    status = mangohud_real_status()
    state = {"not_installed": MISSING_COMPONENTS, "installed_not_ready": ALMOST_READY, "ready": READY}[status]
    return ReadinessItem("mangohud", "gaming_check_mangohud", state)


def check_controller() -> ReadinessItem:
    try:
        with open("/proc/bus/input/devices") as f:
            content = f.read()
    except OSError:
        return ReadinessItem("controller", "gaming_check_controller", UNAVAILABLE)
    found = "joystick" in content.lower() or "gamepad" in content.lower()
    # Absence of a controller is normal (not everyone games with one), so
    # this is "almost_ready" (informational) rather than a hard problem.
    return ReadinessItem("controller", "gaming_check_controller", READY if found else ALMOST_READY)


def check_power_profile() -> ReadinessItem:
    from core import power_providers
    active = power_providers.resolve()["active"]
    return ReadinessItem("power_profile", "gaming_check_power_profile",
                          READY if active else ALMOST_READY, active or "")


def check_turbo() -> ReadinessItem:
    from core.kernel_features.cpu import TurboBoostFeature
    from core.kernel_features.base import SupportStatus
    f = TurboBoostFeature()
    status = f.probe()
    if status not in (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_PERSISTENT):
        return ReadinessItem("turbo", "gaming_check_turbo", UNAVAILABLE)
    r = f.read_current()
    return ReadinessItem("turbo", "gaming_check_turbo", READY if (r.ok and r.value) else ALMOST_READY)


def check_governor() -> ReadinessItem:
    from core.kernel_features.cpu import GovernorFeature
    from core.kernel_features.base import SupportStatus
    f = GovernorFeature()
    status = f.probe()
    if status not in (SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_PERSISTENT):
        return ReadinessItem("governor", "gaming_check_governor", UNAVAILABLE)
    return ReadinessItem("governor", "gaming_check_governor", READY)


# Items whose absence genuinely blocks/limits gaming, as opposed to
# "nice to have" (controller, power profile, turbo, governor).
_CORE_ITEM_IDS = {"gpu_driver", "vulkan", "lib32", "gamemode", "mangohud"}


def overall_state(items: list) -> str:
    core = [i for i in items if i.id in _CORE_ITEM_IDS]
    if any(i.id in ("gpu_driver", "vulkan") and i.state == UNAVAILABLE for i in core):
        return UNAVAILABLE
    missing = sum(1 for i in core if i.state == MISSING_COMPONENTS)
    if missing == 0:
        return READY
    if missing <= 2:
        return ALMOST_READY
    return MISSING_COMPONENTS


def full_report() -> tuple:
    items = [
        check_gpu_driver(), check_vulkan(), check_lib32(), check_gamemode(), check_mangohud(),
        check_controller(), check_power_profile(), check_turbo(), check_governor(),
    ]
    return items, overall_state(items)
