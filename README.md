# STUSteer

Your Android phone as a **steering wheel, gamepad and LSPDFR button box** for GTA V Legacy. It's my
own take on [RojX](https://rojx.io/) (study: `docs/rojx-study.md`), built around GTA V + LSPDFR.

- **Tilt steering** (gravity + gyro fusion) or a **touch wheel**, analog **gas and brake** pedals,
  handbrake, horn/siren, lights, camera.
- **LSPDFR keys on the same screen**: accept/end callout, traffic stop, backup, interact, menu
  navigation. These are sent as real keyboard keys, so LSPDFR keeps its default bindings.
- **On-foot layout** with two sticks, ABXY, bumpers/triggers and a D-pad.
- **Live tuning from the phone**: full-lock angle, deadzone, anti-deadzone, curve, invert. Saved on
  the PC.
- **Safety**: everything is released if the phone goes quiet for 0.5 s, keys go only to `GTA5.exe`,
  and a PIN keeps others on your Wi-Fi out.

How it works: the PC runs a small Python receiver. It serves the controller page to your phone over
Wi-Fi, plugs in a virtual Xbox 360 controller (ViGEmBus), and types keys (Windows SendInput). The
phone needs only Chrome: nothing to install from a store. Details: `docs/design.md`.

```
phone (Chrome) ── Wi-Fi, HTTPS/WebSocket ──► PC: python -m stusteer run ──► virtual Xbox pad + keyboard ──► GTA V
```

## What you install (each one is your call)

| What | Where from | Licence | Needed for |
| --- | --- | --- | --- |
| Python 3.11+ (you have 3.14) | python.org | PSF | the receiver |
| `vgamepad` | PyPI: `pip install vgamepad` | MIT | the virtual Xbox pad |
| ViGEmBus driver 1.22.0 | github.com/nefarius/ViGEmBus/releases (the vgamepad install may offer to run it) [VERIFY] | BSD-3-Clause | the virtual Xbox pad |
| `cryptography` | PyPI: `pip install cryptography` | Apache-2.0 / BSD | HTTPS, which tilt steering needs |

Optional, for developers only: Node + `playwright` to run the end-to-end test.

ViGEmBus is a kernel driver and has been retired upstream since 2023 (no new releases). It still
works on Windows 10/11 [VERIFY on your PC] and is what most phone-controller apps use, probably RojX
too.

## Setup (once)

1. Get the code onto the PC:
   ```bash
   git clone https://github.com/WormZCzech/STUSteer.git
   ```
2. Install the packages in that folder (a virtual environment is optional):
   ```bash
   python -m pip install vgamepad cryptography
   ```
   If no ViGEmBus setup window appears, install ViGEmBus from its releases page, then reboot if it
   asks.
3. Check the setup:
   ```bash
   python -m stusteer check
   ```
   Every line should say `[ok]`. "Running as administrator" must match how you start GTA V / RAGE
   Plugin Hook. If the game runs as admin, the receiver must too, otherwise Windows silently drops
   its keys (UIPI).

## First run (try this order)

1. **Dry run**, nothing reaches the game:
   ```bash
   python -m stusteer run --dry-run
   ```
   The window shows the address and a PIN, for example `https://192.168.1.23:8443` and `PIN: 482913`.
   Windows Firewall asks about Python: allow **private networks** only.
2. **On the phone** (same Wi-Fi): open that address in Chrome. It warns "Your connection is not
   private": tap **Advanced → Proceed**. Type the PIN. Turn the phone sideways.
   *No warnings ever again:* open `https://<pc-ip>:8443/ca.crt` on the phone and install it under
   Settings → Security → Encryption & credentials → Install a certificate → **CA certificate**.
   This CA can only vouch for private IP addresses (it is name-constrained), and its key never
   leaves your PC (`%APPDATA%\STUSteer`).
3. Press buttons and tilt the phone. The PC window logs every output, e.g. `PAD buttons=[RB]
   LX=+0.493 RT=+0.745` and `KEY B down`. Check that **tilting clockwise gives a positive LX**
   (steering right). If it doesn't, open ⚙ → Tuning → Invert.
4. **For real**: stop with Ctrl+C, then
   ```bash
   python -m stusteer run
   ```
   Windows now has an extra Xbox 360 controller (check with `joy.cpl`). Start GTA V, then try
   driving. Keys reach the game only while GTA V has focus. To send them to any window, add
   `--any-window`.
5. Optional: Chrome menu → **Add to Home screen** for an app icon, and ⛶ for full screen.

## Using it

- **Top bar**: connection dot and round-trip time, layout tabs, *Centre* (sets the current tilt as
  straight ahead), full screen, ⚙ settings.
- **Drive (tilt)**: hold the phone like a wheel. Brake on the left edge, gas on the right; the
  higher your thumb on the pedal, the harder you press. The LSPDFR row sits under the wheel.
- **Drive (touch)**: drag the wheel round instead of tilting (360° lock to lock, springs back).
- **On foot**: floating sticks (the stick centres where your thumb lands), ABXY, triggers.
- **LSPDFR**: big buttons for callouts, traffic stop (hold), backup, interact, plus arrows, OK
  and Back for plugin menus.
- **⚙ Settings**: gyro fusion, tilt smoothing, vibration, and live tuning of every analog control.
  Changes apply on the PC at once; *Save on PC* keeps them (`%APPDATA%\STUSteer\tuning-*.json`).

Steering tips for GTA V: lower *Full lock* (e.g. 40°) for quicker steering, raise it for precision.
GTA ignores small stick movements itself; if the first few degrees of tilt do nothing, raise
*Anti-deadzone* to 0.1–0.25. A *Curve* above 1 gives finer control around the centre.

## Changing buttons and layouts

Everything is in `stusteer/profiles/gta5_lspdfr.json`. Each control has a place on the screen in
percent (`x`, `y`, `w`, `h`) and a binding:

```json
{"id": "stp", "type": "button", "label": "STP menu", "hint": "Ctrl+N",
 "x": 66, "y": 2, "w": 10, "h": 30, "bind": {"key": ["LCTRL", "N"]}}
```

Bindings: `{"pad": "RB"}` (buttons A B X Y LB RB LS RS BACK START GUIDE DPAD_UP/DOWN/LEFT/RIGHT),
`{"key": "B"}` or a list for a combination (names in `stusteer/keys.py`), `{"axis": "RT"}` for
pedals and wheels (LX LY RX RY LT RT), `{"stick": "L"}`. Control ids must be unique across the
whole profile. Restart the receiver after editing; it reports every mistake with its location. To
keep your own copy elsewhere, use `run --profile my.json`. Check the LSPDFR keys against your
`lspdfr/keys.ini` (`docs/gta-bindings.md`).

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Page doesn't load on the phone | Same Wi-Fi? Firewall allowed Python on private networks? Is the address the one the receiver printed? Some routers isolate clients ("AP isolation") |
| "Tilt needs HTTPS" | You ran `--http`. Run without it, or on the phone enable `chrome://flags/#unsafely-treat-insecure-origin-as-secure` for `http://<pc-ip>:8443` |
| Steering is reversed | ⚙ → Tuning → Invert, then Save on PC |
| Wheel drifts slowly | Turn off gyro fusion, or press Centre |
| Keys do nothing in GTA | Is GTA V focused? Does the receiver's admin status match the game's (`check`)? The receiver logs "key press held back" when the focus guard blocks a key |
| GTA ignores the pad | Unplug other controllers (GTA uses the first one); check `joy.cpl` shows the virtual Xbox 360 controller moving |
| Laggy | Use 5 GHz Wi-Fi, keep the phone near the router. The top bar shows the round trip; under 15 ms is fine |

## Development

```bash
python -m unittest -v
```

```bash
node tests/e2e/phone_e2e.js shots
```

The unit tests need only Python. The end-to-end test drives the real page in a headless Chromium
posing as a phone, against a dry-run receiver, and writes screenshots to `shots/`. Code map:
`stusteer/server.py` (network), `controller.py` (timing and safety rules), `mapping.py` (curves),
`profile.py` (layouts and bindings), `output/` (ViGEm pad, SendInput keyboard, dry run), `web/`
(the phone app).
