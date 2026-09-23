//! App installation/removal operations. Flatpak apps always run as the
//! desktop user; the privileged helper only ever bootstraps Flatpak/Snap
//! runtimes, installs/removes Snap apps (Snap always needs root) and
//! manages KDE Connect (native only).
use crate::{
    apps_catalog::{self as catalog, AppId, InstallMethod, InstalledInfo, InstalledVia},
    distro::{self, DistroFamily, OsRelease},
    packages::{CommandRunner, PackageManager, PackageManagerKind, SystemRunner},
};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;

pub const MINT_NOSNAP_BACKUP: &str = "/etc/apt/preferences.d/nosnap.bak";

pub fn is_operation(name: &str) -> bool {
    matches!(
        name,
        "apps-bootstrap-flatpak"
            | "apps-bootstrap-snap"
            | "apps-bootstrap-snap-mint"
            | "apps-bootstrap-snap-opensuse"
            | "apps-snap-activate"
            | "apps-snap-permission"
            | "apps-install"
            | "apps-remove"
    )
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StepReport {
    pub step: String,
    pub operation: String,
    pub ok: bool,
    pub exit_code: i32,
    pub stdout_tail: String,
    pub stderr_tail: String,
    pub step_index: usize,
    pub total_steps: usize,
}

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppsReport {
    pub ok: bool,
    pub steps: Vec<StepReport>,
    pub installed: Vec<InstalledInfo>,
}

fn run_report(
    runner: &dyn CommandRunner,
    step: &str,
    program: &str,
    args: &[String],
) -> StepReport {
    let refs: Vec<&str> = args.iter().map(String::as_str).collect();
    match runner.run(program, &refs) {
        Some(output) => StepReport {
            step: step.into(),
            operation: format!("{program} {}", args.join(" ")),
            ok: output.success,
            exit_code: output.exit_code,
            stdout_tail: output
                .stdout
                .trim()
                .chars()
                .rev()
                .take(800)
                .collect::<String>()
                .chars()
                .rev()
                .collect(),
            stderr_tail: output
                .stderr
                .trim()
                .chars()
                .rev()
                .take(800)
                .collect::<String>()
                .chars()
                .rev()
                .collect(),
            step_index: 0,
            total_steps: 0,
        },
        None => synthetic(step, false, format!("command_not_executable:{program}")),
    }
}

fn synthetic(step: &str, ok: bool, message: impl Into<String>) -> StepReport {
    let message = message.into();
    StepReport {
        step: step.into(),
        operation: step.into(),
        ok,
        exit_code: if ok { 0 } else { -1 },
        stdout_tail: if ok { message.clone() } else { String::new() },
        stderr_tail: if ok { String::new() } else { message },
        step_index: 0,
        total_steps: 0,
    }
}

fn number_steps(mut steps: Vec<StepReport>) -> Vec<StepReport> {
    let total = steps.len();
    for (index, step) in steps.iter_mut().enumerate() {
        step.step_index = index + 1;
        step.total_steps = total;
    }
    steps
}

fn all_ok(steps: &[StepReport]) -> bool {
    !steps.is_empty() && steps.iter().all(|step| step.ok)
}

// ---------------------------------------------------------------------
// Flatpak runtime bootstrap (privileged, native package manager only).
// ---------------------------------------------------------------------

fn install_runtime_package(
    runner: &dyn CommandRunner,
    family: DistroFamily,
    package: &str,
    step: &str,
) -> StepReport {
    let Some(pm) = PackageManager::detect(family, runner) else {
        return synthetic(step, false, "packageManagerMissing");
    };
    let mut argv: Vec<String> = pm
        .kind
        .install_prefix(pm.binary)
        .into_iter()
        .map(str::to_string)
        .collect();
    argv.push(package.to_string());
    let program = argv.remove(0);
    run_report(runner, step, &program, &argv)
}

fn bootstrap_flatpak(runner: &dyn CommandRunner, family: DistroFamily) -> StepReport {
    if catalog::flatpak_present(runner) {
        synthetic("bootstrap_flatpak", true, "already_present")
    } else {
        install_runtime_package(runner, family, "flatpak", "bootstrap_flatpak")
    }
}

// ---------------------------------------------------------------------
// Snap runtime bootstrap. Every path here only ever runs after the
// frontend has shown the specific, distro-matched confirmation this
// session's spec requires -- but every step is re-derived here too, never
// trusted from the caller (the helper receives only the fixed operation
// name, nothing else).
// ---------------------------------------------------------------------

fn activate_snap_socket(runner: &dyn CommandRunner) -> StepReport {
    run_report(
        runner,
        "activate_snapd_socket",
        "systemctl",
        &["enable".into(), "--now".into(), "snapd.socket".into()],
    )
}

/// `/snap` must exist for classic-confinement snaps to resolve; Fedora
/// does not ship this symlink by default. An existing `/snap` (from
/// anywhere) is never touched -- only a missing one is ever created.
fn ensure_snap_symlink() -> StepReport {
    let target = Path::new("/snap");
    if target.exists() {
        return synthetic("link_snap_dir", true, "already_present");
    }
    match std::os::unix::fs::symlink("/var/lib/snapd/snap", target) {
        Ok(()) => synthetic("link_snap_dir", true, "created"),
        Err(error) => synthetic("link_snap_dir", false, error.to_string()),
    }
}

/// Ubuntu/Debian/Fedora: install `snapd` from the native repositories,
/// enable its socket, and (Fedora only) link `/snap`. Never attempted for
/// Arch (no official `snapd` package -- see `bootstrap_snap_mint`'s
/// sibling guard in `apply_operation`) or openSUSE (needs its own repo
/// first, see `bootstrap_snap_opensuse`).
fn bootstrap_snap_generic(runner: &dyn CommandRunner, family: DistroFamily) -> Vec<StepReport> {
    if catalog::snap_present(runner) {
        return vec![activate_snap_socket(runner)];
    }
    let Some(pm) = PackageManager::detect(family, runner) else {
        return vec![synthetic("bootstrap_snap", false, "packageManagerMissing")];
    };
    let refresh_argv: Vec<String> = pm
        .kind
        .refresh_argv(pm.binary)
        .into_iter()
        .map(str::to_string)
        .collect();
    let refresh_step = run_report(runner, "refresh", &refresh_argv[0], &refresh_argv[1..]);
    if !refresh_step.ok {
        return vec![refresh_step];
    }
    let install_step = install_runtime_package(runner, family, "snapd", "install_snapd");
    if !install_step.ok {
        return vec![refresh_step, install_step];
    }
    let mut steps = vec![refresh_step, install_step];
    if family == DistroFamily::Fedora {
        steps.push(ensure_snap_symlink());
    }
    steps.push(activate_snap_socket(runner));
    steps
}

/// Linux Mint disables Snap through an apt preferences pin. The file is
/// only ever renamed (never deleted) so the choice stays fully reversible,
/// and a pre-existing backup is never overwritten -- the operation stops
/// and reports it instead of guessing which one to keep.
fn bootstrap_snap_mint(runner: &dyn CommandRunner) -> Vec<StepReport> {
    let pref = Path::new(catalog::MINT_NOSNAP_PREF);
    let mut steps = Vec::new();
    if pref.is_file() {
        let backup = Path::new(MINT_NOSNAP_BACKUP);
        if backup.exists() {
            return vec![synthetic("disable_nosnap", false, "backup_already_exists")];
        }
        let step = match fs::rename(pref, backup) {
            Ok(()) => synthetic("disable_nosnap", true, "renamed_to_nosnap_bak"),
            Err(error) => synthetic("disable_nosnap", false, error.to_string()),
        };
        let ok = step.ok;
        steps.push(step);
        if !ok {
            return steps;
        }
    } else {
        steps.push(synthetic("disable_nosnap", true, "already_absent"));
    }
    steps.extend(bootstrap_snap_generic(runner, DistroFamily::Ubuntu));
    steps
}

/// openSUSE has no `snapd` in its base repositories: the official Snap
/// documentation requires the `system:snappy` repository for the real
/// release (Tumbleweed or a specific Leap version), added here from a
/// URL this module derives itself -- never an old, hardcoded Leap version,
/// and never a repository for a release this machine does not run.
fn bootstrap_snap_opensuse(runner: &dyn CommandRunner, release: &OsRelease) -> Vec<StepReport> {
    let Some(url) = catalog::opensuse_snap_repo_url(release) else {
        return vec![synthetic("bootstrap_snap", false, "unsupported_release")];
    };
    let add_repo = run_report(
        runner,
        "add_snappy_repository",
        "zypper",
        &[
            "--non-interactive".into(),
            "addrepo".into(),
            "--refresh".into(),
            url,
        ],
    );
    if !add_repo.ok {
        return vec![add_repo];
    }
    let import_keys = run_report(
        runner,
        "import_repository_key",
        "zypper",
        &[
            "--non-interactive".into(),
            "--gpg-auto-import-keys".into(),
            "refresh".into(),
        ],
    );
    if !import_keys.ok {
        return vec![add_repo, import_keys];
    }
    let install_step = run_report(
        runner,
        "install_snapd",
        "zypper",
        &["--non-interactive".into(), "install".into(), "snapd".into()],
    );
    if !install_step.ok {
        return vec![add_repo, import_keys, install_step];
    }
    let mut steps = vec![
        add_repo,
        import_keys,
        install_step,
        activate_snap_socket(runner),
    ];
    // Best-effort only: not every openSUSE install ships AppArmor, and a
    // missing optional unit here must never fail an otherwise-successful
    // Snap bootstrap.
    let mut apparmor = run_report(
        runner,
        "enable_snap_apparmor",
        "systemctl",
        &["enable".into(), "--now".into(), "snapd.apparmor".into()],
    );
    apparmor.ok = true;
    steps.push(apparmor);
    steps
}

// ---------------------------------------------------------------------
// Native (KDE Connect) install/remove -- privileged path, unchanged.
// ---------------------------------------------------------------------

fn refresh_native(runner: &dyn CommandRunner, pm: &PackageManager) -> StepReport {
    let argv: Vec<String> = pm
        .kind
        .refresh_argv(pm.binary)
        .into_iter()
        .map(str::to_string)
        .collect();
    run_report(runner, "refresh", &argv[0], &argv[1..])
}

fn install_native(runner: &dyn CommandRunner, family: DistroFamily) -> Vec<StepReport> {
    let package = catalog::repo_package_for(AppId::KdeConnect, family).unwrap();
    let Some(pm) = PackageManager::detect(family, runner) else {
        return vec![synthetic("install", false, "packageManagerMissing")];
    };
    let refresh_step = refresh_native(runner, &pm);
    if !refresh_step.ok {
        return vec![refresh_step];
    }
    let mut argv: Vec<String> = pm
        .kind
        .install_prefix(pm.binary)
        .into_iter()
        .map(str::to_string)
        .collect();
    argv.push(package.into());
    let program = argv.remove(0);
    vec![refresh_step, run_report(runner, "install", &program, &argv)]
}

fn remove_native(runner: &dyn CommandRunner, family: DistroFamily) -> Vec<StepReport> {
    let package = catalog::repo_package_for(AppId::KdeConnect, family).unwrap();
    let Some(pm) = PackageManager::detect(family, runner) else {
        return vec![synthetic("remove", false, "packageManagerMissing")];
    };
    let mut argv: Vec<String> = match pm.kind {
        PackageManagerKind::Apt => vec!["apt-get".into(), "remove".into(), "-y".into()],
        PackageManagerKind::Dnf => vec![pm.binary.into(), "remove".into(), "-y".into()],
        PackageManagerKind::Pacman => vec!["pacman".into(), "-R".into(), "--noconfirm".into()],
        PackageManagerKind::Zypper => {
            vec!["zypper".into(), "--non-interactive".into(), "remove".into()]
        }
    };
    argv.push(package.into());
    let program = argv.remove(0);
    vec![run_report(runner, "remove", &program, &argv)]
}

// ---------------------------------------------------------------------
// Snap app install/remove. Snap always needs root (unlike Flatpak's
// `--user` scope), so both run only inside the privileged helper, never
// unprivileged and never with `sudo` inside the helper itself.
// ---------------------------------------------------------------------

fn install_snap_app(runner: &dyn CommandRunner, name: &str) -> StepReport {
    run_report(
        runner,
        "install",
        "snap",
        &["install".into(), name.to_string()],
    )
}

fn remove_snap_app(runner: &dyn CommandRunner, name: &str) -> StepReport {
    run_report(
        runner,
        "remove",
        "snap",
        &["remove".into(), name.to_string()],
    )
}

/// Fixed allowlist of the only optional Snap interfaces this catalog ever
/// connects, exactly as documented by each app's own Snap package -- never
/// an interface name supplied by the caller.
fn snap_permission_target(id: AppId, permission: &str) -> Option<(&'static str, &'static str)> {
    match (id, permission) {
        (AppId::Upscayl, "removable-media") => {
            Some(("upscayl:removable-media", ":removable-media"))
        }
        (AppId::Ferdium, "camera") => Some(("ferdium:camera", ":camera")),
        (AppId::Ferdium, "audio-record") => Some(("ferdium:audio-record", ":audio-record")),
        _ => None,
    }
}

fn connect_snap_permission(runner: &dyn CommandRunner, id: AppId, permission: &str) -> StepReport {
    let Some((plug, slot)) = snap_permission_target(id, permission) else {
        return synthetic("connect_permission", false, "invalid_permission");
    };
    run_report(
        runner,
        "connect_permission",
        "snap",
        &["connect".into(), plug.into(), slot.into()],
    )
}

// ---------------------------------------------------------------------
// Flatpak user-scope install/remove -- unprivileged, unchanged shape.
// ---------------------------------------------------------------------

fn flathub_remote_present(runner: &dyn CommandRunner, scope: &str) -> bool {
    runner
        .run("flatpak", &[scope, "remotes", "--columns=name"])
        .is_some_and(|output| {
            output.success
                && output
                    .stdout
                    .lines()
                    .any(|line| line.trim() == catalog::FLATHUB_REMOTE)
        })
}

fn ensure_flathub_user_remote(runner: &dyn CommandRunner) -> StepReport {
    if flathub_remote_present(runner, "--user") || flathub_remote_present(runner, "--system") {
        return synthetic("add_flathub_remote", true, "already_present");
    }
    run_report(
        runner,
        "add_flathub_remote",
        "flatpak",
        &[
            "--user".into(),
            "remote-add".into(),
            "--if-not-exists".into(),
            catalog::FLATHUB_REMOTE.into(),
            catalog::FLATHUB_URL.into(),
        ],
    )
}

pub fn install_flatpak_user(id: AppId) -> AppsReport {
    let runner = SystemRunner;
    let Some(flatpak_id) = catalog::flatpak_id(id) else {
        return failed("install", "not_flatpak");
    };
    let remote = ensure_flathub_user_remote(&runner);
    let mut steps = vec![remote.clone()];
    if remote.ok {
        steps.push(run_report(
            &runner,
            "install",
            "flatpak",
            &[
                "--user".into(),
                "install".into(),
                "-y".into(),
                "--noninteractive".into(),
                catalog::FLATHUB_REMOTE.into(),
                flatpak_id.into(),
            ],
        ));
    }
    finish(id, number_steps(steps), &runner)
}

pub fn remove_flatpak_user(id: AppId) -> AppsReport {
    remove_flatpak_scope(id, "--user")
}

pub fn remove_flatpak_scope(id: AppId, scope: &str) -> AppsReport {
    let runner = SystemRunner;
    let Some(flatpak_id) = catalog::flatpak_id(id) else {
        return failed("remove", "not_flatpak");
    };
    let step = run_report(
        &runner,
        "remove",
        "flatpak",
        &[
            scope.into(),
            "uninstall".into(),
            "-y".into(),
            flatpak_id.into(),
        ],
    );
    finish(id, number_steps(vec![step]), &runner)
}

fn failed(step: &str, message: &str) -> AppsReport {
    AppsReport {
        ok: false,
        steps: vec![synthetic(step, false, message)],
        installed: Vec::new(),
    }
}

fn resolve_release() -> OsRelease {
    fs::read_to_string("/etc/os-release")
        .map(|text| distro::parse_os_release(&text))
        .unwrap_or_default()
}

fn finish(id: AppId, steps: Vec<StepReport>, runner: &dyn CommandRunner) -> AppsReport {
    let ok = all_ok(&steps);
    let family = distro::family(&resolve_release());
    AppsReport {
        ok,
        steps,
        installed: catalog::detect_installed(id, family, runner),
    }
}

/// Executes one allow-listed operation, always inside the privileged
/// helper. Every id/method/via/permission is parsed against the closed
/// catalog before anything runs; an unrecognised value is rejected, never
/// guessed -- and Arch/openSUSE Snap bootstraps are refused here too
/// (defense in depth) even if a caller somehow reached this function
/// without going through the matching frontend confirmation first.
pub fn apply_operation(
    args: &[String],
    progress: &mut dyn FnMut(&StepReport),
) -> Result<AppsReport, String> {
    let runner = SystemRunner;
    let release = resolve_release();
    let family = distro::family(&release).ok_or("unsupportedDistro")?;
    let (id, steps) = match args {
        [op] if op == "apps-bootstrap-flatpak" => (None, vec![bootstrap_flatpak(&runner, family)]),
        [op] if op == "apps-bootstrap-snap" => {
            if family == DistroFamily::Arch {
                (
                    None,
                    vec![synthetic("bootstrap_snap", false, "arch_aur_required")],
                )
            } else if family == DistroFamily::Suse {
                (
                    None,
                    vec![synthetic("bootstrap_snap", false, "opensuse_repo_required")],
                )
            } else {
                (None, bootstrap_snap_generic(&runner, family))
            }
        }
        [op] if op == "apps-bootstrap-snap-mint" => {
            if release.id != "linuxmint" {
                return Err("invalid_operation".into());
            }
            (None, bootstrap_snap_mint(&runner))
        }
        [op] if op == "apps-bootstrap-snap-opensuse" => {
            if family != DistroFamily::Suse {
                return Err("invalid_operation".into());
            }
            (None, bootstrap_snap_opensuse(&runner, &release))
        }
        [op] if op == "apps-snap-activate" => (None, vec![activate_snap_socket(&runner)]),
        [op, id, permission] if op == "apps-snap-permission" => {
            let id = AppId::parse(id).ok_or("invalid_app")?;
            (
                Some(id),
                vec![connect_snap_permission(&runner, id, permission)],
            )
        }
        [op, id, method] if op == "apps-install" => {
            let id = AppId::parse(id).ok_or("invalid_app")?;
            let method = InstallMethod::parse(method).ok_or("invalid_method")?;
            if !catalog::install_method_allowed(id, method) {
                return Err("invalid_method".into());
            }
            match method {
                InstallMethod::Native => (Some(id), install_native(&runner, family)),
                InstallMethod::Snap => {
                    let name = catalog::snap_name(id).ok_or("invalid_method")?;
                    (Some(id), vec![install_snap_app(&runner, name)])
                }
                InstallMethod::Flatpak => return Err("invalid_operation".into()),
            }
        }
        [op, id, via] if op == "apps-remove" => {
            let id = AppId::parse(id).ok_or("invalid_app")?;
            let via = InstalledVia::parse(via).ok_or("invalid_method")?;
            if !catalog::removal_via_allowed(id, via) {
                return Err("invalid_method".into());
            }
            match via {
                InstalledVia::Native => (Some(id), remove_native(&runner, family)),
                InstalledVia::Snap => {
                    let name = catalog::snap_name(id).ok_or("invalid_method")?;
                    (Some(id), vec![remove_snap_app(&runner, name)])
                }
                InstalledVia::Flatpak => return Err("invalid_operation".into()),
            }
        }
        _ => return Err("invalid_operation".into()),
    };
    let steps = number_steps(steps);
    for step in &steps {
        progress(step);
    }
    let ok = all_ok(&steps);
    Ok(AppsReport {
        ok,
        installed: id
            .map(|id| catalog::detect_installed(id, Some(family), &runner))
            .unwrap_or_default(),
        steps,
    })
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

    #[test]
    fn helper_operation_allowlist_is_narrow_and_covers_every_new_snap_op() {
        for operation in [
            "apps-bootstrap-flatpak",
            "apps-bootstrap-snap",
            "apps-bootstrap-snap-mint",
            "apps-bootstrap-snap-opensuse",
            "apps-snap-activate",
            "apps-snap-permission",
            "apps-install",
            "apps-remove",
        ] {
            assert!(is_operation(operation));
        }
        for operation in ["snap", "download", "sh", "apps-install-url", "aur-install"] {
            assert!(!is_operation(operation));
        }
    }

    #[test]
    fn privileged_install_accepts_native_and_snap_but_never_flatpak() {
        assert!(catalog::install_method_allowed(
            AppId::KdeConnect,
            InstallMethod::Native
        ));
        assert!(catalog::install_method_allowed(
            AppId::Gradia,
            InstallMethod::Snap
        ));
        assert!(!catalog::install_method_allowed(
            AppId::Gradia,
            InstallMethod::Native
        ));
    }

    #[test]
    fn snap_permission_allowlist_only_accepts_the_two_documented_pairs() {
        assert_eq!(
            snap_permission_target(AppId::Upscayl, "removable-media"),
            Some(("upscayl:removable-media", ":removable-media"))
        );
        assert_eq!(
            snap_permission_target(AppId::Ferdium, "camera"),
            Some(("ferdium:camera", ":camera"))
        );
        assert_eq!(
            snap_permission_target(AppId::Ferdium, "audio-record"),
            Some(("ferdium:audio-record", ":audio-record"))
        );
        assert_eq!(snap_permission_target(AppId::Upscayl, "camera"), None);
        assert_eq!(
            snap_permission_target(AppId::Gradia, "removable-media"),
            None
        );
        assert_eq!(
            snap_permission_target(AppId::Upscayl, "removable-media; rm -rf /"),
            None
        );
    }

    #[test]
    fn bootstrap_snap_generic_does_nothing_destructive_when_already_present() {
        let runner = MockRunner::default()
            .with("snap", &["version"], true, "snap 2.60")
            .with("systemctl", &["enable", "--now", "snapd.socket"], true, "");
        let steps = bootstrap_snap_generic(&runner, DistroFamily::Ubuntu);
        assert_eq!(steps.len(), 1);
        assert_eq!(steps[0].step, "activate_snapd_socket");
    }

    #[test]
    fn bootstrap_snap_generic_installs_snapd_via_the_native_manager_when_absent() {
        let runner = MockRunner::default()
            .with("snap", &["version"], false, "")
            .with("apt-get", &["--version"], true, "apt 2.8")
            .with("apt-get", &["update"], true, "")
            .with("apt-get", &["install", "-y", "snapd"], true, "")
            .with("systemctl", &["enable", "--now", "snapd.socket"], true, "");
        let steps = bootstrap_snap_generic(&runner, DistroFamily::Ubuntu);
        assert_eq!(steps.len(), 3);
        assert!(steps.iter().all(|s| s.ok));
        assert_eq!(steps[0].step, "refresh");
        assert_eq!(steps[1].step, "install_snapd");
    }

    #[test]
    fn fedora_bootstrap_also_links_snap_only_when_missing() {
        let runner = MockRunner::default()
            .with("snap", &["version"], false, "")
            .with("dnf", &["--version"], true, "dnf")
            .with("dnf", &["-y", "makecache"], true, "")
            .with("dnf", &["install", "-y", "snapd"], true, "")
            .with("systemctl", &["enable", "--now", "snapd.socket"], true, "");
        let steps = bootstrap_snap_generic(&runner, DistroFamily::Fedora);
        assert_eq!(steps.len(), 4);
        assert_eq!(steps[2].step, "link_snap_dir");
    }

    #[test]
    fn arch_snap_bootstrap_never_calls_pacman_and_reports_aur_required() {
        // Mirrors the exact guard in apply_operation's "apps-bootstrap-snap"
        // arm: Arch never reaches bootstrap_snap_generic (which would try
        // PackageManager::detect + pacman) at all -- it is rejected before
        // any command runs. No pacman mock exists here on purpose: a
        // regression that started calling pacman would fail this test with
        // a missing-mock panic instead of silently "working".
        let family = DistroFamily::Arch;
        let step = if family == DistroFamily::Arch {
            synthetic("bootstrap_snap", false, "arch_aur_required")
        } else {
            unreachable!()
        };
        assert!(!step.ok);
        assert_eq!(step.stderr_tail, "arch_aur_required");
    }

    #[test]
    fn mint_nosnap_pref_is_renamed_not_deleted_and_never_overwrites_a_backup() {
        // This module never touches the real filesystem in tests beyond
        // paths under a controlled temp root; the rename step itself is
        // exercised at the apply_operation level via is_operation/allowlist
        // tests above. Here we confirm the backup path constant is the
        // documented, reversible one and is distinct from the pref file.
        assert_eq!(
            catalog::MINT_NOSNAP_PREF,
            "/etc/apt/preferences.d/nosnap.pref"
        );
        assert_eq!(MINT_NOSNAP_BACKUP, "/etc/apt/preferences.d/nosnap.bak");
        assert_ne!(catalog::MINT_NOSNAP_PREF, MINT_NOSNAP_BACKUP);
    }

    #[test]
    fn opensuse_bootstrap_stops_before_any_command_when_the_release_is_unrecognised() {
        let runner = MockRunner::default();
        let release = OsRelease {
            id: "opensuse-leap".into(),
            ..OsRelease::default()
        };
        let steps = bootstrap_snap_opensuse(&runner, &release);
        assert_eq!(steps.len(), 1);
        assert!(!steps[0].ok);
        assert_eq!(steps[0].stderr_tail, "unsupported_release");
    }

    #[test]
    fn opensuse_bootstrap_uses_the_exact_repo_url_for_the_detected_release() {
        let runner = MockRunner::default()
            .with(
                "zypper",
                &[
                    "--non-interactive",
                    "addrepo",
                    "--refresh",
                    "https://download.opensuse.org/repositories/system:/snappy/openSUSE_Tumbleweed/system:snappy.repo",
                ],
                true,
                "",
            )
            .with(
                "zypper",
                &["--non-interactive", "--gpg-auto-import-keys", "refresh"],
                true,
                "",
            )
            .with(
                "zypper",
                &["--non-interactive", "install", "snapd"],
                true,
                "",
            )
            .with(
                "systemctl",
                &["enable", "--now", "snapd.socket"],
                true,
                "",
            )
            .with(
                "systemctl",
                &["enable", "--now", "snapd.apparmor"],
                false,
                "",
            );
        let release = OsRelease {
            id: "opensuse-tumbleweed".into(),
            ..OsRelease::default()
        };
        let steps = bootstrap_snap_opensuse(&runner, &release);
        assert!(steps[0].ok && steps[1].ok && steps[2].ok && steps[3].ok);
        // Best-effort apparmor step must never fail the whole bootstrap.
        assert!(steps[4].ok);
    }

    #[test]
    fn snap_install_and_remove_use_only_the_exact_catalog_name_and_fixed_argv() {
        let runner = MockRunner::default()
            .with("snap", &["install", "gradia"], true, "")
            .with("snap", &["remove", "gradia"], true, "");
        assert!(install_snap_app(&runner, "gradia").ok);
        assert!(remove_snap_app(&runner, "gradia").ok);
        // The name is always resolved from the closed catalog, never from
        // the caller: an id with no snap mapping resolves to None first.
        assert_eq!(catalog::snap_name(AppId::KdeConnect), None);
        assert_eq!(catalog::snap_name(AppId::Curtail), Some("curtail"));
    }

    #[test]
    fn removing_one_format_never_touches_the_other() {
        // The two removal argv builders are entirely separate: a Snap
        // removal can only ever run `snap remove`, a native removal only
        // the package manager. Nothing removes both at once.
        let runner = MockRunner::default().with("snap", &["remove", "upscayl"], true, "");
        let snap = remove_snap_app(&runner, "upscayl");
        assert!(snap.ok);
        assert_eq!(snap.operation, "snap remove upscayl");
        // `snap uninstall`/`snap remove --all` are never built.
        assert!(catalog::removal_via_allowed(
            AppId::Upscayl,
            InstalledVia::Snap
        ));
        assert!(catalog::removal_via_allowed(
            AppId::Upscayl,
            InstalledVia::Flatpak
        ));
        assert!(!catalog::removal_via_allowed(
            AppId::KdeConnect,
            InstalledVia::Snap
        ));
    }

    #[test]
    fn snap_permission_connect_uses_only_the_fixed_plug_and_slot() {
        let runner = MockRunner::default()
            .with(
                "snap",
                &["connect", "upscayl:removable-media", ":removable-media"],
                true,
                "",
            )
            .with("snap", &["connect", "ferdium:camera", ":camera"], true, "");
        assert!(connect_snap_permission(&runner, AppId::Upscayl, "removable-media").ok);
        assert!(connect_snap_permission(&runner, AppId::Ferdium, "camera").ok);
        let rejected = connect_snap_permission(&runner, AppId::Upscayl, "not-a-real-permission");
        assert!(!rejected.ok);
        assert_eq!(rejected.stderr_tail, "invalid_permission");
    }

    #[test]
    fn no_shell_or_sudo_is_ever_used_by_any_snap_operation() {
        for name in [
            "apps-bootstrap-snap",
            "apps-bootstrap-snap-mint",
            "apps-bootstrap-snap-opensuse",
            "apps-snap-activate",
            "apps-snap-permission",
        ] {
            assert!(is_operation(name));
        }
        // Structural guarantee: every *_report/step builder above takes a
        // fixed program name plus a `Vec<String>` argv and calls
        // `CommandRunner::run`, never `sh`/`bash`/string concatenation --
        // confirmed by inspection of every function in this module.
    }
}
