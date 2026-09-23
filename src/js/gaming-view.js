// Pure view-model helpers for the Programs/Gaming page.
//
// Keeping this logic here (instead of inside the render functions) makes the
// counting and labelling rules directly testable, which is how the
// "99 componenti da installare" regression gets caught: the status text is a
// single localized string, never a number concatenated with another string
// that already contains the number.

const COMPONENT_KEYS = {
  steam: "programs.compSteam",
  lutris: "programs.compLutris",
  heroic: "programs.compHeroic",
  wine: "programs.compWine",
  winetricks: "programs.compWinetricks",
  protontricks: "programs.compProtontricks",
  flatpak: "programs.compFlatpak",
  vulkan64: "programs.compVulkan64",
  vulkan32: "programs.compVulkan32",
  opengl32: "programs.compOpenGL32",
  nvidia32: "programs.compNvidia32",
  geforce_now: "programs.compGeforceNow",
};

const STEP_KEYS = {
  preflight: "programs.stepPreflight",
  add_i386: "programs.stepAddI386",
  enable_multilib: "programs.stepEnableMultilib",
  refresh: "programs.stepRefresh",
  install_packages: "programs.stepInstall",
  install_flatpak: "programs.stepInstallFlatpak",
  add_flatpak_remote: "programs.stepAddFlatpakRemote",
  add_repository: "programs.stepAddRepository",
};

export function componentLabel(component, t) {
  const key = COMPONENT_KEYS[component.id];
  return key ? t(key) : component.name;
}

export function pendingComponents(plan) {
  return (plan?.components || []).filter((component) => component.state === "toInstall");
}

export function installedComponents(plan) {
  return (plan?.components || []).filter((component) => component.state === "installed");
}

export function unavailableComponents(plan) {
  return (plan?.components || []).filter((component) => component.state !== "toInstall" && component.state !== "installed");
}

export function missingCount(plan) {
  return pendingComponents(plan).length;
}

export function statusKey(pending) {
  if (pending <= 0) return "programs.gamingReadyNone";
  if (pending === 1) return "programs.gamingReadyOne";
  return "programs.gamingReadyMany";
}

/// One single localized sentence, e.g. "9 componenti da installare" or
/// "1 componente da installare" or "Sistema già pronto".
export function statusText(plan, t) {
  const pending = missingCount(plan);
  if (pending === 0 && unavailableComponents(plan).length) return t("programs.gamingIncomplete");
  return t(statusKey(pending), { count: pending });
}

export function actionLabel(plan, outcome, t) {
  const pending = missingCount(plan);
  if (pending === 0 && unavailableComponents(plan).length) return t("programs.reviewRequired");
  if (pending === 0) return t("programs.ready");
  if (outcome && outcome.ok === false) return t("programs.retryInstall", { count: pending });
  return t("programs.installCount", { count: pending });
}

/// The "Modifiche necessarie" section only lists system-level changes: the
/// normal application installs are already covered by "Da installare".
export function specialChanges(plan) {
  return (plan?.changes || []).filter((change) =>
    ["addI386", "enableMultilib", "addRepository"].includes(change.kind)
      || (change.kind === "refresh" && pendingComponents(plan).length > 0),
  );
}

/// Warnings shown to the user: component-level availability is already
/// visible in the lists themselves, so those two keys are dropped here
/// instead of being repeated a second time in the notice.
export function warningKeys(plan) {
  const seen = new Set();
  const out = [];
  for (const warning of plan?.warnings || []) {
    if (warning === "componentUnavailable" || warning === "componentUnknown") continue;
    if (seen.has(warning)) continue;
    seen.add(warning);
    out.push(warning);
  }
  return out;
}

/// Human rows for the collapsible technical drawer: friendly name first,
/// then the real package / Flatpak id. The opposite of the main lists,
/// which must never expose package names upfront.
export function technicalRows(plan, t) {
  return pendingComponents(plan).map((component) => ({
    label: componentLabel(component, t),
    detail: (component.detail || []).join(", "),
  }));
}

export function stepLabel(step, t) {
  return t(STEP_KEYS[step?.step] || "programs.stepInstall");
}

export function failedStep(report) {
  return (report?.steps || []).find((step) => !step.ok) || null;
}

export function lastStep(report) {
  const steps = report?.steps || [];
  return steps.length ? steps[steps.length - 1] : null;
}

/// A human message for a failed report: which step failed, which component
/// or package was involved, and what the package manager actually said.
export function errorMessage(report, t) {
  const step = failedStep(report);
  if (!step) {
    const missing = report?.missingPackages || [];
    if (missing.length) return t("programs.errorVerify", { list: missing.join(", ") });
    return t("programs.errorGeneric");
  }
  if (step.step === "preflight") return t("programs.errorBusy");
  if (step.step === "install_packages") {
    return t("programs.errorInstall", { packages: (step.packages || []).join(", ") });
  }
  if (step.step === "install_flatpak") {
    return t("programs.errorFlatpak", { app: (step.flatpakApps || []).join(", ") });
  }
  return t("programs.errorStep", { step: stepLabel(step, t) });
}

/// Rows for the collapsed error drawer: never more than what is needed to
/// understand the failure without flooding the page with terminal output.
export function errorRows(report) {
  return (report?.steps || []).map((step) => ({
    step: step.step,
    operation: step.operation,
    exitCode: step.exitCode,
    stderr: (step.stderrTail || "").split("\n").slice(-4).join("\n"),
    packages: step.packages || [],
    flatpakApps: step.flatpakApps || [],
  }));
}

export function outcomeIsPartial(report) {
  return !!report && report.ok === false;
}
