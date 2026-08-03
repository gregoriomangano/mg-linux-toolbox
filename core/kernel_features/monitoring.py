"""
PSI — Pressure Stall Information. Read-only: shows how much time
programs spend waiting because CPU, memory or disk can't keep up.
"""
import os
import re

from core.kernel_features.base import KernelFeature, SupportStatus, OpResult

_LINE_RE = re.compile(
    r"(?P<kind>some|full)\s+avg10=(?P<avg10>[\d.]+)\s+avg60=(?P<avg60>[\d.]+)\s+avg300=(?P<avg300>[\d.]+)"
)

# Thresholds for the "Bassa/Moderata/Alta" bucketing, kept as named
# constants (not scattered magic numbers) so they're easy to revisit —
# the spec explicitly warns against rigid, unreviewable thresholds.
# Reused as-is for both avg10 and avg60 (2026-08-03 PSI fix) — avg300
# never feeds a threshold: a five-minute average keeping a box red long
# after a spike ended was exactly the bug that fix removes.
THRESHOLD_MODERATE = 1.0
THRESHOLD_HIGH = 10.0

# Shared polling cadence for any UI that periodically re-reads
# /proc/pressure/* (Panoramica badge/card, Kernel Functions PSI row) —
# a single source of truth so the two never drift apart.
PSI_REFRESH_SECONDS = 2


def _bucket(avg10: float) -> str:
    if avg10 >= THRESHOLD_HIGH:
        return "high"
    if avg10 >= THRESHOLD_MODERATE:
        return "moderate"
    return "low"


class PSIHysteresis:
    """Stateful per-resource PSI classifier for the visible UI state.

    ``avg10`` is the primary, current signal. ``avg60`` only confirms
    whether a high reading is sustained and whether a return to the low
    state is established. ``avg300`` is deliberately not accepted by
    this API: it remains historical/technical data and can never keep a
    badge red.

    Every visible transition needs two consecutive samples with the same
    candidate state. A single spike or dip therefore cannot change the
    UI. When avg10 has recovered but avg60 is still elevated, the state
    may step down from high to moderate, but it cannot claim to be low.
    """

    REQUIRED_SAMPLES = 2

    def __init__(self):
        self._state = "low"
        self._pending_state = None
        self._pending_count = 0
        self.critical = False

    def reset_pending(self):
        """Forget an incomplete transition after a read gap/page pause."""
        self._pending_state = None
        self._pending_count = 0

    def _candidate(self, avg10: float, avg60: float) -> str:
        primary = _bucket(avg10)

        # avg60 confirms a high/critical candidate at the same high
        # threshold. A sharp avg10 spike with a lower one-minute trend
        # is acknowledged as moderate, not red.
        if primary == "high" and avg60 < THRESHOLD_HIGH:
            return "moderate"

        # A low avg10 while the one-minute trend is still elevated is a
        # recovery in progress. Leave red after two coherent samples, but
        # stay amber until avg60 also confirms the low state.
        if primary == "low" and avg60 >= THRESHOLD_MODERATE:
            return "moderate"

        return primary

    def update(self, avg10: float, avg60: float) -> str:
        candidate = self._candidate(avg10, avg60)

        if candidate == self._state:
            self.reset_pending()
            return self._state

        if candidate == self._pending_state:
            self._pending_count += 1
        else:
            self._pending_state = candidate
            self._pending_count = 1

        if self._pending_count >= self.REQUIRED_SAMPLES:
            self._state = candidate
            self.critical = self._state == "high"
            self.reset_pending()

        return self._state


def parse_psi_file(content: str) -> dict:
    """Returns {"some": {"avg10":..,"avg60":..,"avg300":..}, "full": {...}}"""
    result = {}
    for line in content.splitlines():
        m = _LINE_RE.match(line.strip())
        if m:
            result[m.group("kind")] = {
                "avg10": float(m.group("avg10")),
                "avg60": float(m.group("avg60")),
                "avg300": float(m.group("avg300")),
            }
    return result


class PSIFeature(KernelFeature):
    id = "monitoring.psi"
    category = "monitoring"
    technical_name = "PSI - Pressure Stall Information"
    risk = "low"
    read_only = True
    supports_persistence = False

    RESOURCES = ("cpu", "memory", "io")

    def _path(self, resource: str) -> str:
        return os.path.join(self.proc_root, "pressure", resource)

    def probe(self) -> SupportStatus:
        path = self._path("cpu")
        if not os.path.exists(path):
            return SupportStatus.UNSUPPORTED_KERNEL
        try:
            with open(path):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        except OSError:
            return SupportStatus.UNKNOWN
        return SupportStatus.SUPPORTED_READ_ONLY

    def read_current(self) -> OpResult:
        out = {}
        for resource in self.RESOURCES:
            path = self._path(resource)
            try:
                with open(path) as f:
                    content = f.read()
            except FileNotFoundError:
                return OpResult(False, friendly_message="kf_unsupported_kernel")
            except PermissionError:
                return OpResult(False, friendly_message="kf_unavailable")
            except OSError as e:
                return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
            parsed = parse_psi_file(content)
            if "some" not in parsed:
                return OpResult(
                    False,
                    friendly_message="kf_err_generic",
                    technical_detail=f"PSI data missing 'some' line for {resource}",
                )
            out[resource] = parsed
        return OpResult(True, value=out)

    def to_friendly(self, raw_value) -> str:
        """
        raw_value: the per-resource dict from read_current() for ONE
        resource. Returns the bare bucket ("low"/"moderate"/"high") — the
        UI combines it with the resource name to pick the right i18n key,
        since Italian needs gender agreement per resource
        (CPU/Memoria = feminine, Disco = masculine).
        """
        avg10 = raw_value.get("some", {}).get("avg10", 0.0)
        return _bucket(avg10)
