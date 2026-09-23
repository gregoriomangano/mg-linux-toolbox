import test from "node:test";
import assert from "node:assert/strict";
import { friendlyCpuDriver, performanceFrequencyRange, performancePressureBand, quotaLevel } from "../src/js/ui-utils.js";

test("quota color follows remaining thresholds", () => {
  assert.equal(quotaLevel(100), "healthy");
  assert.equal(quotaLevel(50), "healthy");
  assert.equal(quotaLevel(49), "warning");
  assert.equal(quotaLevel(20), "warning");
  assert.equal(quotaLevel(19), "danger");
  assert.equal(quotaLevel(0), "danger");
});

test("performance frequency range never renders missing sysfs values", () => {
  assert.equal(performanceFrequencyRange({ minimumMhz: 1746, maximumMhz: 5086 }, "MHz"), "1746–5086 MHz");
  assert.equal(performanceFrequencyRange({ minimum_mhz: 1746, maximum_mhz: 5086 }, "MHz"), null);
  assert.equal(performanceFrequencyRange({ minimumMhz: null, maximumMhz: 5086 }, "MHz"), null);
});

test("performance pressure bands are centralized and reject invalid data", () => {
  assert.equal(performancePressureBand(0.99), "calm");
  assert.equal(performancePressureBand(1), "busy");
  assert.equal(performancePressureBand(9.99), "busy");
  assert.equal(performancePressureBand(10), "high");
  assert.equal(performancePressureBand(null), null);
  assert.equal(performancePressureBand(-1), null);
});

test("CPU drivers get a human label without hiding unknown real values", () => {
  assert.equal(friendlyCpuDriver("amd-pstate-epp"), "AMD P-State");
  assert.equal(friendlyCpuDriver("intel_pstate"), "Intel P-State");
  assert.equal(friendlyCpuDriver("acpi-cpufreq"), "ACPI CPUFreq");
  assert.equal(friendlyCpuDriver("vendor-driver"), "vendor-driver");
});
