//! Multi-GPU detection for the Programs page.
//!
//! A normal machine can have several GPUs (an Intel/AMD integrated GPU plus
//! an NVIDIA or AMD discrete one), and both matter for the 32-bit graphics
//! stack, so this module never collapses the system to a single "the GPU"
//! string. Everything is read from `/sys/class/drm` and is testable through
//! an injected sysfs root, exactly like the rest of the hardware code.
use serde::Serialize;
use std::{collections::BTreeSet, fs, path::Path};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum GpuVendor {
    Amd,
    Intel,
    Nvidia,
    Other,
}

impl GpuVendor {
    pub fn label(self) -> &'static str {
        match self {
            GpuVendor::Amd => "amd",
            GpuVendor::Intel => "intel",
            GpuVendor::Nvidia => "nvidia",
            GpuVendor::Other => "other",
        }
    }

    fn from_pci_vendor(value: &str) -> Self {
        match value.trim().to_ascii_lowercase().as_str() {
            "0x1002" | "0x1022" => GpuVendor::Amd,
            "0x8086" => GpuVendor::Intel,
            "0x10de" => GpuVendor::Nvidia,
            _ => GpuVendor::Other,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GpuInfo {
    pub vendor: &'static str,
    pub name: String,
    pub driver: Option<String>,
}

fn first_line(path: impl AsRef<Path>) -> Option<String> {
    fs::read_to_string(path)
        .ok()?
        .lines()
        .next()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

/// Every real GPU under a `/sys/class/drm`-shaped root. Connector entries
/// (`card0-DP-1`) are skipped, duplicate PCI devices are reported once, and
/// a card without a readable vendor id is ignored rather than guessed.
pub fn detect_from(root: &Path, pci_database: &str) -> Vec<GpuInfo> {
    let Ok(entries) = fs::read_dir(root) else {
        return Vec::new();
    };
    let mut seen = BTreeSet::new();
    let mut gpus = Vec::new();
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if !name.starts_with("card") || name.contains('-') {
            continue;
        }
        let device = entry.path().join("device");
        let Some(vendor_raw) = first_line(device.join("vendor")) else {
            continue;
        };
        let vendor = GpuVendor::from_pci_vendor(&vendor_raw);
        let device_id = crate::hardware::parse_hex_id(&vendor_raw)
            .zip(first_line(device.join("device")).and_then(|v| crate::hardware::parse_hex_id(&v)));
        let key = device_id
            .map(|(vendor, device)| format!("{vendor:04x}:{device:04x}"))
            .unwrap_or_else(|| name.clone());
        if !seen.insert(key) {
            continue;
        }
        let driver = fs::read_link(device.join("driver"))
            .ok()
            .and_then(|path| path.file_name().map(|v| v.to_string_lossy().to_string()));
        let display = device_id
            .and_then(|(vendor, device)| crate::hardware::pci_name(pci_database, vendor, device))
            .unwrap_or_else(|| match vendor {
                GpuVendor::Amd => "AMD GPU".to_string(),
                GpuVendor::Intel => "Intel GPU".to_string(),
                GpuVendor::Nvidia => "NVIDIA GPU".to_string(),
                GpuVendor::Other => "GPU".to_string(),
            });
        gpus.push(GpuInfo {
            vendor: vendor.label(),
            name: display,
            driver,
        });
    }
    gpus.sort_by(|a, b| (a.vendor, &a.name).cmp(&(b.vendor, &b.name)));
    gpus
}

pub fn detect() -> Vec<GpuInfo> {
    let pci_database = fs::read_to_string("/usr/share/hwdata/pci.ids")
        .or_else(|_| fs::read_to_string("/usr/share/misc/pci.ids"))
        .unwrap_or_default();
    detect_from(Path::new("/sys/class/drm"), &pci_database)
}

/// The distinct vendors present, in a stable order. Used to decide which
/// 32-bit Vulkan/OpenGL userspace packages a machine actually needs, so a
/// hybrid laptop gets both stacks.
pub fn vendors(gpus: &[GpuInfo]) -> Vec<&'static str> {
    let mut out = Vec::new();
    for vendor in ["amd", "intel", "nvidia"] {
        if gpus.iter().any(|gpu| gpu.vendor == vendor) {
            out.push(vendor);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(path: &Path, value: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, value).unwrap();
    }

    fn fixture_root(label: &str) -> std::path::PathBuf {
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "mg-toolbox-gpus-{label}-{}-{stamp}",
            std::process::id()
        ))
    }

    #[test]
    fn detects_both_gpus_of_a_hybrid_laptop() {
        let root = fixture_root("hybrid");
        write(&root.join("card0/device/vendor"), "0x8086\n");
        write(&root.join("card0/device/device"), "0x9a49\n");
        write(&root.join("card1/device/vendor"), "0x10de\n");
        write(&root.join("card1/device/device"), "0x2520\n");
        let pci = "8086  Intel Corporation\n\t9a49  Iris Xe Graphics\n10de  NVIDIA Corporation\n\t2520  GA106M [GeForce RTX 3060 Mobile]\n";
        let gpus = detect_from(&root, pci);
        assert_eq!(gpus.len(), 2);
        assert_eq!(vendors(&gpus), vec!["intel", "nvidia"]);
        assert!(gpus.iter().any(|gpu| gpu.name.contains("Iris Xe")));
        assert!(gpus.iter().any(|gpu| gpu.name.contains("GeForce RTX 3060")));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn connector_entries_and_duplicate_pci_devices_are_not_counted_twice() {
        let root = fixture_root("connectors");
        write(&root.join("card0/device/vendor"), "0x1002\n");
        write(&root.join("card0/device/device"), "0x73df\n");
        write(&root.join("card0-DP-1/device/vendor"), "0x1002\n");
        // A second DRM node for the very same PCI device must not duplicate
        // the GPU: the 32-bit package decision must stay the same.
        write(&root.join("card1/device/vendor"), "0x1002\n");
        write(&root.join("card1/device/device"), "0x73df\n");
        let gpus = detect_from(&root, "");
        assert_eq!(gpus.len(), 1);
        assert_eq!(gpus[0].vendor, "amd");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn an_unknown_vendor_is_reported_as_other_never_invented() {
        let root = fixture_root("other");
        write(&root.join("card0/device/vendor"), "0x1234\n");
        write(&root.join("card0/device/device"), "0x5678\n");
        let gpus = detect_from(&root, "");
        assert_eq!(gpus.len(), 1);
        assert_eq!(gpus[0].vendor, "other");
        assert_eq!(gpus[0].name, "GPU");
        assert!(vendors(&gpus).is_empty());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_missing_sysfs_root_yields_no_gpus_instead_of_an_error() {
        assert!(detect_from(Path::new("/nonexistent/drm"), "").is_empty());
    }

    #[test]
    fn drivers_are_read_from_the_symlink_when_present() {
        let root = fixture_root("driver");
        write(&root.join("card0/device/vendor"), "0x10de\n");
        write(&root.join("card0/device/device"), "0x2504\n");
        let driver_dir = root.join("drivers/nvidia");
        fs::create_dir_all(&driver_dir).unwrap();
        std::os::unix::fs::symlink(&driver_dir, root.join("card0/device/driver")).unwrap();
        let gpus = detect_from(&root, "");
        assert_eq!(gpus[0].driver.as_deref(), Some("nvidia"));
        let _ = fs::remove_dir_all(&root);
    }
}
