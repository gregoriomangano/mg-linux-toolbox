# Privileged helpers

The `.deb` and AUR packages must install the root-owned helper at
`/usr/lib/mg-linux-toolbox/mg-linux-toolbox-performance-helper` with mode `0755`,
and the policy at `/usr/share/polkit-1/actions/com.mg.linuxtoolbox.performance.policy`
with mode `0644`.

The AppImage never installs system files. It detects this helper and keeps the
performance profiles runtime-only when the package-provided infrastructure is absent.
The helper accepts only `apply performance|balanced|saving`, `restore`, and the
root-only `apply-persisted` boot action; it does not accept paths, commands,
environment-controlled roots, or arbitrary arguments. Packages should also install
and enable `packaging/systemd/mg-linux-toolbox-performance.service`.

## Repository helper (APT V1)

A second, separate helper and polkit action, deliberately kept apart from the
performance helper so a bug in one privileged surface can never authorize the
other: `/usr/lib/mg-linux-toolbox/mg-linux-toolbox-repository-helper` (mode
`0755`) and `/usr/share/polkit-1/actions/com.mg.linuxtoolbox.repository.policy`
(mode `0644`). It accepts only the two closed operations `apt-repo-set
<file> <list|sources> <index> <enable|disable> <sha256>` and `apt-repo-restore
<file> <sha256>`; it re-derives the allow-listed APT source files itself from
`apt-config` and re-parses the target before writing, it never trusts a path
or byte offset from the frontend as-is. No systemd unit needed. Use
`install-dev-repository-helper.sh` for local development.
