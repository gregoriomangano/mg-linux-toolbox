"""
Pure string transforms for kernel command lines and /etc/default/grub —
no I/O, no subprocess, importable both by core/bootloader_iommu.py and
by the privileged helper (which embeds this module verbatim). Keeping
the transforms here means the unit tests exercise the exact code the
root-side transaction runs.
"""
import re

GRUB_KEY = "GRUB_CMDLINE_LINUX_DEFAULT"


def iommu_params(vendor: str) -> list:
    prefix = "amd_iommu" if vendor == "amd" else "intel_iommu"
    return [f"{prefix}=on", "iommu=pt"]


def _keys_of(tokens: list) -> set:
    return {t.split("=")[0] for t in tokens}


def apply_params_to_cmdline(current: str, params: list, remove: bool) -> str:
    """Adds or removes exactly the given tokens, never touching any other
    parameter already on the line."""
    tokens = current.split()
    keys_to_touch = _keys_of(params)
    tokens = [t for t in tokens if t.split("=")[0] not in keys_to_touch]
    if not remove:
        tokens.extend(params)
    return " ".join(tokens)


def update_grub_default_content(content: str, params: list, remove: bool) -> str:
    pattern = re.compile(rf'^{GRUB_KEY}="([^"]*)"', re.MULTILINE)
    match = pattern.search(content)
    if match is None:
        if remove:
            return content  # nothing to remove
        new_line = f'{GRUB_KEY}="{" ".join(params)}"\n'
        return content.rstrip("\n") + "\n" + new_line if content.strip() else new_line
    new_value = apply_params_to_cmdline(match.group(1), params, remove)
    new_line = f'{GRUB_KEY}="{new_value}"'
    return content[:match.start()] + new_line + content[match.end():]
