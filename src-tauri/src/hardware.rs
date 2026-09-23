use std::{
    fs,
    net::Ipv4Addr,
    path::Path,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

fn first_line(path: impl AsRef<Path>) -> Option<String> {
    fs::read_to_string(path)
        .ok()?
        .lines()
        .next()
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .map(str::to_owned)
}

/// Shared CPU temperature reader used by both Panoramica and Prestazioni so the
/// two modules never disagree and never scan /sys/class/hwmon twice per tick.
/// Prefers a sensor whose own label names the CPU package (more reliable on
/// multi-sensor chips) over one where only the chip name suggests it.
pub fn cpu_temperature() -> Option<f64> {
    let cpu_chip_names = ["k10temp", "coretemp", "zenpower", "cpu", "package", "pkg"];
    let cpu_label_names = ["cpu", "package", "core", "tctl", "tdie"];
    let mut best: Option<(u8, f64)> = None;
    let hwmons = fs::read_dir("/sys/class/hwmon").ok()?;
    for hwmon in hwmons.flatten() {
        let base = hwmon.path();
        let chip = first_line(base.join("name")).unwrap_or_default();
        let chip_lower = chip.to_ascii_lowercase();
        let chip_is_cpu = cpu_chip_names.iter().any(|name| chip_lower.contains(name));
        let Ok(files) = fs::read_dir(&base) else {
            continue;
        };
        for file in files.flatten() {
            let name = file.file_name().to_string_lossy().to_string();
            if !name.starts_with("temp") || !name.ends_with("_input") {
                continue;
            }
            let Some(value) = first_line(file.path()).and_then(|v| v.parse::<f64>().ok()) else {
                continue;
            };
            if !(-100_000.0..150_000.0).contains(&value) {
                continue;
            }
            let stem = name.trim_end_matches("_input");
            let label = first_line(base.join(format!("{stem}_label"))).unwrap_or_default();
            let label_lower = label.to_ascii_lowercase();
            let label_is_cpu = cpu_label_names
                .iter()
                .any(|name| label_lower.contains(name));
            if !chip_is_cpu && !label_is_cpu {
                continue;
            }
            let priority = if label_is_cpu { 0 } else { 1 };
            if best.is_none_or(|(current, _)| priority < current) {
                best = Some((priority, value / 1000.0));
            }
        }
    }
    best.map(|(_, value)| value)
}

/// First executable found in `PATH` among `candidates`, checked for the
/// execute bit. Shared by the application and the programs helper (which
/// never compiles `lib.rs`), so both resolve tools the same way.
pub fn executable_in_path(candidates: &[&str]) -> Option<std::path::PathBuf> {
    use std::os::unix::fs::PermissionsExt;
    std::env::var_os("PATH")
        .into_iter()
        .flat_map(|paths| std::env::split_paths(&paths).collect::<Vec<_>>())
        .flat_map(|dir| candidates.iter().map(move |name| dir.join(name)))
        .find(|path| {
            path.metadata()
                .is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        })
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Route {
    pub interface: String,
    pub gateway: String,
}

pub fn cpu_model(cpuinfo: &str) -> Option<String> {
    cpuinfo.lines().find_map(|line| {
        let (key, value) = line.split_once(':')?;
        (key.trim() == "model name")
            .then(|| value.trim().to_string())
            .filter(|value| !value.is_empty())
    })
}

pub fn pci_name(database: &str, vendor: u16, device: u16) -> Option<String> {
    let vendor_key = format!("{vendor:04x}");
    let device_key = format!("{device:04x}");
    let mut inside_vendor = false;
    for line in database.lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if !line.starts_with('\t') {
            inside_vendor = line
                .split_whitespace()
                .next()
                .is_some_and(|value| value.eq_ignore_ascii_case(&vendor_key));
            continue;
        }
        if inside_vendor && line.starts_with('\t') && !line.starts_with("\t\t") {
            let trimmed = line.trim_start_matches('\t');
            if let Some(rest) = trimmed.strip_prefix(&device_key) {
                let name = rest.trim();
                if !name.is_empty() {
                    return Some(name.to_string());
                }
            }
        }
    }
    None
}

pub fn parse_hex_id(value: &str) -> Option<u16> {
    u16::from_str_radix(value.trim().trim_start_matches("0x"), 16).ok()
}

pub fn default_route(routes: &str) -> Option<Route> {
    routes.lines().skip(1).find_map(|line| {
        let fields: Vec<&str> = line.split_whitespace().collect();
        if fields.len() < 4 || fields[1] != "00000000" {
            return None;
        }
        let flags = u16::from_str_radix(fields[3], 16).ok()?;
        if flags & 0x2 == 0 {
            return None;
        }
        let raw = u32::from_str_radix(fields[2], 16).ok()?;
        let gateway = Ipv4Addr::from(raw.to_le_bytes()).to_string();
        Some(Route {
            interface: fields[0].to_string(),
            gateway,
        })
    })
}

pub fn parse_ping(output: &str) -> Option<f64> {
    let value = output
        .split_whitespace()
        .find_map(|part| part.strip_prefix("time="))?
        .parse::<f64>()
        .ok()?;
    (value.is_finite() && value >= 0.0).then_some(value)
}

pub fn measure_ping(routes: &str, ping_binary: &Path) -> Option<f64> {
    let route = default_route(routes)?;
    let mut child = Command::new(ping_binary)
        .args(["-n", "-c", "1", "-W", "1", &route.gateway])
        .env("LC_ALL", "C")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let deadline = Instant::now() + Duration::from_millis(1500);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                if !status.success() {
                    return None;
                }
                let output = child.wait_with_output().ok()?;
                return parse_ping(&String::from_utf8_lossy(&output.stdout));
            }
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(25)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_real_cpu_model_without_touching_other_fields() {
        let fixture =
            "processor : 0\nmodel name : AMD Ryzen 9 5950X 16-Core Processor\ncpu MHz : 3400\n";
        assert_eq!(
            cpu_model(fixture).as_deref(),
            Some("AMD Ryzen 9 5950X 16-Core Processor")
        );
        assert_eq!(cpu_model("processor : 0\n"), None);
    }

    #[test]
    fn resolves_pci_device_name_from_database() {
        let fixture = "1002  Advanced Micro Devices, Inc. [AMD/ATI]\n\t73df  Navi 22 [Radeon RX 6700/6700 XT]\n\t\t1eae 6710  Example board\n8086  Intel Corporation\n\t1234  Other\n";
        assert_eq!(
            pci_name(fixture, 0x1002, 0x73df).as_deref(),
            Some("Navi 22 [Radeon RX 6700/6700 XT]")
        );
        assert_eq!(pci_name(fixture, 0x10de, 0xffff), None);
    }

    #[test]
    fn parses_default_route_gateway_from_little_endian_hex() {
        let fixture = "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\nenp42s0 00000000 0101A8C0 0003 0 0 100 00000000 0 0 0\n";
        let route = default_route(fixture).expect("default route");
        assert_eq!(route.interface, "enp42s0");
        assert_eq!(route.gateway, "192.168.1.1");
    }

    #[test]
    fn accepts_only_finite_non_negative_ping_latency() {
        assert_eq!(
            parse_ping("64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=0.864 ms\n"),
            Some(0.864)
        );
        assert_eq!(parse_ping("Destination Host Unreachable"), None);
        assert_eq!(parse_ping("time=-1 ms"), None);
    }
}
