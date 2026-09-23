import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

test("power-profiles-daemon is detected by D-Bus capability, not by distro/unit name", () => {
  const performance = readFileSync(new URL("../src-tauri/src/performance.rs", import.meta.url), "utf8");
  const ppd = readFileSync(new URL("../src-tauri/src/power_profiles_daemon.rs", import.meta.url), "utf8");

  // The informational snapshot must ask the real D-Bus client whether PPD is usable,
  // instead of matching "power-profiles-daemon.service" against systemctl output.
  assert.match(performance, /power_profiles_daemon::status\(\)/);
  assert.doesNotMatch(performance, /power-profiles-daemon\.service/);

  // The D-Bus client talks to the real PPD interface on the system bus (not session bus,
  // and not a distro-specific name), and never shells out to systemctl/powerprofilesctl.
  assert.match(ppd, /net\.hadess\.PowerProfiles/);
  assert.match(ppd, /Connection::system\(\)/);
  assert.doesNotMatch(ppd, /Connection::session\(\)/);
  assert.doesNotMatch(ppd, /systemctl/);
  assert.doesNotMatch(ppd, /powerprofilesctl/);
});

test("applying a profile through power-profiles-daemon never goes through pkexec or the sysfs helper", () => {
  const ppd = readFileSync(new URL("../src-tauri/src/power_profiles_daemon.rs", import.meta.url), "utf8");
  const lib = readFileSync(new URL("../src-tauri/src/lib.rs", import.meta.url), "utf8");

  // The PPD client itself never escalates privileges: it relies on PPD's own polkit
  // policy allowing the active session user to write ActiveProfile directly.
  assert.doesNotMatch(ppd, /Command::new\("pkexec"\)/);
  assert.doesNotMatch(ppd, /std::process::Command/);
  assert.match(ppd, /set_property\("ActiveProfile"/);

  // apply_performance_profile must check the PPD backend first and return before ever
  // reaching privileged_helper(&["apply", ...]), so the two engines can never write to
  // the same knobs (sysfs) at the same time.
  const fn = lib.match(/async fn apply_performance_profile[\s\S]*?\n}\n/)?.[0];
  assert.ok(fn, "apply_performance_profile should exist in lib.rs");
  const ppdBranchIndex = fn.indexOf("power_profiles_daemon::detect()");
  const helperCallIndex = fn.indexOf('privileged_helper(&["apply"');
  assert.ok(ppdBranchIndex >= 0, "apply_performance_profile should check the PPD backend");
  assert.ok(helperCallIndex >= 0, "apply_performance_profile should still support the sysfs helper fallback");
  assert.ok(ppdBranchIndex < helperCallIndex, "the PPD branch must be checked, and return, before the sysfs helper is ever invoked");
  assert.match(fn, /return Ok\(performance::collect_performance_snapshot\(\)\);/);
});

test("the sysfs planner still treats an active power-profiles-daemon as a conflict it must stay hands-off from", () => {
  const profiles = readFileSync(new URL("../src-tauri/src/performance_profiles.rs", import.meta.url), "utf8");
  // This is deliberately kept as a defense-in-depth check independent of the D-Bus
  // capability probe used for routing: if the D-Bus call ever fails transiently while
  // PPD is still active, the sysfs helper must still refuse to write.
  assert.match(profiles, /"power-profiles-daemon",\s*"power-profiles-daemon\.service"/);
});
