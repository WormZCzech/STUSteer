"""Keyboard key names and their hardware scan codes.

Why scan codes and not virtual-key codes: GTA V and RAGE Plugin Hook read the keyboard through raw
input / DirectInput, which look at the scan code (the number the keyboard itself sends), not at the
Windows virtual-key code. Windows' SendInput can inject either; injecting the scan code (flag
KEYEVENTF_SCANCODE) is what games reliably see. Windows still derives the virtual-key code from it,
so ordinary apps see the key too.

The codes are "scan code set 1", the set Windows uses. Some keys share a code with a numpad key and
are told apart by the 0xE0 prefix byte ("extended" keys: the arrows, End, Home, right Ctrl...).
SendInput marks those with KEYEVENTF_EXTENDEDKEY.

This module is pure data (no Windows calls), so the profile can be validated on any platform.
"""

from __future__ import annotations

from typing import NamedTuple


class ScanCode(NamedTuple):
    """One key: its set-1 scan code and whether it needs the 0xE0 'extended' prefix."""

    code: int
    extended: bool = False


def _letters() -> dict[str, ScanCode]:
    rows = {
        0x10: "QWERTYUIOP",
        0x1E: "ASDFGHJKL",
        0x2C: "ZXCVBNM",
    }
    out: dict[str, ScanCode] = {}
    for start, letters in rows.items():
        for offset, letter in enumerate(letters):
            out[letter] = ScanCode(start + offset)
    return out


SCANCODES: dict[str, ScanCode] = {
    **_letters(),
    # Number row: 1..9 are 0x02..0x0A, 0 is 0x0B.
    **{str(n): ScanCode(0x01 + n) for n in range(1, 10)},
    "0": ScanCode(0x0B),
    "ESC": ScanCode(0x01),
    "MINUS": ScanCode(0x0C),
    "EQUALS": ScanCode(0x0D),
    "BACKSPACE": ScanCode(0x0E),
    "TAB": ScanCode(0x0F),
    "LBRACKET": ScanCode(0x1A),
    "RBRACKET": ScanCode(0x1B),
    "ENTER": ScanCode(0x1C),
    "LCTRL": ScanCode(0x1D),
    "SEMICOLON": ScanCode(0x27),
    "APOSTROPHE": ScanCode(0x28),
    "GRAVE": ScanCode(0x29),
    "LSHIFT": ScanCode(0x2A),
    "BACKSLASH": ScanCode(0x2B),
    "COMMA": ScanCode(0x33),
    "PERIOD": ScanCode(0x34),
    "SLASH": ScanCode(0x35),
    "RSHIFT": ScanCode(0x36),
    "NUMPAD_MULTIPLY": ScanCode(0x37),
    "LALT": ScanCode(0x38),
    "SPACE": ScanCode(0x39),
    "CAPSLOCK": ScanCode(0x3A),
    # F1..F10 are 0x3B..0x44; F11 and F12 sit elsewhere.
    **{f"F{n}": ScanCode(0x3A + n) for n in range(1, 11)},
    "F11": ScanCode(0x57),
    "F12": ScanCode(0x58),
    "NUMLOCK": ScanCode(0x45),
    "SCROLLLOCK": ScanCode(0x46),
    "NUMPAD7": ScanCode(0x47),
    "NUMPAD8": ScanCode(0x48),
    "NUMPAD9": ScanCode(0x49),
    "NUMPAD_MINUS": ScanCode(0x4A),
    "NUMPAD4": ScanCode(0x4B),
    "NUMPAD5": ScanCode(0x4C),
    "NUMPAD6": ScanCode(0x4D),
    "NUMPAD_PLUS": ScanCode(0x4E),
    "NUMPAD1": ScanCode(0x4F),
    "NUMPAD2": ScanCode(0x50),
    "NUMPAD3": ScanCode(0x51),
    "NUMPAD0": ScanCode(0x52),
    "NUMPAD_DECIMAL": ScanCode(0x53),
    # Extended keys (0xE0 prefix).
    "NUMPAD_ENTER": ScanCode(0x1C, True),
    "RCTRL": ScanCode(0x1D, True),
    "NUMPAD_DIVIDE": ScanCode(0x35, True),
    "RALT": ScanCode(0x38, True),
    "HOME": ScanCode(0x47, True),
    "UP": ScanCode(0x48, True),
    "PAGEUP": ScanCode(0x49, True),
    "LEFT": ScanCode(0x4B, True),
    "RIGHT": ScanCode(0x4D, True),
    "END": ScanCode(0x4F, True),
    "DOWN": ScanCode(0x50, True),
    "PAGEDOWN": ScanCode(0x51, True),
    "INSERT": ScanCode(0x52, True),
    "DELETE": ScanCode(0x53, True),
    "LWIN": ScanCode(0x5B, True),
    "RWIN": ScanCode(0x5C, True),
    "APPS": ScanCode(0x5D, True),
}

ALIASES: dict[str, str] = {
    "SHIFT": "LSHIFT",
    "CTRL": "LCTRL",
    "CONTROL": "LCTRL",
    "ALT": "LALT",
    "RETURN": "ENTER",
    "ESCAPE": "ESC",
    "DEL": "DELETE",
    "INS": "INSERT",
    "PGUP": "PAGEUP",
    "PGDN": "PAGEDOWN",
    "ARROWUP": "UP",
    "ARROWDOWN": "DOWN",
    "ARROWLEFT": "LEFT",
    "ARROWRIGHT": "RIGHT",
}

MODIFIERS = frozenset({"LSHIFT", "RSHIFT", "LCTRL", "RCTRL", "LALT", "RALT", "LWIN", "RWIN"})


def normalize_key(name: str) -> str:
    """Return the canonical key name ('shift' -> 'LSHIFT'), or raise KeyError for unknown keys."""
    key = name.strip().upper().replace(" ", "")
    key = ALIASES.get(key, key)
    if key not in SCANCODES:
        raise KeyError(name)
    return key


def press_order(keys: set[str] | frozenset[str]) -> list[str]:
    """Order for pressing several keys at once: modifiers first, so Ctrl+N arrives as Ctrl, then N."""
    return sorted(keys, key=lambda k: (k not in MODIFIERS, k))


def release_order(keys: set[str] | frozenset[str]) -> list[str]:
    """Order for releasing: the plain key first, modifiers last (the reverse of press_order)."""
    return list(reversed(press_order(keys)))
