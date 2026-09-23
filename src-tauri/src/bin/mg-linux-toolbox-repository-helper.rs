//! Privileged repository helper (APT V1). A separate polkit action and a
//! separate binary from the performance helper: it never handles CPU/kernel
//! settings, and the performance helper never touches `/etc/apt`. It never
//! trusts anything the caller says about *where* to write -- it re-derives
//! the allow-listed APT source files itself and re-parses the target file
//! fresh before applying the one minimal, span-based edit the frontend
//! requested.
#[allow(dead_code)]
#[path = "../apt_ops.rs"]
mod apt_ops;

fn main() {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if unsafe { libc::geteuid() } != 0 {
        eprintln!("authorization_required");
        std::process::exit(2)
    }
    let result = if args
        .first()
        .is_some_and(|action| apt_ops::is_operation(action))
    {
        apt_ops::apply_operation(&args)
    } else {
        Err("invalid_operation".into())
    };
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1)
    }
}
