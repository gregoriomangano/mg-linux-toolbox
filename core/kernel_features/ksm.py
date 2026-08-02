"""
KSM (Kernel Samepage Merging) — /sys/kernel/mm/ksm/run. The kernel only
really offers a system-wide on/off switch (0 = off, 1 = on; 2 also stops
KSM while unmerging already-shared pages, used for hot-unplug scenarios,
not exposed here). There is no kernel-native "only while a VM is
running" mode — that requires an external daemon (e.g. ksmtuned) polling
libvirt, which this app does not install or manage. Exposing only the
two states the kernel actually offers keeps the friendly description
honest rather than promising automatic VM-aware behaviour that isn't
really there.
"""
import os

from core.kernel_features.base import KernelFeature, SupportStatus, OpResult


class KsmFeature(KernelFeature):
    id = "virt.ksm"
    category = "virt"
    technical_name = "Kernel Samepage Merging (KSM)"
    risk = "low"
    supports_persistence = True

    def _path(self) -> str:
        return os.path.join(self.sys_root, "kernel", "mm", "ksm", "run")

    def probe(self) -> SupportStatus:
        path = self._path()
        if not os.path.isfile(path):
            return SupportStatus.UNSUPPORTED_KERNEL
        try:
            with open(path):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        return SupportStatus.SUPPORTED_PERSISTENT

    def read_current(self) -> OpResult:
        try:
            with open(self._path()) as f:
                raw = f.read().strip()
        except FileNotFoundError:
            return OpResult(False, friendly_message="kf_unsupported_kernel")
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except OSError as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
        return OpResult(True, value=raw in ("1", "2"))

    def to_friendly(self, raw_value) -> str:
        return "ksm_on" if raw_value else "ksm_off"

    def validate(self, value) -> bool:
        return isinstance(value, bool)

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
