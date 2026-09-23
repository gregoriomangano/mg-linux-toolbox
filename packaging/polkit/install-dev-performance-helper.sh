#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
helper="$project_root/src-tauri/target/release/mg-linux-toolbox-performance-helper"
policy="$project_root/packaging/polkit/com.mg.linuxtoolbox.performance.policy"
unit="$project_root/packaging/systemd/mg-linux-toolbox-performance.service"

test -x "$helper"
test -f "$policy"
test -f "$unit"

pkexec --disable-internal-agent /usr/bin/install -d -m 0755 /usr/lib/mg-linux-toolbox
pkexec --disable-internal-agent /usr/bin/install -o root -g root -m 0755 "$helper" /usr/lib/mg-linux-toolbox/mg-linux-toolbox-performance-helper
pkexec --disable-internal-agent /usr/bin/install -o root -g root -m 0644 "$policy" /usr/share/polkit-1/actions/com.mg.linuxtoolbox.performance.policy
pkexec --disable-internal-agent /usr/bin/install -d -m 0755 /usr/lib/systemd/system
pkexec --disable-internal-agent /usr/bin/install -o root -g root -m 0644 "$unit" /usr/lib/systemd/system/mg-linux-toolbox-performance.service
pkexec --disable-internal-agent systemctl daemon-reload
pkexec --disable-internal-agent systemctl enable mg-linux-toolbox-performance.service

echo "DEV helper and Polkit policy installed."
