"""The controller: turns phone snapshots into gamepad and keyboard output.

How the phone talks to us: it never sends "press" or "release" events. It sends a *snapshot*, the
full current value of every control on the active layout ("wheel is at -12 degrees, gas at 0.7,
Backup is held"), whenever something changes and as a heartbeat ten times a second. A control that
is missing from the snapshot is released.

Why snapshots: nothing can get stuck. A lost or reordered message is corrected by the next one,
switching layouts releases the old layout's controls automatically, and "release everything" is
just an empty snapshot. Here we compare the wanted state with what we last sent and emit only the
differences (key down / key up, one gamepad report).

Three safety rules live here:

- Minimum hold time: games read the keyboard once per frame. A tap shorter than a frame (16 ms at
  60 fps) can be missed, so every key and button stays down for at least settings.min_hold_ms.
- Watchdog: if no snapshot arrives for settings.watchdog_ms (Wi-Fi drop, phone screen off,
  browser killed), everything is released. A car must never keep accelerating on its own.
- Several controls may drive the same output (two buttons both bound to E). The output is held
  while any of them is held; for axes, the value furthest from zero wins.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any

from .keys import press_order, release_order
from .mapping import shape_signed, shape_stick, shape_unsigned
from .output import GamepadSink, KeyboardSink
from .profile import AXES, STICKS, TRIGGER_AXES, Profile

log = logging.getLogger("stusteer.controller")


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value) if math.isfinite(value) else 0.0


def _pressed(value: Any) -> bool:
    return value is True or (isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0)


class Controller:
    def __init__(self, profile: Profile, gamepad: GamepadSink, keyboard: KeyboardSink) -> None:
        self.profile = profile
        self._gamepad = gamepad
        self._keyboard = keyboard
        self._want_buttons: set[str] = set()
        self._want_keys: set[str] = set()
        self._axes: dict[str, float] = dict.fromkeys(AXES, 0.0)
        self._buttons: set[str] = set()
        self._keys: set[str] = set()
        self._since: dict[str, float] = {}
        self._sent_pad: tuple | None = None
        self._last_input: float | None = None
        self._timed_out = False
        self._unknown: set[str] = set()
        self.layout: str | None = None

    # -- input -------------------------------------------------------------------------------

    def apply(self, values: Mapping[str, Any], now: float, layout: str | None = None) -> None:
        """Take one snapshot {control_id: value} from the phone and update the outputs."""
        buttons: set[str] = set()
        keys: set[str] = set()
        axes = dict.fromkeys(AXES, 0.0)
        for control_id, value in values.items():
            control = self.profile.controls.get(control_id)
            if control is None:
                if control_id not in self._unknown:
                    self._unknown.add(control_id)
                    log.warning("phone sent unknown control '%s' (ignored)", control_id)
                continue
            if control.type == "button":
                if _pressed(value):
                    if control.pad:
                        buttons.add(control.pad)
                    keys.update(control.keys)
            elif control.type == "pedal":
                self._combine(axes, control.axis, shape_unsigned(_number(value), control.curve))
            elif control.type == "wheel":
                self._combine(axes, control.axis, shape_signed(_number(value), control.curve))
            elif control.type == "stick":
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    sx, sy = shape_stick(_number(value[0]), _number(value[1]), control.curve)
                    ax, ay = STICKS[control.stick]
                    self._combine(axes, ax, sx)
                    self._combine(axes, ay, sy)
        if layout != self.layout and layout is not None:
            log.info("layout: %s", layout)
            self.layout = layout
        self._want_buttons, self._want_keys, self._axes = buttons, keys, axes
        self._last_input = now
        if self._timed_out:
            log.info("input is back")
            self._timed_out = False
        self._sync(now)

    @staticmethod
    def _combine(axes: dict[str, float], axis: str | None, value: float) -> None:
        if axis is None:
            return
        if axis in TRIGGER_AXES:
            value = min(1.0, max(0.0, value))
        else:
            value = min(1.0, max(-1.0, value))
        if abs(value) > abs(axes[axis]):
            axes[axis] = value

    # -- time ----------------------------------------------------------------------------------

    def tick(self, now: float) -> None:
        """Call often (every ~10 ms): finishes deferred releases and runs the watchdog."""
        watchdog = self.profile.settings.watchdog_ms / 1000.0
        if self._last_input is not None and not self._timed_out and now - self._last_input > watchdog:
            log.warning("no input from the phone for %.0f ms: releasing everything", watchdog * 1000)
            self._timed_out = True
            self._clear_wants()
        self._sync(now)

    def neutralize(self, now: float) -> None:
        """Release everything (phone disconnected). Minimum hold times still apply."""
        self._clear_wants()
        self._last_input = None
        self._sync(now)

    def release_all_now(self) -> None:
        """Release everything immediately, ignoring hold times (shutdown)."""
        self._clear_wants()
        for key in release_order(self._keys):
            self._keyboard.key(key, False)
        self._keys.clear()
        self._buttons.clear()
        self._since.clear()
        self._send_pad()

    def _clear_wants(self) -> None:
        self._want_buttons = set()
        self._want_keys = set()
        self._axes = dict.fromkeys(AXES, 0.0)

    # -- output --------------------------------------------------------------------------------

    def _held_long_enough(self, tag: str, now: float) -> bool:
        return now - self._since.get(tag, -math.inf) >= self.profile.settings.min_hold_ms / 1000.0

    def _sync(self, now: float) -> None:
        releasable = {k for k in self._keys - self._want_keys if self._held_long_enough("key:" + k, now)}
        for key in release_order(releasable):
            self._keyboard.key(key, False)
            self._keys.discard(key)
            self._since.pop("key:" + key, None)
        for key in press_order(self._want_keys - self._keys):
            if self._keyboard.key(key, True):
                self._keys.add(key)
                self._since["key:" + key] = now

        for button in {b for b in self._buttons - self._want_buttons if self._held_long_enough("pad:" + b, now)}:
            self._buttons.discard(button)
            self._since.pop("pad:" + button, None)
        for button in self._want_buttons - self._buttons:
            self._buttons.add(button)
            self._since["pad:" + button] = now
        self._send_pad()

    def _send_pad(self) -> None:
        state = (frozenset(self._buttons), tuple(round(self._axes[a], 4) for a in AXES))
        if state != self._sent_pad:
            self._gamepad.set_state(state[0], dict(zip(AXES, state[1])))
            self._sent_pad = state

    def outputs(self) -> dict[str, Any]:
        """What is being sent right now (shown on the phone for tuning)."""
        return {
            "buttons": sorted(self._buttons),
            "keys": sorted(self._keys),
            "axes": {a: round(v, 3) for a, v in self._axes.items()},
        }
