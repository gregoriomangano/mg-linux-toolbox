#[allow(dead_code)]
#[path = "../advanced_ops.rs"]
mod advanced_ops;
#[allow(dead_code)]
#[path = "../performance_profiles.rs"]
mod performance_profiles;
use performance_profiles::{apply, apply_persisted, restore, Profile};
fn main() {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let uid = std::env::var("PKEXEC_UID")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(u32::MAX);
    if unsafe { libc::geteuid() } != 0
        || (uid == u32::MAX && args.as_slice() != ["apply-persisted"])
    {
        eprintln!("authorization_required");
        std::process::exit(2)
    }
    let result = if args
        .first()
        .is_some_and(|action| advanced_ops::is_operation(action))
    {
        advanced_ops::apply_operation(&args)
    } else {
        match args.as_slice() {
            [action, profile] if action == "apply" => Profile::parse(profile)
                .ok_or_else(|| "invalid_profile".into())
                .and_then(|p| apply(p, uid)),
            [action] if action == "restore" => restore(uid),
            [action] if action == "apply-persisted" => apply_persisted(),
            _ => Err("invalid_operation".into()),
        }
    };
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1)
    }
}
