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
THRESHOLD_MODERATE = 1.0
THRESHOLD_HIGH = 10.0


def _bucket(avg10: float) -> str:
    if avg10 >= THRESHOLD_HIGH:
        return "high"
    if avg10 >= THRESHOLD_MODERATE:
        return "moderate"
    return "low"


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
            out[resource] = parse_psi_file(content)
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
