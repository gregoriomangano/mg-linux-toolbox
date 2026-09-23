//! Privileged WinBoat helper. A separate polkit action and a separate
//! binary from the performance/repository/programs/dns helpers: it never
//! touches sysfs, `/etc/apt` repositories, DNS or the Gaming component
//! catalog, and none of those helpers ever touch Docker, KVM group
//! membership or WinBoat. It never trusts anything the caller says: the
//! only accepted input is the one closed operation name, and every
//! requirement, package and group grant is re-derived here from the real
//! system, exactly like the programs helper does for Gaming.
//!
//! Output protocol (one JSON object per line on stdout):
//!   {"kind":"step","step":{...}}     one line per executed step, as it finishes
//!   {"kind":"report","report":{...}} the final step-by-step report
//!   {"kind":"error","code":"..."}    only when no report could be produced
//! Exit code is 0 only when the report says everything really is ready.
#[allow(dead_code)]
#[path = "../distro.rs"]
mod distro;
#[allow(dead_code)]
#[path = "../hardware.rs"]
mod hardware;
#[allow(dead_code)]
#[path = "../packages.rs"]
mod packages;
#[allow(dead_code)]
#[path = "../winboat.rs"]
mod winboat;

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
        .is_some_and(|action| winboat::is_operation(action))
    {
        println!(
            "{}",
            serde_json::json!({"kind":"error","code":"invalid_operation"})
        );
        std::process::exit(1)
    }
    let mut progress = |report: &winboat::StepReport| {
        println!("{}", serde_json::json!({"kind":"step","step":report}));
    };
    match winboat::apply_operation(&args, &mut progress) {
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
