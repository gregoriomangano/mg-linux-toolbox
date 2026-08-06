"""
GPU vendor classification, built on the same real /sys DRM driver probe
already used by core.gaming_readiness (never a distro/hardware guess).

This exists because a single generic Vulkan/OpenGL loader package is not
enough to make 32-bit Vulkan actually work: the loader (libvulkan1) picks
up whichever ICD (Installable Client Driver) is present for the real GPU
at runtime, and that ICD package name is vendor-specific
(libvulkan_radeon vs libvulkan_intel vs libvulkan_nouveau vs a
proprietary NVIDIA package from a repository this app never adds on its
own). Installing the wrong one, or assuming "loader present" means
"Vulkan works", is how a real AMD/Radeon machine ends up with a 32-bit
Vulkan loader and still no working 32-bit Vulkan.
"""
import glob
import os

AMD = "amd"
INTEL = "intel"
NVIDIA_PROPRIETARY = "nvidia_proprietary"
NOUVEAU = "nouveau"
UNKNOWN = "unknown"

# Kernel DRM driver name -> vendor family. "nvidia" here is always the
# proprietary driver (the open nouveau driver reports as "nouveau").
_DRIVER_TO_VENDOR = {
    "amdgpu": AMD,
    "radeon": AMD,
    "i915": INTEL,
    "xe": INTEL,
    "nvidia": NVIDIA_PROPRIETARY,
    "nouveau": NOUVEAU,
}


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


def detect_gpu_vendor(sys_root: str = "/sys") -> str:
    """Returns one of AMD, INTEL, NVIDIA_PROPRIETARY, NOUVEAU, UNKNOWN.
    UNKNOWN covers both "no GPU driver bound" and any driver this app
    doesn't have a confirmed package mapping for — callers must treat it
    as "cannot safely proceed", never as "assume AMD/Intel"."""
    return _DRIVER_TO_VENDOR.get(_gpu_driver(sys_root=sys_root), UNKNOWN)
