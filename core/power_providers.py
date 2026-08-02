"""
Power-profile provider detection.

Several different daemons can own "power profiles" (power-saver / balanced
/ performance) on Linux, and installing a second one on top of an
existing one is actively harmful: they fight over the same D-Bus name
(org.freedesktop.UPower.PowerProfiles) and/or the same systemd Conflicts=
relationship. Concretely, power-profiles-daemon.service declares
`Conflicts=tuned.service tlp.service auto-cpufreq.service
system76-power.service` — starting one stops the others. Pop!_OS ships
system76-power by default, which is exactly this situation.

So before ever proposing to install anything, we detect what's already
there and prefer it — matching the required order:
  1. use an already-installed, working provider;
  2. never install a second, conflicting one;
  3. install something only if nothing compatible is present and it's
     actually available in the configured repositories;
  4. never remove anything automatically on conflict;
  5. explain clearly which provider is already in charge.
"""
from core.executor import run_command

# Checked in priority order. Pop!_OS ships system76-power by default, so it
# must win over power-profiles-daemon whenever both happen to be present.
PROVIDERS = ("system76-power", "power-profiles-daemon", "tuned-ppd", "tlp")

PROVIDER_LABELS = {
    "system76-power": "System76 Power",
    "power-profiles-daemon": "Power Profiles Daemon",
    "tuned-ppd": "TuneD (tuned-ppd)",
    "tlp": "TLP",
    "acpi_platform_profile": "ACPI platform profile (kernel)",
}


def _cmd_exists(cmd: str) -> bool:
    ok, _, _ = run_command(["which", cmd])
    return ok


def _service_active(name: str) -> bool:
    ok, out, _ = run_command(["systemctl", "is-active", name])
    return ok and out.strip() == "active"


def _service_known(name: str) -> bool:
    ok, out, _ = run_command(["systemctl", "list-unit-files", f"{name}.service"])
    return ok and f"{name}.service" in out


def _system76_power_status() -> str:
    """"active" | "installed_inactive" | "absent" """
    if not _cmd_exists("system76-power"):
        return "absent"
    return "active" if _service_active("system76-power") else "installed_inactive"


def _power_profiles_daemon_status() -> str:
    if not _cmd_exists("powerprofilesctl"):
        return "absent"
    return "active" if _service_active("power-profiles-daemon") else "installed_inactive"


def _tuned_ppd_status() -> str:
    """tuned-ppd is TuneD running its ppd-compatible profile, exposing the
    same powerprofilesctl-style interface — only counts if the D-Bus name
    is actually served, which in practice means the tuned-ppd unit (or
    tuned with the ppd plugin) is active."""
    if not _cmd_exists("tuned-adm"):
        return "absent"
    if _service_active("tuned-ppd") or _service_active("tuned"):
        return "active"
    return "installed_inactive" if _service_known("tuned") or _service_known("tuned-ppd") else "absent"


def _tlp_status() -> str:
    if not _cmd_exists("tlp"):
        return "absent"
    return "active" if _service_active("tlp") else "installed_inactive"


def _acpi_platform_profile_present() -> bool:
    return __import__("os").path.exists("/sys/firmware/acpi/platform_profile")


_STATUS_CHECKS = {
    "system76-power": _system76_power_status,
    "power-profiles-daemon": _power_profiles_daemon_status,
    "tuned-ppd": _tuned_ppd_status,
    "tlp": _tlp_status,
}


def detect_active_provider() -> "str | None":
    """Returns the id of the provider that is actually running right now,
    preferring the priority order in PROVIDERS. None if none is active."""
    for provider in PROVIDERS:
        if _STATUS_CHECKS[provider]() == "active":
            return provider
    return None


def detect_installed_inactive_providers() -> list:
    """Providers that are present but not currently running — still
    something we must not silently install a second thing on top of
    without at least warning about a naming/service conflict."""
    return [p for p in PROVIDERS
            if _STATUS_CHECKS[p]() == "installed_inactive"]


def has_kernel_acpi_profile() -> bool:
    return _acpi_platform_profile_present()


def provider_label(provider_id: str) -> str:
    return PROVIDER_LABELS.get(provider_id, provider_id)


def resolve() -> dict:
    """
    Single entry point the UI should call. Returns:
      {
        "active": "system76-power" | "power-profiles-daemon" | ... | None,
        "installed_inactive": [...],
        "should_offer_install": bool,   # only True if nothing usable exists
        "has_acpi_fallback": bool,
      }
    Never recommends installing a second provider when one is already
    active or merely present-but-inactive (that would silently create a
    conflict the next time it starts).
    """
    active = detect_active_provider()
    installed_inactive = detect_installed_inactive_providers()
    return {
        "active": active,
        "installed_inactive": installed_inactive,
        "should_offer_install": active is None and not installed_inactive,
        "has_acpi_fallback": has_kernel_acpi_profile(),
    }
