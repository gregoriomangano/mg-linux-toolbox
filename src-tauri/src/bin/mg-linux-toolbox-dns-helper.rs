//! Privileged DNS helper. Its only accepted input is one provider identifier;
//! every NetworkManager command and address is derived from the closed catalog.
#[allow(dead_code)]
#[path = "../distro.rs"]
mod distro;
#[allow(dead_code)]
#[path = "../dns.rs"]
mod dns;
#[allow(dead_code)]
#[path = "../packages.rs"]
mod packages;

fn main() {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if unsafe { libc::geteuid() } != 0 {
        println!(
            "{}",
            serde_json::json!({"kind":"error","code":"authorization_required"})
        );
        std::process::exit(2);
    }
    let [operation, provider] = args.as_slice() else {
        println!(
            "{}",
            serde_json::json!({"kind":"error","code":"invalid_operation"})
        );
        std::process::exit(1);
    };
    if operation != "set-dns-provider" {
        println!(
            "{}",
            serde_json::json!({"kind":"error","code":"invalid_operation"})
        );
        std::process::exit(1);
    }
    let Some(provider) = dns::DnsProvider::parse(provider) else {
        println!(
            "{}",
            serde_json::json!({"kind":"error","code":"invalid_provider"})
        );
        std::process::exit(1);
    };
    match dns::apply_provider(provider) {
        Ok(status) => println!("{}", serde_json::json!({"kind":"status","status":status})),
        Err(code) => {
            println!("{}", serde_json::json!({"kind":"error","code":code}));
            std::process::exit(1);
        }
    }
}
