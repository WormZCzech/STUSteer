# Configuration: devices, outputs, controls

STUSteer's PC side is a small framework. **Devices** produce control values, the **profile**
says what each control does, and **outputs** emulate the hardware the game reads:

```
devices (inputs)                         profile (bindings, curves)            outputs
  phone  ── WebSocket ──┐                                                     ┌─► virtual Xbox 360 pad
  ESP32 box ─ WebSocket ┼─► snapshots ─► controller: merge, curves, hold, ───┤
  (later) USB / HID / MIDI ┘   per device    watchdog per device              └─► keyboard + mouse buttons
```

Everything is in one JSON file, the profile (`stusteer/profiles/gta5_lspdfr.json`; use your own
copy with `run --profile my.json`). The receiver checks it at start and lists every mistake with
its location, e.g. `devices[0].layouts[1].controls[3].bind.key: unknown key 'SHFT'`.

## The profile file

```json
{
  "schema": 2,
  "name": "GTA V Legacy + LSPDFR",
  "settings": { "min_hold_ms": 60, "watchdog_ms": 500 },
  "outputs": {
    "gamepad":  { "type": "xbox360" },
    "keyboard": { "target_processes": ["GTA5.exe"] }
  },
  "devices": [
    { "id": "phone", "type": "phone", "name": "Android phone", "layouts": [ ... ] },
    { "id": "buttonbox", "type": "network", "enabled": false, "controls": [ ... ] }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `settings.min_hold_ms` | every press lasts at least this long (games read input once a frame) |
| `settings.watchdog_ms` | a device silent for this long has all its controls released |
| `outputs.gamepad.type` | `xbox360` (ViGEmBus) or `none` for keyboard-only setups |
| `outputs.keyboard.target_processes` | key and mouse presses only go to these windows; `[]` = any window |
| `devices[].id` | your name for the device; it connects with `device=<id>` |
| `devices[].type` | `phone` or `network` (below); planned: `serial`, `hid`, `midi`, `streamdeck` |
| `devices[].enabled` | `false` keeps a device in the file but refuses its connections |

## Controls

A phone's controls sit in `layouts` (the screens you switch between) and are placed in percent
of the screen: `x`, `y` from the top-left, `w`, `h`. Other devices have a flat `controls` list
without positions. Control ids must be unique within a device.

| Type | Value the device sends | Binds | Extras |
| --- | --- | --- | --- |
| `button` | 1 pressed / absent released | `pad` or `key` | `label`, `hint`, `accent` |
| `pedal` | 0..1 | `axis` | `curve`, `mode`, `travel` |
| `wheel` | degrees (signed) | `axis` | `curve`, `mode` (tilt/touch), `lock`, `autocenter` |
| `stick` | `[x, y]`, -1..1, up positive | `stick` | `curve` |

**Bindings:**

- `{"pad": "RB"}`: A B X Y LB RB LS RS BACK START GUIDE DPAD_UP/DOWN/LEFT/RIGHT.
- `{"key": "F15"}`: any key. A list presses a combination, modifiers first: `["LCTRL", "F13"]`.
  - Named keys: A–Z, 0–9, **F1–F24**, numpad, arrows, PRINTSCREEN… (`python -m stusteer keys`).
  - Media keys: VOLUME_UP, MEDIA_PLAY_PAUSE…
  - Mouse buttons: MOUSE_LEFT, MOUSE_RIGHT, MOUSE_MIDDLE, MOUSE_X1, MOUSE_X2.
  - Raw codes for anything else: `"SC:0x64"` (scan code), `"SC:0xE05F"` (scan code with the E0
    prefix), `"VK:0x7E"` (Windows virtual-key code).
- `{"axis": "RT"}`: LX LY RX RY LT RT.
- `{"stick": "L"}`: L or R.

F13–F24 exist on no normal keyboard, so nothing in GTA V reacts to them. Give each LSPDFR or plugin
action one of them in its `keys.ini` and bind the same F-key here: no clashes with GTA's own keys.
[VERIFY that each plugin accepts F13–F24; RAGE Plugin Hook uses .NET's `Keys` names, which include
them (I).]

Letters, digits and punctuation follow your keyboard layout. "Y" presses the key that *types* Y,
so it also works on Czech QWERTZ, where that key sits in the US "Z" position. To press a physical
position whatever the layout, use `SC:` codes.

**Curves** (analog controls): `range` (input for full output; degrees for a wheel), `deadzone`,
`anti_deadzone`, `gamma`, `shape` (`linear` or `scurve`), `max_output`, `invert`. Defaults:

- pedals: `scurve`, deadzone 0.02;
- wheel and sticks: `linear`.

All of these can be tuned live from the phone (⚙ → Tuning). *Save on PC* writes only the changes
to `%APPDATA%\STUSteer\tuning-<profile>.json`, which keeps your profile file clean.

**Pedal modes:**

- `absolute` (default): the height of the thumb on the pedal is the amount.
- `relative`: where the thumb lands is 0; sliding it up by `travel` × the pedal height gives full.
- `digital`: any touch gives full.

## Network devices: your own button box

A `network` device is anything that can open a WebSocket on your Wi-Fi: an ESP32, a Raspberry Pi
or a Python script. It needs no driver on the PC; it speaks the same protocol as the phone:

1. Connect to `wss://<pc-ip>:8443/ws?pin=<PIN>&device=<id>` (or `ws://` when the receiver runs
   with `--http`, which is simpler for a microcontroller on a home network).
2. The receiver answers `{"t":"welcome","version":2,"device":{"id":…,"type":"network"},"profile":{"controls":[…]}}`.
3. Send a snapshot whenever a switch changes, and at least every 100 ms as a heartbeat:
   `{"t":"s","c":{"sw1":1,"pot":0.42}}`. Controls that are released are simply left out.
4. If the box loses power or Wi-Fi, the watchdog releases its controls after `watchdog_ms`.
   Nothing it held stays pressed, and the phone's controls are unaffected.

Example entry (in the default profile, switched off):

```json
{ "id": "buttonbox", "type": "network", "name": "ESP32 button box", "enabled": true,
  "controls": [
    { "id": "sw1", "type": "button", "label": "Engine", "bind": { "key": "F13" } },
    { "id": "sw4", "type": "button", "label": "Siren", "bind": { "pad": "LS" } },
    { "id": "pot", "type": "pedal", "label": "Hand throttle", "bind": { "axis": "RT" } }
  ] }
```

A phone and a box can be connected at the same time. Each output is held while any device holds
it, and for axes the value furthest from zero wins.

## Planned device types

| Type | For | Needs (your yes first) |
| --- | --- | --- |
| `serial` | Arduino / ESP32 / Pico over USB cable: no Wi-Fi, lowest latency | pyserial |
| `hid` | USB button boxes, joysticks, sim wheels, external pedals (read them, remap them to the virtual pad or keys) | hidapi or pygame |
| `midi` | MIDI controllers with knobs, faders and pads | mido + python-rtmidi |
| `streamdeck` | Elgato Stream Deck keys | streamdeck |

How one gets added: an entry in `stusteer/devices.py` plus a driver class whose `run(device,
report)` reads the hardware and calls `report({control_id: value})`. Controls, bindings,
curves, the watchdog and the outputs are already shared, so a driver is only the reading part.
