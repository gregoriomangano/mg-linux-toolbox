#!/usr/bin/env bash
set -euo pipefail

install_root="${XDG_DATA_HOME:-$HOME/.local/share}/mg-linux-toolbox"
applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/128x128/apps/mg-linux-toolbox.png"

rm -f "$install_root/MG-Linux-Toolbox-V2.AppImage" \
  "$install_root/MG-Linux-Toolbox-V2.AppImage.backup" \
  "$applications/mg-linux-toolbox.desktop" "$icons"
rmdir "$install_root" 2>/dev/null || true

if [[ "${1:-}" == --purge ]]; then
  printf 'Remove M.G Linux Toolbox user data too? [y/N] '
  read -r answer
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    rm -rf "${XDG_STATE_HOME:-$HOME/.local/state}/mg-linux-toolbox" "$install_root"
  fi
fi

printf '%s\n' 'M.G Linux Toolbox user installation removed.'
