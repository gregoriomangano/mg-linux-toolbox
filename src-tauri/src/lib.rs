use crate::packages::CommandRunner;
use serde::{Deserialize, Serialize};
use std::process::Command;
use tauri::{Emitter, Manager};
mod advanced_controls;
// Compiled into the application so the Programs page can show the app
// catalog/status, and into the privileged apps helper so the same catalog
// executes KDE Connect native installs and the Flatpak runtime bootstrap;
// the application itself never installs a native package directly. Flatpak
// `--user` operations are the one exception (see `apps_ops.rs`): they need
// no root, so the application calls them directly, unprivileged.
mod apps_catalog;
#[allow(dead_code)]
mod apps_ops;
// Compiled into the application only so the helper can share the same validated
// operations; the application itself never writes and therefore never calls it.
#[allow(dead_code)]
mod advanced_ops;
// Compiled into the application only so the repository helper can share the
// same validated operations; the application itself never applies an edit
// directly, it always goes through the privileged helper (see apt_sources.rs).
#[allow(dead_code)]
mod apt_ops;
mod apt_sources;
mod browser_cleanup;
mod claude_auth;
mod cleanup;
mod codexbar_engine;
mod distro;
#[allow(dead_code)]
mod dns;
// Compiled into the application so the Programs page can show the plan, and
// into the privileged programs helper so the same catalog executes it; the
// application itself never installs anything directly.
#[allow(dead_code)]
mod gaming;
mod gpus;
mod hardware;
mod inhibit;
mod opencode_go;
mod packages;
mod performance;
#[allow(dead_code)]
mod performance_profiles;
mod power_profiles_daemon;
mod rss;
mod startup;
mod support;
mod tray;
mod updater;
// Compiled into the application so the Programs page can show the checklist,
// and into the privileged WinBoat helper so the same detection logic plans
// and executes the steps; the application itself never installs a package,
// grants a group or downloads a release directly.
#[allow(dead_code)]
mod winboat;
mod xdg_autostart;
use std::{
    collections::HashMap,
    fs,
    path::Path,
    sync::{Mutex, OnceLock},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

#[derive(Clone, Copy, Default)]
struct CpuTotals {
    user: u64,
    nice: u64,
    system: u64,
    idle: u64,
    iowait: u64,
    irq: u64,
    softirq: u64,
    steal: u64,
}
impl CpuTotals {
    fn total(self) -> u64 {
        self.user
            .saturating_add(self.nice)
            .saturating_add(self.system)
            .saturating_add(self.idle)
            .saturating_add(self.iowait)
            .saturating_add(self.irq)
            .saturating_add(self.softirq)
            .saturating_add(self.steal)
    }

    fn idle(self) -> u64 {
        self.idle.saturating_add(self.iowait)
    }
}

#[derive(Clone, Copy)]
struct ProcessSample {
    ticks: u64,
    start_time: u64,
}

struct CollectedProcess {
    pid: u32,
    sample: ProcessSample,
    name: String,
    memory_bytes: u64,
}

#[derive(Default)]
struct UsageHistory {
    cpu_totals: Option<CpuTotals>,
    processes: HashMap<u32, ProcessSample>,
}
#[derive(Serialize)]
struct CpuInfo {
    usage_percent: Option<f64>,
    frequency_mhz: Option<u64>,
    cores: u64,
    threads: u64,
    model_name: Option<String>,
}
#[derive(Serialize)]
struct MemoryInfo {
    used_bytes: u64,
    total_bytes: u64,
    used_percent: f64,
}
#[derive(Serialize, Clone)]
struct DiskInfo {
    name: String,
    device: String,
    filesystem: String,
    mountpoint: String,
    mounted: bool,
    removable: bool,
    total_bytes: u64,
    used_bytes: u64,
    free_bytes: u64,
    used_percent: f64,
}
#[derive(Serialize)]
struct NetworkInfo {
    connected: bool,
    interface: Option<String>,
    download_bytes_per_second: Option<u64>,
    upload_bytes_per_second: Option<u64>,
}
#[derive(Serialize)]
struct ProcessInfo {
    name: String,
    cpu_percent: Option<f64>,
    memory_bytes: u64,
}
#[derive(Serialize)]
struct SensorInfo {
    name: String,
    value: f64,
    unit: String,
}
#[derive(Serialize)]
struct PowerInfo {
    kind: String,
    name: String,
    status: Option<String>,
    percentage: Option<f64>,
}
#[derive(Serialize)]
struct GpuInfo {
    name: String,
    driver: Option<String>,
}
#[derive(Serialize)]
struct SystemSnapshot {
    distribution: Option<String>,
    kernel: Option<String>,
    uptime_seconds: Option<f64>,
    cpu: CpuInfo,
    memory: MemoryInfo,
    root_disk: Option<DiskInfo>,
    network: NetworkInfo,
    processes: Vec<ProcessInfo>,
    temperatures: Vec<SensorInfo>,
    fans: Vec<SensorInfo>,
    power: Vec<PowerInfo>,
    disks: Vec<DiskInfo>,
    gpu: Option<GpuInfo>,
    collected_at_ms: u128,
}

fn read(path: impl AsRef<Path>) -> Option<String> {
    fs::read_to_string(path).ok()
}
fn first_line(path: impl AsRef<Path>) -> Option<String> {
    read(path)?
        .lines()
        .next()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(String::from)
}
fn number<T: std::str::FromStr>(s: &str) -> Option<T> {
    s.trim().parse().ok()
}
fn bytes_from_kb(s: &str) -> u64 {
    number::<u64>(s).unwrap_or(0).saturating_mul(1024)
}
fn kilobytes_field(s: &str) -> u64 {
    s.split_whitespace()
        .next()
        .and_then(number::<u64>)
        .unwrap_or(0)
        .saturating_mul(1024)
}
fn os_info() -> (Option<String>, Option<String>) {
    let distro = read("/etc/os-release").and_then(|t| {
        t.lines()
            .find_map(|l| l.strip_prefix("PRETTY_NAME="))
            .map(|v| v.trim_matches('"').to_string())
    });
    (distro, first_line("/proc/sys/kernel/osrelease"))
}
fn uptime() -> Option<f64> {
    first_line("/proc/uptime")?
        .split_whitespace()
        .next()
        .and_then(number)
}
fn cpu_totals() -> Option<CpuTotals> {
    let v: Vec<u64> = first_line("/proc/stat")?
        .split_whitespace()
        .skip(1)
        .filter_map(number)
        .collect();
    Some(CpuTotals {
        user: v.first().copied().unwrap_or(0),
        nice: v.get(1).copied().unwrap_or(0),
        system: v.get(2).copied().unwrap_or(0),
        idle: v.get(3).copied().unwrap_or(0),
        iowait: v.get(4).copied().unwrap_or(0),
        irq: v.get(5).copied().unwrap_or(0),
        softirq: v.get(6).copied().unwrap_or(0),
        steal: v.get(7).copied().unwrap_or(0),
    })
}
fn cpu_usage_from_delta(previous: CpuTotals, current: CpuTotals) -> Option<f64> {
    let previous_total = previous.total();
    let current_total = current.total();
    let previous_idle = previous.idle();
    let current_idle = current.idle();
    if current_total <= previous_total || current_idle < previous_idle {
        return None;
    }
    let total_delta = current_total - previous_total;
    let idle_delta = current_idle - previous_idle;
    if total_delta == 0 || idle_delta > total_delta {
        return None;
    }
    Some(((total_delta - idle_delta) as f64 / total_delta as f64 * 100.0).clamp(0.0, 100.0))
}

fn process_usage_from_delta(
    previous_ticks: u64,
    current_ticks: u64,
    previous_total: u64,
    current_total: u64,
    logical_cpus: u64,
) -> Option<f64> {
    if current_ticks < previous_ticks || current_total <= previous_total || logical_cpus == 0 {
        return None;
    }
    let process_delta = current_ticks - previous_ticks;
    let total_delta = current_total - previous_total;
    if total_delta == 0 {
        return None;
    }
    Some(
        (process_delta as f64 / total_delta as f64 * logical_cpus as f64 * 100.0)
            .clamp(0.0, logical_cpus as f64 * 100.0),
    )
}

fn cpu_info(usage_percent: Option<f64>) -> CpuInfo {
    let ci = read("/proc/cpuinfo").unwrap_or_default();
    let threads = ci.lines().filter(|l| l.starts_with("processor")).count() as u64;
    let cores = ci
        .lines()
        .find_map(|l| {
            l.strip_prefix("cpu cores")
                .and_then(|v| v.split(':').nth(1))
        })
        .and_then(number)
        .unwrap_or(threads);
    let frequency_mhz = first_line("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
        .and_then(|value| number::<u64>(&value))
        .map(|v| v / 1000)
        .or_else(|| {
            ci.lines()
                .find_map(|l| l.strip_prefix("cpu MHz").and_then(|v| v.split(':').nth(1)))
                .and_then(number::<f64>)
                .map(|v| v.round() as u64)
        });
    CpuInfo {
        usage_percent,
        frequency_mhz,
        cores,
        threads,
        model_name: hardware::cpu_model(&ci),
    }
}
fn memory_info() -> MemoryInfo {
    let t = read("/proc/meminfo").unwrap_or_default();
    let (mut total, mut available) = (0, 0);
    for l in t.lines() {
        let mut p = l.split_whitespace();
        match p.next() {
            Some("MemTotal:") => total = p.next().map(bytes_from_kb).unwrap_or(0),
            Some("MemAvailable:") => available = p.next().map(bytes_from_kb).unwrap_or(0),
            _ => {}
        }
    }
    let used = total.saturating_sub(available);
    MemoryInfo {
        used_bytes: used,
        total_bytes: total,
        used_percent: if total == 0 {
            0.0
        } else {
            used as f64 / total as f64 * 100.0
        },
    }
}
fn unescape(s: &str) -> String {
    s.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\134", "\\")
}
fn stat_disk(device: String, filesystem: String, mountpoint: String) -> Option<DiskInfo> {
    let path = std::ffi::CString::new(mountpoint.as_bytes()).ok()?;
    let mut st = unsafe { std::mem::zeroed::<libc::statvfs>() };
    if unsafe { libc::statvfs(path.as_ptr(), &mut st) } != 0 {
        return None;
    }
    let total = st.f_blocks as u64 * st.f_frsize as u64;
    let free = st.f_bavail as u64 * st.f_frsize as u64;
    let used = total.saturating_sub(st.f_bfree as u64 * st.f_frsize as u64);
    Some(DiskInfo {
        name: if mountpoint == "/" {
            "Linux".into()
        } else {
            mountpoint.clone()
        },
        device,
        filesystem,
        mountpoint,
        mounted: true,
        removable: false,
        total_bytes: total,
        used_bytes: used,
        free_bytes: free,
        used_percent: if total == 0 {
            0.0
        } else {
            used as f64 / total as f64 * 100.0
        },
    })
}
#[derive(Debug, Deserialize)]
struct LsblkResponse {
    #[serde(default)]
    blockdevices: Vec<LsblkDevice>,
}
#[derive(Debug, Deserialize)]
struct LsblkDevice {
    path: Option<String>,
    name: Option<String>,
    #[serde(rename = "type")]
    kind: Option<String>,
    fstype: Option<String>,
    label: Option<String>,
    #[serde(default)]
    mountpoints: Vec<Option<String>>,
    size: Option<u64>,
    fsused: Option<u64>,
    fsavail: Option<u64>,
    model: Option<String>,
    tran: Option<String>,
    rm: Option<bool>,
    hotplug: Option<bool>,
    #[serde(default)]
    children: Vec<LsblkDevice>,
}
fn storage_filesystem_is_technical(value: &str) -> bool {
    matches!(
        value.to_ascii_lowercase().as_str(),
        "" | "none"
            | "swap"
            | "crypto_luks"
            | "luks"
            | "lvm2_member"
            | "tmpfs"
            | "devtmpfs"
            | "proc"
            | "procfs"
            | "sysfs"
            | "cgroup"
            | "cgroup2"
            | "overlay"
            | "squashfs"
            | "fuse.lxcfs"
            | "lxcfs"
            | "efivarfs"
            | "debugfs"
            | "tracefs"
            | "pstore"
            | "configfs"
            | "fusectl"
            | "devpts"
            | "mqueue"
            | "ramfs"
            | "hugetlbfs"
    )
}
fn storage_mount_is_hidden(mountpoint: &str) -> bool {
    mountpoint.starts_with("/proc")
        || mountpoint.starts_with("/sys")
        || mountpoint.starts_with("/run")
        || mountpoint.starts_with("/dev")
        || mountpoint.starts_with("/snap/")
        || mountpoint.starts_with("/var/lib/incus/")
        || mountpoint.starts_with("/var/lib/lxd/")
        || mountpoint.starts_with("/var/lib/snapd/")
}
fn storage_device_is_hidden(device: &LsblkDevice) -> bool {
    let path = device.path.as_deref().unwrap_or("").to_ascii_lowercase();
    let name = device.name.as_deref().unwrap_or("").to_ascii_lowercase();
    let mapper_name = device.name.as_deref().and_then(|name| {
        read(format!("/sys/class/block/{name}/dm/name"))
            .map(|value| value.trim().to_ascii_lowercase())
    });
    path.contains("/mapper/default-containers_")
        || path.contains("/mapper/default-incusthinpool")
        || name.starts_with("default-containers_")
        || name.starts_with("default-incusthinpool")
        || mapper_name.as_deref().is_some_and(|value| {
            value.starts_with("default-containers_") || value.starts_with("default-incusthinpool")
        })
}
fn storage_mountpoint(device: &LsblkDevice) -> Option<String> {
    device
        .mountpoints
        .iter()
        .flatten()
        .find(|mountpoint| !storage_mount_is_hidden(mountpoint))
        .cloned()
}
fn storage_display_name(device: &LsblkDevice, mountpoint: Option<&str>) -> String {
    if mountpoint == Some("/") {
        return "Linux".into();
    }
    if let Some(label) = device.label.as_deref().filter(|v| !v.trim().is_empty()) {
        return label.to_string();
    }
    if device.rm == Some(true)
        || device.hotplug == Some(true)
        || device.tran.as_deref() == Some("usb")
    {
        return format!("USB {}", device.model.as_deref().unwrap_or("disco"));
    }
    match device.fstype.as_deref() {
        Some("ntfs") => "Partizione NTFS".into(),
        Some("exfat") => "Disco dati".into(),
        _ => device.name.clone().unwrap_or_else(|| "Volume".into()),
    }
}
fn lsblk_disk(device: &LsblkDevice) -> Option<DiskInfo> {
    let filesystem = device.fstype.as_deref().unwrap_or("").trim();
    if storage_filesystem_is_technical(filesystem)
        || device.kind.as_deref() == Some("loop")
        || storage_device_is_hidden(device)
    {
        return None;
    }
    let mountpoint = storage_mountpoint(device);
    let size = device.size.unwrap_or(0);
    let significant = mountpoint.is_some() || size >= 1024 * 1024 * 1024;
    if !significant || (mountpoint.is_none() && size < 1024 * 1024 * 1024) {
        return None;
    }
    let mountpoint_value = mountpoint.clone().unwrap_or_default();
    if mountpoint_value == "/boot" || mountpoint_value == "/boot/efi" || size < 1024 * 1024 * 1024 {
        return None;
    }
    let total = size;
    let used = device.fsused.unwrap_or(0);
    let free = device.fsavail.unwrap_or_else(|| total.saturating_sub(used));
    Some(DiskInfo {
        name: storage_display_name(device, mountpoint.as_deref()),
        device: device
            .path
            .clone()
            .or_else(|| device.name.as_ref().map(|name| format!("/dev/{name}")))
            .unwrap_or_default(),
        filesystem: filesystem.to_string(),
        mountpoint: mountpoint_value,
        mounted: mountpoint.is_some(),
        removable: device.rm == Some(true)
            || device.hotplug == Some(true)
            || device.tran.as_deref() == Some("usb"),
        total_bytes: total,
        used_bytes: used,
        free_bytes: free,
        used_percent: if total == 0 {
            0.0
        } else {
            used as f64 / total as f64 * 100.0
        },
    })
}
fn collect_lsblk(devices: &[LsblkDevice], out: &mut Vec<DiskInfo>) {
    for device in devices {
        if let Some(disk) = lsblk_disk(device) {
            if !out.iter().any(|item| item.device == disk.device) {
                out.push(disk);
            }
        }
        collect_lsblk(&device.children, out);
    }
}
fn disks() -> Vec<DiskInfo> {
    if let Ok(output) = Command::new("lsblk")
        .args([
            "--json", "--bytes", "--paths", "--output",
            "NAME,PATH,TYPE,FSTYPE,LABEL,PARTLABEL,MOUNTPOINTS,SIZE,FSUSED,FSAVAIL,MODEL,VENDOR,TRAN,RM,HOTPLUG",
        ])
        .output()
    {
        if output.status.success() {
            if let Ok(parsed) = serde_json::from_slice::<LsblkResponse>(&output.stdout) {
                let mut out = Vec::new();
                collect_lsblk(&parsed.blockdevices, &mut out);
                if !out.is_empty() {
                    out.sort_by_key(|disk| if disk.mountpoint == "/" { 0 } else if disk.removable { 2 } else { 1 });
                    return out;
                }
            }
        }
    }
    disks_from_mountinfo()
}
fn disks_from_mountinfo() -> Vec<DiskInfo> {
    let mut out = Vec::new();
    if let Some(text) = read("/proc/self/mountinfo") {
        for line in text.lines() {
            let Some((left, right)) = line.split_once(" - ") else {
                continue;
            };
            let lf: Vec<&str> = left.split_whitespace().collect();
            let rf: Vec<&str> = right.split_whitespace().collect();
            if lf.len() < 6 || rf.len() < 2 {
                continue;
            }
            let mountpoint = unescape(lf[4]);
            let filesystem = rf[0].to_string();
            let device = unescape(rf[1]);
            if storage_filesystem_is_technical(&filesystem)
                || storage_mount_is_hidden(&mountpoint)
                || mountpoint == "/boot"
                || mountpoint == "/boot/efi"
            {
                continue;
            }
            if let Some(d) = stat_disk(device, filesystem, mountpoint) {
                if !out.iter().any(|x: &DiskInfo| x.mountpoint == d.mountpoint) {
                    out.push(d)
                }
            }
        }
    }
    out
}
fn gpu() -> Option<GpuInfo> {
    let pci_database = read("/usr/share/hwdata/pci.ids")
        .or_else(|| read("/usr/share/misc/pci.ids"))
        .unwrap_or_default();
    let entries = fs::read_dir("/sys/class/drm").ok()?;
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if !name.starts_with("card") || name.contains('-') {
            continue;
        }
        let base = entry.path().join("device");
        let vendor = first_line(base.join("vendor"))?;
        let driver = fs::read_link(base.join("driver"))
            .ok()
            .and_then(|p| p.file_name().map(|v| v.to_string_lossy().to_string()));
        let vendor_id = hardware::parse_hex_id(&vendor);
        let device_id = first_line(base.join("device")).and_then(|v| hardware::parse_hex_id(&v));
        let vendor_name = match vendor.as_str() {
            "0x1002" => "AMD GPU",
            "0x10de" => "NVIDIA GPU",
            "0x8086" => "Intel GPU",
            _ => "GPU",
        };
        return Some(GpuInfo {
            name: vendor_id
                .zip(device_id)
                .and_then(|(vendor, device)| hardware::pci_name(&pci_database, vendor, device))
                .unwrap_or_else(|| vendor_name.to_string()),
            driver,
        });
    }
    None
}
fn network() -> NetworkInfo {
    let (mut active, mut connected) = (None, false);
    if let Some(t) = read("/proc/net/route") {
        for l in t.lines().skip(1) {
            let p: Vec<&str> = l.split_whitespace().collect();
            if p.len() > 2 && p[1] == "00000000" {
                active = Some(p[0].to_string());
                connected = true;
                break;
            }
        }
    }
    if active.is_none() {
        if let Some(t) = read("/proc/net/dev") {
            active = t
                .lines()
                .skip(2)
                .filter_map(|l| l.split_once(':').map(|(i, _)| i.trim().to_string()))
                .find(|i| i != "lo");
            connected = active.is_some()
        }
    }
    let (rx, tx) = active.as_deref().and_then(network_bytes).unwrap_or((0, 0));
    static PREVIOUS: OnceLock<Mutex<Option<(u64, u64, Instant)>>> = OnceLock::new();
    let now = Instant::now();
    let (download, upload) =
        if let Ok(mut previous) = PREVIOUS.get_or_init(|| Mutex::new(None)).lock() {
            let result = previous
                .map(|(old_rx, old_tx, old_time)| {
                    let seconds = now.duration_since(old_time).as_secs_f64();
                    if seconds > 0.0 {
                        (
                            Some((rx.saturating_sub(old_rx) as f64 / seconds) as u64),
                            Some((tx.saturating_sub(old_tx) as f64 / seconds) as u64),
                        )
                    } else {
                        (None, None)
                    }
                })
                .unwrap_or((None, None));
            *previous = Some((rx, tx, now));
            result
        } else {
            (None, None)
        };
    NetworkInfo {
        connected,
        interface: active,
        download_bytes_per_second: download,
        upload_bytes_per_second: upload,
    }
}

fn network_bytes(interface: &str) -> Option<(u64, u64)> {
    read("/proc/net/dev")?.lines().skip(2).find_map(|line| {
        let (name, values) = line.split_once(':')?;
        if name.trim() != interface {
            return None;
        }
        let values: Vec<u64> = values.split_whitespace().filter_map(number).collect();
        Some((values.first().copied()?, values.get(8).copied()?))
    })
}
fn collect_processes() -> Vec<CollectedProcess> {
    let mut list = Vec::new();
    let Ok(entries) = fs::read_dir("/proc") else {
        return list;
    };
    for e in entries.flatten() {
        let pid_text = e.file_name().to_string_lossy().to_string();
        let Some(pid) = number::<u32>(&pid_text) else {
            continue;
        };
        let Some(stat) = read(e.path().join("stat")) else {
            continue;
        };
        let Some((_, rest)) = stat.split_once(") ") else {
            continue;
        };
        let f: Vec<&str> = rest.split_whitespace().collect();
        if f.len() < 20 {
            continue;
        }
        let ticks = number::<u64>(f[11]).unwrap_or(0) + number::<u64>(f[12]).unwrap_or(0);
        let start_time = number::<u64>(f[19]).unwrap_or(0);
        let memory = read(e.path().join("status"))
            .and_then(|s| {
                s.lines()
                    .find_map(|l| l.strip_prefix("VmRSS:"))
                    .map(kilobytes_field)
            })
            .unwrap_or(0);
        let name = read(e.path().join("comm"))
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or(pid_text);
        list.push(CollectedProcess {
            pid,
            sample: ProcessSample { ticks, start_time },
            name,
            memory_bytes: memory,
        })
    }
    list
}

/// Aggregate CPU usage only, without the per-process `/proc` scan. Used whenever
/// the process ranking is not currently shown, so the expensive per-PID work in
/// `collect_processes()` is skipped while the aggregate figure (used by both
/// Panoramica and the Prestazioni cross-reference) keeps updating every tick.
fn cpu_usage_live(cpu_totals: Option<CpuTotals>) -> Option<f64> {
    let mut history = usage_history().lock().ok()?;
    let cpu_usage = history
        .cpu_totals
        .zip(cpu_totals)
        .and_then(|(previous, current)| cpu_usage_from_delta(previous, current));
    history.cpu_totals = cpu_totals;
    cpu_usage
}

fn process_list_live(
    cpu_totals: Option<CpuTotals>,
    logical_cpus: u64,
) -> (Option<f64>, Vec<ProcessInfo>) {
    let collected = collect_processes();
    let next_processes = collected
        .iter()
        .map(|process| (process.pid, process.sample))
        .collect::<HashMap<_, _>>();
    let Ok(mut history) = usage_history().lock() else {
        return (
            None,
            collected
                .into_iter()
                .map(|process| ProcessInfo {
                    name: process.name,
                    cpu_percent: None,
                    memory_bytes: process.memory_bytes,
                })
                .take(5)
                .collect(),
        );
    };
    let previous_totals = history.cpu_totals;
    let cpu_usage = previous_totals
        .zip(cpu_totals)
        .and_then(|(previous, current)| cpu_usage_from_delta(previous, current));
    let previous_total = previous_totals.map(CpuTotals::total);
    let current_total = cpu_totals.map(CpuTotals::total);
    let mut list = collected
        .into_iter()
        .map(|process| {
            let cpu_percent = history
                .processes
                .get(&process.pid)
                .filter(|previous| previous.start_time == process.sample.start_time)
                .and_then(|previous| {
                    previous_total
                        .zip(current_total)
                        .and_then(|(previous_total, current_total)| {
                            process_usage_from_delta(
                                previous.ticks,
                                process.sample.ticks,
                                previous_total,
                                current_total,
                                logical_cpus,
                            )
                        })
                });
            ProcessInfo {
                name: process.name,
                cpu_percent,
                memory_bytes: process.memory_bytes,
            }
        })
        .collect::<Vec<_>>();
    history.cpu_totals = cpu_totals;
    history.processes = next_processes;
    list.sort_by(|a, b| {
        b.cpu_percent
            .unwrap_or_default()
            .partial_cmp(&a.cpu_percent.unwrap_or_default())
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(b.memory_bytes.cmp(&a.memory_bytes))
    });
    list.truncate(5);
    (cpu_usage, list)
}

fn usage_history() -> &'static Mutex<UsageHistory> {
    static HISTORY: OnceLock<Mutex<UsageHistory>> = OnceLock::new();
    HISTORY.get_or_init(|| Mutex::new(UsageHistory::default()))
}
fn sensors() -> (Vec<SensorInfo>, Vec<SensorInfo>, Vec<PowerInfo>) {
    let (mut temps, mut fans, mut power) = (Vec::new(), Vec::new(), Vec::new());
    if let Ok(hwmons) = fs::read_dir("/sys/class/hwmon") {
        for hw in hwmons.flatten() {
            let base = hw.path();
            let chip = first_line(base.join("name")).unwrap_or_default();
            if let Ok(files) = fs::read_dir(&base) {
                for f in files.flatten() {
                    let n = f.file_name().to_string_lossy().to_string();
                    if !n.ends_with("_input") || !n.starts_with("fan") {
                        continue;
                    }
                    let Some(v) = first_line(f.path()).and_then(|value| number::<f64>(&value))
                    else {
                        continue;
                    };
                    if v > 0.0 {
                        fans.push(SensorInfo {
                            name: if chip.is_empty() {
                                "Ventola".into()
                            } else {
                                chip.clone()
                            },
                            value: v,
                            unit: "RPM".into(),
                        });
                    }
                }
            }
        }
    }
    if let Some(value) = hardware::cpu_temperature() {
        temps.push(SensorInfo {
            name: "CPU".into(),
            value,
            unit: "°C".into(),
        });
    }
    if temps.is_empty() {
        if let Ok(zones) = fs::read_dir("/sys/class/thermal") {
            for zone in zones.flatten() {
                let kind = first_line(zone.path().join("type")).unwrap_or_default();
                let kind_lower = kind.to_ascii_lowercase();
                let is_cpu = ["cpu", "package", "core", "x86_pkg"]
                    .iter()
                    .any(|name| kind_lower.contains(name));
                let Some(value) =
                    first_line(zone.path().join("temp")).and_then(|raw| number::<f64>(&raw))
                else {
                    continue;
                };
                if is_cpu && (-100_000.0..150_000.0).contains(&value) {
                    temps.push(SensorInfo {
                        name: "CPU".into(),
                        value: value / 1000.0,
                        unit: "°C".into(),
                    });
                    break;
                }
            }
        }
    }
    if let Ok(supplies) = fs::read_dir("/sys/class/power_supply") {
        for s in supplies.flatten() {
            let base = s.path();
            let kind = first_line(base.join("type")).unwrap_or_default();
            if !kind.is_empty() {
                power.push(PowerInfo {
                    kind,
                    name: s.file_name().to_string_lossy().to_string(),
                    status: first_line(base.join("status")),
                    percentage: first_line(base.join("capacity")).and_then(|value| number(&value)),
                })
            }
        }
    }
    (temps, fans, power)
}
#[tauri::command]
fn get_system_snapshot(include_processes: bool) -> SystemSnapshot {
    let (distribution, kernel) = os_info();
    let uptime_seconds = uptime();
    let current_cpu_totals = cpu_totals();
    let threads = read("/proc/cpuinfo")
        .map(|cpuinfo| {
            cpuinfo
                .lines()
                .filter(|line| line.starts_with("processor"))
                .count() as u64
        })
        .unwrap_or(1)
        .max(1);
    let (cpu_usage, processes) = if include_processes {
        process_list_live(current_cpu_totals, threads)
    } else {
        (cpu_usage_live(current_cpu_totals), Vec::new())
    };
    let all_disks = disks();
    let root_disk = all_disks.iter().find(|d| d.mountpoint == "/").cloned();
    let (temperatures, fans, power) = sensors();
    let collected_at_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_millis();
    SystemSnapshot {
        distribution,
        kernel,
        uptime_seconds,
        cpu: cpu_info(cpu_usage),
        memory: memory_info(),
        root_disk,
        network: network(),
        processes,
        temperatures,
        fans,
        power,
        disks: all_disks,
        gpu: gpu(),
        collected_at_ms,
    }
}
/// The single AI command: one complete CodexBar engine snapshot, already
/// mapped to the fixed M.G view model. There is no per-provider command.
#[tauri::command]
async fn get_ai_usage(app: tauri::AppHandle) -> Result<codexbar_engine::AiUsageSnapshot, String> {
    let config_dir = app
        .path()
        .app_config_dir()
        .map_err(|_| "AI engine configuration unavailable".to_string())?;
    let resource_dir = app.path().resource_dir().ok();
    tauri::async_runtime::spawn_blocking(move || {
        codexbar_engine::snapshot(&config_dir, resource_dir.as_deref())
    })
    .await
    .map_err(|_| "AI engine snapshot failed".to_string())
}
#[tauri::command]
async fn get_claude_auth_status() -> Result<claude_auth::ClaudeAuthStatus, String> {
    tauri::async_runtime::spawn_blocking(claude_auth::status)
        .await
        .map_err(|_| "claude_auth_status_failed".to_string())
}
#[tauri::command]
async fn reconnect_claude_code() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(claude_auth::start_reconnect)
        .await
        .map_err(|_| "claude_login_terminal_failed".to_string())?
}
#[tauri::command]
async fn get_network_latency() -> Result<Option<f64>, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let routes = read("/proc/net/route").unwrap_or_default();
        hardware::executable_in_path(&["ping"])
            .as_deref()
            .and_then(|ping| hardware::measure_ping(&routes, ping))
    })
    .await
    .map_err(|_| "Latency measurement failed".to_string())
}
#[tauri::command]
async fn get_performance_snapshot() -> Result<performance::PerformanceSnapshot, String> {
    tauri::async_runtime::spawn_blocking(performance::collect_performance_snapshot)
        .await
        .map_err(|_| "Performance capability detection failed".to_string())
}
#[tauri::command]
async fn get_advanced_controls() -> Result<Vec<advanced_controls::AdvancedControl>, String> {
    tauri::async_runtime::spawn_blocking(advanced_controls::detect_all)
        .await
        .map_err(|_| "advanced_detection_failed".to_string())
}
#[tauri::command]
async fn set_advanced_control(
    id: String,
    enabled: bool,
) -> Result<Vec<advanced_controls::AdvancedControl>, String> {
    tauri::async_runtime::spawn_blocking(move || advanced_controls::set_control(&id, enabled))
        .await
        .map_err(|_| "advanced_set_failed".to_string())?
}
#[tauri::command]
fn get_inhibit_status(state: tauri::State<'_, inhibit::InhibitState>) -> inhibit::InhibitStatus {
    state.status()
}
#[tauri::command]
fn set_inhibit_active(
    active: bool,
    state: tauri::State<'_, inhibit::InhibitState>,
) -> Result<inhibit::InhibitStatus, String> {
    state.set_active(active)
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PerformanceControlStatus {
    /// Which engine is in charge of this profile: "ppd" (power-profiles-daemon owns it),
    /// "sysfs" (our own planner/helper owns it), or "blocked" (a different, unsupported
    /// power manager is active and we stay hands-off).
    backend: &'static str,
    available: bool,
    active: bool,
    helper_available: bool,
    snapshot_available: bool,
    external_manager: Option<String>,
}

#[tauri::command]
async fn get_performance_control_status(
    profile: performance_profiles::Profile,
) -> Result<PerformanceControlStatus, String> {
    tauri::async_runtime::spawn_blocking(move || {
        if let Ok(ppd) = power_profiles_daemon::status() {
            let name = power_profiles_daemon::profile_name(profile);
            let available = ppd.profiles.iter().any(|p| p.name == name);
            return Ok(PerformanceControlStatus {
                backend: "ppd",
                available,
                active: available && ppd.active_profile == name,
                helper_available: true,
                snapshot_available: false,
                external_manager: None,
            });
        }
        let policies = performance_profiles::scan(Path::new("/sys/devices/system/cpu/cpufreq"))?;
        let manager = performance_profiles::active_external_manager();
        let uid = unsafe { libc::getuid() };
        let snapshot_available = Path::new(performance_profiles::STATE_DIR)
            .join(format!("{uid}.json"))
            .exists();
        let plan = performance_profiles::plan(profile, &policies, manager.is_some());
        Ok(PerformanceControlStatus {
            backend: if manager.is_some() {
                "blocked"
            } else {
                "sysfs"
            },
            available: plan.available,
            active: plan.available && plan.changes.is_empty(),
            helper_available: Path::new(performance_profiles::HELPER_PATH).is_file(),
            snapshot_available,
            external_manager: manager,
        })
    })
    .await
    .map_err(|_| "performance_status_failed".to_string())?
}
pub(crate) fn privileged_helper_args(args: &[String]) -> Result<(), String> {
    let refs = args.iter().map(String::as_str).collect::<Vec<_>>();
    privileged_helper(&refs)
}
/// Same pkexec contract as [`privileged_helper`], but for the separate
/// repository helper/polkit action, so a bug in one privileged surface
/// (performance sysfs writes vs. `/etc/apt` edits) can never authorize the
/// other.
pub(crate) fn privileged_repository_helper_args(args: &[String]) -> Result<(), String> {
    if !Path::new(apt_ops::REPOSITORY_HELPER_PATH).is_file() {
        return Err("helper_missing".into());
    }
    let status = std::process::Command::new("pkexec")
        .arg("--disable-internal-agent")
        .arg(apt_ops::REPOSITORY_HELPER_PATH)
        .args(args)
        .status()
        .map_err(|_| "polkit_unavailable")?;
    match status.code() {
        Some(0) => Ok(()),
        Some(126) => Err("authorization_cancelled".into()),
        Some(127) => Err("authorization_unavailable".into()),
        _ => Err("apply_failed".into()),
    }
}
/// Read-only Gaming plan for the Programs page: distribution, every GPU,
/// each component's real state and the changes a confirmation would apply.
#[tauri::command]
async fn get_gaming_status() -> Result<gaming::GamingPlan, String> {
    tauri::async_runtime::spawn_blocking(gaming::snapshot)
        .await
        .map_err(|_| "gaming_status_failed".to_string())
}
/// Applies the approved gaming set through the privileged programs helper,
/// which re-derives the whole plan itself, then returns the fresh status so
/// the UI shows what is really installed.
#[tauri::command]
async fn prepare_gaming(app: tauri::AppHandle) -> Result<gaming::InstallReport, String> {
    tauri::async_runtime::spawn_blocking(move || {
        run_programs_helper(&app, &["programs-prepare-gaming".to_string()])
    })
    .await
    .map_err(|_| "gaming_prepare_failed".to_string())?
}
/// Read-only status of the separate GeForce NOW cloud client.
#[tauri::command]
async fn get_geforce_now_status() -> Result<gaming::GamingPlan, String> {
    tauri::async_runtime::spawn_blocking(gaming::geforce_now_snapshot)
        .await
        .map_err(|_| "geforce_now_status_failed".to_string())
}
/// GeForce NOW is a cloud client, deliberately separate from the local
/// gaming set and installable on any GPU vendor.
#[tauri::command]
async fn install_geforce_now(app: tauri::AppHandle) -> Result<gaming::InstallReport, String> {
    tauri::async_runtime::spawn_blocking(move || {
        run_programs_helper(&app, &["programs-install-geforce-now".to_string()])
    })
    .await
    .map_err(|_| "geforce_now_install_failed".to_string())?
}
/// Read-only NetworkManager state for the active connection. This never reads
/// or writes resolv.conf and is safe to call without privileges.
#[tauri::command]
async fn get_dns_status() -> Result<dns::DnsStatus, String> {
    tauri::async_runtime::spawn_blocking(dns::status)
        .await
        .map_err(|_| "dns_status_failed".to_string())
}

/// Applies one closed DNS preset through the dedicated allow-listed helper.
/// The provider enum is the complete input surface: no command, address,
/// profile name or path can be supplied by the frontend.
#[tauri::command]
async fn set_dns_provider(
    app: tauri::AppHandle,
    provider: dns::DnsProvider,
) -> Result<dns::DnsStatus, String> {
    tauri::async_runtime::spawn_blocking(move || run_dns_helper(&app, provider))
        .await
        .map_err(|_| "dns_apply_failed".to_string())?
}

fn run_dns_helper(
    _app: &tauri::AppHandle,
    provider: dns::DnsProvider,
) -> Result<dns::DnsStatus, String> {
    use std::io::{BufRead, Read};
    if !Path::new(dns::DNS_HELPER_PATH).is_file() {
        return Err("helper_missing".into());
    }
    let mut child = std::process::Command::new("pkexec")
        .arg("--disable-internal-agent")
        .arg(dns::DNS_HELPER_PATH)
        .arg("set-dns-provider")
        .arg(provider.as_str())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|_| "polkit_unavailable".to_string())?;
    let stream = child.stdout.take().ok_or("helper_protocol")?;
    let mut stderr = child.stderr.take().ok_or("helper_protocol")?;
    let mut final_status = None;
    let mut error_code = None;
    for line in std::io::BufReader::new(stream)
        .lines()
        .map_while(Result::ok)
    {
        let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) else {
            continue;
        };
        match value.get("kind").and_then(|kind| kind.as_str()) {
            Some("status") => {
                final_status = value.get("status").and_then(|status| {
                    serde_json::from_value::<dns::DnsStatus>(status.clone()).ok()
                });
            }
            Some("error") => {
                error_code = value
                    .get("code")
                    .and_then(|code| code.as_str())
                    .map(str::to_string);
            }
            _ => {}
        }
    }
    let status = child.wait().map_err(|_| "helper_wait_failed".to_string())?;
    let mut stderr_text = String::new();
    let _ = stderr.read_to_string(&mut stderr_text);
    if !stderr_text.trim().is_empty() {
        eprintln!("APP HELPER STDERR: {}", stderr_text.trim());
    }
    match status.code() {
        Some(126) => return Err("authorization_cancelled".into()),
        Some(127) => return Err("authorization_unavailable".into()),
        _ => {}
    }
    final_status.ok_or_else(|| error_code.unwrap_or_else(|| "helper_protocol".into()))
}
/// Same pkexec contract as the other helpers, but for the third, separate
/// privileged surface: installing the approved programs can never authorize
/// a sysfs write or an `/etc/apt` edit, and neither of those can install a
/// package.
///
/// Unlike the other helpers this one *reads back* the helper's JSON output:
/// every step is forwarded to the UI as it happens (`gaming-progress`) and
/// the final report -- with exit codes, stderr and the verification result --
/// is returned to the caller. A bare "it failed" is never enough to explain
/// what happened.
pub(crate) fn run_programs_helper(
    app: &tauri::AppHandle,
    args: &[String],
) -> Result<gaming::InstallReport, String> {
    use std::io::BufRead;
    if !Path::new(gaming::PROGRAMS_HELPER_PATH).is_file() {
        return Err("helper_missing".into());
    }
    let mut child = std::process::Command::new("pkexec")
        .arg("--disable-internal-agent")
        .arg(gaming::PROGRAMS_HELPER_PATH)
        .args(args)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|_| "polkit_unavailable".to_string())?;
    let stream = child.stdout.take().ok_or("helper_protocol")?;
    let mut final_report: Option<gaming::InstallReport> = None;
    let mut error_code: Option<String> = None;
    for line in std::io::BufReader::new(stream)
        .lines()
        .map_while(Result::ok)
    {
        let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) else {
            continue;
        };
        match value.get("kind").and_then(|kind| kind.as_str()) {
            Some("step") => {
                if let Some(step) = value.get("step").and_then(|step| {
                    serde_json::from_value::<gaming::StepReport>(step.clone()).ok()
                }) {
                    let _ = app.emit("gaming-progress", &step);
                }
            }
            Some("report") => {
                if let Some(report) = value.get("report").and_then(|report| {
                    serde_json::from_value::<gaming::InstallReport>(report.clone()).ok()
                }) {
                    final_report = Some(report);
                }
            }
            Some("error") => {
                error_code = value
                    .get("code")
                    .and_then(|code| code.as_str())
                    .map(str::to_string);
            }
            _ => {}
        }
    }
    let status = child.wait().map_err(|_| "helper_wait_failed".to_string())?;
    match status.code() {
        Some(126) => return Err("authorization_cancelled".into()),
        Some(127) => return Err("authorization_unavailable".into()),
        _ => {}
    }
    if let Some(report) = final_report {
        if !report.ok
            && args
                .first()
                .is_some_and(|operation| operation == "apps-remove")
        {
            for step in report.steps.iter().filter(|step| !step.ok) {
                eprintln!(
                    "APP_REMOVE_FAILED app={} method={} command={} args={:?} exit_code={} stderr=\"{}\"",
                    args.get(1).map(String::as_str).unwrap_or("unknown"),
                    args.get(3).or_else(|| args.get(2)).map(String::as_str).unwrap_or("unknown"),
                    step.operation.split_whitespace().next().unwrap_or("unknown"),
                    args,
                    step.exit_code,
                    step.stderr_tail
                );
            }
        }
        return Ok(report);
    }
    if let Some(code) = error_code {
        return Err(code);
    }
    Err("helper_protocol".into())
}

/// Read-only WinBoat status for the Programs page: every requirement, in
/// the fixed checklist order, never privileged.
#[tauri::command]
async fn get_winboat_status() -> Result<winboat::WinboatStatus, String> {
    tauri::async_runtime::spawn_blocking(winboat::status)
        .await
        .map_err(|_| "winboat_status_failed".to_string())
}
/// Same pkexec/streaming-progress contract as [`run_programs_helper`], but
/// for the separate WinBoat helper/polkit action: installing Gaming
/// components can never authorize a Docker/KVM group grant or a WinBoat
/// download, and neither of those can install a gaming package.
fn run_winboat_helper(
    app: &tauri::AppHandle,
    args: &[String],
) -> Result<winboat::WinboatReport, String> {
    use std::io::BufRead;
    if !Path::new(winboat::WINBOAT_HELPER_PATH).is_file() {
        return Err("helper_missing".into());
    }
    let mut child = std::process::Command::new("pkexec")
        .arg("--disable-internal-agent")
        .arg(winboat::WINBOAT_HELPER_PATH)
        .args(args)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|_| "polkit_unavailable".to_string())?;
    let stream = child.stdout.take().ok_or("helper_protocol")?;
    let mut final_report: Option<winboat::WinboatReport> = None;
    let mut error_code: Option<String> = None;
    for line in std::io::BufReader::new(stream)
        .lines()
        .map_while(Result::ok)
    {
        let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) else {
            continue;
        };
        match value.get("kind").and_then(|kind| kind.as_str()) {
            Some("step") => {
                if let Some(step) = value.get("step").and_then(|step| {
                    serde_json::from_value::<winboat::StepReport>(step.clone()).ok()
                }) {
                    let _ = app.emit("winboat-progress", &step);
                }
            }
            Some("report") => {
                if let Some(report) = value.get("report").and_then(|report| {
                    serde_json::from_value::<winboat::WinboatReport>(report.clone()).ok()
                }) {
                    final_report = Some(report);
                }
            }
            Some("error") => {
                error_code = value
                    .get("code")
                    .and_then(|code| code.as_str())
                    .map(str::to_string);
            }
            _ => {}
        }
    }
    let status = child.wait().map_err(|_| "helper_wait_failed".to_string())?;
    match status.code() {
        Some(126) => return Err("authorization_cancelled".into()),
        Some(127) => return Err("authorization_unavailable".into()),
        _ => {}
    }
    if let Some(report) = final_report {
        return Ok(report);
    }
    if let Some(code) = error_code {
        return Err(code);
    }
    Err("helper_protocol".into())
}
/// Applies the one closed WinBoat operation through the privileged helper,
/// which re-derives every requirement and step itself, then returns the
/// fresh report so the UI shows what is really ready.
#[tauri::command]
async fn prepare_winboat(app: tauri::AppHandle) -> Result<winboat::WinboatReport, String> {
    tauri::async_runtime::spawn_blocking(move || {
        run_winboat_helper(&app, &["winboat-prepare".to_string()])
    })
    .await
    .map_err(|_| "winboat_prepare_failed".to_string())?
}
/// Launches the real, already-installed WinBoat binary this module itself
/// resolves -- never a path supplied by the frontend -- detached from M.G
/// so it keeps running after this call returns.
#[tauri::command]
async fn open_winboat() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(|| {
        let path =
            winboat::winboat_binary_path().ok_or_else(|| "winboat_not_installed".to_string())?;
        std::process::Command::new(path)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    })
    .await
    .map_err(|_| "open_winboat_failed".to_string())?
}
/// One unprivileged, read-only snapshot for all five Programs app cards.
/// Individual unavailable sources are represented inside the snapshot, so
/// Flatpak/Snap absence never makes the whole page fail.
#[tauri::command]
async fn get_apps_snapshot() -> Result<apps_catalog::AppsSnapshot, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let release = fs::read_to_string("/etc/os-release")
            .map(|text| distro::parse_os_release(&text))
            .unwrap_or_default();
        let runner = packages::SystemRunner;
        apps_catalog::snapshot(&release, &runner)
    })
    .await
    .map_err(|_| "apps_snapshot_failed".to_string())
}
/// Same pkexec/streaming-progress contract as [`run_winboat_helper`], but
/// for the separate apps helper/polkit action: installing/removing a
/// catalog app can never authorize a sysfs write, an `/etc/apt` edit, a
/// DNS change, a Gaming install or a WinBoat step, and none of those can
/// install or remove a catalog app.
fn run_apps_helper(
    app: &tauri::AppHandle,
    args: &[String],
) -> Result<apps_ops::AppsReport, String> {
    use std::io::BufRead;
    if !Path::new(apps_catalog::APPS_HELPER_PATH).is_file() {
        return Err("helper_missing".into());
    }
    let mut child = std::process::Command::new("pkexec")
        .arg("--disable-internal-agent")
        .arg(apps_catalog::APPS_HELPER_PATH)
        .args(args)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|_| "polkit_unavailable".to_string())?;
    eprintln!("APP HELPER COMMAND executable=pkexec args={:?}", args);
    let stream = child.stdout.take().ok_or("helper_protocol")?;
    let mut final_report: Option<apps_ops::AppsReport> = None;
    let mut error_code: Option<String> = None;
    for line in std::io::BufReader::new(stream)
        .lines()
        .map_while(Result::ok)
    {
        let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) else {
            continue;
        };
        match value.get("kind").and_then(|kind| kind.as_str()) {
            Some("step") => {
                if let Some(step) = value.get("step").and_then(|step| {
                    serde_json::from_value::<apps_ops::StepReport>(step.clone()).ok()
                }) {
                    let _ = app.emit("apps-progress", &step);
                }
            }
            Some("report") => {
                if let Some(report) = value.get("report").and_then(|report| {
                    serde_json::from_value::<apps_ops::AppsReport>(report.clone()).ok()
                }) {
                    final_report = Some(report);
                }
            }
            Some("error") => {
                error_code = value
                    .get("code")
                    .and_then(|code| code.as_str())
                    .map(str::to_string);
            }
            _ => {}
        }
    }
    let status = child.wait().map_err(|_| "helper_wait_failed".to_string())?;
    match status.code() {
        Some(126) => return Err("authorization_cancelled".into()),
        Some(127) => return Err("authorization_unavailable".into()),
        _ => {}
    }
    if let Some(report) = final_report {
        return Ok(report);
    }
    if let Some(code) = error_code {
        return Err(code);
    }
    Err("helper_protocol".into())
}
/// Read-only, unprivileged snapshot of the shared install infrastructure:
/// whether Flatpak/Flathub and Snap are actually usable right now, the
/// detected distribution, and the exact bootstrap path (`flatpakPlan`/
/// `snapPlan`) each install button should follow. The frontend only ever
/// displays these fixed strings and picks the matching confirmation text
/// for them -- it never derives any of this distro logic itself.
#[tauri::command]
async fn get_apps_environment() -> Result<apps_catalog::AppsEnvironment, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let release = fs::read_to_string("/etc/os-release")
            .map(|text| distro::parse_os_release(&text))
            .unwrap_or_default();
        let runner = packages::SystemRunner;
        apps_catalog::environment(&release, &runner)
    })
    .await
    .map_err(|_| "apps_environment_failed".to_string())
}
/// Installs one catalog app through the one method the frontend named --
/// parsed against the closed catalog first, so an unrecognised id or
/// method is rejected before anything runs.
///
/// Flatpak never goes through the privileged helper for the app install
/// itself: it needs no root, so once the Flatpak runtime is present (or
/// has just been bootstrapped through the one privileged, single-confirm
/// step) the app install runs directly, unprivileged, as the real desktop
/// user -- never as root, which would otherwise write to root's own
/// Flatpak user data instead of the person using the app.
///
/// Snap always needs root, so both its runtime bootstrap and the app
/// install itself go through the privileged helper. The exact bootstrap
/// operation is chosen here from the same environment plan the frontend
/// already showed a matching confirmation for (`snapPlan`), and is always
/// re-derived server-side -- never trusted from the caller -- so a stale
/// or fabricated frontend state can only ever trigger one of the fixed,
/// allow-listed bootstrap operations, never an arbitrary command.
#[tauri::command]
async fn install_app(
    app: tauri::AppHandle,
    id: String,
    method: String,
) -> Result<apps_ops::AppsReport, String> {
    let app_id = apps_catalog::AppId::parse(&id).ok_or_else(|| "invalid_app".to_string())?;
    let method =
        apps_catalog::InstallMethod::parse(&method).ok_or_else(|| "invalid_method".to_string())?;
    if !apps_catalog::install_method_allowed(app_id, method) {
        return Err("invalid_method".to_string());
    }
    tauri::async_runtime::spawn_blocking(move || -> Result<apps_ops::AppsReport, String> {
        let runner = packages::SystemRunner;
        if method == apps_catalog::InstallMethod::Flatpak {
            if !apps_catalog::flatpak_present(&runner) {
                run_apps_helper(&app, &["apps-bootstrap-flatpak".to_string()])?;
            }
            return Ok(apps_ops::install_flatpak_user(app_id));
        }
        if method == apps_catalog::InstallMethod::Snap && !apps_catalog::snap_ready(&runner) {
            let release = fs::read_to_string("/etc/os-release")
                .map(|text| distro::parse_os_release(&text))
                .unwrap_or_default();
            let env = apps_catalog::environment(&release, &runner);
            match env.snap_plan {
                "ready" => {}
                "needs_activation" => {
                    run_apps_helper(&app, &["apps-snap-activate".to_string()])?;
                }
                "needs_bootstrap" => {
                    run_apps_helper(&app, &["apps-bootstrap-snap".to_string()])?;
                }
                "mint_nosnap" => {
                    run_apps_helper(&app, &["apps-bootstrap-snap-mint".to_string()])?;
                }
                "opensuse_repo" => {
                    run_apps_helper(&app, &["apps-bootstrap-snap-opensuse".to_string()])?;
                }
                _ => return Err("snap_unsupported".to_string()),
            }
        }
        run_apps_helper(
            &app,
            &[
                "apps-install".to_string(),
                app_id.as_str().to_string(),
                method.as_str().to_string(),
            ],
        )
    })
    .await
    .map_err(|_| "apps_install_failed".to_string())?
}
/// Connects one of the two optional, documented Snap interfaces (Upscayl's
/// removable-media, Ferdium's camera/audio-record) -- never granted
/// automatically at install time, only after this explicit, separate call.
#[tauri::command]
async fn grant_app_snap_permission(
    app: tauri::AppHandle,
    id: String,
    permission: String,
) -> Result<apps_ops::AppsReport, String> {
    let app_id = apps_catalog::AppId::parse(&id).ok_or_else(|| "invalid_app".to_string())?;
    tauri::async_runtime::spawn_blocking(move || -> Result<apps_ops::AppsReport, String> {
        run_apps_helper(
            &app,
            &[
                "apps-snap-permission".to_string(),
                app_id.as_str().to_string(),
                permission,
            ],
        )
    })
    .await
    .map_err(|_| "apps_permission_failed".to_string())?
}
/// Removes one catalog app through the real method it is installed with --
/// the frontend supplies the `via` value the last status read reported,
/// re-validated here, never trusted blindly. Flatpak removals run
/// unprivileged for the same reason Flatpak installs do.
#[tauri::command]
async fn remove_app(
    app: tauri::AppHandle,
    id: String,
    via: String,
) -> Result<apps_ops::AppsReport, String> {
    let app_id = apps_catalog::AppId::parse(&id).ok_or_else(|| "invalid_app".to_string())?;
    let via =
        apps_catalog::InstalledVia::parse(&via).ok_or_else(|| "invalid_method".to_string())?;
    if !apps_catalog::removal_via_allowed(app_id, via) {
        return Err("invalid_method".to_string());
    }
    tauri::async_runtime::spawn_blocking(move || -> Result<apps_ops::AppsReport, String> {
        if via == apps_catalog::InstalledVia::Flatpak {
            let runner = packages::SystemRunner;
            let scope = ["--user", "--system"]
                .into_iter()
                .find(|scope| {
                    runner
                        .run(
                            "flatpak",
                            &[
                                scope,
                                "info",
                                apps_catalog::flatpak_id(app_id).unwrap_or(""),
                            ],
                        )
                        .is_some_and(|output| output.success)
                })
                .unwrap_or("--user");
            return Ok(apps_ops::remove_flatpak_scope(app_id, scope));
        }
        run_apps_helper(
            &app,
            &[
                "apps-remove".to_string(),
                app_id.as_str().to_string(),
                via.as_str().to_string(),
            ],
        )
    })
    .await
    .map_err(|_| "apps_remove_failed".to_string())?
}
/// Launches an already-installed catalog app through the one real,
/// re-detected way it is installed -- never a path or command supplied by
/// the frontend. When an app is installed through more than one format,
/// `primary_installed` picks a fixed, documented priority (Flatpak, then
/// native, then Snap); removing a specific format is always an explicit,
/// separate choice (see `remove_app`).
#[tauri::command]
async fn open_app(id: String) -> Result<(), String> {
    let app_id = apps_catalog::AppId::parse(&id).ok_or_else(|| "invalid_app".to_string())?;
    tauri::async_runtime::spawn_blocking(move || {
        let runner = packages::SystemRunner;
        let release = fs::read_to_string("/etc/os-release")
            .map(|text| distro::parse_os_release(&text))
            .unwrap_or_default();
        let family = distro::family(&release);
        let installed_all = apps_catalog::detect_installed(app_id, family, &runner);
        let installed = apps_catalog::primary_installed(&installed_all)
            .ok_or_else(|| "app_not_installed".to_string())?;
        let spawn = |mut command: std::process::Command| {
            command
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .spawn()
                .map(|_| ())
                .map_err(|e| e.to_string())
        };
        match apps_catalog::InstalledVia::parse(&installed.via) {
            Some(apps_catalog::InstalledVia::Flatpak) => {
                let flatpak_id = apps_catalog::flatpak_id(app_id).ok_or("not_flatpak")?;
                let mut command = std::process::Command::new("flatpak");
                if let Some(scope) = installed.scope.as_deref() {
                    command.arg(if scope == "system" {
                        "--system"
                    } else {
                        "--user"
                    });
                }
                command.arg("run").arg(flatpak_id);
                spawn(command)
            }
            Some(apps_catalog::InstalledVia::Snap) => {
                // `snap run <name>` is snapd's own universal launcher for
                // any installed snap: verified by construction (we only
                // reach this branch once detection confirmed the snap is
                // really installed), never a guessed desktop command.
                let name = apps_catalog::snap_name(app_id).ok_or("not_snap")?;
                let mut command = std::process::Command::new("snap");
                command.arg("run").arg(name);
                spawn(command)
            }
            _ => {
                let candidates = apps_catalog::launch_binary(app_id);
                let binary = hardware::executable_in_path(candidates)
                    .ok_or_else(|| "app_binary_not_found".to_string())?;
                spawn(std::process::Command::new(binary))
            }
        }
    })
    .await
    .map_err(|_| "open_app_failed".to_string())?
}
/// Asks the user's own login session to reboot, through the same
/// `systemd-logind` action (and its own, separate polkit policy) any
/// desktop "Restart" button uses -- never through our own privileged
/// helper, since a reboot needs no `/etc/apt`, sysfs, DNS, Gaming or
/// WinBoat privilege at all.
#[tauri::command]
async fn reboot_now() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(|| {
        std::process::Command::new("systemctl")
            .arg("reboot")
            .status()
            .map_err(|_| "reboot_unavailable".to_string())
            .and_then(|status| {
                status
                    .success()
                    .then_some(())
                    .ok_or_else(|| "reboot_failed".to_string())
            })
    })
    .await
    .map_err(|_| "reboot_failed".to_string())?
}
#[tauri::command]
async fn list_apt_repositories() -> Result<Vec<apt_sources::AptRepository>, String> {
    tauri::async_runtime::spawn_blocking(apt_sources::list_repositories)
        .await
        .map_err(|_| "apt_list_failed".to_string())
}
#[tauri::command]
async fn set_apt_repository_enabled(
    id: String,
    enabled: bool,
    expected_revision: String,
) -> Result<Vec<apt_sources::AptRepository>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        apt_sources::set_enabled(&id, enabled, &expected_revision)
    })
    .await
    .map_err(|_| "apt_set_failed".to_string())?
}
#[tauri::command]
async fn restore_apt_repository(
    id: String,
    expected_revision: String,
) -> Result<Vec<apt_sources::AptRepository>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        apt_sources::restore_last_backup(&id, &expected_revision)
    })
    .await
    .map_err(|_| "apt_restore_failed".to_string())?
}
fn privileged_helper(args: &[&str]) -> Result<(), String> {
    if !Path::new(performance_profiles::HELPER_PATH).is_file() {
        return Err("helper_missing".into());
    }
    let status = std::process::Command::new("pkexec")
        .arg("--disable-internal-agent")
        .arg(performance_profiles::HELPER_PATH)
        .args(args)
        .status()
        .map_err(|_| "polkit_unavailable")?;
    match status.code() {
        Some(0) => Ok(()),
        Some(126) => Err("authorization_cancelled".into()),
        Some(127) => Err("authorization_unavailable".into()),
        _ => Err("apply_failed".into()),
    }
}
#[tauri::command]
async fn apply_performance_profile(
    profile: performance_profiles::Profile,
) -> Result<performance::PerformanceSnapshot, String> {
    tauri::async_runtime::spawn_blocking(move || {
        // power-profiles-daemon, when present, is the single source of truth for the
        // active profile: we write ActiveProfile over D-Bus and never touch sysfs or the
        // privileged helper, so the two engines can never fight over the same knobs.
        if power_profiles_daemon::detect() {
            let name = power_profiles_daemon::profile_name(profile);
            power_profiles_daemon::set_active_profile(name)?;
            return Ok(performance::collect_performance_snapshot());
        }
        let arg = match profile {
            performance_profiles::Profile::Performance => "performance",
            performance_profiles::Profile::Balanced => "balanced",
            performance_profiles::Profile::Saving => "saving",
        };
        privileged_helper(&["apply", arg])?;
        Ok(performance::collect_performance_snapshot())
    })
    .await
    .map_err(|_| "apply_failed".to_string())?
}
#[tauri::command]
async fn restore_previous_performance_state() -> Result<performance::PerformanceSnapshot, String> {
    tauri::async_runtime::spawn_blocking(|| {
        privileged_helper(&["restore"])?;
        Ok(performance::collect_performance_snapshot())
    })
    .await
    .map_err(|_| "restore_failed".to_string())?
}
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // Must be the first plugin registered: a second launch attempt is
        // answered by focusing the already-running window instead of
        // starting a parallel instance (one tray icon, one set of timers).
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            tray::show_main_window(app);
        }))
        // Official Tauri autostart plugin: on Linux this is the user's
        // standard `~/.config/autostart` entry, with `--hidden` as the
        // marker that the launch came from the login session.
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![startup::HIDDEN_ARG]),
        ))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let config_dir = app.path().app_config_dir()?;
            let startup_settings = startup::load(&config_dir);
            app.manage(rss::FeedStore::new(config_dir.join("feeds.json")));
            app.manage(inhibit::InhibitState::default());
            // Resident mode first: the close-to-tray behaviour below is only
            // enabled when the tray really exists.
            let tray_available = tray::init(app.handle());
            app.manage(tray::TrayState {
                available: tray_available,
            });
            // Case C: started by the login session with "start hidden" on.
            // If the tray could not be created, show the window anyway so
            // the process is never silently unreachable.
            let started_hidden = std::env::args().any(|argument| argument == startup::HIDDEN_ARG)
                && startup_settings.start_hidden;
            if let Some(window) = app.get_webview_window("main") {
                if !(started_hidden && tray_available) {
                    window.show()?;
                }
            }
            Ok(())
        })
        // The window starts hidden (see tauri.conf.json) so the startup
        // decision above happens before anything is ever painted. When the
        // tray is available the X button hides the window and M.G stays
        // resident; otherwise it keeps Tauri's normal close behaviour.
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let tray_available = window
                    .app_handle()
                    .try_state::<tray::TrayState>()
                    .map(|state| state.available)
                    .unwrap_or(false);
                if tray_available {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_system_snapshot,
            get_ai_usage,
            get_claude_auth_status,
            reconnect_claude_code,
            get_network_latency,
            get_inhibit_status,
            set_inhibit_active,
            get_performance_snapshot,
            get_advanced_controls,
            set_advanced_control,
            list_apt_repositories,
            set_apt_repository_enabled,
            restore_apt_repository,
            get_performance_control_status,
            apply_performance_profile,
            restore_previous_performance_state,
            rss::list_feeds,
            rss::save_feed,
            rss::delete_feed,
            rss::refresh_feeds,
            rss::cached_news,
            rss::open_external_url,
            updater::get_update_status,
            updater::check_for_update,
            updater::download_and_install_update,
            xdg_autostart::list_autostart_entries,
            xdg_autostart::set_autostart_entry_enabled,
            cleanup::scan_cleanup_targets,
            cleanup::clean_selected_cleanup_targets,
            startup::get_startup_settings,
            support::get_support_nudge,
            support::dismiss_support_nudge,
            startup::set_launch_at_login,
            startup::set_start_hidden,
            get_gaming_status,
            get_geforce_now_status,
            get_dns_status,
            prepare_gaming,
            install_geforce_now,
            set_dns_provider,
            get_winboat_status,
            prepare_winboat,
            open_winboat,
            get_apps_snapshot,
            get_apps_environment,
            install_app,
            grant_app_snap_permission,
            remove_app,
            open_app,
            reboot_now
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod live_usage_tests {
    use super::*;

    fn totals(user: u64, system: u64, idle: u64) -> CpuTotals {
        CpuTotals {
            user,
            system,
            idle,
            ..CpuTotals::default()
        }
    }

    #[test]
    fn cpu_usage_uses_the_interval_between_two_proc_stat_samples() {
        let previous = totals(100, 50, 850);
        let current = totals(150, 75, 875);
        assert_eq!(cpu_usage_from_delta(previous, current), Some(75.0));
    }

    #[test]
    fn cpu_usage_rejects_a_zero_or_backwards_counter_delta() {
        let sample = totals(100, 50, 850);
        assert_eq!(cpu_usage_from_delta(sample, sample), None);
        assert_eq!(cpu_usage_from_delta(sample, totals(90, 50, 850)), None);
    }

    #[test]
    fn process_usage_is_based_on_recent_ticks_without_assuming_user_hz() {
        assert_eq!(
            process_usage_from_delta(100, 110, 1_000, 1_100, 8),
            Some(80.0)
        );
        assert_eq!(
            process_usage_from_delta(100, 100, 1_000, 1_100, 8),
            Some(0.0)
        );
        assert_eq!(process_usage_from_delta(110, 100, 1_000, 1_100, 8), None);
    }
}

#[cfg(test)]
mod storage_tests {
    use super::*;

    fn device(fstype: &str, path: &str, mountpoint: Option<&str>, size: u64) -> LsblkDevice {
        LsblkDevice {
            path: Some(path.into()),
            name: Some(path.trim_start_matches("/dev/").into()),
            kind: Some("part".into()),
            fstype: Some(fstype.into()),
            label: None,
            mountpoints: vec![mountpoint.map(str::to_string)],
            size: Some(size),
            fsused: Some(size / 2),
            fsavail: Some(size / 2),
            model: None,
            tran: None,
            rm: Some(false),
            hotplug: Some(false),
            children: Vec::new(),
        }
    }

    #[test]
    fn root_ext4_is_named_linux_and_kept_first() {
        let root = device(
            "ext4",
            "/dev/mapper/ubuntu--vg-ubuntu--lv",
            Some("/"),
            2_000_000_000,
        );
        let mut output = Vec::new();
        collect_lsblk(&[root], &mut output);
        assert_eq!(output[0].name, "Linux");
        assert_eq!(output[0].mountpoint, "/");
    }

    #[test]
    fn loop_squashfs_snap_and_incus_mounts_are_excluded() {
        assert!(storage_filesystem_is_technical("squashfs"));
        assert!(storage_mount_is_hidden("/snap/core/current"));
        assert!(storage_mount_is_hidden(
            "/var/lib/incus/storage-pools/default"
        ));
        let incus_volume = device(
            "ext4",
            "/dev/mapper/default-containers_debian----test",
            None,
            70_000_000_000,
        );
        assert!(storage_device_is_hidden(&incus_volume));
        let loop_device = device(
            "squashfs",
            "/dev/loop0",
            Some("/snap/core/current"),
            20_000_000_000,
        );
        let mut output = Vec::new();
        collect_lsblk(&[loop_device], &mut output);
        assert!(output.is_empty());
    }

    #[test]
    fn encrypted_luks_and_lvm_container_devices_are_excluded() {
        assert!(storage_filesystem_is_technical("crypto_LUKS"));
        assert!(storage_filesystem_is_technical("LVM2_member"));
        let luks = device("crypto_LUKS", "/dev/nvme0n1p3", None, 2_000_000_000_000);
        let lvm = device(
            "LVM2_member",
            "/dev/mapper/dm_crypt-0",
            None,
            2_000_000_000_000,
        );
        let mut output = Vec::new();
        collect_lsblk(&[luks, lvm], &mut output);
        assert!(output.is_empty());
    }

    #[test]
    fn efi_and_boot_partitions_are_excluded_but_ntfs_and_usb_are_kept() {
        let efi = device("vfat", "/dev/nvme0n1p1", Some("/boot/efi"), 300_000_000);
        let ntfs = device(
            "ntfs",
            "/dev/nvme1n1p1",
            Some("/mnt/windows"),
            100_000_000_000,
        );
        let mut usb = device("exfat", "/dev/sdb1", None, 32_000_000_000);
        usb.label = Some("Kingston".into());
        usb.tran = Some("usb".into());
        usb.rm = Some(true);
        let mut output = Vec::new();
        collect_lsblk(&[efi, ntfs, usb], &mut output);
        assert_eq!(output.len(), 2);
        assert_eq!(output[0].name, "Partizione NTFS");
        assert_eq!(output[1].name, "Kingston");
        assert!(!output[1].mounted);
    }
}
