#!/usr/bin/env bash
set -euo pipefail

version="0.60.4"
archive="CodexBarCLI-v0.60.4-linux-x86_64.tar.gz"
url="https://github.com/steipete/CodexBar/releases/download/v0.60.4/$archive"
sha256="12b51e3016500a7068b73f45c50a034d0e436d6599430c83c1ee3112592005e3"

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
destination="$project_root/src-tauri/resources/codexbar-linux-x86_64"
cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/mg-linux-toolbox/codexbar"
cached_archive="$cache_root/$archive"

for command_name in curl sha256sum tar; do
  command -v "$command_name" >/dev/null || {
    printf 'Missing required command: %s\n' "$command_name" >&2
    exit 1
  }
done

mkdir -p "$cache_root"
if [[ ! -f "$cached_archive" ]] || ! printf '%s  %s\n' "$sha256" "$cached_archive" | sha256sum --check --status; then
  rm -f "$cached_archive"
  curl --fail --location --proto '=https' --tlsv1.2 --silent --show-error "$url" -o "$cached_archive"
fi
printf '%s  %s\n' "$sha256" "$cached_archive" | sha256sum --check --status || {
  printf 'CodexBar %s archive checksum verification failed.\n' "$version" >&2
  exit 1
}

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
tar -xzf "$cached_archive" -C "$staging"

binary="$(find "$staging" -type f -name CodexBarCLI -print -quit)"
[[ -n "$binary" ]] || {
  printf 'CodexBar %s archive has an unexpected layout.\n' "$version" >&2
  exit 1
}

rm -rf "$destination"
mkdir -p "$destination"
cp -a "$(dirname -- "$binary")/." "$destination/"
cp "$project_root/third_party/CodexBar-MIT.txt" "$destination/LICENSE"
chmod 0755 "$destination/CodexBarCLI"
printf 'CodexBar %s prepared from verified official release.\n' "$version"
