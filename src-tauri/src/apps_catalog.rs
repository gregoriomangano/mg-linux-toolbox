//! Closed app catalog used by both the desktop process and privileged helper.
//! Callers provide only opaque app and method identifiers; package names,
//! Flatpak IDs and Snap names are always derived here.
use crate::{
    distro::{DistroFamily, OsRelease},
    packages::{Availability, CommandRunner, PackageManager},
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

pub const APPS_HELPER_PATH: &str = "/usr/lib/mg-linux-toolbox/mg-linux-toolbox-apps-helper";
pub const FLATHUB_REMOTE: &str = "flathub";
pub const FLATHUB_URL: &str = "https://dl.flathub.org/repo/flathub.flatpakrepo";
/// Linux Mint disables Snap by default through this apt preferences pin.
/// Detecting it is read-only and safe from an unprivileged context (the
/// directory is world-readable); only the privileged helper ever renames it,
/// and only after explicit user consent (see `apps_ops::bootstrap_snap_mint`).
pub const MINT_NOSNAP_PREF: &str = "/etc/apt/preferences.d/nosnap.pref";

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AppId {
    Gradia,
    Upscayl,
    Curtail,
    Ferdium,
    #[serde(rename = "kde-connect")]
    KdeConnect,
}

impl AppId {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "gradia" => Some(Self::Gradia),
            "upscayl" => Some(Self::Upscayl),
            "curtail" => Some(Self::Curtail),
            "ferdium" => Some(Self::Ferdium),
            "kde-connect" => Some(Self::KdeConnect),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Gradia => "gradia",
            Self::Upscayl => "upscayl",
            Self::Curtail => "curtail",
            Self::Ferdium => "ferdium",
            Self::KdeConnect => "kde-connect",
        }
    }

    pub fn all() -> [Self; 5] {
        [
            Self::Gradia,
            Self::Upscayl,
            Self::Curtail,
            Self::Ferdium,
            Self::KdeConnect,
        ]
    }
}

/// The three ways the UI can ever ask to install an app. KDE Connect only
/// ever allows `Native`; the other four only ever allow `Flatpak`/`Snap`
/// (see [`install_method_allowed`]) -- never a fourth, invented method.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum InstallMethod {
    Native,
    Flatpak,
    Snap,
}

impl InstallMethod {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "native" => Some(Self::Native),
            "flatpak" => Some(Self::Flatpak),
            "snap" => Some(Self::Snap),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Native => "native",
            Self::Flatpak => "flatpak",
            Self::Snap => "snap",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum InstalledVia {
    Native,
    Flatpak,
    Snap,
}

impl InstalledVia {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "native" => Some(Self::Native),
            "flatpak" => Some(Self::Flatpak),
            "snap" => Some(Self::Snap),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Native => "native",
            Self::Flatpak => "flatpak",
            Self::Snap => "snap",
        }
    }
}

struct AppFacts {
    id: AppId,
    category: &'static str,
    flatpak_id: Option<&'static str>,
    snap_name: Option<&'static str>,
    /// `true` only for Curtail: its Snap is published by a third party
    /// (Sameer Sharma), not by upstream author Hugo Posnic. The UI must
    /// show this discreetly next to the Snap choice, never block it.
    snap_community: bool,
    launch_binary: &'static [&'static str],
}

const CATALOG: &[AppFacts] = &[
    AppFacts {
        id: AppId::Gradia,
        category: "utility",
        flatpak_id: Some("be.alexandervanhee.gradia"),
        snap_name: Some("gradia"),
        snap_community: false,
        launch_binary: &[],
    },
    AppFacts {
        id: AppId::Upscayl,
        category: "multimedia",
        flatpak_id: Some("org.upscayl.Upscayl"),
        snap_name: Some("upscayl"),
        snap_community: false,
        launch_binary: &[],
    },
    AppFacts {
        id: AppId::Curtail,
        category: "multimedia",
        flatpak_id: Some("com.github.huluti.Curtail"),
        snap_name: Some("curtail"),
        snap_community: true,
        launch_binary: &[],
    },
    AppFacts {
        id: AppId::Ferdium,
        category: "communication",
        flatpak_id: Some("org.ferdium.Ferdium"),
        snap_name: Some("ferdium"),
        snap_community: false,
        launch_binary: &[],
    },
    AppFacts {
        id: AppId::KdeConnect,
        category: "utility",
        flatpak_id: None,
        snap_name: None,
        snap_community: false,
        launch_binary: &["kdeconnect-app", "kdeconnect-settings"],
    },
];

fn facts(id: AppId) -> &'static AppFacts {
    CATALOG.iter().find(|facts| facts.id == id).unwrap()
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstalledInfo {
    pub via: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scope: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MethodOffer {
    pub method: &'static str,
    pub immediate: bool,
    pub community: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppStatus {
    pub id: &'static str,
    pub category: &'static str,
    pub is_cli: bool,
    /// Every real installed format found for this app, never just the
    /// first one: an app can genuinely be installed as both Flatpak and
    /// Snap at once, and the UI must show that plainly instead of hiding
    /// one of the two (see `resolve_with`/task 19).
    pub installed: Vec<InstalledInfo>,
    pub methods: Vec<MethodOffer>,
    pub vulkan_uncertain: bool,
    pub icon: Option<String>,
}

pub fn flatpak_id(id: AppId) -> Option<&'static str> {
    facts(id).flatpak_id
}

pub fn snap_name(id: AppId) -> Option<&'static str> {
    facts(id).snap_name
}

pub fn snap_is_community(id: AppId) -> bool {
    facts(id).snap_community
}

pub fn repo_package_for(id: AppId, family: DistroFamily) -> Option<&'static str> {
    if id != AppId::KdeConnect {
        return None;
    }
    Some(match family {
        DistroFamily::Ubuntu | DistroFamily::Debian | DistroFamily::Arch => "kdeconnect",
        DistroFamily::Fedora => "kde-connect",
        DistroFamily::Suse => "kdeconnect-kde",
    })
}

pub fn launch_binary(id: AppId) -> &'static [&'static str] {
    facts(id).launch_binary
}

pub fn install_method_allowed(id: AppId, method: InstallMethod) -> bool {
    match method {
        InstallMethod::Native => id == AppId::KdeConnect,
        InstallMethod::Flatpak => flatpak_id(id).is_some(),
        InstallMethod::Snap => snap_name(id).is_some(),
    }
}

pub fn removal_via_allowed(id: AppId, via: InstalledVia) -> bool {
    match via {
        InstalledVia::Native => id == AppId::KdeConnect,
        InstalledVia::Flatpak => flatpak_id(id).is_some(),
        InstalledVia::Snap => snap_name(id).is_some(),
    }
}

// ---------------------------------------------------------------------
// Flatpak detection (read-only, unprivileged).
// ---------------------------------------------------------------------

pub fn flatpak_present(runner: &dyn CommandRunner) -> bool {
    runner
        .run("flatpak", &["--version"])
        .is_some_and(|output| output.success)
}

fn list_flatpak_apps(runner: &dyn CommandRunner, scope: &str) -> Option<BTreeSet<String>> {
    let output = runner.run(
        "flatpak",
        &[scope, "list", "--app", "--columns=application"],
    )?;
    output.success.then(|| {
        output
            .stdout
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .map(str::to_string)
            .collect()
    })
}

/// `true` when Flathub is configured in either scope: the app itself is
/// always installed `--user`, but a system-wide Flathub (added by the
/// distro or another tool) already satisfies the same need and must never
/// be reported as "missing" just because the user scope is empty.
fn flathub_configured(runner: &dyn CommandRunner) -> bool {
    ["--user", "--system"].iter().any(|scope| {
        runner
            .run("flatpak", &[scope, "remotes", "--columns=name"])
            .is_some_and(|output| {
                output.success
                    && output
                        .stdout
                        .lines()
                        .any(|line| line.trim() == FLATHUB_REMOTE)
            })
    })
}

fn flathub_remote_present(runner: &dyn CommandRunner, scope: &str) -> bool {
    runner
        .run("flatpak", &[scope, "remotes", "--columns=name"])
        .is_some_and(|output| {
            output.success
                && output
                    .stdout
                    .lines()
                    .any(|line| line.trim() == FLATHUB_REMOTE)
        })
}

// ---------------------------------------------------------------------
// Snap detection (read-only, unprivileged). A binary on disk is never
// enough: `snap version` must actually succeed, and snapd's own socket
// must be active, or the app cannot really be installed/opened yet.
// ---------------------------------------------------------------------

pub fn snap_present(runner: &dyn CommandRunner) -> bool {
    runner
        .run("snap", &["version"])
        .is_some_and(|output| output.success)
}

pub fn snapd_socket_active(runner: &dyn CommandRunner) -> bool {
    runner
        .run("systemctl", &["is-active", "--quiet", "snapd.socket"])
        .is_some_and(|output| output.success)
}

/// `true` only when Snap is fully usable right now: the binary responds
/// *and* snapd's socket is active. "the binary exists" alone is exactly
/// the false positive this session's spec calls out.
pub fn snap_ready(runner: &dyn CommandRunner) -> bool {
    snap_present(runner) && snapd_socket_active(runner)
}

fn list_snap_apps(runner: &dyn CommandRunner) -> Option<BTreeSet<String>> {
    let output = runner.run("snap", &["list"])?;
    output.success.then(|| {
        output
            .stdout
            .lines()
            .skip(1) // header: Name Version Rev Tracking Publisher Notes
            .filter_map(|line| line.split_whitespace().next())
            .map(str::to_string)
            .collect()
    })
}

/// Read-only check for Linux Mint's Snap block. Never written here: only
/// the privileged helper renames it, and only after explicit consent.
pub fn mint_nosnap_present() -> bool {
    Path::new(MINT_NOSNAP_PREF).is_file()
}

/// The official Snap repository for the real openSUSE release, or `None`
/// for an unrecognised variant/version -- never an old, hardcoded Leap
/// version guessed at compile time.
pub fn opensuse_snap_repo_url(release: &OsRelease) -> Option<String> {
    match release.id.as_str() {
        "opensuse-tumbleweed" => Some(
            "https://download.opensuse.org/repositories/system:/snappy/openSUSE_Tumbleweed/system:snappy.repo"
                .to_string(),
        ),
        "opensuse-leap" => release.version_id.as_ref().map(|version| {
            format!(
                "https://download.opensuse.org/repositories/system:/snappy/openSUSE_Leap_{version}/system:snappy.repo"
            )
        }),
        _ => None,
    }
}

// ---------------------------------------------------------------------
// One shared, batched detection pass per catalog refresh.
// ---------------------------------------------------------------------

#[derive(Default)]
struct CatalogProbe {
    flatpak_present: bool,
    flathub_user: bool,
    flathub_system: bool,
    flatpak_user_apps: Option<BTreeSet<String>>,
    flatpak_system_apps: Option<BTreeSet<String>>,
    snap_present: bool,
    snap_active: bool,
    snap_apps: Option<BTreeSet<String>>,
    manager: Option<PackageManager>,
    installed_packages: BTreeMap<String, bool>,
    availability: BTreeMap<String, Availability>,
}

impl CatalogProbe {
    fn build(family: Option<DistroFamily>, runner: &dyn CommandRunner) -> Self {
        let flatpak_present = flatpak_present(runner);
        let flathub_user = flatpak_present && flathub_remote_present(runner, "--user");
        let flathub_system = flatpak_present && flathub_remote_present(runner, "--system");
        let flatpak_user_apps = flatpak_present
            .then(|| list_flatpak_apps(runner, "--user"))
            .flatten();
        let flatpak_system_apps = flatpak_present
            .then(|| list_flatpak_apps(runner, "--system"))
            .flatten();
        let snap_present = snap_present(runner);
        let snap_active = snap_present && snapd_socket_active(runner);
        let snap_apps = snap_present.then(|| list_snap_apps(runner)).flatten();
        let Some(family) = family else {
            return Self {
                flatpak_present,
                flathub_user,
                flathub_system,
                flatpak_user_apps,
                flatpak_system_apps,
                snap_present,
                snap_active,
                snap_apps,
                ..Self::default()
            };
        };
        let manager = PackageManager::detect(family, runner);
        let package = repo_package_for(AppId::KdeConnect, family).unwrap();
        let installed_packages = manager
            .as_ref()
            .and_then(|pm| pm.installed_many(runner, &[package]))
            .unwrap_or_default();
        let availability = manager
            .as_ref()
            .and_then(|pm| pm.repo_availability_many(runner, &[package]))
            .unwrap_or_default();
        Self {
            flatpak_present,
            flathub_user,
            flathub_system,
            flatpak_user_apps,
            flatpak_system_apps,
            snap_present,
            snap_active,
            snap_apps,
            manager,
            installed_packages,
            availability,
        }
    }
}

fn detect_installed_with(
    id: AppId,
    family: Option<DistroFamily>,
    runner: &dyn CommandRunner,
    probe: Option<&CatalogProbe>,
) -> Vec<InstalledInfo> {
    if id == AppId::KdeConnect {
        let Some(family) = family else {
            return Vec::new();
        };
        let Some(package) = repo_package_for(id, family) else {
            return Vec::new();
        };
        let installed = probe
            .and_then(|probe| probe.installed_packages.get(package).copied())
            .or_else(|| {
                probe
                    .and_then(|probe| probe.manager.as_ref().cloned())
                    .or_else(|| PackageManager::detect(family, runner))
                    .map(|pm| pm.is_installed(runner, package))
            })
            .unwrap_or(false);
        return if installed {
            vec![InstalledInfo {
                via: InstalledVia::Native.as_str().to_string(),
                scope: None,
            }]
        } else {
            Vec::new()
        };
    }
    let mut installed = Vec::new();
    if let Some(flatpak_id) = flatpak_id(id) {
        let user = probe
            .and_then(|p| p.flatpak_user_apps.as_ref())
            .map(|apps| apps.contains(flatpak_id));
        let system = probe
            .and_then(|p| p.flatpak_system_apps.as_ref())
            .map(|apps| apps.contains(flatpak_id));
        let (user, system) = (user.unwrap_or(false), system.unwrap_or(false));
        if user {
            installed.push(InstalledInfo {
                via: InstalledVia::Flatpak.as_str().to_string(),
                scope: Some("user".into()),
            });
        }
        if system {
            installed.push(InstalledInfo {
                via: InstalledVia::Flatpak.as_str().to_string(),
                scope: Some("system".into()),
            });
        }
    }
    if let Some(snap_name) = snap_name(id) {
        let present = probe
            .and_then(|probe| probe.snap_apps.as_ref())
            .map(|apps| apps.contains(snap_name))
            .unwrap_or(false);
        if present {
            installed.push(InstalledInfo {
                via: InstalledVia::Snap.as_str().to_string(),
                scope: None,
            });
        }
    }
    installed
}

pub fn detect_installed(
    id: AppId,
    family: Option<DistroFamily>,
    runner: &dyn CommandRunner,
) -> Vec<InstalledInfo> {
    detect_installed_with(id, family, runner, None)
}

/// Picks the one format to use for "Apri" when an app is installed through
/// more than one: Flatpak first (sandboxed, self-contained), then Native,
/// then Snap. Arbitrary but fixed and documented -- removal always lets
/// the user pick the exact format explicitly instead (see `AppStatus`).
pub fn primary_installed(installed: &[InstalledInfo]) -> Option<&InstalledInfo> {
    let rank = |via: &str| match via {
        "flatpak" => 0,
        "native" => 1,
        "snap" => 2,
        _ => 3,
    };
    installed.iter().min_by_key(|info| rank(&info.via))
}

fn resolve_with(
    id: AppId,
    family: Option<DistroFamily>,
    runner: &dyn CommandRunner,
    probe: &CatalogProbe,
) -> AppStatus {
    let installed = detect_installed_with(id, family, runner, Some(probe));
    let installed_vias: BTreeSet<&str> = installed.iter().map(|info| info.via.as_str()).collect();
    let mut methods = Vec::new();
    if let Some(flatpak_id) = flatpak_id(id) {
        if !installed_vias.contains("flatpak") {
            let _ = flatpak_id;
            methods.push(MethodOffer {
                method: InstallMethod::Flatpak.as_str(),
                immediate: probe.flatpak_present,
                community: false,
            });
        }
    }
    if snap_name(id).is_some() && !installed_vias.contains("snap") {
        methods.push(MethodOffer {
            method: InstallMethod::Snap.as_str(),
            immediate: probe.snap_active,
            community: snap_is_community(id),
        });
    }
    if flatpak_id(id).is_none() && snap_name(id).is_none() {
        // KDE Connect: the only native app in this catalog.
        if installed.is_empty() {
            if let Some(family) = family {
                if let Some(package) = repo_package_for(id, family) {
                    let available = probe
                        .availability
                        .get(package)
                        .copied()
                        .or_else(|| {
                            probe
                                .manager
                                .as_ref()
                                .map(|pm| pm.availability(runner, package))
                        })
                        .is_some_and(|value| {
                            matches!(value, Availability::Installed | Availability::Available)
                        });
                    if available {
                        methods.push(MethodOffer {
                            method: InstallMethod::Native.as_str(),
                            immediate: true,
                            community: false,
                        });
                    }
                }
            }
        }
    }
    let vulkan_uncertain = id == AppId::Upscayl && installed.is_empty();
    AppStatus {
        id: id.as_str(),
        category: facts(id).category,
        is_cli: false,
        installed,
        methods,
        vulkan_uncertain,
        icon: None,
    }
}

#[cfg(test)]
pub fn resolve_all(family: Option<DistroFamily>, runner: &dyn CommandRunner) -> Vec<AppStatus> {
    let probe = CatalogProbe::build(family, runner);
    AppId::all()
        .into_iter()
        .map(|id| resolve_with(id, family, runner, &probe))
        .collect()
}

// ---------------------------------------------------------------------
// Small, discreet system-wide status ("Supporto applicazioni") plus the
// exact bootstrap path each install button should follow, all resolved
// here, server-side -- the frontend only ever displays these fixed
// strings, it never derives distro logic itself.
// ---------------------------------------------------------------------

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppsEnvironment {
    pub flatpak_installed: bool,
    pub flathub_configured: bool,
    pub snap_installed: bool,
    pub snap_active: bool,
    pub distro_family: Option<&'static str>,
    pub distro_id: String,
    /// "ready" | "needs_flathub" | "needs_install" | "unsupported"
    pub flatpak_plan: &'static str,
    /// "ready" | "needs_activation" | "needs_bootstrap" | "mint_nosnap" |
    /// "arch_blocked" | "opensuse_repo" | "unsupported"
    pub snap_plan: &'static str,
}

/// The Flatpak path decision, pure and directly testable: what the
/// "Installa Flatpak" button must do for this exact machine state.
pub fn flatpak_plan(
    flatpak_installed: bool,
    flathub_configured: bool,
    family_known: bool,
) -> &'static str {
    if flatpak_installed && flathub_configured {
        "ready"
    } else if flatpak_installed {
        "needs_flathub"
    } else if family_known {
        "needs_install"
    } else {
        "unsupported"
    }
}

/// The Snap path decision, pure and directly testable. `nosnap_present` is
/// injected so the Linux Mint case (which reads a real file in
/// [`mint_nosnap_present`]) is deterministic in tests instead of depending
/// on the machine running them.
pub fn snap_plan(
    release: &OsRelease,
    snap_installed: bool,
    snap_active: bool,
    nosnap_present: bool,
) -> &'static str {
    if snap_active {
        "ready"
    } else if snap_installed {
        "needs_activation"
    } else {
        match crate::distro::family(release) {
            Some(DistroFamily::Arch) => "arch_blocked",
            Some(DistroFamily::Ubuntu) if release.id == "linuxmint" && nosnap_present => {
                "mint_nosnap"
            }
            Some(DistroFamily::Ubuntu)
            | Some(DistroFamily::Debian)
            | Some(DistroFamily::Fedora) => "needs_bootstrap",
            Some(DistroFamily::Suse) if opensuse_snap_repo_url(release).is_some() => {
                "opensuse_repo"
            }
            _ => "unsupported",
        }
    }
}

pub fn environment(release: &OsRelease, runner: &dyn CommandRunner) -> AppsEnvironment {
    let family = crate::distro::family(release);
    let family_label = family.map(|family| match family {
        DistroFamily::Arch => "arch",
        DistroFamily::Fedora => "fedora",
        DistroFamily::Debian => "debian",
        DistroFamily::Ubuntu => "ubuntu",
        DistroFamily::Suse => "suse",
    });

    let flatpak_installed = flatpak_present(runner);
    let flathub_configured = flatpak_installed && flathub_configured(runner);
    let snap_installed = snap_present(runner);
    let snap_active = snap_installed && snapd_socket_active(runner);
    let flatpak_plan = flatpak_plan(flatpak_installed, flathub_configured, family.is_some());
    let snap_plan = snap_plan(release, snap_installed, snap_active, mint_nosnap_present());

    AppsEnvironment {
        flatpak_installed,
        flathub_configured,
        snap_installed,
        snap_active,
        distro_family: family_label,
        distro_id: release.id.clone(),
        flatpak_plan,
        snap_plan,
    }
}

/// The one read-only source of truth for the Programs app cards. A failed
/// optional source stays local to that source: absent Flatpak/Snap and absent
/// apps are normal empty sets, never a catalog-wide detection failure.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppsSnapshot {
    pub apps: Vec<AppStatus>,
    pub flatpak_available: bool,
    pub flathub_user: bool,
    pub flathub_system: bool,
    pub snap_available: bool,
    pub snap_ready: bool,
    pub flatpak_status: &'static str,
    pub snap_status: &'static str,
    #[serde(flatten)]
    pub environment: AppsEnvironment,
}

pub fn snapshot(release: &OsRelease, runner: &dyn CommandRunner) -> AppsSnapshot {
    let family = crate::distro::family(release);
    let probe = CatalogProbe::build(family, runner);
    let flatpak_status = if !probe.flatpak_present {
        "unavailable"
    } else if probe.flatpak_user_apps.is_none() || probe.flatpak_system_apps.is_none() {
        "error"
    } else {
        "ok"
    };
    let snap_status = if !probe.snap_present {
        "unavailable"
    } else if probe.snap_apps.is_none() {
        "error"
    } else {
        "ok"
    };
    let environment = AppsEnvironment {
        flatpak_installed: probe.flatpak_present,
        flathub_configured: probe.flathub_user || probe.flathub_system,
        snap_installed: probe.snap_present,
        snap_active: probe.snap_active,
        distro_family: family.map(|family| match family {
            DistroFamily::Arch => "arch",
            DistroFamily::Fedora => "fedora",
            DistroFamily::Debian => "debian",
            DistroFamily::Ubuntu => "ubuntu",
            DistroFamily::Suse => "suse",
        }),
        distro_id: release.id.clone(),
        flatpak_plan: flatpak_plan(
            probe.flatpak_present,
            probe.flathub_user || probe.flathub_system,
            family.is_some(),
        ),
        snap_plan: snap_plan(
            release,
            probe.snap_present,
            probe.snap_active,
            mint_nosnap_present(),
        ),
    };
    AppsSnapshot {
        apps: AppId::all()
            .into_iter()
            .map(|id| resolve_with(id, family, runner, &probe))
            .collect(),
        flatpak_available: probe.flatpak_present,
        flathub_user: probe.flathub_user,
        flathub_system: probe.flathub_system,
        snap_available: probe.snap_present,
        snap_ready: probe.snap_active,
        flatpak_status,
        snap_status,
        environment,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::packages::CommandOutput;
    use std::collections::HashMap;

    #[derive(Default)]
    struct MockRunner {
        outputs: HashMap<String, CommandOutput>,
    }

    impl MockRunner {
        fn with(mut self, program: &str, args: &[&str], success: bool, stdout: &str) -> Self {
            self.outputs.insert(
                format!("{program}\0{}", args.join("\0")),
                CommandOutput {
                    success,
                    exit_code: if success { 0 } else { 1 },
                    stdout: stdout.to_string(),
                    stderr: String::new(),
                },
            );
            self
        }
    }

    impl CommandRunner for MockRunner {
        fn run(&self, program: &str, args: &[&str]) -> Option<CommandOutput> {
            self.outputs
                .get(&format!("{program}\0{}", args.join("\0")))
                .cloned()
        }
    }

    fn os(id: &str) -> OsRelease {
        OsRelease {
            id: id.to_string(),
            ..OsRelease::default()
        }
    }

    #[test]
    fn catalog_exposes_exactly_the_five_opaque_ids() {
        assert_eq!(
            AppId::all().map(AppId::as_str),
            ["gradia", "upscayl", "curtail", "ferdium", "kde-connect"]
        );
        for id in AppId::all() {
            assert_eq!(AppId::parse(id.as_str()), Some(id));
        }
        for invalid in ["docker", "signal", "Gradia", "../../gradia", "gradia;sh"] {
            assert_eq!(AppId::parse(invalid), None);
        }
    }

    #[test]
    fn flatpak_and_snap_ids_are_exact_for_all_four_apps() {
        assert_eq!(flatpak_id(AppId::Gradia), Some("be.alexandervanhee.gradia"));
        assert_eq!(snap_name(AppId::Gradia), Some("gradia"));
        assert_eq!(flatpak_id(AppId::Upscayl), Some("org.upscayl.Upscayl"));
        assert_eq!(snap_name(AppId::Upscayl), Some("upscayl"));
        assert_eq!(
            flatpak_id(AppId::Curtail),
            Some("com.github.huluti.Curtail")
        );
        assert_eq!(snap_name(AppId::Curtail), Some("curtail"));
        assert!(snap_is_community(AppId::Curtail));
        assert_eq!(flatpak_id(AppId::Ferdium), Some("org.ferdium.Ferdium"));
        assert_eq!(snap_name(AppId::Ferdium), Some("ferdium"));
        for id in [AppId::Gradia, AppId::Upscayl, AppId::Ferdium] {
            assert!(!snap_is_community(id));
        }
        assert_eq!(flatpak_id(AppId::KdeConnect), None);
        assert_eq!(snap_name(AppId::KdeConnect), None);
    }

    #[test]
    fn snapshot_keeps_sources_independent_and_absent_apps_normal() {
        let runner = MockRunner::default()
            .with("flatpak", &["--version"], true, "Flatpak 1.18")
            .with(
                "flatpak",
                &["--user", "remotes", "--columns=name"],
                true,
                "flathub\n",
            )
            .with(
                "flatpak",
                &["--system", "remotes", "--columns=name"],
                true,
                "",
            )
            .with(
                "flatpak",
                &["--user", "list", "--app", "--columns=application"],
                true,
                "be.alexandervanhee.gradia\n",
            )
            .with(
                "flatpak",
                &["--system", "list", "--app", "--columns=application"],
                true,
                "org.ferdium.Ferdium\n",
            )
            .with("snap", &["version"], true, "snap 2.0")
            .with(
                "systemctl",
                &["is-active", "--quiet", "snapd.socket"],
                true,
                "",
            )
            .with("snap", &["list"], true, "Name Version\nupscayl 2.0\n")
            .with("apt-get", &["--version"], true, "apt")
            .with(
                "dpkg-query",
                &["-W", "-f=${Package} ${Status}\n", "kdeconnect"],
                true,
                "kdeconnect install ok installed\n",
            )
            .with(
                "apt-cache",
                &["policy", "kdeconnect"],
                true,
                "kdeconnect:\n  Candidate: 1\n",
            );
        let snapshot = snapshot(&os("ubuntu"), &runner);
        let installed = |id: &str| {
            snapshot
                .apps
                .iter()
                .find(|app| app.id == id)
                .unwrap()
                .installed
                .clone()
        };
        assert_eq!(
            installed("gradia"),
            vec![InstalledInfo {
                via: "flatpak".into(),
                scope: Some("user".into())
            }]
        );
        assert_eq!(
            installed("ferdium"),
            vec![InstalledInfo {
                via: "flatpak".into(),
                scope: Some("system".into())
            }]
        );
        assert_eq!(
            installed("upscayl"),
            vec![InstalledInfo {
                via: "snap".into(),
                scope: None
            }]
        );
        assert!(installed("curtail").is_empty());
        assert_eq!(
            installed("kde-connect"),
            vec![InstalledInfo {
                via: "native".into(),
                scope: None
            }]
        );
    }

    #[test]
    fn snapshot_keeps_flatpak_results_when_snap_list_fails() {
        let runner = MockRunner::default()
            .with("flatpak", &["--version"], true, "Flatpak 1.18")
            .with(
                "flatpak",
                &["--user", "remotes", "--columns=name"],
                true,
                "flathub\n",
            )
            .with(
                "flatpak",
                &["--system", "remotes", "--columns=name"],
                true,
                "",
            )
            .with(
                "flatpak",
                &["--user", "list", "--app", "--columns=application"],
                true,
                "be.alexandervanhee.gradia\n",
            )
            .with(
                "flatpak",
                &["--system", "list", "--app", "--columns=application"],
                true,
                "",
            )
            .with("snap", &["version"], true, "snap 2.0")
            .with(
                "systemctl",
                &["is-active", "--quiet", "snapd.socket"],
                true,
                "",
            )
            .with("snap", &["list"], false, "")
            .with("apt-get", &["--version"], true, "apt");
        let snapshot = snapshot(&os("ubuntu"), &runner);
        assert_eq!(snapshot.snap_status, "error");
        assert_eq!(snapshot.flatpak_status, "ok");
        assert_eq!(
            snapshot
                .apps
                .iter()
                .find(|app| app.id == "gradia")
                .unwrap()
                .installed,
            vec![InstalledInfo {
                via: "flatpak".into(),
                scope: Some("user".into())
            }]
        );
    }

    #[test]
    fn kde_connect_package_mapping_is_exact_and_stays_native_only() {
        for (family, package) in [
            (DistroFamily::Ubuntu, "kdeconnect"),
            (DistroFamily::Debian, "kdeconnect"),
            (DistroFamily::Arch, "kdeconnect"),
            (DistroFamily::Fedora, "kde-connect"),
            (DistroFamily::Suse, "kdeconnect-kde"),
        ] {
            assert_eq!(repo_package_for(AppId::KdeConnect, family), Some(package));
        }
        assert!(install_method_allowed(
            AppId::KdeConnect,
            InstallMethod::Native
        ));
        assert!(!install_method_allowed(
            AppId::KdeConnect,
            InstallMethod::Flatpak
        ));
        assert!(!install_method_allowed(
            AppId::KdeConnect,
            InstallMethod::Snap
        ));
        assert!(!removal_via_allowed(AppId::KdeConnect, InstalledVia::Snap));
    }

    #[test]
    fn the_four_flatpak_snap_apps_never_offer_native() {
        for id in [
            AppId::Gradia,
            AppId::Upscayl,
            AppId::Curtail,
            AppId::Ferdium,
        ] {
            assert!(install_method_allowed(id, InstallMethod::Flatpak));
            assert!(install_method_allowed(id, InstallMethod::Snap));
            assert!(!install_method_allowed(id, InstallMethod::Native));
            assert!(removal_via_allowed(id, InstalledVia::Flatpak));
            assert!(removal_via_allowed(id, InstalledVia::Snap));
            assert!(!removal_via_allowed(id, InstalledVia::Native));
            assert_eq!(repo_package_for(id, DistroFamily::Ubuntu), None);
        }
    }

    #[test]
    fn method_and_via_parsers_reject_unknown_or_injected_values() {
        assert_eq!(InstallMethod::parse("appimage"), None);
        assert_eq!(InstallMethod::parse("native;sh"), None);
        assert_eq!(InstallMethod::parse("snap"), Some(InstallMethod::Snap));
        assert_eq!(InstalledVia::parse("snap"), Some(InstalledVia::Snap));
        assert_eq!(InstalledVia::parse("appimage"), None);
    }

    #[test]
    fn snap_is_ready_only_when_the_binary_works_and_the_socket_is_active() {
        let ready = MockRunner::default()
            .with("snap", &["version"], true, "snap 2.60")
            .with(
                "systemctl",
                &["is-active", "--quiet", "snapd.socket"],
                true,
                "",
            );
        assert!(snap_present(&ready));
        assert!(snapd_socket_active(&ready));
        assert!(snap_ready(&ready));

        let installed_inactive = MockRunner::default()
            .with("snap", &["version"], true, "snap 2.60")
            .with(
                "systemctl",
                &["is-active", "--quiet", "snapd.socket"],
                false,
                "",
            );
        assert!(snap_present(&installed_inactive));
        assert!(!snap_ready(&installed_inactive));

        let absent = MockRunner::default();
        assert!(!snap_present(&absent));
        assert!(!snap_ready(&absent));
    }

    #[test]
    fn resolve_all_returns_five_entries_and_offers_both_formats_when_absent() {
        let runner = MockRunner::default()
            .with("flatpak", &["--version"], true, "Flatpak 1.14")
            .with(
                "flatpak",
                &["list", "--app", "--columns=application"],
                true,
                "",
            )
            .with("snap", &["version"], true, "snap 2.60")
            .with(
                "systemctl",
                &["is-active", "--quiet", "snapd.socket"],
                true,
                "",
            )
            .with("snap", &["list"], true, "Name  Version\n");
        let statuses = resolve_all(None, &runner);
        assert_eq!(statuses.len(), 5);
        let gradia = &statuses[0];
        assert!(gradia.installed.is_empty());
        let methods: Vec<&str> = gradia.methods.iter().map(|m| m.method).collect();
        assert_eq!(methods, vec!["flatpak", "snap"]);
        assert!(gradia.methods[0].immediate);
        assert!(gradia.methods[1].immediate);
    }

    #[test]
    fn curtail_snap_offer_is_flagged_community_and_others_are_not() {
        let runner = MockRunner::default()
            .with("flatpak", &["--version"], false, "")
            .with("snap", &["version"], false, "");
        let statuses = resolve_all(None, &runner);
        let curtail = statuses.iter().find(|s| s.id == "curtail").unwrap();
        let snap_offer = curtail.methods.iter().find(|m| m.method == "snap").unwrap();
        assert!(snap_offer.community);
        let gradia = statuses.iter().find(|s| s.id == "gradia").unwrap();
        let gradia_snap = gradia.methods.iter().find(|m| m.method == "snap").unwrap();
        assert!(!gradia_snap.community);
    }

    #[test]
    fn an_app_installed_via_both_formats_is_reported_as_both_never_just_one() {
        let runner = MockRunner::default()
            .with("flatpak", &["--version"], true, "Flatpak 1.14")
            .with(
                "flatpak",
                &["--user", "list", "--app", "--columns=application"],
                true,
                "org.upscayl.Upscayl\n",
            )
            .with(
                "flatpak",
                &["--system", "list", "--app", "--columns=application"],
                true,
                "",
            )
            .with("snap", &["version"], true, "snap 2.60")
            .with(
                "systemctl",
                &["is-active", "--quiet", "snapd.socket"],
                true,
                "",
            )
            .with("snap", &["list"], true, "Name     Version\nupscayl  2.0\n");
        let statuses = resolve_all(None, &runner);
        let upscayl = statuses.iter().find(|s| s.id == "upscayl").unwrap();
        let vias: Vec<&str> = upscayl.installed.iter().map(|i| i.via.as_str()).collect();
        assert_eq!(vias, vec!["flatpak", "snap"]);
        assert!(upscayl.methods.is_empty(), "nothing left to install");
    }

    #[test]
    fn primary_installed_prefers_flatpak_then_native_then_snap() {
        let both = vec![
            InstalledInfo {
                via: "snap".into(),
                scope: None,
            },
            InstalledInfo {
                via: "flatpak".into(),
                scope: Some("user".into()),
            },
        ];
        assert_eq!(primary_installed(&both).unwrap().via, "flatpak");
        let native_and_snap = vec![
            InstalledInfo {
                via: "snap".into(),
                scope: None,
            },
            InstalledInfo {
                via: "native".into(),
                scope: None,
            },
        ];
        assert_eq!(primary_installed(&native_and_snap).unwrap().via, "native");
        assert!(primary_installed(&[]).is_none());
    }

    #[test]
    fn opensuse_repo_url_is_exact_for_tumbleweed_and_leap_never_a_hardcoded_version() {
        assert_eq!(
            opensuse_snap_repo_url(&os("opensuse-tumbleweed")),
            Some(
                "https://download.opensuse.org/repositories/system:/snappy/openSUSE_Tumbleweed/system:snappy.repo"
                    .to_string()
            )
        );
        let mut leap = os("opensuse-leap");
        leap.version_id = Some("15.6".into());
        assert_eq!(
            opensuse_snap_repo_url(&leap),
            Some(
                "https://download.opensuse.org/repositories/system:/snappy/openSUSE_Leap_15.6/system:snappy.repo"
                    .to_string()
            )
        );
        assert_eq!(opensuse_snap_repo_url(&os("opensuse-leap")), None);
        assert_eq!(opensuse_snap_repo_url(&os("fedora")), None);
    }

    #[test]
    fn environment_plan_covers_every_supported_family_and_the_mint_and_arch_special_cases() {
        let no_tools = MockRunner::default()
            .with("flatpak", &["--version"], false, "")
            .with("snap", &["version"], false, "");

        assert_eq!(
            environment(&os("arch"), &no_tools).snap_plan,
            "arch_blocked"
        );
        assert_eq!(
            environment(&os("fedora"), &no_tools).snap_plan,
            "needs_bootstrap"
        );
        assert_eq!(
            environment(&os("debian"), &no_tools).snap_plan,
            "needs_bootstrap"
        );
        assert_eq!(
            environment(&os("ubuntu"), &no_tools).snap_plan,
            "needs_bootstrap"
        );
        assert_eq!(
            environment(&os("opensuse-tumbleweed"), &no_tools).snap_plan,
            "opensuse_repo"
        );
        let leap_unknown = os("opensuse-leap");
        assert_eq!(
            environment(&leap_unknown, &no_tools).snap_plan,
            "unsupported"
        );
        assert_eq!(
            environment(&os("some-niche-distro"), &no_tools).snap_plan,
            "unsupported"
        );

        let active = MockRunner::default()
            .with("flatpak", &["--version"], true, "Flatpak 1.14")
            .with(
                "flatpak",
                &["--user", "remotes", "--columns=name"],
                true,
                "flathub\n",
            )
            .with("snap", &["version"], true, "snap 2.60")
            .with(
                "systemctl",
                &["is-active", "--quiet", "snapd.socket"],
                true,
                "",
            );
        let env = environment(&os("ubuntu"), &active);
        assert_eq!(env.snap_plan, "ready");
        assert_eq!(env.flatpak_plan, "ready");

        let inactive = MockRunner::default()
            .with("flatpak", &["--version"], false, "")
            .with("snap", &["version"], true, "snap 2.60")
            .with(
                "systemctl",
                &["is-active", "--quiet", "snapd.socket"],
                false,
                "",
            );
        assert_eq!(
            environment(&os("arch"), &inactive).snap_plan,
            "needs_activation"
        );
    }

    #[test]
    fn mint_snap_plan_depends_on_the_nosnap_pin_and_is_deterministic() {
        let mint = os("linuxmint");
        // Mint spells the special case: with the pin still in place the
        // plan must ask for the specific, reversible Mint confirmation...
        assert_eq!(snap_plan(&mint, false, false, true), "mint_nosnap");
        // ...and without it, Mint behaves like its Ubuntu family.
        assert_eq!(snap_plan(&mint, false, false, false), "needs_bootstrap");
        // An already-working snapd on Mint never needs any of that.
        assert_eq!(snap_plan(&mint, true, true, true), "ready");
        assert_eq!(snap_plan(&mint, true, false, true), "needs_activation");
        // The pin is a Mint-only concept: the same flag on plain Ubuntu
        // must never produce the Mint confirmation.
        assert_eq!(
            snap_plan(&os("ubuntu"), false, false, true),
            "needs_bootstrap"
        );
    }

    #[test]
    fn snap_plan_is_exact_for_every_supported_family_and_blocked_on_arch_without_snapd() {
        assert_eq!(
            snap_plan(&os("ubuntu"), false, false, false),
            "needs_bootstrap"
        );
        assert_eq!(
            snap_plan(&os("debian"), false, false, false),
            "needs_bootstrap"
        );
        assert_eq!(
            snap_plan(&os("fedora"), false, false, false),
            "needs_bootstrap"
        );
        assert_eq!(
            snap_plan(&os("opensuse-tumbleweed"), false, false, false),
            "opensuse_repo"
        );
        let mut leap = os("opensuse-leap");
        leap.version_id = Some("15.6".into());
        assert_eq!(snap_plan(&leap, false, false, false), "opensuse_repo");
        // openSUSE Leap without a version is never guessed: unsupported.
        assert_eq!(
            snap_plan(&os("opensuse-leap"), false, false, false),
            "unsupported"
        );
        assert_eq!(
            snap_plan(&os("some-unknown-distro"), false, false, false),
            "unsupported"
        );
        // Arch without snapd must never offer an install path at all
        // (snapd comes from AUR there), but an already-installed one is
        // supported exactly like anywhere else.
        assert_eq!(snap_plan(&os("arch"), false, false, false), "arch_blocked");
        assert_eq!(
            snap_plan(&os("arch"), true, false, false),
            "needs_activation"
        );
        assert_eq!(snap_plan(&os("arch"), true, true, false), "ready");
    }

    #[test]
    fn flatpak_plan_is_exact_in_all_four_states() {
        assert_eq!(flatpak_plan(true, true, true), "ready");
        assert_eq!(flatpak_plan(true, false, true), "needs_flathub");
        assert_eq!(flatpak_plan(false, false, true), "needs_install");
        assert_eq!(flatpak_plan(false, false, false), "unsupported");
        // Flathub is only ever meaningful once Flatpak itself is present.
        assert_eq!(flatpak_plan(false, true, true), "needs_install");
    }

    #[test]
    fn flathub_missing_is_distinguished_from_flatpak_missing_entirely() {
        let flatpak_only_no_flathub = MockRunner::default()
            .with("flatpak", &["--version"], true, "Flatpak 1.14")
            .with(
                "flatpak",
                &["--user", "remotes", "--columns=name"],
                true,
                "",
            )
            .with(
                "flatpak",
                &["--system", "remotes", "--columns=name"],
                true,
                "",
            );
        let env = environment(&os("fedora"), &flatpak_only_no_flathub);
        assert_eq!(env.flatpak_plan, "needs_flathub");

        let nothing = MockRunner::default().with("flatpak", &["--version"], false, "");
        assert_eq!(
            environment(&os("fedora"), &nothing).flatpak_plan,
            "needs_install"
        );
    }
}
