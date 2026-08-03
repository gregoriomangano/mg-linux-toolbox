# Troubleshooting

[Italiano](PROBLEMI_COMUNI.md) | [English](TROUBLESHOOTING.md)

## The AppImage does not open

- Check in the file properties that execution as a program is allowed.
- Verify that the file is complete using the official checksum.
- Check that the file architecture matches the computer.
- Start the file from a terminal and read the first meaningful error.

The official file is `MG-Linux-Toolbox-0.9.0-beta.4-x86_64.AppImage` and is associated with [Release v0.9.0-beta.4](https://github.com/gregoriomangano/mg-linux-toolbox/releases/tag/v0.9.0-beta.4).

## FUSE error

Install only the FUSE package documented by your distribution and suitable for that version. Do not add packages with names found at random: variants differ between systems.

When using `install.sh`, the script first shows the available package and asks for confirmation before using `sudo`. Never put `sudo` before the whole installer.

## GTK, Adwaita, or `gi` error

The system must provide GTK4, Libadwaita, PyGObject, and the required introspection data. Use the distribution's software manager or official documentation. If sufficiently recent components do not exist, the system is too old for this edition.

## "M.G Linux Toolbox requires a recent version of GTK4 and Libadwaita"

Since Beta 4, both `install.sh` and the AppImage itself check the real minimum versions before proceeding (**Libadwaita 1.4** is the actual requirement — GTK4 4.8 and PyGObject 3.42 are not the constraint). The message shows the version found and the version required, never a raw technical error.

Concrete example: Debian 12 ships Libadwaita 1.2.2, below the minimum — verified for real in a Debian 12 container while developing Beta 4. To fix it, upgrade the distribution to a newer release or install Libadwaita from your distribution's official backport repository.

## A feature is unavailable

Not every feature exists on every kernel or hardware platform. The item should show the detected state and why it is unavailable. This is safer than offering an unsupported change.

## A change does not produce the expected result

- Restore the previous value from the same feature or from history.
- If the trial was temporary, reboot the computer.
- Check whether another service manages the same setting.
- Do not make a change permanent before checking its effect.

## Administrative changes do not work (or you saw a "Permission denied" error on `/tmp/.mount_...`)

In Beta 3, when the app ran as an AppImage, password-protected changes could fail with a technical error mentioning a `/tmp/.mount_...` path. Beta 4 fixes this with a stable administrative component installed by the automatic method (`install.sh`).

If the app says the administrative component is not installed or must be updated:

1. run (or re-run) the `install.sh` script from the current Release;
2. confirm the administrative component installation when offered;
3. check from the Overview page with "Check it works".

A portable AppImage started from Downloads or a USB stick only offers the read-only features: that is expected behaviour, not a fault.

## The program asks for a password

Operations that change system settings may need administrative privileges. Read the displayed action before confirming. Close the request if you do not recognize the operation.

## The icon does not appear in the menu

After an automatic installation, close and reopen the applications menu. If necessary, sign out and back in. The entry must be named **M.G Linux Toolbox** and uses files in the personal XDG directory; it does not require changes under `/usr`.

## An update fails

Do not ignore a checksum error. The script rejects the new file before replacing the working one and keeps a temporary copy of the previous version during an update. Retry only with the official Release.

## Data after uninstalling

A normal uninstall keeps history, settings, and recovery points. Only `uninstall.sh --purge`, followed by the requested explicit confirmation, also deletes this data. Shared dependencies are not removed.

## Asking for help without exposing personal data

Share the distribution, its version, the kernel, and the minimum error text. Remove user names, hostnames, personal paths, IP addresses, network names, serial numbers, and real history. Do not attach the complete application database.
