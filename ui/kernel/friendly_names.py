"""
Centralized feature_id -> friendly display name resolution — used
wherever a raw feature_id could otherwise leak into the UI as a title
(the History/Cronologia page, checkpoint restore reports). Purely
presentational: the stored record always keeps the real feature_id
untouched, this only decides what a human reads for it.

Reuses, wherever possible, the exact same title text already shown
elsewhere in the app for that feature — never a second, independently
worded name for the same thing.
"""
from core.i18n import T

# feature_id (exactly as stored/logged) -> the SAME i18n title key the
# rest of the app already uses for that feature. Built from every
# KernelFeatureRow's own i18n_key_base (page_kernel.py, page_audio.py,
# page_performance.py, page_virt.py, page_security.py) plus the
# handful of ids that only ever appear in the history log because they
# come from a setup module rather than a KernelFeature registry entry
# (core/virt_setup.py, core/bootloader_iommu.py, core/vfio_setup.py,
# core/apparmor_setup.py).
FEATURE_ID_TITLE_KEYS = {
    # Funzioni Kernel
    "monitoring.psi": "kf_psi_title",
    "cpu.turbo_boost": "turbo_title",
    "cpu.governor": "governor_title",
    "cpu.epp": "epp_title",
    "cpu.frequency_limits": "kf_cpu_freq_limits_title",
    "memory.swappiness": "kf_swappiness_title",
    "memory.thp": "thp_title",
    "memory.zram": "zram_title",
    "memory.zswap": "zswap_title",
    "memory.mglru": "kf_mglru_title",
    "memory.swap_readahead": "kf_swap_readahead_title",
    "storage.io_scheduler": "kf_io_scheduler_title",
    "storage.read_ahead": "kf_read_ahead_title",
    "network.tcp_congestion_control": "kf_tcp_congestion_title",
    "security.dmesg_restrict": "kf_dmesg_restrict_title",
    "security.kptr_restrict": "kf_kptr_restrict_title",
    "security.ptrace_scope": "kf_ptrace_scope_title",
    "security.protected_paths": "kf_protected_paths_title",
    # Energia e batteria / Audio
    "battery.status": "battery_status_title",
    "battery.charge_threshold": "battery_protection_title",
    "battery.platform_profile": "battery_platform_profile_title",
    "battery.suspend_mode": "battery_suspend_mode_title",
    "audio.power_save": "audio_power_title",
    # Virtualizzazione / Sicurezza
    "virt.ksm": "virt_ksm_title",
    "selinux.mode": "selinux_title",
    # History-only pseudo-ids (no KernelFeature registry entry — logged
    # directly by their setup module) — reuse the same title already
    # shown on the Virtualizzazione/Sicurezza pages where possible.
    "virt.kvm": "kvm_title",
    "virt.iommu": "iommu_title",
    "virt.vfio": "vfio_title",
    "virt.virt_manager": "history_feature_virt_manager",
    "apparmor.profile": "apparmor_title",
}


def _derive_readable_fallback(feature_id: str) -> str:
    """An unrecognized id never shows as a raw dotted string — this is
    a best-effort readability transform (last segment, underscores to
    spaces, capitalized), NOT a verified translation. Used only when
    the id genuinely isn't in the table above."""
    if not feature_id:
        return T("history_feature_unknown")
    tail = feature_id.rsplit(".", 1)[-1]
    words = tail.replace("_", " ").replace("-", " ").strip()
    if not words:
        return feature_id
    return words[0].upper() + words[1:]


def friendly_feature_name(feature_id: str) -> str:
    """The name to show as a TITLE — never the raw feature_id itself.
    Known id -> the same title used elsewhere in the app. Unknown id ->
    a readable derived text, never an invented translation."""
    key = FEATURE_ID_TITLE_KEYS.get(feature_id)
    if key:
        return T(key)
    return _derive_readable_fallback(feature_id)
