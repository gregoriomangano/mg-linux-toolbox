"""
Presentation-only ISO-8601 -> local-timezone, localized-text
conversion — used anywhere a stored timestamp (always UTC ISO-8601,
see core.kernel_features.base.utc_now_iso) needs to be shown to a
human instead of as a raw "2026-08-01T06:11:20+00:00" string. The
stored value itself is never touched, only what's displayed for it.
"""
from datetime import datetime, timezone

from core.i18n import T
from core import i18n as _i18n_mod

_MONTH_NAMES = {
    "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
           "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"],
    "en": ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio",
           "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin",
           "juillet", "août", "septembre", "octobre", "novembre", "décembre"],
}


def format_local_datetime(raw: "str | None") -> str:
    """A stored UTC ISO-8601 timestamp -> a localized, local-timezone
    string ("1 agosto 2026, 08:11"). Never raises: an empty/missing
    value returns a translated placeholder, an unparseable one is
    returned exactly as stored (still visible, never hidden, never a
    crash) rather than guessed at.

    Handles: an explicit UTC offset ("+00:00" or "Z"), any other
    explicit offset, a timezone-naive value (assumed UTC — that's
    exactly what utc_now_iso() without a timezone-aware caller would
    have meant), and a genuinely invalid/unparseable string.
    """
    if not raw:
        return T("history_timestamp_unknown")
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        local_dt = dt.astimezone()
    except (OverflowError, OSError, ValueError):
        return raw

    lang = _i18n_mod._lang
    months = _MONTH_NAMES.get(lang, _MONTH_NAMES["en"])
    month_name = months[local_dt.month - 1]
    time_text = local_dt.strftime("%H:%M")
    if lang == "en":
        return f"{month_name} {local_dt.day}, {local_dt.year}, {time_text}"
    return f"{local_dt.day} {month_name} {local_dt.year}, {time_text}"
