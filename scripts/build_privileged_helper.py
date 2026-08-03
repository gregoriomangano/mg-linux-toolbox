#!/usr/bin/env python3
"""
Generates packaging/helper/mg-privileged-helper — the single,
self-contained privileged helper that install.sh copies (root-owned)
into /usr/libexec/mg-linux-toolbox/.

Why generated instead of hand-written: the helper must contain exactly
the same writer code the repository tests exercise (core/priv_writer.py
and the persistence stores), with zero imports from the AppImage mount.
This script embeds each dependency module's source verbatim and
registers it in sys.modules before executing priv_writer's source, so
`from core.persistence import sysctl_store` keeps working unchanged
inside a file that ships alone.

Deterministic: same inputs -> byte-identical output (test-enforced by
tests/test_privileged_helper.py). Run from the repository root:

    python3 scripts/build_privileged_helper.py
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_PATH = os.path.join(REPO_ROOT, "packaging", "helper", "mg-privileged-helper")

# Order matters: dependencies first. Each entry is (module_name, path).
EMBEDDED_MODULES = [
    ("core", None),                       # package stub
    ("core.persistence", None),           # package stub
    ("core.kernel_features", None),       # package stub
    ("core.privileged", None),            # package stub
    ("core.persistence.atomic_io", "core/persistence/atomic_io.py"),
    ("core.persistence.rollback_store", "core/persistence/rollback_store.py"),
    ("core.persistence.sysctl_store", "core/persistence/sysctl_store.py"),
    ("core.persistence.tmpfiles_store", "core/persistence/tmpfiles_store.py"),
    ("core.persistence.selinux_config_store", "core/persistence/selinux_config_store.py"),
    ("core.kernel_features.base", "core/kernel_features/base.py"),
    ("core.privileged.cmdline_edit", "core/privileged/cmdline_edit.py"),
]
MAIN_MODULE_PATH = "core/priv_writer.py"

HEADER = '''#!/usr/bin/env python3
# mg-privileged-helper — M.G Linux Toolbox privileged component.
#
# GENERATED FILE — do not edit by hand. Regenerate with:
#     python3 scripts/build_privileged_helper.py
#
# This file is self-contained on purpose: it is installed root-owned at
# /usr/libexec/mg-linux-toolbox/mg-privileged-helper and invoked via a
# dedicated Polkit action. It never imports anything from the AppImage
# mount or any user-writable location.
import sys
import types

_EMBEDDED_SOURCES = {}


def _register(name, source):
    module = types.ModuleType(name)
    module.__package__ = name if source is None else name.rpartition(".")[0]
    if source is None:
        module.__path__ = []  # mark as package
    sys.modules[name] = module
    if source is not None:
        exec(compile(source, f"<embedded {name}>", "exec"), module.__dict__)
    parent_name, _, child = name.rpartition(".")
    if parent_name and parent_name in sys.modules:
        setattr(sys.modules[parent_name], child, module)
    return module

'''

FOOTER = '''
for _name, _source_key in _EMBED_ORDER:
    _register(_name, _EMBEDDED_SOURCES.get(_source_key) if _source_key else None)

_main_globals = {"__name__": "__main__", "__file__": __file__}
exec(compile(_EMBEDDED_SOURCES["__main__"], "<embedded core.priv_writer>", "exec"), _main_globals)
'''


def read_source(rel_path: str) -> str:
    with open(os.path.join(REPO_ROOT, rel_path), encoding="utf-8") as f:
        return f.read()


def _extract_marker(source: str, marker: str) -> str:
    for line in source.splitlines():
        if line.startswith(marker):
            return line.partition("=")[2].strip().strip('"')
    raise SystemExit(f"marker {marker} not found in {MAIN_MODULE_PATH}")


def build() -> str:
    main_probe = read_source(MAIN_MODULE_PATH)
    version = _extract_marker(main_probe, "MG_HELPER_VERSION")
    protocol = _extract_marker(main_probe, "MG_HELPER_PROTOCOL")
    # Literal marker lines near the top so the client can learn the
    # installed helper's version by reading the file — never executing it.
    parts = [HEADER,
             f'MG_HELPER_VERSION = "{version}"\n',
             f'MG_HELPER_PROTOCOL = "{protocol}"\n\n']
    embed_order = []
    for name, rel_path in EMBEDDED_MODULES:
        if rel_path is None:
            embed_order.append((name, None))
            continue
        key = name
        source = read_source(rel_path)
        parts.append(f"_EMBEDDED_SOURCES[{key!r}] = {source!r}\n")
        embed_order.append((name, key))
    main_source = read_source(MAIN_MODULE_PATH)
    # The standalone file must never extend sys.path towards its own
    # directory's parent (that made sense running from the source tree).
    main_source = main_source.replace(
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")\n', "")
    parts.append(f"_EMBEDDED_SOURCES['__main__'] = {main_source!r}\n")
    parts.append(f"_EMBED_ORDER = {embed_order!r}\n")
    parts.append(FOOTER)
    return "".join(parts)


def main():
    content = build()
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(OUTPUT_PATH, 0o755)
    print(f"written {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH)} bytes)")


if __name__ == "__main__":
    sys.exit(main())
