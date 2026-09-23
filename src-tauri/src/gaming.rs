//! Gaming module for the Programs page.
//!
//! This module owns the whole feature in one place so it stays small and
//! readable: the closed component catalog, the per-family package mapping,
//! the plan computation (what is already there, what would be installed,
//! what is blocked and why) and the privileged execution used by the
//! programs helper. It is compiled into both the application (plan display)
//! and the helper (execution), exactly like `apt_ops.rs`.
//!
//! Deliberately out of scope: kernels, schedulers, sysctl/gamescope/mangohud
//! style tweaks, and every automatic GPU driver install. The only NVIDIA
//! action this module can ever plan is the 32-bit userspace counterpart of
//! the driver stack that is *already installed* ("match, never replace"):
//! when the origin cannot be identified with certainty, nothing is proposed.
//!
//! Package names verified against the distributions' real package metadata
//! (see the report). Families whose unknown variants cannot be mapped
//! safely are reported as unavailable per component instead of guessed.
use crate::{
    distro::{self, DistroFamily, OsRelease},
    gpus::{self, GpuInfo},
    packages::{Availability, CommandRunner, PackageManager},
};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, fs, path::Path};

pub const PROGRAMS_HELPER_PATH: &str = "/usr/lib/mg-linux-toolbox/mg-linux-toolbox-programs-helper";
pub const PACMAN_CONF: &str = "/etc/pacman.conf";
pub const RPMFUSION_NONFREE_URL: &str =
    "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-{version}.noarch.rpm";
pub const FLATHUB_REMOTE: &str = "flathub";
pub const FLATHUB_URL: &str = "https://dl.flathub.org/repo/flathub.flatpakrepo";
pub const GEFORCE_NOW_REMOTE: &str = "GeForceNOW";
pub const GEFORCE_NOW_URL: &str =
    "https://international.download.nvidia.com/GFNLinux/flatpak/geforcenow.flatpakrepo";
pub const GEFORCE_NOW_APP: &str = "com.nvidia.geforcenow";
pub const HEROIC_APP: &str = "com.heroicgameslauncher.hgl";
pub const PROTONTRICKS_APP: &str = "com.github.Matoking.protontricks";

/// The two closed operations the privileged helper accepts.
pub fn is_operation(name: &str) -> bool {
    matches!(
        name,
        "programs-prepare-gaming" | "programs-install-geforce-now"
    )
}

#[derive(Clone)]
enum Step {
    AddI386,
    EnableMultilib,
    InstallRpmFusion {
        version: String,
    },
    FlatpakRemote {
        name: &'static str,
        url: &'static str,
    },
    Refresh,
    InstallNative {
        packages: Vec<String>,
        components: Vec<&'static str>,
    },
    InstallFlatpak {
        remote: &'static str,
        app: &'static str,
        component: &'static str,
    },
}

#[derive(Clone)]
struct ResolvedComponent {
    id: &'static str,
    name: &'static str,
    kind: &'static str,
    state: &'static str,
    detail: Vec<String>,
    note: Option<&'static str>,
}

#[derive(Default, Clone)]
struct Resolved {
    components: Vec<ResolvedComponent>,
    steps: Vec<Step>,
    warnings: Vec<&'static str>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GamingPlan {
    pub distribution: distro::Distribution,
    pub gpus: Vec<GpuInfo>,
    pub package_manager: Option<&'static str>,
    pub supported: bool,
    pub unsupported_reason: Option<&'static str>,
    pub components: Vec<PlannedComponent>,
    pub changes: Vec<PlannedChange>,
    pub warnings: Vec<&'static str>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlannedComponent {
    pub id: &'static str,
    pub name: &'static str,
    pub kind: &'static str,
    pub state: &'static str,
    pub detail: Vec<String>,
    pub note: Option<&'static str>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlannedChange {
    pub kind: &'static str,
    pub label: &'static str,
    pub detail: Vec<String>,
}

fn component(
    id: &'static str,
    name: &'static str,
    kind: &'static str,
    detail: &[&str],
) -> ResolvedComponent {
    ResolvedComponent {
        id,
        name,
        kind,
        state: "toInstall",
        detail: detail.iter().map(|v| (*v).to_string()).collect(),
        note: None,
    }
}

/// Resolution context shared by every component lookup. `thirty_two_bit`
/// records whether the 32-bit repositories are already active on this
/// machine (`Some(false)` = they would have to be enabled first): on a fresh
/// install the `:i386` / `lib32-` packages cannot be seen by the manager
/// yet, so their availability is evaluated in the context of the enabling
/// step the same plan proposes -- never silently reported as missing.
///
/// `rpmfusion_pending` and `arch_steam_needs_multilib` extend the exact same
/// pattern to the two other "a repository must be enabled before this one
/// named package resolves" cases this module knows about: Steam on standard
/// Fedora needs RPM Fusion nonfree, and Steam on Arch lives only in the
/// `[multilib]` repository. Both are decided once, before any component is
/// resolved, so the enabling step and the package end up in the very same
/// plan instead of Steam being silently dropped as "unavailable" forever.
struct Resolver<'a> {
    pm: &'a PackageManager,
    runner: &'a dyn CommandRunner,
    thirty_two_bit: Option<bool>,
    rpmfusion_pending: bool,
    arch_steam_needs_multilib: bool,
}

impl Resolver<'_> {
    fn availability(&self, package: &str) -> Availability {
        let availability = self.pm.availability(self.runner, package);
        if availability != Availability::Missing {
            return availability;
        }
        let needs_enabling = package.ends_with(":i386") || package.starts_with("lib32-");
        if needs_enabling && self.thirty_two_bit == Some(false) {
            return Availability::Available;
        }
        if package == "steam" && (self.rpmfusion_pending || self.arch_steam_needs_multilib) {
            return Availability::Available;
        }
        availability
    }
}

/// One native package's state given the real system: a package already
/// installed never appears in the install list (idempotence), a package the
/// manager can resolve is offered, and one that cannot be resolved is
/// reported instead of silently failing later.
fn resolve_native(resolver: &Resolver, package: &str) -> Availability {
    resolver.availability(package)
}

fn native_step(packages: Vec<String>, components: Vec<&'static str>) -> Step {
    Step::InstallNative {
        packages,
        components,
    }
}

fn native_components(
    resolver: &Resolver,
    resolved: &mut Resolved,
    warnings: &mut Vec<&'static str>,
    wanted: &[(&'static str, &'static str, &[&str])],
) {
    let mut to_install: Vec<String> = Vec::new();
    let mut install_components: Vec<&'static str> = Vec::new();
    for &(id, name, packages) in wanted {
        let mut detail = Vec::new();
        let mut component_to_install = Vec::new();
        let mut missing: Vec<&str> = Vec::new();
        let mut unknown: Vec<&str> = Vec::new();
        let mut installed_all = true;
        for package in packages {
            match resolve_native(resolver, package) {
                Availability::Installed => detail.push((*package).to_string()),
                Availability::Available => {
                    installed_all = false;
                    detail.push((*package).to_string());
                    component_to_install.push((*package).to_string());
                }
                Availability::Missing => {
                    installed_all = false;
                    detail.push((*package).to_string());
                    missing.push(package);
                }
                Availability::Unknown => {
                    installed_all = false;
                    detail.push((*package).to_string());
                    unknown.push(package);
                }
            }
        }
        let (state, note) = if installed_all {
            ("installed", None)
        } else if !missing.is_empty() {
            warnings.push("componentUnavailable");
            ("unavailable", Some("componentUnavailable"))
        } else if !unknown.is_empty() {
            warnings.push("componentUnknown");
            ("unknown", Some("componentUnknown"))
        } else {
            ("toInstall", None)
        };
        if state == "toInstall" {
            to_install.extend(component_to_install);
            install_components.push(id);
        }
        resolved.components.push(ResolvedComponent {
            id,
            name,
            kind: "app",
            state,
            detail,
            note,
        });
    }
    if !to_install.is_empty() {
        to_install.sort();
        to_install.dedup();
        resolved
            .steps
            .push(native_step(to_install, install_components));
    }
}

fn flatpak_available(runner: &dyn CommandRunner) -> bool {
    runner
        .run("flatpak", &["--version"])
        .is_some_and(|output| output.success)
}

fn flatpak_remote_present(runner: &dyn CommandRunner, name: &str) -> bool {
    runner
        // Gaming installs its applications system-wide from the privileged
        // helper, so an unrelated per-user remote must not satisfy this check.
        .run("flatpak", &["remotes", "--system", "--columns=name"])
        .is_some_and(|output| {
            output.success && output.stdout.lines().any(|line| line.trim() == name)
        })
}

#[derive(Clone, Copy)]
struct FlatpakTarget {
    id: &'static str,
    name: &'static str,
    kind: &'static str,
    remote: &'static str,
    remote_url: &'static str,
    app: &'static str,
}

const HEROIC_TARGET: FlatpakTarget = FlatpakTarget {
    id: "heroic",
    name: "Heroic Games Launcher",
    kind: "app",
    remote: FLATHUB_REMOTE,
    remote_url: FLATHUB_URL,
    app: HEROIC_APP,
};
const PROTONTRICKS_TARGET: FlatpakTarget = FlatpakTarget {
    id: "protontricks",
    name: "Protontricks",
    kind: "app",
    remote: FLATHUB_REMOTE,
    remote_url: FLATHUB_URL,
    app: PROTONTRICKS_APP,
};
const GEFORCE_NOW_TARGET: FlatpakTarget = FlatpakTarget {
    id: "geforce_now",
    name: "GeForce NOW",
    kind: "cloud",
    remote: GEFORCE_NOW_REMOTE,
    remote_url: GEFORCE_NOW_URL,
    app: GEFORCE_NOW_APP,
};

fn push_flatpak_component(
    runner: &dyn CommandRunner,
    resolved: &mut Resolved,
    target: FlatpakTarget,
) {
    let FlatpakTarget {
        id,
        name,
        kind,
        remote,
        remote_url,
        app,
    } = target;
    let installed = runner
        .run("flatpak", &["info", app])
        .is_some_and(|output| output.success);
    let state = if installed { "installed" } else { "toInstall" };
    resolved.components.push(ResolvedComponent {
        id,
        name,
        kind,
        state,
        detail: vec![format!("flatpak: {app}")],
        note: Some("flatpakNeeded"),
    });
    if installed {
        return;
    }
    if !flatpak_available(runner)
        && !resolved
            .components
            .iter()
            .any(|component| component.id == "flatpak")
    {
        resolved
            .steps
            .push(native_step(vec!["flatpak".to_string()], vec!["flatpak"]));
        resolved.components.push(ResolvedComponent {
            id: "flatpak",
            name: "Flatpak",
            kind: "runtime",
            state: "toInstall",
            detail: vec!["flatpak".to_string()],
            note: None,
        });
    }
    let remote_already_planned = resolved
        .steps
        .iter()
        .any(|step| matches!(step, Step::FlatpakRemote { name, .. } if *name == remote));
    if !flatpak_remote_present(runner, remote) && !remote_already_planned {
        resolved.steps.push(Step::FlatpakRemote {
            name: remote,
            url: remote_url,
        });
    }
    resolved.steps.push(Step::InstallFlatpak {
        remote,
        app,
        component: id,
    });
}

fn nvidia_32bit_packages(
    family: DistroFamily,
    resolver: &Resolver,
    warnings: &mut Vec<&'static str>,
) -> Vec<String> {
    let pm = resolver.pm;
    let runner = resolver.runner;
    match family {
        DistroFamily::Arch => {
            if pm.is_installed(runner, "nvidia-utils") {
                vec!["lib32-nvidia-utils".to_string()]
            } else {
                warnings.push("nvidiaStackUnknown");
                Vec::new()
            }
        }
        DistroFamily::Fedora => {
            let fedora_stack = ["xorg-x11-drv-nvidia", "akmod-nvidia"]
                .iter()
                .any(|package| pm.is_installed(runner, package));
            if fedora_stack {
                vec!["xorg-x11-drv-nvidia-libs.i686".to_string()]
            } else {
                warnings.push("nvidiaStackUnknown");
                Vec::new()
            }
        }
        DistroFamily::Ubuntu => {
            // Match the installed branch by name, never assume one.
            let installed = pm.installed_matching(runner, "libnvidia-gl-*:amd64");
            let branches: BTreeSet<String> = installed
                .iter()
                .filter_map(|name| {
                    name.trim_end_matches(":amd64")
                        .rsplit('-')
                        .next()
                        .filter(|branch| branch.chars().all(|c| c.is_ascii_digit()))
                        .map(str::to_string)
                })
                .collect();
            if branches.is_empty() {
                if pm.is_installed(runner, "nvidia-driver-libs") {
                    // A Debian-style stack on Ubuntu: the Debian mapping is
                    // the correct counterpart, not an Ubuntu branch guess.
                    return vec!["nvidia-driver-libs:i386".to_string()];
                }
                warnings.push("nvidiaStackUnknown");
                return Vec::new();
            }
            branches
                .into_iter()
                .map(|branch| format!("libnvidia-gl-{branch}:i386"))
                .collect()
        }
        DistroFamily::Debian => {
            if pm.is_installed(runner, "nvidia-driver-libs")
                || pm.is_installed(runner, "nvidia-driver")
            {
                vec!["nvidia-driver-libs:i386".to_string()]
            } else {
                warnings.push("nvidiaStackUnknown");
                Vec::new()
            }
        }
        DistroFamily::Suse => {
            // Only the exact `-32bit` counterpart of an installed generation
            // (`*-G0x`) is ever proposed, and only when it really exists in
            // the repositories.
            let installed = pm.installed_matching(runner, "nvidia-*-G0*");
            let candidates: Vec<String> = installed
                .iter()
                .filter(|name| !name.ends_with("-32bit") && !name.ends_with("-kmp-default"))
                .map(|name| format!("{name}-32bit"))
                .collect();
            let available: Vec<String> = candidates
                .into_iter()
                .filter(|candidate| resolver.availability(candidate) == Availability::Available)
                .collect();
            if available.is_empty() {
                warnings.push("nvidiaStackUnknown");
            }
            available
        }
    }
}

fn graphics_components(
    family: DistroFamily,
    resolver: &Resolver,
    gpus: &[GpuInfo],
    resolved: &mut Resolved,
) {
    let mut warnings = Vec::new();
    let vendors = gpus::vendors(gpus);
    if vendors.is_empty() {
        warnings.push("noGpuDetected");
    }
    let (vk64, vk32, gl32): (Vec<&str>, Vec<&str>, Vec<&str>) = match family {
        DistroFamily::Arch => {
            let mut vk64 = Vec::new();
            let mut vk32 = Vec::new();
            if vendors.contains(&"amd") {
                vk64.push("vulkan-radeon");
                vk32.push("lib32-vulkan-radeon");
            }
            if vendors.contains(&"intel") {
                vk64.push("vulkan-intel");
                vk32.push("lib32-vulkan-intel");
            }
            (vk64, vk32, vec!["lib32-mesa"])
        }
        DistroFamily::Fedora => (
            vec!["mesa-vulkan-drivers.x86_64"],
            vec!["mesa-vulkan-drivers.i686"],
            vec!["mesa-dri-drivers.i686"],
        ),
        DistroFamily::Ubuntu | DistroFamily::Debian => (
            vec!["mesa-vulkan-drivers:amd64"],
            vec!["mesa-vulkan-drivers:i386"],
            vec!["libgl1-mesa-dri:i386"],
        ),
        DistroFamily::Suse => {
            let mut vk64 = Vec::new();
            let mut vk32 = Vec::new();
            if vendors.contains(&"amd") {
                vk64.push("libvulkan_radeon");
                vk32.push("libvulkan_radeon-32bit");
            }
            if vendors.contains(&"intel") {
                vk64.push("libvulkan_intel");
                vk32.push("libvulkan_intel-32bit");
            }
            (vk64, vk32, vec!["Mesa-dri-32bit"])
        }
    };
    if !vk64.is_empty() {
        native_components(
            resolver,
            resolved,
            &mut warnings,
            &[("vulkan64", "Vulkan 64-bit", &vk64[..])],
        );
    }
    if !vk32.is_empty() {
        native_components(
            resolver,
            resolved,
            &mut warnings,
            &[("vulkan32", "Vulkan 32-bit", &vk32[..])],
        );
    }
    if !gl32.is_empty() && vendors.iter().any(|v| *v != "nvidia") {
        native_components(
            resolver,
            resolved,
            &mut warnings,
            &[("opengl32", "OpenGL 32-bit", &gl32[..])],
        );
    }
    if vendors.contains(&"nvidia") {
        let packages = nvidia_32bit_packages(family, resolver, &mut warnings);
        if packages.is_empty() {
            resolved.components.push(ResolvedComponent {
                id: "nvidia32",
                name: "NVIDIA 32-bit",
                kind: "graphics",
                state: "unavailable",
                detail: Vec::new(),
                note: Some("nvidiaMatchOnly"),
            });
        } else {
            let list: Vec<&str> = packages.iter().map(String::as_str).collect();
            native_components(
                resolver,
                resolved,
                &mut warnings,
                &[("nvidia32", "NVIDIA 32-bit", &list[..])],
            );
        }
    }
    resolved.warnings.extend(warnings);
}

fn resolve_core(
    release: &OsRelease,
    gpus: &[GpuInfo],
    runner: &dyn CommandRunner,
    immutable: bool,
) -> Result<(DistroFamily, PackageManager, Resolved, Vec<&'static str>), &'static str> {
    if immutable {
        return Err("unsupportedImmutable");
    }
    let family = distro::family(release).ok_or("unsupportedDistro")?;
    let pm = PackageManager::detect(family, runner).ok_or("packageManagerMissing")?;
    // Whether the 32-bit repositories are already active on this machine.
    let thirty_two_bit = match family {
        DistroFamily::Ubuntu | DistroFamily::Debian => Some(
            runner
                .run("dpkg", &["--print-foreign-architectures"])
                .is_some_and(|output| output.stdout.lines().any(|line| line.trim() == "i386")),
        ),
        DistroFamily::Arch => Some(arch_multilib_active(runner)),
        DistroFamily::Fedora | DistroFamily::Suse => None,
    };
    let mut warnings: Vec<&'static str> = Vec::new();

    // Whether Steam needs a repository enabled before it resolves, decided
    // *before* any component is looked up -- the same "enabling step
    // precedes resolution" pattern as the i386/lib32- handling above.
    // Without this, Steam is looked up while the repository that would
    // provide it does not exist yet, is marked "unavailable" once, and is
    // never reconsidered even though the very same plan goes on to enable
    // that repository.
    let mut rpmfusion_version: Option<String> = None;
    if family == DistroFamily::Fedora {
        let variant = distro::variant_name(release);
        let rpmfusion_present = pm.is_installed(runner, "rpmfusion-nonfree-release");
        let steam_missing = pm.availability(runner, "steam") == Availability::Missing;
        if steam_missing && variant != Some("nobara") && !rpmfusion_present {
            rpmfusion_version = runner
                .run("rpm", &["-E", "%fedora"])
                .filter(|output| output.success && !output.stdout.trim().is_empty())
                .map(|output| output.stdout.trim().to_string());
            if rpmfusion_version.is_none() {
                warnings.push("rpmfusionRequiredManual");
            }
        }
    }
    // Steam lives only in Arch's `[multilib]` repository: on a fresh system
    // with multilib disabled, `pacman -Si steam` fails even though enabling
    // multilib would make it resolvable a moment later.
    let arch_steam_needs_multilib = family == DistroFamily::Arch
        && thirty_two_bit == Some(false)
        && pm.availability(runner, "steam") == Availability::Missing;

    let resolver = Resolver {
        pm: &pm,
        runner,
        thirty_two_bit,
        rpmfusion_pending: rpmfusion_version.is_some(),
        arch_steam_needs_multilib,
    };
    let mut resolved = Resolved::default();
    let mut wanted: Vec<(&'static str, &'static str, &'static [&'static str])> = Vec::new();
    let steam: &[&str] = match family {
        DistroFamily::Ubuntu | DistroFamily::Debian => &["steam-installer"],
        _ => &["steam"],
    };
    let protontricks_native: &[&str] = &["protontricks"];
    wanted.push(("steam", "Steam", steam));
    wanted.push(("lutris", "Lutris", &["lutris"]));
    wanted.push(("wine", "Wine", &["wine"]));
    wanted.push(("winetricks", "Winetricks", &["winetricks"]));
    // Protontricks falls back to its official Flatpak when the native
    // package is not resolvable on this release (verified on Debian 13).
    let protontricks_state = pm.availability(runner, "protontricks");
    let protontricks_flatpak = protontricks_state == Availability::Missing;
    if !protontricks_flatpak {
        wanted.push(("protontricks", "Protontricks", protontricks_native));
    }
    native_components(&resolver, &mut resolved, &mut warnings, &wanted);
    if rpmfusion_version.is_some() {
        warnings.push("rpmfusionRequired");
    } else if warnings.contains(&"rpmfusionRequiredManual") {
        // The version could not be determined, so no repository will be
        // added: keep the specific reason on the Steam component itself
        // instead of the generic "componentUnavailable" note.
        if let Some(component) = resolved.components.iter_mut().find(|c| c.id == "steam") {
            component.note = Some("rpmfusionRequiredManual");
        }
    }

    if protontricks_flatpak {
        push_flatpak_component(runner, &mut resolved, PROTONTRICKS_TARGET);
    }
    push_flatpak_component(runner, &mut resolved, HEROIC_TARGET);

    graphics_components(family, &resolver, gpus, &mut resolved);

    // Enabling step for the 32-bit repositories, planned when a real 32-bit
    // package is part of the install set, or when Steam itself needs
    // multilib, and the machine does not have it active yet. It runs
    // BEFORE the metadata refresh on purpose: the new architecture's index
    // can only be downloaded once it exists, which is exactly what the
    // previous ordering got wrong (steam-libs-i386 was unresolvable
    // because the i386 index had never been fetched).
    let mut enabling: Option<Step> = None;
    if thirty_two_bit == Some(false) {
        let planned_32bit = arch_steam_needs_multilib || resolved.steps.iter().any(|step| {
            matches!(step, Step::InstallNative { packages, .. } if packages.iter().any(|package| {
                package.ends_with(":i386") || package.starts_with("lib32-")
            }))
        });
        if planned_32bit {
            match family {
                DistroFamily::Arch => {
                    enabling = Some(Step::EnableMultilib);
                    warnings.push("multilibRequired");
                }
                DistroFamily::Ubuntu | DistroFamily::Debian => {
                    enabling = Some(Step::AddI386);
                    warnings.push("i386Required");
                }
                DistroFamily::Fedora | DistroFamily::Suse => {}
            }
        }
    }
    // Insertion order matters: each `insert(0, ..)` below pushes the given
    // step to the very front, so the LAST insertion here ends up FIRST in
    // the final plan. RPM Fusion (or multilib) must run, then the refresh
    // that picks up its metadata, then everything else -- exactly the
    // order the real system requires and the previous code got backwards
    // for RPM Fusion (it used to run the refresh first).
    resolved.steps.insert(0, Step::Refresh);
    if let Some(step) = enabling {
        resolved.steps.insert(0, step);
    }
    if let Some(version) = rpmfusion_version {
        resolved.steps.insert(0, Step::InstallRpmFusion { version });
    }
    resolved.warnings.extend(warnings);
    Ok((family, pm, resolved, Vec::new()))
}

fn plan_from_resolved(
    release: &OsRelease,
    gpus: &[GpuInfo],
    family: Option<DistroFamily>,
    pm: Option<&PackageManager>,
    resolved: Resolved,
    unsupported_reason: Option<&'static str>,
) -> GamingPlan {
    let components = resolved
        .components
        .iter()
        .map(|component| PlannedComponent {
            id: component.id,
            name: component.name,
            kind: component.kind,
            state: component.state,
            detail: component.detail.clone(),
            note: component.note,
        })
        .collect();
    let mut changes: Vec<PlannedChange> = Vec::new();
    for step in &resolved.steps {
        let change = match step {
            Step::AddI386 => Some(PlannedChange {
                kind: "addI386",
                label: "addI386",
                detail: Vec::new(),
            }),
            Step::EnableMultilib => Some(PlannedChange {
                kind: "enableMultilib",
                label: "enableMultilib",
                detail: vec![PACMAN_CONF.to_string()],
            }),
            Step::InstallRpmFusion { version } => Some(PlannedChange {
                kind: "addRepository",
                label: "addRpmFusion",
                detail: vec![RPMFUSION_NONFREE_URL.replace("{version}", version)],
            }),
            Step::FlatpakRemote { name, .. } => Some(PlannedChange {
                kind: "addRepository",
                label: "addFlatpakRemote",
                detail: vec![(*name).to_string()],
            }),
            Step::Refresh => Some(PlannedChange {
                kind: "refresh",
                label: "refresh",
                detail: Vec::new(),
            }),
            Step::InstallNative { packages, .. } => Some(PlannedChange {
                kind: "install",
                label: "installNative",
                detail: packages.clone(),
            }),
            Step::InstallFlatpak { app, .. } => Some(PlannedChange {
                kind: "install",
                label: "installFlatpak",
                detail: vec![(*app).to_string()],
            }),
        };
        if let Some(change) = change {
            changes.push(change);
        }
    }
    let package_manager = pm.map(|manager| manager.kind.name());
    let supported = family.is_some() && pm.is_some() && unsupported_reason.is_none();
    GamingPlan {
        distribution: distro::describe(release, package_manager, unsupported_reason),
        gpus: gpus.to_vec(),
        package_manager,
        supported,
        unsupported_reason,
        components,
        changes,
        warnings: resolved.warnings,
    }
}

/// Computes the plan from injected inputs (used by tests and by the real
/// snapshot), never guessing: every package is checked against the real
/// package manager before it can appear in the plan.
pub fn plan_for(
    release: &OsRelease,
    gpus: &[GpuInfo],
    runner: &dyn CommandRunner,
    immutable: bool,
) -> GamingPlan {
    match resolve_core(release, gpus, runner, immutable) {
        Ok((family, pm, resolved, _)) => {
            plan_from_resolved(release, gpus, Some(family), Some(&pm), resolved, None)
        }
        Err(reason) => {
            let pm =
                distro::family(release).and_then(|family| PackageManager::detect(family, runner));
            plan_from_resolved(
                release,
                gpus,
                distro::family(release),
                pm.as_ref(),
                Resolved::default(),
                Some(reason),
            )
        }
    }
}

/// Production snapshot: reads the real `/etc/os-release`, sysfs GPUs and the
/// ostree markers. Read-only, no privileges.
pub fn snapshot() -> GamingPlan {
    let release = fs::read_to_string("/etc/os-release")
        .map(|text| distro::parse_os_release(&text))
        .unwrap_or_default();
    let immutable = distro::is_immutable(
        &release,
        Path::new("/run/ostree-booted").exists(),
        crate::hardware::executable_in_path(&["rpm-ostree"]).is_some(),
    );
    plan_for(
        &release,
        &gpus::detect(),
        &crate::packages::SystemRunner,
        immutable,
    )
}

/// Plan used by the GeForce NOW operation: cloud client only, no driver.
pub fn geforce_now_plan_for(
    release: &OsRelease,
    gpus: &[GpuInfo],
    runner: &dyn CommandRunner,
    immutable: bool,
) -> GamingPlan {
    let family = distro::family(release);
    let pm = family.and_then(|family| PackageManager::detect(family, runner));
    let reason = if immutable {
        Some("unsupportedImmutable")
    } else if family.is_none() {
        Some("unsupportedDistro")
    } else if pm.is_none() {
        Some("packageManagerMissing")
    } else {
        None
    };
    if reason.is_some() {
        return plan_from_resolved(
            release,
            gpus,
            family,
            pm.as_ref(),
            Resolved::default(),
            reason,
        );
    }
    let mut resolved = Resolved::default();
    push_flatpak_component(runner, &mut resolved, GEFORCE_NOW_TARGET);
    plan_from_resolved(release, gpus, family, pm.as_ref(), resolved, None)
}

pub fn geforce_now_snapshot() -> GamingPlan {
    let release = fs::read_to_string("/etc/os-release")
        .map(|text| distro::parse_os_release(&text))
        .unwrap_or_default();
    let immutable = distro::is_immutable(
        &release,
        Path::new("/run/ostree-booted").exists(),
        crate::hardware::executable_in_path(&["rpm-ostree"]).is_some(),
    );
    geforce_now_plan_for(
        &release,
        &gpus::detect(),
        &crate::packages::SystemRunner,
        immutable,
    )
}

/// `true` when the `[multilib]` section is active (not commented) in a
/// pacman.conf-shaped text. Parsing only, never a blind substitution.
pub fn multilib_enabled(conf: &str) -> bool {
    conf.lines().any(|line| line.trim() == "[multilib]")
}

/// Whether pacman itself currently resolves the `multilib` repository, used
/// at *planning* time to decide if an `enableMultilib` step is needed.
///
/// This intentionally goes through the same command as the later
/// verification step (`pacman-conf --repo-list`) instead of re-parsing
/// `/etc/pacman.conf` directly: pacman-conf reports the configuration
/// pacman itself actually applies (Include directives, drop-ins, etc.), so
/// planning and verification can never disagree about whether multilib is
/// really active. It also makes this check go through the injectable
/// `CommandRunner`, like every other planning decision here, instead of
/// silently depending on whatever `/etc/pacman.conf` happens to exist on
/// the machine that runs the test suite.
fn arch_multilib_active(runner: &dyn CommandRunner) -> bool {
    runner
        .run("pacman-conf", &["--repo-list"])
        .is_some_and(|output| {
            output.success && output.stdout.lines().any(|line| line.trim() == "multilib")
        })
}

/// Uncomments `[multilib]` (and its Include line) or appends the section,
/// preserving every other byte of the file. `None` when it is already on.
pub fn enable_multilib_in_conf(conf: &str) -> Option<String> {
    if multilib_enabled(conf) {
        return None;
    }
    let lines: Vec<&str> = conf.lines().collect();
    for (index, line) in lines.iter().enumerate() {
        if line.trim() == "#[multilib]" {
            let mut updated: Vec<String> = lines.iter().map(|l| (*l).to_string()).collect();
            updated[index] = "[multilib]".to_string();
            if let Some(next) = updated.get_mut(index + 1) {
                let trimmed = next.trim_start();
                if let Some(rest) = trimmed.strip_prefix('#') {
                    if rest
                        .trim_start()
                        .to_ascii_lowercase()
                        .starts_with("include")
                    {
                        *next = rest.trim_start().to_string();
                    }
                }
            }
            let mut result = updated.join("\n");
            if conf.ends_with('\n') {
                result.push('\n');
            }
            return Some(result);
        }
    }
    let mut result = conf.to_string();
    if !result.ends_with('\n') && !result.is_empty() {
        result.push('\n');
    }
    result.push_str("[multilib]\nInclude = /etc/pacman.d/mirrorlist\n");
    Some(result)
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StepReport {
    pub step: String,
    pub components: Vec<String>,
    pub operation: String,
    pub packages: Vec<String>,
    pub flatpak_apps: Vec<String>,
    pub exit_code: i32,
    pub ok: bool,
    pub stdout_tail: String,
    pub stderr_tail: String,
}

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallReport {
    pub ok: bool,
    pub steps: Vec<StepReport>,
    /// Packages the post-install verification could not find installed.
    pub missing_packages: Vec<String>,
}

fn tail(text: &str) -> String {
    const LIMIT: usize = 2000;
    let trimmed = text.trim();
    if trimmed.len() <= LIMIT {
        return trimmed.to_string();
    }
    let mut start = trimmed.len() - LIMIT;
    while start < trimmed.len() && !trimmed.is_char_boundary(start) {
        start += 1;
    }
    format!("…{}", &trimmed[start..])
}

fn step_argv(step: &Step, pm: &PackageManager) -> Vec<String> {
    match step {
        Step::AddI386 => vec!["dpkg".into(), "--add-architecture".into(), "i386".into()],
        // Only a read-only confirmation that the just-written `[multilib]`
        // section is now recognised: the actual metadata sync happens once,
        // in the `Refresh` step that immediately follows. A second
        // `pacman -Sy` here would be redundant and is exactly the kind of
        // unpaired sync-without-upgrade Arch's own documentation warns can
        // lead to a partial upgrade if repeated inconsistently over time.
        Step::EnableMultilib => vec!["pacman-conf".into(), "--repo-list".into()],
        Step::InstallRpmFusion { version } => vec![
            pm.binary.to_string(),
            "install".into(),
            "-y".into(),
            RPMFUSION_NONFREE_URL.replace("{version}", version),
        ],
        Step::FlatpakRemote { name, url } => vec![
            "flatpak".into(),
            "remote-add".into(),
            "--system".into(),
            "--if-not-exists".into(),
            (*name).into(),
            (*url).into(),
        ],
        Step::Refresh => pm
            .kind
            .refresh_argv(pm.binary)
            .iter()
            .map(|v| (*v).to_string())
            .collect(),
        Step::InstallNative { packages, .. } => {
            let mut argv: Vec<String> = pm
                .kind
                .install_prefix(pm.binary)
                .iter()
                .map(|v| (*v).to_string())
                .collect();
            argv.extend(packages.iter().cloned());
            argv
        }
        Step::InstallFlatpak { remote, app, .. } => vec![
            "flatpak".into(),
            "install".into(),
            "--system".into(),
            "-y".into(),
            (*remote).into(),
            (*app).into(),
        ],
    }
}

fn step_id(step: &Step) -> &'static str {
    match step {
        Step::AddI386 => "add_i386",
        Step::EnableMultilib => "enable_multilib",
        Step::InstallRpmFusion { .. } => "add_repository",
        Step::FlatpakRemote { .. } => "add_flatpak_remote",
        Step::Refresh => "refresh",
        Step::InstallNative { .. } => "install_packages",
        Step::InstallFlatpak { .. } => "install_flatpak",
    }
}

/// One step executed with everything needed to explain it afterwards: the
/// exact argv, the exit code and the real stderr/stdout tails. The file edit
/// for `[multilib]` is reported like any other step, never in silence.
fn run_one_step(runner: &dyn CommandRunner, pm: &PackageManager, step: &Step) -> StepReport {
    let argv = step_argv(step, pm);
    let (packages, flatpak_apps, components) = match step {
        Step::InstallNative {
            packages,
            components,
        } => (
            packages.clone(),
            Vec::new(),
            components.iter().map(|c| (*c).to_string()).collect(),
        ),
        Step::InstallFlatpak { app, component, .. } => (
            Vec::new(),
            vec![(*app).to_string()],
            vec![(*component).to_string()],
        ),
        _ => (Vec::new(), Vec::new(), Vec::new()),
    };
    let mut report = StepReport {
        step: step_id(step).to_string(),
        components,
        operation: argv.join(" "),
        packages,
        flatpak_apps,
        exit_code: 0,
        ok: false,
        stdout_tail: String::new(),
        stderr_tail: String::new(),
    };

    // The resolver guard runs *here*, right before the real install
    // command, rather than once upfront for the whole plan: on a fresh
    // system the install step for Steam/32-bit packages only resolves
    // after AddI386 and Refresh have already run for real (they are
    // earlier steps in the very same loop). Guarding upfront -- before
    // those prerequisite steps existed -- made every fresh-machine
    // installation fail with `resolver_guard_failed:100`, even though the
    // exact same transaction becomes resolvable moments later once the
    // architecture is really added and the index really refreshed. Placing
    // the check here preserves the original safety property (nothing that
    // would remove or downgrade the graphics stack ever runs for real)
    // while judging the simulation against the state the real install
    // step will actually see.
    if let Step::InstallNative { packages, .. } = step {
        if let Err(error) = resolver_guard(runner, pm, packages) {
            report.exit_code = -1;
            report.stderr_tail = error;
            return report;
        }
    }

    if let Step::EnableMultilib = step {
        match fs::read_to_string(PACMAN_CONF) {
            Ok(conf) => {
                if let Some(updated) = enable_multilib_in_conf(&conf) {
                    if let Err(error) = atomic_write(Path::new(PACMAN_CONF), &updated) {
                        report.exit_code = -1;
                        report.stderr_tail = error;
                        return report;
                    }
                }
            }
            Err(error) => {
                report.exit_code = -1;
                report.stderr_tail = format!("read_failed:{error}");
                return report;
            }
        }
    }

    let refs: Vec<&str> = argv.iter().map(String::as_str).collect();
    match runner.run(refs[0], &refs[1..]) {
        Some(output) => {
            report.exit_code = output.exit_code;
            report.ok = output.success;
            report.stdout_tail = tail(&output.stdout);
            report.stderr_tail = tail(&output.stderr);
        }
        None => {
            report.exit_code = -1;
            report.stderr_tail = format!("{} konnte nicht ausgeführt werden", refs[0]);
        }
    }
    report
}

/// Runs the steps in order, stopping at the first real failure (so a broken
/// transaction never cascades), reporting every step as it completes.
///
/// A non-zero exit code is not, on its own, proof that a step failed: a
/// package manager can exit non-zero purely because of a noisy `%posttrans`
/// scriptlet (observed for real with `wine-core` on a minimal Fedora
/// container: `alternatives` warnings made `dnf` report failure even though
/// every requested package was correctly installed). Exactly like
/// [`verify_installed`] afterwards, a step that reports failure is re-asked
/// to the real package manager before the whole operation is declared
/// broken; only a step that is genuinely missing something it was supposed
/// to install stays failed.
fn execute_steps(
    runner: &dyn CommandRunner,
    pm: &PackageManager,
    steps: &[Step],
    progress: &mut dyn FnMut(&StepReport),
) -> InstallReport {
    let mut report = InstallReport {
        ok: true,
        ..InstallReport::default()
    };
    for step in steps {
        let mut step_report = run_one_step(runner, pm, step);
        if !step_report.ok
            && (!step_report.packages.is_empty() || !step_report.flatpak_apps.is_empty())
            && missing_from_step(runner, pm, &step_report).is_empty()
        {
            step_report.ok = true;
        }
        let failed = !step_report.ok;
        report.steps.push(step_report.clone());
        progress(&step_report);
        if failed {
            report.ok = false;
            break;
        }
    }
    if report.ok {
        report.missing_packages = verify_installed(runner, pm, &report.steps);
        if !report.missing_packages.is_empty() {
            report.ok = false;
        }
    }
    report
}

fn atomic_write(path: &Path, contents: &str) -> Result<(), String> {
    use std::io::Write;
    let dir = path.parent().ok_or("no_parent_directory")?;
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let temp = dir.join(format!(".mgtoolbox-multilib-{stamp}"));
    {
        let mut file = fs::File::create(&temp).map_err(|e| format!("temp_write_failed:{e}"))?;
        file.write_all(contents.as_bytes())
            .map_err(|e| format!("temp_write_failed:{e}"))?;
        file.sync_all()
            .map_err(|e| format!("temp_sync_failed:{e}"))?;
    }
    let _ = fs::set_permissions(
        &temp,
        fs::metadata(path)
            .map(|m| m.permissions())
            .unwrap_or_else(|_| fs::Permissions::from_mode(0o644)),
    );
    fs::rename(&temp, path).map_err(|e| format!("rename_failed:{e}"))?;
    Ok(())
}

use std::os::unix::fs::PermissionsExt;

/// The resolver guard: on APT, a simulated transaction must not remove or
/// downgrade the graphics stack; otherwise the whole operation is aborted
/// before anything real happens. The simulation's own exit code is part of
/// the check, so an unresolvable plan is caught here too.
fn resolver_guard(
    runner: &dyn CommandRunner,
    pm: &PackageManager,
    packages: &[String],
) -> Result<(), String> {
    let Some(argv) = pm.kind.simulate_install_argv(packages) else {
        return Ok(());
    };
    let refs: Vec<&str> = argv.iter().map(String::as_str).collect();
    let Some(output) = runner.run(refs[0], &refs[1..]) else {
        return Err("resolver_guard_unavailable".into());
    };
    if !output.success {
        return Err(format!("resolver_guard_failed:{}", output.exit_code));
    }
    if removes_graphics(&output.stdout) {
        return Err("resolver_would_remove_graphics".into());
    }
    Ok(())
}

fn removes_graphics(simulation: &str) -> bool {
    crate::packages::PackageManagerKind::simulation_removes_graphics(simulation)
}

/// Every package/Flatpak app named by one step that the package manager
/// does not actually report as installed, checked directly and fresh --
/// never inferred from the step's own exit code.
fn missing_from_step(
    runner: &dyn CommandRunner,
    pm: &PackageManager,
    step: &StepReport,
) -> Vec<String> {
    let mut missing: Vec<String> = Vec::new();
    for package in &step.packages {
        if !pm.is_installed(runner, package) {
            missing.push(package.clone());
        }
    }
    for app in &step.flatpak_apps {
        let installed = runner
            .run("flatpak", &["info", app])
            .is_some_and(|output| output.success);
        if !installed {
            missing.push(app.clone());
        }
    }
    missing
}

/// Verifies that every package of every executed step really is present
/// afterwards; the package manager is asked again, exit codes alone are
/// never trusted.
fn verify_installed(
    runner: &dyn CommandRunner,
    pm: &PackageManager,
    steps: &[StepReport],
) -> Vec<String> {
    steps
        .iter()
        .filter(|step| step.ok)
        .flat_map(|step| missing_from_step(runner, pm, step))
        .collect()
}

/// A package manager already running would make every transaction fail with
/// a lock error; better to say so plainly before touching anything.
/// Processes that really hold a package-manager lock and must block a new
/// transaction. `packagekitd` is deliberately absent: it is a permanently
/// running desktop session daemon, not an active transaction (the real
/// lock holders it spawns, apt/dpkg, are the ones listed here).
pub const BUSY_PROCESSES: &[&str] = &[
    "apt", "apt-get", "dpkg", "dpkg-deb", "dnf", "dnf5", "pacman", "zypper",
];

fn package_manager_busy() -> Option<String> {
    let entries = fs::read_dir("/proc").ok()?;
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if name.parse::<u32>().is_err() {
            continue;
        }
        if let Ok(comm) = fs::read_to_string(entry.path().join("comm")) {
            let comm = comm.trim();
            if BUSY_PROCESSES.contains(&comm) {
                return Some(comm.to_string());
            }
        }
    }
    None
}

fn resolve_release() -> OsRelease {
    fs::read_to_string("/etc/os-release")
        .map(|text| distro::parse_os_release(&text))
        .unwrap_or_default()
}

fn immutable_now() -> bool {
    let release = resolve_release();
    distro::is_immutable(
        &release,
        Path::new("/run/ostree-booted").exists(),
        crate::hardware::executable_in_path(&["rpm-ostree"]).is_some(),
    )
}

fn busy_report(process: &str) -> InstallReport {
    InstallReport {
        ok: false,
        steps: vec![StepReport {
            step: "preflight".to_string(),
            components: Vec::new(),
            operation: String::new(),
            packages: Vec::new(),
            flatpak_apps: Vec::new(),
            exit_code: -1,
            ok: false,
            stdout_tail: String::new(),
            stderr_tail: format!("package_manager_busy:{process}"),
        }],
        missing_packages: Vec::new(),
    }
}

/// Executes one allow-listed operation. Everything is re-derived here from
/// the real system: the caller never names a package, a repository or a
/// command. The result is always a step-by-step report, never a bare
/// "maybe it worked".
pub fn apply_operation(
    args: &[String],
    progress: &mut dyn FnMut(&StepReport),
) -> Result<InstallReport, String> {
    let runner = crate::packages::SystemRunner;
    match args {
        [action] if action == "programs-prepare-gaming" => {
            if let Some(process) = package_manager_busy() {
                return Ok(busy_report(&process));
            }
            let release = resolve_release();
            let gpus = gpus::detect();
            let (_family, pm, resolved, _) =
                resolve_core(&release, &gpus, &runner, immutable_now())?;
            Ok(execute_steps(&runner, &pm, &resolved.steps, progress))
        }
        [action] if action == "programs-install-geforce-now" => {
            if let Some(process) = package_manager_busy() {
                return Ok(busy_report(&process));
            }
            let release = resolve_release();
            let gpus = gpus::detect();
            let plan = geforce_now_plan_for(&release, &gpus, &runner, immutable_now());
            if !plan.supported {
                return Err(plan.unsupported_reason.unwrap_or("unsupported").to_string());
            }
            let mut resolved = Resolved::default();
            push_flatpak_component(&runner, &mut resolved, GEFORCE_NOW_TARGET);
            let family = distro::family(&release).ok_or("unsupportedDistro")?;
            let pm = PackageManager::detect(family, &runner).ok_or("packageManagerMissing")?;
            Ok(execute_steps(&runner, &pm, &resolved.steps, progress))
        }
        _ => Err("invalid_operation".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::packages::{CommandOutput, CommandRunner};
    use std::collections::HashMap;

    #[derive(Default)]
    struct MockRunner {
        outputs: HashMap<String, CommandOutput>,
    }

    impl MockRunner {
        fn with(mut self, program: &str, args: &[&str], success: bool, stdout: &str) -> Self {
            self.outputs.insert(
                format!("{program}\u{0}{}", args.join("\u{0}")),
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
                .get(&format!("{program}\u{0}{}", args.join("\u{0}")))
                .cloned()
        }
    }

    fn ubuntu_release() -> OsRelease {
        distro::parse_os_release(
            "ID=ubuntu\nID_LIKE=debian\nVERSION_ID=24.04\nVERSION_CODENAME=noble\n",
        )
    }

    fn amd_gpu() -> GpuInfo {
        GpuInfo {
            vendor: "amd",
            name: "Navi 22".to_string(),
            driver: Some("amdgpu".to_string()),
        }
    }

    fn nvidia_gpu() -> GpuInfo {
        GpuInfo {
            vendor: "nvidia",
            name: "GA106".to_string(),
            driver: Some("nvidia".to_string()),
        }
    }

    fn base_ubuntu_runner() -> MockRunner {
        MockRunner::default()
            .with("apt-get", &["--version"], true, "apt 2.8")
            .with("dpkg", &["--print-foreign-architectures"], true, "")
            .with("flatpak", &["--version"], true, "Flatpak 1.14")
            .with(
                "flatpak",
                &["remotes", "--system", "--columns=name"],
                true,
                "flathub\n",
            )
            .with("flatpak", &["info", HEROIC_APP], false, "")
            .with("flatpak", &["info", PROTONTRICKS_APP], false, "")
            .with(
                "apt-cache",
                &["policy", "steam-installer"],
                true,
                "  Candidate: 1.0\n",
            )
            .with(
                "apt-cache",
                &["policy", "lutris"],
                true,
                "  Candidate: 1.0\n",
            )
            .with("apt-cache", &["policy", "wine"], true, "  Candidate: 1.0\n")
            .with(
                "apt-cache",
                &["policy", "winetricks"],
                true,
                "  Candidate: 1.0\n",
            )
            .with(
                "apt-cache",
                &["policy", "protontricks"],
                true,
                "  Candidate: 1.0\n",
            )
            .with(
                "apt-cache",
                &["policy", "mesa-vulkan-drivers:amd64"],
                true,
                "  Candidate: 1.0\n",
            )
            .with(
                "apt-cache",
                &["policy", "mesa-vulkan-drivers:i386"],
                true,
                "  Candidate: 1.0\n",
            )
            .with(
                "apt-cache",
                &["policy", "libgl1-mesa-dri:i386"],
                true,
                "  Candidate: 1.0\n",
            )
    }

    #[test]
    fn ubuntu_plan_lists_the_approved_set_and_the_i386_change() {
        let runner = base_ubuntu_runner();
        let plan = plan_for(&ubuntu_release(), &[amd_gpu()], &runner, false);
        assert!(plan.supported);
        assert_eq!(plan.package_manager, Some("apt"));
        let by_id = |id: &str| plan.components.iter().find(|c| c.id == id).unwrap();
        assert_eq!(by_id("steam").detail, vec!["steam-installer".to_string()]);
        assert_eq!(by_id("wine").state, "toInstall");
        assert_eq!(
            by_id("vulkan32").detail,
            vec!["mesa-vulkan-drivers:i386".to_string()]
        );
        assert!(plan.changes.iter().any(|c| c.kind == "addI386"));
        assert!(plan.warnings.contains(&"i386Required"));
    }

    #[test]
    fn an_already_installed_package_is_never_planned_again() {
        let runner = base_ubuntu_runner()
            .with(
                "dpkg-query",
                &["-W", "-f=${Status}", "lutris"],
                true,
                "install ok installed",
            )
            .with(
                "dpkg-query",
                &["-W", "-f=${Status}", "wine"],
                true,
                "install ok installed",
            )
            .with(
                "dpkg-query",
                &["-W", "-f=${Status}", "winetricks"],
                true,
                "install ok installed",
            )
            .with(
                "dpkg-query",
                &["-W", "-f=${Status}", "protontricks"],
                true,
                "install ok installed",
            )
            .with(
                "dpkg-query",
                &["-W", "-f=${Status}", "mesa-vulkan-drivers:amd64"],
                true,
                "install ok installed",
            );
        let plan = plan_for(&ubuntu_release(), &[amd_gpu()], &runner, false);
        let install = plan
            .changes
            .iter()
            .find(|change| change.kind == "install")
            .map(|change| change.detail.clone())
            .unwrap_or_default();
        assert!(!install.contains(&"lutris".to_string()));
        assert!(!install.contains(&"wine".to_string()));
        assert!(install.contains(&"steam-installer".to_string()));
        // Steam is still to install, so the i386 prerequisite is still planned.
        assert!(plan.changes.iter().any(|change| change.kind == "addI386"));
    }

    #[test]
    fn nvidia_on_ubuntu_only_adds_the_matching_installed_branch() {
        let runner = base_ubuntu_runner()
            .with(
                "dpkg-query",
                &["-W", "-f=${Package}\n", "libnvidia-gl-*:amd64"],
                true,
                "libnvidia-gl-550:amd64\n",
            )
            .with(
                "dpkg-query",
                &["-W", "-f=${Package}\n", "libnvidia-gl-*:i386"],
                true,
                "",
            )
            .with(
                "apt-cache",
                &["policy", "libnvidia-gl-550:i386"],
                true,
                "  Candidate: 1.0\n",
            );
        let plan = plan_for(&ubuntu_release(), &[nvidia_gpu()], &runner, false);
        let nvidia = plan.components.iter().find(|c| c.id == "nvidia32").unwrap();
        assert_eq!(nvidia.detail, vec!["libnvidia-gl-550:i386".to_string()]);
        assert_eq!(nvidia.state, "toInstall");
    }

    #[test]
    fn nvidia_without_an_identifiable_stack_changes_nothing() {
        let runner = base_ubuntu_runner().with(
            "dpkg-query",
            &["-W", "-f=${Package}\n", "libnvidia-gl-*:amd64"],
            true,
            "",
        );
        let plan = plan_for(&ubuntu_release(), &[nvidia_gpu()], &runner, false);
        let nvidia = plan.components.iter().find(|c| c.id == "nvidia32").unwrap();
        assert_eq!(nvidia.state, "unavailable");
        assert!(plan.warnings.contains(&"nvidiaStackUnknown"));
        assert!(!plan
            .changes
            .iter()
            .any(|change| change.detail.iter().any(|d| d.contains("nvidia"))));
    }

    #[test]
    fn hybrid_amd_and_nvidia_needs_both_userspace_stacks() {
        let runner = base_ubuntu_runner().with(
            "dpkg-query",
            &["-W", "-f=${Package}\n", "libnvidia-gl-*:amd64"],
            true,
            "libnvidia-gl-590:amd64\n",
        );
        let plan = plan_for(
            &ubuntu_release(),
            &[amd_gpu(), nvidia_gpu()],
            &runner,
            false,
        );
        let ids: Vec<&str> = plan.components.iter().map(|c| c.id).collect();
        assert!(ids.contains(&"vulkan32"));
        assert!(ids.contains(&"nvidia32"));
    }

    #[test]
    fn arch_plans_multilib_and_the_amd_vulkan_pair() {
        let release = distro::parse_os_release("ID=cachyos\nID_LIKE=arch\n");
        let runner = MockRunner::default()
            .with("pacman", &["--version"], true, "Pacman 6.1")
            .with("pacman", &["-Qq"], true, "")
            .with("pacman", &["-Si", "steam"], true, "Name : steam\n")
            .with("pacman", &["-Si", "lutris"], true, "Name : lutris\n")
            .with("pacman", &["-Si", "wine"], true, "Name : wine\n")
            .with(
                "pacman",
                &["-Si", "winetricks"],
                true,
                "Name : winetricks\n",
            )
            .with(
                "pacman",
                &["-Si", "protontricks"],
                true,
                "Name : protontricks\n",
            )
            .with(
                "pacman",
                &["-Si", "vulkan-radeon"],
                true,
                "Name : vulkan-radeon\n",
            )
            .with(
                "pacman",
                &["-Si", "lib32-vulkan-radeon"],
                true,
                "Name : x\n",
            )
            .with("pacman", &["-Si", "lib32-mesa"], true, "Name : x\n")
            .with("flatpak", &["--version"], false, "")
            .with("dpkg", &["--print-foreign-architectures"], false, "");
        let plan = plan_for(&release, &[amd_gpu()], &runner, false);
        assert_eq!(plan.package_manager, Some("pacman"));
        assert!(plan.changes.iter().any(|c| c.kind == "enableMultilib"));
        let vulkan = plan.components.iter().find(|c| c.id == "vulkan32").unwrap();
        assert_eq!(vulkan.detail, vec!["lib32-vulkan-radeon".to_string()]);
    }

    #[test]
    fn arch_with_multilib_already_active_never_replans_enabling_it() {
        // Regression test for the real container finding: a machine that
        // already had multilib enabled by a previous run must plan zero
        // `enableMultilib` steps, decided from `pacman-conf --repo-list`
        // (what pacman itself resolves), not from a second, independent
        // re-parse of `/etc/pacman.conf` that could disagree with it.
        let release = distro::parse_os_release("ID=arch\n");
        let runner = MockRunner::default()
            .with("pacman", &["--version"], true, "Pacman 7.1")
            .with("pacman", &["-Qq"], true, "")
            .with(
                "pacman-conf",
                &["--repo-list"],
                true,
                "core\nextra\nmultilib\n",
            )
            .with("pacman", &["-Si", "steam"], true, "Name : steam\n")
            .with("pacman", &["-Si", "lutris"], true, "Name : lutris\n")
            .with("pacman", &["-Si", "wine"], true, "Name : wine\n")
            .with(
                "pacman",
                &["-Si", "winetricks"],
                true,
                "Name : winetricks\n",
            )
            .with(
                "pacman",
                &["-Si", "protontricks"],
                true,
                "Name : protontricks\n",
            )
            .with("flatpak", &["--version"], false, "")
            .with("dpkg", &["--print-foreign-architectures"], false, "");
        let plan = plan_for(&release, &[], &runner, false);
        assert!(
            !plan.changes.iter().any(|c| c.kind == "enableMultilib"),
            "multilib is already active: nothing should plan to enable it again"
        );
        let steam = plan.components.iter().find(|c| c.id == "steam").unwrap();
        assert_eq!(steam.state, "toInstall");
    }

    #[test]
    fn fresh_arch_enables_multilib_for_steam_even_without_any_other_32bit_package() {
        // Regression test for the real container finding: on a fresh Arch
        // install with `[multilib]` still disabled, `pacman -Si steam`
        // genuinely fails (steam only exists in multilib), matching the
        // exact real error observed (`error: package 'steam' was not
        // found`). No GPU-driven 32-bit package is needed here on purpose,
        // to prove multilib gets enabled for Steam alone, and that Steam
        // itself ends up in the install step afterwards.
        let release = distro::parse_os_release("ID=arch\n");
        let runner = MockRunner::default()
            .with("pacman", &["--version"], true, "Pacman 7.1")
            .with("pacman", &["-Qq"], true, "")
            .with(
                "pacman",
                &["-Si", "steam"],
                false,
                "error: package 'steam' was not found\n",
            )
            .with("pacman", &["-Si", "lutris"], true, "Name : lutris\n")
            .with("pacman", &["-Si", "wine"], true, "Name : wine\n")
            .with(
                "pacman",
                &["-Si", "winetricks"],
                true,
                "Name : winetricks\n",
            )
            .with(
                "pacman",
                &["-Si", "protontricks"],
                true,
                "Name : protontricks\n",
            )
            .with("flatpak", &["--version"], false, "")
            .with("dpkg", &["--print-foreign-architectures"], false, "");
        let plan = plan_for(&release, &[], &runner, false);
        assert!(
            plan.changes.iter().any(|c| c.kind == "enableMultilib"),
            "multilib must be proposed even with no GPU packages involved"
        );
        let steam = plan.components.iter().find(|c| c.id == "steam").unwrap();
        assert_eq!(
            steam.state, "toInstall",
            "steam must not be left unavailable once multilib is being enabled"
        );
        assert!(plan
            .changes
            .iter()
            .any(|c| c.kind == "install" && c.detail.iter().any(|d| d == "steam")));
        let multilib_index = plan
            .changes
            .iter()
            .position(|c| c.kind == "enableMultilib")
            .unwrap();
        let refresh_index = plan
            .changes
            .iter()
            .position(|c| c.kind == "refresh")
            .unwrap();
        assert!(
            multilib_index < refresh_index,
            "enableMultilib must precede refresh"
        );
    }

    #[test]
    fn fedora_standard_plans_rpmfusion_only_when_steam_is_not_resolvable() {
        let release = distro::parse_os_release("ID=fedora\nVERSION_ID=42\n");
        let runner = MockRunner::default()
            .with("dnf", &["--version"], true, "4.20")
            .with("rpm", &["-q", "rpmfusion-nonfree-release"], false, "")
            .with("rpm", &["-E", "%fedora"], true, "42\n")
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "steam",
                ],
                true,
                "",
            )
            .with(
                "dnf",
                &["-q", "repoquery", "--available", "--qf", "%{name}", "steam"],
                true,
                "",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "lutris",
                ],
                true,
                "lutris\n",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "wine",
                ],
                true,
                "wine\n",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "winetricks",
                ],
                true,
                "winetricks\n",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "protontricks",
                ],
                true,
                "protontricks\n",
            )
            .with(
                "rpm",
                &["-qa", "--qf", "%{NAME}\n", "nvidia-*-G0*"],
                true,
                "",
            )
            .with("flatpak", &["--version"], true, "Flatpak 1.14")
            .with(
                "flatpak",
                &["remotes", "--system", "--columns=name"],
                true,
                "flathub\n",
            )
            .with("flatpak", &["info", HEROIC_APP], false, "")
            .with("flatpak", &["info", PROTONTRICKS_APP], false, "")
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "mesa-vulkan-drivers.x86_64",
                ],
                true,
                "mesa-vulkan-drivers.x86_64\n",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "mesa-vulkan-drivers.i686",
                ],
                true,
                "mesa-vulkan-drivers.i686\n",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "mesa-dri-drivers.i686",
                ],
                true,
                "mesa-dri-drivers.i686\n",
            );
        let plan = plan_for(&release, &[amd_gpu()], &runner, false);
        assert!(plan.changes.iter().any(|c| c.kind == "addRepository"));
        assert!(plan.warnings.contains(&"rpmfusionRequired"));
        assert!(plan.changes.iter().any(|c| c
            .detail
            .iter()
            .any(|d| d.contains("rpmfusion-nonfree-release-42"))));
        // Regression test for the real bug: Steam itself must end up in the
        // plan as installable, not silently left "unavailable" forever just
        // because it could not resolve before RPM Fusion existed.
        let steam = plan.components.iter().find(|c| c.id == "steam").unwrap();
        assert_eq!(steam.state, "toInstall");
        assert!(plan
            .changes
            .iter()
            .any(|c| c.kind == "install" && c.detail.iter().any(|d| d == "steam")));
        // And the repository must be added, and metadata refreshed, before
        // Steam is ever installed -- not after.
        let repo_index = plan
            .changes
            .iter()
            .position(|c| c.kind == "addRepository")
            .unwrap();
        let refresh_index = plan
            .changes
            .iter()
            .position(|c| c.kind == "refresh")
            .unwrap();
        let install_index = plan
            .changes
            .iter()
            .position(|c| c.kind == "install" && c.detail.iter().any(|d| d == "steam"))
            .unwrap();
        assert!(
            repo_index < refresh_index && refresh_index < install_index,
            "expected addRepository < refresh < install(steam), got {:?}",
            plan.changes.iter().map(|c| c.kind).collect::<Vec<_>>()
        );
    }

    #[test]
    fn nobara_never_has_rpmfusion_planned() {
        let release = distro::parse_os_release("ID=nobara\nID_LIKE=\"fedora\"\nVERSION_ID=42\n");
        let runner = MockRunner::default()
            .with("dnf", &["--version"], true, "4.20")
            .with("rpm", &["-q", "rpmfusion-nonfree-release"], true, "")
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "steam",
                ],
                true,
                "steam\n",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "lutris",
                ],
                true,
                "lutris\n",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "wine",
                ],
                true,
                "wine\n",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "winetricks",
                ],
                true,
                "winetricks\n",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "protontricks",
                ],
                true,
                "protontricks\n",
            )
            .with("flatpak", &["--version"], true, "Flatpak 1.14")
            .with(
                "flatpak",
                &["remotes", "--system", "--columns=name"],
                true,
                "flathub\n",
            )
            .with("flatpak", &["info", HEROIC_APP], false, "")
            .with("flatpak", &["info", PROTONTRICKS_APP], false, "")
            .with(
                "rpm",
                &["-qa", "--qf", "%{NAME}\n", "nvidia-*-G0*"],
                true,
                "",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "mesa-vulkan-drivers.x86_64",
                ],
                true,
                "x\n",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "mesa-vulkan-drivers.i686",
                ],
                true,
                "x\n",
            )
            .with(
                "dnf",
                &[
                    "-q",
                    "--cacheonly",
                    "repoquery",
                    "--available",
                    "--qf",
                    "%{name}",
                    "mesa-dri-drivers.i686",
                ],
                true,
                "x\n",
            );
        let plan = plan_for(&release, &[amd_gpu()], &runner, false);
        assert!(!plan.changes.iter().any(|c| c.kind == "addRepository"));
        assert!(!plan.warnings.contains(&"rpmfusionRequired"));
    }

    #[test]
    fn nobara_is_mapped_to_fedora_family() {
        let release = distro::parse_os_release("ID=nobara\nID_LIKE=fedora\n");
        assert_eq!(distro::family(&release), Some(DistroFamily::Fedora));
        assert_eq!(distro::variant_name(&release), Some("nobara"));
    }

    #[test]
    fn suse_steam_comes_from_the_non_oss_repo_and_is_reported_when_missing() {
        let release = distro::parse_os_release("ID=opensuse-tumbleweed\nID_LIKE=\"suse\"\n");
        let runner = MockRunner::default()
            .with("zypper", &["--version"], true, "zypper 1.14")
            .with(
                "zypper",
                &[
                    "--non-interactive",
                    "--quiet",
                    "search",
                    "--match-exact",
                    "--type",
                    "package",
                    "steam",
                ],
                true,
                "",
            )
            .with(
                "zypper",
                &[
                    "--non-interactive",
                    "--quiet",
                    "search",
                    "--match-exact",
                    "--type",
                    "package",
                    "lutris",
                ],
                true,
                "S | lutris | package | 1.0 | x86_64\n",
            )
            .with(
                "zypper",
                &[
                    "--non-interactive",
                    "--quiet",
                    "search",
                    "--match-exact",
                    "--type",
                    "package",
                    "wine",
                ],
                true,
                "S | wine | package | 1.0 | x86_64\n",
            )
            .with(
                "zypper",
                &[
                    "--non-interactive",
                    "--quiet",
                    "search",
                    "--match-exact",
                    "--type",
                    "package",
                    "winetricks",
                ],
                true,
                "S | winetricks | package | 1.0 | noarch\n",
            )
            .with(
                "zypper",
                &[
                    "--non-interactive",
                    "--quiet",
                    "search",
                    "--match-exact",
                    "--type",
                    "package",
                    "protontricks",
                ],
                true,
                "S | protontricks | package | 1.0 | noarch\n",
            )
            .with(
                "zypper",
                &[
                    "--non-interactive",
                    "--quiet",
                    "search",
                    "--match-exact",
                    "--type",
                    "package",
                    "libvulkan_radeon",
                ],
                true,
                "S | libvulkan_radeon | package | 1.0 | x86_64\n",
            )
            .with(
                "zypper",
                &[
                    "--non-interactive",
                    "--quiet",
                    "search",
                    "--match-exact",
                    "--type",
                    "package",
                    "libvulkan_radeon-32bit",
                ],
                true,
                "S | libvulkan_radeon-32bit | package | 1.0 | x86_64\n",
            )
            .with(
                "zypper",
                &[
                    "--non-interactive",
                    "--quiet",
                    "search",
                    "--match-exact",
                    "--type",
                    "package",
                    "Mesa-dri-32bit",
                ],
                true,
                "S | Mesa-dri-32bit | package | 1.0 | x86_64\n",
            )
            .with("flatpak", &["--version"], true, "Flatpak 1.14")
            .with(
                "flatpak",
                &["remotes", "--system", "--columns=name"],
                true,
                "flathub\n",
            )
            .with("flatpak", &["info", HEROIC_APP], false, "")
            .with("flatpak", &["info", PROTONTRICKS_APP], false, "")
            .with(
                "rpm",
                &["-qa", "--qf", "%{NAME}\n", "nvidia-*-G0*"],
                true,
                "",
            );
        let plan = plan_for(&release, &[amd_gpu()], &runner, false);
        let steam = plan.components.iter().find(|c| c.id == "steam").unwrap();
        assert_eq!(steam.state, "unavailable");
        assert!(plan.warnings.contains(&"componentUnavailable"));
        let vulkan = plan.components.iter().find(|c| c.id == "vulkan32").unwrap();
        assert_eq!(vulkan.detail, vec!["libvulkan_radeon-32bit".to_string()]);
    }

    #[test]
    fn immutable_systems_are_reported_unsupported_with_a_reason() {
        let release = distro::parse_os_release("ID=bazzite\nID_LIKE=fedora\n");
        let plan = plan_for(&release, &[amd_gpu()], &MockRunner::default(), true);
        assert!(!plan.supported);
        assert_eq!(plan.unsupported_reason, Some("unsupportedImmutable"));
        assert!(plan.components.is_empty());
    }

    #[test]
    fn an_unknown_distro_is_reported_unsupported_never_guessed() {
        let release = distro::parse_os_release("ID=some-niche-distro\n");
        let plan = plan_for(&release, &[], &MockRunner::default(), false);
        assert!(!plan.supported);
        assert_eq!(plan.unsupported_reason, Some("unsupportedDistro"));
    }

    #[test]
    fn geforce_now_is_cloud_only_and_never_touches_the_driver() {
        let runner = base_ubuntu_runner()
            .with(
                "flatpak",
                &["remotes", "--system", "--columns=name"],
                true,
                "flathub\nGeForceNOW\n",
            )
            .with("flatpak", &["info", GEFORCE_NOW_APP], false, "");
        let plan = geforce_now_plan_for(&ubuntu_release(), &[amd_gpu()], &runner, false);
        assert!(plan.supported);
        assert!(plan
            .changes
            .iter()
            .any(|c| c.detail.iter().any(|d| d == GEFORCE_NOW_APP)));
        assert!(!plan.changes.iter().any(|c| c
            .detail
            .iter()
            .any(|d| d.to_ascii_lowercase().contains("nvidia-gl"))));
    }

    #[test]
    fn geforce_now_is_installable_on_an_amd_only_machine() {
        let runner = base_ubuntu_runner()
            .with(
                "flatpak",
                &["remotes", "--system", "--columns=name"],
                true,
                "GeForceNOW\n",
            )
            .with("flatpak", &["info", GEFORCE_NOW_APP], false, "");
        let plan = geforce_now_plan_for(&ubuntu_release(), &[amd_gpu()], &runner, false);
        assert!(plan.supported);
    }

    #[test]
    fn package_manager_failure_is_reported_not_swallowed() {
        let runner = MockRunner::default().with("dnf", &["--version"], false, "");
        let release = distro::parse_os_release("ID=fedora\n");
        let plan = plan_for(&release, &[], &runner, false);
        assert!(!plan.supported);
        assert_eq!(plan.unsupported_reason, Some("packageManagerMissing"));
    }

    #[test]
    fn multilib_detection_and_edit_preserve_the_rest_of_the_file() {
        let commented = "# comment\n[core]\nInclude = /etc/pacman.d/mirrorlist\n\n#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n";
        assert!(!multilib_enabled(commented));
        let updated = enable_multilib_in_conf(commented).unwrap();
        assert!(multilib_enabled(&updated));
        assert!(updated.contains("[core]"));
        assert!(updated.contains("# comment"));
        assert!(updated.contains("Include = /etc/pacman.d/mirrorlist"));
        let active = "[core]\n[multilib]\nInclude = /etc/pacman.d/mirrorlist\n";
        assert!(multilib_enabled(active));
        assert!(enable_multilib_in_conf(active).is_none());
    }

    #[test]
    fn multilib_is_appended_when_no_commented_section_exists() {
        let conf = "[core]\nInclude = /etc/pacman.d/mirrorlist\n";
        let updated = enable_multilib_in_conf(conf).unwrap();
        assert!(updated.starts_with(conf));
        assert!(updated.ends_with("[multilib]\nInclude = /etc/pacman.d/mirrorlist\n"));
    }

    #[test]
    fn the_plan_serializes_to_json_exactly_as_the_frontend_expects() {
        // A serialization failure would make the Tauri command reject with an
        // opaque error, which is exactly the "empty card" symptom.
        let plan = plan_for(
            &ubuntu_release(),
            &[amd_gpu()],
            &base_ubuntu_runner(),
            false,
        );
        let json = serde_json::to_string(&plan).expect("the plan must serialize");
        assert!(json.contains("\"distribution\""));
        assert!(json.contains("\"components\""));
        assert!(json.contains("\"changes\""));
        assert!(json.contains("\"packageManager\""));
        let report = InstallReport {
            ok: true,
            steps: vec![StepReport {
                step: "refresh".to_string(),
                components: Vec::new(),
                operation: "apt-get update".to_string(),
                packages: Vec::new(),
                flatpak_apps: Vec::new(),
                exit_code: 0,
                ok: true,
                stdout_tail: String::new(),
                stderr_tail: String::new(),
            }],
            missing_packages: Vec::new(),
        };
        let roundtrip: InstallReport =
            serde_json::from_str(&serde_json::to_string(&report).unwrap()).unwrap();
        assert!(roundtrip.ok);
        assert_eq!(roundtrip.steps[0].exit_code, 0);
    }

    #[test]
    #[ignore = "reads this machine's real distribution/GPU/package state; run manually with -- --ignored --nocapture"]
    fn live_gaming_plan_against_the_real_system() {
        let plan = snapshot();
        eprintln!(
            "distro={} family={} variant={:?} pm={:?} supported={}",
            plan.distribution.name,
            plan.distribution.family,
            plan.distribution.variant,
            plan.package_manager,
            plan.supported
        );
        for gpu in &plan.gpus {
            eprintln!("gpu: [{}] {} driver={:?}", gpu.vendor, gpu.name, gpu.driver);
        }
        for component in &plan.components {
            eprintln!(
                "component: {:<14} {:<10} {}",
                component.id,
                component.state,
                component.detail.join(", ")
            );
        }
        for change in &plan.changes {
            eprintln!("change: {:<16} {}", change.kind, change.detail.join(", "));
        }
        eprintln!("warnings: {:?}", plan.warnings);
        if plan.distribution.family == "unknown" {
            // A genuinely unrecognised distro (e.g. Void, which this
            // module never claims to support) must degrade safely instead
            // of half-producing a plan: not supported, no component and no
            // change ever proposed, never a crash. This is the real
            // contract this test exists to prove, on every machine that
            // can run it -- not just on the officially supported ones.
            assert!(
                !plan.supported,
                "an unrecognised distro must report unsupported"
            );
            assert!(
                plan.components.is_empty(),
                "an unrecognised distro must never propose components"
            );
            assert!(
                plan.changes.is_empty(),
                "an unrecognised distro must never propose changes"
            );
        }
    }

    #[test]
    fn the_i386_step_runs_before_the_metadata_refresh() {
        // Regression test for the real `apply_failed`: the i386 index can
        // only be fetched after the architecture exists, so the enabling
        // step must come first.
        let runner = base_ubuntu_runner();
        let release = ubuntu_release();
        let (_family, _pm, resolved, _warnings) =
            resolve_core(&release, &[amd_gpu()], &runner, false).unwrap();
        let ids: Vec<&str> = resolved.steps.iter().map(step_id).collect();
        let add = ids.iter().position(|id| *id == "add_i386").unwrap();
        let refresh = ids.iter().position(|id| *id == "refresh").unwrap();
        assert!(add < refresh, "add_i386 must precede refresh, got {ids:?}");
        // The install of the 32-bit packages must come after the refresh.
        let install = ids.iter().position(|id| *id == "install_packages").unwrap();
        assert!(
            refresh < install,
            "refresh must precede installs, got {ids:?}"
        );
    }

    #[test]
    fn arch_multilib_step_also_precedes_the_refresh() {
        let release = distro::parse_os_release("ID=cachyos\nID_LIKE=arch\n");
        let runner = MockRunner::default()
            .with("pacman", &["--version"], true, "Pacman 6.1")
            .with("pacman", &["-Qq"], true, "")
            .with("pacman", &["-Si", "steam"], true, "Name : steam\n")
            .with("pacman", &["-Si", "lutris"], true, "Name : lutris\n")
            .with("pacman", &["-Si", "wine"], true, "Name : wine\n")
            .with(
                "pacman",
                &["-Si", "winetricks"],
                true,
                "Name : winetricks\n",
            )
            .with(
                "pacman",
                &["-Si", "protontricks"],
                true,
                "Name : protontricks\n",
            )
            .with(
                "pacman",
                &["-Si", "vulkan-radeon"],
                true,
                "Name : vulkan-radeon\n",
            )
            .with(
                "pacman",
                &["-Si", "lib32-vulkan-radeon"],
                true,
                "Name : x\n",
            )
            .with("pacman", &["-Si", "lib32-mesa"], true, "Name : x\n")
            .with("flatpak", &["--version"], false, "")
            .with("dpkg", &["--print-foreign-architectures"], false, "");
        let (_family, _pm, resolved, _) =
            resolve_core(&release, &[amd_gpu()], &runner, false).unwrap();
        let ids: Vec<&str> = resolved.steps.iter().map(step_id).collect();
        let multilib = ids.iter().position(|id| *id == "enable_multilib").unwrap();
        let refresh = ids.iter().position(|id| *id == "refresh").unwrap();
        assert!(
            multilib < refresh,
            "enable_multilib must precede refresh, got {ids:?}"
        );
    }

    #[test]
    fn a_failed_step_produces_a_report_with_exit_code_and_stderr() {
        let pm = PackageManager::new(crate::packages::PackageManagerKind::Apt);
        let runner = MockRunner::default()
            .with(
                "apt-get",
                &["-s", "install", "steam-installer"],
                true,
                "Inst steam-installer\n",
            )
            .with("apt-get", &["install", "-y", "steam-installer"], false, "");
        let steps = vec![Step::InstallNative {
            packages: vec!["steam-installer".to_string()],
            components: vec!["steam"],
        }];
        let mut seen: Vec<String> = Vec::new();
        let report = execute_steps(&runner, &pm, &steps, &mut |step| {
            seen.push(step.step.clone())
        });
        assert!(!report.ok);
        assert_eq!(seen, vec!["install_packages".to_string()]);
        let failed = &report.steps[0];
        assert!(!failed.ok);
        assert_eq!(failed.step, "install_packages");
        assert_eq!(failed.components, vec!["steam".to_string()]);
        assert!(failed.operation.starts_with("apt-get install -y"));
        assert!(
            report.missing_packages.is_empty(),
            "verification does not run after a failed step"
        );
    }

    #[test]
    fn apt_resolver_guard_rejects_a_failed_simulation() {
        let pm = PackageManager::new(crate::packages::PackageManagerKind::Apt);
        let runner =
            MockRunner::default().with("apt-get", &["-s", "install", "steam-installer"], false, "");
        let error = resolver_guard(&runner, &pm, &["steam-installer".to_string()]).unwrap_err();
        assert_eq!(error, "resolver_guard_failed:1");
    }

    #[test]
    fn resolver_guard_runs_at_install_time_not_before_addi386_exists() {
        // Regression test for the real Debian 13 finding: on a genuinely
        // fresh machine `apt-get -s install steam-installer` fails
        // (exit 100) until i386 is really added and really refreshed,
        // because steam-installer depends on an i386 package. Guarding the
        // whole plan *before* AddI386/Refresh ran made every fresh install
        // fail immediately. The guard must instead run once execution
        // reaches the real InstallNative step, so it never blocks a plan
        // whose own earlier steps are what make the simulation resolvable.
        let pm = PackageManager::new(crate::packages::PackageManagerKind::Apt);
        let runner = MockRunner::default().with(
            "apt-get",
            &["-s", "install", "steam-installer"],
            false,
            "E: Unable to correct problems, you have held broken packages.\n",
        );
        let step = Step::InstallNative {
            packages: vec!["steam-installer".to_string()],
            components: vec!["steam"],
        };
        let report = run_one_step(&runner, &pm, &step);
        assert!(
            !report.ok,
            "a failing simulation must not be treated as success"
        );
        assert_eq!(report.exit_code, -1);
        assert!(report.stderr_tail.starts_with("resolver_guard_failed:"));
    }

    #[test]
    fn an_unavailable_bundle_never_installs_only_its_available_subset() {
        const PACKAGES: &[&str] = &["available-half", "missing-half"];
        let pm = PackageManager::new(crate::packages::PackageManagerKind::Apt);
        let runner = MockRunner::default()
            .with(
                "apt-cache",
                &["policy", "available-half"],
                true,
                "Candidate: 1.0\n",
            )
            .with(
                "apt-cache",
                &["policy", "missing-half"],
                true,
                "Candidate: (none)\n",
            );
        let resolver = Resolver {
            pm: &pm,
            runner: &runner,
            thirty_two_bit: None,
            rpmfusion_pending: false,
            arch_steam_needs_multilib: false,
        };
        let mut resolved = Resolved::default();
        let mut warnings = Vec::new();
        native_components(
            &resolver,
            &mut resolved,
            &mut warnings,
            &[("bundle", "Bundle", PACKAGES)],
        );
        assert_eq!(resolved.components[0].state, "unavailable");
        assert!(resolved.steps.is_empty());
    }

    #[test]
    fn shared_flatpak_prerequisites_are_planned_once() {
        let runner = MockRunner::default()
            .with("flatpak", &["--version"], false, "")
            .with("flatpak", &["info", HEROIC_APP], false, "")
            .with("flatpak", &["info", PROTONTRICKS_APP], false, "");
        let mut resolved = Resolved::default();
        push_flatpak_component(&runner, &mut resolved, HEROIC_TARGET);
        push_flatpak_component(&runner, &mut resolved, PROTONTRICKS_TARGET);
        assert_eq!(
            resolved
                .components
                .iter()
                .filter(|component| component.id == "flatpak")
                .count(),
            1
        );
        assert_eq!(
            resolved
                .steps
                .iter()
                .filter(|step| matches!(step, Step::FlatpakRemote { name, .. } if *name == FLATHUB_REMOTE))
                .count(),
            1
        );
    }

    #[test]
    fn a_step_that_lies_about_success_is_caught_by_the_real_verification() {
        // The package manager exits 0 but the package is not really there:
        // the report must say so instead of claiming success.
        let pm = PackageManager::new(crate::packages::PackageManagerKind::Apt);
        let runner = MockRunner::default()
            .with(
                "apt-get",
                &["-s", "install", "steam-installer"],
                true,
                "Inst steam-installer\n",
            )
            .with(
                "apt-get",
                &["install", "-y", "steam-installer"],
                true,
                "done",
            )
            .with(
                "dpkg-query",
                &["-W", "-f=${Status}", "steam-installer"],
                true,
                "deinstall ok config-files",
            );
        let steps = vec![Step::InstallNative {
            packages: vec!["steam-installer".to_string()],
            components: vec!["steam"],
        }];
        let report = execute_steps(&runner, &pm, &steps, &mut |_| {});
        assert!(!report.ok);
        assert_eq!(report.missing_packages, vec!["steam-installer".to_string()]);
        assert!(report.steps[0].ok, "the step itself ran fine");
    }

    #[test]
    fn a_noisy_posttrans_scriptlet_failure_is_rescued_by_the_real_verification() {
        // Real container finding: `dnf install -y ... wine ...` exits 1
        // purely because of an `alternatives`/%posttrans scriptlet warning,
        // even though every requested package really is installed
        // afterwards. Exit codes alone must never turn a successful
        // installation into a reported failure.
        let pm = PackageManager::new(crate::packages::PackageManagerKind::Dnf);
        let runner = MockRunner::default()
            .with("dnf", &["install", "-y", "wine"], false, "")
            .with("rpm", &["-q", "wine"], true, "wine-11.0-3.fc44");
        let steps = vec![Step::InstallNative {
            packages: vec!["wine".to_string()],
            components: vec!["wine"],
        }];
        let report = execute_steps(&runner, &pm, &steps, &mut |_| {});
        assert!(
            report.ok,
            "verification must rescue a step whose packages are all really installed"
        );
        assert!(report.steps[0].ok);
        assert!(report.missing_packages.is_empty());
    }

    #[test]
    fn a_step_that_really_fails_stays_failed_even_after_verification() {
        // The mirror case: nothing was mocked as installed, so the rescue
        // check must not paper over a genuine failure.
        let pm = PackageManager::new(crate::packages::PackageManagerKind::Dnf);
        let runner = MockRunner::default().with("dnf", &["install", "-y", "wine"], false, "");
        let steps = vec![Step::InstallNative {
            packages: vec!["wine".to_string()],
            components: vec!["wine"],
        }];
        let report = execute_steps(&runner, &pm, &steps, &mut |_| {});
        assert!(!report.ok);
        assert!(!report.steps[0].ok);
    }

    #[test]
    fn a_flatpak_step_is_verified_through_flatpak_info() {
        let pm = PackageManager::new(crate::packages::PackageManagerKind::Apt);
        let runner = MockRunner::default()
            .with(
                "flatpak",
                &["install", "--system", "-y", FLATHUB_REMOTE, HEROIC_APP],
                true,
                "installed",
            )
            .with("flatpak", &["info", HEROIC_APP], false, "");
        let steps = vec![Step::InstallFlatpak {
            remote: FLATHUB_REMOTE,
            app: HEROIC_APP,
            component: "heroic",
        }];
        let report = execute_steps(&runner, &pm, &steps, &mut |_| {});
        assert!(!report.ok);
        assert_eq!(report.missing_packages, vec![HEROIC_APP.to_string()]);
    }

    #[test]
    fn a_fully_successful_plan_reports_ok_with_no_missing_packages() {
        let pm = PackageManager::new(crate::packages::PackageManagerKind::Apt);
        let runner = MockRunner::default()
            .with(
                "apt-get",
                &["-s", "install", "lutris"],
                true,
                "Inst lutris\n",
            )
            .with("apt-get", &["install", "-y", "lutris"], true, "")
            .with(
                "dpkg-query",
                &["-W", "-f=${Status}", "lutris"],
                true,
                "install ok installed",
            );
        let steps = vec![Step::InstallNative {
            packages: vec!["lutris".to_string()],
            components: vec!["lutris"],
        }];
        let report = execute_steps(&runner, &pm, &steps, &mut |_| {});
        assert!(report.ok);
        assert!(report.missing_packages.is_empty());
        assert_eq!(report.steps.len(), 1);
    }

    #[test]
    fn the_busy_list_ignores_the_always_on_packagekit_daemon() {
        // Regression test: a permanently running `packagekitd` must never
        // make every install fail its preflight check.
        assert!(!BUSY_PROCESSES.contains(&"packagekitd"));
        assert!(BUSY_PROCESSES.contains(&"dpkg"));
        assert!(BUSY_PROCESSES.contains(&"apt-get"));
        assert!(BUSY_PROCESSES.contains(&"dnf5"));
    }

    #[test]
    fn the_operation_allowlist_is_closed() {
        assert!(is_operation("programs-prepare-gaming"));
        assert!(is_operation("programs-install-geforce-now"));
        assert!(!is_operation("pacman -S steam"));
        assert!(!is_operation("shell"));
        match apply_operation(&["apt-get install -y steam".to_string()], &mut |_| {}) {
            Err(reason) => assert_eq!(reason, "invalid_operation"),
            Ok(_) => panic!("a raw command must never be accepted"),
        }
    }
}
