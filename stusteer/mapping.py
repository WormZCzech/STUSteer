"""Response curves: how a raw phone input becomes a controller axis value.

Every analog control (wheel, pedal, stick) has a Curve. The phone sends raw values (degrees of tilt
for the wheel, 0..1 for a pedal, a unit vector for a stick); the curve turns them into XInput axis
values. Keeping this on the PC side means one place to tune and test it.

The shaping, for an input magnitude m:

    m <= deadzone              -> 0                     (ignore jitter around the centre)
    t = (m - deadzone) / (range - deadzone), capped at 1   (0..1 between deadzone and full lock)
    t = t ** gamma             (gamma > 1: finer control near the centre, faster near the end)
    out = anti + (1 - anti) * t    (anti-deadzone: jump straight past the game's own deadzone)

Anti-deadzone matters for GTA V: the game ignores small stick deflections itself, so a gentle tilt
would do nothing. Setting anti_deadzone to the game's deadzone (try 0.1-0.25) makes the first
degree of tilt already turn the wheels.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Any


@dataclass(frozen=True)
class Curve:
    """Shaping parameters for one analog control. Units of range/deadzone follow the input."""

    range: float = 1.0
    deadzone: float = 0.0
    anti_deadzone: float = 0.0
    gamma: float = 1.0
    invert: bool = False

    def validate(self) -> list[str]:
        """Return problems with these parameters (empty list = valid)."""
        problems = []
        if not (self.range > 0 and math.isfinite(self.range)):
            problems.append("range must be a positive number")
        if not (0 <= self.deadzone < self.range):
            problems.append("deadzone must be >= 0 and smaller than range")
        if not (0 <= self.anti_deadzone < 1):
            problems.append("anti_deadzone must be in [0, 1)")
        if not (0.2 <= self.gamma <= 5):
            problems.append("gamma must be in [0.2, 5]")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def updated(self, changes: dict[str, Any]) -> Curve:
        """A copy with some fields changed; unknown fields raise KeyError, bad types ValueError."""
        clean: dict[str, Any] = {}
        for name, value in changes.items():
            if name not in Curve.__dataclass_fields__:
                raise KeyError(name)
            if name == "invert":
                clean[name] = bool(value)
            else:
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError(f"{name} must be finite")
                clean[name] = number
        return replace(self, **clean)

    @staticmethod
    def from_dict(data: dict[str, Any] | None, default: Curve) -> Curve:
        return default.updated(data or {})


def _shape_magnitude(m: float, c: Curve) -> float:
    if m <= c.deadzone:
        return 0.0
    t = min(1.0, (m - c.deadzone) / (c.range - c.deadzone))
    t = t**c.gamma
    return c.anti_deadzone + (1.0 - c.anti_deadzone) * t


def shape_signed(x: float, c: Curve) -> float:
    """Shape a signed input (wheel degrees) to -1..1."""
    if not math.isfinite(x):
        return 0.0
    out = math.copysign(_shape_magnitude(abs(x), c), x)
    if out == 0.0:
        return 0.0
    return -out if c.invert else out


def shape_unsigned(x: float, c: Curve) -> float:
    """Shape a 0..1 input (pedal) to 0..1. Negative input counts as released; invert is ignored."""
    if not math.isfinite(x) or x <= 0:
        return 0.0
    return _shape_magnitude(x, c)


def shape_stick(x: float, y: float, c: Curve) -> tuple[float, float]:
    """Shape a stick vector radially: the deadzone is a circle, the direction is kept."""
    if not (math.isfinite(x) and math.isfinite(y)):
        return 0.0, 0.0
    m = math.hypot(x, y)
    if m == 0:
        return 0.0, 0.0
    out = _shape_magnitude(m, c)
    sx, sy = x / m * out, y / m * out
    if c.invert:
        sy = -sy
    return sx, sy
