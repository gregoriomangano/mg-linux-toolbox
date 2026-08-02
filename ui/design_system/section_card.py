"""
section_card — centralizes the "titled Adw.PreferencesGroup, optionally
with a one-line description" pattern so every migrated page builds its
sections the same way instead of each rolling its own slightly
different make_group() call. Wraps native Adw.PreferencesGroup — does
not reimplement grouping/layout.
"""
import gi
gi.require_version("Adw", "1")
from gi.repository import Adw

from core.i18n import T, on_change


def make_section(title_key: str, description_key: str = None) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup()
    group.set_title(T(title_key))
    on_change(lambda: group.set_title(T(title_key)))
    if description_key:
        group.set_description(T(description_key))
        on_change(lambda: group.set_description(T(description_key)))
    return group
