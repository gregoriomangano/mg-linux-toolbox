// Pure presentation helpers for the five fixed application cards. KDE
// Connect stays native-only (a single "Installa" button); the other four
// let the user choose explicitly between Flatpak and Snap -- never a
// single button that silently picks one for them.
export const APP_IDS = ["gradia", "upscayl", "curtail", "ferdium", "kde-connect"];

const APP_META = {
  gradia: { name: "Gradia", descKey: "apps.descGradia", icon: "app-card.svg" },
  upscayl: { name: "Upscayl", descKey: "apps.descUpscayl", icon: "app-card.svg" },
  curtail: { name: "Curtail", descKey: "apps.descCurtail", icon: "app-card.svg" },
  ferdium: { name: "Ferdium", descKey: "apps.descFerdium", icon: "app-card.svg" },
  "kde-connect": { name: "KDE Connect", descKey: "apps.descKdeConnect", icon: "app-card.svg" },
};

export const appName = (id) => APP_META[id]?.name || id;
export const appDescription = (id, t) => APP_META[id] ? t(APP_META[id].descKey) : "";
export const bundledIconPath = (id) => APP_META[id] ? `assets/apps/${APP_META[id].icon}` : null;

// The list of formats an app is really installed through, e.g. []
// (not installed), ["flatpak"], ["snap"] or ["flatpak","snap"] (both at
// once -- never silently collapsed to just one).
export function installedVias(status) {
  return (status?.installed || []).map((info) => info.via);
}

export function isInstalledVia(status, via) {
  return installedVias(status).includes(via);
}

// One clear status line per real state: reading, unavailable, ready to
// choose a format, installed via exactly one format, or installed via
// both at once (flagged, since that is normally an accident to clean up
// rather than something to hide).
export function statusText(status, t) {
  if (!status) return t("apps.reading");
  const vias = installedVias(status);
  if (vias.length === 2) return t("apps.installedBoth");
  if (vias.includes("flatpak")) return t("apps.installedViaFlatpak");
  if (vias.includes("snap")) return t("apps.installedViaSnap");
  if (vias.includes("native")) return t("apps.installedBadge");
  return status.methods?.length ? t("apps.readyToInstall") : t("apps.notAvailable");
}

export function vulkanNote(status, t) {
  return status?.vulkanUncertain ? t("apps.vulkanNote") : null;
}

export function methodOffer(status, method) {
  return (status?.methods || []).find((offer) => offer.method === method) || null;
}

// The one discreet note Curtail's Snap choice carries: it is published by
// a third party (Sameer Sharma), not by upstream author Hugo Posnic. Never
// blocks the choice, only informs it.
export function communityNote(offer, t) {
  return offer?.community ? t("apps.snapCommunityNote") : null;
}

// ---------------------------------------------------------------------
// Confirmation copy for each install button, chosen from the shared,
// backend-resolved environment (`flatpakPlan`/`snapPlan`) -- the frontend
// never derives any of this distro/runtime logic itself, it only formats
// the fixed strings the backend's plan value points at.
// ---------------------------------------------------------------------

export function flatpakConfirmText(env, appLabel, t) {
  switch (env?.flatpakPlan) {
    case "needs_flathub":
      return t("apps.confirmFlathubMissing", { app: appLabel });
    case "needs_install":
      return t("apps.confirmFlatpakMissing", { app: appLabel });
    default:
      return "";
  }
}

export function snapConfirmText(env, appLabel, t) {
  switch (env?.snapPlan) {
    case "needs_activation":
      return t("apps.confirmSnapInactive", { app: appLabel });
    case "needs_bootstrap":
      return t("apps.confirmSnapMissing", { app: appLabel });
    case "mint_nosnap":
      return t("apps.confirmSnapMint");
    case "opensuse_repo":
      return t("apps.confirmSnapOpensuse");
    default:
      return "";
  }
}

// `true` when clicking the button can go straight to installing, with no
// confirmation dialog at all (the runtime is already fully ready).
export function isPlanReady(plan) {
  return plan === "ready";
}

// `true` when the format cannot be offered at all on this system: the
// button must show the fixed unsupported message and never attempt an
// install (Arch without snapd already present, or an unrecognised distro).
export function isPlanBlocked(plan) {
  return plan === "arch_blocked" || plan === "unsupported";
}

export function blockedPlanText(plan, t) {
  return plan === "arch_blocked" ? t("apps.snapArchBlocked") : t("apps.formatUnsupported");
}

const ERROR_CODE_KEYS = {
  authorization_cancelled: "programs.errorCancelled",
  authorization_unavailable: "programs.errorAuthUnavailable",
  polkit_unavailable: "programs.errorAuthUnavailable",
  helper_missing: "programs.errorHelperMissing",
  invalid_app: "apps.errorGeneric",
  invalid_method: "apps.errorGeneric",
  app_not_installed: "apps.errorNotInstalled",
  app_binary_not_found: "apps.errorBinaryNotFound",
  snap_unsupported: "apps.formatUnsupported",
  permission_failed: "apps.permissionFailed",
};

export function errorCodeKey(errorCode) {
  return ERROR_CODE_KEYS[errorCode] || null;
}

export function outcomeMessage(report, errorCode, t, operation = "install") {
  const errorKey = errorCodeKey(errorCode);
  if (errorKey) return t(errorKey);
  if (!report) return t(operation === "remove" ? "apps.errorRemove" : operation === "open" ? "apps.errorOpen" : "apps.errorGeneric");
  if (report.ok) return t("apps.operationComplete");
  const failed = (report.steps || []).find((step) => !step.ok);
  const detail = (failed?.stderrTail || failed?.stdoutTail || "").trim();
  const key = operation === "remove" ? "apps.errorRemove" : operation === "open" ? "apps.errorOpen" : "apps.errorGeneric";
  return detail ? `${t(key)}: ${detail}` : t(key);
}

// Optional Snap permissions this catalog ever offers, and only ever as an
// explicit, separate choice after install -- never granted automatically.
// Only Upscayl's is actionable (a real `snap connect`, gated by its own
// allow-listed backend operation); Ferdium's is informational only, per
// its Snap package's own documented (not auto-connected) interfaces.
export function upscaylRemovableMediaOffer(id) {
  return id === "upscayl" ? { permission: "removable-media" } : null;
}

export function ferdiumPermissionNote(id, t) {
  return id === "ferdium" ? t("apps.permissionFerdiumNote") : null;
}

// The small, discreet "Supporto applicazioni" line near the Programs
// header: only ever reflects `get_apps_environment`'s own booleans, never
// configures anything from here.
export function supportSummary(env, t) {
  if (!env) return "";
  const flatpak = env.flatpakInstalled ? t("apps.supportFlatpakReady") : t("apps.supportFlatpakMissing");
  const snap = env.snapActive ? t("apps.supportSnapReady") : t("apps.supportSnapMissing");
  return `${flatpak} · ${snap}`;
}
