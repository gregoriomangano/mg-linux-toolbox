fn main() {
    println!("cargo:rerun-if-env-changed=MG_UPDATER_ENDPOINT");
    println!("cargo:rerun-if-env-changed=MG_UPDATER_PUBKEY");
    tauri_build::build()
}
