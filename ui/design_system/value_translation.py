"""
value_translation — presentation-only mapping from raw technical
values (exactly what the backend sends/receives: "power-saver",
"balanced", "enabled", ...) to translated display text. The technical
value itself NEVER changes — this only decides what string a page
shows for it. Unmapped values pass through unchanged (same rule
ChoiceKernelFeatureRow._display_value already follows for governor/EPP
values it can't honestly translate).
"""
from core.i18n import T, on_change
from core import i18n as _i18n_mod

_ds_value_strings = {
    "ds_val_power_saver":  {"en": "Power saver", "it": "Risparmio energetico", "es": "Ahorro de energía", "fr": "Économie d'énergie"},
    "ds_val_balanced":     {"en": "Balanced", "it": "Bilanciato", "es": "Equilibrado", "fr": "Équilibré"},
    "ds_val_performance":  {"en": "Performance", "it": "Prestazioni", "es": "Rendimiento", "fr": "Performance"},
    "ds_val_enabled":      {"en": "Enabled", "it": "Attivo", "es": "Activado", "fr": "Activé"},
    "ds_val_disabled":     {"en": "Disabled", "it": "Disattivato", "es": "Desactivado", "fr": "Désactivé"},
    "ds_val_installed":    {"en": "Installed", "it": "Installato", "es": "Instalado", "fr": "Installé"},
    "ds_val_not_installed": {"en": "Not installed", "it": "Non installato", "es": "No instalado", "fr": "Non installé"},
    "ds_val_unknown":      {"en": "Unknown status", "it": "Stato sconosciuto", "es": "Estado desconocido", "fr": "État inconnu"},
    "ds_val_not_available": {"en": "Not available", "it": "Non disponibile", "es": "No disponible", "fr": "Non disponible"},
}
for _k, _v in _ds_value_strings.items():
    _i18n_mod._strings[_k] = _v

# raw technical value (exactly as the backend uses it) -> i18n key.
TECH_VALUE_KEYS = {
    "power-saver": "ds_val_power_saver",
    "balanced": "ds_val_balanced",
    "performance": "ds_val_performance",
    "enabled": "ds_val_enabled",
    "disabled": "ds_val_disabled",
    "installed": "ds_val_installed",
    "not_installed": "ds_val_not_installed",
    "unknown": "ds_val_unknown",
    "not_available": "ds_val_not_available",
}


def translated_value(raw_value: str) -> str:
    """Display text for a raw technical value — the value itself is
    never altered, only what's shown for it. Returns the raw value
    unchanged if there's no honest translation for it (never invents
    one), exactly like the rest of this app already does for kernel
    values it can't safely rename."""
    key = TECH_VALUE_KEYS.get(raw_value)
    return T(key) if key else raw_value
