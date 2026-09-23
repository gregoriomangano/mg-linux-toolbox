export function quotaLevel(remaining) {
  if (remaining >= 50) return "healthy";
  if (remaining >= 20) return "warning";
  return "danger";
}

export function performanceFrequencyRange(limits, unit) {
  const minimum = limits?.minimumMhz;
  const maximum = limits?.maximumMhz;
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return null;
  return `${minimum.toLocaleString("it-IT")}–${maximum.toLocaleString("it-IT")} ${unit}`;
}

export function performancePressureBand(value) {
  if (!Number.isFinite(value) || value < 0) return null;
  // PSI avg10 is the percentage of wall-clock time tasks were stalled.
  // Under 1% is quiet, 1–10% is noticeable contention, 10%+ is sustained pressure.
  if (value < 1) return "calm";
  if (value < 10) return "busy";
  return "high";
}

export function friendlyCpuDriver(driver) {
  if (!driver) return null;
  if (driver.startsWith("amd-pstate")) return "AMD P-State";
  if (driver === "intel_pstate") return "Intel P-State";
  if (driver === "acpi-cpufreq") return "ACPI CPUFreq";
  return driver;
}

// Technical EPP values map onto a translation key; unknown values are returned
// as null so the caller can fall back to showing the real value unchanged.
const EPP_KEYS = {
  performance: "eppPerformance",
  balance_performance: "eppBalancePerformance",
  balance_power: "eppBalancePower",
  power: "eppPower",
  default: "eppDefault",
  custom: "eppCustom",
  dynamic: "eppDynamic",
};

export function friendlyEpp(value) {
  return value ? EPP_KEYS[value] ?? null : null;
}

// Same idea for the cpufreq governors the kernel can expose.
const GOVERNOR_KEYS = {
  performance: "govPerformance",
  powersave: "govPowersave",
  schedutil: "govSchedutil",
  ondemand: "govOndemand",
  conservative: "govConservative",
  userspace: "govUserspace",
};

export function friendlyGovernor(value) {
  return value ? GOVERNOR_KEYS[value] ?? null : null;
}

export function validHttpUrl(value) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}
