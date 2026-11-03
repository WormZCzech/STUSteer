"""Keyboard output on Windows: SendInput with scan codes, guarded by the foreground window.

SendInput (user32.dll) puts synthetic input into the same queue real hardware input goes into. We
fill an INPUT structure of type INPUT_KEYBOARD with the key's scan code and the flags
KEYEVENTF_SCANCODE (+ KEYEVENTF_EXTENDEDKEY for 0xE0 keys, + KEYEVENTF_KEYUP to release). ctypes
describes the C structures so Python can pass them to the DLL; the union is declared in full because
SendInput checks sizeof(INPUT) (40 bytes on 64-bit Windows).

Focus guard: injected keys go to whatever window has focus. If you alt-tab to a browser and your
phone is still in your hand, "B" would be typed there. So a key press is only sent while the
foreground window belongs to one of settings.key_target_processes (GTA5.exe by default); releases
are always sent so nothing can stay stuck. An empty list turns the guard off.

Two Windows limits worth knowing:
- UIPI: a normal process cannot inject into an elevated (run-as-admin) window, and SendInput does
  not report it. If GTA V / RAGE Plugin Hook runs as admin, run STUSteer as admin too.
- Some anti-cheat blocks injected input. Not an issue for single-player GTA V with LSPDFR.
"""

from __future__ import annotations

import ctypes
import logging
import os
from collections.abc import Callable, Sequence
from ctypes import wintypes

from ..keys import SCANCODES

log = logging.getLogger("stusteer.keyboard")

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def foreground_process_name() -> str | None:
    """File name of the process that owns the foreground window ('GTA5.exe'), or None."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return os.path.basename(buffer.value)
    finally:
        kernel32.CloseHandle(handle)


class FocusGuard:
    """Decides whether a key press may be sent now. Pure logic; the lookup is injected for tests."""

    def __init__(self, targets: Sequence[str], lookup: Callable[[], str | None]) -> None:
        self._targets = {t.casefold() for t in targets}
        self._lookup = lookup
        self._last_blocked: str | None = None

    def allows_press(self) -> bool:
        if not self._targets:
            return True
        name = self._lookup()
        if name is not None and name.casefold() in self._targets:
            self._last_blocked = None
            return True
        if name != self._last_blocked:  # log once per window, not on every retry
            log.warning("key press held back: foreground window is %s, not %s", name, sorted(self._targets))
            self._last_blocked = name
        return False


class WindowsKeyboard:
    def __init__(self, target_processes: Sequence[str]) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self._user32.SendInput.restype = wintypes.UINT
        self._guard = FocusGuard(target_processes, foreground_process_name)

    def key(self, name: str, down: bool) -> bool:
        if down and not self._guard.allows_press():
            return False
        scan = SCANCODES[name]
        flags = KEYEVENTF_SCANCODE
        if scan.extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        if not down:
            flags |= KEYEVENTF_KEYUP
        event = INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(0, scan.code, flags, 0, 0)))
        sent = self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
        if sent != 1:
            log.error("SendInput failed for %s (error %d)", name, ctypes.get_last_error())
            return False
        return True

    def close(self) -> None:
        pass
