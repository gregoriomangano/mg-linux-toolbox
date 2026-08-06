"""
Single source of truth for the app's name/version/channel. Every other
part of the app (About page, updater, diagnostics, changelog, packaging
scripts) reads from here — never duplicate the version string elsewhere.
"""

APP_NAME = "M.G Linux Toolbox"
APP_VERSION = "0.9.0-beta.6"
UPDATE_CHANNEL = "beta"  # "stable" | "beta" — controls which releases the updater considers


def display_version() -> str:
    return f"{APP_NAME} {APP_VERSION}"


# ── Minimum runtime requirements ────────────────────────────────────
# Determined empirically for Beta 4 (2026-08-03), not assumed: tested
# in real Debian 12 (libadwaita 1.2.2, GTK4 4.8.3, PyGObject 3.42.2,
# Python 3.11.2), Fedora, Arch and openSUSE (all libadwaita 1.8+)
# Distrobox containers. Debian 12's construction of every page failed
# with AttributeError on Adw.ExpanderRow.add_suffix(), Adw.ToolbarView
# and Adw.Breakpoint/BreakpointCondition — all three introduced in
# libadwaita 1.4, none present below it. GTK4 and PyGObject themselves
# were NOT the limiting factor: Debian 12's GTK4 4.8.3 and PyGObject
# 3.42.2 constructed every widget this app uses without error once
# libadwaita is new enough — a system with libadwaita >= 1.4 installed
# always carries a GTK4 new enough to satisfy libadwaita's own build
# requirement, so checking libadwaita's version alone is sufficient;
# no separate GTK4-only floor is enforced. Python's floor is the oldest
# interpreter this was actually run against (3.11) — nothing in the
# codebase requires more (a couple of bare `X | None` annotations that
# would have needed 3.10 were quoted as strings in this same Beta 4
# pass, so the real requirement may be lower still, but 3.11 is what
# was verified).
MIN_LIBADWAITA_VERSION = (1, 4, 0)
MIN_GTK_VERSION = (4, 8, 0)      # not the binding constraint — see note above
MIN_PYGOBJECT_VERSION = (3, 42, 0)
MIN_PYTHON_VERSION = (3, 11)


def _format_version(v: tuple) -> str:
    return ".".join(str(p) for p in v)


def check_runtime_requirements() -> dict:
    """
    Real, imported-at-runtime version check — never a guess from a
    distro name. Returns {"ok": bool, "found": {...}, "required": {...},
    "reason": str}. Only ever imports what's already guaranteed
    available (gi itself) — a system entirely missing PyGObject/GTK4/
    Adwaita is reported as "ok": False with reason "import_failed",
    never a raw ImportError/traceback.
    """
    import sys

    required = {
        "python": MIN_PYTHON_VERSION,
        "gtk": MIN_GTK_VERSION,
        "libadwaita": MIN_LIBADWAITA_VERSION,
        "pygobject": MIN_PYGOBJECT_VERSION,
    }

    py_found = sys.version_info[:2]
    if py_found < MIN_PYTHON_VERSION:
        return {"ok": False, "reason": "python_too_old",
                "found": {"python": _format_version(py_found)},
                "required": {k: _format_version(v) for k, v in required.items()}}

    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk, Adw
        Adw.init()
        gtk_found = (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())
        adw_found = (Adw.MAJOR_VERSION, Adw.MINOR_VERSION, Adw.MICRO_VERSION)
        pygobject_found = tuple(int(p) for p in gi.__version__.split(".")[:3])
    except Exception:
        return {"ok": False, "reason": "import_failed",
                "found": {"python": _format_version(py_found)},
                "required": {k: _format_version(v) for k, v in required.items()}}

    found = {
        "python": _format_version(py_found),
        "gtk": _format_version(gtk_found),
        "libadwaita": _format_version(adw_found),
        "pygobject": _format_version(pygobject_found),
    }
    ok = (gtk_found >= MIN_GTK_VERSION and adw_found >= MIN_LIBADWAITA_VERSION
          and pygobject_found >= MIN_PYGOBJECT_VERSION)
    return {"ok": ok, "found": found,
            "required": {k: _format_version(v) for k, v in required.items()},
            "reason": "" if ok else "version_too_old"}
