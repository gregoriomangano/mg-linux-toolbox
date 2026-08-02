"""
Sound card power saving — snd_hda_intel's power_save module parameter
(idle timeout in seconds before the codec is suspended; 0 = never).
power_save_controller (whether the HD-audio *controller* itself, not
just the codec, may also be suspended) is a separate, more advanced
knob, bundled here only inside the "custom advanced" path.
"""
import os

from core.kernel_features.base import KernelFeature, SupportStatus, OpResult


class AudioPowerSaveFeature(KernelFeature):
    id = "audio.power_save"
    category = "audio"
    technical_name = "snd_hda_intel power_save"
    risk = "low"
    supports_persistence = False

    MIN, MAX = 0, 3600  # seconds; 0 = always on

    def _path(self) -> str:
        return os.path.join(self.sys_root, "module", "snd_hda_intel", "parameters", "power_save")

    def _controller_path(self) -> str:
        return os.path.join(self.sys_root, "module", "snd_hda_intel", "parameters", "power_save_controller")

    def probe(self) -> SupportStatus:
        path = self._path()
        if not os.path.isfile(path):
            return SupportStatus.UNSUPPORTED_KERNEL
        try:
            with open(path):
                pass
        except PermissionError:
            return SupportStatus.UNAVAILABLE
        return SupportStatus.SUPPORTED_RUNTIME

    def has_controller_option(self) -> bool:
        return os.path.isfile(self._controller_path())

    def read_current(self) -> OpResult:
        try:
            with open(self._path()) as f:
                seconds = int(f.read().strip())
        except FileNotFoundError:
            return OpResult(False, friendly_message="kf_unsupported_kernel")
        except PermissionError:
            return OpResult(False, friendly_message="kf_unavailable")
        except (OSError, ValueError) as e:
            return OpResult(False, friendly_message="kf_err_generic", technical_detail=str(e))
        controller = None
        if self.has_controller_option():
            try:
                with open(self._controller_path()) as f:
                    controller = f.read().strip() in ("Y", "1", "y")
            except OSError:
                pass
        return OpResult(True, value={"seconds": seconds, "controller": controller})

    def to_friendly(self, raw_value) -> str:
        seconds = raw_value["seconds"] if isinstance(raw_value, dict) else raw_value
        if seconds == 0:
            return "audio_power_always_on"
        return str(seconds)

    def validate(self, value) -> bool:
        if not isinstance(value, dict) or "seconds" not in value:
            return False
        seconds = value["seconds"]
        return isinstance(seconds, int) and self.MIN <= seconds <= self.MAX

    def apply_temporary(self, value) -> OpResult:
        if not self.validate(value):
            return OpResult(False, friendly_message="kf_err_invalid_value")
        return self._privileged_writer.execute(self.id, "apply_temporary", value)

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
