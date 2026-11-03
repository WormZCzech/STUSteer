"""Device types: the kinds of input hardware a profile can list.

A **device** is anything that produces control values: the phone, an ESP32 button box on the
Wi-Fi, later a USB button box or a MIDI controller. Every device delivers the same thing to the
controller: a *snapshot* {control_id: value} of all its controls, whenever something changes and
as a heartbeat. The controller merges the snapshots of all connected devices, so a phone and a
button box can drive the same game at the same time, and each device has its own watchdog.

How a device's snapshots reach the receiver is its **transport**:

- "websocket": the device connects to the receiver (`/ws?pin=...&device=<id>`) and sends JSON
  snapshots, the protocol in docs/devices.md. The phone page does this; an ESP32 or any small
  program can too, with no driver on the PC.
- "local" (planned): the receiver itself reads the hardware (USB serial, HID, MIDI) with a driver
  class and turns it into snapshots. Each needs a Python package, which is your call to install.

Adding a device type = one DeviceType entry here (plus, for a local transport, a driver class with
`async def run(device, report)`), and its controls are configured like any other.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceType:
    name: str
    description: str
    transport: str          # "websocket" or "local"
    has_layouts: bool       # True: controls are placed on screens (x, y, w, h); False: a flat list
    requires: tuple[str, ...] = ()  # Python packages the driver needs (local transports)


DEVICE_TYPES: dict[str, DeviceType] = {
    "phone": DeviceType(
        "phone", "a phone or tablet running the STUSteer page in its browser", "websocket", True,
    ),
    "network": DeviceType(
        "network", "any device that speaks the snapshot protocol over WebSocket (ESP32, Raspberry Pi, "
        "a script)", "websocket", False,
    ),
}

# Known next steps, so a profile that names one gets a clear message instead of "unknown type".
PLANNED_TYPES: dict[str, str] = {
    "serial": "Arduino / ESP32 button boxes over USB serial (needs the pyserial package)",
    "hid": "USB HID button boxes, joysticks and wheels (needs hidapi or pygame)",
    "midi": "MIDI controllers with knobs and pads (needs mido + python-rtmidi)",
    "streamdeck": "Elgato Stream Deck (needs the streamdeck package)",
}
