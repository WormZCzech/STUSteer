"""Keyboard and mouse-button output on Windows: SendInput, guarded by the foreground window.

SendInput (user32.dll) puts synthetic input into the same queue real hardware input goes into. We
fill INPUT structures (ctypes describes the C layout; the union is declared in full because
SendInput checks sizeof(INPUT), 40 bytes on 64-bit Windows):

- keys with a scan code: KEYEVENTF_SCANCODE (+ KEYEVENTF_EXTENDEDKEY for 0xE0 keys, + KEYEVENTF_KEYUP
  to release). Windows derives the virtual key from the scan code, so raw-input games and VK-based
  plugins both see a normal key press. For letters, digits and punctuation the scan code comes from
  the foreground window's keyboard layout (MapVirtualKeyEx), so "Y" types Y on QWERTZ too. The code
  used for the press is remembered and reused for the release.
- keys without a usable scan code (media keys, PAUSE, "VK:0x.."): the virtual-key code only.
- mouse buttons: an INPUT_MOUSE event with MOUSEEVENTF_LEFTDOWN / ...UP (X buttons carry 1 or 2 in
  mouseData).

Focus guard: injected input goes to whatever window has focus. A press is only sent while the
foreground window belongs to one of the target processes (GTA5.exe by default); releases always go
out, so nothing can stay stuck. An empty list turns the guard off.

Windows limits worth knowing:
- UIPI: a normal process cannot inject into an elevated (run-as-admin) window, and SendInput does
  not report it. If GTA V / RAGE Plugin Hook runs as admin, run STUSteer as admin too.
"""

from __future__ import annotations

import ctypes
import logging
import os
from collections.abc import Callable, Sequence
from ctypes import wintypes

from ..keys import key_def

log = logging.getLogger("stusteer.keyboard")

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC_EX = 4
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ULONG_PTR = ctypes.c_size_t
# (down flag, up flag, mouseData) per mouse button number
MOUSE_FLAGS = {1: (0x0002, 0x0004, 0), 2: (0x0008, 0x0010, 0), 3: (0x0020, 0x0040, 0),
               4: (0x0080, 0x0100, 1), 5: (0x0080, 0x0100, 2)}


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


def _foreground() -> tuple[int, int]:
    """(window handle, thread id) of the foreground window; (0, 0) if none."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, 0
    pid = wintypes.DWORD()
    thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value, thread


def foreground_process_name() -> str | None:
    """File name of the process that owns the foreground window ('GTA5.exe'), or None."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    pid, _ = _foreground()
    if not pid:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
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
    """Decides whether a press may be sent now. Pure logic; the lookup is injected for tests."""

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
        self._user32.MapVirtualKeyExW.argtypes = (wintypes.UINT, wintypes.UINT, ctypes.c_void_p)
        self._user32.MapVirtualKeyExW.restype = wintypes.UINT
        self._user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
        self._user32.GetKeyboardLayout.restype = ctypes.c_void_p
        self._guard = FocusGuard(target_processes, foreground_process_name)
        self._down: dict[str, INPUT] = {}  # name -> the event used for the press, reused for the release

    def _layout_scan(self, vk: int) -> tuple[int, bool] | None:
        _, thread = _foreground()
        hkl = self._user32.GetKeyboardLayout(thread)
        code = self._user32.MapVirtualKeyExW(vk, MAPVK_VK_TO_VSC_EX, hkl)
        if not code:
            return None
        return code & 0xFF, (code >> 8) == 0xE0

    def _press_event(self, name: str) -> INPUT:
        spec = key_def(name)
        if spec.kind == "mouse":
            down, _, data = MOUSE_FLAGS[spec.mouse]
            return INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, data, down, 0, 0)))
        if spec.kind == "vk":
            return INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(spec.vk, 0, 0, 0, 0)))
        scan, extended = spec.scan, spec.extended
        if spec.layout:
            scan, extended = self._layout_scan(spec.vk) or (scan, extended)
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if extended else 0)
        return INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(0, scan, flags, 0, 0)))

    @staticmethod
    def _release_event(press: INPUT, name: str) -> INPUT:
        if press.type == INPUT_MOUSE:
            _, up, data = MOUSE_FLAGS[key_def(name).mouse]
            return INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, data, up, 0, 0)))
        ki = press.u.ki
        return INPUT(type=INPUT_KEYBOARD,
                     u=_INPUTUNION(ki=KEYBDINPUT(ki.wVk, ki.wScan, ki.dwFlags | KEYEVENTF_KEYUP, 0, 0)))

    def key(self, name: str, down: bool) -> bool:
        if down:
            if not self._guard.allows_press():
                return False
            event = self._press_event(name)
        else:
            press = self._down.pop(name, None) or self._press_event(name)
            event = self._release_event(press, name)
        sent = self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
        if sent != 1:
            log.error("SendInput failed for %s (error %d)", name, ctypes.get_last_error())
            return False
        if down:
            self._down[name] = event
        return True

    def close(self) -> None:
        pass
