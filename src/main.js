import { applyTranslations, getLanguage, onLanguageChange, setLanguage, t } from "./js/i18n.js";
import { actionLabel, componentLabel, errorMessage, errorRows, failedStep, installedComponents, outcomeIsPartial, pendingComponents, specialChanges, statusText, stepLabel, technicalRows, unavailableComponents, warningKeys } from "./js/gaming-view.js";
import { aiNextRefreshDelayMs, aiSnapshotIsStale } from "./js/ai-refresh-policy.js";
import { friendlyCpuDriver, friendlyEpp, friendlyGovernor, performancePressureBand, quotaLevel, validHttpUrl } from "./js/ui-utils.js";
import { AUTHOR, externalLink } from "./js/author.js";
import {
  appDescription,
  appName,
  blockedPlanText,
  bundledIconPath,
  communityNote,
  ferdiumPermissionNote,
  flatpakConfirmText,
  installedVias,
  isPlanBlocked,
  isPlanReady,
  methodOffer,
  outcomeMessage,
  snapConfirmText,
  statusText as appsStatusText,
  supportSummary,
  upscaylRemovableMediaOffer,
  vulkanNote,
} from "./js/apps-view.js";
import { DEFAULT_TEXT_SCALE, TEXT_SCALE_OPTIONS, loadTextScale, saveTextScale, textScalePercentLabel } from "./js/appearance-view.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const $ = (id) => document.getElementById(id);
const state = {
  snapshot: null, ai: null, feeds: [], news: null, updater: null,
  ping: null, performance: null, performanceControls: {}, advanced: null, advancedBusyId: null, inhibit: null, claudeAuth: null, claudeReconnectBusy: false, page: "overview", performanceBusy: false, inhibitBusy: false, aiBusy: false, rssBusy: false, updateBusy: false,
  software: null, softwareLoading: false, softwareBusyId: null,
  autostart: null, autostartLoading: false, autostartBusyId: null, autostartSearch: "", autostartFilter: "all", autostartManagerOpen: false,
  cleanup: null, cleanupLoading: false, cleanupExpanded: false, cleanupBusy: false, cleanupOutcome: null,
  cleanupSelection: new Set(), cleanupCookieSelection: new Set(),
  startup: null, startupBusy: false,
  gaming: null, gamingError: null, gamingLoading: false, gamingOpen: false, gamingBusy: false, gamingOutcome: null, gamingProgress: [],
  geforce: null, geforceBusy: false,
  dns: null, dnsError: null, dnsLoading: false, dnsOpen: false, dnsBusy: false, dnsSelection: null,
  winboat: null, winboatError: null, winboatLoading: false, winboatOpen: false, winboatBusy: false, winboatOutcome: null, winboatProgress: [],
  apps: null, appsError: null, appsLoading: false, appsBusyId: null, appsOutcomes: {}, appsPendingConfirm: null,
  appsEnvironment: null, appsMoreOptions: new Set(), appsConfirmInfoOnly: false,
  appsSnapPermissionOffer: new Set(), appsSnapPermNote: new Set(),
  advancedHelpId: null,
};
let advancedHelpOpener=null;
let aiTimer;
let rssTimer;
let pingTimer;
let updateTimer;
let performanceTimer;
let systemTimer;
let aiRefreshFlight=null;
const RSS_REFRESH_SECONDS=3600;

const locale = () => getLanguage() === "en" ? "en-GB" : "it-IT";
const formatBytes = (input) => {
  if (!Number.isFinite(input)) return null;
  let value=input; const units=["B","KB","MB","GB","TB"]; let index=0;
  while(value>=1024 && index<units.length-1){value/=1024;index++;}
  return value.toLocaleString(locale(),{maximumFractionDigits:value<10&&index>0?1:0})+" "+units[index];
};
function element(tag,className,text) {
  const value=document.createElement(tag);
  if(className)value.className=className;
  if(text!=null)value.textContent=text;
  return value;
}
function icon(name) {
  const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
  svg.classList.add("pen-icon"); svg.setAttribute("aria-hidden","true");
  const use=document.createElementNS(svg.namespaceURI,"use");
  use.setAttribute("href","assets/pen-icons.svg#"+name); svg.append(use); return svg;
}
function setText(id,value) {
  const target=$(id); if(!target)return;
  target.hidden=value==null||value===""; if(value!=null)target.textContent=value;
}
function setProgress(value,id,level) {
  const bar=id?$(id):element("span");
  const safe=Number.isFinite(value)?Math.max(0,Math.min(100,value)):0;
  bar.style.width=safe+"%";
  if(level)bar.dataset.level=level; else delete bar.dataset.level;
  if(id)return;
  const track=element("div","progress"); track.append(bar); return track;
}
function updateClock() {
  const now=new Date();
  setText("hero-date",new Intl.DateTimeFormat(locale(),{weekday:"short",day:"numeric",month:"short",year:"numeric"}).format(now));
  setText("hero-time",new Intl.DateTimeFormat(locale(),{timeStyle:"short"}).format(now));
  $("hero-time").dateTime=now.toISOString();
}
function updateDisks(disks) {
  $("disk-list").replaceChildren(...disks.map(d=>{
    const row=element("article","disk-item");
    const name=element("div");
    name.append(element("strong","",d.name|| (d.mountpoint==="/"?t("disk.linuxRoot"):d.mountpoint)),element("small","",d.mountpoint?d.device:t("disk.unmounted")));
    const fs=element("div","",d.filesystem);
    const space=element("div","disk-space");
    space.append(element("p","",formatBytes(d.used_bytes)+" / "+formatBytes(d.total_bytes)),setProgress(d.used_percent));
    row.append(name,fs,space); return row;
  }));
}
function renderSnapshot() {
  const s=state.snapshot; if(!s)return;
  const cpuUsage=Number.isFinite(s.cpu.usage_percent)?Math.round(s.cpu.usage_percent)+"%":"—";
  setText("cpu-usage",cpuUsage); setProgress(s.cpu.usage_percent,"cpu-progress");
  setText("cpu-frequency",s.cpu.frequency_mhz?(s.cpu.frequency_mhz/1000).toLocaleString(locale(),{maximumFractionDigits:2})+" GHz":null);
  setText("cpu-cores",t("common.cores",{cores:s.cpu.cores,threads:s.cpu.threads}));
  setText("cpu-model",s.cpu.model_name); $("cpu-model").title=s.cpu.model_name||"";
  setText("memory-usage",Math.round(s.memory.used_percent)+"%"); setProgress(s.memory.used_percent,"memory-progress");
  setText("memory-detail",formatBytes(s.memory.used_bytes)+" / "+formatBytes(s.memory.total_bytes));
  $("disk-card").hidden=!s.root_disk;
  if(s.root_disk){
    setText("disk-usage",Math.round(s.root_disk.used_percent)+"%"); setProgress(s.root_disk.used_percent,"disk-progress");
    setText("disk-detail",formatBytes(s.root_disk.used_bytes)+" / "+formatBytes(s.root_disk.total_bytes));
  }
  $("gpu-card").hidden=!s.gpu;
  if(s.gpu){
    setText("gpu-name",s.gpu.name); $("gpu-name").title=s.gpu.name;
    setText("gpu-driver",s.gpu.driver?t("common.driver",{value:s.gpu.driver}):null);
  }
  setText("network-status",s.network.connected?t("network.connected"):t("network.disconnected"));
  $("network-status").classList.toggle("connected",s.network.connected);
  setText("network-interface",s.network.interface);
  setText("network-down",s.network.download_bytes_per_second==null?null:formatBytes(s.network.download_bytes_per_second)+"/s");
  setText("network-up",s.network.upload_bytes_per_second==null?null:formatBytes(s.network.upload_bytes_per_second)+"/s");
  const showPing=s.network.connected&&Number.isFinite(state.ping);
  $("network-ping-row").hidden=!showPing; if(showPing)setText("network-ping",state.ping.toLocaleString(locale(),{maximumFractionDigits:1})+" ms");
  $("process-list").replaceChildren(...s.processes.map(p=>{
    const row=element("div","process-row");
    const name=element("span","process-name",p.name); name.title=p.name;
    const cpu=Number.isFinite(p.cpu_percent)?p.cpu_percent.toLocaleString(locale(),{maximumFractionDigits:1})+"%":"—";
    row.append(name,element("span","",cpu),element("span","",formatBytes(p.memory_bytes)));
    return row;
  }));
  const rows=[
    [t("status.os"),s.distribution],[t("status.kernel"),s.kernel],
    [t("status.root"),s.root_disk?t("status.free",{value:formatBytes(s.root_disk.free_bytes)}):null],
    ...s.temperatures.map(value=>[t("status.cpuTemperature"),value.value.toLocaleString(locale(),{maximumFractionDigits:1})+" °C"]),
    ...s.fans.filter(value=>value.value>0).map(value=>[t("status.fan",{name:value.name}),Math.round(value.value)+" RPM"]),
    ...s.power.map(value=>[t("status.power"),[value.status,value.percentage!=null?value.percentage+"%":null].filter(Boolean).join(" · ")])
  ];
  $("status-list").replaceChildren(...rows.filter(([,value])=>value).flatMap(([label,value])=>[element("dt","",label),element("dd","",value)]));
  updateDisks(s.disks);
}
function profileHelp(kind) {
  const field=(label,value)=>[element("dt","",t(label)),element("dd","",value)];
  const details=element("details");const summary=element("summary","",t("performance.whatChanges"));details.append(summary);
  const list=element("dl");
  list.append(...field("performance.what",t(`performance.profile.${kind}What`)),...field("performance.notice",t(`performance.profile.${kind}Notice`)),...field("performance.heat",t(`performance.profile.${kind}Heat`)),...field("performance.safe",t("performance.profile.safe")));
  details.append(list);return details;
}
function setHealth(id,label,detail) { const indicator=$(id);indicator.hidden=label==null; if(label!=null){indicator.querySelector("strong").textContent=label;indicator.querySelector("small").textContent=detail||"";} }
function pressureLabel(value,kind) { const band=performancePressureBand(value);return band?t(`performance.${kind}.${band}`):null; }
function localizedPerformanceValue(value,key) {
  if(value==null||value==="")return null;
  if(value==="<multiple>")return t("performance.mixed");
  return key?t("performance."+key):value;
}
function formatGhz(mhz) { return (mhz/1000).toLocaleString(locale(),{maximumFractionDigits:2}); }
function renderCpuDetails(snapshot) {
  const governor=snapshot.governor?.current;
  $("performance-governor-label").hidden=!governor;
  setText("performance-governor-value",localizedPerformanceValue(governor,friendlyGovernor(governor)));
  const epp=snapshot.epp?.current;
  $("performance-epp-label").hidden=!epp;
  const eppValue=localizedPerformanceValue(epp,friendlyEpp(epp));
  setText("performance-epp-value",eppValue);
  if(epp&&eppValue!=null)$("performance-epp-value").title=epp;
  const limits=snapshot.frequencyLimits;
  $("performance-range-label").hidden=!limits;
  setText("performance-range-value",limits?t("performance.range",{min:formatGhz(limits.minimumMhz),max:formatGhz(limits.maximumMhz)}):null);
  const degraded=snapshot.powerManager?.degraded;
  const degradedNote=$("performance-degraded-note");
  degradedNote.hidden=!degraded;
  if(degraded)degradedNote.textContent=degraded==="high-operating-temperature"?t("performance.degradedTemperature"):t("performance.degradedGeneric");
}
// Six optional, backend-detected features shown as compact decision cards
// (name, tri-state status, one-line description, action) -- every longer
// explanation (what it is, when it helps, when to leave it off, what it
// really changes, the on-this-computer recommendation and the raw technical
// state) lives only in the "Scopri di più" help dialog, never permanently
// expanded inside the card. See openAdvancedHelp/updateAdvancedHelpDynamic.
function controlIcon(id) {
  return {autogroup:"software",thp:"memory",ksm:"processes",zswap:"disk",inhibit:"status",fstrim:"restore"}[id]||"settings";
}
// Inhibit is detected through a different command (get_inhibit_status) and
// kept in its own state slot, but it is displayed and evaluated exactly like
// the five backend-driven controls: this turns it into the same shape so a
// single render/help path can serve all six without duplicating markup.
function inhibitAsControl() {
  const status=state.inhibit;
  if(!status?.available)return {id:"inhibit",enabled:false,writable:false,state:"unavailable",note:null,managed:false,owner:null};
  return {id:"inhibit",enabled:!!status.active,writable:true,state:status.active?"active":"disabled",note:null,managed:false,owner:null};
}
function resolveHelpControl(id) {
  if(id==="inhibit")return inhibitAsControl();
  return (state.advanced||[]).find(c=>c.id===id)||{id,enabled:false,writable:false,state:"unavailable",note:null,managed:false,owner:null};
}
// Three states only, shown as a dot + word on the card and in the dialog:
// on (state==="active"), off (writable but currently off, incl. fstrim's
// "recommended" state), or a single dash covering every case the Toolbox
// cannot or should not toggle (managed/system-managed/incompatible/
// unavailable) -- the exact reason for the dash lives in the help dialog.
function controlStatusKind(control) {
  return control.state==="active"?"on":control.writable?"off":"dash";
}
// One evaluative tone, shared by the card's single optional badge and the
// dialog's "recommend it on this computer?" verdict -- never both a badge
// and a contradictory recommendation. `null` means the Toolbox cannot state
// this with sense (transient/system-managed/unavailable), and the dialog
// then falls back to "Da valutare in base all'uso.".
function controlEvaluation(control) {
  switch(control.id){
    case "autogroup": return control.writable?"recommended":null;
    case "thp": return control.writable?"situational":null;
    case "ksm": return control.writable?"situational":null;
    case "zswap":
      if(control.writable)return "situational";
      if(control.note==="zswap-zram"||control.note==="zswap-no-swap")return "notNeeded";
      return null;
    case "inhibit": return control.writable?"situational":null;
    case "fstrim":
      if(control.writable)return "recommended";
      if(control.note==="fstrim-not-needed"||control.note==="fstrim-continuous-discard")return "notNeeded";
      return null;
    default: return null;
  }
}
const EVALUATION_BADGE_KEY={recommended:"performance.badgeRecommended",situational:"performance.badgeSituational",notNeeded:"performance.badgeNotNeeded"};
const EVALUATION_VERDICT_KEY={recommended:"performance.verdictYes",situational:"performance.verdictSituational",notNeeded:"performance.verdictNotNeeded"};
// The specific, real reason behind the verdict: the same per-control
// sentence used for "recommended"/"situational", the matching detected-
// condition note for "notNeeded" (already used to explain that condition
// elsewhere), or -- when nothing can be claimed -- the real reason the
// Toolbox already knows (system-managed, a transient kernel state, another
// component owning it, or the kernel not exposing this at all).
function controlReasonKey(control,evaluation) {
  if(evaluation==="recommended"||evaluation==="situational")return `performance.reco.${control.id}`;
  if(evaluation==="notNeeded"){
    return {
      "zswap-zram":"performance.control.zswap.zram",
      "zswap-no-swap":"performance.control.zswap.noSwap",
      "fstrim-not-needed":"performance.control.fstrim.notNeeded",
      "fstrim-continuous-discard":"performance.control.fstrim.managed",
    }[control.note]||"performance.recommendationUnknown";
  }
  if(control.note==="thp-always")return "performance.control.thp.always";
  if(control.note==="ksm-unmerge")return "performance.control.ksm.unmerge";
  if(control.managed)return "performance.controlManagedHint";
  if(control.state==="unavailable")return "performance.controlUnavailableHint";
  return "performance.recommendationUnknown";
}
// The literal, technical detected value -- moved out of the card entirely
// and shown only under "Dettagli tecnici" in the help dialog.
function technicalStateValue(control) {
  switch(control.id){
    case "thp":
      if(control.note==="thp-always")return "always";
      if(control.state==="unavailable")return "—";
      return control.enabled?"madvise":"never";
    case "ksm":
      if(control.note==="ksm-unmerge")return "2";
      if(control.state==="unavailable")return "—";
      return control.enabled?"1":"0";
    case "zswap":
      if(control.state==="unavailable")return "—";
      return control.enabled?"Y":"N";
    case "autogroup":
      if(control.state==="unavailable")return "—";
      return control.enabled?"1":"0";
    case "fstrim":
      if(control.state==="unavailable")return "—";
      return t(control.enabled?"performance.techEnabled":"performance.techDisabled");
    case "inhibit":
      return t(control.enabled?"performance.techEnabled":"performance.techDisabled");
    default: return "—";
  }
}
function controlOwnerLabel(control) {
  return control.owner==="system"?t("performance.controlSystem"):control.owner;
}
function isControlBusy(id) {
  return id==="inhibit"?state.inhibitBusy:state.advancedBusyId===id;
}
function toggleControl(id,enabled) {
  return id==="inhibit"?toggleInhibit():toggleAdvanced(id,enabled);
}
// The card itself: icon+name, a tri-state status line, at most one badge,
// one short description and two actions. Nothing here ever expands: every
// longer explanation lives only in the help dialog opened by "Scopri di più".
function buildFeatureCard(control) {
  const busy=isControlBusy(control.id);
  const kind=controlStatusKind(control);
  const card=element("article","data-panel perf-feature-card");
  card.dataset.control=control.id;
  card.dataset.status=kind;
  const head=element("div","perf-feature-head");
  head.append(icon(controlIcon(control.id)),element("h3","",t(`performance.control.${control.id}.title`)));
  const statusLine=element("p","perf-feature-status");
  statusLine.append(element("span",`perf-status-dot ${kind}`),document.createTextNode(t(kind==="on"?"performance.cardStateOn":kind==="off"?"performance.cardStateOff":"performance.cardStateDash")));
  const evaluation=controlEvaluation(control);
  card.append(head,statusLine);
  if(evaluation)card.append(element("span",`perf-feature-badge ${evaluation}`,t(EVALUATION_BADGE_KEY[evaluation])));
  card.append(element("p","perf-feature-desc",t(`performance.control.${control.id}.text`)));
  const actions=element("div","perf-feature-actions");
  if(control.writable){
    const toggleButton=element("button","",busy?t("performance.applying"):t(control.enabled?"performance.controlDisable":"performance.controlEnable"));
    toggleButton.type="button";toggleButton.disabled=busy;
    if(control.enabled)toggleButton.classList.add("active");
    toggleButton.setAttribute("aria-pressed",String(!!control.enabled));
    if(control.id==="inhibit")toggleButton.id="inhibit-toggle";
    toggleButton.addEventListener("click",()=>toggleControl(control.id,!control.enabled));
    actions.append(toggleButton);
  }
  const moreButton=element("button","secondary-action perf-feature-more",t("performance.learnMore"));
  moreButton.type="button";
  moreButton.addEventListener("click",()=>openAdvancedHelp(control.id,moreButton));
  actions.append(moreButton);
  card.append(actions);
  return card;
}
function renderAdvancedControls() {
  const grid=$("advanced-grid");
  const controls=[...(state.advanced||[]),inhibitAsControl()];
  grid.replaceChildren(...controls.map(buildFeatureCard));
  $("performance-advanced").hidden=!state.advanced;
  updateAdvancedHelpDynamic();
}
function helpSection(titleText,contentNode) {
  const section=element("section","help-block");
  section.append(element("h3","",titleText),contentNode);
  return section;
}
function helpBulletList(items) {
  const list=element("ul","help-bullets");
  list.append(...items.map(text=>element("li","",text)));
  return list;
}
const HELP_USEFUL_ITEMS={
  autogroup:["desktop","conversions","compiling","compression","cpuBusy"],
  thp:["vms","memoryHeavy","games"],
  ksm:["multipleVms","duplicatedMemory"],
  zswap:["diskSwap","ramPressure","fewerWrites"],
  fstrim:["ssdNvme","compatibleFs","noOtherMechanism"],
  inhibit:["presentations","downloads","conversions","processing","video"],
};
const HELP_OFF_ITEMS={
  autogroup:["servers","batch","cgroupManaged"],
  thp:["databases","specificConfig","noBenefit"],
  ksm:["normalDesktop","noVms","notWorthCpu"],
  zswap:["noSwap","zramRedundant","managedDifferently"],
  fstrim:["hdd","noDiscard","alreadyManaged"],
  inhibit:["normalUse"],
};
// Builds the full, mostly static help content for one feature (title, icon,
// what it is, when it helps, when to leave it off, what it really changes,
// technology name) once, when the dialog is opened or the language changes.
// The few values that can change between the page's periodic refreshes
// (current state, badge, reason, action button, detected technical state)
// get stable ids here and are the ONLY thing updateAdvancedHelpDynamic()
// ever touches afterwards -- the dialog is never torn down or rebuilt by
// a background refresh, so it can never appear to "close itself".
function openAdvancedHelp(id,opener) {
  state.advancedHelpId=id;
  if(opener)advancedHelpOpener=opener;
  $("advanced-help-icon-use").setAttribute("href",`assets/pen-icons.svg#${controlIcon(id)}`);
  setText("advanced-help-title",t(`performance.control.${id}.title`));
  const sections=[
    helpSection(t("performance.help.whatIsTitle"),element("p","",t(`performance.help.${id}.what`))),
    helpSection(t("performance.help.usefulTitle"),helpBulletList((HELP_USEFUL_ITEMS[id]||[]).map(key=>t(`performance.help.${id}.useful.${key}`)))),
    helpSection(t("performance.help.offTitle"),helpBulletList((HELP_OFF_ITEMS[id]||[]).map(key=>t(`performance.help.${id}.off.${key}`)))),
    helpSection(t("performance.help.changesTitle"),element("p","",t(`performance.help.${id}.changes`))),
  ];
  const recommendSection=element("section","help-block help-recommend");
  recommendSection.append(element("h3","",t("performance.help.recommendTitle")));
  const verdictLine=element("p","help-recommend-line");
  const verdictIcon=element("span","perf-rec-icon");verdictIcon.id="advanced-help-verdict-icon";
  const verdictText=element("strong","");verdictText.id="advanced-help-verdict-text";
  verdictLine.append(verdictIcon,document.createTextNode(" "),verdictText);
  recommendSection.append(verdictLine);
  const reasonLine=element("p","help-recommend-reason");
  reasonLine.append(element("strong","",t("performance.help.reasonLabel")+" "));
  const reasonText=element("span","");reasonText.id="advanced-help-reason";
  reasonLine.append(reasonText);
  recommendSection.append(reasonLine);
  sections.push(recommendSection);
  const techSection=element("section","help-block help-tech");
  techSection.append(element("h3","",t("performance.help.techTitle")));
  const dl=element("dl");
  dl.append(element("dt","",t("performance.help.techName")),element("dd","",t(`performance.control.${id}.tech`)));
  const techStateDd=element("dd","");techStateDd.id="advanced-help-tech-state";
  dl.append(element("dt","",t("performance.help.techState")),techStateDd);
  techSection.append(dl);
  sections.push(techSection);
  $("advanced-help-body").replaceChildren(...sections);
  const actionButton=$("advanced-help-action");
  actionButton.onclick=()=>toggleControl(id,!resolveHelpControl(id).enabled);
  updateAdvancedHelpDynamic();
  const dialog=$("advanced-help-dialog");
  if(!dialog.open)dialog.showModal();
}
// Runs on every render of the six cards (page load, toggle, periodic
// refresh, language change): if the help dialog is open, it re-reads only
// the small set of values that can actually change (state dot/word, badge,
// reason, action button, technical state) and writes them in place. It
// never rebuilds the dialog's body, never calls showModal()/close(), and
// never touches scroll or focus -- a background refresh can update the
// numbers but can never close or reset the panel the user is reading.
function updateAdvancedHelpDynamic() {
  if(!state.advancedHelpId)return;
  const control=resolveHelpControl(state.advancedHelpId);
  const kind=controlStatusKind(control);
  const evaluation=controlEvaluation(control);
  const verdictIcon=$("advanced-help-verdict-icon");
  if(verdictIcon)verdictIcon.className=`perf-rec-icon ${evaluation||"dash"}`;
  setText("advanced-help-verdict-text",t(evaluation?EVALUATION_VERDICT_KEY[evaluation]:"performance.recommendationUnknown"));
  setText("advanced-help-reason",t(controlReasonKey(control,evaluation),{owner:controlOwnerLabel(control)}));
  setText("advanced-help-tech-state",technicalStateValue(control));
  const actionButton=$("advanced-help-action");
  if(actionButton){
    const busy=isControlBusy(control.id);
    actionButton.hidden=!control.writable;
    actionButton.disabled=busy;
    actionButton.textContent=busy?t("performance.applying"):t(control.enabled?"performance.controlDisable":"performance.controlEnable");
  }
}
async function toggleAdvanced(id,enabled) {
  if(state.advancedBusyId)return;
  state.advancedBusyId=id;renderAdvancedControls();
  try{state.advanced=await invoke("set_advanced_control",{id,enabled});}
  catch(error){console.error("Advanced control request failed",error);await loadAdvanced();}
  finally{state.advancedBusyId=null;renderAdvancedControls();}
}
async function loadAdvanced() {
  try{state.advanced=await invoke("get_advanced_controls");}
  catch(error){console.error("Advanced control detection failed",error);state.advanced=[];}
  renderAdvancedControls();
}
function renderPerformance() {
  const snapshot=state.performance;if(!snapshot)return;
  $("performance-loading").hidden=true;
  for(const help of document.querySelectorAll("[data-profile-help]"))help.replaceChildren(profileHelp(help.dataset.profileHelp));
  setText("performance-driver-value",friendlyCpuDriver(snapshot.cpuDriver)||t("common.unknown"));
  renderCpuDetails(snapshot);
  const managerWarning=$("performance-manager-warning");
  managerWarning.hidden=!snapshot.powerManager?.detected;
  if(snapshot.powerManager?.detected)managerWarning.textContent=t(snapshot.powerManager.integrated?"performance.managedByPpd":"performance.managerConflict",{name:snapshot.powerManager.name});
  const cpuPsi=snapshot.cpuPsi?.someAvg10,ioPsi=snapshot.ioPsi?.someAvg10;
  const cpuLoad=state.snapshot?.cpu?.usage_percent;
  setHealth("health-cpu",pressureLabel(cpuPsi,"cpuState"),Number.isFinite(cpuPsi)?t("performance.cpuLiveDetail",{load:Number.isFinite(cpuLoad)?Math.round(cpuLoad):"—",wait:cpuPsi.toLocaleString(locale(),{maximumFractionDigits:2})}):t("common.unknown"));
  setHealth("health-io",pressureLabel(ioPsi,"diskState"),Number.isFinite(ioPsi)?t("performance.diskLiveDetail",{value:ioPsi.toLocaleString(locale(),{maximumFractionDigits:2})}):t("common.unknown"));
  const thermal=snapshot.thermal;
  const temperature=thermal?.temperatureCelsius;
  const throttle=thermal?.throttleEvents;
  const thermalDetail=throttle===0?t("performance.noThermalLimit"):Number.isFinite(throttle)?t("performance.throttle",{count:throttle.toLocaleString(locale())}):null;
  setHealth("health-thermal",Number.isFinite(temperature)?`${temperature.toLocaleString(locale(),{maximumFractionDigits:1})} °C`:t("common.unknown"),thermalDetail);
  const frequency=snapshot.currentFrequency;
  $("health-speed").hidden=!frequency;
  if(frequency){setText("cpu-speed-value",(frequency.averageMhz/1000).toLocaleString(locale(),{maximumFractionDigits:2})+" GHz");setText("cpu-speed-detail",frequency.minimumMhz===frequency.maximumMhz?t("performance.cpuSpeedSingle"):t("performance.cpuSpeedRange",{min:(frequency.minimumMhz/1000).toLocaleString(locale(),{maximumFractionDigits:2}),max:(frequency.maximumMhz/1000).toLocaleString(locale(),{maximumFractionDigits:2})}));}
  renderAdvancedControls();
}
async function loadPerformance() {
  if(state.performanceBusy)return;
  state.performanceBusy=true;$("performance-loading").hidden=false;
  try { state.performance=await invoke("get_performance_snapshot");renderPerformance();await Promise.allSettled([loadPerformanceControls(),loadAdvanced(),loadInhibit()]); }
  catch(error) { console.error("Performance capability detection failed",error);setText("performance-loading",t("common.unknown")); }
  finally { state.performanceBusy=false;renderPerformanceControls(); }
}
function renderPerformanceControls(){for(const card of document.querySelectorAll("[data-profile]")){const control=state.performanceControls[card.dataset.profile],label=card.querySelector(".profile-state");card.classList.remove("active","unavailable","working");if(state.performanceBusy){card.classList.add("working");label.textContent=t("performance.applying")}else if(!control){label.textContent=t("performance.checking")}else if(control.backend==="blocked"){card.classList.add("unavailable");label.textContent=t("performance.managed",{name:control.externalManager})}else if(!control.helperAvailable||!control.available){card.classList.add("unavailable");label.textContent=t(control.helperAvailable?"performance.unavailable":"performance.helperMissing")}else if(control.active){card.classList.add("active");label.textContent=t("performance.active")}else label.textContent=t("performance.available")}}
async function loadPerformanceControls(){const profiles=["performance","balanced","saving"];try{const values=await Promise.all(profiles.map(async profile=>[profile,await invoke("get_performance_control_status",{profile})]));state.performanceControls=Object.fromEntries(values);$("performance-restore").hidden=!values.some(([,control])=>control.snapshotAvailable)}catch(error){console.error("Performance control detection failed",error)}renderPerformanceControls()}
async function refreshPerformanceLive(){if(state.page!=="performance"||state.performanceBusy||document.hidden)return;try{state.performance=await invoke("get_performance_snapshot");renderPerformance()}catch(error){console.error("Performance live refresh failed",error)}
  // power-profiles-daemon can be changed from outside the Toolbox at any time (GNOME Quick
  // Settings, powerprofilesctl, another app), so the three cards are re-checked on the same
  // tick to stay in sync with that single source of truth instead of only refreshing on
  // page-enter or after our own apply/restore.
  await loadPerformanceControls();
}
async function loadInhibit(){try{state.inhibit=await invoke("get_inhibit_status");}catch(error){console.error("Inhibit capability detection failed",error);state.inhibit={available:false,active:false};}renderAdvancedControls();}
async function toggleInhibit(){if(state.inhibitBusy||!state.inhibit?.available)return;state.inhibitBusy=true;renderAdvancedControls();try{state.inhibit=await invoke("set_inhibit_active",{active:!state.inhibit.active})}catch(error){console.error("Inhibit request failed",error)}finally{state.inhibitBusy=false;renderAdvancedControls()}}
async function applyPerformanceProfile(profile){if(state.performanceBusy)return;const control=state.performanceControls[profile];if(!control?.helperAvailable||!control.available||control.backend==="blocked")return;state.performanceBusy=true;renderPerformanceControls();try{state.performance=await invoke("apply_performance_profile",{profile});renderPerformance();if(control.backend==="sysfs")$("performance-restore").hidden=false;await loadPerformanceControls()}catch(error){console.error("Performance profile failed",error)}finally{state.performanceBusy=false;renderPerformanceControls()}}
async function restorePerformanceState(){if(state.performanceBusy)return;state.performanceBusy=true;renderPerformanceControls();try{state.performance=await invoke("restore_previous_performance_state");renderPerformance();$("performance-restore").hidden=true;await loadPerformanceControls()}catch(error){console.error("Performance restore failed",error)}finally{state.performanceBusy=false;renderPerformanceControls()}}
function repoStateLabel(repo) {
  if(repo.note==="manual")return t("software.stateManual");
  if(repo.note==="incompatible")return t("software.stateIncompatible");
  return t(repo.enabled?"software.stateActive":"software.stateDisabled");
}
function repoSubtitle(repo) {
  const known={"apt.official":"software.official","apt.security":"software.security"}[repo.subtitle];
  return known?t(known):repo.subtitle;
}
function repoDetails(repo) {
  const dl=element("dl","repo-details-list");
  const row=(label,value)=>{if(!value)return;dl.append(element("dt","",t(label)),element("dd","",value));};
  row("software.detailBackend",repo.backend.toUpperCase());
  row("software.detailFormat",t(repo.format==="sources"?"software.formatSources":"software.formatList"));
  row("software.detailFile",repo.file);
  row("software.detailTypes",repo.kind);
  row("software.detailUris",repo.uris.filter(Boolean).join(", "));
  row("software.detailSuites",repo.suites.filter(Boolean).join(", "));
  row("software.detailComponents",repo.components.filter(Boolean).join(", "));
  if(repo.architectures.length)row("software.detailArch",repo.architectures.join(", "));
  if(repo.signedBy)row("software.detailSignedBy",repo.signedBy==="embedded"?t("software.signedByEmbedded"):repo.signedBy);
  const details=element("details","repo-details");
  const summary=element("summary","",t("software.details"));
  details.append(summary,dl);
  return details;
}
function renderSoftware() {
  const list=$("repo-list"),repos=state.software||[];
  list.replaceChildren(...repos.map(repo=>{
    const busy=state.softwareBusyId===repo.id;
    const card=element("article","data-panel repo-card");
    card.dataset.repoId=repo.id;
    const head=element("div","repo-card-head");
    head.append(element("strong","repo-name",repo.name));
    const subtitle=repoSubtitle(repo);
    if(subtitle)head.append(element("small","repo-subtitle",subtitle));
    card.append(head,element("span","repo-status "+(repo.enabled?"on":"off"),repoStateLabel(repo)));
    const actions=element("div","repo-actions");
    if(repo.writable){
      const toggle=element("button","",busy?t("performance.applying"):t(repo.enabled?"software.disable":"software.enable"));
      toggle.type="button";toggle.disabled=busy;
      toggle.addEventListener("click",()=>toggleRepo(repo));
      actions.append(toggle);
    }
    card.append(actions,repoDetails(repo));
    if(repo.hasBackup){
      const restore=element("button","performance-restore",t("software.restore"));
      restore.type="button";restore.disabled=busy;
      restore.addEventListener("click",()=>restoreRepo(repo));
      card.append(restore);
    }
    return card;
  }));
  $("software-empty").hidden=repos.length>0;
}
async function loadSoftware() {
  // Applicazioni all'avvio and Pulizia are independent, read-only detections
  // (own loading guards, own render functions): they run alongside the APT
  // repository load instead of waiting on it.
  loadAutostart();
  scanCleanup();
  if(state.softwareLoading)return;
  state.softwareLoading=true;$("software-loading").hidden=false;$("software-conflict").hidden=true;
  try{state.software=await invoke("list_apt_repositories");}
  catch(error){console.error("APT repository detection failed",error);state.software=[];}
  finally{state.softwareLoading=false;$("software-loading").hidden=true;renderSoftware();}
}
async function toggleRepo(repo) {
  if(state.softwareBusyId)return;
  state.softwareBusyId=repo.id;renderSoftware();
  try{
    state.software=await invoke("set_apt_repository_enabled",{id:repo.id,enabled:!repo.enabled,expectedRevision:repo.revision});
    $("software-conflict").hidden=true;
  }catch(error){
    console.error("Repository toggle failed",error);
    if(error==="revision_mismatch"){$("software-conflict").hidden=false;}
    else{try{state.software=await invoke("list_apt_repositories");}catch{/* keep last known list */}}
  }finally{state.softwareBusyId=null;renderSoftware();}
}
async function restoreRepo(repo) {
  if(state.softwareBusyId)return;
  state.softwareBusyId=repo.id;renderSoftware();
  try{
    state.software=await invoke("restore_apt_repository",{id:repo.id,expectedRevision:repo.revision});
    $("software-conflict").hidden=true;
  }catch(error){
    console.error("Repository restore failed",error);
    if(error==="revision_mismatch"){$("software-conflict").hidden=false;}
    else{try{state.software=await invoke("list_apt_repositories");}catch{/* keep last known list */}}
  }finally{state.softwareBusyId=null;renderSoftware();}
}
// Applicazioni all'avvio: one XDG Autostart engine (see xdg_autostart.rs),
// the frontend only ever sends an opaque entry id back, never a path.
function autostartStateLabel(entry) { return t(entry.enabled?"software.autostartOn":"software.autostartOff"); }
function autostartOriginLabel(entry) { return t(entry.origin==="system"?"software.autostartOriginSystem":"software.autostartOriginUser"); }
function autostartDetails(entry) {
  const dl=element("dl","repo-details-list");
  const row=(label,value)=>{if(!value)return;dl.append(element("dt","",t(label)),element("dd","",value));};
  row("software.autostartDetailOrigin",autostartOriginLabel(entry));
  row("software.autostartDetailFile",entry.file);
  row("software.autostartDetailExec",entry.exec);
  if(entry.iconName)row("software.autostartDetailIcon",entry.iconName);
  if(!entry.appliesToCurrentDesktop)row("software.autostartDetailDesktop",t("software.autostartNotForThisDesktop"));
  const details=element("details","repo-details");
  details.append(element("summary","",t("software.details")),dl);
  return details;
}
function autostartMatchesFilter(entry) {
  if(state.autostartFilter==="active"&&!entry.enabled)return false;
  if(state.autostartFilter==="disabled"&&entry.enabled)return false;
  const q=state.autostartSearch.trim().toLowerCase();
  if(!q)return true;
  return entry.name.toLowerCase().includes(q)||(entry.comment||"").toLowerCase().includes(q);
}
function renderAutostart() {
  const entries=state.autostart||[];
  $("autostart-loading").hidden=!state.autostartLoading;
  $("autostart-empty").hidden=state.autostartLoading||entries.length>0;
  $("autostart-summary").hidden=state.autostartLoading||entries.length===0;
  const active=entries.filter(e=>e.enabled).length;
  setText("autostart-count-active",String(active));
  setText("autostart-count-disabled",String(entries.length-active));
  const toggleButton=$("autostart-toggle-button");
  toggleButton.setAttribute("aria-expanded",String(state.autostartManagerOpen));
  $("autostart-panel").hidden=!state.autostartManagerOpen;
  const filtered=entries.filter(autostartMatchesFilter);
  $("autostart-list").replaceChildren(...filtered.map(entry=>{
    const busy=state.autostartBusyId===entry.id;
    const row=element("article","data-panel autostart-row");
    const head=element("div","autostart-row-head");
    const nameWrap=element("div");
    nameWrap.append(element("strong","autostart-row-name",entry.name));
    if(entry.comment)nameWrap.append(element("small","autostart-row-comment",entry.comment));
    const stateWrap=element("div","autostart-row-state");
    stateWrap.append(element("span",entry.enabled?"on":"off",(entry.enabled?"● ":"○ ")+autostartStateLabel(entry)));
    const toggle=element("button","",busy?t("performance.applying"):t(entry.enabled?"software.autostartDisable":"software.autostartEnable"));
    toggle.type="button";toggle.disabled=busy;
    toggle.addEventListener("click",()=>toggleAutostart(entry));
    stateWrap.append(toggle);
    head.append(nameWrap,stateWrap);
    row.append(head,autostartDetails(entry));
    return row;
  }));
  $("autostart-empty-filtered").hidden=entries.length===0||filtered.length>0;
}
function toggleAutostartManager() {
  state.autostartManagerOpen=!state.autostartManagerOpen;
  renderAutostart();
}
async function loadAutostart() {
  if(state.autostartLoading)return;
  state.autostartLoading=true;$("autostart-loading").hidden=false;
  try{state.autostart=await invoke("list_autostart_entries");}
  catch(error){console.error("Autostart detection failed",error);state.autostart=[];}
  finally{state.autostartLoading=false;$("autostart-loading").hidden=true;renderAutostart();}
}
async function toggleAutostart(entry) {
  if(state.autostartBusyId)return;
  state.autostartBusyId=entry.id;renderAutostart();
  try{state.autostart=await invoke("set_autostart_entry_enabled",{id:entry.id,enabled:!entry.enabled});}
  catch(error){console.error("Autostart toggle failed",error);try{state.autostart=await invoke("list_autostart_entries");}catch{/* keep last known list */}}
  finally{state.autostartBusyId=null;renderAutostart();}
}
// Pulizia: analyse-first (never a "clean now" default), granular per-item
// selection, cookies always a separate, never-auto-selected list.
const CLEANUP_CATEGORY_LABELS={temp:"software.cleanupCategoryTemp",app_cache:"software.cleanupCategoryAppCache",browser_cache:"software.cleanupCategoryBrowserCache",thumbnails:"software.cleanupCategoryThumbnails",trash:"software.cleanupCategoryTrash"};
function findCleanupItem(id) {
  for(const category of state.cleanup?.categories||[]){const found=category.items.find(i=>i.id===id);if(found)return found;}
  return null;
}
function renderCleanupCategory(category) {
  const wrap=element("article","data-panel cleanup-category");
  const header=element("div","cleanup-category-header");
  const allSelected=category.items.length>0&&category.items.every(i=>state.cleanupSelection.has(i.id));
  const noneSelected=category.items.every(i=>!state.cleanupSelection.has(i.id));
  const label=document.createElement("label");
  const checkbox=document.createElement("input");checkbox.type="checkbox";
  checkbox.checked=allSelected;checkbox.indeterminate=!allSelected&&!noneSelected;
  checkbox.addEventListener("change",()=>{
    for(const item of category.items){if(checkbox.checked)state.cleanupSelection.add(item.id);else state.cleanupSelection.delete(item.id);}
    renderCleanup();
  });
  label.append(checkbox,document.createTextNode(t(CLEANUP_CATEGORY_LABELS[category.id]||category.id)));
  header.append(label,element("span","cleanup-category-size",formatBytes(category.sizeBytes)||"—"));
  wrap.append(header);
  if(category.items.length){
    const details=element("details","");
    details.append(element("summary","",t("software.cleanupShowItems",{count:category.items.length})));
    for(const item of category.items){
      const row=element("div","cleanup-item-row");
      const itemLabel=document.createElement("label");
      const itemCheckbox=document.createElement("input");itemCheckbox.type="checkbox";
      itemCheckbox.checked=state.cleanupSelection.has(item.id);
      itemCheckbox.addEventListener("change",()=>{if(itemCheckbox.checked)state.cleanupSelection.add(item.id);else state.cleanupSelection.delete(item.id);renderCleanup();});
      itemLabel.append(itemCheckbox,document.createTextNode(item.label));
      row.append(itemLabel,element("span","",formatBytes(item.sizeBytes)||"—"));
      details.append(row);
    }
    wrap.append(details);
  }
  return wrap;
}
function renderCleanupCookies() {
  const groups=state.cleanup?.cookieGroups||[];
  const container=$("cleanup-cookie-list");container.replaceChildren();
  $("cleanup-cookies").hidden=groups.length===0;
  for(const group of groups){
    const row=element("div","cleanup-cookie-row"+(group.isRunning?" running":""));
    const label=document.createElement("label");
    const checkbox=document.createElement("input");checkbox.type="checkbox";
    checkbox.checked=state.cleanupCookieSelection.has(group.id);
    checkbox.disabled=group.isRunning;
    checkbox.addEventListener("change",()=>{if(checkbox.checked)state.cleanupCookieSelection.add(group.id);else state.cleanupCookieSelection.delete(group.id);renderCleanup();});
    const textWrap=document.createElement("span");
    const title=document.createElement("strong");title.textContent=group.browser+(group.profileLabel?` — ${group.profileLabel}`:"");
    const small=element("small","",group.isRunning?t("software.cleanupBrowserOpen",{name:group.browser}):(formatBytes(group.sizeBytes)||"—"));
    textWrap.append(title,document.createElement("br"),small);
    label.append(checkbox,textWrap);
    row.append(label);
    container.append(row);
  }
}
function confirmCleanup(itemIds,cookieIds) {
  const container=$("cleanup-actions");
  const totalBytes=itemIds.reduce((sum,id)=>sum+(findCleanupItem(id)?.sizeBytes||0),0);
  const nodes=[element("span","confirm-copy",t("software.cleanupConfirm",{size:formatBytes(totalBytes)||"0 B"}))];
  if(cookieIds.length)nodes.push(element("span","confirm-copy",t("software.cleanupCookieConfirm",{count:cookieIds.length})));
  const cancel=element("button","",t("action.cancel"));cancel.type="button";cancel.addEventListener("click",renderCleanup);
  const confirm=element("button","danger-action",t("action.confirm"));confirm.type="button";
  confirm.addEventListener("click",()=>runCleanup(itemIds,cookieIds));
  nodes.push(cancel,confirm);
  container.replaceChildren(...nodes);
}
function renderCleanupActions() {
  const container=$("cleanup-actions");
  const itemIds=[...state.cleanupSelection],cookieIds=[...state.cleanupCookieSelection];
  if(!itemIds.length&&!cookieIds.length){container.replaceChildren();return;}
  const button=element("button","",t("software.cleanupCleanSelected"));
  button.type="button";button.disabled=state.cleanupBusy;
  button.addEventListener("click",()=>confirmCleanup(itemIds,cookieIds));
  container.replaceChildren(button);
}
function renderCleanup() {
  const scan=state.cleanup;
  $("cleanup-scan-button").setAttribute("aria-expanded",String(state.cleanupExpanded));
  $("cleanup-panel").hidden=!state.cleanupExpanded;
  if(!scan){
    setText("cleanup-total-preview",t("software.cleanupNotAnalyzed"));
    setText("cleanup-preview-temp","—");
    setText("cleanup-preview-browser","—");
    return;
  }
  setText("cleanup-total-preview",formatBytes(scan.totalReclaimableBytes)||"0 B");
  setText("cleanup-preview-temp",t("software.cleanupAnalyzed"));
  const browserCategory=scan.categories.find(c=>c.id==="browser_cache");
  const hasBrowserData=(browserCategory&&browserCategory.items.length>0)||(scan.cookieGroups||[]).length>0;
  setText("cleanup-preview-browser",hasBrowserData?t("software.cleanupAvailable"):t("software.cleanupNone"));
  $("cleanup-total-value").textContent=formatBytes(scan.totalReclaimableBytes)||"0 B";
  $("cleanup-categories").replaceChildren(...scan.categories.filter(c=>c.items.length).map(renderCleanupCategory));
  renderCleanupCookies();
  const outcome=$("cleanup-outcome");
  if(state.cleanupOutcome){
    outcome.hidden=false;
    const skipped=state.cleanupOutcome.skipped.length?" · "+t("software.cleanupSkipped",{count:state.cleanupOutcome.skipped.length}):"";
    outcome.textContent=t("software.cleanupFreed",{size:formatBytes(state.cleanupOutcome.freedBytes)||"0 B"})+skipped;
  }else outcome.hidden=true;
  renderCleanupActions();
}
async function scanCleanup() {
  if(state.cleanupLoading)return;
  state.cleanupLoading=true;$("cleanup-loading").hidden=false;
  try{
    state.cleanup=await invoke("scan_cleanup_targets");
    state.cleanupSelection=new Set(state.cleanup.categories.filter(c=>c.selectedByDefault).flatMap(c=>c.items.map(i=>i.id)));
    state.cleanupCookieSelection=new Set();
  }catch(error){
    console.error("Cleanup scan failed",error);
    state.cleanup={totalReclaimableBytes:0,categories:[],cookieGroups:[]};
    state.cleanupSelection=new Set();state.cleanupCookieSelection=new Set();
  }finally{state.cleanupLoading=false;$("cleanup-loading").hidden=true;renderCleanup();}
}
// Programmi: Gaming. The page only ever asks the backend to prepare the
// approved set; it never names a package, a repository or a command (see
// gaming.rs, where the whole plan is re-derived server-side). All counting
// and labelling rules live in gaming-view.js so they can be tested directly.
function gamingUnsupportedText(plan) {
  const known={unsupportedImmutable:"programs.unsupportedImmutable",packageManagerMissing:"programs.packageManagerMissing"};
  return t(known[plan.unsupportedReason]||"programs.unsupportedDistro");
}
function gamingWarningLabel(key) {
  const known={componentUnavailable:"programs.warnComponentUnavailable",componentUnknown:"programs.warnComponentUnknown",nvidiaStackUnknown:"programs.warnNvidiaStackUnknown",noGpuDetected:"programs.warnNoGpuDetected",rpmfusionRequired:"programs.warnRpmfusionRequired",rpmfusionRequiredManual:"programs.warnRpmfusionRequiredManual",multilibRequired:"programs.warnMultilibRequired",i386Required:"programs.warnI386Required",flatpakNeeded:"programs.warnFlatpakNeeded",nvidiaMatchOnly:"programs.warnNvidiaMatchOnly"};
  return known[key]?t(known[key]):null;
}
function gamingChangeLabel(change) {
  if(change.kind==="addRepository")return change.label==="addRpmFusion"?t("programs.changeAddRpmFusion"):t("programs.changeAddFlatpakRemote")+" ("+change.detail.join(", ")+")";
  const known={addI386:"programs.changeAddI386",enableMultilib:"programs.changeEnableMultilib",refresh:"programs.changeRefresh"};
  return known[change.kind]?t(known[change.kind]):change.label;
}
function gpuChipLabel(plan) {
  const vendors=[...new Set((plan.gpus||[]).map(gpu=>gpu.vendor))];
  if(!vendors.length)return t("programs.noGpu");
  return vendors.map(v=>({amd:"AMD Radeon",intel:"Intel",nvidia:"NVIDIA"}[v]||"GPU")).join(" + ");
}
function chip(text) {
  return element("span","programs-chip",text);
}
function renderGaming() {
  const plan=state.gaming;
  $("gaming-loading")&&($("gaming-loading").hidden=true);
  if(!plan){
    $("gaming-chips").hidden=true;
    const status=$("gaming-status");status.hidden=false;
    status.textContent=state.gamingError?t("programs.detectFailed"):"";
    status.hidden=!state.gamingError;
    $("gaming-hint").hidden=true;
    return;
  }
  const chips=$("gaming-chips");chips.hidden=false;chips.replaceChildren();
  chips.append(chip(plan.distribution.name+(plan.distribution.version?" "+plan.distribution.version:"")));
  chips.append(chip(gpuChipLabel(plan)));
  if(plan.packageManager)chips.append(chip(plan.packageManager.toUpperCase()));
  const status=$("gaming-status");status.hidden=false;status.textContent=statusText(plan,t);
  setText("gaming-hint",plan.supported?"":gamingUnsupportedText(plan));
  const toggle=$("gaming-toggle-button");toggle.setAttribute("aria-expanded",String(state.gamingOpen));
  $("gaming-panel").hidden=!state.gamingOpen;
  $("gaming-panel-subtitle").textContent=plan.distribution.name+(plan.distribution.version?" "+(plan.distribution.version):"");
  const unsupported=$("gaming-unsupported");
  unsupported.hidden=plan.supported||!state.gamingOpen;
  if(!plan.supported)unsupported.textContent=gamingUnsupportedText(plan);
  setText("gaming-distro-value",plan.distribution.name);
  setText("gaming-gpus-value",(plan.gpus||[]).map(gpu=>gpu.name).join(", ")||t("programs.noGpu"));
  setText("gaming-pm-value",plan.packageManager||"—");
  const listInto=(target,items)=>{target.replaceChildren(...items);};
  const pending=pendingComponents(plan);
  const present=installedComponents(plan);
  listInto($("gaming-to-install-list"),pending.map(component=>element("li","",componentLabel(component,t))));
  listInto($("gaming-present-list"),present.map(component=>element("li","",componentLabel(component,t))));
  listInto($("gaming-changes-list"),specialChanges(plan).map(change=>element("li","",gamingChangeLabel(change))));
  const warnings=warningKeys(plan).map(gamingWarningLabel).filter(Boolean);
  const unavailable=unavailableComponents(plan).map(component=>componentLabel(component,t));
  const warningList=$("gaming-warnings-list");
  warningList.replaceChildren(...warnings.map(item=>element("li","",item)),...unavailable.map(item=>element("li","",item)));
  $("gaming-to-install").hidden=pending.length===0;
  $("gaming-present").hidden=present.length===0;
  $("gaming-changes-block").hidden=specialChanges(plan).length===0;
  $("gaming-warnings-block").hidden=warningList.childElementCount===0;
  const technical=$("gaming-technical");
  const rows=technicalRows(plan,t);
  technical.hidden=rows.length===0;
  $("gaming-technical-list").replaceChildren(...rows.flatMap(row=>[element("dt","",row.label),element("dd","",row.detail)]));
  renderGamingOutcome(plan);
  renderGamingActions(plan);
}
function renderGamingOutcome(plan) {
  const outcome=state.gamingOutcome;
  const container=$("gaming-outcome");
  container.hidden=!outcome;
  $("gaming-error-details").hidden=true;
  if(!outcome)return;
  const partial=outcomeIsPartial(outcome);
  container.classList.toggle("partial",partial);
  setText("gaming-outcome-title",partial?t("programs.outcomePartial"):t("programs.outcomeOk"));
  const step=failedStep(outcome);
  setText("gaming-outcome-message",partial?errorMessage(outcome,t):"");
  const lists=$("gaming-outcome-lists");lists.replaceChildren();
  const block=(title,items)=>{if(!items.length)return;const wrap=element("div","programs-block");wrap.append(element("h3","",title),element("ul","programs-chips-list",""));const ul=wrap.querySelector("ul");for(const item of items)ul.append(element("li","",item));lists.append(wrap);};
  if(partial){
    block(t("programs.notCompleted"),pendingComponents(plan).map(component=>componentLabel(component,t)));
    block(t("programs.installed"),installedComponents(plan).map(component=>componentLabel(component,t)));
  }
  const details=$("gaming-error-details");
  if(step||!partial){
    details.hidden=!partial||!outcome.steps?.length;
    const steps=$("gaming-error-steps");steps.replaceChildren();
    for(const row of errorRows(outcome)){
      const line=[stepLabel(row,t),row.operation,row.exitCode!=null?`exit ${row.exitCode}`:null,row.stderr].filter(Boolean).join("\n");
      steps.append(element("div","programs-error-step",line));
    }
  }
}
function renderGamingActions(plan) {
  const actions=$("gaming-actions");actions.replaceChildren();
  if(!plan.supported)return;
  if(state.gamingBusy){const busy=element("button","",t("programs.installing"));busy.type="button";busy.disabled=true;actions.append(busy);return;}
  const button=element("button","",actionLabel(plan,state.gamingOutcome,t));
  button.type="button";button.disabled=pendingComponents(plan).length===0;
  button.addEventListener("click",confirmGaming);
  const cancel=element("button","secondary-action",t("action.cancel"));
  cancel.type="button";cancel.addEventListener("click",closeGamingPanel);
  actions.append(button,cancel);
}
function renderGamingProgress(steps) {
  const container=$("gaming-progress"),list=$("gaming-progress-list");
  container.hidden=!steps.length;
  list.replaceChildren(...steps.map(step=>element("li",step.ok?"done":"failed",stepLabel(step,t))));
}
function renderGeforce() {
  const plan=state.geforce;
  const status=$("geforce-status"),button=$("geforce-install-button");
  if(!plan){status.hidden=true;button.disabled=true;return;}
  const installed=installedComponents(plan).some(component=>component.id==="geforce_now");
  status.hidden=false;status.textContent=installed?t("programs.geforceInstalled"):t("programs.geforceNotInstalled");
  button.hidden=!plan.supported||installed;
  button.disabled=state.geforceBusy;
  button.textContent=state.geforceBusy?t("programs.installing"):t("programs.geforceInstall");
}
async function loadGaming() {
  if(state.gamingLoading)return;
  state.gamingLoading=true;renderGaming();
  // Each command has its own error path: a GeForce NOW failure must never
  // blank the Gaming plan, and vice versa.
  try{state.gaming=await invoke("get_gaming_status");}
  catch(error){console.error("Gaming detection failed",error);state.gaming=null;state.gamingError=String(error);}
  try{state.geforce=await invoke("get_geforce_now_status");}
  catch(error){console.error("GeForce NOW detection failed",error);state.geforce=null;}
  state.gamingLoading=false;renderGaming();renderGeforce();
}
function toggleGamingPanel() {
  if(state.gamingOpen){closeGamingPanel();return;}
  state.gamingOpen=true;
  if(!state.gaming)loadGaming();
  renderGaming();renderGeforce();
}
function closeGamingPanel() {
  state.gamingOpen=false;
  renderGaming();renderGeforce();
}
async function confirmGaming() {
  if(state.gamingBusy)return;
  state.gamingBusy=true;state.gamingOutcome=null;state.gamingProgress=[];
  renderGaming();renderGamingProgress([]);
  try{
    const report=await invoke("prepare_gaming");
    state.gamingOutcome=report;
    state.gaming=await invoke("get_gaming_status");
    state.geforce=await invoke("get_geforce_now_status");
  }catch(error){
    console.error("Gaming preparation failed",error);
    state.gamingOutcome={ok:false,steps:[],missingPackages:[],errorCode:String(error)};
    try{state.gaming=await invoke("get_gaming_status");}catch{/* keep last known plan */}
  }
  finally{state.gamingBusy=false;state.gamingProgress=[];renderGamingProgress([]);renderGaming();renderGeforce();}
}
async function installGeforce() {
  if(state.geforceBusy)return;
  state.geforceBusy=true;$("geforce-outcome").hidden=true;renderGeforce();
  try{
    const report=await invoke("install_geforce_now");
    state.geforce=await invoke("get_geforce_now_status");
    if(!report.ok){const notice=$("geforce-outcome");notice.hidden=false;notice.textContent=errorMessage(report,t);}
  }catch(error){
    console.error("GeForce NOW installation failed",error);
    const notice=$("geforce-outcome");notice.hidden=false;
    notice.textContent=t(String(error)==="authorization_cancelled"?"programs.errorCancelled":"programs.errorGeneric");
  }
  finally{state.geforceBusy=false;renderGeforce();}
}
function dnsProviderLabel(provider) {
  const known={automatic:"programs.dnsAutomatic",cloudflare:"programs.dnsCloudflare",google:"programs.dnsGoogle",quad9:"programs.dnsQuad9",adguard:"programs.dnsAdguard",custom:"programs.dnsCustom"};
  return t(known[provider]||"programs.dnsUnavailable");
}
function dnsUnavailableText(reason) {
  const known={networkManagerUnavailable:"programs.dnsUnavailable",noActiveConnection:"programs.dnsNoConnection",ambiguousConnection:"programs.dnsAmbiguous",statusReadFailed:"programs.dnsReadFailed",unsupportedAddressMethod:"programs.dnsUnsupportedMethod"};
  return t(known[reason]||"programs.dnsUnavailable");
}
function dnsAddresses(family) {
  const active=family?.activeServers||[];
  return active.length?active.join(" · "):t("programs.dnsNone");
}
function renderDns() {
  const status=state.dns;
  const cardStatus=$("dns-status"),hint=$("dns-hint"),toggle=$("dns-toggle-button");
  toggle.setAttribute("aria-expanded",String(state.dnsOpen));
  $("dns-panel").hidden=!state.dnsOpen;
  if(!status){
    cardStatus.textContent=state.dnsLoading?t("programs.dnsReading"):t("programs.dnsUnavailable");
    hint.textContent=state.dnsError?t("programs.dnsReadFailed"):"";
    toggle.disabled=state.dnsLoading;
    return;
  }
  toggle.disabled=false;
  cardStatus.textContent=status.available?dnsProviderLabel(status.provider):t("programs.dnsUnavailable");
  hint.textContent=status.available?(status.connectionName||""):dnsUnavailableText(status.reason);
  $("dns-panel-subtitle").textContent=status.connectionName||"";
  const unavailable=$("dns-unavailable");
  unavailable.hidden=status.available||!state.dnsOpen;
  unavailable.textContent=status.available?"":dnsUnavailableText(status.reason);
  $("dns-details").hidden=!status.available;
  if(!status.available)return;
  setText("dns-connection-value",status.connectionName);
  setText("dns-device-value",status.device);
  setText("dns-current-value",dnsProviderLabel(status.provider));
  setText("dns-ipv4-value",dnsAddresses(status.ipv4));
  setText("dns-ipv6-value",dnsAddresses(status.ipv6));
  if(!state.dnsSelection&&status.provider!=="custom")state.dnsSelection=status.provider;
  for(const input of document.querySelectorAll('input[name="dns-provider"]')){
    input.checked=input.value===state.dnsSelection;
    input.disabled=state.dnsBusy;
  }
  const apply=$("dns-apply-button");
  apply.disabled=state.dnsBusy||!state.dnsSelection||state.dnsSelection===status.provider;
  apply.textContent=state.dnsBusy?t("programs.dnsApplying"):t("programs.dnsApply");
}
async function loadDns() {
  if(state.dnsLoading)return;
  state.dnsLoading=true;state.dnsError=null;renderDns();
  try{state.dns=await invoke("get_dns_status");state.dnsSelection=state.dns.provider==="custom"?null:state.dns.provider;}
  catch(error){console.error("DNS detection failed",error);state.dns=null;state.dnsError=String(error);}
  finally{state.dnsLoading=false;renderDns();}
}
function toggleDnsPanel() {
  state.dnsOpen=!state.dnsOpen;
  if(state.dnsOpen&&!state.dns)loadDns();
  renderDns();
}
function closeDnsPanel() {state.dnsOpen=false;renderDns();}
async function applyDnsProvider() {
  if(state.dnsBusy||!state.dnsSelection)return;
  state.dnsBusy=true;$("dns-outcome").hidden=true;renderDns();
  try{
    state.dns=await invoke("set_dns_provider",{provider:state.dnsSelection});
    const outcome=$("dns-outcome");outcome.hidden=false;outcome.textContent=t("programs.dnsApplied",{provider:dnsProviderLabel(state.dns.provider)});
  }catch(error){
    console.error("DNS change failed",error);
    const outcome=$("dns-outcome");outcome.hidden=false;
    outcome.textContent=t(String(error)==="authorization_cancelled"?"programs.errorCancelled":"programs.dnsApplyFailed");
    try{state.dns=await invoke("get_dns_status");}catch{/* preserve the last readable state */}
  }finally{state.dnsBusy=false;renderDns();}
}
const WINBOAT_REQ_LABELS={kvm:"programs.winboatReqKvm",ram:"programs.winboatReqRam",cpu:"programs.winboatReqCpu",disk:"programs.winboatReqDisk",docker_engine:"programs.winboatReqDockerEngine",docker_compose:"programs.winboatReqDockerCompose",docker_group:"programs.winboatReqDockerGroup",docker_daemon:"programs.winboatReqDockerDaemon",freerdp:"programs.winboatReqFreerdp",winboat:"programs.winboatReqWinboat"};
const WINBOAT_UNSUPPORTED_LABELS={unsupportedDistro:"programs.unsupportedDistro",userUnknown:"programs.winboatUserUnknown"};
const WINBOAT_STEP_LABELS={ensure_kvm_ready:"programs.winboatStep.ensure_kvm_ready",add_docker_repo:"programs.winboatStep.add_docker_repo",refresh_indexes:"programs.winboatStep.refresh_indexes",install_docker:"programs.winboatStep.install_docker",create_docker_group:"programs.winboatStep.create_docker_group",enable_docker:"programs.winboatStep.enable_docker",add_user_docker_group:"programs.winboatStep.add_user_docker_group",install_freerdp:"programs.winboatStep.install_freerdp",download_winboat:"programs.winboatStep.download_winboat",install_winboat:"programs.winboatStep.install_winboat"};
function winboatReqIcon(state) {return state==="ok"?"✓":state==="blocked"?"✕":"○";}
function winboatCardText(status) {
  if(!status)return t("programs.winboatReading");
  if(!status.supported)return t(WINBOAT_UNSUPPORTED_LABELS[status.unsupportedReason]||"programs.winboatUnsupported");
  if(status.ready)return t("programs.winboatReadyBadge");
  if(status.rebootSuggested)return t("programs.winboatRebootPendingBadge");
  return status.pendingCount===1?t("programs.winboatPendingOne"):t("programs.winboatPendingMany",{count:status.pendingCount});
}
function renderWinboatChecklist(reqs) {
  const list=$("winboat-checklist");
  list.replaceChildren(...reqs.map(req=>{
    const li=element("li","winboat-check "+req.state);
    li.append(
      element("span","winboat-check-icon",winboatReqIcon(req.state)),
      element("span","winboat-check-label",t(WINBOAT_REQ_LABELS[req.id]||req.id)),
      element("small","winboat-check-detail",req.detail),
    );
    return li;
  }));
}
function renderWinboatProgress(steps) {
  const container=$("winboat-progress"),list=$("winboat-progress-list"),fill=$("winboat-progress-fill");
  container.hidden=!steps.length;
  list.replaceChildren(...steps.map(step=>element("li",step.ok?"done":"failed",t(WINBOAT_STEP_LABELS[step.step]||step.step))));
  const last=steps[steps.length-1];
  const percent=last&&last.totalSteps?Math.round((last.stepIndex/last.totalSteps)*100):0;
  fill.style.width=percent+"%";
}
function winboatOutcomeText(outcome) {
  if(outcome.ok)return t("programs.winboatOutcomeOk");
  const known={authorization_cancelled:"programs.errorCancelled",virtualizationUnavailable:"programs.winboatVirtualizationUnsupported",virtualizationDisabledInFirmware:"programs.winboatVirtualizationDisabled",dockerConflict:"programs.winboatDockerConflict",requirementsNotMet:"programs.winboatRequirementsNotMet",no_matching_asset:"programs.winboatNoAsset",only_prerelease_available:"programs.winboatNoAsset",helper_missing:"programs.winboatHelperMissing",unsupportedDistro:"programs.unsupportedDistro"};
  return t(known[outcome.errorCode]||"programs.winboatOutcomeFailed");
}
function renderWinboat() {
  const status=state.winboat;
  const card=$("winboat-status"),hint=$("winboat-hint"),toggle=$("winboat-toggle-button");
  toggle.setAttribute("aria-expanded",String(state.winboatOpen));
  toggle.disabled=state.winboatLoading;
  $("winboat-panel").hidden=!state.winboatOpen;
  card.hidden=!status&&!state.winboatLoading;
  card.textContent=winboatCardText(status);
  hint.textContent=state.winboatError?t("programs.detectFailed"):"";
  if(!state.winboatOpen)return;
  const unsupported=$("winboat-unsupported");
  unsupported.hidden=!status||status.supported;
  if(status&&!status.supported)unsupported.textContent=t(WINBOAT_UNSUPPORTED_LABELS[status.unsupportedReason]||"programs.winboatUnsupported");
  $("winboat-details").hidden=!status||!status.supported;
  if(!status||!status.supported)return;
  renderWinboatChecklist(status.requirements);
  renderWinboatProgress(state.winboatBusy?state.winboatProgress:[]);
  const outcome=$("winboat-outcome");
  if(state.winboatOutcome&&!state.winboatBusy){
    outcome.hidden=false;
    outcome.textContent=winboatOutcomeText(state.winboatOutcome);
  }else{
    outcome.hidden=true;
  }
  const reboot=$("winboat-reboot");
  reboot.hidden=state.winboatBusy||!status.rebootSuggested;
  const install=$("winboat-install-button"),open=$("winboat-open-button");
  install.hidden=status.ready||status.rebootSuggested;
  install.disabled=state.winboatBusy;
  install.textContent=state.winboatBusy?t("programs.installing"):t("programs.winboatInstall");
  open.hidden=!status.ready;
}
async function loadWinboat() {
  if(state.winboatLoading)return;
  state.winboatLoading=true;state.winboatError=null;renderWinboat();
  try{state.winboat=await invoke("get_winboat_status");}
  catch(error){console.error("WinBoat detection failed",error);state.winboat=null;state.winboatError=String(error);}
  finally{state.winboatLoading=false;renderWinboat();}
}
function toggleWinboatPanel() {
  if(state.winboatOpen){closeWinboatPanel();return;}
  state.winboatOpen=true;
  if(!state.winboat)loadWinboat();
  renderWinboat();
}
function closeWinboatPanel() {state.winboatOpen=false;renderWinboat();}
async function confirmWinboat() {
  if(state.winboatBusy)return;
  state.winboatBusy=true;state.winboatOutcome=null;state.winboatProgress=[];renderWinboat();
  try{
    const report=await invoke("prepare_winboat");
    state.winboatOutcome=report;
  }catch(error){
    console.error("WinBoat prepare failed",error);
    state.winboatOutcome={ok:false,steps:[],rebootRequired:false,requirements:[],errorCode:String(error)};
  }
  try{state.winboat=await invoke("get_winboat_status");}catch{/* keep last known status */}
  state.winboatBusy=false;state.winboatProgress=[];renderWinboatProgress([]);renderWinboat();
}
async function openWinboat() {
  try{await invoke("open_winboat");}catch(error){console.error("WinBoat launch failed",error);}
}
async function rebootNow() {
  try{await invoke("reboot_now");}catch(error){console.error("Reboot request failed",error);}
}
// One install button per format ("Installa Flatpak"/"Installa Snap"),
// never a single generic button that silently picks one for the user.
// Curtail's Snap button additionally carries a discreet third-party note.
function installFormatButton(id,offer,busy) {
  const label=offer.method==="flatpak"?t("apps.installFlatpak"):t("apps.installSnap");
  const selected=busy?.operation==="install"&&busy.method===offer.method;
  const button=element("button","",selected?`${label} ${t("programs.installing")}`:label);
  button.type="button";button.disabled=busy;
  button.addEventListener("click",()=>requestInstallApp(id,offer));
  return button;
}
function renderAppCard(card,id){
  const status=state.apps?.find(app=>app.id===id);
  const pending=state.appsBusyId;
  const busy=pending?.appId===id;
  const isBusy=(operation,method)=>pending?.appId===id&&pending.operation===operation&&pending.method===method;
  const installLabel=(method)=>isBusy("install",method)?`${method==="flatpak"?t("apps.installFlatpak"):t("apps.installSnap")} ${t("programs.installing")}`:method==="flatpak"?t("apps.installFlatpak"):t("apps.installSnap");
  const head=element("div","panel-title app-card-head");
  const image=element("img","app-card-icon");image.src=bundledIconPath(id);image.alt="";
  head.append(image,element("h3","",appName(id)));
  const statusLine=element("strong","adv-state",state.appsError?t("programs.detectFailed"):appsStatusText(status,t));
  const children=[head,element("p","",appDescription(id,t)),statusLine];
  const note=vulkanNote(status,t);if(note)children.push(element("small","adv-note software-tool-hint",note));
  const vias=installedVias(status);
  const nativeOffer=methodOffer(status,"native");
  const flatpakOffer=methodOffer(status,"flatpak");
  const snapOffer=methodOffer(status,"snap");
  const actions=element("div","apps-app-actions");
  if(nativeOffer||vias.includes("native")){
    // KDE Connect: unchanged, single native install/open/remove flow.
    if(vias.includes("native")){
      const open=element("button","",t("apps.open"));open.type="button";open.disabled=busy;open.addEventListener("click",()=>openApp(id));
      const remove=element("button","secondary-action",t("apps.remove"));remove.type="button";remove.disabled=busy;remove.addEventListener("click",()=>removeApp(id,"native"));
      actions.append(open,remove);
    }else if(nativeOffer){
      const install=element("button","",busy?t("programs.installing"):t("apps.install"));install.type="button";install.disabled=busy;install.addEventListener("click",()=>requestInstallApp(id,nativeOffer));actions.append(install);
    }
  }else if(vias.length===0){
    // Not installed at all: both choices, evident and side by side.
     if(flatpakOffer)actions.append(installFormatButton(id,flatpakOffer,pending));
     if(snapOffer)actions.append(installFormatButton(id,snapOffer,pending));
    if(actions.childElementCount)children.push(actions);
    const community=communityNote(snapOffer,t);
    if(community)children.push(element("small","adv-note software-tool-hint apps-snap-community",community));
  }else if(vias.length===1){
    // Installed via exactly one format: Apri/Rimuovi first, the other
    // format only ever behind an explicit "Altre opzioni" toggle, so a
    // second copy is never installed by accident.
    const open=element("button","",t("apps.open"));open.type="button";open.disabled=busy;open.addEventListener("click",()=>openApp(id));
    const remove=element("button","secondary-action",t("apps.remove"));remove.type="button";remove.disabled=busy;remove.addEventListener("click",()=>removeApp(id,vias[0]));
    actions.append(open,remove);
    const missingOffer=vias[0]==="flatpak"?snapOffer:flatpakOffer;
    if(missingOffer){
      const expanded=state.appsMoreOptions.has(id);
      const toggle=element("button","secondary-action apps-more-toggle",t("apps.moreInstallOptions"));
      toggle.type="button";toggle.setAttribute("aria-expanded",String(expanded));
      toggle.addEventListener("click",()=>{expanded?state.appsMoreOptions.delete(id):state.appsMoreOptions.add(id);renderApps();});
      actions.append(toggle);
      if(expanded){
        const moreRow=element("div","apps-app-actions apps-more-options");
         moreRow.append(installFormatButton(id,missingOffer,pending));
        if(actions.childElementCount)children.push(actions);
        children.push(moreRow);
        const community=communityNote(missingOffer,t);
        if(community)children.push(element("small","adv-note software-tool-hint apps-snap-community",community));
      }
    }
    if(!(missingOffer&&state.appsMoreOptions.has(id))&&actions.childElementCount)children.push(actions);
  }else{
    // Installed via both formats at once: one Apri (fixed priority,
    // documented server-side) and one Rimuovi button per format, so
    // removal always names exactly the copy it deletes.
    const open=element("button","",t("apps.open"));open.type="button";open.disabled=busy;open.addEventListener("click",()=>openApp(id));
    actions.append(open);
    children.push(actions);
    const removeRow=element("div","apps-app-actions apps-remove-choice");
    const removeFlatpak=element("button","secondary-action",t("apps.removeFlatpak"));removeFlatpak.type="button";removeFlatpak.disabled=busy;removeFlatpak.addEventListener("click",()=>removeApp(id,"flatpak"));
    const removeSnap=element("button","secondary-action",t("apps.removeSnap"));removeSnap.type="button";removeSnap.disabled=busy;removeSnap.addEventListener("click",()=>removeApp(id,"snap"));
    removeRow.append(removeFlatpak,removeSnap);
    children.push(removeRow);
  }
  const outcome=state.appsOutcomes[id];
  const outcomeStillRelevant=outcome&&((outcome.operation!=="remove")||vias.includes(outcome.method));
  if(outcomeStillRelevant&&!busy)children.push(element("p","programs-notice programs-inline-notice",outcomeMessage(outcome.report,outcome.errorCode,t,outcome.operation)));
  // Post-install, opt-in Snap extras: never granted automatically. Upscayl
  // gets one explicit [Non ora] [Consenti] prompt; Ferdium only gets an
  // informational note pointing at its own in-app settings, per its Snap
  // package's own documented (not auto-connected) interfaces.
  if(vias.includes("snap")&&state.appsSnapPermissionOffer.has(id)){
    const permission=upscaylRemovableMediaOffer(id);
    if(permission){
      const ask=element("div","apps-permission-ask");
      ask.append(element("p","",t("apps.permissionUpscaylAsk")));
      const row=element("div","apps-app-actions");
      const allow=element("button","",t("apps.permissionAllow"));allow.type="button";allow.disabled=busy;
      allow.addEventListener("click",()=>grantSnapPermission(id,permission.permission));
      const later=element("button","secondary-action",t("apps.permissionNotNow"));later.type="button";later.disabled=busy;
      later.addEventListener("click",()=>{state.appsSnapPermissionOffer.delete(id);renderApps();});
      row.append(allow,later);
      ask.append(row);
      children.push(ask);
    }
  }
  if(vias.includes("snap")&&state.appsSnapPermNote.has(id)){
    const ferdiumNote=ferdiumPermissionNote(id,t);
    if(ferdiumNote)children.push(element("small","adv-note software-tool-hint",ferdiumNote));
  }
  card.replaceChildren(...children);
}
function renderApps() {
  for(const card of document.querySelectorAll("[data-app]"))renderAppCard(card,card.dataset.app);
  setText("apps-support-summary",supportSummary(state.appsEnvironment,t));
}
async function loadApps() {
  if(state.appsLoading)return;
  state.appsLoading=true;state.appsError=null;renderApps();
  try{
    const snapshot=await invoke("get_apps_snapshot");
    state.apps=snapshot.apps;state.appsEnvironment=snapshot;
  }catch(error){
    console.error("Apps catalog detection failed",error);
    state.apps=null;state.appsError=String(error);
  }
  finally{state.appsLoading=false;renderApps();}
}
async function refreshApps(id,method) {
  try{
    const snapshot=await invoke("get_apps_snapshot");
    state.apps=snapshot.apps;state.appsEnvironment=snapshot;
    const installed=snapshot.apps.find((app)=>app.id===id)?.installed?.some((info)=>info.via===method);
    const outcome=state.appsOutcomes[id];
    if(outcome&&((outcome.operation==="remove"&&!installed)||(outcome.operation==="install"&&installed)))delete state.appsOutcomes[id];
  }catch(error){console.error("Apps snapshot refresh failed",error);}
}
async function runInstallApp(id,method) {
  if(state.appsBusyId)return;
  state.appsBusyId={appId:id,operation:"install",method};delete state.appsOutcomes[id];renderApps();
  try{
    const report=await invoke("install_app",{id,method});
    state.appsOutcomes[id]={report,errorCode:null,operation:"install"};
    if(method==="snap"&&report?.ok){
      if(upscaylRemovableMediaOffer(id))state.appsSnapPermissionOffer.add(id);
      if(ferdiumPermissionNote(id,t))state.appsSnapPermNote.add(id);
    }
  }catch(error){
    console.error("App install failed",error);
    state.appsOutcomes[id]={report:null,errorCode:String(error),operation:"install"};
  }
  await refreshApps(id,method);
  state.appsBusyId=null;renderApps();
}
// Grants one of the fixed, documented Snap interfaces (today only
// Upscayl's removable-media). The permission token comes from this
// module's own constant (apps-view.js), never from user input, and the
// backend re-validates it against its own allowlist anyway.
async function grantSnapPermission(id,permission) {
  if(state.appsBusyId)return;
  state.appsBusyId=id;state.appsOutcomes[id]=null;renderApps();
  try{
    const report=await invoke("grant_app_snap_permission",{id,permission});
    state.appsOutcomes[id]={report,errorCode:null};
  }catch(error){
    console.error("Snap permission grant failed",error);
    state.appsOutcomes[id]={report:null,errorCode:"permission_failed"};
  }
  state.appsSnapPermissionOffer.delete(id);
  state.appsBusyId=null;renderApps();
}
function openAppsInfoDialog(text) {
  setText("apps-confirm-text",text);
  state.appsConfirmInfoOnly=true;
  $("apps-confirm-confirm").hidden=true;
  $("apps-confirm-cancel").textContent=t("action.close");
  $("apps-confirm-dialog").showModal();
}
function openAppsConfirmDialog(text,id,method) {
  state.appsPendingConfirm={id,method};
  state.appsConfirmInfoOnly=false;
  setText("apps-confirm-text",text);
  $("apps-confirm-confirm").hidden=false;
  $("apps-confirm-cancel").textContent=t("action.cancel");
  $("apps-confirm-dialog").showModal();
}
// Every install button reads the one, shared, backend-resolved
// environment (never re-derived here) to decide: install immediately, ask
// the one matching confirmation for this exact distro/runtime state, or
// show a fixed "not supported here" message with no action at all.
async function requestInstallApp(id,offer) {
  if(state.appsBusyId)return;
  const env=state.appsEnvironment;
  const label=appName(id);
  if(offer.method==="flatpak"){
    if(isPlanReady(env?.flatpakPlan)){await runInstallApp(id,"flatpak");return;}
    if(isPlanBlocked(env?.flatpakPlan)){openAppsInfoDialog(blockedPlanText(env.flatpakPlan,t));return;}
    openAppsConfirmDialog(flatpakConfirmText(env,label,t),id,"flatpak");
    return;
  }
  if(isPlanReady(env?.snapPlan)){await runInstallApp(id,"snap");return;}
  if(isPlanBlocked(env?.snapPlan)){openAppsInfoDialog(blockedPlanText(env.snapPlan,t));return;}
  let text=snapConfirmText(env,label,t);
  const community=communityNote(offer,t);
  if(community)text=`${text}\n\n${community}`;
  openAppsConfirmDialog(text,id,"snap");
}
async function confirmAppsBootstrap() {
  const pending=state.appsPendingConfirm;
  state.appsPendingConfirm=null;
  $("apps-confirm-dialog").close();
  if(pending&&!state.appsConfirmInfoOnly)await runInstallApp(pending.id,pending.method);
}
async function openApp(id) {
  if(state.appsBusyId)return;
  state.appsBusyId={appId:id,operation:"open",method:"primary"};renderApps();
  try{await invoke("open_app",{id});}
  catch(error){console.error("App launch failed",error);state.appsOutcomes[id]={report:null,errorCode:String(error),operation:"open"};}
  state.appsBusyId=null;renderApps();
}
async function removeApp(id,via) {
  if(state.appsBusyId)return;
  state.appsBusyId={appId:id,operation:"remove",method:via};renderApps();
  try{const report=await invoke("remove_app",{id,via});state.appsOutcomes[id]={report,errorCode:null,operation:"remove"};}
  catch(error){state.appsOutcomes[id]={report:null,errorCode:String(error),operation:"remove"};}
  await refreshApps(id,via);
  state.appsBusyId=null;renderApps();
}
async function loadPrograms() {
  await Promise.all([loadGaming(),loadDns(),loadWinboat(),loadApps()]);
}
async function runCleanup(itemIds,cookieIds) {
  if(state.cleanupBusy)return;
  state.cleanupBusy=true;renderCleanup();
  try{state.cleanupOutcome=await invoke("clean_selected_cleanup_targets",{selection:{itemIds,cookieGroupIds:cookieIds}});}
  catch(error){console.error("Cleanup run failed",error);}
  finally{await scanCleanup();state.cleanupBusy=false;renderCleanup();}
}
function aboutLinkCard(card) {
  const article=element("article","data-panel about-link"+(card.secondary?" about-link-secondary":""));
  article.append(element("strong","about-link-title",card.title));
  if(card.name)article.append(element("span","about-link-name",card.name));
  if(card.value)article.append(element("span","about-link-value",card.value));
  if(card.description)article.append(element("p","about-link-desc",card.description));
  if(card.url&&card.button)article.append(externalLink(card.url,"about-link-button",card.button));
  return article;
}
// Every address shown here comes from the single AUTHOR module and is
// opened through the official opener plugin (anchors with target="_blank"
// for http/https/mailto), never through a command line.
function renderAbout() {
  const links=$("about-links");
  if(links){
    const cards=[
      {title:"YouTube",name:AUTHOR.name,value:AUTHOR.youtube,description:t("about.youtubeDesc"),url:AUTHOR.youtube,button:t("about.openChannel")},
      {title:t("about.cardWebsite"),value:AUTHOR.website,description:t("about.websiteDesc"),url:AUTHOR.website,button:t("about.visitSite")},
      {title:"GitHub",value:AUTHOR.github,description:t("about.githubDesc"),url:AUTHOR.github,button:t("about.openGithub")},
    ];
    links.replaceChildren(...cards.map(aboutLinkCard));
  }
  const actions=$("about-support-actions");
  if(actions)actions.replaceChildren(
    externalLink(AUTHOR.youtube,"about-action primary-action",t("about.subscribe")),
    externalLink(AUTHOR.support,"about-action",t("about.support")),
  );
}
// Startup nudge: shown once per launch at most, only when the backend
// says the stored deadline has really arrived. It never opens on top of
// another modal (it waits for the first free moment) and every dismissal
// path persists exactly one semantic mode.
let nudgeShown=false;
let nudgeStage=0;
let nudgeMode=null;
// True only while the dialog was opened by hand from Settings. A manual
// review is strictly view-only: it must never touch the stored first-run/
// cadence state, so its close handler skips the dismiss command entirely.
let nudgeManual=false;
function updateSupportNudgeChannel() {
  const link=$("support-nudge-channel");
  if(link)link.href=AUTHOR.youtube;
}
function configureSupportNudge(stage) {
  nudgeStage=stage;
  updateSupportNudgeChannel();
  const row=$("support-nudge-remind-row"),box=$("support-nudge-remind");
  if(stage>=2){row.hidden=true;return;}
  row.hidden=false;box.checked=true;
  setText("support-nudge-remind-label",t(stage===0?"nudge.remind10":"nudge.remind30"));
}
async function maybeShowSupportNudge() {
  if(nudgeShown)return;
  let state_;
  try{state_=await invoke("get_support_nudge");}
  catch(error){console.error("Support nudge state unavailable",error);return;}
  if(!state_?.show)return;
  const dialog=$("support-nudge-dialog");
  const attempt=()=>{
    if(nudgeShown)return;
    if(document.querySelector("dialog[open]")){setTimeout(attempt,1500);return;}
    nudgeShown=true;configureSupportNudge(state_.stage);dialog.showModal();
  };
  attempt();
}
function closeSupportNudge(mode) {
  nudgeMode=mode||null;
  $("support-nudge-dialog").close();
}
// "Mostra schermata di benvenuto" (Settings): shows the exact same dialog
// the first-run/reminder path shows, but in view-only mode. The
// state-changing controls (the "remind me" checkbox and the permanent
// opt-out) are hidden for this one viewing so it is impossible to alter
// the stored state by mistake, and the close handler below skips the
// dismiss command while nudgeManual is set.
async function openSupportNudgeManually() {
  const dialog=$("support-nudge-dialog");
  if(dialog.open)return;
  nudgeManual=true;
  nudgeShown=true;
  updateSupportNudgeChannel();
  // Read-only peek at the stored stage so the controls can be restored with
  // the right label afterwards; this never writes anything.
  try{const current=await invoke("get_support_nudge");if(current)nudgeStage=current.stage;}catch(error){console.error("Support nudge state unavailable",error);}
  $("support-nudge-remind-row").hidden=true;
  $("support-nudge-never").hidden=true;
  dialog.showModal();
}
function refreshSupportNudgeLanguage() {
  if(!nudgeShown)return;
  const row=$("support-nudge-remind-row");
  if(row.hidden)return;
  setText("support-nudge-remind-label",t(nudgeStage===0?"nudge.remind10":"nudge.remind30"));
}
const PAGES = {
  overview: {content:"main-content", nav:"overview-nav", breadcrumb:"breadcrumb.home", onEnter:enterOverview},
  performance: {content:"performance-page", nav:"performance-nav", breadcrumb:"breadcrumb.performance", onEnter:loadPerformance},
  software: {content:"software-page", nav:"software-nav", breadcrumb:"breadcrumb.software", onEnter:loadSoftware},
  programs: {content:"programs-page", nav:"programs-nav", breadcrumb:"breadcrumb.programs", onEnter:loadPrograms},
  about: {content:"about-page", nav:"about-nav", breadcrumb:"breadcrumb.about", onEnter:renderAbout},
  assistance: {content:"assistance-page", nav:"assistance-nav", breadcrumb:"breadcrumb.assistance", onEnter:enterAssistance},
};
function setPage(page) {
  if(!PAGES[page])return;
  state.page=page;
  for(const [key,config] of Object.entries(PAGES)){
    const active=key===page;
    $(config.content).hidden=!active;
    $(config.nav).classList.toggle("active",active);
    $(config.nav).toggleAttribute("aria-current",active);
  }
  document.querySelector(".breadcrumb span").dataset.i18n=PAGES[page].breadcrumb;
  applyTranslations(document.querySelector(".topbar"));
  if(page!=="overview"){clearTimeout(aiTimer);aiTimer=null;}
  PAGES[page].onEnter();
}
// The assistance page embeds Gregorio's public Google Form. The iframe is
// only pointed at the form the first time the page is opened, so the app
// never makes a network request for a page the user does not visit; the
// placeholder text stays until the form has really loaded. The form itself
// is Google's own page: submissions, validation and consent are entirely
// Google Forms' job, nothing is sent through the Toolbox.
function enterAssistance() {
  const frame=$("assistance-form"),loading=$("assistance-loading");
  if(!frame)return;
  const reveal=()=>{if(loading)loading.hidden=true;};
  if(frame.dataset.loaded==="true"){reveal();return;}
  frame.addEventListener("load",()=>{frame.dataset.loaded="true";reveal();},{once:true});
  if(!frame.src&&frame.dataset.src)frame.src=frame.dataset.src;
}
function enterOverview() {
  refreshSystem();
  if(aiSnapshotIsStale(state.ai))refreshAi();
  else scheduleAiRefresh();
}
function aiWindowLabel(window) {
  const known={session:"quota.fiveHours",weekly:"quota.week",tertiary:"quota.month",month:"quota.month",daily:"quota.day"};
  const key=known[window.kind];
  return key?t(key):(window.label||t("quota.current"));
}
function aiWindow(window) {
  // The engine already preserved usedPercent and remainingPercent as
  // upstream reported them; the UI shows the remaining bar exactly like the
  // rest of the Toolbox and never invents a conversion of its own.
  const remaining=window.remainingPercent;
  const row=element("div","quota-window");
  row.append(element("span","",aiWindowLabel(window)),element("span","",t("quota.remaining",{value:remaining.toLocaleString(locale(),{maximumFractionDigits:1})})),setProgress(remaining,null,quotaLevel(remaining)));
  if(window.resetsAt){
    const reset=new Date(window.resetsAt);
    if(!Number.isNaN(reset.valueOf())){
      const label=element("small","",t("quota.reset",{date:new Intl.DateTimeFormat(locale(),{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"}).format(reset)}));
      label.style.gridColumn="1 / -1"; row.append(label);
    }
  }
  return row;
}
function aiStateText(provider) {
  return {
    detected:t("ai.detected"),
    notDetected:t("ai.notDetected"),
    unauthenticated:t("ai.unauthenticated"),
    unsupportedOs:t("ai.notSupported"),
    noFetchStrategy:t("ai.notSupported"),
    temporary:t("ai.temporary"),
    unavailable:t("ai.usageUnavailable"),
  }[provider.status]||t("ai.usageUnavailable");
}
// One simple translation of the engine's own state; the engine's own
// message is always preserved underneath as the technical detail, so
// nothing upstream said is hidden or reinterpreted.
function aiReasonText(provider) {
  if(provider.status==="unsupportedOs")return t("ai.reasonUnsupportedOs");
  if(provider.status==="noFetchStrategy")return t("ai.reasonNoFetchStrategy");
  return provider.diagnostic||null;
}
function claudeNeedsReconnect(provider) {
  return provider.id==="claude"&&provider.status==="noFetchStrategy"&&state.claudeAuth?.loginRequired;
}
function renderAi() {
  const snapshot=state.ai;
  if(!snapshot)return;
  $("ai-list").replaceChildren(...snapshot.providers.map(provider=>{
    const row=element("div","ai-row");
    row.append(icon("ai"),element("strong","",provider.name),element("span","ai-state",claudeNeedsReconnect(provider)?t("ai.claudeReconnectNeeded"):aiStateText(provider)));
    const metadata=element("div","ai-metadata");
    if(provider.plan)metadata.append(element("small","ai-plan",t("ai.plan",{plan:provider.plan})));
    if(provider.available&&provider.source)metadata.append(element("small","ai-source",t("ai.source",{name:provider.name})));
    if(metadata.childElementCount)row.append(metadata);
    const reason=aiReasonText(provider);
    if(reason)row.append(element("small","ai-reason",reason));
    if(claudeNeedsReconnect(provider)){
      const reconnect=element("button","ai-reconnect-button",state.claudeReconnectBusy?t("ai.reconnecting"):t("ai.reconnect"));
      reconnect.type="button";reconnect.disabled=state.claudeReconnectBusy;
      reconnect.addEventListener("click",reconnectClaudeCode);row.append(reconnect);
    }
    if(provider.available){
      // A reading that is not authoritative (CodexBar's own local heuristics,
      // e.g. OpenCode Go without an API key) must never look like a real
      // quota bar: same rule the Toolbox already applies to every other
      // number it cannot fully vouch for.
      if(provider.confidence!=="authoritative"){
        row.append(element("small","ai-estimate",t("ai.estimatedNotice")));
      }else{
        const quota=element("div","ai-quota");
        for(const window of provider.windows)quota.append(aiWindow(window));
        row.append(quota);
      }
    }
    return row;
  }));
  setText("ai-provider-summary",`${snapshot.providers.filter(provider=>provider.status!=="notDetected").length} provider rilevati`);
  const updated=$("ai-updated");
  const generated=snapshot.generatedAt?new Date(snapshot.generatedAt):null;
  let label=generated&&!Number.isNaN(generated.valueOf())?t("ai.updated",{time:new Intl.DateTimeFormat(locale(),{timeStyle:"short"}).format(generated)}):t("ai.detecting");
  if(snapshot.stale)label=t("ai.staleNotice",{minutes:Math.round((snapshot.ageSeconds||0)/60)});
  else if(snapshot.engineError&&!snapshot.providers.some(provider=>provider.available))label=t("ai.readFailed");
  setText("ai-updated",label);
  updated.title=snapshot.engineVersion?`CodexBar ${snapshot.engineVersion}`:"";
  renderAiRefreshButton();
}
function renderAiRefreshButton() {
  const button=$("ai-refresh-button");if(!button)return;
  button.disabled=state.aiBusy;
  button.classList.toggle("refreshing",state.aiBusy);
  button.setAttribute("aria-busy",String(state.aiBusy));
}
function setupAccordions() {
  for(const toggle of document.querySelectorAll(".accordion-toggle")) {
    const content=$(toggle.getAttribute("aria-controls"));
    if(!content)continue;
    const setOpen=open=>{toggle.setAttribute("aria-expanded",String(open));content.hidden=!open;toggle.closest(".accordion-panel")?.classList.toggle("accordion-open",open);};
    toggle.addEventListener("click",()=>setOpen(toggle.getAttribute("aria-expanded")!=="true"));
    setOpen(false);
  }
}
function scheduleAiRefresh() {
  clearTimeout(aiTimer);aiTimer=null;
  if(document.hidden||state.page!=="overview"||!state.ai)return;
  aiTimer=setTimeout(()=>refreshAi(),Math.max(1000,aiNextRefreshDelayMs(state.ai)));
}
function refreshAi() {
  if(aiRefreshFlight)return aiRefreshFlight;
  clearTimeout(aiTimer);aiTimer=null;state.aiBusy=true;renderAiRefreshButton();
  aiRefreshFlight=Promise.resolve()
    .then(()=>invoke("get_ai_usage"))
    .then(async snapshot=>{
      state.ai=snapshot;
      const claude=snapshot.providers?.find(provider=>provider.id==="claude");
      if(claude?.status==="noFetchStrategy"){
        try{state.claudeAuth=await invoke("get_claude_auth_status");}catch{state.claudeAuth=null;}
      }else state.claudeAuth=null;
    })
    .catch(error=>{console.error("AI engine snapshot failed",error);})
    .finally(()=>{
      state.aiBusy=false;aiRefreshFlight=null;renderAi();scheduleAiRefresh();
    });
  return aiRefreshFlight;
}
async function reconnectClaudeCode() {
  if(state.claudeReconnectBusy)return;
  state.claudeReconnectBusy=true;renderAi();
  try{await invoke("reconnect_claude_code");}
  catch(error){console.error("Claude Code reconnect could not start",error);}
  finally{state.claudeReconnectBusy=false;renderAi();}
}
function renderFeedsManager() {
  const list=$("feed-manager-list"); list.replaceChildren();
  if(!state.feeds.length){list.append(element("p","feed-note",t("feed.none")));return;}
  for(const feed of state.feeds){
    const row=element("article","feed-manager-row");
    const info=element("div"); info.append(element("strong","",feed.name),element("small","",feed.url));
    const actions=element("div","feed-actions");
    const edit=element("button","",t("action.edit")); edit.type="button"; edit.addEventListener("click",()=>openFeedEditor(feed));
    const remove=element("button","danger-action",t("action.delete")); remove.type="button";
    remove.addEventListener("click",()=>{
      actions.replaceChildren(element("span","confirm-copy",t("feed.deleteConfirm",{name:feed.name})));
      const cancel=element("button","",t("action.cancel")); cancel.type="button"; cancel.addEventListener("click",renderFeedsManager);
      const confirm=element("button","danger-action",t("action.confirm")); confirm.type="button";
      confirm.addEventListener("click",async()=>{state.feeds=await invoke("delete_feed",{id:feed.id});renderFeedsManager();renderNews();});
      actions.append(cancel,confirm);
    });
    actions.append(edit,remove); row.append(info,actions); list.append(row);
  }
}
function articleDate(value) {
  if(!value)return "";
  const date=new Date(value); return Number.isNaN(date.valueOf())?"":new Intl.DateTimeFormat(locale(),{dateStyle:"medium"}).format(date);
}
function renderNews() {
  const status=$("news-status"),list=$("news-list"); list.replaceChildren();
  if(!state.feeds.length){
    status.textContent=t("feed.emptyTitle")+" "+t("feed.emptyText"); setText("news-updated",null); return;
  }
  const articles=state.news?.articles||[];
  const firstError=state.news?.errors?.[0];
  status.textContent=state.rssBusy?t("feed.refreshing"):firstError?feedErrorText(firstError.code):articles.length?"":t("feed.failed");
  const groups=new Map();for(const article of articles){const group=groups.get(article.source)||[];if(group.length<2)group.push(article);groups.set(article.source,group);}
  for(const [source,group] of groups){
    const section=element("section","news-source");section.append(element("strong","news-source-title",source));
    for(const article of group){
      const item=element("article","news-item");
      const link=element("button","news-link",article.title); link.type="button";link.title=t("feed.open");
      link.addEventListener("click",()=>invoke("open_external_url",{url:article.url}));
      const meta=element("small","",articleDate(article.published_at)||"");
      item.append(link,meta);section.append(item);
    } list.append(section);
  }
  setText("news-updated",state.news?.refreshed_at?t("feed.updated",{time:new Intl.DateTimeFormat(locale(),{timeStyle:"short"}).format(new Date(state.news.refreshed_at*1000))}):null);
}
function feedErrorText(code) {
  if(code==="maximum_feeds_reached")return t("feed.maximumReached");
  if(code?.startsWith("http_status_"))return t("feed.httpStatus",{status:code.slice("http_status_".length)});
  const known={network_error:"feed.networkError",timeout:"feed.timeout",redirect_error:"feed.redirectError",tls_error:"feed.tlsError",invalid_feed:"feed.invalidFeed",unsupported_feed:"feed.unsupportedFeed",empty_feed:"feed.emptyFeed"};
  return t(known[code]||"feed.loadFailed");
}
async function loadFeeds() {
  try{state.feeds=await invoke("list_feeds");state.news=await invoke("cached_news");renderFeedsManager();renderNews();if(state.feeds.length)await refreshFeeds(false);}
  catch{setText("news-status",t("feed.loadFailed"));}
}
async function refreshFeeds(force=true) {
  if(state.rssBusy)return;
  clearTimeout(rssTimer);state.rssBusy=true;renderNews();
  try{state.news=await invoke("refresh_feeds",{force});}
  catch{state.news=null;setText("news-status",t("feed.loadFailed"));}
  finally{state.rssBusy=false;renderNews();rssTimer=setTimeout(()=>refreshFeeds(false),RSS_REFRESH_SECONDS*1000);}
}
function openFeedEditor(feed=null) {
  $("feed-id").value=feed?.id||"";$("feed-name").value=feed?.name||"";$("feed-url").value=feed?.url||"";
  setText("feed-form-error",null);
  $("feed-editor-title").dataset.i18n=feed?"feed.editTitle":"feed.addTitle";applyTranslations($("feed-editor-dialog"));
  $("feed-manager-dialog").close();$("feed-editor-dialog").showModal();$("feed-name").focus();
}
function renderUpdater() {
  const status=state.updater;if(!status)return;
  setText("update-version",status.installedVersion);
  setText("update-channel",t({appImage:"updates.appImage",deb:"updates.deb",aur:"updates.aur"}[status.channel]||"updates.packageManager"));
  const messages={notConfigured:"updates.notConfigured",packageManaged:"updates.packageManaged",idle:"updates.idle",current:"updates.current",available:"updates.available",checking:"updates.checking",downloading:"updates.downloading",ready:"updates.ready",error:"updates.error"};
  setText("update-message",t(messages[status.state]||"updates.error",{version:status.availableVersion||""}));
  const check=$("check-update-button"),install=$("install-update-button");
  check.disabled=state.updateBusy||["notConfigured","packageManaged","checking","downloading"].includes(status.state);
  install.hidden=status.state!=="available";install.disabled=state.updateBusy;
  $("update-progress").hidden=status.state!=="downloading";
}
async function loadUpdater(auto=false) {
  try{
    state.updater=await invoke("get_update_status");renderUpdater();
    if(auto&&state.updater.state==="idle")await checkUpdater();
  }catch{state.updater={state:"error",installedVersion:"",channel:"unknown"};renderUpdater();}
}
async function checkUpdater() {
  if(state.updateBusy)return;
  state.updateBusy=true;state.updater={...state.updater,state:"checking"};renderUpdater();
  try{state.updater=await invoke("check_for_update");}
  catch{state.updater={...state.updater,state:"error"};}
  finally{state.updateBusy=false;renderUpdater();clearTimeout(updateTimer);if(state.updater.state!=="notConfigured"&&state.updater.state!=="packageManaged")updateTimer=setTimeout(checkUpdater,21600000);}
}
async function installUpdater() {
  if(state.updateBusy)return;
  state.updateBusy=true;state.updater={...state.updater,state:"downloading"};renderUpdater();
  try{await invoke("download_and_install_update");}
  catch{state.updater={...state.updater,state:"error"};state.updateBusy=false;renderUpdater();}
}
function renderStartupSettings() {
  const startup=state.startup;if(!startup)return;
  const login=$("startup-launch-at-login"),hidden=$("startup-start-hidden");
  login.checked=!!startup.launchAtLogin;login.disabled=state.startupBusy;
  hidden.checked=!!startup.startHidden;hidden.disabled=state.startupBusy||!startup.launchAtLogin||!startup.trayAvailable;
}
async function loadStartupSettings() {
  try{state.startup=await invoke("get_startup_settings");}
  catch(error){console.error("Startup settings unavailable",error);state.startup=null;}
  renderStartupSettings();
}
async function setLaunchAtLogin(enabled) {
  if(state.startupBusy)return;
  state.startupBusy=true;renderStartupSettings();
  try{state.startup=await invoke("set_launch_at_login",{enabled});}
  catch(error){console.error("Start at login change failed",error);}
  finally{state.startupBusy=false;renderStartupSettings();}
}
async function setStartHidden(enabled) {
  if(state.startupBusy)return;
  state.startupBusy=true;renderStartupSettings();
  try{state.startup=await invoke("set_start_hidden",{enabled});}
  catch(error){console.error("Start hidden change failed",error);}
  finally{state.startupBusy=false;renderStartupSettings();}
}
async function refreshSystem() {
  clearTimeout(systemTimer);
  const includeProcesses=state.page==="overview"&&!document.hidden;
  try{state.snapshot=await invoke("get_system_snapshot",{includeProcesses});renderSnapshot();}catch{console.error("System snapshot failed");}
  finally{systemTimer=setTimeout(refreshSystem,3000);}
}
async function refreshPing() {
  clearTimeout(pingTimer);
  try{state.ping=await invoke("get_network_latency");}catch{state.ping=null;}
  renderSnapshot();pingTimer=setTimeout(refreshPing,10000);
}
function setupTheme() {
  const system=matchMedia("(prefers-color-scheme: dark)");let preference;
  try{preference=localStorage.getItem("mg-theme");}catch{}
  if(!["light","dark"].includes(preference))preference="system";
  const apply=()=>{
    const theme=preference==="system"?(system.matches?"dark":"light"):preference;
    document.documentElement.dataset.theme=theme;
    const label=theme==="dark"?t("theme.toLight"):t("theme.toDark");
    $("theme-toggle").setAttribute("aria-label",label);$("theme-toggle").title=label;
  };
  system.addEventListener("change",()=>{if(preference==="system")apply();});
  $("theme-toggle").addEventListener("click",()=>{preference=document.documentElement.dataset.theme==="dark"?"light":"dark";try{localStorage.setItem("mg-theme",preference);}catch{}apply();});
  return apply;
}
// Global text-size control (Settings -> Aspetto). Purely a CSS custom
// property (--ui-scale), read/written through appearance-view.js exactly
// like the theme preference above: no backend command, applied instantly,
// persisted in localStorage, restored on every startup. Never a WebView
// zoom/transform: only the typographic `font`/`font-size` declarations in
// the stylesheets consume this variable (see styles.css/refinement.css).
function applyTextScale(scale) {
  document.documentElement.style.setProperty("--ui-scale",String(scale));
}
function renderTextScaleOptions(active) {
  const container=$("text-scale-options");
  if(!container)return;
  container.replaceChildren(...TEXT_SCALE_OPTIONS.map(option=>{
    const button=element("button","",textScalePercentLabel(option));
    button.type="button";
    const isActive=Math.abs(option-active)<0.001;
    button.classList.toggle("active",isActive);
    button.setAttribute("aria-pressed",String(isActive));
    button.addEventListener("click",()=>setTextScale(option));
    return button;
  }));
}
function setTextScale(value) {
  const clamped=saveTextScale(localStorage,value);
  applyTextScale(clamped);
  renderTextScaleOptions(clamped);
}
function setupTextScale() {
  const scale=loadTextScale(localStorage);
  applyTextScale(scale);
  renderTextScaleOptions(scale);
  $("text-scale-reset").addEventListener("click",()=>setTextScale(DEFAULT_TEXT_SCALE));
}
function hideMenus() {
  $("context-menu").hidden=true;$("language-menu").hidden=true;$("language-toggle").setAttribute("aria-expanded","false");
}
function showContextMenu(event,items) {
  event.preventDefault();const menu=$("context-menu");menu.replaceChildren();
  for(const item of items){
    const button=element("button","",t(item.key));button.type="button";button.role="menuitem";button.disabled=!!item.disabled;
    button.addEventListener("click",()=>{hideMenus();item.action();});menu.append(button);
  }
  menu.hidden=false;
  const box=menu.getBoundingClientRect();
  menu.style.left=Math.max(8,Math.min(event.clientX,innerWidth-box.width-8))+"px";
  menu.style.top=Math.max(8,Math.min(event.clientY,innerHeight-box.height-8))+"px";
  menu.querySelector("button:not(:disabled)")?.focus();
}
function setupMenus() {
  document.addEventListener("contextmenu",event=>{
    const ai=event.target.closest("#ai-panel"),news=event.target.closest("#news-panel");
    if(ai)showContextMenu(event,[{key:"ai.refreshNow",disabled:state.aiBusy,action:()=>refreshAi()}]);
    else if(news)showContextMenu(event,[{key:"feed.add",action:()=>openFeedEditor()},{key:"feed.manage",action:()=>{$("feed-manager-dialog").showModal();renderFeedsManager();}},{key:"feed.refreshNow",disabled:state.rssBusy,action:refreshFeeds}]);
    else {event.preventDefault();hideMenus();}
  });
  document.addEventListener("pointerdown",event=>{if(!event.target.closest(".paper-menu")&&!event.target.closest("#language-toggle"))hideMenus();});
  $("language-toggle").addEventListener("click",event=>{event.stopPropagation();const menu=$("language-menu");const open=menu.hidden;hideMenus();menu.hidden=!open;$("language-toggle").setAttribute("aria-expanded",String(open));if(open)menu.querySelector("button")?.focus();});
  for(const button of document.querySelectorAll("[data-language]"))button.addEventListener("click",()=>{setLanguage(button.dataset.language);hideMenus();});
}
function setupDialogs() {
  $("overview-nav").addEventListener("click",()=>setPage("overview"));
  $("performance-nav").addEventListener("click",()=>setPage("performance"));
  $("software-nav").addEventListener("click",()=>setPage("software"));
  $("programs-nav").addEventListener("click",()=>setPage("programs"));
  $("gaming-toggle-button").addEventListener("click",toggleGamingPanel);
  $("geforce-install-button").addEventListener("click",installGeforce);
  $("dns-toggle-button").addEventListener("click",toggleDnsPanel);
  $("dns-cancel-button").addEventListener("click",closeDnsPanel);
  $("dns-apply-button").addEventListener("click",applyDnsProvider);
  for(const input of document.querySelectorAll('input[name="dns-provider"]'))input.addEventListener("change",event=>{state.dnsSelection=event.target.value;renderDns();});
  $("winboat-toggle-button").addEventListener("click",toggleWinboatPanel);
  $("winboat-install-button").addEventListener("click",confirmWinboat);
  $("winboat-open-button").addEventListener("click",openWinboat);
  $("winboat-close-button").addEventListener("click",closeWinboatPanel);
  $("winboat-reboot-now-button").addEventListener("click",rebootNow);
  $("winboat-reboot-later-button").addEventListener("click",()=>{$("winboat-reboot").hidden=true;});
  $("apps-confirm-confirm").addEventListener("click",confirmAppsBootstrap);
  $("apps-confirm-cancel").addEventListener("click",()=>{state.appsPendingConfirm=null;});
  // Closed only by X, Escape or an outside click (the generic dialog
  // handlers below already cover all three): never by a periodic refresh.
  // Restores focus to the "Scopri di più" button that opened it, exactly
  // like a native disclosure would.
  $("advanced-help-dialog").addEventListener("close",()=>{
    state.advancedHelpId=null;
    advancedHelpOpener?.focus();
  });
  $("assistance-nav").addEventListener("click",()=>setPage("assistance"));
  $("about-nav").addEventListener("click",()=>setPage("about"));
  // "Vai al canale" only opens YouTube: the popup stays open and the
  // reminder state is untouched (its own click handler lives in author.js).
  $("support-nudge-x").addEventListener("click",()=>closeSupportNudge());
  $("support-nudge-later").addEventListener("click",()=>closeSupportNudge());
  $("support-nudge-never").addEventListener("click",()=>closeSupportNudge("never"));
  $("support-nudge-dialog").addEventListener("close",()=>{
    // A manual review from Settings is view-only: restore the controls the
    // automatic path needs for its next real appearance and persist nothing.
    if(nudgeManual){
      nudgeManual=false;
      $("support-nudge-never").hidden=false;
      configureSupportNudge(nudgeStage);
      return;
    }
    const row=$("support-nudge-remind-row");
    const mode=nudgeMode||((!row.hidden&&$("support-nudge-remind").checked)?"remind":"close");
    nudgeMode=null;
    invoke("dismiss_support_nudge",{mode}).catch(error=>console.error("Support nudge update failed",error));
  });
  $("welcome-show-button").addEventListener("click",openSupportNudgeManually);
  listen("gaming-progress",event=>{
    if(!state.gamingBusy)return;
    state.gamingProgress=[...state.gamingProgress,event.payload];
    renderGamingProgress(state.gamingProgress);
  });
  listen("winboat-progress",event=>{
    if(!state.winboatBusy)return;
    state.winboatProgress=[...state.winboatProgress,event.payload];
    renderWinboatProgress(state.winboatProgress);
  });
  for(const card of document.querySelectorAll("[data-profile]")){const activate=()=>applyPerformanceProfile(card.dataset.profile);card.addEventListener("click",event=>{if(!event.target.closest("details"))activate()});card.addEventListener("keydown",event=>{if((event.key==="Enter"||event.key===" ")&&!event.target.closest("details")){event.preventDefault();activate()}})}
  $("performance-restore").addEventListener("click",restorePerformanceState);
  $("disk-card").addEventListener("click",()=>$("disks-dialog").showModal());
  $("settings-button").addEventListener("click",async()=>{$("updates-dialog").showModal();await loadUpdater(false);await loadStartupSettings();});
  $("startup-launch-at-login").addEventListener("change",event=>setLaunchAtLogin(event.target.checked));
  $("startup-start-hidden").addEventListener("change",event=>setStartHidden(event.target.checked));
  for(const button of document.querySelectorAll("[data-close]"))button.addEventListener("click",()=>$(button.dataset.close).close());
  for(const id of ["close-dialog","dialog-close-button"])$(id).addEventListener("click",()=>$("disks-dialog").close());
  for(const dialog of document.querySelectorAll("dialog"))dialog.addEventListener("click",event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close();}});
  $("manager-add-feed").addEventListener("click",()=>openFeedEditor());
  $("ai-refresh-button").addEventListener("click",event=>{event.stopPropagation();refreshAi();});
  $("ai-refresh-button").addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();event.stopPropagation();refreshAi();}});
  setupAccordions();
  $("feed-form").addEventListener("submit",async event=>{
    event.preventDefault();const name=$("feed-name").value.trim(),url=$("feed-url").value.trim();
    if(!name||!url){setText("feed-form-error",t("feed.required"));return;}
    if(!validHttpUrl(url)){setText("feed-form-error",t("feed.invalidUrl"));return;}
    const submit=event.submitter;submit.disabled=true;
    try{state.feeds=await invoke("save_feed",{id:$("feed-id").value||null,name,url});$("feed-editor-dialog").close();renderFeedsManager();await refreshFeeds(true);}
    catch(error){setText("feed-form-error",feedErrorText(String(error)));}
    finally{submit.disabled=false;}
  });
  $("check-update-button").addEventListener("click",checkUpdater);
  $("install-update-button").addEventListener("click",installUpdater);
  $("autostart-toggle-button").addEventListener("click",toggleAutostartManager);
  $("autostart-search").addEventListener("input",event=>{state.autostartSearch=event.target.value;renderAutostart();});
  for(const button of document.querySelectorAll(".autostart-filters button")){
    button.addEventListener("click",()=>{
      state.autostartFilter=button.dataset.filter;
      for(const b of document.querySelectorAll(".autostart-filters button"))b.classList.toggle("active",b===button);
      renderAutostart();
    });
  }
  $("cleanup-scan-button").addEventListener("click",async()=>{state.cleanupExpanded=true;await scanCleanup();});
}
function applyLanguage() {
  applyTranslations();$("language-code").textContent=getLanguage().toUpperCase();
  for(const item of document.querySelectorAll(".nav-item")){const label=item.querySelector("span")?.textContent;if(label){item.setAttribute("aria-label",label);item.title=label;}}
  updateClock();renderSnapshot();renderPerformance();renderSoftware();renderAutostart();renderCleanup();renderGaming();renderGeforce();renderDns();renderWinboat();renderApps();renderAi();renderNews();renderFeedsManager();renderUpdater();renderStartupSettings();renderAbout();refreshSupportNudgeLanguage();themeApply();
}
document.addEventListener("visibilitychange",()=>{
  if(document.hidden){clearTimeout(aiTimer);aiTimer=null;return;}
  refreshSystem();
  if(state.page==="performance")refreshPerformanceLive();
  if(state.page==="overview"){if(aiSnapshotIsStale(state.ai))refreshAi();else scheduleAiRefresh();}
});
const themeApply=setupTheme();
setupTextScale();
setupMenus();setupDialogs();applyTranslations();onLanguageChange(applyLanguage);applyLanguage();
updateClock();setInterval(updateClock,30000);
refreshSystem();refreshPing();refreshAi();loadFeeds();loadUpdater(true);
performanceTimer=setInterval(refreshPerformanceLive,3000);
requestAnimationFrame(()=>setTimeout(maybeShowSupportNudge,600));
