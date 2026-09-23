import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

// Strips Rust/JS line and block comments so "must not contain" checks look
// at real code and commands, not at documentation explaining what the
// module deliberately avoids.
const codeOnly = (source) =>
  source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trimStart().startsWith("//"))
    .join("\n");

test("WinBoat uses a dedicated allow-listed helper and its own polkit action", () => {
  const helper = read("../src-tauri/src/bin/mg-linux-toolbox-winboat-helper.rs");
  const lib = read("../src-tauri/src/lib.rs");
  const policy = read("../packaging/polkit/com.mg.linuxtoolbox.winboat.policy");
  const packageConfig = read("../packaging/tauri.deb.conf.json");
  assert.match(helper, /is_operation\(action\)/);
  assert.match(helper, /geteuid\(\)/);
  assert.match(helper, /!= 0/);
  assert.match(helper, /authorization_required/);
  assert.match(policy, /com\.mg\.linuxtoolbox\.winboat/);
  assert.match(policy, /mg-linux-toolbox-winboat-helper/);
  assert.match(packageConfig, /mg-linux-toolbox-winboat-helper/);
  assert.match(packageConfig, /com\.mg\.linuxtoolbox\.winboat\.policy/);
  // The Tauri layer only ever passes the one closed operation name.
  assert.match(lib, /"winboat-prepare"\.to_string\(\)/);
});

test("the frontend never names a command, a package or a path for WinBoat", () => {
  const frontend = read("../src/main.js");
  assert.match(frontend, /invoke\("get_winboat_status"\)/);
  assert.match(frontend, /invoke\("prepare_winboat"\)/);
  assert.match(frontend, /invoke\("open_winboat"\)/);
  assert.match(frontend, /invoke\("reboot_now"\)/);
  for (const forbidden of [
    "usermod", "modprobe", "apt-get", "docker-ce", "systemctl", "/etc/",
    "download.docker.com",
  ]) {
    assert.ok(
      !frontend.includes(forbidden),
      `the frontend must not mention ${forbidden}`,
    );
  }
});

test("no helper, no module and no frontend path uses a shell or stores a password", () => {
  for (const path of [
    "../src-tauri/src/winboat.rs",
    "../src-tauri/src/bin/mg-linux-toolbox-winboat-helper.rs",
    "../src-tauri/src/lib.rs",
    "../src/main.js",
  ]) {
    const source = read(path);
    assert.doesNotMatch(source, /sudo -S|Command::new\("sh"\)|Command::new\("bash"\)|sh -c/);
    assert.doesNotMatch(source, /password|passwd/);
  }
});

test("KVM handling never installs the libvirt stack and never adds the libvirt group", () => {
  const winboat = codeOnly(read("../src-tauri/src/winboat.rs"));
  for (const forbidden of ["libvirt", "virt-manager", "qemu", "virt-install", "virsh"]) {
    assert.ok(
      !winboat.toLowerCase().includes(forbidden),
      `WinBoat must not depend on ${forbidden}`,
    );
  }
  // The only group grants this module may ever perform are docker and the
  // group that really owns /dev/kvm -- never a hardcoded libvirt group.
  assert.match(winboat, /"usermod"/);
  assert.doesNotMatch(winboat, /-aG", "libvirt/);
});

test("docker repo/firewall/AppArmor hacks from the old scripts are not present", () => {
  const winboat = codeOnly(read("../src-tauri/src/winboat.rs"));
  for (const forbidden of [
    "ip_tables", "iptable_nat", "nftables", "firewalld", "apparmor",
    "net.bridge.bridge-nf-call",
  ]) {
    assert.ok(
      !winboat.toLowerCase().includes(forbidden.toLowerCase()),
      `WinBoat must not configure ${forbidden}`,
    );
  }
});

test("Docker Desktop and Snap installations are reported, never silently replaced", () => {
  const winboat = read("../src-tauri/src/winboat.rs");
  assert.match(winboat, /desktop-linux/);
  assert.match(winboat, /docker_path_is_snap/);
  // Removing an existing Docker installation is never planned.
  for (const forbidden of ["remove", "purge", "removing"]) {
    assert.doesNotMatch(winboat, new RegExp(`"${forbidden}"`));
  }
});

test("the reboot is only ever requested through logind and never automatic", () => {
  const lib = read("../src-tauri/src/lib.rs");
  const frontend = read("../src/main.js");
  assert.match(lib, /"reboot"/);
  // The frontend calls reboot_now only from its own explicit button handler.
  assert.match(frontend, /winboat-reboot-now-button"\)\.addEventListener\("click",rebootNow\)/);
  assert.doesNotMatch(frontend, /setTimeout\([^)]*rebootNow/);
});

test("Italian and English expose every programs.winboat* key the page uses", () => {
  const i18n = read("../src/js/i18n.js");
  const frontend = read("../src/main.js") + read("../src/index.html");
  const keys = new Set();
  for (const match of frontend.matchAll(
    /programs\.winboat[A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*/g,
  )) {
    keys.add(match[0]);
  }
  for (const key of keys) {
    const occurrences = i18n.split(`"${key}"`).length - 1;
    assert.equal(occurrences, 2, `${key} must exist in both dictionaries`);
  }
});
