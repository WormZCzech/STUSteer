"""Output sinks: where controller state goes on the PC.

The controller logic (stusteer/controller.py) only talks to these two small interfaces, so it can be
tested without Windows, and --dry-run can swap in sinks that only log.

- GamepadSink: the virtual Xbox 360 pad (ViGEmBus through vgamepad, see gamepad_vigem.py).
- KeyboardSink: injected key presses (Windows SendInput, see keyboard_win.py).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class GamepadSink(Protocol):
    def set_state(self, buttons: frozenset[str], axes: Mapping[str, float]) -> None:
        """Send the whole pad state: pressed button names and all six axes (sticks -1..1, triggers 0..1)."""

    def close(self) -> None:
        """Release everything and unplug the virtual pad."""


class KeyboardSink(Protocol):
    def key(self, name: str, down: bool) -> bool:
        """Press or release one key (canonical name from keys.py). Return False if it was not sent."""

    def close(self) -> None:
        """Free resources (the controller releases keys before calling this)."""
