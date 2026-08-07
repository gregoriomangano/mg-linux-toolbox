[Italiano](README.md) | [English](README_EN.md)

# M.G Linux Toolbox

M.G Linux Toolbox brings together Linux features that would normally require the terminal in a simple graphical interface.

## Project status

Version **0.9.0 Beta 7** is the current prerelease candidate. It adds ClamAV antivirus management (install from official repositories, signature updates and file/folder scanning) and splits the former "Network & Security" page into "Network & Devices" and "Security".

The verified AppImage package and its checksum are associated with [Release v0.9.0-beta.7](https://github.com/gregoriomangano/mg-linux-toolbox/releases/tag/v0.9.0-beta.7).

The code is distributed under the **GPL-3.0-or-later** license. The project name, logo, and identity are addressed separately in [TRADEMARKS.md](TRADEMARKS.md).

## Main screenshots

| Overview | Kernel features |
|---|---|
| [![M.G Linux Toolbox overview](docs/images/screenshots/panoramica.png)](docs/images/screenshots/panoramica.png) | [![Kernel features](docs/images/screenshots/funzioni-kernel.png)](docs/images/screenshots/funzioni-kernel.png) |
| System and disks | Power and battery |
| [![System and disks](docs/images/screenshots/sistema-disco.png)](docs/images/screenshots/sistema-disco.png) | [![Power and battery](docs/images/screenshots/energia-batteria.png)](docs/images/screenshots/energia-batteria.png) |
| Network and devices | Gaming |
| [![Network and devices](docs/images/screenshots/rete-sicurezza.png)](docs/images/screenshots/rete-sicurezza.png) | [![Gaming](docs/images/screenshots/gaming.png)](docs/images/screenshots/gaming.png) |
| Security | History and recovery |
| [![Security](docs/images/screenshots/sicurezza.png)](docs/images/screenshots/sicurezza.png) | [![History and recovery](docs/images/screenshots/cronologia-ripristino.png)](docs/images/screenshots/cronologia-ripristino.png) |

All approved screenshots are available in the [complete gallery](docs/SCREENSHOTS.md).

## What it lets you do

M.G Linux Toolbox makes Linux features and settings that would normally require the terminal easier to use.

The program detects what the kernel, hardware, and system actually support, explains every feature in plain language and, when possible, lets you try it temporarily before making it permanent.

A feature may show:

- its risk level;
- what it is;
- its possible benefit;
- when it should be avoided;
- its current state;
- its initial value;
- a test that lasts until reboot;
- a permanent change, when supported;
- how to restore the previous value.

## Kernel features

This section shows only features detected on the current computer. It may include processor management, memory, swap, ZRAM, Zswap, Transparent Huge Pages, disk schedulers, and other capabilities offered by the kernel.

Availability changes with the kernel, hardware, drivers, and distribution. An unavailable item is not presented as usable.

## Program areas

- **System and disks:** system information, devices, TRIM, SMART, controlled maintenance, and live disk activity based on `/proc` and `/sys` data.
- **Network and devices:** Wi-Fi, hotspot, Bluetooth, IPv6, Samba file sharing, and DNS (including DNS-over-TLS).
- **Power and battery:** power profiles, suspend, battery, and device power saving.
- **Audio:** PipeWire status, devices, audio restart, and audio power-saving options.
- **Printers:** CUPS service, basic support, and detected drivers.
- **Software and repositories:** Flatpak and Flathub status, detected software sources, and package-health checks with distribution-aware behavior.
- **Gaming:** GameMode, Vulkan, libraries, and tools commonly used for gaming; the Gaming Pack checks real availability in configured repositories and safely installs or removes only packages recorded by the Toolbox.
- **Virtualization:** KVM, IOMMU, VFIO, KSM, and container engines.
- **Services:** status, start, stop, and automatic activation of recognized services.
- **Security:** firewall, SSH access and SSH root login, automatic updates, AppArmor/SELinux, Secure Boot, and ClamAV antivirus (install from official repositories, signature updates, and file/folder scanning).
- **History and recovery:** recorded operations, recovery points, and restoration of saved values.

## Try until reboot

When a feature allows it, you can test it without making it permanent immediately. The change lasts until reboot so that you can check stability, power use, and behavior before deciding.

A temporary test does not guarantee an improvement. If the result is not useful, reboot or use the recovery option shown by the program.

## Permanent changes and recovery

Permanent changes are offered only when the system supports them. Before confirming:

1. read the risk and when the feature should be avoided;
2. note or check the initial value;
3. run a temporary test first, when available;
4. keep a recovery point;
5. verify the result after rebooting.

The history helps reconstruct performed operations. Some system changes require administrative privileges.

Beta 4 re-reads the real value after every restore: an operation is recorded as successful only when the value actually matches the initial value saved for the current trial.

## Installation

The automatic method checks dependencies, verifies the checksum, installs the AppImage in the user's home, and adds the correct name and icon to the applications menu:

```bash
curl -fsSL https://raw.githubusercontent.com/gregoriomangano/mg-linux-toolbox/main/install.sh | bash
```

Alternatively, the AppImage can be downloaded from the Release and started manually. Do not use old AppImages or links not listed through the official channels. The AppImage uses Python 3, GTK4, Libadwaita, PyGObject, and FUSE supplied by the system; the installer checks that they are present, including the real minimum versions (**Libadwaita 1.4** is the actual constraint — see the [installation guide](docs/INSTALLATION.md#real-minimum-versions-as-of-beta-4)).

See the [installation guide](docs/INSTALLATION.md) for both methods, dependencies, and FUSE.

## Updates

With the automatic installation, running the same command again downloads the newest version available for the selected channel. The checksum is checked before replacing the file; the previous version is kept temporarily and remains active if verification or the update fails.

## Uninstalling

- For an AppImage launched manually, close the program and move only the AppImage file to the trash.
- For the automatic installation, use `uninstall.sh`: it removes the AppImage, menu entry, icon, and previous-version backup.
- `uninstall.sh` keeps history, settings, and recovery points by default. The `--purge` option removes them only after explicit confirmation.
- Python, GTK4, Libadwaita, PyGObject, and FUSE are not removed because other programs may need them.

Normal removal of an automatic installation:

```bash
curl -fsSL https://raw.githubusercontent.com/gregoriomangano/mg-linux-toolbox/main/uninstall.sh | bash
```

User data is normally stored in `~/.local/share/mg-linux-toolbox` and `~/.local/state/mg-linux-toolbox`, or in the equivalent XDG directories.

## Common problems

The [Troubleshooting](docs/TROUBLESHOOTING.md) guide explains what to check when:

- the AppImage does not start;
- a FUSE error appears;
- GTK4, Libadwaita, or PyGObject is missing;
- a feature is unavailable;
- an error must be collected without sharing personal data.

## Security and limitations

M.G Linux Toolbox does not promise:

- better performance in every case;
- guaranteed higher FPS;
- faster Internet in every case;
- compatibility with every distribution;
- an absolute absence of risk.

A choice that is useful on one computer may be ineffective or counterproductive on another. Always read the explanations shown and grant privileges only for an action you recognize.

Environment verified during public preparation: **Pop!_OS 24.04 LTS**, Python 3.12, GTK 4.14, Libadwaita 1.5, and PyGObject 3.48. The Gaming Pack was verified in clean Debian 13, Fedora 44, Arch Linux, and openSUSE Tumbleweed containers; final package availability still depends on the repositories configured on the user's machine. Compatibility with other modern distributions must be tested. **Debian 12 is not declared as supported: its Libadwaita 1.2.2 is below the real minimum required version (1.4)**, verified empirically in a Debian 12 container — the app detects this and says so clearly instead of failing with a technical error.

For private security reports, see [SECURITY.md](SECURITY.md). For general limitations, see [DISCLAIMER.md](DISCLAIMER.md).

## Support and donations

- Contact: <https://www.manganogregorio.it/contatti-gregorio-mangano-mondovi/>
- PayPal donation: <https://www.paypal.com/donate/?hosted_button_id=7LCEUTKBTB6HW>

Always verify the recipient before confirming a payment.

## Official links

- Website: <https://www.manganogregorio.it/>
- Project page: <https://www.manganogregorio.it/m-g-linux-toolbox/>
- YouTube channel: <https://www.youtube.com/@GregorioMangano>
- Contact: <https://www.manganogregorio.it/contatti-gregorio-mangano-mondovi/>
- Public source: <https://github.com/gregoriomangano/mg-linux-toolbox>
- Release: <https://github.com/gregoriomangano/mg-linux-toolbox/releases/tag/v0.9.0-beta.7>

## Author

M.G Linux Toolbox is developed by **Gregorio Mangano**.

## License

The code is available under the **GNU General Public License, version 3 or later** (`GPL-3.0-or-later`). The complete text is in [LICENSE](LICENSE).

The GPL applies to the code and does not automatically make modified versions official. The project name, logo, icon, and identity are described in [TRADEMARKS.md](TRADEMARKS.md).

## Building and running from source

Minimum requirements:

- Python 3;
- PyGObject;
- GTK4;
- Libadwaita.

Run from source:

```bash
python3 main.py
```

Run the automated tests:

```bash
python3 -m unittest discover -s tests
```

Preparing an AppImage also requires `rsync`, `sha256sum`, and a verified copy of `appimagetool`. The package for each Release is built from a fresh AppDir and verified before publication.
