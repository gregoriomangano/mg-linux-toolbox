#!/usr/bin/env python3
import sys
import os

# Make sure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from ui.window import LinuxToolboxApp

if __name__ == "__main__":
    app = LinuxToolboxApp()
    sys.exit(app.run(sys.argv))
