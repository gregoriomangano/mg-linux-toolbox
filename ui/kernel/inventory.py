"""
Single source of truth for "which FeatureCards does the Kernel page
actually build right now, on this machine". Both KernelPage itself
(to decide which optional rows to construct) and the Home page's
"Funzioni Kernel" summary card read from this same list — so the two
can never again show two different interpretations of the same page.

Deliberately excludes battery/audio/virt/SELinux features — those live
on other pages, even though some of them are also KernelFeature
subclasses registered elsewhere. Deliberately ONE entry for
cpu.frequency_limits regardless of how many real cpufreq policies this
machine has (the UI aggregates every policy into a single card), and
ONE entry per real disk for storage.io_scheduler/storage.read_ahead
(never per-policy, never per-partition).

Nothing here writes anything — every entry comes from probe(), which
only ever reads /proc or /sys.
"""
from dataclasses import dataclass
from typing import Optional

from core.kernel_features.base import SupportStatus
from core.kernel_features.monitoring import PSIFeature
from core.kernel_features.cpu import TurboBoostFeature, GovernorFeature, EPPFeature, CpuFrequencyLimitsFeature
from core.kernel_features.memory import (
    SwappinessFeature, THPFeature, ZramFeature, ZswapFeature, MGLRUFeature, SwapReadaheadFeature,
)
from core.kernel_features.storage import IOSchedulerFeature, ReadAheadFeature, list_real_disks
from core.kernel_features.network import TcpCongestionControlFeature
from core.kernel_features.security import (
    DmesgRestrictFeature, KptrRestrictFeature, PtraceScopeFeature, ProtectedPathsFeature,
)

SUPPORTED_STATUSES = (
    SupportStatus.SUPPORTED_READ_ONLY, SupportStatus.SUPPORTED_RUNTIME, SupportStatus.SUPPORTED_PERSISTENT,
)
UNSUPPORTED_STATUSES = (
    SupportStatus.UNSUPPORTED_KERNEL, SupportStatus.UNSUPPORTED_HARDWARE,
)


@dataclass
class KernelInventoryEntry:
    feature_id: str
    group: str  # "pressure" | "cpu" | "memory" | "storage" | "network" | "security"
    support: SupportStatus
    device_id: Optional[str] = None
    friendly_disk_name: Optional[str] = None


def _add_optional(feature, group: str, entries: list) -> None:
    """Only appended if genuinely usable — these rows don't exist at
    all in the page when unsupported (unlike the 'always shown' ones
    below, which appear as a card even to explain they're unsupported)."""
    status = feature.probe()
    if status in SUPPORTED_STATUSES:
        entries.append(KernelInventoryEntry(feature.id, group, status))


def build_kernel_inventory(proc_root: str = "/proc", sys_root: str = "/sys") -> list:
    """Every entry the Kernel page constructs a FeatureCard for, right
    now — same order, same gating conditions the page itself uses.

    proc_root/sys_root default to the real filesystem, exactly like
    every KernelFeature already does — passing overrides (as the tests
    do) never touches the real machine; it only points every feature
    instance built here at a fake tree instead, the same injectable-
    dependency pattern KernelFeature itself already uses.
    """
    kwargs = {"proc_root": proc_root, "sys_root": sys_root}
    entries = []

    # ── Pressione e stato — always shown ──────────────────────────
    entries.append(KernelInventoryEntry("monitoring.psi", "pressure", PSIFeature(**kwargs).probe()))

    # ── CPU — Turbo/Governor always shown; EPP and frequency limits
    # only when genuinely usable. Frequency limits is ONE entry no
    # matter how many real cpufreq policies exist underneath it. ────
    entries.append(KernelInventoryEntry("cpu.turbo_boost", "cpu", TurboBoostFeature(**kwargs).probe()))
    entries.append(KernelInventoryEntry("cpu.governor", "cpu", GovernorFeature(**kwargs).probe()))
    _add_optional(EPPFeature(**kwargs), "cpu", entries)
    _add_optional(CpuFrequencyLimitsFeature(**kwargs), "cpu", entries)

    # ── Memoria — Swappiness/THP/ZRAM always shown; the rest only
    # when the kernel really exposes them. ──────────────────────────
    entries.append(KernelInventoryEntry("memory.swappiness", "memory", SwappinessFeature(**kwargs).probe()))
    entries.append(KernelInventoryEntry("memory.thp", "memory", THPFeature(**kwargs).probe()))
    entries.append(KernelInventoryEntry("memory.zram", "memory", ZramFeature(**kwargs).probe()))
    _add_optional(ZswapFeature(**kwargs), "memory", entries)
    _add_optional(MGLRUFeature(**kwargs), "memory", entries)
    _add_optional(SwapReadaheadFeature(**kwargs), "memory", entries)

    # ── Disco e I/O — one I/O-scheduler card per real disk (always
    # shown for every disk list_real_disks() finds), one read-ahead
    # card per real disk (only where the kernel supports it there). ──
    disks = list_real_disks(sys_root=sys_root)
    for device_id, friendly_name in disks:
        status = IOSchedulerFeature(device_id, **kwargs).probe()
        entries.append(KernelInventoryEntry(
            f"storage.io_scheduler:{device_id}", "storage", status,
            device_id=device_id, friendly_disk_name=friendly_name,
        ))
    for device_id, friendly_name in disks:
        feature = ReadAheadFeature(device_id, **kwargs)
        status = feature.probe()
        if status in SUPPORTED_STATUSES:
            entries.append(KernelInventoryEntry(
                f"storage.read_ahead:{device_id}", "storage", status,
                device_id=device_id, friendly_disk_name=friendly_name,
            ))

    # ── Rete kernel — only when the kernel really exposes it. ───────
    _add_optional(TcpCongestionControlFeature(**kwargs), "network", entries)

    # ── Sicurezza kernel (secondo blocco) — each independently, only
    # when genuinely usable. ─────────────────────────────────────────
    for feature in (DmesgRestrictFeature(**kwargs), KptrRestrictFeature(**kwargs),
                    PtraceScopeFeature(**kwargs), ProtectedPathsFeature(**kwargs)):
        _add_optional(feature, "security", entries)

    return entries


def count_kernel_inventory(proc_root: str = "/proc", sys_root: str = "/sys") -> tuple:
    """(total, available, unsupported) computed from the exact same
    inventory the page renders. 'available' is what the card would
    really let you read/try; 'unsupported' is a real UNSUPPORTED_KERNEL/
    UNSUPPORTED_HARDWARE probe result — never a guess. The two don't
    always sum to the total (a permission error or a transient probe
    failure is neither) — that's an honest gap, not a bug."""
    entries = build_kernel_inventory(proc_root=proc_root, sys_root=sys_root)
    available = sum(1 for e in entries if e.support in SUPPORTED_STATUSES)
    unsupported = sum(1 for e in entries if e.support in UNSUPPORTED_STATUSES)
    return len(entries), available, unsupported
