# STUSteer design

## The picture

```
 Android phone (Chrome)                              Windows PC (python -m stusteer run)
 ┌──────────────────────────┐   Wi-Fi, HTTPS +     ┌───────────────────────────────────────────┐
 │ web app (stusteer/web)   │   WebSocket, PIN     │ server.py   HTTP(S) + WebSocket, PIN,      │
 │  layouts from profile    │ ───────────────────► │             10 ms ticker                   │
 │  touch: buttons, pedals, │  snapshots           │ controller  snapshot → wanted outputs,     │
 │  sticks, touch wheel     │  {"wheel":-12.5,     │             min hold, watchdog, diff       │
 │  sensors: tilt wheel     │   "gas":0.8, ...}    │ mapping     curves (deadzone, gamma, ...)  │
 │  (gravity + gyro fusion) │ ◄─────────────────── │ output/     ViGEmBus Xbox 360 pad ─────────┼─► GTA V (XInput)
 │  settings, live tuning   │  welcome, pong, ...  │             SendInput scan codes ──────────┼─► GTA V / LSPDFR
 └──────────────────────────┘                      │             (only while GTA5.exe has focus)│   (keyboard)
                                                   └───────────────────────────────────────────┘
```

STUSteer never reads or writes game files. It talks to the game the way a USB pad and a keyboard
do.

## The framework (profile schema 2)

- **Devices** (`devices.py`, `profile.py`) produce snapshots of their controls. Types: `phone`
  (layouts on screens) and `network` (a flat list of controls, any WebSocket client, e.g. an
  ESP32). Planned: `serial`, `hid`, `midi`, `streamdeck`, each a small driver class.
- **The controller** keeps one snapshot per device, merges them (held if any device holds it;
  furthest-from-zero axis wins) and runs a watchdog per device.
- **Outputs** (`outputs` in the profile): the virtual Xbox 360 pad (or none), and keyboard + mouse
  buttons with the focus guard. A DualShock 4 pad (vgamepad has it) or vJoy would be further
  output types.
- **Keys** (`keys.py`) cover every key Windows knows. Each key has its VK and scan code; for
  layout-dependent keys the scan code is looked up in the active layout at send time
  (MapVirtualKeyEx), so "Y" means the key that types Y.

## Your decisions (2026-11-03)

| # | Decision |
| --- | --- |
| U-001 | Pedals keep **thumb height** (`absolute`) as the default; **slide from touch** (`relative`) is an option per pedal |
| U-002 | Response curves are **selectable**; **S-curve** is the default for pedals (wheel and sticks stay linear) |
| U-003 | **Every button can send any key**, including keys no keyboard has (F13–F24), plus media keys, mouse buttons and raw codes |
| U-004 | The PC side is a **config framework** for several devices: the phone now, a network button box now, more custom and external boxes later (`docs/devices.md`) |

## Decisions (proposed 2026-11-03, yours to confirm)

| # | Decision | Alternatives | Why |
| --- | --- | --- | --- |
| S-001 | **Receiver in Python** (3.11+, standard library + 2 packages) | C#/.NET like STUIV | You know Python best; the receiver is small (I/O and a little maths); vgamepad makes ViGEm a 10-line job; it runs and is tested on Linux in dry-run. Switching to C# later is a port of ~600 lines |
| S-002 | **The phone side is a web app served by the receiver**, not a native APK | Kotlin Android app (like RojX) | No Android SDK, no APK sideloading, no Play Store; one place for layouts. Costs: tilt needs HTTPS (handled, S-006) and browsers have no UDP (S-005). A native app stays possible: it would speak the same protocol |
| S-003 | **Xbox 360 pad through ViGEmBus + vgamepad** | vJoy (DirectInput), keyboard-only steering | GTA V reads controllers only through XInput; analog steering needs a stick axis. ViGEmBus is retired (no new versions since 2023) but still the standard and works on Windows 10/11 [VERIFY on your PC] |
| S-004 | **Keyboard through SendInput with scan codes**, behind a focus guard | Rebinding LSPDFR to pad buttons | LSPDFR's defaults are keys; scan codes are what raw-input/DirectInput games read. The guard keeps keys out of other windows |
| S-005 | **State snapshots over a WebSocket** (JSON, ~100 bytes, on change + 10 Hz heartbeat) | Press/release events over UDP (RojX) | A lost or delayed message cannot leave a key stuck; layout switches release old controls by themselves; trivially testable. TCP's retransmits can add a few ms of jitter on bad Wi-Fi [EST]; measured round trip is shown in the top bar |
| S-006 | **HTTPS with a local CA** made by the receiver; 6-digit PIN | Plain HTTP + a Chrome flag; `adb reverse` over USB | Chrome only exposes motion sensors to secure pages. Installing `ca.crt` on the phone once removes every warning; tapping through the warning works too. `--http` remains for touch-only use |
| S-007 | **Curves on the PC**, the phone sends raw values (degrees, 0..1) | Curves on the phone | One place to tune, test and save; the phone just measures |

## Timing and safety rules (controller.py)

- **Minimum hold 60 ms**: games sample input once a frame; shorter taps are stretched.
- **Watchdog 500 ms**: no snapshot for that long → everything released. Covers Wi-Fi loss, the
  screen turning off, Chrome being killed. The phone also sends an empty snapshot when it is hidden.
- **Disconnect / takeover**: a closed connection releases everything; a second phone takes over the
  first.
- **Ctrl+C** releases everything at once and unplugs the virtual pad.
- **Shared outputs**: an output is held while any control bound to it is held; for axes the value
  furthest from zero wins.

## Tilt steering (web/app.js)

1. `accelerationIncludingGravity` is the "up" vector in the phone's axes. It is rotated into the
   current screen orientation (`screen.orientation.angle`).
2. The wheel angle is `atan2(-x, y)` of that vector in the screen plane: 0° held upright, positive
   turning clockwise. It does not care how far the phone leans back (unless it lies almost flat).
3. A complementary filter: the gyro's turn rate around the screen axis (`rotationRate.alpha`) moves
   the angle at once, and the accelerometer pulls it back with a 0.3 s time constant, removing
   drift. Without a gyro, the accelerometer angle is smoothed (adjustable).
4. The gyro's sign and units (deg/s vs rad/s) vary across devices, so the page learns the factor
   by least squares while you turn the phone, then snaps it to ±1 or ±57.3.
5. "Centre" stores the current angle as straight ahead.

[VERIFY on your phone] the sign: turning the phone clockwise must steer right. If not, tick Invert
in the tuning panel (and tell me, the default should then change).

## Latency budget [EST]

| Step | Time |
| --- | --- |
| Touch / sensor event → JS (Chrome on Android) | 4-16 ms (sensor rate 60-200 Hz; touch sampling) |
| JS → Wi-Fi → PC | 1-5 ms on a quiet 5 GHz network |
| Python: parse, map, ViGEm report / SendInput | < 1 ms |
| Game reads input | next frame (≤ 16 ms at 60 fps) |

Analog changes are coalesced to one snapshot per animation frame; presses and releases go out
immediately.

## Testing

- `python -m unittest` (43 tests) covers:
  - keys (including F13–F24 and raw codes), curves (S-curve, cap) and profile validation;
  - the controller's timing rules and merging of several devices with separate watchdogs;
  - the focus guard and WebSocket framing;
  - the receiver over real sockets with HTTP and HTTPS: phone and button box together, live
    tuning of curves and pedal modes.
- `node tests/e2e/phone_e2e.js [shots]` (18 checks): a headless Chromium posing as a 915×412
  Android phone drives a dry-run receiver. It checks tilt, both pedal modes with the S-curve,
  multitouch, keys, the sticks, the touch wheel, live tuning and the watchdog, and saves screenshots.
- Not testable here: ViGEmBus, SendInput, GTA itself, and the motion sensors of a real phone. Those
  are the first manual checks (README, "First run").

## Next steps (not built yet)

1. Your first real run (README checklist) and fixes from it; the touch probe on your S24.
2. Layout editor on the phone (drag, resize, bind to any key), saving to the profile on the PC.
3. The first local device driver (USB serial for an Arduino/ESP32 box, needs pyserial).
4. Tilt pedals (pitch → gas/brake) and paddle/gear controls for other games.
5. Optional native Android app with UDP for the last few ms, using the same profile and snapshots.
6. A QR code in the receiver window for pairing.
