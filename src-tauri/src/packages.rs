//! Small, reusable package-manager layer for the Programs page.
//!
//! It knows the four native managers this iteration supports (APT, DNF,
//! Pacman, Zypper) and offers the operations the Gaming module needs:
//! `is_installed`, `installed_version`, `installed_matching`,
//! `availability` and the argv templates used for install/refresh. It never
//! builds a shell string and every command is executed as `program` plus
//! separate arguments, so nothing in a package name can ever be interpreted
//! as shell syntax.
//!
//! Read-only queries run directly as the normal user. Mutating commands are
//! only ever built here and executed by the privileged programs helper,
//! which re-derives them itself -- the frontend never names a command.
use crate::distro::DistroFamily;
use std::{
    collections::{BTreeMap, BTreeSet},
    process::{Command, Stdio},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PackageManagerKind {
    Apt,
    Dnf,
    Pacman,
    Zypper,
}

impl PackageManagerKind {
    pub fn name(self) -> &'static str {
        match self {
            PackageManagerKind::Apt => "apt",
            PackageManagerKind::Dnf => "dnf",
            PackageManagerKind::Pacman => "pacman",
            PackageManagerKind::Zypper => "zypper",
        }
    }

    /// Candidate executable names for this manager, tried in order at
    /// detection time. Fedora now ships `dnf5` as the real package manager;
    /// most releases still install a `dnf` compatibility symlink, but a
    /// minimal system can have `dnf5` only. Both are checked so a real,
    /// working package manager is never reported as "missing" just because
    /// the legacy name is absent.
    pub fn binary_candidates(self) -> &'static [&'static str] {
        match self {
            PackageManagerKind::Apt => &["apt-get"],
            PackageManagerKind::Dnf => &["dnf", "dnf5"],
            PackageManagerKind::Pacman => &["pacman"],
            PackageManagerKind::Zypper => &["zypper"],
        }
    }

    /// The package manager a distribution family is expected to use. The
    /// binary itself is still probed before anything is offered, so a family
    /// whose expected tool is missing is reported as unsupported instead of
    /// being assumed.
    pub fn for_family(family: DistroFamily) -> Self {
        match family {
            DistroFamily::Arch => PackageManagerKind::Pacman,
            DistroFamily::Fedora => PackageManagerKind::Dnf,
            DistroFamily::Debian | DistroFamily::Ubuntu => PackageManagerKind::Apt,
            DistroFamily::Suse => PackageManagerKind::Zypper,
        }
    }

    /// Install argv (without the package list): non-destructive by design --
    /// no `--allowerasing`, no automatic removal, no forced downgrade.
    /// `binary` is the executable actually detected on this machine (see
    /// [`PackageManagerKind::binary_candidates`]), so a `dnf5`-only system
    /// gets `dnf5 install -y ...` instead of a hardcoded, absent `dnf`.
    pub fn install_prefix(self, binary: &'static str) -> Vec<&'static str> {
        match self {
            PackageManagerKind::Apt => vec!["apt-get", "install", "-y"],
            PackageManagerKind::Dnf => vec![binary, "install", "-y"],
            PackageManagerKind::Pacman => vec!["pacman", "-S", "--noconfirm", "--needed"],
            PackageManagerKind::Zypper => vec!["zypper", "--non-interactive", "install"],
        }
    }

    pub fn refresh_argv(self, binary: &'static str) -> Vec<&'static str> {
        match self {
            PackageManagerKind::Apt => vec!["apt-get", "update"],
            PackageManagerKind::Dnf => vec![binary, "-y", "makecache"],
            PackageManagerKind::Pacman => vec!["pacman", "-Sy", "--noconfirm"],
            PackageManagerKind::Zypper => vec!["zypper", "--non-interactive", "refresh"],
        }
    }

    /// Dry-run argv used as a resolver guard before an APT transaction:
    /// `apt-get -s install` reports what would happen without changing
    /// anything, so a plan that would remove the graphics stack can be
    /// aborted before it runs.
    pub fn simulate_install_argv(self, packages: &[String]) -> Option<Vec<String>> {
        match self {
            PackageManagerKind::Apt => {
                let mut argv = vec![
                    "apt-get".to_string(),
                    "-s".to_string(),
                    "install".to_string(),
                ];
                argv.extend(packages.iter().cloned());
                Some(argv)
            }
            _ => None,
        }
    }

    /// `true` when a grep-style pattern would be removed by an APT simulation
    /// output: only lines that actually remove (`Remv `) or downgrade
    /// (`Inst ... (downgrade)`) are considered, and only for graphics-stack
    /// package names, so an unrelated `Conf`/`Inst` line never aborts.
    pub fn simulation_removes_graphics(simulation: &str) -> bool {
        const GRAPHICS: &[&str] = &[
            "nvidia",
            "mesa",
            "libgl",
            "vulkan",
            "lib32-",
            "xserver-xorg-video",
        ];
        simulation.lines().any(|line| {
            let removal = line.starts_with("Remv ");
            let downgrade = line.starts_with("Inst ") && line.contains("(downgrade)");
            (removal || downgrade)
                && GRAPHICS
                    .iter()
                    .any(|name| line.to_ascii_lowercase().contains(name))
        })
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CommandOutput {
    pub success: bool,
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}

pub trait CommandRunner {
    fn run(&self, program: &str, args: &[&str]) -> Option<CommandOutput>;
}

/// Real execution: never a shell, `LC_ALL=C` for stable parsing.
pub struct SystemRunner;

impl CommandRunner for SystemRunner {
    fn run(&self, program: &str, args: &[&str]) -> Option<CommandOutput> {
        let output = Command::new(program)
            .args(args)
            .env("LC_ALL", "C")
            .stdin(Stdio::null())
            .output()
            .ok()?;
        Some(CommandOutput {
            success: output.status.success(),
            exit_code: output.status.code().unwrap_or(-1),
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Availability {
    Installed,
    Available,
    Missing,
    /// The check itself could not run (no metadata cache, tool failure): the
    /// UI shows this as "to be verified" instead of inventing an answer.
    Unknown,
}

#[derive(Clone, Debug)]
pub struct PackageManager {
    pub kind: PackageManagerKind,
    /// The executable actually found on this machine, e.g. `"dnf5"` on a
    /// system that only ships the modern DNF5 binary. Every command this
    /// manager builds uses this name instead of a hardcoded legacy one.
    pub binary: &'static str,
}

impl PackageManager {
    #[allow(dead_code)]
    pub fn new(kind: PackageManagerKind) -> Self {
        let binary = kind.binary_candidates().first().copied().unwrap_or("");
        Self { kind, binary }
    }

    /// Picks the manager for a family and verifies that a real binary
    /// exists before it is used for anything, trying every known candidate
    /// name (e.g. `dnf` then `dnf5`) so a modern, `dnf5`-only Fedora is
    /// never reported as having no package manager at all.
    pub fn detect(family: DistroFamily, runner: &dyn CommandRunner) -> Option<Self> {
        let kind = PackageManagerKind::for_family(family);
        for binary in kind.binary_candidates() {
            if runner
                .run(binary, &["--version"])
                .is_some_and(|output| output.success)
            {
                return Some(Self { kind, binary });
            }
        }
        None
    }

    pub fn is_installed(&self, runner: &dyn CommandRunner, package: &str) -> bool {
        match self.kind {
            PackageManagerKind::Apt => runner
                .run("dpkg-query", &["-W", "-f=${Status}", package])
                .is_some_and(|output| {
                    output.success && output.stdout.trim() == "install ok installed"
                }),
            PackageManagerKind::Dnf | PackageManagerKind::Zypper => runner
                .run("rpm", &["-q", package])
                .is_some_and(|output| output.success),
            PackageManagerKind::Pacman => runner
                .run("pacman", &["-Q", package])
                .is_some_and(|output| output.success),
        }
    }

    #[allow(dead_code)]
    pub fn installed_version(&self, runner: &dyn CommandRunner, package: &str) -> Option<String> {
        match self.kind {
            PackageManagerKind::Apt => {
                let output = runner.run("dpkg-query", &["-W", "-f=${Version}", package])?;
                (output.success && !output.stdout.trim().is_empty())
                    .then(|| output.stdout.trim().to_string())
            }
            PackageManagerKind::Dnf | PackageManagerKind::Zypper => {
                let format = "%{VERSION}";
                let output = runner.run("rpm", &["-q", "--qf", format, package])?;
                (output.success && !output.stdout.trim().is_empty())
                    .then(|| output.stdout.trim().to_string())
            }
            PackageManagerKind::Pacman => {
                let output = runner.run("pacman", &["-Q", package])?;
                output.success.then(|| {
                    output
                        .stdout
                        .split_whitespace()
                        .nth(1)
                        .unwrap_or_default()
                        .to_string()
                })
            }
        }
    }

    /// Every installed package whose name matches a simple `prefix*suffix`
    /// pattern, used to find the installed NVIDIA branch without guessing.
    pub fn installed_matching(&self, runner: &dyn CommandRunner, pattern: &str) -> Vec<String> {
        let names: Vec<String> = match self.kind {
            PackageManagerKind::Apt => runner
                .run("dpkg-query", &["-W", "-f=${Package}\n", pattern])
                .map(|output| {
                    output
                        .stdout
                        .lines()
                        .map(str::to_string)
                        .collect::<Vec<String>>()
                })
                .unwrap_or_default(),
            PackageManagerKind::Dnf | PackageManagerKind::Zypper => runner
                .run("rpm", &["-qa", "--qf", "%{NAME}\n", pattern])
                .map(|output| {
                    output
                        .stdout
                        .lines()
                        .map(str::to_string)
                        .collect::<Vec<String>>()
                })
                .unwrap_or_default(),
            PackageManagerKind::Pacman => runner
                .run("pacman", &["-Qq"])
                .map(|output| {
                    output
                        .stdout
                        .lines()
                        .map(str::to_string)
                        .collect::<Vec<String>>()
                })
                .unwrap_or_default()
                .into_iter()
                .filter(|name| glob_matches(pattern, name))
                .collect(),
        };
        let mut names: Vec<String> = names
            .into_iter()
            .map(|name| name.trim().to_string())
            .filter(|name| !name.is_empty())
            .collect();
        names.sort();
        names.dedup();
        names
    }

    /// Batch variant of [`PackageManager::is_installed`]: one query for many
    /// packages instead of one per package. Returns `None` when the batch
    /// cannot be used (unsupported manager, or the command could not run),
    /// so every caller keeps the per-package path with its exact semantics.
    ///
    /// Only implemented for APT so far: `dpkg-query` accepts a package list
    /// and prints one `<name> <status>` line per package it knows, which is
    /// unambiguous to parse. The other managers are measured on their own
    /// distributions before being batched, so a parsing guess can never turn
    /// an available app into a "not available" one.
    pub fn installed_many(
        &self,
        runner: &dyn CommandRunner,
        packages: &[&str],
    ) -> Option<BTreeMap<String, bool>> {
        if packages.is_empty() {
            return Some(BTreeMap::new());
        }
        match self.kind {
            PackageManagerKind::Apt => {
                let mut argv: Vec<&str> = vec!["-W", "-f=${Package} ${Status}\n"];
                argv.extend_from_slice(packages);
                let output = runner.run("dpkg-query", &argv)?;
                let requested: BTreeSet<&str> = packages.iter().copied().collect();
                let mut found: BTreeMap<String, bool> =
                    packages.iter().map(|p| ((*p).to_string(), false)).collect();
                for line in output.stdout.lines() {
                    let Some((name, status)) = line.split_once(' ') else {
                        continue;
                    };
                    if !requested.contains(name) {
                        continue;
                    }
                    found.insert(name.to_string(), status.trim() == "install ok installed");
                }
                Some(found)
            }
            _ => None,
        }
    }

    /// Batch variant of the *repository* half of [`PackageManager::availability`]:
    /// it never reports `Installed`, because the caller already knows the
    /// installed state and combines the two exactly like `availability` does.
    /// Returns `None` when the batch cannot be used, so the caller falls back
    /// to the per-package query.
    pub fn repo_availability_many(
        &self,
        runner: &dyn CommandRunner,
        packages: &[&str],
    ) -> Option<BTreeMap<String, Availability>> {
        if packages.is_empty() {
            return Some(BTreeMap::new());
        }
        match self.kind {
            PackageManagerKind::Apt => {
                // `apt-cache policy p1 p2 ...` costs the same as a single
                // package (the cost is loading the apt cache, measured at
                // ~0.58s either way) and prints one `<name>:` block per
                // package it knows; a package it does not know is simply
                // absent, which means "not in any repository" -- the same
                // answer the single-package call gives.
                let mut argv: Vec<&str> = vec!["policy"];
                argv.extend_from_slice(packages);
                let output = runner.run("apt-cache", &argv)?;
                if !output.success {
                    return None;
                }
                let requested: BTreeSet<&str> = packages.iter().copied().collect();
                let mut found: BTreeMap<String, Availability> = BTreeMap::new();
                let mut current: Option<String> = None;
                for line in output.stdout.lines() {
                    if !line.starts_with([' ', '\t']) {
                        current = line
                            .strip_suffix(':')
                            .filter(|name| requested.contains(name))
                            .map(str::to_string);
                        if let Some(name) = &current {
                            // A block with no Candidate line at all means the
                            // package is known but has no installable version.
                            found.insert(name.clone(), Availability::Missing);
                        }
                        continue;
                    }
                    if let (Some(name), Some(candidate)) =
                        (current.as_ref(), line.trim().strip_prefix("Candidate:"))
                    {
                        let value = if candidate.trim() == "(none)" {
                            Availability::Missing
                        } else {
                            Availability::Available
                        };
                        found.insert(name.clone(), value);
                    }
                }
                for package in packages {
                    found
                        .entry((*package).to_string())
                        .or_insert(Availability::Missing);
                }
                Some(found)
            }
            _ => None,
        }
    }

    pub fn availability(&self, runner: &dyn CommandRunner, package: &str) -> Availability {
        if self.is_installed(runner, package) {
            return Availability::Installed;
        }
        match self.kind {
            PackageManagerKind::Apt => {
                let Some(output) = runner.run("apt-cache", &["policy", package]) else {
                    return Availability::Unknown;
                };
                if !output.success {
                    return Availability::Unknown;
                }
                let candidate = output
                    .stdout
                    .lines()
                    .find_map(|line| line.trim().strip_prefix("Candidate:"));
                match candidate.map(str::trim) {
                    Some("(none)") | None => Availability::Missing,
                    Some(_) => Availability::Available,
                }
            }
            PackageManagerKind::Pacman => {
                match runner.run("pacman", &["-Si", package]) {
                    Some(output) if output.success && !output.stdout.trim().is_empty() => {
                        Availability::Available
                    }
                    // `pacman -Si` failing with a clean "package not found"
                    // is definitive: the package is not in any sync database.
                    Some(output) if output.stdout.is_empty() => Availability::Missing,
                    Some(_) => Availability::Missing,
                    None => Availability::Unknown,
                }
            }
            PackageManagerKind::Dnf => {
                let cached = runner.run(
                    self.binary,
                    &[
                        "-q",
                        "--cacheonly",
                        "repoquery",
                        "--available",
                        "--qf",
                        "%{name}",
                        package,
                    ],
                );
                if let Some(output) = cached {
                    if output.success && !output.stdout.trim().is_empty() {
                        return Availability::Available;
                    }
                }
                match runner.run(
                    self.binary,
                    &["-q", "repoquery", "--available", "--qf", "%{name}", package],
                ) {
                    Some(output) if output.success && !output.stdout.trim().is_empty() => {
                        Availability::Available
                    }
                    Some(output) if output.success => Availability::Missing,
                    _ => Availability::Unknown,
                }
            }
            PackageManagerKind::Zypper => {
                match runner.run(
                    "zypper",
                    &[
                        "--non-interactive",
                        "--quiet",
                        "search",
                        "--match-exact",
                        "--type",
                        "package",
                        package,
                    ],
                ) {
                    Some(output) if output.success => {
                        let found = output.stdout.lines().any(|line| {
                            line.split('|')
                                .nth(1)
                                .map(str::trim)
                                .is_some_and(|name| name == package)
                                || line
                                    .split_whitespace()
                                    .next()
                                    .is_some_and(|name| name == package)
                        });
                        if found {
                            Availability::Available
                        } else {
                            Availability::Missing
                        }
                    }
                    _ => Availability::Unknown,
                }
            }
        }
    }
}

/// Minimal `prefix*suffix` matcher (at most one `*`), enough for the
/// package-name patterns this module uses and small enough to test.
pub fn glob_matches(pattern: &str, value: &str) -> bool {
    match pattern.split_once('*') {
        None => pattern == value,
        Some((prefix, suffix)) => {
            value.len() >= prefix.len() + suffix.len()
                && value.starts_with(prefix)
                && value.ends_with(suffix)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[derive(Default)]
    struct MockRunner {
        outputs: HashMap<String, CommandOutput>,
    }

    impl MockRunner {
        fn with(mut self, program: &str, args: &[&str], success: bool, stdout: &str) -> Self {
            let key = format!("{program}\u{0}{}", args.join("\u{0}"));
            self.outputs.insert(
                key,
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

    #[test]
    fn maps_each_family_to_its_native_manager() {
        assert_eq!(
            PackageManagerKind::for_family(DistroFamily::Arch),
            PackageManagerKind::Pacman
        );
        assert_eq!(
            PackageManagerKind::for_family(DistroFamily::Fedora),
            PackageManagerKind::Dnf
        );
        assert_eq!(
            PackageManagerKind::for_family(DistroFamily::Ubuntu),
            PackageManagerKind::Apt
        );
        assert_eq!(
            PackageManagerKind::for_family(DistroFamily::Debian),
            PackageManagerKind::Apt
        );
        assert_eq!(
            PackageManagerKind::for_family(DistroFamily::Suse),
            PackageManagerKind::Zypper
        );
    }

    #[test]
    fn detect_requires_the_real_binary_to_be_present() {
        let present = MockRunner::default().with("dnf", &["--version"], true, "4.20");
        assert!(PackageManager::detect(DistroFamily::Fedora, &present).is_some());
        let absent = MockRunner::default().with("dnf", &["--version"], false, "");
        assert!(PackageManager::detect(DistroFamily::Fedora, &absent).is_none());
        let missing = MockRunner::default();
        assert!(PackageManager::detect(DistroFamily::Arch, &missing).is_none());
    }

    #[test]
    fn apt_installed_detection_uses_the_real_dpkg_status_string() {
        let manager = PackageManager::new(PackageManagerKind::Apt);
        let installed = MockRunner::default().with(
            "dpkg-query",
            &["-W", "-f=${Status}", "steam-installer"],
            true,
            "install ok installed",
        );
        assert!(manager.is_installed(&installed, "steam-installer"));
        let removed = MockRunner::default().with(
            "dpkg-query",
            &["-W", "-f=${Status}", "steam-installer"],
            true,
            "deinstall ok installed",
        );
        assert!(!manager.is_installed(&removed, "steam-installer"));
    }

    #[test]
    fn apt_availability_reads_the_candidate_field() {
        let manager = PackageManager::new(PackageManagerKind::Apt);
        let available = MockRunner::default().with(
            "apt-cache",
            &["policy", "lutris"],
            true,
            "lutris:\n  Installed: (none)\n  Candidate: 0.5.18-1\n",
        );
        assert_eq!(
            manager.availability(&available, "lutris"),
            Availability::Available
        );
        let missing = MockRunner::default().with(
            "apt-cache",
            &["policy", "does-not-exist"],
            true,
            "does-not-exist:\n  Installed: (none)\n  Candidate: (none)\n",
        );
        assert_eq!(
            manager.availability(&missing, "does-not-exist"),
            Availability::Missing
        );
        let broken = MockRunner::default().with("apt-cache", &["policy", "x"], false, "");
        assert_eq!(manager.availability(&broken, "x"), Availability::Unknown);
    }

    #[test]
    fn pacman_availability_treats_a_clean_missing_package_as_missing_not_unknown() {
        let manager = PackageManager::new(PackageManagerKind::Pacman);
        let available =
            MockRunner::default().with("pacman", &["-Si", "wine"], true, "Name : wine\n");
        assert_eq!(
            manager.availability(&available, "wine"),
            Availability::Available
        );
        let missing = MockRunner::default().with(
            "pacman",
            &["-Si", "ghost-package"],
            false,
            "error: package 'ghost-package' was not found\n",
        );
        assert_eq!(
            manager.availability(&missing, "ghost-package"),
            Availability::Missing
        );
        let no_tool = MockRunner::default();
        assert_eq!(
            manager.availability(&no_tool, "wine"),
            Availability::Unknown
        );
    }

    #[test]
    fn pending_unattended_upgrades_are_not_mistaken_for_installed() {
        // A package known to dpkg but not installed must never be treated as
        // present: the plan would then skip a real installation.
        let manager = PackageManager::new(PackageManagerKind::Apt);
        let runner = MockRunner::default().with(
            "dpkg-query",
            &["-W", "-f=${Status}", "wine"],
            true,
            "install ok config-files",
        );
        assert!(!manager.is_installed(&runner, "wine"));
    }

    #[test]
    fn zypper_availability_reads_the_package_column() {
        let manager = PackageManager::new(PackageManagerKind::Zypper);
        let runner = MockRunner::default()
            .with("rpm", &["-q", "lutris"], false, "")
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
                "S | Name   | Summary | Type    | Version | Arch   | Repository\n  | lutris | Games   | package | 0.5.18  | noarch | games\n",
            );
        assert_eq!(
            manager.availability(&runner, "lutris"),
            Availability::Available
        );
    }

    #[test]
    fn installed_matching_finds_the_nvidia_branch_without_guessing() {
        let manager = PackageManager::new(PackageManagerKind::Apt);
        let runner = MockRunner::default().with(
            "dpkg-query",
            &["-W", "-f=${Package}\n", "libnvidia-gl-*:amd64"],
            true,
            "libnvidia-gl-550:amd64\nlibnvidia-gl-590:amd64\n",
        );
        let found = manager.installed_matching(&runner, "libnvidia-gl-*:amd64");
        assert_eq!(
            found,
            vec![
                "libnvidia-gl-550:amd64".to_string(),
                "libnvidia-gl-590:amd64".to_string()
            ]
        );
    }

    #[test]
    fn pacman_installed_matching_uses_the_glob_matcher() {
        let manager = PackageManager::new(PackageManagerKind::Pacman);
        let runner = MockRunner::default().with(
            "pacman",
            &["-Qq"],
            true,
            "nvidia-utils\nnvidia-settings\nwine\n",
        );
        assert_eq!(
            manager.installed_matching(&runner, "nvidia-*"),
            vec!["nvidia-settings".to_string(), "nvidia-utils".to_string()]
        );
        assert!(glob_matches("lib32-*", "lib32-mesa"));
        assert!(!glob_matches("lib32-*", "mesa"));
        assert!(glob_matches("exact", "exact"));
    }

    #[test]
    fn install_and_refresh_argv_never_force_removal_or_downgrade() {
        for kind in [
            PackageManagerKind::Apt,
            PackageManagerKind::Dnf,
            PackageManagerKind::Pacman,
            PackageManagerKind::Zypper,
        ] {
            let binary = kind.binary_candidates()[0];
            let argv = kind.install_prefix(binary).join(" ");
            for forbidden in ["--allowerasing", "-R", "remove", "--force", "downgrade"] {
                assert!(
                    !argv.contains(forbidden),
                    "{} install must not contain {forbidden}",
                    kind.name()
                );
            }
            assert!(!kind.refresh_argv(binary).is_empty());
        }
    }

    #[test]
    fn dnf5_only_systems_are_still_detected_and_used_for_every_command() {
        // Regression test for the real container finding: a modern Fedora
        // that only ships `dnf5` (no `dnf` compatibility symlink) must still
        // be detected, and every generated command must call `dnf5`, never
        // the absent legacy name.
        let runner = MockRunner::default()
            .with("dnf", &["--version"], false, "")
            .with("dnf5", &["--version"], true, "dnf5 version 5.4.4.0");
        let pm = PackageManager::detect(DistroFamily::Fedora, &runner)
            .expect("dnf5-only Fedora must still be detected");
        assert_eq!(pm.binary, "dnf5");
        assert_eq!(pm.kind.install_prefix(pm.binary), ["dnf5", "install", "-y"]);
        assert_eq!(pm.kind.refresh_argv(pm.binary), ["dnf5", "-y", "makecache"]);
    }

    #[test]
    fn simulation_guard_flags_graphics_removals_and_downgrades_only() {
        let safe = "Inst lutris (0.5.18 stable)\nConf wine\n";
        assert!(!PackageManagerKind::simulation_removes_graphics(safe));
        let removing_driver = "Remv libnvidia-gl-550 [550.1]\n";
        assert!(PackageManagerKind::simulation_removes_graphics(
            removing_driver
        ));
        let downgrading_mesa = "Inst mesa-vulkan-drivers [25.1] (25.0 (downgrade))\n";
        assert!(PackageManagerKind::simulation_removes_graphics(
            downgrading_mesa
        ));
        let removing_unrelated = "Remv old-print-driver [1.0]\n";
        assert!(!PackageManagerKind::simulation_removes_graphics(
            removing_unrelated
        ));
    }

    #[test]
    fn apt_batch_availability_parses_one_block_per_package_and_treats_absent_as_missing() {
        // Real `apt-cache policy p1 p2 ...` shape (LC_ALL=C, as SystemRunner
        // forces): one "<name>:" block per known package, nothing at all for
        // a package apt does not know.
        let manager = PackageManager::new(PackageManagerKind::Apt);
        let stdout = concat!(
            "filelight:\n",
            "  Installed: (none)\n",
            "  Candidate: 4:26.08.1-0ubuntu1\n",
            "  Version table:\n",
            "     4:26.08.1-0ubuntu1 500\n",
            "git:\n",
            "  Installed: 1:2.55.0-1ubuntu1\n",
            "  Candidate: 1:2.55.0-1ubuntu1\n",
            "obsolete-pkg:\n",
            "  Installed: (none)\n",
            "  Candidate: (none)\n",
        );
        let runner = MockRunner::default().with(
            "apt-cache",
            &["policy", "filelight", "git", "obsolete-pkg", "ghost-pkg"],
            true,
            stdout,
        );
        let found = manager
            .repo_availability_many(&runner, &["filelight", "git", "obsolete-pkg", "ghost-pkg"])
            .expect("apt must support the batch");
        assert_eq!(found["filelight"], Availability::Available);
        assert_eq!(found["git"], Availability::Available);
        // "(none)" candidate and a package apt never mentioned both mean the
        // same thing the per-package call means: not in any repository.
        assert_eq!(found["obsolete-pkg"], Availability::Missing);
        assert_eq!(found["ghost-pkg"], Availability::Missing);
    }

    #[test]
    fn apt_batch_installed_reads_the_real_dpkg_status_string_for_each_package() {
        let manager = PackageManager::new(PackageManagerKind::Apt);
        let runner = MockRunner::default().with(
            "dpkg-query",
            &["-W", "-f=${Package} ${Status}\n", "git", "wine", "ghost"],
            true,
            "git install ok installed\nwine deinstall ok config-files\n",
        );
        let found = manager
            .installed_many(&runner, &["git", "wine", "ghost"])
            .expect("apt must support the batch");
        assert!(found["git"]);
        // A package known to dpkg but not installed, and one dpkg never
        // printed, must both read as "not installed".
        assert!(!found["wine"]);
        assert!(!found["ghost"]);
    }

    #[test]
    fn the_batch_is_declined_for_managers_it_was_not_verified_on_so_they_keep_the_old_path() {
        let runner = MockRunner::default();
        for kind in [
            PackageManagerKind::Pacman,
            PackageManagerKind::Zypper,
            PackageManagerKind::Dnf,
        ] {
            let manager = PackageManager::new(kind);
            assert!(
                manager.installed_many(&runner, &["git"]).is_none(),
                "{} must decline the batch until it is measured on its own distro",
                kind.name()
            );
            assert!(manager.repo_availability_many(&runner, &["git"]).is_none());
        }
    }

    #[test]
    fn a_batch_that_cannot_run_declines_instead_of_inventing_answers() {
        // No mock for the batch command: the runner returns None, and the
        // batch must report "no answer" so the caller falls back per package
        // instead of marking everything as missing.
        let manager = PackageManager::new(PackageManagerKind::Apt);
        let runner = MockRunner::default();
        assert!(manager.repo_availability_many(&runner, &["git"]).is_none());
        assert!(manager.installed_many(&runner, &["git"]).is_none());
        // An empty request never spawns anything at all.
        assert_eq!(
            manager.repo_availability_many(&runner, &[]).unwrap().len(),
            0
        );
    }

    #[test]
    fn simulate_argv_is_only_offered_for_apt() {
        let packages = vec!["lutris".to_string()];
        let apt = PackageManagerKind::Apt.simulate_install_argv(&packages);
        assert_eq!(
            apt,
            Some(vec![
                "apt-get".to_string(),
                "-s".to_string(),
                "install".to_string(),
                "lutris".to_string()
            ])
        );
        assert!(PackageManagerKind::Pacman
            .simulate_install_argv(&packages)
            .is_none());
    }
}
