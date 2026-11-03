"""Command line: `python -m stusteer run`, `python -m stusteer check`.

    run      start the receiver (add --dry-run first: it only logs, nothing reaches the game)
    check    report what is installed, the profile's devices and what the phone should open
    keys     list every key name a button can press
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import ssl
import sys
from pathlib import Path

from . import __version__
from .config import TuningStore, app_dir, load_pin
from .controller import Controller
from .profile import ProfileError, load_profile
from .server import Receiver, lan_addresses

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PROFILE = PACKAGE_DIR / "profiles" / "gta5_lspdfr.json"
log = logging.getLogger("stusteer")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stusteer", description="Phone steering wheel and button box for GTA V.")
    parser.add_argument("--version", action="version", version=f"STUSteer {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="start the receiver")
    run.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="profile JSON (layouts and bindings)")
    run.add_argument("--port", type=int, default=8443)
    run.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all)")
    run.add_argument("--http", action="store_true", help="plain HTTP, no certificate (tilt needs a Chrome flag)")
    run.add_argument("--dry-run", action="store_true", help="log the output instead of sending it to the game")
    run.add_argument("--any-window", action="store_true", help="send keys whatever window has focus")
    run.add_argument("--new-pin", action="store_true", help="make a new pairing PIN")
    run.add_argument("--pin", help=argparse.SUPPRESS)  # fixed PIN for automated tests
    run.add_argument("-v", "--verbose", action="store_true")

    check = sub.add_parser("check", help="check the setup")
    check.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)

    sub.add_parser("keys", help="list every key name a button can press")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    if args.command == "check":
        return _check(args)
    if args.command == "keys":
        return _keys()
    return _run(args)


def _keys() -> int:
    from .keys import ALIASES, KEYS

    groups: dict[str, list[str]] = {"keys": [], "media keys (virtual-key only)": [], "mouse buttons": []}
    for name, spec in KEYS.items():
        group = {"key": "keys", "vk": "media keys (virtual-key only)", "mouse": "mouse buttons"}[spec.kind]
        groups[group].append(name)
    for title, names in groups.items():
        print(f"{title}:")
        line = "   "
        for name in names:
            if len(line) + len(name) > 96:
                print(line)
                line = "   "
            line += " " + name
        print(line)
    print("aliases: " + ", ".join(f"{a}={k}" for a, k in ALIASES.items()))
    print("raw codes: SC:0x64 (scan code), SC:0xE05F (with the E0 prefix), VK:0x7E (virtual-key code)")
    print("combinations: a list, e.g. \"bind\": {\"key\": [\"LCTRL\", \"F13\"]}")
    return 0


def _make_sinks(dry_run: bool, outputs):
    from .output.dryrun import DryRunGamepad, DryRunKeyboard, NullGamepad

    if dry_run:
        return (DryRunGamepad() if outputs.gamepad != "none" else NullGamepad()), DryRunKeyboard()
    if sys.platform != "win32":
        raise SystemExit("Real output needs Windows. Use --dry-run here.")
    if outputs.gamepad == "none":
        gamepad = NullGamepad()
    else:
        gamepad = _vigem()
    from .output.keyboard_win import WindowsKeyboard

    return gamepad, WindowsKeyboard(outputs.key_targets)


def _vigem():
    try:
        from .output.gamepad_vigem import VigemGamepad

        return VigemGamepad()
    except ImportError:
        raise SystemExit("The 'vgamepad' package is missing: pip install vgamepad (see README).") from None
    except Exception as exc:  # vgamepad raises a plain Exception when ViGEmBus is not installed
        raise SystemExit(f"Could not create the virtual Xbox pad ({exc}). Is the ViGEmBus driver installed?") from exc


def _ssl_context(folder: Path, addresses: list[str]) -> ssl.SSLContext:
    try:
        from .certs import ensure_certificates
    except ImportError:
        raise SystemExit("HTTPS needs the 'cryptography' package (pip install cryptography), or use --http.") from None
    try:
        cert, key = ensure_certificates(folder, addresses)
    except ImportError:
        raise SystemExit("HTTPS needs the 'cryptography' package (pip install cryptography), or use --http.") from None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


def _run(args) -> int:
    try:
        profile = load_profile(args.profile)
    except (OSError, ProfileError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.any_window:
        profile.outputs.key_targets = ()
    folder = app_dir()
    pin = args.pin or load_pin(folder, regenerate=args.new_pin)
    addresses = lan_addresses()
    context = None if args.http else _ssl_context(folder, addresses)
    gamepad, keyboard = _make_sinks(args.dry_run, profile.outputs)
    controller = Controller(profile, gamepad, keyboard)
    receiver = Receiver(
        controller, pin, TuningStore(folder, args.profile.stem), PACKAGE_DIR / "web",
        ca_cert=None if args.http else folder / "ca.crt",
    )

    scheme = "http" if args.http else "https"
    targets = ", ".join(profile.outputs.key_targets) or "any window"
    pad = "virtual Xbox 360 pad" if profile.outputs.gamepad == "xbox360" else "no gamepad"
    print()
    print(f"  STUSteer {__version__}   profile: {profile.name}")
    if args.dry_run:
        print("  DRY RUN: nothing is sent to the game, the output is only logged.")
    else:
        print(f"  Output: {pad} + keyboard/mouse (presses only while {targets} has focus)")
    for device in profile.devices.values():
        state = "enabled" if device.enabled else "disabled"
        print(f"  Device {device.id} ({device.type}, {state}): {device.name}")
    print("  On the phone (same Wi-Fi), open:")
    for address in addresses or ["<this PC's IP address>"]:
        print(f"      {scheme}://{address}:{args.port}")
    print(f"  PIN: {pin}")
    if not args.http:
        print("  First visit: Chrome warns about the certificate -> Advanced -> Proceed (or install ca.crt, README).")
    print("  Ctrl+C stops the receiver and releases everything.")
    print()

    async def serve() -> None:
        server = await receiver.serve(args.host, args.port, context)
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(f"Could not listen on port {args.port}: {exc}", file=sys.stderr)
        return 1
    finally:
        controller.release_all_now()
        gamepad.close()
        keyboard.close()
        log.info("stopped; everything released")
    return 0


def _check(args) -> int:
    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok = ok and good
        print(f"  [{'ok' if good else '!!'}] {text}")

    print(f"STUSteer {__version__} on {platform.system()} {platform.release()}, Python {platform.python_version()}")
    try:
        profile = load_profile(args.profile)
        line(True, f"profile {args.profile.name}: outputs {profile.outputs.gamepad} pad + keyboard")
        for device in profile.devices.values():
            state = "enabled" if device.enabled else "disabled"
            print(f"       device {device.id}: {device.type}, {len(device.controls)} controls, {state}")
    except (OSError, ProfileError) as exc:
        line(False, f"profile: {exc}")
    try:
        import cryptography  # noqa: F401

        line(True, "cryptography installed (HTTPS, tilt steering)")
    except ImportError:
        line(False, "cryptography missing: pip install cryptography (or run with --http)")
    if sys.platform == "win32":
        try:
            from .output.gamepad_vigem import VigemGamepad

            VigemGamepad().close()
            line(True, "virtual Xbox 360 pad works (vgamepad + ViGEmBus)")
        except ImportError:
            line(False, "vgamepad missing: pip install vgamepad")
        except Exception as exc:
            line(False, f"virtual pad failed ({exc}): install the ViGEmBus driver")
        import ctypes

        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        print(f"  [..] running as administrator: {admin} (must match GTA V / RAGE Plugin Hook, see README)")
    else:
        print("  [..] not Windows: only --dry-run works here")
    addresses = lan_addresses()
    line(bool(addresses), f"LAN addresses: {', '.join(addresses) or 'none found'}")
    print(f"  [..] settings folder: {app_dir()}")
    print(f"  [..] STUSTEER_HOME={os.environ.get('STUSTEER_HOME', '(not set)')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
