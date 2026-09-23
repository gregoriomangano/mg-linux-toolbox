import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("DNS uses a dedicated allow-listed helper and never writes resolv.conf", () => {
  const dns = read("../src-tauri/src/dns.rs");
  const helper = read("../src-tauri/src/bin/mg-linux-toolbox-dns-helper.rs");
  const lib = read("../src-tauri/src/lib.rs");
  const policy = read("../packaging/polkit/com.mg.linuxtoolbox.dns.policy");
  assert.match(helper, /operation != "set-dns-provider"/);
  assert.match(helper, /DnsProvider::parse/);
  assert.match(lib, /provider: dns::DnsProvider/);
  assert.match(lib, /\.arg\(provider\.as_str\(\)\)/);
  assert.match(policy, /com\.mg\.linuxtoolbox\.dns/);
  assert.match(dns, /"connection"\.into\(\),\s*"modify"\.into\(\)/s);
  assert.match(dns, /"device", "reapply"/);
  for (const source of [dns, helper, lib]) {
    assert.doesNotMatch(source, /sudo -S|Command::new\("sh"\)|Command::new\("bash"\)/);
    assert.doesNotMatch(source, /\/etc\/resolv\.conf/);
  }
});

test("the frontend sends only a semantic provider identifier", () => {
  const frontend = read("../src/main.js");
  assert.match(frontend, /invoke\("set_dns_provider",\{provider:state\.dnsSelection\}\)/);
  assert.doesNotMatch(frontend, /nmcli|resolv\.conf|ignore-auto-dns|ipv4\.dns|ipv6\.dns/);
});

test("all DNS provider addresses are fixed in the backend, including IPv6", () => {
  const dns = read("../src-tauri/src/dns.rs");
  for (const address of [
    "1.1.1.1", "1.0.0.1", "2606:4700:4700::1111", "2606:4700:4700::1001",
    "8.8.8.8", "8.8.4.4", "2001:4860:4860::8888", "2001:4860:4860::8844",
    "9.9.9.9", "149.112.112.112", "2620:fe::fe", "2620:fe::9",
    "94.140.14.14", "94.140.15.15", "2a10:50c0::ad1:ff", "2a10:50c0::ad2:ff",
  ]) assert.ok(dns.includes(address), `missing ${address}`);
});
