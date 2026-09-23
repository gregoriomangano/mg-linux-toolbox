#!/usr/bin/env bash
set -euo pipefail

if [[ -f "$HOME/.cargo/env" ]]; then
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
fi

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

for command_name in node npm rustc cargo pkg-config; do
  command -v "$command_name" >/dev/null || {
    printf 'Missing required command: %s\n' "$command_name" >&2
    exit 1
  }
done

pkg-config --exists gtk+-3.0 webkit2gtk-4.1 openssl || {
  printf '%s\n' 'Required native pkg-config modules are missing.' >&2
  exit 1
}

npm ci
(cd src-tauri && cargo check)
(cd src-tauri && cargo test)
(cd src-tauri && cargo clippy -- -D warnings)
npm test
("$project_root/scripts/bootstrap-codexbar.sh")
(cd src-tauri && cargo build --release --bins)

npm run tauri build -- --bundles deb --config packaging/tauri.deb.conf.json --config '{"bundle":{"createUpdaterArtifacts":false}}'

mapfile -t deb_files < <(
  find "$project_root/src-tauri/target/release/bundle/deb" \
    -maxdepth 1 -type f -name '*.deb' -printf '%T@ %p\n' |
    sort -nr |
    sed 's/^[^ ]* //'
)

if (( ${#deb_files[@]} == 0 )); then
  printf '%s\n' 'No Debian package was produced.' >&2
  exit 1
fi

deb_file="${deb_files[0]}"
printf '\nPackage to install:\n%s\n' "$deb_file"
dpkg-deb -f "$deb_file" Package Version Architecture Depends
stat -c 'Size: %s bytes' "$deb_file"

read -r -p 'Install this package with apt? [y/N] ' answer
case "$answer" in
  y|Y|yes|YES)
    sudo apt install "$deb_file"
    ;;
  *)
    printf '%s\n' 'Package build completed; installation skipped.'
    ;;
esac
