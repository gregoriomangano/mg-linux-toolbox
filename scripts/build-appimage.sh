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

# Some distributions (e.g. Ubuntu 26.10 onward) ship the uutils/Rust
# reimplementation of coreutils as the default /usr/bin/cp. Its
# `cp --archive --parents --target-directory=...` has a path-resolution bug
# that makes Tauri's bundled linuxdeploy-plugin-gtk.sh fail with a false
# "are the same file" error while copying GLib schemas into the AppDir. GNU
# coreutils does not have this bug. When both uutils cp and a side-by-side
# GNU coreutils package are present (as gnucp, gnumkdir, ...), build with a
# small PATH shim so linuxdeploy's helper scripts use the GNU tools instead.
# This only affects this build's subprocess PATH; it changes nothing on the
# rest of the machine and nothing about the shipped AppImage itself.
build_path="$PATH"
if cp --version 2>/dev/null | head -1 | grep -qi uutils; then
  shim_dir="$(mktemp -d)"
  trap 'rm -rf "$shim_dir"' EXIT
  missing=()
  for tool in cp mkdir chmod ln; do
    if [[ -x "/usr/bin/gnu$tool" ]]; then
      ln -s "/usr/bin/gnu$tool" "$shim_dir/$tool"
    else
      missing+=("gnu$tool")
    fi
  done
  if (( ${#missing[@]} > 0 )); then
    printf 'uutils coreutils detected as the default cp, but the GNU coreutils fallback binaries are missing: %s\n' "${missing[*]}" >&2
    printf 'Install the distribution package that provides them (e.g. "gnu-coreutils" on Ubuntu) before building the AppImage.\n' >&2
    exit 1
  fi
  build_path="$shim_dir:$PATH"
  printf 'Using GNU coreutils shim for the AppImage build (uutils cp is the system default and mis-handles linuxdeploy-plugin-gtk.sh).\n'
fi

npm ci
(cd src-tauri && cargo check)
(cd src-tauri && cargo test)
(cd src-tauri && cargo clippy -- -D warnings)
npm test
("$project_root/scripts/bootstrap-codexbar.sh")
(cd src-tauri && cargo build --release --bins)

PATH="$build_path" npm run tauri build -- --bundles appimage --config packaging/tauri.appimage.conf.json --config '{"bundle":{"createUpdaterArtifacts":false}}'

mapfile -t appimage_files < <(
  find "$project_root/src-tauri/target/release/bundle/appimage" \
    -maxdepth 1 -type f -name '*.AppImage' -printf '%T@ %p\n' |
    sort -nr |
    sed 's/^[^ ]* //'
)

if (( ${#appimage_files[@]} == 0 )); then
  printf '%s\n' 'No AppImage was produced.' >&2
  exit 1
fi

appimage_file="${appimage_files[0]}"
printf '\nAppImage built:\n%s\n' "$appimage_file"
stat -c 'Size: %s bytes' "$appimage_file"
chmod +x "$appimage_file"
printf 'Run it directly to test: "%s"\n' "$appimage_file"
