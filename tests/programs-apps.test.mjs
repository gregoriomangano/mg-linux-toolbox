import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const removed = ["filelight","remmina","gparted","filezilla","kdenlive","obs-studio","audacity","haruna","docker","podman","lazygit","git-lfs","git","cmake","clamav","firejail","keepassxc","wireshark","nmap","wireguard","signal","telegram","discord","thunderbird","nheko"];

test("Programs contains exactly nine direct cards and no catalog UI", () => {
  const html=read("../src/index.html");
  const page=html.slice(html.indexOf('id="programs-page"'),html.indexOf('id="assistance-page"'));
  assert.equal((page.match(/class="[^"]*programs-card/g)||[]).length,9);
  for(const id of ["gradia","upscayl","curtail","ferdium","kde-connect"])assert.match(page,new RegExp(`data-app="${id}"`));
  assert.doesNotMatch(html,/Altre app|Sfoglia app|apps-catalog-dialog|apps-search|apps-filters|apps-list/);
});

test("the fixed app list contains only the five requested opaque ids", async()=>{
  const {APP_IDS}=await import("../src/js/apps-view.js");
  assert.deepEqual(APP_IDS,["gradia","upscayl","curtail","ferdium","kde-connect"]);
});

test("frontend sends only opaque ids/methods to backend commands",()=>{
  const main=read("../src/main.js");
  assert.match(main,/invoke\("get_apps_snapshot"\)/);
  assert.doesNotMatch(main,/get_apps_catalog|get_apps_environment/);
  assert.match(main,/invoke\("install_app",\{id,method\}\)/);
  assert.match(main,/invoke\("remove_app",\{id,via\}\)/);
  assert.match(main,/invoke\("open_app",\{id\}\)/);
  assert.match(main,/invoke\("grant_app_snap_permission",\{id,permission\}\)/);
  assert.doesNotMatch(main,/flatpak\s+(install|uninstall)|apt-get|dnf5?\s|pacman\s|zypper\s|sudo\s|sh -c/);
  // The permission token is never a user-supplied value either.
  assert.doesNotMatch(main,/prompt\(|permission:\s*(input|value)/);
});

test("backend allowlists exact Flatpak IDs, exact Snap names and KDE Connect distro packages",()=>{
  const rust=read("../src-tauri/src/apps_catalog.rs");
  for(const id of ["be.alexandervanhee.gradia","org.upscayl.Upscayl","com.github.huluti.Curtail","org.ferdium.Ferdium"])assert.match(rust,new RegExp(id.replaceAll(".","\\.")));
  for(const name of ['"gradia"','"upscayl"','"curtail"','"ferdium"'])assert.match(rust,new RegExp(`snap_name: Some\\(${name}\\)`));
  for(const pkg of ['"kdeconnect"','"kde-connect"','"kdeconnect-kde"'])assert.match(rust,new RegExp(pkg));
  assert.match(rust,/pub fn all\(\) -> \[Self; 5\]/);
  // Curtail is the only Snap flagged as third-party maintained.
  assert.match(rust,/snap_name: Some\("curtail"\),\s*snap_community: true/);
  assert.match(rust,/snap_name: Some\("gradia"\),\s*snap_community: false/);
});

test("KDE Connect never gets a Flatpak or Snap offer, only native",()=>{
  const rust=read("../src-tauri/src/apps_catalog.rs");
  assert.match(rust,/InstallMethod::Native => id == AppId::KdeConnect/);
  assert.match(rust,/InstallMethod::Flatpak => flatpak_id\(id\)\.is_some\(\)/);
  assert.match(rust,/InstallMethod::Snap => snap_name\(id\)\.is_some\(\)/);
});

test("removed apps are absent from public app code and translations",()=>{
  const rust=read("../src-tauri/src/apps_catalog.rs").split("#[cfg(test)]")[0];
  const translations=read("../src/js/i18n.js").replace(/programs\.winboat[^\n]*/g,"");
  const sources=[rust,"../src-tauri/src/apps_ops.rs","../src/js/apps-view.js"].map(value=>value.startsWith("../")?read(value):value).join("\n").concat(translations).toLowerCase();
  for(const id of removed)assert.ok(!sources.includes(`\"${id}\"`),`${id} remains exposed`);
});

test("all five app icons are local and documented",async()=>{
  const {APP_IDS,bundledIconPath}=await import("../src/js/apps-view.js");
  const licences=read("../THIRD_PARTY_LICENSES.md");
  for(const id of APP_IDS){const path=bundledIconPath(id);assert.match(path,/^assets\/apps\/[a-z0-9-]+\.svg$/);assert.ok(existsSync(new URL(`../src/${path}`,import.meta.url)));assert.ok(licences.includes(path.split("/").pop()));}
});

test("the four apps show explicit Flatpak/Snap buttons, never one generic Installa button that picks for the user",()=>{
  const html=read("../src/index.html");
  const main=read("../src/main.js");
  assert.doesNotMatch(html,/id="apps-catalog-dialog"/);
  assert.match(main,/function installFormatButton/);
  assert.match(main,/t\("apps\.installFlatpak"\)/);
  assert.match(main,/t\("apps\.installSnap"\)/);
});

test("each install button reads the shared, backend-resolved environment (never re-derived in JS) to pick ready/confirm/blocked",()=>{
  const main=read("../src/main.js");
  assert.match(main,/invoke\("get_apps_snapshot"\)/);
  assert.match(main,/isPlanReady\(env\?\.flatpakPlan\)/);
  assert.match(main,/isPlanReady\(env\?\.snapPlan\)/);
  assert.match(main,/isPlanBlocked\(env\?\.flatpakPlan\)/);
  assert.match(main,/isPlanBlocked\(env\?\.snapPlan\)/);
  assert.match(main,/apps-confirm-dialog/);
  // No distro/package/family logic is ever computed in the frontend.
  assert.doesNotMatch(main,/DistroFamily|linuxmint|nosnap|opensuse|arch_aur/i);
});

test("Curtail's Snap choice carries the discreet third-party note, other apps do not",async()=>{
  const {communityNote}=await import("../src/js/apps-view.js");
  const {dictionaries}=await import("../src/js/i18n.js");
  assert.equal(communityNote({community:true},(k)=>dictionaries.it[k]),"Snap mantenuto da terzi");
  assert.equal(communityNote({community:false},(k)=>dictionaries.it[k]),null);
  assert.equal(communityNote(null,(k)=>dictionaries.it[k]),null);
});

test("status text distinguishes not installed, single-format and dual-format installs",async()=>{
  const {statusText,installedVias}=await import("../src/js/apps-view.js");
  const t=(k)=>({"apps.installedViaFlatpak":"flatpak","apps.installedViaSnap":"snap","apps.installedBoth":"both","apps.readyToInstall":"ready","apps.notAvailable":"none"}[k]||k);
  assert.equal(statusText({installed:[],methods:[{method:"flatpak"}]},t),"ready");
  assert.equal(statusText({installed:[{via:"flatpak"}],methods:[]},t),"flatpak");
  assert.equal(statusText({installed:[{via:"snap"}],methods:[]},t),"snap");
  assert.equal(statusText({installed:[{via:"flatpak"},{via:"snap"}],methods:[]},t),"both");
  assert.deepEqual(installedVias({installed:[{via:"flatpak"},{via:"snap"}]}),["flatpak","snap"]);
});

test("removal always names the exact installed format, never both at once, and dual installs offer a choice",()=>{
  const main=read("../src/main.js");
  assert.match(main,/removeApp\(id,vias\[0\]\)/);
  assert.match(main,/removeApp\(id,"flatpak"\)/);
  assert.match(main,/removeApp\(id,"snap"\)/);
  assert.match(main,/apps\.removeFlatpak/);
  assert.match(main,/apps\.removeSnap/);
});

test("a single installed format never shows a prominent second install button, only a secondary 'other options' toggle",()=>{
  const main=read("../src/main.js");
  assert.match(main,/apps\.moreInstallOptions/);
  assert.match(main,/appsMoreOptions/);
});

test("the discreet Programs-page support summary reflects Flatpak/Snap state and configures nothing",()=>{
  const html=read("../src/index.html");
  const main=read("../src/main.js");
  assert.match(html,/id="apps-support-summary"/);
  assert.match(main,/supportSummary\(state\.appsEnvironment,t\)/);
});

test("the frontend never computes a Snap permission connect string itself",async()=>{
  const main=read("../src/main.js");
  // The permission token may only come from apps-view.js's own fixed
  // helper: main.js must never build a `snap connect` or hardcode an
  // interface name itself.
  assert.doesNotMatch(main,/snap connect|"removable-media"|"audio-record"/);
  assert.match(main,/invoke\("grant_app_snap_permission",\{id,permission\}\)/);
  const {upscaylRemovableMediaOffer}=await import("../src/js/apps-view.js");
  assert.deepEqual(upscaylRemovableMediaOffer("upscayl"),{permission:"removable-media"});
  assert.equal(upscaylRemovableMediaOffer("gradia"),null);
});

test("Snap extras are opt-in only: Upscayl gets one [Non ora]/[Consenti] prompt, Ferdium only a note, nothing is auto-granted",async()=>{
  const main=read("../src/main.js");
  const {upscaylRemovableMediaOffer,ferdiumPermissionNote}=await import("../src/js/apps-view.js");
  const {dictionaries}=await import("../src/js/i18n.js");
  // The offer/note can only ever be added after a successful snap install.
  assert.match(main,/if\(method==="snap"&&report\?\.ok\)/);
  assert.match(main,/state\.appsSnapPermissionOffer\.add\(id\)/);
  assert.match(main,/state\.appsSnapPermNote\.add\(id\)/);
  assert.match(main,/apps\.permissionUpscaylAsk/);
  assert.match(main,/apps\.permissionAllow/);
  assert.match(main,/apps\.permissionNotNow/);
  assert.equal(upscaylRemovableMediaOffer("upscayl").permission,"removable-media");
  assert.equal(upscaylRemovableMediaOffer("ferdium"),null);
  assert.equal(upscaylRemovableMediaOffer("gradia"),null);
  assert.notEqual(ferdiumPermissionNote("ferdium",(k)=>dictionaries.it[k]),null);
  assert.equal(ferdiumPermissionNote("gradia",(k)=>dictionaries.it[k]),null);
  // The Ferdium note is informational only: no connect call for it.
  assert.doesNotMatch(main,/ferdium:camera|ferdium:audio-record/);
});

test("Programs grid is 3 columns, then 2 and 1",()=>{
  const css=read("../src/styles/refinement.css");
  assert.match(css,/\.programs-grid \{grid-template-columns:repeat\(3,minmax\(0,1fr\)\)\}/);
  assert.match(css,/@media\(max-width:1050px\) \{\.programs-grid\{grid-template-columns:repeat\(2,minmax\(0,1fr\)\)\}/);
  assert.match(css,/@media\(max-width:620px\) \{\.programs-grid\{grid-template-columns:1fr\}\}/);
});

test("app descriptions match the requested concise copy",async()=>{
  const {dictionaries}=await import("../src/js/i18n.js");
  assert.equal(dictionaries.it["apps.descGradia"],"Annota e prepara gli screenshot con testo, frecce, sfondi e censura.");
  assert.equal(dictionaries.it["apps.descUpscayl"],"Aumenta la risoluzione delle immagini utilizzando modelli AI.");
  assert.equal(dictionaries.it["apps.descCurtail"],"Comprimi immagini PNG, JPEG, WebP e SVG in modo semplice.");
  assert.equal(dictionaries.it["apps.descFerdium"],"Riunisce servizi di messaggistica e web app in un'unica applicazione.");
  assert.equal(dictionaries.it["apps.descKdeConnect"],"Collega smartphone e computer per notifiche, file, appunti e controllo remoto.");
});
