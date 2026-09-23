# M.G Linux Toolbox V2

English | [Italiano](README_IT.md)

M.G Linux Toolbox is an open-source cross-distro Linux desktop utility for
system information, performance, software management, gaming, cleanup and
application installation.

## Supported distributions

- Ubuntu and Debian: `.deb` package.
- Fedora, Arch Linux and openSUSE: AppImage.

Official releases support amd64. Feature availability depends on the kernel,
hardware, drivers and configured repositories.

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

## License

The original M.G Linux Toolbox code is licensed under
[GPL-3.0-or-later](LICENSE). See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)
for bundled third-party materials and [TRADEMARKS.md](TRADEMARKS.md) for the
project name and official identity.

## Author

Developed by **Gregorio Mangano**. Project website:
<https://www.manganogregorio.it/mg-linux-toolbox.html>.
