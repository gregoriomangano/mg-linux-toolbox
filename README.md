# M.G Linux Toolbox V2

English | [Italiano](README_IT.md)

M.G Linux Toolbox is designed to make Linux easier, even for people who do not
want to use the terminal.

## Easy installation

Open the terminal, copy this command, paste it, and press Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/gregoriomangano/mg-linux-toolbox/main/install.sh | bash
```

The installer recognises your Linux distribution, downloads the right package,
checks its checksum, and creates an entry in the applications menu. It may ask
for your administrator password when needed.

## Prefer to download manually?

- Ubuntu and Debian: download the `.deb` package.
- Fedora, Arch Linux and openSUSE: download the AppImage.

Download the package from the [latest stable release](https://github.com/gregoriomangano/mg-linux-toolbox/releases/latest).

## What can it do?

- Show information about your computer, disks, network, and AI tools.
- Help manage performance settings.
- Install and manage software and repositories.
- Help clean temporary files and unused data.
- Offer useful options for gaming and DNS.
- Help install and manage Gradia, Upscayl, Curtail, Ferdium, and KDE Connect.
- Work with Flatpak, Snap, and native packages.
- Offer WinBoat and GeForce NOW where they are supported.

## Supported distributions

- Ubuntu and Debian use the `.deb` package.
- Fedora, Arch Linux, and openSUSE use the AppImage.

Official releases support amd64. Some features depend on your hardware,
drivers, Linux distribution, and configured software sources.

## Updating

To update, run the same installation command again. It will download and
install the newest available package for your distribution.

## Open source and license

M.G Linux Toolbox is open source and licensed under
[GPL-3.0-or-later](LICENSE). See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)
for included third-party materials and [TRADEMARKS.md](TRADEMARKS.md) for the
project name and official identity.

<details>
<summary><strong>For developers</strong></summary>

## Development

```bash
npm install
npm run tauri dev
```

## Tests

```bash
npm test
```

## Build

```bash
npm run tauri build
```

The release scripts compile the privileged helper binaries from
`src-tauri/src/bin/` before packaging; no generated helper is committed.

</details>
