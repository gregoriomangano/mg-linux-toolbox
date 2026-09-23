//! Privileged apps-catalog helper. A separate polkit action and a separate
//! binary from every other helper: it never touches sysfs, `/etc/apt`
//! repositories, DNS, the Gaming catalog or WinBoat, and none of those
//! helpers ever install/remove a catalog app. It never trusts anything the
//! caller says: the only accepted input is one of the three closed
//! operation names plus one opaque app id and one opaque method/via
//! string, both checked against the closed catalog in `apps_catalog.rs`
//! before anything runs. Flatpak `--user` app installs/removals never
//! reach this helper by design (see `lib.rs::install_app`): only the
//! Flatpak/Snap *runtime* bootstrap, the native/official installer, and
//! Snap app installs/removals ever need root.
//!
//! Output protocol (one JSON object per line on stdout):
//!   {"kind":"step","step":{...}}     one line per executed step, as it finishes
//!   {"kind":"report","report":{...}} the final step-by-step report
//!   {"kind":"error","code":"..."}    only when no report could be produced
//! Exit code is 0 only when the report says the operation really succeeded.
#[allow(dead_code)]
#[path = "../apps_catalog.rs"]
mod apps_catalog;
#[allow(dead_code)]
#[path = "../apps_ops.rs"]
mod apps_ops;
#[allow(dead_code)]
#[path = "../distro.rs"]
mod distro;
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
        std::process::exit(2)
    }
    if !args
        .first()
        .is_some_and(|action| apps_ops::is_operation(action))
    {
        println!(
            "{}",
            serde_json::json!({"kind":"error","code":"invalid_operation"})
        );
        std::process::exit(1)
    }
    let mut progress = |report: &apps_ops::StepReport| {
        println!("{}", serde_json::json!({"kind":"step","step":report}));
    };
    match apps_ops::apply_operation(&args, &mut progress) {
        Ok(report) => {
            println!("{}", serde_json::json!({"kind":"report","report":report}));
            if !report.ok {
                std::process::exit(1)
            }
        }
        Err(code) => {
            println!("{}", serde_json::json!({"kind":"error","code":code}));
            std::process::exit(1)
        }
    }
}
