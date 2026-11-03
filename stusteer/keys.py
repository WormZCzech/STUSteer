"""Every key a control can press: names, Windows virtual-key codes, scan codes, mouse buttons.

Three numbers describe a key on Windows:

- the **scan code**: the number the keyboard hardware sends for a physical key position ("scan
  code set 1"). Some keys share a code and are told apart by a 0xE0 prefix byte ("extended": the
  arrows, End, right Ctrl...). Games that read raw input or DirectInput see this one.
- the **virtual-key code (VK)**: Windows' meaning of the key after the keyboard *layout* is
  applied. Windows Forms apps and RAGE Plugin Hook / LSPDFR's keys.ini (Keys.Y, Keys.F13...) work
  with VKs.
- for mouse buttons, neither: they are a separate kind of input event.

The layout matters on a Czech QWERTZ keyboard: the key labelled Z sits where a US keyboard has Y.
LSPDFR's "Y" means the key that *types* Y, so for letters, digits and punctuation the Windows sink
asks the active layout which scan code produces that VK (MapVirtualKeyEx) and sends that. Other keys
(F-keys, arrows, Ctrl...) are the same on every layout and use the fixed code below.

Names you can use in a profile:

- named keys: A-Z, 0-9, F1-F24, ESC, ENTER, SPACE, LSHIFT, RCTRL, UP, NUMPAD5, PRINTSCREEN, ...
  (`python -m stusteer keys` lists them). F13-F24 exist on no ordinary keyboard, which makes them
  ideal for LSPDFR and plugin actions: nothing else in GTA uses them.
- media keys (sent as virtual keys): VOLUME_UP, MEDIA_PLAY_PAUSE, ...
- mouse buttons: MOUSE_LEFT, MOUSE_RIGHT, MOUSE_MIDDLE, MOUSE_X1, MOUSE_X2
- raw codes for anything else: "SC:0x64" (a physical scan code; "SC:0xE05F" with the E0 prefix)
  or "VK:0x7E" (a virtual-key code, see Microsoft's "Virtual-Key Codes" table).

This module is pure data, so profiles validate on any platform.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeyDef:
    """How to send one key. kind: 'key' (scan code), 'vk' (virtual key only) or 'mouse'."""

    name: str
    kind: str
    vk: int = 0
    scan: int = 0
    extended: bool = False
    layout: bool = False  # True: look up the scan code in the active keyboard layout at send time
    mouse: int = 0        # mouse button number (1 left, 2 right, 3 middle, 4 X1, 5 X2)


def _table() -> dict[str, KeyDef]:
    keys: dict[str, KeyDef] = {}

    def key(name: str, vk: int, scan: int, extended: bool = False, layout: bool = False) -> None:
        keys[name] = KeyDef(name, "key", vk, scan, extended, layout)

    rows = {0x10: "QWERTYUIOP", 0x1E: "ASDFGHJKL", 0x2C: "ZXCVBNM"}
    for start, letters in rows.items():
        for offset, letter in enumerate(letters):
            key(letter, ord(letter), start + offset, layout=True)
    for n in range(1, 10):
        key(str(n), ord(str(n)), 0x01 + n, layout=True)
    key("0", ord("0"), 0x0B, layout=True)
    # Punctuation: VK_OEM_* codes; US layout positions as the fallback scan codes.
    for name, vk, scan in (
        ("MINUS", 0xBD, 0x0C), ("EQUALS", 0xBB, 0x0D), ("LBRACKET", 0xDB, 0x1A), ("RBRACKET", 0xDD, 0x1B),
        ("SEMICOLON", 0xBA, 0x27), ("APOSTROPHE", 0xDE, 0x28), ("GRAVE", 0xC0, 0x29), ("BACKSLASH", 0xDC, 0x2B),
        ("COMMA", 0xBC, 0x33), ("PERIOD", 0xBE, 0x34), ("SLASH", 0xBF, 0x35),
        ("OEM_102", 0xE2, 0x56),  # the extra key next to left Shift on European keyboards
    ):
        key(name, vk, scan, layout=True)

    for name, vk, scan in (
        ("ESC", 0x1B, 0x01), ("BACKSPACE", 0x08, 0x0E), ("TAB", 0x09, 0x0F), ("ENTER", 0x0D, 0x1C),
        ("LCTRL", 0xA2, 0x1D), ("LSHIFT", 0xA0, 0x2A), ("RSHIFT", 0xA1, 0x36), ("LALT", 0xA4, 0x38),
        ("SPACE", 0x20, 0x39), ("CAPSLOCK", 0x14, 0x3A), ("NUMLOCK", 0x90, 0x45), ("SCROLLLOCK", 0x91, 0x46),
        ("NUMPAD_MULTIPLY", 0x6A, 0x37), ("NUMPAD_MINUS", 0x6D, 0x4A), ("NUMPAD_PLUS", 0x6B, 0x4E),
        ("NUMPAD_DECIMAL", 0x6E, 0x53),
        ("NUMPAD7", 0x67, 0x47), ("NUMPAD8", 0x68, 0x48), ("NUMPAD9", 0x69, 0x49),
        ("NUMPAD4", 0x64, 0x4B), ("NUMPAD5", 0x65, 0x4C), ("NUMPAD6", 0x66, 0x4D),
        ("NUMPAD1", 0x61, 0x4F), ("NUMPAD2", 0x62, 0x50), ("NUMPAD3", 0x63, 0x51), ("NUMPAD0", 0x60, 0x52),
    ):
        key(name, vk, scan)
    # F1-F10 are scan codes 0x3B-0x44, F11/F12 0x57/0x58, F13-F23 0x64-0x6E and F24 0x76
    # (Windows' kbd.h). VK_F1..VK_F24 are 0x70..0x87.
    for n in range(1, 25):
        scan = 0x3A + n if n <= 10 else {11: 0x57, 12: 0x58, 24: 0x76}.get(n, 0x64 + n - 13)
        key(f"F{n}", 0x6F + n, scan)
    for name, vk, scan in (
        ("NUMPAD_ENTER", 0x0D, 0x1C), ("RCTRL", 0xA3, 0x1D), ("NUMPAD_DIVIDE", 0x6F, 0x35),
        ("PRINTSCREEN", 0x2C, 0x37), ("RALT", 0xA5, 0x38), ("HOME", 0x24, 0x47), ("UP", 0x26, 0x48),
        ("PAGEUP", 0x21, 0x49), ("LEFT", 0x25, 0x4B), ("RIGHT", 0x27, 0x4D), ("END", 0x23, 0x4F),
        ("DOWN", 0x28, 0x50), ("PAGEDOWN", 0x22, 0x51), ("INSERT", 0x2D, 0x52), ("DELETE", 0x2E, 0x53),
        ("LWIN", 0x5B, 0x5B), ("RWIN", 0x5C, 0x5C), ("APPS", 0x5D, 0x5D),
    ):
        key(name, vk, scan, extended=True)

    for name, vk in (
        ("PAUSE", 0x13), ("VOLUME_MUTE", 0xAD), ("VOLUME_DOWN", 0xAE), ("VOLUME_UP", 0xAF),
        ("MEDIA_NEXT", 0xB0), ("MEDIA_PREV", 0xB1), ("MEDIA_STOP", 0xB2), ("MEDIA_PLAY_PAUSE", 0xB3),
    ):
        keys[name] = KeyDef(name, "vk", vk)

    for number, name in enumerate(("MOUSE_LEFT", "MOUSE_RIGHT", "MOUSE_MIDDLE", "MOUSE_X1", "MOUSE_X2"), 1):
        keys[name] = KeyDef(name, "mouse", mouse=number)
    return keys


KEYS: dict[str, KeyDef] = _table()

ALIASES: dict[str, str] = {
    "SHIFT": "LSHIFT", "CTRL": "LCTRL", "CONTROL": "LCTRL", "ALT": "LALT", "RETURN": "ENTER",
    "ESCAPE": "ESC", "DEL": "DELETE", "INS": "INSERT", "PGUP": "PAGEUP", "PGDN": "PAGEDOWN",
    "ARROWUP": "UP", "ARROWDOWN": "DOWN", "ARROWLEFT": "LEFT", "ARROWRIGHT": "RIGHT",
    "PRTSC": "PRINTSCREEN", "LMB": "MOUSE_LEFT", "RMB": "MOUSE_RIGHT", "MMB": "MOUSE_MIDDLE",
}

MODIFIERS = frozenset({"LSHIFT", "RSHIFT", "LCTRL", "RCTRL", "LALT", "RALT", "LWIN", "RWIN"})


def _raw(key: str) -> KeyDef | None:
    """'SC:0x64', 'SC:0xE05F', 'VK:0x7E' -> a KeyDef, None if the text is not a raw code."""
    prefix, sep, number = key.partition(":")
    if not sep or prefix not in ("SC", "VK"):
        return None
    try:
        value = int(number, 16)
    except ValueError:
        raise KeyError(key) from None
    if prefix == "VK":
        if not 0x01 <= value <= 0xFE:
            raise KeyError(key)
        return KeyDef(f"VK:0x{value:02X}", "vk", vk=value)
    extended = value >> 8 == 0xE0
    code = value & 0xFF if extended else value
    if not 0x01 <= code <= 0x7F or (value > 0xFF and not extended):
        raise KeyError(key)
    name = f"SC:0xE0{code:02X}" if extended else f"SC:0x{code:02X}"
    return KeyDef(name, "key", scan=code, extended=extended)


def normalize_key(name: str) -> str:
    """The canonical name ('shift' -> 'LSHIFT', 'sc:64' -> 'SC:0x64'); KeyError if unknown."""
    key = name.strip().upper().replace(" ", "")
    raw = _raw(key)
    if raw is not None:
        return raw.name
    key = ALIASES.get(key, key)
    if key not in KEYS:
        raise KeyError(name)
    return key


def key_def(name: str) -> KeyDef:
    """The KeyDef for a canonical name (as returned by normalize_key)."""
    return KEYS.get(name) or _raw(name) or KEYS[name]


def press_order(keys: set[str] | frozenset[str]) -> list[str]:
    """Order for pressing several keys at once: modifiers first, so Ctrl+N arrives as Ctrl, then N."""
    return sorted(keys, key=lambda k: (k not in MODIFIERS, k))


def release_order(keys: set[str] | frozenset[str]) -> list[str]:
    """Order for releasing: the plain key first, modifiers last (the reverse of press_order)."""
    return list(reversed(press_order(keys)))
