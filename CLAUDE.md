# STUSteer: phone controller for GTA V Legacy

STUSteer turns my Android phone into a steering wheel, gamepad and LSPDFR button box for GTA V
Legacy (single-player, LSPDFR). It's my own take on RojX (`docs/rojx-study.md`). A Python receiver on
Windows serves a web controller to the phone over Wi-Fi. It drives a virtual Xbox 360 pad
(ViGEmBus via vgamepad) and sends LSPDFR keys (SendInput scan codes).

**Status:** v0.1.0 (2026-11-03): receiver, phone app, default profile, 35 unit tests and a
16-check end-to-end test, all in dry-run. Not yet run against the real game, ViGEmBus or a real
phone. Decisions S-001..S-007 in `docs/design.md` are proposals waiting for my yes.

## Working with me

- Explain what the code does and why (protocols, sensor maths, Windows input APIs). My background is
  Python, MATLAB and embedded systems.
- Downloads and installs (packages, drivers, SDKs, tools) need my yes for each one: name, source,
  version, licence.
- `--dry-run` first, always.
- Tag facts [O] observed (say where), [I] inferred, [?] unknown; numbers [EST] or [VERIFY].
- Nothing is committed or pushed without my OK.

## Hard rules

- STUSteer never reads or writes game files. It talks to the game only as a pad and a keyboard.
- Nothing may stay pressed: snapshots (absent = released), the watchdog, release on disconnect and
  on exit. Any new output path keeps these properties and gets a test.
- Keys go only to the window list in the profile (`key_target_processes`, default `GTA5.exe`).
- The phone sends only control ids; bindings live in the profile on the PC.
- Keys, certificates and the PIN stay in `%APPDATA%\STUSteer`, never in git.

## Where things are

| What | Where |
| --- | --- |
| Setup and use | `README.md` |
| Architecture, decisions, protocol, roadmap | `docs/design.md`, `stusteer/server.py` docstring |
| RojX study | `docs/rojx-study.md` |
| GTA V / LSPDFR bindings | `docs/gta-bindings.md`, `stusteer/profiles/gta5_lspdfr.json` |
| Tests | `tests/` (`python -m unittest`), `tests/e2e/phone_e2e.js` |
