#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
helper="$project_root/src-tauri/target/release/mg-linux-toolbox-repository-helper"
policy="$project_root/packaging/polkit/com.mg.linuxtoolbox.repository.policy"

test -x "$helper"
test -f "$policy"

pkexec --disable-internal-agent /usr/bin/install -d -m 0755 /usr/lib/mg-linux-toolbox
pkexec --disable-internal-agent /usr/bin/install -o root -g root -m 0755 "$helper" /usr/lib/mg-linux-toolbox/mg-linux-toolbox-repository-helper
pkexec --disable-internal-agent /usr/bin/install -o root -g root -m 0644 "$policy" /usr/share/polkit-1/actions/com.mg.linuxtoolbox.repository.policy

echo "DEV repository helper and Polkit policy installed."
