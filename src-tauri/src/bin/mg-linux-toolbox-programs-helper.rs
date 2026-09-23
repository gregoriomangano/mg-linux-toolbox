//! Privileged programs helper (Gaming module). A separate polkit action and
//! a separate binary from the performance/repository helpers: it never
//! touches sysfs or `/etc/apt`, and the other helpers never install
//! packages. It never trusts anything the caller says: the only accepted
//! input is one of two closed operation names, and every package, every
//! repository and every command is re-derived here from the real system and
//! the shared catalog in `gaming.rs`.
//!
//! Output protocol (one JSON object per line on stdout):
//!   {"kind":"step","step":{...}}    one line per executed step, as it finishes
//!   {"kind":"report","report":{...}} the final step-by-step report
//!   {"kind":"error","code":"..."}   only when no report could be produced
//! Exit code is 0 only when the report says everything really is installed.
#[allow(dead_code)]
#[path = "../distro.rs"]
mod distro;
#[allow(dead_code)]
#[path = "../gaming.rs"]
mod gaming;
#[allow(dead_code)]
#[path = "../gpus.rs"]
mod gpus;
#[allow(dead_code)]
#[path = "../hardware.rs"]
mod hardware;
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
        .is_some_and(|action| gaming::is_operation(action))
    {
        println!(
            "{}",
            serde_json::json!({"kind":"error","code":"invalid_operation"})
        );
        std::process::exit(1)
    }
    let mut progress = |report: &gaming::StepReport| {
        println!("{}", serde_json::json!({"kind":"step","step":report}));
    };
    match gaming::apply_operation(&args, &mut progress) {
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
