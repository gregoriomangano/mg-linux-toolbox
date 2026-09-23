//! WinBoat module for the Programs page.
//!
//! Mirrors the shape of `gaming.rs`: one place owns detection (read-only,
//! unprivileged, used for the card/checklist), the plan/step execution
//! (privileged, used by the WinBoat helper) and every package name this
//! iteration supports. Compiled into both the application and the helper,
//! exactly like `gaming.rs`/`apt_ops.rs`.
//!
//! Deliberately out of scope: libvirt/virt-manager/qemu-full/bridge
//! networking (WinBoat only needs `/dev/kvm`, never the libvirt stack),
//! firewall/AppArmor tweaks, and Void/XBPS (no native package mapping
//! exists for it here, so it is reported unsupported rather than guessed).
use crate::{
    distro::{self, DistroFamily},
    hardware,
    packages::{CommandRunner, PackageManager},
};
use serde::{Deserialize, Serialize};
use std::{fs, path::Path, path::PathBuf};

pub const WINBOAT_HELPER_PATH: &str = "/usr/lib/mg-linux-toolbox/mg-linux-toolbox-winboat-helper";
pub const GITHUB_LATEST_RELEASE_URL: &str =
    "https://api.github.com/repos/winboat-org/winboat/releases/latest";
pub const DOCKER_APT_KEYRING: &str = "/etc/apt/keyrings/docker.asc";
pub const DOCKER_APT_LIST: &str = "/etc/apt/sources.list.d/docker.list";
pub const DOCKER_FEDORA_REPO_URL: &str = "https://download.docker.com/linux/fedora/docker-ce.repo";
pub const DOCKER_FEDORA_REPO_FILE: &str = "/etc/yum.repos.d/docker-ce.repo";
const WINBOAT_TMP_DIR: &str = "/var/lib/mg-linux-toolbox/tmp";
const MIN_RAM_BYTES: u64 = 4 * 1024 * 1024 * 1024;
const MIN_DISK_BYTES: u64 = 32 * 1024 * 1024 * 1024;
const MIN_CORES: u64 = 2;

/// The one closed operation the privileged helper accepts.
pub fn is_operation(name: &str) -> bool {
    matches!(name, "winboat-prepare")
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ReqState {
    Ok,
    Missing,
    /// CPU supports virtualization but the firmware keeps it disabled: never
    /// attempted automatically, only reported.
    Blocked,
    Unknown,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Requirement {
    pub id: String,
    pub state: ReqState,
    pub detail: String,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WinboatStatus {
    pub supported: bool,
    pub unsupported_reason: Option<&'static str>,
    pub package_manager: Option<&'static str>,
    pub requirements: Vec<Requirement>,
    pub pending_count: usize,
    /// True only when every requirement is satisfied *for the current
    /// session* (including group membership actually being active).
    pub ready: bool,
    /// True when docker/kvm group membership was granted on disk but the
    /// running session has not picked it up yet -- everything else is fine,
    /// only a new session is needed.
    pub reboot_suggested: bool,
}

fn requirement(id: &'static str, state: ReqState, detail: impl Into<String>) -> Requirement {
    Requirement {
        id: id.to_string(),
        state,
        detail: detail.into(),
    }
}

// ---------------------------------------------------------------------
// Pure parsers (fixture-testable, no process/filesystem access)
// ---------------------------------------------------------------------

/// `Some("vmx")`/`Some("svm")` from a `flags`/`Features` line, `None` when
/// hardware virtualization is not exposed at all.
pub fn cpu_virt_flag(cpuinfo: &str) -> Option<&'static str> {
    let line = cpuinfo
        .lines()
        .find(|l| l.trim_start().starts_with("flags"))?;
    let flags = line.split_once(':')?.1;
    if flags.split_whitespace().any(|f| f == "svm") {
        Some("svm")
    } else if flags.split_whitespace().any(|f| f == "vmx") {
        Some("vmx")
    } else {
        None
    }
}

pub fn cpu_vendor(cpuinfo: &str) -> Option<&'static str> {
    let line = cpuinfo
        .lines()
        .find(|l| l.trim_start().starts_with("vendor_id"))?;
    match line.split_once(':')?.1.trim() {
        "AuthenticAMD" => Some("amd"),
        "GenuineIntel" => Some("intel"),
        _ => None,
    }
}

pub fn kvm_module_name(vendor: &str) -> Option<&'static str> {
    match vendor {
        "amd" => Some("kvm_amd"),
        "intel" => Some("kvm_intel"),
        _ => None,
    }
}

pub fn ram_total_bytes(meminfo: &str) -> u64 {
    meminfo
        .lines()
        .find_map(|l| l.strip_prefix("MemTotal:"))
        .and_then(|rest| rest.split_whitespace().next())
        .and_then(|kb| kb.parse::<u64>().ok())
        .map(|kb| kb * 1024)
        .unwrap_or(0)
}

pub fn cpu_core_count(cpuinfo: &str) -> u64 {
    cpuinfo
        .lines()
        .find_map(|l| l.strip_prefix("cpu cores"))
        .and_then(|rest| rest.split_once(':'))
        .and_then(|(_, v)| v.trim().parse::<u64>().ok())
        .unwrap_or_else(|| {
            cpuinfo
                .lines()
                .filter(|l| l.starts_with("processor"))
                .count() as u64
        })
}

/// Major version from a real `xfreerdp[3] --version` banner, e.g.
/// "This is FreeRDP version 3.31.1+..." -> `3`. `None` when the text does
/// not contain a recognisable "version X.Y" token.
pub fn parse_freerdp_major(output: &str) -> Option<u32> {
    let lower = output.to_ascii_lowercase();
    let idx = lower.find("version ")?;
    let after = output.get(idx + "version ".len()..)?;
    let token = after.split_whitespace().next()?;
    let digits: String = token.chars().take_while(|c| c.is_ascii_digit()).collect();
    digits.parse().ok()
}

/// Major version from `docker compose version`, e.g. "Docker Compose
/// version v2.29.7" -> `2`.
pub fn parse_compose_major(output: &str) -> Option<u32> {
    output
        .split_whitespace()
        .filter_map(|token| {
            let cleaned = token.trim_start_matches('v');
            let digits: String = cleaned.chars().take_while(|c| c.is_ascii_digit()).collect();
            (!digits.is_empty() && cleaned.as_bytes().get(digits.len()) == Some(&b'.'))
                .then(|| digits.parse().ok())
                .flatten()
        })
        .next()
}

/// `true` when `group_line` (the third, comma-separated field of a real
/// `getent group <name>` line) lists `user`.
pub fn group_line_lists_user(group_line: &str, user: &str) -> bool {
    group_line
        .trim()
        .split(':')
        .nth(3)
        .unwrap_or("")
        .split(',')
        .any(|member| member == user)
}

fn docker_compose_asset_extension(family: DistroFamily) -> &'static str {
    match family {
        DistroFamily::Ubuntu | DistroFamily::Debian => "deb",
        DistroFamily::Fedora => "rpm",
        DistroFamily::Arch | DistroFamily::Suse => "AppImage",
    }
}

fn winboat_download_path(family: DistroFamily) -> PathBuf {
    Path::new(WINBOAT_TMP_DIR).join(format!(
        "winboat.{}",
        docker_compose_asset_extension(family)
    ))
}

/// Picks the release asset for this family's install method: the first
/// asset whose filename ends with the right extension and mentions the
/// right architecture token. Never guesses when nothing matches.
pub fn pick_release_asset(
    assets: &[(String, String)],
    family: DistroFamily,
) -> Option<&(String, String)> {
    let ext = docker_compose_asset_extension(family);
    let arch_token = if ext == "deb" { "amd64" } else { "x86_64" };
    assets.iter().find(|(name, _url)| {
        let lower = name.to_ascii_lowercase();
        lower.ends_with(&format!(".{}", ext.to_ascii_lowercase()))
            && (lower.contains(arch_token) || lower.contains("x86_64") || lower.contains("amd64"))
    })
}

// ---------------------------------------------------------------------
// Real (impure) detection, injected through CommandRunner + a few direct
// filesystem/libc reads exactly like the rest of this codebase.
// ---------------------------------------------------------------------

fn read_cpuinfo() -> String {
    fs::read_to_string("/proc/cpuinfo").unwrap_or_default()
}
fn read_meminfo() -> String {
    fs::read_to_string("/proc/meminfo").unwrap_or_default()
}

fn free_bytes(path: &str) -> Option<u64> {
    let c = std::ffi::CString::new(path).ok()?;
    let mut st = unsafe { std::mem::zeroed::<libc::statvfs>() };
    if unsafe { libc::statvfs(c.as_ptr(), &mut st) } != 0 {
        return None;
    }
    Some(st.f_bavail as u64 * st.f_frsize as u64)
}

/// The real user that started M.G, resolved without ever trusting a string
/// from the frontend: `getuid()` for the unprivileged status read, or
/// `PKEXEC_UID` (set by pkexec itself, never by the caller) inside the
/// privileged helper.
fn current_session_user() -> Option<(String, u32)> {
    let uid = unsafe { libc::getuid() };
    username_for_uid(uid).map(|name| (name, uid))
}

fn invoking_user() -> Result<(String, u32), String> {
    let uid: u32 = std::env::var("PKEXEC_UID")
        .map_err(|_| "invoking_user_unknown".to_string())?
        .parse()
        .map_err(|_| "invoking_user_unknown".to_string())?;
    username_for_uid(uid)
        .map(|name| (name, uid))
        .ok_or_else(|| "invoking_user_unknown".to_string())
}

fn username_for_uid(uid: u32) -> Option<String> {
    unsafe {
        let pw = libc::getpwuid(uid);
        if pw.is_null() {
            return None;
        }
        Some(
            std::ffi::CStr::from_ptr((*pw).pw_name)
                .to_string_lossy()
                .into_owned(),
        )
    }
}

/// `true` when the *running process* already has `group` among its real
/// supplementary groups -- the one signal that tells apart "already active
/// this session" from "granted on disk, needs a new session".
fn process_has_group(group: &str) -> bool {
    unsafe {
        let gr = libc::getgrnam(std::ffi::CString::new(group).unwrap().as_ptr());
        if gr.is_null() {
            return false;
        }
        let target_gid = (*gr).gr_gid;
        if libc::getgid() == target_gid {
            return true;
        }
        let mut buf = [0u32; 64];
        let count = libc::getgroups(buf.len() as i32, buf.as_mut_ptr());
        count > 0 && buf[..count as usize].contains(&target_gid)
    }
}

fn kvm_device_group(runner: &dyn CommandRunner) -> Option<String> {
    let meta = fs::metadata("/dev/kvm").ok()?;
    let gid = std::os::unix::fs::MetadataExt::gid(&meta);
    let output = runner.run("getent", &["group", &gid.to_string()])?;
    output
        .success
        .then(|| output.stdout.split(':').next().map(str::to_string))
        .flatten()
}

/// `true` when `user`'s primary or supplementary group membership already
/// grants access to `group`, checked the same way (via `getent`/`id`)
/// whether this runs as the user or as root on the user's behalf -- the
/// helper never has to guess or trust a claim about its own privilege.
fn group_is_granted(group: &str, user: &str, runner: &dyn CommandRunner) -> bool {
    let primary = runner
        .run("id", &["-gn", user])
        .is_some_and(|o| o.success && o.stdout.trim() == group);
    let supplementary = runner
        .run("getent", &["group", group])
        .is_some_and(|o| o.success && group_line_lists_user(&o.stdout, user));
    primary || supplementary
}

/// Really tries to open `/dev/kvm` read-write. Only meaningful when it runs
/// as the user being checked (root would always succeed); callers that run
/// as root must pass [`KvmProbe::OtherUser`] instead of trusting this.
fn kvm_openable_now() -> bool {
    std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open("/dev/kvm")
        .is_ok()
}

/// Parses `getfacl -p <device>` output for a named-user entry that really
/// grants read+write to `user` -- the POSIX ACL path many distributions
/// (udev `uaccess` tags, systemd default ACLs) use to hand a desktop user
/// `/dev/kvm` *without* any `kvm` group membership at all. Adding the user
/// to the group in that case would be an unnecessary, permanent change.
fn device_acl_grants(runner: &dyn CommandRunner, device: &str, user: &str) -> bool {
    let Some(output) = runner.run("getfacl", &["-p", device]) else {
        return false;
    };
    if !output.success {
        return false;
    }
    output.stdout.lines().any(|line| {
        let line = line.trim();
        let Some(rest) = line.strip_prefix("user:") else {
            return false;
        };
        let mut fields = rest.split(':');
        let name = fields.next().unwrap_or("");
        let permissions = fields.next().unwrap_or("");
        name == user && permissions.contains('r') && permissions.contains('w')
    })
}

/// Whether *this* call can trust a live `open()` test: true only when the
/// process really runs as the user being checked.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum KvmProbe {
    /// We are the user: `open()` is the definitive answer.
    ThisUser,
    /// We run as root on the user's behalf (privileged helper): the live
    /// `open()` would lie, so only ACL/group disk state may be trusted.
    OtherUser,
}

/// The KVM requirement is only ever "ok" when `user` can really use
/// `/dev/kvm`: CPU flag present, device present, and access really granted
/// (by a live open when possible, otherwise by an ACL entry or real group
/// membership). Never "a package is installed", never "the device exists".
fn kvm_requirement(user: &str, runner: &dyn CommandRunner, probe: KvmProbe) -> Requirement {
    let cpuinfo = read_cpuinfo();
    let Some(_flag) = cpu_virt_flag(&cpuinfo) else {
        return requirement(
            "kvm",
            ReqState::Missing,
            "La CPU non espone i flag vmx/svm: virtualizzazione hardware non disponibile.",
        );
    };
    if !Path::new("/dev/kvm").exists() {
        return requirement(
            "kvm",
            ReqState::Missing,
            "CPU compatibile ma /dev/kvm non è presente: i moduli KVM vanno caricati.",
        );
    }
    if probe == KvmProbe::ThisUser && kvm_openable_now() {
        return requirement("kvm", ReqState::Ok, "/dev/kvm accessibile");
    }
    if device_acl_grants(runner, "/dev/kvm", user) {
        return requirement("kvm", ReqState::Ok, "/dev/kvm accessibile (ACL utente)");
    }
    let Some(group) = kvm_device_group(runner) else {
        return requirement("kvm", ReqState::Ok, "/dev/kvm disponibile");
    };
    if group_is_granted(&group, user, runner) {
        requirement(
            "kvm",
            ReqState::Ok,
            format!("/dev/kvm disponibile (gruppo {group})"),
        )
    } else {
        requirement(
            "kvm",
            ReqState::Missing,
            format!("/dev/kvm presente ma richiede il gruppo {group}: autorizzazione da concedere"),
        )
    }
}

fn ram_requirement() -> Requirement {
    let total = ram_total_bytes(&read_meminfo());
    let gb = total as f64 / (1024.0 * 1024.0 * 1024.0);
    if total >= MIN_RAM_BYTES {
        requirement("ram", ReqState::Ok, format!("{gb:.1} GB rilevati"))
    } else {
        requirement(
            "ram",
            ReqState::Missing,
            format!("{gb:.1} GB rilevati, minimo 4 GB"),
        )
    }
}

fn cpu_requirement() -> Requirement {
    let arch_ok = std::env::consts::ARCH == "x86_64";
    let cpuinfo = read_cpuinfo();
    let cores = cpu_core_count(&cpuinfo);
    if !arch_ok {
        return requirement(
            "cpu",
            ReqState::Missing,
            format!("Architettura {} non supportata", std::env::consts::ARCH),
        );
    }
    if cores < MIN_CORES {
        return requirement(
            "cpu",
            ReqState::Missing,
            format!("{cores} core fisici rilevati, minimo 2"),
        );
    }
    requirement("cpu", ReqState::Ok, format!("x86_64 · {cores} core fisici"))
}

fn disk_requirement() -> Requirement {
    let free = free_bytes("/").unwrap_or(0);
    let gb = free as f64 / (1024.0 * 1024.0 * 1024.0);
    if free >= MIN_DISK_BYTES {
        requirement("disk", ReqState::Ok, format!("{gb:.0} GB liberi"))
    } else {
        requirement(
            "disk",
            ReqState::Missing,
            format!("{gb:.0} GB liberi, minimo 32 GB"),
        )
    }
}

/// `true` for the binary paths a Snap-packaged `docker` resolves to. Pure
/// and directly testable, unlike a real `$PATH` scan.
fn docker_path_is_snap(path: &Path) -> bool {
    path.starts_with("/snap") || path.starts_with("/var/lib/snapd")
}

/// `true` when the signals available for Docker Desktop line up: its
/// per-user state directory exists, or the active context is the one it
/// registers. Pure (the directory check is passed in, not read here), so
/// it is testable without a real `$HOME`.
fn is_docker_desktop(desktop_dir_exists: bool, context_output: &str) -> bool {
    desktop_dir_exists || context_output.trim() == "desktop-linux"
}

fn docker_binary() -> Option<PathBuf> {
    hardware::executable_in_path(&["docker"])
}

fn docker_engine_requirement(runner: &dyn CommandRunner) -> Requirement {
    // The presence check itself always goes through the injectable runner
    // (never only a raw `$PATH` scan), so this requirement's Ok/Missing
    // split stays testable with a fixture instead of silently depending on
    // whatever happens to be installed on the machine running the tests.
    let version = runner
        .run("docker", &["--version"])
        .filter(|o| o.success)
        .map(|o| o.stdout.trim().to_string());
    let Some(version) = version else {
        return requirement("docker_engine", ReqState::Missing, "Docker non installato");
    };
    if docker_binary().is_some_and(|path| docker_path_is_snap(&path)) {
        return requirement(
            "docker_engine",
            ReqState::Blocked,
            "Docker installato tramite Snap: configurazione non supportata da WinBoat, serve intervento manuale.",
        );
    }
    let desktop_dir_exists = std::env::var("HOME")
        .map(|home| Path::new(&home).join(".docker/desktop").is_dir())
        .unwrap_or(false);
    let context_output = runner
        .run("docker", &["context", "show"])
        .filter(|o| o.success)
        .map(|o| o.stdout)
        .unwrap_or_default();
    if is_docker_desktop(desktop_dir_exists, &context_output) {
        return requirement(
            "docker_engine",
            ReqState::Blocked,
            "Rilevato Docker Desktop: WinBoat richiede Docker Engine, non Docker Desktop.",
        );
    }
    requirement("docker_engine", ReqState::Ok, version)
}

fn docker_compose_requirement(runner: &dyn CommandRunner) -> Requirement {
    let Some(output) = runner.run("docker", &["compose", "version"]) else {
        return requirement(
            "docker_compose",
            ReqState::Missing,
            "Docker Compose non disponibile",
        );
    };
    if !output.success {
        return requirement(
            "docker_compose",
            ReqState::Missing,
            "Docker Compose non disponibile",
        );
    }
    match parse_compose_major(&output.stdout) {
        Some(major) if major >= 2 => {
            requirement("docker_compose", ReqState::Ok, output.stdout.trim())
        }
        Some(major) => requirement(
            "docker_compose",
            ReqState::Missing,
            format!("Versione {major}.x rilevata, richiesta major >= 2"),
        ),
        None => requirement("docker_compose", ReqState::Unknown, output.stdout.trim()),
    }
}

fn docker_daemon_requirement(runner: &dyn CommandRunner) -> Requirement {
    match runner.run("docker", &["info", "--format", "{{.ServerVersion}}"]) {
        Some(output) if output.success && !output.stdout.trim().is_empty() => requirement(
            "docker_daemon",
            ReqState::Ok,
            format!("Daemon attivo (server {})", output.stdout.trim()),
        ),
        _ => requirement(
            "docker_daemon",
            ReqState::Missing,
            "Il servizio Docker non risponde",
        ),
    }
}

/// Ok as soon as `docker` group membership is really granted (`getent`),
/// independent of whether *this particular running process* has picked it
/// up yet -- that distinction is `reboot_suggested`'s job in `status_with`,
/// computed once, only on the unprivileged read side.
fn docker_group_requirement(user: &str, runner: &dyn CommandRunner) -> Requirement {
    if group_is_granted("docker", user, runner) {
        requirement(
            "docker_group",
            ReqState::Ok,
            format!("{user} è nel gruppo docker"),
        )
    } else {
        requirement(
            "docker_group",
            ReqState::Missing,
            "Utente non nel gruppo docker",
        )
    }
}

fn freerdp_requirement(runner: &dyn CommandRunner) -> Requirement {
    let Some(binary) = hardware::executable_in_path(&["xfreerdp3", "xfreerdp"]) else {
        return requirement("freerdp", ReqState::Missing, "FreeRDP non installato");
    };
    let name = binary.to_string_lossy().to_string();
    let Some(output) = runner.run(&name, &["--version"]) else {
        return requirement(
            "freerdp",
            ReqState::Unknown,
            "impossibile leggere la versione",
        );
    };
    match parse_freerdp_major(&output.stdout).or_else(|| parse_freerdp_major(&output.stderr)) {
        Some(major) if major >= 3 => requirement(
            "freerdp",
            ReqState::Ok,
            format!("FreeRDP {major}.x ({name})"),
        ),
        Some(major) => requirement(
            "freerdp",
            ReqState::Missing,
            format!("FreeRDP {major}.x rilevato, richiesta major >= 3"),
        ),
        None => requirement("freerdp", ReqState::Unknown, "versione non riconosciuta"),
    }
}

/// The real, resolved WinBoat executable, if one is really installed --
/// never a path the frontend could supply, only what this function itself
/// finds on `$PATH` or at the one fixed AppImage install location.
pub fn winboat_binary_path() -> Option<PathBuf> {
    hardware::executable_in_path(&["winboat"]).or_else(|| {
        let appimage = Path::new("/opt/winboat/winboat");
        appimage.is_file().then(|| appimage.to_path_buf())
    })
}

pub fn winboat_installed() -> bool {
    winboat_binary_path().is_some() || Path::new("/usr/share/applications/winboat.desktop").exists()
}

fn winboat_requirement() -> Requirement {
    if winboat_installed() {
        requirement("winboat", ReqState::Ok, "WinBoat installato")
    } else {
        requirement("winboat", ReqState::Missing, "WinBoat non installato")
    }
}

/// Every requirement in the fixed order the UI checklist uses. Read-only,
/// safe to call as the normal user *and* meaningful when called as root on
/// the user's behalf (every group check goes through `getent`/`id`/`getfacl`,
/// never through this process's own credentials -- see [`KvmProbe`]).
fn requirements(user: &str, runner: &dyn CommandRunner, probe: KvmProbe) -> Vec<Requirement> {
    vec![
        kvm_requirement(user, runner, probe),
        ram_requirement(),
        cpu_requirement(),
        disk_requirement(),
        docker_engine_requirement(runner),
        docker_compose_requirement(runner),
        docker_group_requirement(user, runner),
        docker_daemon_requirement(runner),
        freerdp_requirement(runner),
        winboat_requirement(),
    ]
}

pub fn status_with(runner: &dyn CommandRunner) -> WinboatStatus {
    let release = fs::read_to_string("/etc/os-release")
        .map(|text| distro::parse_os_release(&text))
        .unwrap_or_default();
    let family = distro::family(&release);
    let Some(family) = family else {
        return WinboatStatus {
            supported: false,
            unsupported_reason: Some("unsupportedDistro"),
            package_manager: None,
            requirements: Vec::new(),
            pending_count: 0,
            ready: false,
            reboot_suggested: false,
        };
    };
    let pm = PackageManager::detect(family, runner);
    let Some((user, _uid)) = current_session_user() else {
        return WinboatStatus {
            supported: true,
            unsupported_reason: Some("userUnknown"),
            package_manager: pm.as_ref().map(|p| p.kind.name()),
            requirements: Vec::new(),
            pending_count: 0,
            ready: false,
            reboot_suggested: false,
        };
    };
    let reqs = requirements(&user, runner, KvmProbe::ThisUser);
    let pending = reqs.iter().filter(|r| r.state != ReqState::Ok).count();
    // Every requirement is really granted on disk, but *this session's*
    // process might not have picked a new grant up yet: that, and only
    // that, is what asking for a reboot instead of "still not ready" means.
    // The live `open()` test is the definitive answer for KVM; for Docker
    // it is the process's own group list.
    let kvm_reboot = reqs
        .iter()
        .any(|r| r.id == "kvm" && r.state == ReqState::Ok)
        && !kvm_openable_now();
    let docker_reboot = reqs
        .iter()
        .any(|r| r.id == "docker_group" && r.state == ReqState::Ok)
        && !process_has_group("docker");
    let reboot_suggested = docker_reboot || kvm_reboot;
    WinboatStatus {
        supported: true,
        unsupported_reason: None,
        package_manager: pm.as_ref().map(|p| p.kind.name()),
        requirements: reqs,
        pending_count: pending,
        ready: pending == 0 && !reboot_suggested,
        reboot_suggested,
    }
}

pub fn status() -> WinboatStatus {
    status_with(&crate::packages::SystemRunner)
}

// ---------------------------------------------------------------------
// Privileged plan + execution (helper side)
// ---------------------------------------------------------------------

#[derive(Clone, Debug)]
enum Step {
    /// Loads kvm/kvm_amd|kvm_intel if `/dev/kvm` is missing, then grants
    /// the device's group to the invoking user if it still isn't. Kept as
    /// one step (not two) because whether a group grant is even needed can
    /// only be known once `/dev/kvm` genuinely exists.
    EnsureKvmReady,
    AddDockerAptRepo {
        slug: &'static str,
    },
    /// Refreshes the package index right after a new repository was
    /// written: APT cannot install anything from a repository it has not
    /// fetched yet, so skipping this made the very next step fail on any
    /// machine that did not already have Docker's index cached. The
    /// packages that must become resolvable are carried along so a refresh
    /// that only fails because of *unrelated* broken repositories on this
    /// machine can be told apart from a refresh that really did not work.
    RefreshIndexes {
        packages: Vec<String>,
    },
    AddDockerFedoraRepo,
    InstallDocker {
        packages: Vec<String>,
    },
    CreateDockerGroup,
    EnableStartDocker,
    AddUserToDockerGroup,
    InstallFreeRdp {
        packages: Vec<String>,
    },
    DownloadWinboat,
    InstallWinboat,
}

fn step_id(step: &Step) -> &'static str {
    match step {
        Step::EnsureKvmReady => "ensure_kvm_ready",
        Step::AddDockerAptRepo { .. } | Step::AddDockerFedoraRepo => "add_docker_repo",
        Step::RefreshIndexes { .. } => "refresh_indexes",
        Step::InstallDocker { .. } => "install_docker",
        Step::CreateDockerGroup => "create_docker_group",
        Step::EnableStartDocker => "enable_docker",
        Step::AddUserToDockerGroup => "add_user_docker_group",
        Step::InstallFreeRdp { .. } => "install_freerdp",
        Step::DownloadWinboat => "download_winboat",
        Step::InstallWinboat => "install_winboat",
    }
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
    /// 1-based position and the real, final count of steps *this* run
    /// planned -- so the UI can show a true "N of M done" progress bar
    /// derived from real completed steps, never a fake timer.
    pub step_index: usize,
    pub total_steps: usize,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WinboatReport {
    pub ok: bool,
    pub steps: Vec<StepReport>,
    pub reboot_required: bool,
    pub requirements: Vec<Requirement>,
}

fn tail(text: &str) -> String {
    const LIMIT: usize = 800;
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

fn run_report(
    runner: &dyn CommandRunner,
    step: &Step,
    program: &str,
    args: &[String],
) -> StepReport {
    let refs: Vec<&str> = args.iter().map(String::as_str).collect();
    match runner.run(program, &refs) {
        Some(output) => StepReport {
            step: step_id(step).to_string(),
            operation: format!("{program} {}", args.join(" ")),
            ok: output.success,
            exit_code: output.exit_code,
            stdout_tail: tail(&output.stdout),
            stderr_tail: tail(&output.stderr),
            step_index: 0,
            total_steps: 0,
        },
        None => StepReport {
            step: step_id(step).to_string(),
            operation: format!("{program} {}", args.join(" ")),
            ok: false,
            exit_code: -1,
            stdout_tail: String::new(),
            stderr_tail: "command_spawn_failed".to_string(),
            step_index: 0,
            total_steps: 0,
        },
    }
}

fn synthetic_report(step: &Step, ok: bool, message: impl Into<String>) -> StepReport {
    let text = message.into();
    StepReport {
        step: step_id(step).to_string(),
        operation: step_id(step).to_string(),
        ok,
        exit_code: if ok { 0 } else { -1 },
        stdout_tail: if ok { text.clone() } else { String::new() },
        stderr_tail: if ok { String::new() } else { text },
        step_index: 0,
        total_steps: 0,
    }
}

/// Builds the ordered, minimal step list from the real requirement gaps.
/// Never proposes a step for something already satisfied (idempotent by
/// construction, not by re-checking at execution time only).
/// The exact package set each family installs for Docker Engine + Compose
/// v2. Kept in one place so the refresh step's resolvability check and the
/// install step can never disagree about what has to become available.
fn docker_packages_for(family: DistroFamily) -> Vec<String> {
    match family {
        DistroFamily::Ubuntu | DistroFamily::Debian | DistroFamily::Fedora => vec![
            "docker-ce".into(),
            "docker-ce-cli".into(),
            "containerd.io".into(),
            "docker-buildx-plugin".into(),
            "docker-compose-plugin".into(),
        ],
        DistroFamily::Arch => vec!["docker".into(), "docker-compose".into()],
        DistroFamily::Suse => vec![
            "docker".into(),
            "docker-compose".into(),
            "docker-buildx".into(),
        ],
    }
}

fn build_steps(
    family: DistroFamily,
    reqs: &[Requirement],
    pm: &PackageManager,
) -> Result<Vec<Step>, &'static str> {
    let by_id = |id: &str| reqs.iter().find(|r| r.id == id);
    let mut steps = Vec::new();

    if let Some(kvm) = by_id("kvm") {
        if kvm.state == ReqState::Missing && kvm.detail.contains("vmx/svm") {
            // CPU itself lacks hardware virtualization: nothing to plan,
            // the caller stops before touching anything else.
            return Err("virtualizationUnavailable");
        }
        if kvm.state == ReqState::Missing {
            steps.push(Step::EnsureKvmReady);
        }
    }

    let docker_engine_ok = by_id("docker_engine").is_some_and(|r| r.state == ReqState::Ok);
    let docker_engine_blocked =
        by_id("docker_engine").is_some_and(|r| r.state == ReqState::Blocked);
    if docker_engine_blocked {
        return Err("dockerConflict");
    }
    if !docker_engine_ok {
        let packages = docker_packages_for(family);
        match family {
            DistroFamily::Ubuntu | DistroFamily::Debian => {
                let slug = if family == DistroFamily::Debian {
                    "debian"
                } else {
                    "ubuntu"
                };
                steps.push(Step::AddDockerAptRepo { slug });
                steps.push(Step::RefreshIndexes {
                    packages: packages.clone(),
                });
                steps.push(Step::InstallDocker { packages });
            }
            DistroFamily::Fedora => {
                steps.push(Step::AddDockerFedoraRepo);
                steps.push(Step::InstallDocker { packages });
            }
            DistroFamily::Arch | DistroFamily::Suse => {
                steps.push(Step::InstallDocker { packages });
            }
        }
    } else if by_id("docker_compose").is_some_and(|r| r.state != ReqState::Ok) {
        // Engine present, plugin missing: install just the compose plugin
        // package for this family, without touching the engine at all.
        let package = match family {
            DistroFamily::Ubuntu | DistroFamily::Debian | DistroFamily::Fedora => {
                "docker-compose-plugin"
            }
            DistroFamily::Arch | DistroFamily::Suse => "docker-compose",
        };
        steps.push(Step::InstallDocker {
            packages: vec![package.to_string()],
        });
    }

    if !pm.is_installed(&crate::packages::SystemRunner, "docker") {
        // best-effort informational only; real gate stays the getent check below
    }
    steps.push(Step::CreateDockerGroup);
    steps.push(Step::EnableStartDocker);
    if by_id("docker_group").is_some_and(|r| r.state != ReqState::Ok) {
        steps.push(Step::AddUserToDockerGroup);
    }

    if by_id("freerdp").is_some_and(|r| r.state != ReqState::Ok) {
        let packages = match family {
            DistroFamily::Ubuntu | DistroFamily::Debian => vec!["freerdp3-x11".to_string()],
            DistroFamily::Fedora | DistroFamily::Arch | DistroFamily::Suse => {
                vec!["freerdp".to_string()]
            }
        };
        steps.push(Step::InstallFreeRdp { packages });
    }

    if by_id("winboat").is_some_and(|r| r.state != ReqState::Ok) {
        steps.push(Step::DownloadWinboat);
        steps.push(Step::InstallWinboat);
    }

    Ok(steps)
}

/// The plain `program`/`args` steps: every step whose execution is a
/// single real command with no extra logic around it.
fn step_program_args(step: &Step, pm: &PackageManager) -> Option<(String, Vec<String>)> {
    match step {
        Step::InstallDocker { packages } | Step::InstallFreeRdp { packages } => {
            let mut argv: Vec<String> = pm
                .kind
                .install_prefix(pm.binary)
                .iter()
                .map(|v| (*v).to_string())
                .collect();
            argv.extend(packages.iter().cloned());
            Some((argv.remove(0), argv))
        }
        Step::RefreshIndexes { .. } => {
            let mut argv: Vec<String> = pm
                .kind
                .refresh_argv(pm.binary)
                .iter()
                .map(|v| (*v).to_string())
                .collect();
            Some((argv.remove(0), argv))
        }
        Step::EnableStartDocker => Some((
            "systemctl".into(),
            vec!["enable".into(), "--now".into(), "docker".into()],
        )),
        Step::AddUserToDockerGroup => {
            let user = invoking_user().ok()?.0;
            Some(("usermod".into(), vec!["-aG".into(), "docker".into(), user]))
        }
        _ => None,
    }
}

fn run_step(
    runner: &dyn CommandRunner,
    step: &Step,
    pm: &PackageManager,
    family: DistroFamily,
) -> StepReport {
    match step {
        Step::EnsureKvmReady => run_ensure_kvm_ready(runner, step),
        Step::AddDockerAptRepo { slug } => run_add_docker_apt_repo(runner, step, slug),
        Step::AddDockerFedoraRepo => run_add_docker_fedora_repo(runner, step),
        Step::RefreshIndexes { packages } => run_refresh_indexes(runner, step, pm, packages),
        Step::CreateDockerGroup => {
            let exists = runner
                .run("getent", &["group", "docker"])
                .is_some_and(|o| o.success);
            if exists {
                synthetic_report(step, true, "gruppo docker già presente")
            } else {
                run_report(runner, step, "groupadd", &["docker".to_string()])
            }
        }
        Step::DownloadWinboat => run_download_winboat(step, family),
        Step::InstallWinboat => run_install_winboat(runner, step, family),
        Step::AddUserToDockerGroup => {
            if group_is_granted(
                "docker",
                &invoking_user().map(|(u, _)| u).unwrap_or_default(),
                runner,
            ) {
                return synthetic_report(step, true, "utente già nel gruppo docker");
            }
            match step_program_args(step, pm) {
                Some((program, args)) => run_report(runner, step, &program, &args),
                None => synthetic_report(step, false, "invoking_user_unknown"),
            }
        }
        _ => match step_program_args(step, pm) {
            Some((program, args)) => run_report(runner, step, &program, &args),
            None => synthetic_report(step, false, "step_not_executable"),
        },
    }
}

/// Loads the KVM kernel modules only if `/dev/kvm` is really missing (never
/// re-run once it exists), then grants the device's group to the invoking
/// user only if that grant is really still missing. Stops with a clear,
/// specific error when the CPU supports virtualization but the firmware
/// keeps it disabled -- never a silent retry, never a guess.
fn run_ensure_kvm_ready(runner: &dyn CommandRunner, step: &Step) -> StepReport {
    let (user, _uid) = match invoking_user() {
        Ok(pair) => pair,
        Err(e) => return synthetic_report(step, false, e),
    };
    if !Path::new("/dev/kvm").exists() {
        let cpuinfo = read_cpuinfo();
        let Some(vendor) = cpu_vendor(&cpuinfo) else {
            return synthetic_report(step, false, "kvm_vendor_unknown");
        };
        let Some(module) = kvm_module_name(vendor) else {
            return synthetic_report(step, false, "kvm_vendor_unknown");
        };
        if let Some(output) = runner.run("modprobe", &["kvm"]) {
            if !output.success {
                return synthetic_report(step, false, tail(&output.stderr));
            }
        } else {
            return synthetic_report(step, false, "command_spawn_failed");
        }
        match runner.run("modprobe", &[module]) {
            Some(output) if output.success => {}
            Some(output) => return synthetic_report(step, false, tail(&output.stderr)),
            None => return synthetic_report(step, false, "command_spawn_failed"),
        }
        if !Path::new("/dev/kvm").exists() {
            return synthetic_report(step, false, "virtualization_disabled_in_firmware");
        }
    }
    // An ACL entry for the invoking user is just as valid as group
    // membership, and is what most desktop distributions actually use
    // (udev `uaccess` / default ACLs): adding the group on top would be an
    // unnecessary permanent change.
    if device_acl_grants(runner, "/dev/kvm", &user) {
        return synthetic_report(step, true, "/dev/kvm pronto (ACL utente)");
    }
    let Some(group) = kvm_device_group(runner) else {
        return synthetic_report(step, true, "/dev/kvm pronto");
    };
    if group_is_granted(&group, &user, runner) {
        return synthetic_report(step, true, format!("/dev/kvm pronto (gruppo {group})"));
    }
    let refs = ["-aG".to_string(), group.clone(), user];
    let report = run_report(runner, step, "usermod", &refs);
    if report.ok {
        synthetic_report(step, true, format!("utente aggiunto al gruppo {group}"))
    } else {
        report
    }
}

/// Whether Docker really publishes packages for this suite (a HEAD on the
/// suite's own `Release` file, the smallest authoritative answer).
fn docker_suite_published(slug: &str, suite: &str) -> bool {
    let url = format!("https://download.docker.com/linux/{slug}/dists/{suite}/Release");
    let Ok(client) = reqwest::blocking::Client::builder()
        .user_agent(format!("M.G-Linux-Toolbox/{}", env!("CARGO_PKG_VERSION")))
        .timeout(std::time::Duration::from_secs(15))
        .build()
    else {
        return false;
    };
    client
        .head(&url)
        .send()
        .map(|response| response.status().is_success())
        .unwrap_or(false)
}

/// The `dists/` suite to write into the Docker source line.
///
/// The distribution's own codename is used whenever Docker really publishes
/// it. A development/pre-release distribution (e.g. an Ubuntu development
/// branch, whose codename has no `dists/` entry yet) falls back to the
/// latest *released* codename as reported by the system's own
/// `distro-info --stable` -- never by guessing -- and only when that
/// codename is itself really published. When neither is available the step
/// fails with a clear reason instead of writing a source list that cannot
/// possibly work.
fn resolve_docker_apt_suite(runner: &dyn CommandRunner, slug: &str) -> Result<String, String> {
    let codename = os_release_codename().ok_or("codename_unavailable")?;
    if docker_suite_published(slug, &codename) {
        return Ok(codename);
    }
    let stable = runner
        .run("distro-info", &["--stable"])
        .filter(|output| output.success)
        .map(|output| output.stdout.trim().to_string())
        .filter(|value| !value.is_empty() && *value != codename);
    if let Some(stable) = stable {
        if docker_suite_published(slug, &stable) {
            return Ok(stable);
        }
    }
    Err("docker_repo_suite_unavailable".into())
}

/// `true` when APT really offers an install candidate for `package`, the
/// same check the interactive `apt policy` output is built from.
fn apt_candidate_available(runner: &dyn CommandRunner, package: &str) -> bool {
    runner
        .run("apt-cache", &["policy", package])
        .is_some_and(|output| {
            output.success
                && output.stdout.lines().any(|line| {
                    let line = line.trim();
                    line.starts_with("Candidate:") && !line.contains("(none)")
                })
        })
}

/// Runs the package-index refresh and decides, from the real result rather
/// than from the exit code alone, whether it actually did its job.
///
/// `apt-get update` exits non-zero when *any* configured repository fails
/// -- including repositories that have nothing to do with WinBoat. A
/// machine carrying one unrelated broken third-party source therefore
/// failed here even though Docker's own index was fetched correctly. The
/// refresh is only rescued when every package the following install step
/// needs is really resolvable afterwards; when even one is not, the real
/// failure is kept.
fn run_refresh_indexes(
    runner: &dyn CommandRunner,
    step: &Step,
    pm: &PackageManager,
    packages: &[String],
) -> StepReport {
    let Some((program, args)) = step_program_args(step, pm) else {
        return synthetic_report(step, false, "step_not_executable");
    };
    let report = run_report(runner, step, &program, &args);
    if report.ok {
        return report;
    }
    let recovered = pm.kind == crate::packages::PackageManagerKind::Apt
        && !packages.is_empty()
        && packages
            .iter()
            .all(|package| apt_candidate_available(runner, package));
    if recovered {
        return synthetic_report(
            step,
            true,
            "indice aggiornato; errori di repository non correlati ignorati",
        );
    }
    report
}

fn run_add_docker_apt_repo(runner: &dyn CommandRunner, step: &Step, slug: &str) -> StepReport {
    if Path::new(DOCKER_APT_LIST).exists() && Path::new(DOCKER_APT_KEYRING).exists() {
        return synthetic_report(step, true, "repository Docker già configurato");
    }
    let result = (|| -> Result<(), String> {
        let suite = resolve_docker_apt_suite(runner, slug)?;
        fs::create_dir_all("/etc/apt/keyrings").map_err(|e| e.to_string())?;
        let key_bytes = fetch_bytes(&format!("https://download.docker.com/linux/{slug}/gpg"))?;
        fs::write(DOCKER_APT_KEYRING, &key_bytes).map_err(|e| e.to_string())?;
        let _ = fs::set_permissions(
            DOCKER_APT_KEYRING,
            std::os::unix::fs::PermissionsExt::from_mode(0o644),
        );
        let arch = detect_dpkg_arch().unwrap_or_else(|| "amd64".to_string());
        let line = format!(
            "deb [arch={arch} signed-by={DOCKER_APT_KEYRING}] https://download.docker.com/linux/{slug} {suite} stable\n"
        );
        fs::write(DOCKER_APT_LIST, line).map_err(|e| e.to_string())?;
        Ok(())
    })();
    match result {
        Ok(()) => synthetic_report(step, true, "repository Docker configurato"),
        Err(e) => synthetic_report(step, false, e),
    }
}

fn run_add_docker_fedora_repo(runner: &dyn CommandRunner, step: &Step) -> StepReport {
    if Path::new(DOCKER_FEDORA_REPO_FILE).exists() {
        return synthetic_report(step, true, "repository Docker già configurato");
    }
    let args = [
        "config-manager".to_string(),
        "addrepo".to_string(),
        format!("--from-repofile={DOCKER_FEDORA_REPO_URL}"),
    ];
    run_report(runner, step, "dnf5", &args)
}

fn detect_dpkg_arch() -> Option<String> {
    crate::packages::SystemRunner
        .run("dpkg", &["--print-architecture"])
        .filter(|o| o.success)
        .map(|o| o.stdout.trim().to_string())
}

fn os_release_codename() -> Option<String> {
    let text = fs::read_to_string("/etc/os-release").ok()?;
    let release = distro::parse_os_release(&text);
    release.ubuntu_codename.or(release.version_codename)
}

fn fetch_bytes(url: &str) -> Result<Vec<u8>, String> {
    let client = reqwest::blocking::Client::builder()
        .user_agent(format!("M.G-Linux-Toolbox/{}", env!("CARGO_PKG_VERSION")))
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())?;
    let response = client.get(url).send().map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(format!("http_status_{}", response.status().as_u16()));
    }
    response
        .bytes()
        .map(|b| b.to_vec())
        .map_err(|e| e.to_string())
}

#[derive(serde::Deserialize)]
struct GhAsset {
    name: String,
    browser_download_url: String,
}
#[derive(serde::Deserialize)]
struct GhRelease {
    tag_name: String,
    prerelease: bool,
    assets: Vec<GhAsset>,
}

fn run_download_winboat(step: &Step, family: DistroFamily) -> StepReport {
    let result = (|| -> Result<String, String> {
        let bytes = fetch_bytes(GITHUB_LATEST_RELEASE_URL)?;
        let release: GhRelease =
            serde_json::from_slice(&bytes).map_err(|_| "release_metadata_invalid".to_string())?;
        if release.prerelease {
            return Err("only_prerelease_available".into());
        }
        let assets: Vec<(String, String)> = release
            .assets
            .into_iter()
            .map(|a| (a.name, a.browser_download_url))
            .collect();
        let Some((name, url)) = pick_release_asset(&assets, family) else {
            return Err("no_matching_asset".into());
        };
        let asset_bytes = fetch_bytes(url)?;
        fs::create_dir_all(WINBOAT_TMP_DIR).map_err(|e| e.to_string())?;
        let path = winboat_download_path(family);
        fs::write(&path, &asset_bytes).map_err(|e| e.to_string())?;
        Ok(format!(
            "{} ({} bytes) -> {}",
            name,
            asset_bytes.len(),
            release.tag_name
        ))
    })();
    match result {
        Ok(detail) => synthetic_report(step, true, detail),
        Err(e) => synthetic_report(step, false, e),
    }
}

fn run_install_winboat(
    runner: &dyn CommandRunner,
    step: &Step,
    family: DistroFamily,
) -> StepReport {
    if winboat_installed() {
        return synthetic_report(step, true, "WinBoat già installato");
    }
    let path = winboat_download_path(family);
    if !path.exists() {
        return synthetic_report(step, false, "download_missing");
    }
    let path_str = path.to_string_lossy().to_string();
    match family {
        DistroFamily::Ubuntu | DistroFamily::Debian => run_report(
            runner,
            step,
            "apt-get",
            &["install".into(), "-y".into(), path_str],
        ),
        DistroFamily::Fedora => run_report(
            runner,
            step,
            "dnf5",
            &["install".into(), "-y".into(), path_str],
        ),
        DistroFamily::Arch | DistroFamily::Suse => {
            let result = (|| -> Result<(), String> {
                fs::create_dir_all("/opt/winboat").map_err(|e| e.to_string())?;
                let dest = "/opt/winboat/winboat";
                fs::copy(&path, dest).map_err(|e| e.to_string())?;
                let _ =
                    fs::set_permissions(dest, std::os::unix::fs::PermissionsExt::from_mode(0o755));
                let desktop = "[Desktop Entry]\nName=WinBoat\nExec=/opt/winboat/winboat %U\nTerminal=false\nType=Application\nIcon=winboat\nStartupWMClass=winboat\nComment=Windows for Penguins\nCategories=Utility;\n";
                fs::write("/usr/share/applications/winboat.desktop", desktop)
                    .map_err(|e| e.to_string())?;
                Ok(())
            })();
            match result {
                Ok(()) => synthetic_report(step, true, "AppImage installata in /opt/winboat"),
                Err(e) => synthetic_report(step, false, e),
            }
        }
    }
}

/// Executes the one allow-listed WinBoat operation. Everything is
/// re-derived here from the real system: the caller never names a package,
/// a repository, a group or a command.
pub fn apply_operation(
    args: &[String],
    progress: &mut dyn FnMut(&StepReport),
) -> Result<WinboatReport, String> {
    let [action] = args else {
        return Err("invalid_operation".into());
    };
    if action != "winboat-prepare" {
        return Err("invalid_operation".into());
    }
    let runner = crate::packages::SystemRunner;
    let release = fs::read_to_string("/etc/os-release")
        .map(|text| distro::parse_os_release(&text))
        .unwrap_or_default();
    let family = distro::family(&release).ok_or("unsupportedDistro")?;
    let pm = PackageManager::detect(family, &runner).ok_or("packageManagerMissing")?;
    let (user, _uid) = invoking_user()?;
    // Root's own `open()` on /dev/kvm would always succeed: only ACL/group
    // disk state may be trusted here (see KvmProbe).
    let reqs = requirements(&user, &runner, KvmProbe::OtherUser);
    if reqs
        .iter()
        .any(|r| ["ram", "cpu", "disk"].contains(&r.id.as_str()) && r.state != ReqState::Ok)
    {
        return Err("requirementsNotMet".into());
    }
    let steps = build_steps(family, &reqs, &pm)?;

    let total_steps = steps.len();
    let mut ok = true;
    let mut reports = Vec::new();
    for (index, step) in steps.iter().enumerate() {
        let mut report = run_step(&runner, step, &pm, family);
        report.step_index = index + 1;
        report.total_steps = total_steps;
        progress(&report);
        let failed = !report.ok;
        reports.push(report);
        if failed {
            ok = false;
            break;
        }
    }
    let final_requirements = requirements(&user, &runner, KvmProbe::OtherUser);
    // Every requirement is really granted now, but the desktop session that
    // will actually run WinBoat may not have picked a fresh grant up yet --
    // a group that was missing and is now granted always needs a new
    // session, so that exact transition is what triggers the reboot advice.
    let group_was_missing_before = reqs
        .iter()
        .any(|r| (r.id == "docker_group" || r.id == "kvm") && r.state != ReqState::Ok);
    let group_ok_now = final_requirements
        .iter()
        .any(|r| (r.id == "docker_group" || r.id == "kvm") && r.state == ReqState::Ok);
    let reboot_required = ok && group_was_missing_before && group_ok_now;
    if ok {
        let still_missing = final_requirements
            .iter()
            .any(|r| r.state != ReqState::Ok && r.id != "docker_group" && r.id != "kvm");
        if still_missing {
            ok = false;
        }
    }
    Ok(WinboatReport {
        ok,
        steps: reports,
        reboot_required,
        requirements: final_requirements,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{cell::RefCell, collections::HashMap};

    #[derive(Default)]
    struct MockRunner {
        outputs: HashMap<String, crate::packages::CommandOutput>,
        calls: RefCell<Vec<String>>,
    }
    impl MockRunner {
        fn with(mut self, program: &str, args: &[&str], success: bool, stdout: &str) -> Self {
            self.outputs.insert(
                format!("{program}\u{0}{}", args.join("\u{0}")),
                crate::packages::CommandOutput {
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
        fn run(&self, program: &str, args: &[&str]) -> Option<crate::packages::CommandOutput> {
            self.calls
                .borrow_mut()
                .push(format!("{program} {}", args.join(" ")));
            self.outputs
                .get(&format!("{program}\u{0}{}", args.join("\u{0}")))
                .cloned()
        }
    }

    #[test]
    fn detects_amd_and_intel_virtualization_flags() {
        assert_eq!(
            cpu_virt_flag("flags\t\t: fpu vme de pse svm nx\n"),
            Some("svm")
        );
        assert_eq!(
            cpu_virt_flag("flags\t\t: fpu vme de pse vmx nx\n"),
            Some("vmx")
        );
        assert_eq!(cpu_virt_flag("flags\t\t: fpu vme de pse nx\n"), None);
        assert_eq!(cpu_virt_flag("processor : 0\n"), None);
    }

    #[test]
    fn maps_vendor_to_the_right_kvm_module() {
        assert_eq!(cpu_vendor("vendor_id\t: AuthenticAMD\n"), Some("amd"));
        assert_eq!(cpu_vendor("vendor_id\t: GenuineIntel\n"), Some("intel"));
        assert_eq!(kvm_module_name("amd"), Some("kvm_amd"));
        assert_eq!(kvm_module_name("intel"), Some("kvm_intel"));
        assert_eq!(kvm_module_name("other"), None);
    }

    #[test]
    fn parses_ram_and_core_count_from_real_proc_files() {
        assert_eq!(
            ram_total_bytes("MemTotal:       63657708 kB\n"),
            63657708 * 1024
        );
        assert_eq!(ram_total_bytes("nothing here\n"), 0);
        assert_eq!(cpu_core_count("processor : 0\ncpu cores\t: 16\n"), 16);
        assert_eq!(cpu_core_count("processor : 0\nprocessor : 1\n"), 2);
    }

    #[test]
    fn parses_real_freerdp_and_compose_version_banners() {
        assert_eq!(
            parse_freerdp_major("This is FreeRDP version 3.31.1+g8a3c1 (git)\n"),
            Some(3)
        );
        assert_eq!(
            parse_freerdp_major("This is FreeRDP version 2.11.0\n"),
            Some(2)
        );
        assert_eq!(parse_freerdp_major("garbage output\n"), None);
        assert_eq!(
            parse_compose_major("Docker Compose version v2.29.7\n"),
            Some(2)
        );
        assert_eq!(
            parse_compose_major("Docker Compose version 2.29.7\n"),
            Some(2)
        );
        assert_eq!(
            parse_compose_major("docker-compose version 1.29.2\n"),
            Some(1)
        );
    }

    #[test]
    fn group_line_membership_is_read_from_the_fourth_colon_field() {
        assert!(group_line_lists_user("docker:x:999:alice,bob", "bob"));
        assert!(!group_line_lists_user("docker:x:999:alice", "bob"));
        assert!(!group_line_lists_user("docker:x:999:", "bob"));
    }

    #[test]
    fn device_acl_grants_reads_only_a_real_named_user_rw_entry() {
        let real = "# file: /dev/kvm\n# owner: root\n# group: kvm\nuser::rw-\nuser:gregorio:rw-\ngroup::rw-\nmask::rw-\nother::---\n";
        let runner = MockRunner::default().with("getfacl", &["-p", "/dev/kvm"], true, real);
        assert!(device_acl_grants(&runner, "/dev/kvm", "gregorio"));
        assert!(!device_acl_grants(&runner, "/dev/kvm", "someoneelse"));
        // Read-only ACL must never count as usable access.
        let ro = "user::rw-\nuser:gregorio:r--\ngroup::rw-\nother::---\n";
        let runner = MockRunner::default().with("getfacl", &["-p", "/dev/kvm"], true, ro);
        assert!(!device_acl_grants(&runner, "/dev/kvm", "gregorio"));
        // Plain group-only output has no named-user entry at all.
        let plain = "user::rw-\ngroup::rw-\nother::---\n";
        let runner = MockRunner::default().with("getfacl", &["-p", "/dev/kvm"], true, plain);
        assert!(!device_acl_grants(&runner, "/dev/kvm", "gregorio"));
    }

    #[test]
    fn live_kvm_requirement_matches_the_real_open_result() {
        // Live contract check (skipped only when this machine has no
        // virtualization at all): for the user actually running the tests,
        // the requirement must be Ok exactly when /dev/kvm really opens.
        // This is what stops the UI from ever claiming "KVM ready" for a
        // user who could not actually use it.
        let cpuinfo = read_cpuinfo();
        if cpu_virt_flag(&cpuinfo).is_none() || !Path::new("/dev/kvm").exists() {
            return;
        }
        let runner = crate::packages::SystemRunner;
        let user = current_session_user()
            .map(|(u, _)| u)
            .unwrap_or_else(|| "nobody".to_string());
        let req = kvm_requirement(&user, &runner, KvmProbe::ThisUser);
        if kvm_openable_now() {
            assert_eq!(req.state, ReqState::Ok);
        } else {
            assert_eq!(req.state, ReqState::Missing);
        }
    }

    #[test]
    fn picks_the_matching_release_asset_per_family_never_the_wrong_extension() {
        let assets = vec![
            (
                "winboat_0.9.2_amd64.deb".to_string(),
                "https://x/deb".to_string(),
            ),
            (
                "winboat-0.9.2-1.x86_64.rpm".to_string(),
                "https://x/rpm".to_string(),
            ),
            (
                "WinBoat-0.9.2-x86_64.AppImage".to_string(),
                "https://x/appimage".to_string(),
            ),
            (
                "winboat_0.9.2_arm64.deb".to_string(),
                "https://x/arm".to_string(),
            ),
        ];
        assert_eq!(
            pick_release_asset(&assets, DistroFamily::Ubuntu).map(|(n, _)| n.as_str()),
            Some("winboat_0.9.2_amd64.deb")
        );
        assert_eq!(
            pick_release_asset(&assets, DistroFamily::Fedora).map(|(n, _)| n.as_str()),
            Some("winboat-0.9.2-1.x86_64.rpm")
        );
        assert_eq!(
            pick_release_asset(&assets, DistroFamily::Arch).map(|(n, _)| n.as_str()),
            Some("WinBoat-0.9.2-x86_64.AppImage")
        );
    }

    #[test]
    fn missing_matching_asset_is_none_never_a_guess() {
        let assets = vec![("winboat_0.9.2_arm64.deb".to_string(), "u".to_string())];
        assert_eq!(pick_release_asset(&assets, DistroFamily::Ubuntu), None);
    }

    #[test]
    fn docker_desktop_is_reported_blocked_never_as_a_plain_missing_engine() {
        let runner = MockRunner::default()
            .with(
                "docker",
                &["--version"],
                true,
                "Docker version 27.3.1, build x",
            )
            .with("docker", &["context", "show"], true, "desktop-linux");
        let req = docker_engine_requirement(&runner);
        assert_eq!(req.state, ReqState::Blocked);
    }

    #[test]
    fn missing_docker_binary_is_reported_missing_not_blocked() {
        // Hermetic: relies only on the injected runner, never on whatever
        // `docker` (if any) happens to sit on the real `$PATH` of the
        // machine running the tests.
        let runner = MockRunner::default();
        let req = docker_engine_requirement(&runner);
        assert_eq!(req.state, ReqState::Missing);
    }

    #[test]
    fn plain_docker_engine_is_reported_ok_with_the_real_version_string() {
        let runner = MockRunner::default()
            .with(
                "docker",
                &["--version"],
                true,
                "Docker version 27.3.1, build ce12230",
            )
            .with("docker", &["context", "show"], true, "default");
        let req = docker_engine_requirement(&runner);
        assert_eq!(req.state, ReqState::Ok);
        assert!(req.detail.contains("27.3.1"));
    }

    #[test]
    fn snap_and_desktop_detection_are_pure_and_directly_testable() {
        assert!(docker_path_is_snap(Path::new(
            "/snap/docker/current/bin/docker"
        )));
        assert!(docker_path_is_snap(Path::new(
            "/var/lib/snapd/snap/docker/bin/docker"
        )));
        assert!(!docker_path_is_snap(Path::new("/usr/bin/docker")));
        assert!(is_docker_desktop(true, "default"));
        assert!(is_docker_desktop(false, "desktop-linux"));
        assert!(!is_docker_desktop(false, "default"));
    }

    #[test]
    fn compose_below_major_two_is_reported_missing_with_the_real_version_in_detail() {
        let runner = MockRunner::default().with(
            "docker",
            &["compose", "version"],
            true,
            "Docker Compose version v1.29.2\n",
        );
        let req = docker_compose_requirement(&runner);
        assert_eq!(req.state, ReqState::Missing);
        assert!(req.detail.contains('1'));
    }

    #[test]
    fn compose_v2_is_reported_ok() {
        let runner = MockRunner::default().with(
            "docker",
            &["compose", "version"],
            true,
            "Docker Compose version v2.29.7\n",
        );
        let req = docker_compose_requirement(&runner);
        assert_eq!(req.state, ReqState::Ok);
    }

    #[test]
    fn docker_daemon_not_responding_is_reported_missing_not_unknown() {
        let runner = MockRunner::default();
        let req = docker_daemon_requirement(&runner);
        assert_eq!(req.state, ReqState::Missing);
    }

    #[test]
    fn build_steps_skips_docker_entirely_when_already_ok() {
        let reqs = vec![
            requirement("kvm", ReqState::Ok, ""),
            requirement("ram", ReqState::Ok, ""),
            requirement("cpu", ReqState::Ok, ""),
            requirement("disk", ReqState::Ok, ""),
            requirement("docker_engine", ReqState::Ok, ""),
            requirement("docker_compose", ReqState::Ok, ""),
            requirement("docker_group", ReqState::Ok, ""),
            requirement("docker_daemon", ReqState::Ok, ""),
            requirement("freerdp", ReqState::Ok, ""),
            requirement("winboat", ReqState::Ok, ""),
        ];
        let pm = PackageManager::new(crate::packages::PackageManagerKind::Apt);
        let steps = build_steps(DistroFamily::Ubuntu, &reqs, &pm).unwrap();
        assert!(steps
            .iter()
            .all(|s| !matches!(s, Step::InstallDocker { .. })));
        assert!(steps
            .iter()
            .all(|s| !matches!(s, Step::AddDockerAptRepo { .. })));
        assert!(steps
            .iter()
            .all(|s| !matches!(s, Step::AddUserToDockerGroup)));
        assert!(steps
            .iter()
            .all(|s| !matches!(s, Step::InstallFreeRdp { .. })));
        assert!(steps
            .iter()
            .all(|s| !matches!(s, Step::DownloadWinboat | Step::InstallWinboat)));
        // Enable/start docker + group existence check are still safe,
        // idempotent baseline steps even when everything else is ready.
        assert!(steps.iter().any(|s| matches!(s, Step::EnableStartDocker)));
    }

    #[test]
    fn build_steps_plans_the_official_docker_repo_only_for_debian_family() {
        let reqs = vec![
            requirement("kvm", ReqState::Ok, ""),
            requirement("ram", ReqState::Ok, ""),
            requirement("cpu", ReqState::Ok, ""),
            requirement("disk", ReqState::Ok, ""),
            requirement("docker_engine", ReqState::Missing, ""),
            requirement("docker_compose", ReqState::Missing, ""),
            requirement("docker_group", ReqState::Missing, ""),
            requirement("docker_daemon", ReqState::Missing, ""),
            requirement("freerdp", ReqState::Missing, ""),
            requirement("winboat", ReqState::Missing, ""),
        ];
        let apt = PackageManager::new(crate::packages::PackageManagerKind::Apt);
        let steps = build_steps(DistroFamily::Ubuntu, &reqs, &apt).unwrap();
        let repo_index = steps
            .iter()
            .position(|s| matches!(s, Step::AddDockerAptRepo { slug: "ubuntu" }))
            .expect("the official Docker repository must be planned");
        let refresh_index = steps
            .iter()
            .position(|s| matches!(s, Step::RefreshIndexes { .. }))
            .expect("the new repository must be followed by an index refresh");
        let install_index = steps
            .iter()
            .position(|s| matches!(s, Step::InstallDocker { .. }))
            .expect("the install step must be planned");
        assert!(
            repo_index < refresh_index && refresh_index < install_index,
            "order must be add-repository, refresh-index, then install"
        );

        let arch_pm = PackageManager::new(crate::packages::PackageManagerKind::Pacman);
        let arch_steps = build_steps(DistroFamily::Arch, &reqs, &arch_pm).unwrap();
        assert!(arch_steps
            .iter()
            .all(|s| !matches!(s, Step::AddDockerAptRepo { .. } | Step::AddDockerFedoraRepo)));
        assert!(arch_steps.iter().any(|s| matches!(s, Step::InstallDocker { packages } if packages.contains(&"docker".to_string()))));
    }

    #[test]
    fn refresh_failure_is_rescued_only_when_every_package_is_really_resolvable() {
        // Real host finding: apt-get update exits 100 because of an
        // unrelated broken third-party repository, even though Docker's own
        // index was fetched fine. The step must be rescued in that case...
        let pm = PackageManager::new(crate::packages::PackageManagerKind::Apt);
        let packages = vec!["docker-ce".to_string(), "docker-compose-plugin".to_string()];
        let step = Step::RefreshIndexes {
            packages: packages.clone(),
        };
        let runner = MockRunner::default()
            .with("apt-get", &["update"], false, "Err:11 404 Not Found")
            .with(
                "apt-cache",
                &["policy", "docker-ce"],
                true,
                "docker-ce:\n  Candidate: 5:29.8.1\n",
            )
            .with(
                "apt-cache",
                &["policy", "docker-compose-plugin"],
                true,
                "docker-compose-plugin:\n  Candidate: 2.40.0\n",
            );
        let report = run_refresh_indexes(&runner, &step, &pm, &packages);
        assert!(
            report.ok,
            "an unrelated repository failure must be recovered"
        );
        assert!(report.stdout_tail.contains("non correlati"));

        // ...but never when a needed package really has no candidate: that
        // is a genuine failure and must stay one.
        let runner = MockRunner::default()
            .with("apt-get", &["update"], false, "Err:1 no network")
            .with(
                "apt-cache",
                &["policy", "docker-ce"],
                true,
                "docker-ce:\n  Candidate: (none)\n",
            )
            .with(
                "apt-cache",
                &["policy", "docker-compose-plugin"],
                true,
                "docker-compose-plugin:\n  Candidate: (none)\n",
            );
        let report = run_refresh_indexes(&runner, &step, &pm, &packages);
        assert!(!report.ok, "a real refresh failure must never be hidden");

        // A successful refresh is returned untouched.
        let runner = MockRunner::default().with("apt-get", &["update"], true, "Hit:1 ...");
        assert!(run_refresh_indexes(&runner, &step, &pm, &packages).ok);
    }

    #[test]
    fn build_steps_refuses_when_cpu_lacks_virtualization_entirely() {
        let reqs = vec![requirement(
            "kvm",
            ReqState::Missing,
            "La CPU non espone i flag vmx/svm: virtualizzazione hardware non disponibile.",
        )];
        let pm = PackageManager::new(crate::packages::PackageManagerKind::Apt);
        assert_eq!(
            build_steps(DistroFamily::Ubuntu, &reqs, &pm).unwrap_err(),
            "virtualizationUnavailable"
        );
    }

    #[test]
    fn status_reports_unsupported_distro_without_guessing_requirements() {
        let runner = MockRunner::default();
        let status = status_with(&runner);
        // On the machine that runs `cargo test`, family is whatever it is;
        // we only assert the invariant that must hold in every case: an
        // unsupported distro never carries any requirement or pending gap.
        if !status.supported {
            assert!(status.requirements.is_empty());
            assert_eq!(status.pending_count, 0);
        }
    }

    #[test]
    #[ignore = "reads this machine's real KVM/Docker/FreeRDP/WinBoat state; run manually with -- --ignored --nocapture"]
    fn live_winboat_status_against_the_real_system() {
        let status = status();
        eprintln!(
            "supported={} reason={:?} pm={:?} pending={} ready={} rebootSuggested={}",
            status.supported,
            status.unsupported_reason,
            status.package_manager,
            status.pending_count,
            status.ready,
            status.reboot_suggested
        );
        for requirement in &status.requirements {
            eprintln!(
                "  {:<15} {:?} — {}",
                requirement.id, requirement.state, requirement.detail
            );
        }
    }
}
