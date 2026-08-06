import os
import platform
import shutil


class DistroManager:
    """Detects Linux distribution and returns the correct package manager commands."""

    def __init__(self):
        self.id = ""
        self.id_like = ""
        self._detect()

    def _detect(self):
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ID="):
                        self.id = line.split("=", 1)[1].strip('"').lower()
                    elif line.startswith("ID_LIKE="):
                        self.id_like = line.split("=", 1)[1].strip('"').lower()
        except Exception:
            self.id = "debian"

    @property
    def is_debian(self):
        return any(x in (self.id + " " + self.id_like) for x in ["debian", "ubuntu", "pop", "mint", "elementary", "kali", "mxlinux"])

    @property
    def is_arch(self):
        return any(x in (self.id + " " + self.id_like) for x in ["arch", "manjaro", "garuda", "endeavour"])

    @property
    def is_fedora(self):
        return any(x in (self.id + " " + self.id_like) for x in ["fedora", "rhel", "centos", "rocky", "alma"])

    @property
    def is_opensuse(self):
        return any(x in (self.id + " " + self.id_like) for x in ["opensuse", "suse", "sles"])

    def install_cmd(self, packages: dict) -> list:
        """
        packages: {"debian": "pkg-name", "arch": "pkg-name", "fedora": "pkg-name", "opensuse": "pkg-name"}
        Returns the list of command args (without pkexec).
        """
        if self.is_arch:
            pkg = packages.get("arch", packages.get("default", ""))
            return ["pacman", "-S", "--noconfirm", pkg] if pkg else []
        elif self.is_opensuse:
            pkg = packages.get("opensuse", packages.get("default", ""))
            return ["zypper", "--non-interactive", "install", pkg] if pkg else []
        elif self.is_fedora:
            pkg = packages.get("fedora", packages.get("default", ""))
            return ["dnf", "install", "-y", pkg] if pkg else []
        else:  # default to debian
            pkg = packages.get("debian", packages.get("default", ""))
            return ["apt-get", "install", "-y", pkg] if pkg else []

    def remove_cmd(self, packages: dict) -> list:
        """
        Same package-name map as install_cmd(), the removal side.
        Never purges/autoremoves — only takes the named package(s) off,
        so it never cascades into removing unrelated dependents.
        """
        if self.is_arch:
            pkg = packages.get("arch", packages.get("default", ""))
            return ["pacman", "-R", "--noconfirm", pkg] if pkg else []
        elif self.is_opensuse:
            pkg = packages.get("opensuse", packages.get("default", ""))
            return ["zypper", "--non-interactive", "remove", pkg] if pkg else []
        elif self.is_fedora:
            pkg = packages.get("fedora", packages.get("default", ""))
            return ["dnf", "remove", "-y", pkg] if pkg else []
        else:  # default to debian
            pkg = packages.get("debian", packages.get("default", ""))
            return ["apt-get", "remove", "-y", pkg] if pkg else []

    def is_installed(self, packages: dict) -> bool:
        """
        Check if a package is installed, using the package database that
        actually exists on this distro (this used to always fall back to
        dpkg, which silently reported "not installed" for everything on
        Fedora/openSUSE since they don't have dpkg at all).
        """
        from core.executor import run_command
        if self.is_arch:
            pkg = packages.get("arch", packages.get("default", ""))
            if not pkg:
                return False
            ok, _, _ = run_command(["pacman", "-Qi", pkg])
            return ok
        elif self.is_fedora or self.is_opensuse:
            # Both RHEL-family and openSUSE are RPM-based — `rpm -q` works
            # identically on either, regardless of dnf vs zypper on top.
            pkg = packages.get("fedora" if self.is_fedora else "opensuse",
                                packages.get("default", ""))
            if not pkg:
                return False
            ok, _, _ = run_command(["rpm", "-q", pkg])
            return ok
        else:
            pkg = packages.get("debian", packages.get("default", ""))
            if not pkg:
                return False
            ok, out, _ = run_command(["dpkg", "-l", pkg])
            return ok and "ii" in out


distro = DistroManager()


class DistroContext:
    """
    Read-only snapshot of everything the rest of the app needs to know
    about "which machine is this" in one place: identity (id/id_like/
    version), package manager, init system, bootloader, root filesystem,
    kernel and architecture.

    This does NOT replace `DistroManager`/`distro` above — `is_arch`/
    `is_fedora`/`is_opensuse`/`install_cmd`/`is_installed` and the
    `packages: dict` shape they use are depended on by `core/repo_check.py`
    and `backend/all.py`, so that surface stays exactly as-is. This class
    is the single place new features (history, checkpoints, virtualization,
    AppArmor/SELinux, updates) go to answer "what distro/provider is this
    entry about", instead of re-deriving it ad hoc.
    """

    def __init__(self, manager: "DistroManager | None" = None,
                 proc_root: str = "/proc", sys_root: str = "/sys"):
        self._m = manager or distro
        self.proc_root = proc_root
        self.sys_root = sys_root
        self.version_id = self._read_os_release_field("VERSION_ID")
        self.pretty_name = self._read_os_release_field("PRETTY_NAME")

    @property
    def id(self) -> str:
        return self._m.id

    @property
    def id_like(self) -> str:
        return self._m.id_like

    @property
    def family(self) -> str:
        """One of 'arch', 'fedora', 'opensuse', 'debian' — never a mix."""
        if self._m.is_arch:
            return "arch"
        if self._m.is_fedora:
            return "fedora"
        if self._m.is_opensuse:
            return "opensuse"
        return "debian"

    @property
    def package_manager(self) -> str:
        return {
            "arch": "pacman",
            "fedora": "dnf",
            "opensuse": "zypper",
            "debian": "apt",
        }[self.family]

    @property
    def init_system(self) -> str:
        if os.path.isdir("/run/systemd/system"):
            return "systemd"
        if shutil.which("openrc"):
            return "openrc"
        if os.path.exists("/etc/init.d") and not shutil.which("systemctl"):
            return "sysvinit"
        return "unknown"

    @property
    def bootloader(self) -> str:
        """Best-effort, read-only detection — never assumed for a write
        without also checking the on-disk evidence at the point of use."""
        if shutil.which("kernelstub") or os.path.isdir("/boot/efi/EFI/Pop_OS-current"):
            return "kernelstub"
        if os.path.isdir("/boot/loader/entries") or shutil.which("bootctl"):
            return "systemd-boot"
        if (os.path.exists("/boot/grub/grub.cfg")
                or os.path.exists("/boot/grub2/grub.cfg")
                or shutil.which("grub-mkconfig")
                or shutil.which("grub2-mkconfig")):
            return "grub"
        return "unknown"

    @property
    def root_filesystem(self) -> str:
        try:
            with open(os.path.join(self.proc_root, "mounts")) as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1] == "/":
                        return parts[2]
        except OSError:
            pass
        return "unknown"

    @property
    def kernel_version(self) -> str:
        try:
            return platform.release()
        except Exception:
            return "unknown"

    @property
    def architecture(self) -> str:
        try:
            return platform.machine()
        except Exception:
            return "unknown"

    def _read_os_release_field(self, field: str) -> str:
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(field + "="):
                        return line.split("=", 1)[1].strip('"')
        except OSError:
            pass
        return ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "id_like": self.id_like,
            "version_id": self.version_id,
            "family": self.family,
            "package_manager": self.package_manager,
            "init_system": self.init_system,
            "bootloader": self.bootloader,
            "root_filesystem": self.root_filesystem,
            "kernel_version": self.kernel_version,
            "architecture": self.architecture,
        }


def get_context() -> DistroContext:
    """Fresh snapshot each call — cheap (a handful of file reads), and
    callers logging history entries want the *current* bootloader/kernel,
    not one cached at import time."""
    return DistroContext()
