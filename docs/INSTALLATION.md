# Installing M.G Linux Toolbox

[Italiano](INSTALLAZIONE.md) | [English](INSTALLATION.md)

This guide is written for people who have never used a terminal as well.

## Current status

The new AppImage is associated with the official 0.9.0 Beta 3 Release.

Download it only from [Release v0.9.0-beta.3](https://github.com/gregoriomangano/mg-linux-toolbox/releases/tag/v0.9.0-beta.3).

Do not use AppImages from old backups as if they were the final version.

## Where to download the AppImage

Use only the official link published in the README and on the project page. The same page provides:

- the AppImage for a supported architecture;
- its SHA-256 checksum;
- the release notes.

The file for x86_64 computers is `MG-Linux-Toolbox-0.9.0-beta.3-x86_64.AppImage`.

## Making the file executable without a terminal

1. Open the **Downloads** folder in your file manager.
2. Right-click the AppImage.
3. Open **Properties**, then **Permissions**.
4. Enable the option that allows the file to run as a program.
5. Close the properties window and double-click the AppImage.

The exact wording may differ between Files, Dolphin, Nemo, and other file managers.

## Terminal alternative

Open a terminal in the folder containing the downloaded file and run:

```bash
chmod +x "MG-Linux-Toolbox-0.9.0-beta.3-x86_64.AppImage"
./"MG-Linux-Toolbox-0.9.0-beta.3-x86_64.AppImage"
```

Always compare the SHA-256 checksum with the one published in the same Release before starting the file.

## Automatic method

The `install.sh` script supports systems in the apt, dnf, pacman, and zypper families. It detects the x86_64 architecture, checks Python 3, GTK4, Libadwaita, PyGObject, and FUSE, shows missing components first, and asks for confirmation. It uses `sudo` only for any missing system packages.

Run:

```bash
curl -fsSL https://raw.githubusercontent.com/gregoriomangano/mg-linux-toolbox/main/install.sh | bash
```

The script downloads the AppImage and checksum from the Release, rejects an altered file, and installs everything under `~/.local/opt/mg-linux-toolbox` in the user's home.

When it completes, **M.G Linux Toolbox** will appear in the applications menu with the MG icon. Do not run the script as root and do not put `sudo` before the command.

## Required components

### FUSE

FUSE lets many AppImages mount their contents while starting. If an error names FUSE, open your distribution's software manager and look for the FUSE package compatible with that version. Package names differ and should not be guessed.

### GTK4

GTK4 draws the windows, buttons, and panels. A system that cannot provide a compatible GTK4 version is too old for this edition.

### Libadwaita

Libadwaita provides the modern graphical components used by the interface. It must be available together with the introspection data supplied by the distribution.

### PyGObject

PyGObject connects Python to GTK4 and Libadwaita. Depending on the distribution, the package name may include `python3-gi`, `python-gobject`, or `python3-gobject`.

The automatic method checks packages that are actually available before offering installation. For a manual setup, use your distribution's documentation or software manager.

## Distribution status

- **Tested:** Pop!_OS 24.04 LTS in the local validation environment.
- **Expected compatibility:** modern Ubuntu, Fedora, Arch Linux, and openSUSE family systems with suitable dependencies.
- **Unverified:** any distribution or version without dedicated testing.
- **System too old:** a system that cannot provide compatible GTK4, Libadwaita, or PyGObject packages.

Expected compatibility is not a promise for every derivative.

## If the app does not open

1. Check that the file is executable.
2. Check that its architecture matches the computer.
3. Look for FUSE, GTK, Adwaita, or `gi` in the error.
4. Start the file from a terminal to see the complete error.
5. Read [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Collecting an error without sharing personal data

For a source checkout, save only errors with:

```bash
python3 main.py 2>mg-toolbox-error.txt
```

Before sharing the file:

- open it in a text editor;
- remove your user name, computer name, and home-directory paths;
- remove IP addresses, network names, disk models, or serial numbers unless required;
- do not share databases, history, full desktop screenshots, or personal configuration files.

The exact AppImage command will be documented in the official Release.

## Updating

For a managed installation, run the same automatic command again. The script compares the available version, verifies the new checksum, and temporarily keeps the previous AppImage. If the download or verification fails, the installed version is not replaced. History, settings, and recovery points remain unchanged.

For a manually used AppImage, download the new file from the Release, verify its checksum, close the previous version, and start the new one. Remove the older AppImage only after checking the new launch.

## Uninstalling

### Manually used AppImage

Close the program and move only the downloaded AppImage to the trash. Shared components such as GTK4 or FUSE do not need to be removed.

### Managed installation

For a normal uninstall, run:

```bash
curl -fsSL https://raw.githubusercontent.com/gregoriomangano/mg-linux-toolbox/main/uninstall.sh | bash
```

The script removes only the installed AppImage, launcher command, menu entry, icon, and previous-AppImage backup. It does not remove Python, GTK4, Libadwaita, PyGObject, or FUSE, and it keeps personal data.

To also delete history, settings, and recovery points, download `uninstall.sh`, make it executable, run `./uninstall.sh --purge`, and type the exact confirmation shown by the script.

## Where history and data remain

Default locations:

- `~/.local/share/mg-linux-toolbox`: history database and restore points;
- `~/.local/state/mg-linux-toolbox`: application state and records.

If the system uses `XDG_DATA_HOME` or `XDG_STATE_HOME`, the corresponding directories defined by those variables are used.
