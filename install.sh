#!/usr/bin/env bash
set -euo pipefail

repo="${MG_GITHUB_REPOSITORY:-gregoriomangano/mg-linux-toolbox}"
api_url="https://api.github.com/repos/$repo/releases/latest"
command -v curl >/dev/null || { printf '%s\n' 'curl is required.' >&2; exit 1; }
command -v sha256sum >/dev/null || { printf '%s\n' 'sha256sum is required.' >&2; exit 1; }

case "$(uname -m)" in
  x86_64|amd64) arch=amd64 ;;
  *) printf 'This release supports amd64 only (detected %s).\n' "$(uname -m)" >&2; exit 1 ;;
esac

if ! command -v python3 >/dev/null; then
  printf '%s\n' 'Python 3 is required to read release metadata; install it with the system package manager.' >&2
  exit 1
fi

os_id=""; os_like=""
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  os_id="${ID:-}"; os_like="${ID_LIKE:-}"
fi
if [[ "$os_id $os_like" =~ (debian|ubuntu) ]]; then
  preferred_kind=deb
else
  preferred_kind=appimage
fi

tmpdir="$(mktemp -d)"
rollback_file="$tmpdir/rollback.sh"
rollback_needed=0
trap 'if [[ "$rollback_needed" == 1 && -x "$rollback_file" ]]; then "$rollback_file" || true; fi; rm -rf "$tmpdir"' EXIT

metadata="$tmpdir/release.json"
curl --fail --location --proto '=https' --tlsv1.2 --silent --show-error \
  -H 'Accept: application/vnd.github+json' "$api_url" -o "$metadata"

readarray -t release_values < <(python3 - "$metadata" "$arch" "$preferred_kind" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    release = json.load(stream)
if release.get("prerelease") or release.get("draft"):
    raise SystemExit("latest GitHub release is not a stable published release")
arch = sys.argv[2]
preferred = sys.argv[3]
patterns = {
    "deb": f"_amd64.deb",
    "appimage": f"_amd64.AppImage",
}
for kind in (preferred, "appimage" if preferred == "deb" else "deb"):
    suffix = patterns[kind]
    asset = next((item for item in release.get("assets", [])
                  if item.get("name", "").endswith(suffix)), None)
    if asset:
        checksum = next((item for item in release.get("assets", [])
                         if item.get("name") == asset["name"] + ".sha256"), None)
        if checksum:
            print(kind)
            print(release["tag_name"])
            print(asset["name"])
            print(asset["browser_download_url"])
            print(checksum["browser_download_url"])
            break
else:
    raise SystemExit("no compatible amd64 asset and checksum found in the latest release")
PY
)

asset_kind="${release_values[0]}"
version="${release_values[1]#v}"
asset_name="${release_values[2]}"
asset_url="${release_values[3]}"
checksum_url="${release_values[4]}"
asset="$tmpdir/$asset_name"
checksum_file="$tmpdir/$asset_name.sha256"

curl --fail --location --proto '=https' --tlsv1.2 --silent --show-error "$asset_url" -o "$asset"
curl --fail --location --proto '=https' --tlsv1.2 --silent --show-error "$checksum_url" -o "$checksum_file"
checksum="$(awk '{print $1}' "$checksum_file")"
printf '%s  %s\n' "$checksum" "$asset" | sha256sum --check --status || {
  printf '%s\n' 'Release checksum verification failed.' >&2
  exit 1
}

if [[ "$asset_kind" == deb ]]; then
  sudo apt-get install -y "$asset"
  dpkg-query -W -f='${Status}' m-g-linux-toolbox-v2 2>/dev/null | grep -q 'install ok installed' || {
    printf '%s\n' 'DEB installation could not be verified.' >&2
    exit 1
  }
  printf 'M.G Linux Toolbox %s installed from DEB.\n' "$version"
  exit 0
fi

if ! ldconfig -p 2>/dev/null | grep -q 'libfuse.so.2'; then
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) fuse_package=libfuse2t64; [[ "$ID" == debian ]] && fuse_package=libfuse2 ;;
    fedora) fuse_package=fuse-libs ;;
    arch) fuse_package=fuse2 ;;
    opensuse*|suse) fuse_package=fuse2 ;;
    *) fuse_package='' ;;
  esac
  if [[ -n "$fuse_package" ]]; then
    case "${ID:-}" in
      fedora) sudo dnf install -y "$fuse_package" ;;
      arch) sudo pacman -S --needed --noconfirm "$fuse_package" ;;
      opensuse*|suse) sudo zypper --non-interactive install "$fuse_package" ;;
      *) sudo apt-get update && sudo apt-get install -y "$fuse_package" ;;
    esac
  fi
fi

install_root="${XDG_DATA_HOME:-$HOME/.local/share}/mg-linux-toolbox"
applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/128x128/apps"
mkdir -p "$install_root" "$applications" "$icons"
new_appimage="$install_root/MG-Linux-Toolbox-V2.AppImage"
old_appimage="$install_root/MG-Linux-Toolbox-V2.AppImage.backup"
if [[ -f "$new_appimage" ]]; then
  cp -a -- "$new_appimage" "$old_appimage"
fi
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$rollback_file"
chmod +x "$rollback_file"
if [[ -f "$new_appimage" ]]; then
  printf '%s\n' "cp -a '$old_appimage' '$new_appimage'" > "$rollback_file"
fi

chmod 0755 "$asset"
mv -- "$asset" "$new_appimage"
extract_dir="$tmpdir/squashfs-root"
(cd "$tmpdir" && APPIMAGE_EXTRACT_AND_RUN=1 "$new_appimage" --appimage-extract >/dev/null)
[[ -d "$extract_dir" ]] || { printf '%s\n' 'Could not extract the AppImage payload.' >&2; exit 1; }

helper_dir="$tmpdir/helpers"
mkdir -p "$helper_dir"
mapfile -t helpers < <(find "$extract_dir" -type f -name 'mg-linux-toolbox-*-helper' -perm -u=x -print)
(( ${#helpers[@]} >= 6 )) || { printf '%s\n' 'The AppImage does not contain all privileged helpers.' >&2; exit 1; }
mapfile -t policies < <(find "$extract_dir" -type f -name 'com.mg.linuxtoolbox.*.policy' -print)
(( ${#policies[@]} >= 6 )) || { printf '%s\n' 'The AppImage does not contain all Polkit policies.' >&2; exit 1; }

printf '%s\n' '#!/usr/bin/env bash' 'set -e' > "$rollback_file"
sudo install -d -m 0755 /usr/lib/mg-linux-toolbox /usr/share/polkit-1/actions /usr/lib/systemd/system
for helper in "${helpers[@]}"; do
  name="$(basename "$helper")"
  sudo install -m 0755 "$helper" "/usr/lib/mg-linux-toolbox/$name"
  printf 'sudo rm -f %q\n' "/usr/lib/mg-linux-toolbox/$name" >> "$rollback_file"
done
for policy in "${policies[@]}"; do
  name="$(basename "$policy")"
  sudo install -m 0644 "$policy" "/usr/share/polkit-1/actions/$name"
  printf 'sudo rm -f %q\n' "/usr/share/polkit-1/actions/$name" >> "$rollback_file"
done
service="$(find "$extract_dir" -type f -name 'mg-linux-toolbox-*.service' -print -quit)"
if [[ -n "$service" ]]; then
  sudo install -m 0644 "$service" /usr/lib/systemd/system/"$(basename "$service")"
  sudo systemctl daemon-reload || true
fi

icon="$(find "$extract_dir" -type f \( -name '*.png' -o -name '*.svg' \) -print -quit || true)"
[[ -z "$icon" ]] || cp -- "$icon" "$icons/mg-linux-toolbox.png"
cat > "$applications/mg-linux-toolbox.desktop" <<EOF
[Desktop Entry]
Name=M.G Linux Toolbox V2
Exec=$new_appimage
Icon=mg-linux-toolbox
Type=Application
Categories=System;Utility;
Terminal=false
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$applications" >/dev/null 2>&1 || true
[[ -x "$new_appimage" && -x /usr/lib/mg-linux-toolbox/mg-linux-toolbox-apps-helper ]] || {
  printf '%s\n' 'Installation verification failed; previous AppImage was preserved.' >&2
  exit 1
}
rollback_needed=0
printf 'M.G Linux Toolbox %s installed as AppImage with helpers and policies.\n' "$version"
