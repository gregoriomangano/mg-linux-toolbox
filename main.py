#!/usr/bin/env python3
import sys
import os

# Make sure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Checked here too (AppRun already checks this before Python even
# starts, for the AppImage case) so `python3 main.py` from an old
# source checkout also fails with one clear sentence instead of an
# AttributeError deep inside widget construction the first time the
# app happens to call a libadwaita 1.4+-only method.
from core.version import check_runtime_requirements, APP_NAME

_req = check_runtime_requirements()
if not _req["ok"]:
    found = _req.get("found", {})
    required = _req["required"]
    print(f"{APP_NAME} richiede una versione recente di GTK4 e Libadwaita.", file=sys.stderr)
    print("La versione presente su questo sistema è troppo vecchia.", file=sys.stderr)
    if _req["reason"] == "import_failed":
        print("PyGObject, GTK4 o Libadwaita non risultano installati.", file=sys.stderr)
    print(f"Trovata: {found}", file=sys.stderr)
    print(f"Richiesta: {required}", file=sys.stderr)
    sys.exit(1)

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from ui.window import LinuxToolboxApp

if __name__ == "__main__":
    app = LinuxToolboxApp()
    sys.exit(app.run(sys.argv))
