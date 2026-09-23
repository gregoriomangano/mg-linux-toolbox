//! Capability-based DNS management for the Programs page.
//!
//! NetworkManager remains the single owner of network configuration. The
//! frontend only selects a closed provider; this module discovers the active
//! profile, changes only its DNS properties, reapplies it and reads it back.
use crate::packages::{CommandOutput, CommandRunner};
use serde::{Deserialize, Serialize};
use std::{thread, time::Duration};

pub const DNS_HELPER_PATH: &str = "/usr/lib/mg-linux-toolbox/mg-linux-toolbox-dns-helper";

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum DnsProvider {
    Automatic,
    Cloudflare,
    Google,
    Quad9,
    Adguard,
}

impl DnsProvider {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "automatic" => Some(Self::Automatic),
            "cloudflare" => Some(Self::Cloudflare),
            "google" => Some(Self::Google),
            "quad9" => Some(Self::Quad9),
            "adguard" => Some(Self::Adguard),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Automatic => "automatic",
            Self::Cloudflare => "cloudflare",
            Self::Google => "google",
            Self::Quad9 => "quad9",
            Self::Adguard => "adguard",
        }
    }

    fn addresses(self) -> (&'static [&'static str], &'static [&'static str]) {
        match self {
            Self::Automatic => (&[], &[]),
            Self::Cloudflare => (
                &["1.1.1.1", "1.0.0.1"],
                &["2606:4700:4700::1111", "2606:4700:4700::1001"],
            ),
            Self::Google => (
                &["8.8.8.8", "8.8.4.4"],
                &["2001:4860:4860::8888", "2001:4860:4860::8844"],
            ),
            Self::Quad9 => (
                &["9.9.9.9", "149.112.112.112"],
                &["2620:fe::fe", "2620:fe::9"],
            ),
            Self::Adguard => (
                &["94.140.14.14", "94.140.15.15"],
                &["2a10:50c0::ad1:ff", "2a10:50c0::ad2:ff"],
            ),
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DnsFamilyStatus {
    pub method: String,
    pub configured_servers: Vec<String>,
    pub active_servers: Vec<String>,
    pub ignore_automatic: bool,
    pub manageable: bool,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DnsStatus {
    pub available: bool,
    pub reason: Option<String>,
    pub connection_name: Option<String>,
    pub connection_uuid: Option<String>,
    pub device: Option<String>,
    pub provider: String,
    pub ipv4: DnsFamilyStatus,
    pub ipv6: DnsFamilyStatus,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct ActiveConnection {
    uuid: String,
    name: String,
    device: String,
}

fn escaped_fields(line: &str) -> Vec<String> {
    let mut fields = vec![String::new()];
    let mut escaped = false;
    for character in line.chars() {
        if escaped {
            fields.last_mut().unwrap().push(character);
            escaped = false;
        } else if character == '\\' {
            escaped = true;
        } else if character == ':' {
            fields.push(String::new());
        } else {
            fields.last_mut().unwrap().push(character);
        }
    }
    if escaped {
        fields.last_mut().unwrap().push('\\');
    }
    fields
}

fn parse_active_connections(text: &str) -> Vec<ActiveConnection> {
    text.lines()
        .filter_map(|line| {
            let fields = escaped_fields(line);
            if fields.len() != 4 || fields[0].is_empty() || fields[3].is_empty() {
                return None;
            }
            Some(ActiveConnection {
                uuid: fields[0].clone(),
                name: fields[1].clone(),
                device: fields[3].clone(),
            })
        })
        .collect()
}

fn run(runner: &dyn CommandRunner, args: &[String]) -> Option<CommandOutput> {
    let refs = args.iter().map(String::as_str).collect::<Vec<_>>();
    runner.run("nmcli", &refs)
}

fn value(runner: &dyn CommandRunner, uuid: &str, property: &str) -> Option<String> {
    let args = vec![
        "--colors".into(),
        "no".into(),
        "--escape".into(),
        "no".into(),
        "--get-values".into(),
        property.into(),
        "connection".into(),
        "show".into(),
        "uuid".into(),
        uuid.into(),
    ];
    run(runner, &args)
        .filter(|output| output.success)
        .map(|output| output.stdout.trim().to_string())
}

fn device_values(runner: &dyn CommandRunner, device: &str, field: &str) -> Option<Vec<String>> {
    let args = vec![
        "--colors".into(),
        "no".into(),
        "--escape".into(),
        "no".into(),
        "--get-values".into(),
        field.into(),
        "device".into(),
        "show".into(),
        device.into(),
    ];
    run(runner, &args)
        .filter(|output| output.success)
        .map(|output| {
            output
                .stdout
                .lines()
                .map(str::trim)
                .filter(|line| !line.is_empty() && *line != "--")
                .flat_map(servers)
                .collect()
        })
}

fn servers(value: &str) -> Vec<String> {
    value
        .split(|character: char| character == ',' || character == '|' || character.is_whitespace())
        .map(str::trim)
        .filter(|entry| !entry.is_empty() && *entry != "--")
        .map(str::to_string)
        .collect()
}

fn manageable_method(method: &str) -> bool {
    matches!(method, "auto" | "manual" | "dhcp")
}

fn family_status(
    runner: &dyn CommandRunner,
    connection: &ActiveConnection,
    family: &str,
) -> Option<DnsFamilyStatus> {
    let method = value(runner, &connection.uuid, &format!("{family}.method"))?;
    let configured_servers = servers(&value(runner, &connection.uuid, &format!("{family}.dns"))?);
    let ignore_automatic = value(
        runner,
        &connection.uuid,
        &format!("{family}.ignore-auto-dns"),
    )?
    .eq_ignore_ascii_case("yes");
    let active_servers = device_values(
        runner,
        &connection.device,
        if family == "ipv4" {
            "IP4.DNS"
        } else {
            "IP6.DNS"
        },
    )
    .unwrap_or_default();
    Some(DnsFamilyStatus {
        manageable: manageable_method(&method),
        method,
        configured_servers,
        active_servers,
        ignore_automatic,
    })
}

fn select_connection(
    runner: &dyn CommandRunner,
    connections: Vec<ActiveConnection>,
) -> Result<ActiveConnection, &'static str> {
    if connections.is_empty() {
        return Err("noActiveConnection");
    }
    let route_owners = connections
        .iter()
        .filter(|connection| {
            let ipv4 = device_values(runner, &connection.device, "IP4.GATEWAY")
                .is_some_and(|values| !values.is_empty());
            let ipv6 = device_values(runner, &connection.device, "IP6.GATEWAY")
                .is_some_and(|values| !values.is_empty());
            ipv4 || ipv6
        })
        .cloned()
        .collect::<Vec<_>>();
    match route_owners.as_slice() {
        [connection] => Ok(connection.clone()),
        [] if connections.len() == 1 => Ok(connections[0].clone()),
        [] => Err("ambiguousConnection"),
        _ => Err("ambiguousConnection"),
    }
}

fn detected_provider(ipv4: &DnsFamilyStatus, ipv6: &DnsFamilyStatus) -> String {
    let managed = [ipv4, ipv6]
        .into_iter()
        .filter(|family| family.manageable)
        .collect::<Vec<_>>();
    if managed.is_empty() {
        return "unavailable".into();
    }
    if managed
        .iter()
        .all(|family| family.configured_servers.is_empty() && !family.ignore_automatic)
    {
        return "automatic".into();
    }
    for provider in [
        DnsProvider::Cloudflare,
        DnsProvider::Google,
        DnsProvider::Quad9,
        DnsProvider::Adguard,
    ] {
        let (ipv4_addresses, ipv6_addresses) = provider.addresses();
        let matches = (!ipv4.manageable
            || (ipv4.ignore_automatic && ipv4.configured_servers == strings(ipv4_addresses)))
            && (!ipv6.manageable
                || (ipv6.ignore_automatic && ipv6.configured_servers == strings(ipv6_addresses)));
        if matches {
            return provider.as_str().into();
        }
    }
    "custom".into()
}

fn strings(values: &[&str]) -> Vec<String> {
    values.iter().map(|value| (*value).to_string()).collect()
}

pub fn status_with(runner: &dyn CommandRunner) -> DnsStatus {
    let probe = runner.run("nmcli", &["--version"]);
    if !probe.is_some_and(|output| output.success) {
        return DnsStatus {
            reason: Some("networkManagerUnavailable".into()),
            ..DnsStatus::default()
        };
    }
    let active = runner.run(
        "nmcli",
        &[
            "--colors",
            "no",
            "--terse",
            "--escape",
            "yes",
            "--fields",
            "UUID,NAME,TYPE,DEVICE",
            "connection",
            "show",
            "--active",
        ],
    );
    let Some(active) = active.filter(|output| output.success) else {
        return DnsStatus {
            reason: Some("networkManagerUnavailable".into()),
            ..DnsStatus::default()
        };
    };
    let connection = match select_connection(runner, parse_active_connections(&active.stdout)) {
        Ok(connection) => connection,
        Err(reason) => {
            return DnsStatus {
                reason: Some(reason.into()),
                ..DnsStatus::default()
            }
        }
    };
    let Some(ipv4) = family_status(runner, &connection, "ipv4") else {
        return DnsStatus {
            reason: Some("statusReadFailed".into()),
            ..DnsStatus::default()
        };
    };
    let Some(ipv6) = family_status(runner, &connection, "ipv6") else {
        return DnsStatus {
            reason: Some("statusReadFailed".into()),
            ..DnsStatus::default()
        };
    };
    let manageable = ipv4.manageable || ipv6.manageable;
    DnsStatus {
        available: manageable,
        reason: (!manageable).then(|| "unsupportedAddressMethod".into()),
        connection_name: Some(connection.name),
        connection_uuid: Some(connection.uuid),
        device: Some(connection.device),
        provider: detected_provider(&ipv4, &ipv6),
        ipv4,
        ipv6,
    }
}

pub fn status() -> DnsStatus {
    status_with(&crate::packages::SystemRunner)
}

fn modify_args(status: &DnsStatus, provider: DnsProvider) -> Result<Vec<String>, String> {
    let uuid = status
        .connection_uuid
        .as_ref()
        .ok_or("noActiveConnection")?;
    let (ipv4, ipv6) = provider.addresses();
    let mut args = vec![
        "connection".into(),
        "modify".into(),
        "uuid".into(),
        uuid.clone(),
    ];
    if status.ipv4.manageable {
        args.extend([
            "ipv4.dns".into(),
            ipv4.join(","),
            "ipv4.ignore-auto-dns".into(),
            if provider == DnsProvider::Automatic {
                "no".into()
            } else {
                "yes".into()
            },
        ]);
    }
    if status.ipv6.manageable {
        args.extend([
            "ipv6.dns".into(),
            ipv6.join(","),
            "ipv6.ignore-auto-dns".into(),
            if provider == DnsProvider::Automatic {
                "no".into()
            } else {
                "yes".into()
            },
        ]);
    }
    Ok(args)
}

fn restore_args(status: &DnsStatus) -> Result<Vec<String>, String> {
    let uuid = status
        .connection_uuid
        .as_ref()
        .ok_or("noActiveConnection")?;
    let mut args = vec![
        "connection".into(),
        "modify".into(),
        "uuid".into(),
        uuid.clone(),
    ];
    for (name, family) in [("ipv4", &status.ipv4), ("ipv6", &status.ipv6)] {
        if family.manageable {
            args.extend([
                format!("{name}.dns"),
                family.configured_servers.join(","),
                format!("{name}.ignore-auto-dns"),
                if family.ignore_automatic {
                    "yes".into()
                } else {
                    "no".into()
                },
            ]);
        }
    }
    Ok(args)
}

fn reapply(runner: &dyn CommandRunner, device: &str) -> bool {
    runner
        .run("nmcli", &["device", "reapply", device])
        .is_some_and(|output| output.success)
}

fn restore(runner: &dyn CommandRunner, before: &DnsStatus, device: &str) -> Result<(), String> {
    let rollback = restore_args(before)?;
    if !run(runner, &rollback).is_some_and(|output| output.success) || !reapply(runner, device) {
        return Err("dnsRollbackFailed".into());
    }
    for _ in 0..20 {
        let restored = status_with(runner);
        if before.provider == "automatic" && verified(&restored, DnsProvider::Automatic) {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(200));
    }
    Err("dnsRollbackFailed".into())
}

fn verified(status: &DnsStatus, provider: DnsProvider) -> bool {
    if !status.available || status.provider != provider.as_str() {
        return false;
    }
    if provider == DnsProvider::Automatic {
        return true;
    }
    let (ipv4, ipv6) = provider.addresses();
    (!status.ipv4.manageable
        || strings(ipv4)
            .iter()
            .all(|address| status.ipv4.active_servers.contains(address)))
        && (!status.ipv6.manageable
            || strings(ipv6)
                .iter()
                .all(|address| status.ipv6.active_servers.contains(address)))
}

pub fn apply_provider_with(
    runner: &dyn CommandRunner,
    provider: DnsProvider,
) -> Result<DnsStatus, String> {
    let before = status_with(runner);
    if !before.available {
        return Err(before.reason.unwrap_or_else(|| "dnsUnavailable".into()));
    }
    let device = before.device.clone().ok_or("noActiveConnection")?;
    let args = modify_args(&before, provider)?;
    let changed = run(runner, &args).is_some_and(|output| output.success);
    if !changed || !reapply(runner, &device) {
        restore(runner, &before, &device)?;
        return Err("dnsApplyFailed".into());
    }
    for _ in 0..20 {
        let after = status_with(runner);
        if verified(&after, provider) {
            return Ok(after);
        }
        thread::sleep(Duration::from_millis(200));
    }
    restore(runner, &before, &device)?;
    Err("dnsVerificationFailed".into())
}

pub fn apply_provider(provider: DnsProvider) -> Result<DnsStatus, String> {
    apply_provider_with(&crate::packages::SystemRunner, provider)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{cell::RefCell, collections::VecDeque};

    #[test]
    #[ignore = "reads this machine's real NetworkManager/systemd-resolved state; run manually with -- --ignored --nocapture"]
    fn live_dns_status_against_the_real_system() {
        let status = status();
        eprintln!(
            "available={} reason={:?} connection={:?} device={:?} provider={}",
            status.available, status.reason, status.connection_name, status.device, status.provider
        );
        eprintln!(
            "ipv4: method={} manageable={} configured={:?} active={:?} ignoreAuto={}",
            status.ipv4.method,
            status.ipv4.manageable,
            status.ipv4.configured_servers,
            status.ipv4.active_servers,
            status.ipv4.ignore_automatic
        );
        eprintln!(
            "ipv6: method={} manageable={} configured={:?} active={:?} ignoreAuto={}",
            status.ipv6.method,
            status.ipv6.manageable,
            status.ipv6.configured_servers,
            status.ipv6.active_servers,
            status.ipv6.ignore_automatic
        );
    }

    struct QueueRunner {
        outputs: RefCell<VecDeque<CommandOutput>>,
        calls: RefCell<Vec<(String, Vec<String>)>>,
    }

    impl QueueRunner {
        fn new(outputs: Vec<CommandOutput>) -> Self {
            Self {
                outputs: RefCell::new(outputs.into()),
                calls: RefCell::new(Vec::new()),
            }
        }
    }

    impl CommandRunner for QueueRunner {
        fn run(&self, program: &str, args: &[&str]) -> Option<CommandOutput> {
            self.calls.borrow_mut().push((
                program.to_string(),
                args.iter().map(|arg| (*arg).to_string()).collect(),
            ));
            self.outputs.borrow_mut().pop_front()
        }
    }

    fn output(success: bool, stdout: &str) -> CommandOutput {
        CommandOutput {
            success,
            exit_code: if success { 0 } else { 1 },
            stdout: stdout.into(),
            stderr: String::new(),
        }
    }

    #[test]
    fn parses_active_connections_and_escaped_names() {
        let parsed = parse_active_connections(
            "11111111-1111-1111-1111-111111111111:Office\\: wired:802-3-ethernet:enp1s0\n",
        );
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].name, "Office: wired");
        assert_eq!(parsed[0].device, "enp1s0");
    }

    #[test]
    fn provider_catalog_contains_official_ipv4_and_ipv6_addresses() {
        assert_eq!(
            DnsProvider::Cloudflare.addresses(),
            (
                &["1.1.1.1", "1.0.0.1"][..],
                &["2606:4700:4700::1111", "2606:4700:4700::1001"][..]
            )
        );
        assert_eq!(
            DnsProvider::Quad9.addresses().1,
            &["2620:fe::fe", "2620:fe::9"]
        );
        assert_eq!(
            DnsProvider::Adguard.addresses().1,
            &["2a10:50c0::ad1:ff", "2a10:50c0::ad2:ff"]
        );
        assert!(DnsProvider::parse("8.8.8.8").is_none());
        assert!(DnsProvider::parse("; rm -rf /").is_none());
    }

    #[test]
    fn automatic_and_manual_state_are_detected() {
        let automatic = DnsFamilyStatus {
            method: "auto".into(),
            manageable: true,
            ..DnsFamilyStatus::default()
        };
        let disabled = DnsFamilyStatus {
            method: "disabled".into(),
            ..DnsFamilyStatus::default()
        };
        assert_eq!(detected_provider(&automatic, &disabled), "automatic");
        let cloudflare = DnsFamilyStatus {
            configured_servers: strings(DnsProvider::Cloudflare.addresses().0),
            ignore_automatic: true,
            ..automatic.clone()
        };
        assert_eq!(detected_provider(&cloudflare, &disabled), "cloudflare");
    }

    #[test]
    fn restore_automatic_clears_dns_without_changing_ip_methods() {
        let status = DnsStatus {
            connection_uuid: Some("uuid-1".into()),
            ipv4: DnsFamilyStatus {
                method: "auto".into(),
                manageable: true,
                ..DnsFamilyStatus::default()
            },
            ipv6: DnsFamilyStatus {
                method: "disabled".into(),
                ..DnsFamilyStatus::default()
            },
            ..DnsStatus::default()
        };
        let args = modify_args(&status, DnsProvider::Automatic).unwrap();
        assert_eq!(
            args,
            [
                "connection",
                "modify",
                "uuid",
                "uuid-1",
                "ipv4.dns",
                "",
                "ipv4.ignore-auto-dns",
                "no"
            ]
        );
        assert!(!args.iter().any(|arg| arg == "ipv4.method"));
    }

    #[test]
    fn absent_network_manager_is_reported_without_fallback() {
        let runner = QueueRunner::new(vec![output(false, "")]);
        let status = status_with(&runner);
        assert!(!status.available);
        assert_eq!(status.reason.as_deref(), Some("networkManagerUnavailable"));
        assert_eq!(runner.calls.borrow().len(), 1);
    }

    fn status_outputs(ipv4_dns: &str, ipv4_ignore: &str, active_ipv4: &str) -> Vec<CommandOutput> {
        vec![
            output(true, "nmcli tool, version 1.46.0\n"),
            output(
                true,
                "11111111-1111-1111-1111-111111111111:Home:802-11-wireless:wlan0\n",
            ),
            output(true, "192.168.1.1\n"),
            output(true, "\n"),
            output(true, "auto\n"),
            output(true, ipv4_dns),
            output(true, ipv4_ignore),
            output(true, active_ipv4),
            output(true, "disabled\n"),
            output(true, "\n"),
            output(true, "no\n"),
            output(true, "\n"),
        ]
    }

    #[test]
    fn active_connection_and_automatic_dns_are_detected_read_only() {
        let runner = QueueRunner::new(status_outputs("\n", "no\n", "192.168.1.1\n"));
        let status = status_with(&runner);
        assert!(status.available);
        assert_eq!(status.connection_name.as_deref(), Some("Home"));
        assert_eq!(status.device.as_deref(), Some("wlan0"));
        assert_eq!(status.provider, "automatic");
        assert_eq!(status.ipv4.active_servers, ["192.168.1.1"]);
        assert!(runner
            .calls
            .borrow()
            .iter()
            .all(|(_, args)| !args.iter().any(|arg| arg == "modify" || arg == "reapply")));
    }

    #[test]
    fn manual_provider_dns_is_detected_from_profile_and_runtime_state() {
        let runner = QueueRunner::new(status_outputs(
            "1.1.1.1,1.0.0.1\n",
            "yes\n",
            "1.1.1.1\n1.0.0.1\n",
        ));
        let status = status_with(&runner);
        assert_eq!(status.provider, "cloudflare");
        assert!(status.ipv4.ignore_automatic);
        assert_eq!(status.ipv4.configured_servers, ["1.1.1.1", "1.0.0.1"]);
    }

    #[test]
    fn splits_nmcli_pipe_separated_runtime_dns_servers() {
        assert_eq!(servers("8.8.8.8 | 8.8.4.4"), ["8.8.8.8", "8.8.4.4"]);
        let values = device_values(
            &QueueRunner::new(vec![output(true, "8.8.8.8 | 8.8.4.4\n")]),
            "eth0",
            "IP4.DNS",
        )
        .unwrap();
        assert!(values.contains(&"8.8.8.8".into()));
        assert!(values.contains(&"8.8.4.4".into()));
    }

    #[test]
    fn multiple_route_owners_are_rejected_as_ambiguous() {
        let runner = QueueRunner::new(vec![
            output(true, "1.1.1.1\n"),
            output(true, ""),
            output(true, "10.0.0.1\n"),
            output(true, ""),
        ]);
        let connections = vec![
            ActiveConnection {
                uuid: "one".into(),
                name: "one".into(),
                device: "eth0".into(),
            },
            ActiveConnection {
                uuid: "two".into(),
                name: "two".into(),
                device: "wlan0".into(),
            },
        ];
        assert_eq!(
            select_connection(&runner, connections),
            Err("ambiguousConnection")
        );
    }
}
