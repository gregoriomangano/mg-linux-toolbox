"""
Audio output/input device listing and default selection — talks to the
already-running PipeWire/WirePlumber (via pactl, which speaks the
PulseAudio-compatible protocol PipeWire implements) or PulseAudio itself.
No new dependency: pactl ships with pipewire-pulse / pulseaudio, both of
which are prerequisites for the system to have working audio at all.
"""
import json

from core.executor import run_command

_TYPE_KEYWORDS = (
    ("hdmi", "hdmi"),
    ("displayport", "hdmi"),
    ("bluez", "bluetooth"),
    ("bluetooth", "bluetooth"),
    ("usb", "usb"),
    ("headset", "headphones"),
    ("headphone", "headphones"),
)


def _classify(entry: dict) -> str:
    text = " ".join([
        entry.get("name", ""), entry.get("description", ""),
        (entry.get("properties", {}) or {}).get("device.bus", ""),
    ]).lower()
    for keyword, category in _TYPE_KEYWORDS:
        if keyword in text:
            return category
    ports = entry.get("ports") or []
    if ports and isinstance(ports, list):
        port_type = str(ports[0].get("type", "")).lower()
        if "hdmi" in port_type:
            return "hdmi"
    return "speakers"


def _list_nodes(kind: str) -> list:
    """kind: 'sinks' (outputs) or 'sources' (inputs)."""
    ok, out, _ = run_command(["pactl", "-f", "json", "list", kind])
    if not ok or not out.strip():
        return []
    try:
        raw = json.loads(out)
    except json.JSONDecodeError:
        return []
    nodes = []
    for entry in raw:
        name = entry.get("name", "")
        if not name:
            continue
        nodes.append({
            "name": name,
            "description": entry.get("description", name),
            "category": _classify(entry),
        })
    return nodes


def list_outputs() -> list:
    return _list_nodes("sinks")


def list_inputs() -> list:
    """Excludes ".monitor" sources — these are loopback taps of an
    output device (e.g. for recording what's playing), not a real
    microphone, and would confuse a beginner picking an input."""
    return [n for n in _list_nodes("sources") if not n["name"].endswith(".monitor")]


def get_default_output() -> str:
    ok, out, _ = run_command(["pactl", "get-default-sink"])
    return out.strip() if ok else ""


def get_default_input() -> str:
    ok, out, _ = run_command(["pactl", "get-default-source"])
    return out.strip() if ok else ""


def set_default_output(name: str) -> bool:
    ok, _, _ = run_command(["pactl", "set-default-sink", name])
    return ok and get_default_output() == name


def set_default_input(name: str) -> bool:
    ok, _, _ = run_command(["pactl", "set-default-source", name])
    return ok and get_default_input() == name


# ─── Audio service restart ──────────────────────────────────────────────
_CANDIDATE_SERVICES = ("pipewire", "pipewire-pulse", "wireplumber", "pulseaudio")


def _user_service_active(name: str) -> bool:
    ok, out, _ = run_command(["systemctl", "--user", "is-active", name])
    return ok and out.strip() == "active"


def _user_service_known(name: str) -> bool:
    ok, out, _ = run_command(["systemctl", "--user", "list-unit-files", f"{name}.service"])
    return ok and f"{name}.service" in out


def detect_audio_services() -> list:
    """Only services that genuinely exist on this system — never assumes
    PipeWire over PulseAudio or vice versa."""
    return [s for s in _CANDIDATE_SERVICES if _user_service_known(s)]


def restart_audio_services() -> bool:
    """Restarts only present user-session audio services — no root
    needed, these are always per-user services."""
    services = detect_audio_services()
    if not services:
        return False
    for s in services:
        run_command(["systemctl", "--user", "restart", s])
    return all(_user_service_active(s) for s in services)
