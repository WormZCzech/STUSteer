# RojX study (2026-11-03)

What RojX is, how it probably works, and what STUSteer takes from it. Sources: rojx.io (home,
/pricing, /blog index), read 2026-11-03. Tags: [O] observed on the site, [I] inferred, [?] unknown.

## What it is

RojX turns a phone into a PC game controller. A phone app (Android, iOS) is the controller; a
Windows program, "RojX Receiver" (v1.0.31), receives the input and feeds it to games. [O]

## Features advertised

| Feature | Detail | Tag |
| --- | --- | --- |
| Xbox 360 controller emulation | Works with any game that supports Xbox pads | [O] |
| Virtual steering wheel | "Up to 900°" rotation, auto-centring | [O] |
| Gyro / tilt steering | Tilt to steer; tilt elements for gas and brake; gyro aiming | [O] |
| Layout builder | Drag and drop, "no limit" on controls, 44+ ready-made controls (pedals, paddles, gear selector, switches, sliders, D-pads, PS5-style buttons) | [O] |
| Templates | Several saved layouts, switched in-game. The GTA V example: on-foot, vehicle cockpit, flight yoke | [O] |
| Calibration | Linearity, sensitivity, deadzone for sticks, triggers, wheel | [O] |
| Latency | "Under 8 ms", FAQ "3-8 ms" over Wi-Fi | [O] claim, not verified |
| Connection | Same Wi-Fi network; one setup step also mentions a USB cable | [O] |
| Head tracking | A blog post covers head tracking | [O] |
| Price | 7-day trial, then $1.99/month or $11.99/year. No free tier on the pricing page | [O] |

LSPDFR is not mentioned anywhere; GTA V only as a layout example. [O]

## How it probably works

- **Driver:** "installs the virtual controller drivers in one click" plus Xbox 360 emulation on
  Windows almost certainly means **ViGEmBus** (the standard virtual-gamepad driver; nothing else
  does Xbox 360 emulation for free on Windows). [I] from the wording
- **Transport:** UDP on the local network, the phone sending input packets to the receiver. [O] the
  site says UDP; [I] the packet format is not published.
- **"900°":** for the touch wheel this is the lock-to-lock range (2.5 turns of your finger). For
  tilt it can only mean a scaling: nobody tilts a phone 450° either way. The game sees one stick
  axis either way, so it only sets how far you turn for full lock. [I]
- **Keyboard:** no keyboard output is advertised. [O] For LSPDFR that is the gap: its actions
  (accept a callout, traffic stop, backup) are keyboard keys by default. You could rebind them to
  pad buttons in `lspdfr/keys.ini`, but the pad has few spare buttons in a car. [I]

## What STUSteer takes, changes and skips

| RojX | STUSteer | Why |
| --- | --- | --- |
| Native phone app | A web app the receiver serves; open it in Chrome, optionally add it to the home screen | Nothing to build or install on the phone, and a layout change on the PC shows up at once. A native Android app is a later option (`docs/design.md`) |
| Xbox 360 pad via ViGEmBus | The same (vgamepad over ViGEmBus) | GTA V reads controllers only through XInput |
| Pad only | Pad **and keyboard** (scan codes) from the same screen | LSPDFR keys stay on their defaults, no `keys.ini` edits |
| UDP | WebSocket (TCP, Nagle off) on the LAN; ~1-10 ms on home Wi-Fi [EST] | The only low-latency channel a browser has. Snapshots make a lost or late message harmless |
| Drag-and-drop builder | Layouts in a JSON profile (percent coordinates) | Enough for a personal tool. An on-phone editor is the obvious next step |
| Calibration (linearity, sensitivity, deadzone) | Per-control curves (full-lock range, deadzone, anti-deadzone, gamma, invert), tuned live from the phone and saved on the PC | Same idea. Anti-deadzone matters for GTA V's own stick deadzone |
| Tilt gas/brake, head tracking, gyro aiming | Not in v1 | Not needed for GTA V + LSPDFR driving; easy to add as extra control types |
| Subscription | Free, yours | — |

Safety features RojX does not advertise (it may still have them [?]), added because a phone
controls a car in a game and types on a PC:

- a **watchdog**: everything is released if the phone goes quiet for 0.5 s;
- a **focus guard**: key presses only go to `GTA5.exe`, never to your browser;
- a **PIN**: nobody else on the Wi-Fi can drive the PC;
- a **minimum hold** of 60 ms so short taps are not missed by the game.

No RojX code, art or assets were used; it is closed source. STUSteer is a separate tool built on
the same general idea (phone as controller), designed around GTA V and LSPDFR.
