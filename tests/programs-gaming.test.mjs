import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("the Programs page hosts Gaming, GeForce NOW and DNS with separate full-width panels", () => {
  const html = read("../src/index.html");
  assert.match(html, /id="programs-nav"/);
  assert.match(html, /data-i18n="nav\.programs"/);
  assert.match(html, /<main class="performance" id="programs-page" hidden>/);
  assert.match(html, /data-control="gaming"/);
  assert.match(html, /data-control="geforce"/);
  assert.match(html, /data-control="dns"/);
  assert.match(html, /id="geforce-install-button"/);
  assert.match(html, /id="gaming-panel"/);
  assert.match(html, /id="gaming-chips"/);
  assert.match(html, /id="gaming-to-install-list"/);
  assert.match(html, /id="gaming-changes-list"/);
  assert.match(html, /id="gaming-warnings-list"/);
  assert.match(html, /id="gaming-technical"/);
  assert.match(html, /id="gaming-progress"/);
  assert.match(html, /id="gaming-outcome"/);
  assert.match(html, /id="gaming-error-details"/);
  assert.match(html, /id="dns-toggle-button"/);
  assert.match(html, /id="dns-panel"/);
  assert.match(html, /name="dns-provider" value="automatic"/);
  for (const provider of ["cloudflare", "google", "quad9", "adguard"]) {
    assert.match(html, new RegExp(`name="dns-provider" value="${provider}"`));
  }
  // The Programs nav item comes after Gestione e pulizia.
  const order = [html.indexOf('id="software-nav"'), html.indexOf('id="programs-nav"')];
  assert.ok(order[0] >= 0 && order[1] > order[0]);
});

test("the Programs page is only loaded on navigation, never at startup", () => {
  const frontend = read("../src/main.js");
  assert.match(frontend, /programs: \{content:"programs-page", nav:"programs-nav", breadcrumb:"breadcrumb\.programs", onEnter:loadPrograms\}/);
  const bootstrapLine = frontend.split("\n").find((line) => line.includes("refreshSystem();refreshPing();"));
  assert.ok(bootstrapLine, "bootstrap line not found");
  assert.doesNotMatch(bootstrapLine, /loadPrograms|loadGaming|loadDns/);
  assert.doesNotMatch(frontend, /setInterval\([^)]*[Gg]aming/);
});

test("the frontend never names a package, a repository or a shell command", () => {
  const frontend = read("../src/main.js");
  assert.match(frontend, /invoke\("get_gaming_status"\)/);
  assert.match(frontend, /invoke\("get_geforce_now_status"\)/);
  assert.match(frontend, /invoke\("prepare_gaming"\)/);
  assert.match(frontend, /invoke\("install_geforce_now"\)/);
  assert.match(frontend, /invoke\("get_dns_status"\)/);
  assert.match(frontend, /invoke\("set_dns_provider",\{provider:state\.dnsSelection\}\)/);
  assert.match(frontend, /listen\("gaming-progress"/);
  for (const source of [frontend, read("../src/js/gaming-view.js")]) {
    for (const forbidden of ["apt-get", "pacman", "dnf ", "zypper", "pkexec", "flatpak install", "sudo ", "mesa-vulkan-drivers", "steam-installer", "libgl1"]) {
      assert.doesNotMatch(source, new RegExp(forbidden), `the UI must never contain "${forbidden}"`);
    }
  }
});

test("a completed Gaming plan does not present metadata refresh as a missing requirement", async () => {
  const view = await import("../src/js/gaming-view.js");
  const ready = { components: [{ id: "steam", state: "installed" }], changes: [{ kind: "refresh", label: "refresh", detail: [] }] };
  assert.deepEqual(view.specialChanges(ready), []);
  const pending = { components: [{ id: "steam", state: "toInstall" }], changes: ready.changes };
  assert.deepEqual(view.specialChanges(pending).map((change) => change.kind), ["refresh"]);
});

test("an unavailable component is never described as a ready system", async () => {
  const view = await import("../src/js/gaming-view.js");
  const t = (key) => key;
  const plan = { components: [{ id: "steam", state: "unavailable" }] };
  assert.equal(view.statusText(plan, t), "programs.gamingIncomplete");
  assert.equal(view.actionLabel(plan, null, t), "programs.reviewRequired");
});

test("the count is derived from the plan and never double-interpolated", async () => {
  const view = await import("../src/js/gaming-view.js");
  const dict = {
    "programs.gamingReadyNone": "Sistema già pronto",
    "programs.gamingReadyOne": "1 componente da installare",
    "programs.gamingReadyMany": "{count} componenti da installare",
    "programs.installCount": "Installa {count} componenti",
    "programs.retryInstall": "Riprova i mancanti ({count})",
    "programs.ready": "Sistema pronto",
  };
  const t = (key, params = {}) =>
    (dict[key] ?? key).replace(/\{(\w+)\}/g, (_, name) => params[name] ?? `{${name}}`);
  const planWith = (count) => ({
    components: Array.from({ length: count }, (_, i) => ({ id: `c${i}`, name: `C${i}`, state: "toInstall", detail: [] })),
  });
  assert.equal(view.missingCount(planWith(9)), 9);
  assert.equal(view.statusText(planWith(9), t), "9 componenti da installare");
  assert.equal(view.statusText(planWith(1), t), "1 componente da installare");
  assert.equal(view.statusText(planWith(0), t), "Sistema già pronto");
  assert.equal(view.statusText({ components: [] }, t), "Sistema già pronto");
  assert.equal(view.actionLabel(planWith(9), null, t), "Installa 9 componenti");
  assert.equal(view.actionLabel(planWith(3), { ok: false }, t), "Riprova i mancanti (3)");
  assert.equal(view.actionLabel(planWith(0), { ok: false }, t), "Sistema pronto");
});

test("view helpers split the plan into the right human sections", async () => {
  const view = await import("../src/js/gaming-view.js");
  const t = (key) => key;
  const plan = {
    components: [
      { id: "steam", name: "Steam", state: "toInstall", detail: ["steam-installer"] },
      { id: "wine", name: "Wine", state: "installed", detail: ["wine"] },
      { id: "nvidia32", name: "NVIDIA", state: "unavailable", detail: [] },
    ],
    changes: [
      { kind: "addI386", label: "addI386", detail: [] },
      { kind: "refresh", label: "refresh", detail: [] },
      { kind: "install", label: "installNative", detail: ["steam-installer"] },
    ],
    warnings: ["i386Required", "i386Required", "componentUnavailable", "nvidiaStackUnknown"],
  };
  assert.deepEqual(view.pendingComponents(plan).map((c) => c.id), ["steam"]);
  assert.deepEqual(view.installedComponents(plan).map((c) => c.id), ["wine"]);
  assert.deepEqual(view.unavailableComponents(plan).map((c) => c.id), ["nvidia32"]);
  assert.deepEqual(view.specialChanges(plan).map((c) => c.kind), ["addI386", "refresh"]);
  assert.deepEqual(view.warningKeys(plan), ["i386Required", "nvidiaStackUnknown"]);
  assert.deepEqual(view.technicalRows(plan, t), [{ label: "programs.compSteam", detail: "steam-installer" }]);
  assert.equal(view.componentLabel(plan.components[0], t), "programs.compSteam");
  assert.equal(view.componentLabel({ id: "unknown", name: "Proprietary Thing" }, t), "Proprietary Thing");
});

test("failure reporting names the step, the packages and the real stderr", async () => {
  const view = await import("../src/js/gaming-view.js");
  const t = (key, params = {}) =>
    (key === "programs.errorInstall" ? "Il gestore pacchetti non ha potuto installare: {packages}." : key)
      .replace(/\{(\w+)\}/g, (_, name) => params[name] ?? `{${name}}`);
  const report = {
    ok: false,
    steps: [
      { step: "add_i386", ok: true, operation: "dpkg --add-architecture i386", exitCode: 0, packages: [], flatpakApps: [] },
      {
        step: "install_packages",
        ok: false,
        operation: "apt-get install -y steam-installer",
        exitCode: 100,
        packages: ["steam-installer"],
        flatpakApps: [],
        stderrTail: "E: Unable to satisfy dependencies",
      },
    ],
    missingPackages: [],
  };
  assert.equal(view.failedStep(report).step, "install_packages");
  assert.match(view.errorMessage(report, t), /steam-installer/);
  const rows = view.errorRows(report);
  assert.equal(rows.length, 2);
  assert.equal(rows[1].exitCode, 100);
  assert.match(rows[1].stderr, /Unable to satisfy/);
  assert.equal(view.outcomeIsPartial(report), true);
  assert.equal(view.outcomeIsPartial({ ok: true, steps: [] }), false);
  const busy = { ok: false, steps: [{ step: "preflight", ok: false, exitCode: -1, packages: [], flatpakApps: [], stderrTail: "package_manager_busy:apt" }] };
  assert.equal(view.failedStep(busy).step, "preflight");
});

test("the privileged programs helper is separate, allow-listed and reports steps", () => {
  const bin = read("../src-tauri/src/bin/mg-linux-toolbox-programs-helper.rs");
  assert.match(bin, /gaming::is_operation/);
  assert.match(bin, /gaming::apply_operation/);
  assert.match(bin, /geteuid/);
  assert.match(bin, /"kind":"step"/);
  assert.match(bin, /"kind":"report"/);
  assert.doesNotMatch(bin, /Command::new\("sh"/);
  assert.doesNotMatch(bin, /Command::new\("bash"/);
  const gaming = read("../src-tauri/src/gaming.rs");
  assert.match(gaming, /"programs-prepare-gaming" \| "programs-install-geforce-now"/);
  assert.match(gaming, /PROGRAMS_HELPER_PATH/);
  assert.match(gaming, /pub struct StepReport/);
  assert.match(gaming, /pub struct InstallReport/);
  assert.match(gaming, /missing_packages/);
  const lib = read("../src-tauri/src/lib.rs");
  assert.match(lib, /run_programs_helper/);
  assert.match(lib, /gaming::PROGRAMS_HELPER_PATH/);
  const policy = read("../packaging/polkit/com.mg.linuxtoolbox.programs.policy");
  assert.match(policy, /com\.mg\.linuxtoolbox\.programs/);
  assert.doesNotMatch(policy, /com\.mg\.linuxtoolbox\.repository/);
  const packageConfig = read("../packaging/tauri.deb.conf.json");
  assert.match(packageConfig, /mg-linux-toolbox-programs-helper/);
  assert.match(packageConfig, /com\.mg\.linuxtoolbox\.programs\.policy/);
  assert.doesNotMatch(read("../src-tauri/tauri.conf.json"), /target\/release/);
});

test("the gaming module only ever plans the 32-bit NVIDIA counterpart, never a driver", () => {
  const gaming = read("../src-tauri/src/gaming.rs");
  for (const forbidden of ["gamescope", "mangohud", "goverlay", "gamemode", "nvidia-open"]) {
    assert.doesNotMatch(gaming, new RegExp(`"${forbidden}"`, "i"), `gaming.rs must not propose ${forbidden}`);
  }
  assert.doesNotMatch(gaming, /"nvidia-driver-[0-9]/, "no versioned NVIDIA driver package may be proposed");
  assert.match(gaming, /Match the installed branch by name, never assume one/);
  assert.match(gaming, /lib32-nvidia-utils/);
  assert.match(gaming, /xorg-x11-drv-nvidia-libs\.i686/);
  assert.match(gaming, /nvidia-driver-libs:i386/);
});

test("Italian and English expose every programs.* key the page uses", async () => {
  const { dictionaries } = await import("../src/js/i18n.js");
  const keys = [
    "nav.programs", "breadcrumb.programs",
    "programs.title", "programs.intro", "programs.gamingTitle", "programs.gamingText", "programs.gamingConfigure",
    "programs.distro", "programs.gpus", "programs.packageManager",
    "programs.toInstall", "programs.present", "programs.changes", "programs.warnings",
    "programs.installCount", "programs.retryInstall", "programs.ready", "programs.installing",
    "programs.gamingReadyNone", "programs.gamingReadyOne", "programs.gamingReadyMany",
    "programs.gamingIncomplete", "programs.reviewRequired",
    "programs.noGpu",
    "programs.progressTitle", "programs.installed", "programs.notCompleted",
    "programs.stepPreflight", "programs.stepAddI386", "programs.stepEnableMultilib", "programs.stepRefresh",
    "programs.stepInstall", "programs.stepInstallFlatpak", "programs.stepAddFlatpakRemote", "programs.stepAddRepository",
    "programs.compSteam", "programs.compLutris", "programs.compHeroic", "programs.compWine", "programs.compWinetricks",
    "programs.compProtontricks", "programs.compFlatpak", "programs.compVulkan64", "programs.compVulkan32",
    "programs.compOpenGL32", "programs.compNvidia32", "programs.compGeforceNow",
    "programs.outcomeOk", "programs.outcomePartial",
    "programs.errorGeneric", "programs.errorBusy", "programs.errorInstall", "programs.errorFlatpak", "programs.detectFailed",
    "programs.errorStep", "programs.errorVerify", "programs.errorCancelled",
    "programs.technical", "programs.technicalDetails",
    "programs.geforceTitle", "programs.geforceText", "programs.geforceInstall", "programs.geforceInstalled",
    "programs.geforceUnavailable", "programs.geforceBadge", "programs.geforceHint", "programs.geforceNotInstalled",
    "programs.changeAddI386", "programs.changeEnableMultilib", "programs.changeAddRpmFusion", "programs.changeAddFlatpakRemote",
    "programs.changeRefresh", "programs.changeInstallNative", "programs.changeInstallFlatpak",
    "programs.warnComponentUnavailable", "programs.warnComponentUnknown", "programs.warnNvidiaStackUnknown", "programs.warnNoGpuDetected",
    "programs.warnRpmfusionRequired", "programs.warnRpmfusionRequiredManual", "programs.warnMultilibRequired", "programs.warnI386Required",
    "programs.warnFlatpakNeeded", "programs.warnNvidiaMatchOnly",
    "programs.unsupportedImmutable", "programs.unsupportedDistro", "programs.packageManagerMissing",
    "programs.dnsTitle", "programs.dnsText", "programs.dnsManage", "programs.dnsReading", "programs.dnsConnection", "programs.dnsInterface", "programs.dnsCurrent",
    "programs.dnsChoose", "programs.dnsAutomatic", "programs.dnsAutomaticHint", "programs.dnsCloudflare", "programs.dnsGoogle", "programs.dnsQuad9", "programs.dnsAdguard", "programs.dnsCustom",
    "programs.dnsApply", "programs.dnsApplying", "programs.dnsApplied", "programs.dnsApplyFailed", "programs.dnsUnavailable", "programs.dnsNoConnection", "programs.dnsAmbiguous", "programs.dnsReadFailed", "programs.dnsUnsupportedMethod", "programs.dnsNone",
  ];
  for (const key of keys) {
    assert.ok(dictionaries.it[key], `Italian dictionary is missing ${key}`);
    assert.ok(dictionaries.en[key], `English dictionary is missing ${key}`);
  }
});
