"""The virtual Xbox 360 controller, through ViGEmBus and the vgamepad package.

How it works: ViGEmBus is a Windows kernel driver that can plug in virtual USB game controllers. To
Windows and to games, a ViGEm Xbox 360 pad looks exactly like a real wired one (XInput device 0, 1,
2 or 3). GTA V reads controllers only through XInput, which is why we emulate an Xbox pad and not a
"wheel": the game has no native wheel support and steers from the left stick's X axis.

vgamepad (MIT licence, PyPI) is a thin Python wrapper over ViGEm's client DLL; it ships the DLL and,
on first install, offers to install the ViGEmBus driver. This file is the only one that imports it.

XInput value ranges: sticks are signed 16-bit (-32768..32767, up is positive), triggers 0..255.
vgamepad's *_float methods take -1..1 and 0..1 and convert for us.
"""

from __future__ import annotations

from collections.abc import Mapping


class VigemGamepad:
    def __init__(self) -> None:
        import vgamepad as vg  # imported here so the rest of STUSteer works without it

        self._pad = vg.VX360Gamepad()
        b = vg.XUSB_BUTTON
        self._map = {
            "A": b.XUSB_GAMEPAD_A,
            "B": b.XUSB_GAMEPAD_B,
            "X": b.XUSB_GAMEPAD_X,
            "Y": b.XUSB_GAMEPAD_Y,
            "LB": b.XUSB_GAMEPAD_LEFT_SHOULDER,
            "RB": b.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "LS": b.XUSB_GAMEPAD_LEFT_THUMB,
            "RS": b.XUSB_GAMEPAD_RIGHT_THUMB,
            "BACK": b.XUSB_GAMEPAD_BACK,
            "START": b.XUSB_GAMEPAD_START,
            "GUIDE": b.XUSB_GAMEPAD_GUIDE,
            "DPAD_UP": b.XUSB_GAMEPAD_DPAD_UP,
            "DPAD_DOWN": b.XUSB_GAMEPAD_DPAD_DOWN,
            "DPAD_LEFT": b.XUSB_GAMEPAD_DPAD_LEFT,
            "DPAD_RIGHT": b.XUSB_GAMEPAD_DPAD_RIGHT,
        }
        self._pad.reset()
        self._pad.update()

    def set_state(self, buttons: frozenset[str], axes: Mapping[str, float]) -> None:
        for name, button in self._map.items():
            if name in buttons:
                self._pad.press_button(button=button)
            else:
                self._pad.release_button(button=button)
        self._pad.left_joystick_float(x_value_float=axes["LX"], y_value_float=axes["LY"])
        self._pad.right_joystick_float(x_value_float=axes["RX"], y_value_float=axes["RY"])
        self._pad.left_trigger_float(value_float=axes["LT"])
        self._pad.right_trigger_float(value_float=axes["RT"])
        self._pad.update()  # one report to the driver with the whole state

    def close(self) -> None:
        self._pad.reset()
        self._pad.update()
        del self._pad  # vgamepad unplugs the virtual pad when the object is freed
