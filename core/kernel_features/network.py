"""
Network-related kernel tunables. Fase 1 of this category: only TCP
congestion control (which algorithm decides how much data to send
before the network is considered congested) — never a static list,
always exactly what /proc/sys/net/ipv4/tcp_available_congestion_control
really reports on this kernel.
"""
import os

from core.kernel_features.base import KernelFeature, SupportStatus, OpResult

# Only algorithms with a real, verified plain-language description —
# anything else the kernel reports is still shown (never hidden), just
# with the generic "available, no description yet" text instead of an
# invented one.
TCP_CONGESTION_DESCRIPTIONS = {
    "cubic": "kf_tcp_cc_cubic",
    "reno": "kf_tcp_cc_reno",
    "bbr": "kf_tcp_cc_bbr",
    "vegas": "kf_tcp_cc_vegas",
    "westwood": "kf_tcp_cc_westwood",
    "htcp": "kf_tcp_cc_htcp",
}

# Display spelling only — same technical value is still sent to the
# kernel and still shown alongside as "valore tecnico". Locale-
# independent (these are proper/acronym names, not translated words),
# so no i18n key needed: an algorithm not in this table is shown
# exactly as the kernel reports it, never an invented capitalization.
TCP_CONGESTION_DISPLAY_NAMES = {
    "cubic": "CUBIC",
    "reno": "Reno",
    "bbr": "BBR",
    "vegas": "Vegas",
    "westwood": "Westwood",
    "htcp": "HTCP",
}


class TcpCongestionControlFeature(KernelFeature):
    id = "network.tcp_congestion_control"
    category = "network"
    technical_name = "TCP congestion control"
    risk = "low"
    supports_persistence = True

    CURRENT_FILENAME = "tcp_congestion_control"
    AVAILABLE_FILENAME = "tcp_available_congestion_control"

    def _dir(self) -> str:
        return os.path.join(self.proc_root, "sys", "net", "ipv4")

    def _path(self, filename: str) -> str:
        return os.path.join(self._dir(), filename)

    def probe(self) -> SupportStatus:
        path = self._path(self.CURRENT_FILENAME)
        if not os.path.exists(path):
            return SupportStatus.UNSUPPORTED_KERNEL
        try:
            with open(path):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        return SupportStatus.SUPPORTED_PERSISTENT

    def read_current(self) -> OpResult:
        try:
            with open(self._path(self.CURRENT_FILENAME)) as f:
                return OpResult(True, value=f.read().strip())
        except FileNotFoundError:
            return OpResult(False, friendly_message="kf_unsupported_kernel")
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except OSError as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))

    def read_available(self):
        try:
            with open(self._path(self.AVAILABLE_FILENAME)) as f:
                return f.read().split()
        except OSError:
            return None

    def description_key(self, raw_value: str) -> str:
        """A verified plain-language description key, or the generic
        "no description yet" key — never an invented one for an
        algorithm this app hasn't actually documented."""
        return TCP_CONGESTION_DESCRIPTIONS.get(raw_value, "kf_tcp_cc_unknown_algorithm")

    def to_friendly(self, raw_value) -> str:
        # Same technical value is still sent to the kernel — this only
        # picks the display spelling (e.g. "cubic" -> "CUBIC") for a
        # name this app has verified. Never invents one for an
        # algorithm it doesn't recognize (shown exactly as reported).
        return TCP_CONGESTION_DISPLAY_NAMES.get(raw_value, raw_value or "—")

    def validate(self, value) -> bool:
        available = self.read_available()
        return bool(available) and value in available

    def apply_temporary(self, value) -> OpResult:
        if not self.validate(value):
            return OpResult(False, friendly_message="kf_err_invalid_value")
        return self._privileged_writer.execute(self.id, "apply_temporary", value)

    def apply_persistent(self, value) -> OpResult:
        if not self.validate(value):
            return OpResult(False, friendly_message="kf_err_invalid_value")
        return self._privileged_writer.execute(self.id, "apply_persistent", value)

    def restore(self, force: bool = False) -> OpResult:
        current = self.read_current()
        if not current.ok:
            return current
        rec = self.get_record()
        if rec is None:
            return OpResult(False, friendly_message="kf_err_nothing_to_restore")
        if not force and self.external_change_detected(current.value):
            return OpResult(False, friendly_message="kf_external_change_detected", value=current.value)
        return self._privileged_writer.execute(self.id, "restore", None, force=force)
