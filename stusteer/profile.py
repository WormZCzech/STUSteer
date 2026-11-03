"""Profiles: the phone layouts and what each control does on the PC.

A profile is one JSON file (see stusteer/profiles/gta5_lspdfr.json). It holds:

- settings: timing and safety (minimum key hold time, connection watchdog, which game windows may
  receive keystrokes);
- layouts: the screens you switch between on the phone (Drive, On foot, LSPDFR...). Each layout is
  a list of controls placed in percent of the phone screen (x, y, w, h from the top-left corner);
- per control a "bind": what it drives on the PC. One of
      {"pad": "RB"}               an Xbox 360 button
      {"key": "B"}                a keyboard key; {"key": ["LCTRL", "N"]} for a combination
      {"axis": "RT"}              an Xbox axis (pedals, the wheel)
      {"stick": "L"}              both axes of a thumbstick

The phone only ever sends "control id -> value". The PC looks the binding up here, so the phone
cannot press keys that are not in your profile, and the profile is the single place to change
bindings.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .keys import normalize_key
from .mapping import Curve

PAD_BUTTONS = frozenset(
    {
        "A", "B", "X", "Y", "LB", "RB", "LS", "RS", "BACK", "START", "GUIDE",
        "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
    }
)
STICK_AXES = ("LX", "LY", "RX", "RY")
TRIGGER_AXES = ("LT", "RT")
AXES = STICK_AXES + TRIGGER_AXES
STICKS = {"L": ("LX", "LY"), "R": ("RX", "RY")}
CONTROL_TYPES = frozenset({"button", "pedal", "wheel", "stick"})

DEFAULT_CURVES = {
    "wheel": Curve(range=90.0, deadzone=0.5),
    "pedal": Curve(range=1.0, deadzone=0.02),
    "stick": Curve(range=1.0, deadzone=0.08),
}


class ProfileError(ValueError):
    """The profile file is invalid; the message lists every problem with its JSON path."""


@dataclass
class Control:
    id: str
    type: str
    layout: str
    pad: str | None = None
    keys: tuple[str, ...] = ()
    axis: str | None = None
    stick: str | None = None
    curve: Curve | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_client(self) -> dict[str, Any]:
        """What the phone needs to draw this control (bindings included as labels)."""
        data = dict(self.raw)
        if self.curve is not None:
            data["curve"] = self.curve.to_dict()
        return data


@dataclass
class Settings:
    min_hold_ms: float = 60.0
    watchdog_ms: float = 500.0
    key_target_processes: tuple[str, ...] = ("GTA5.exe",)


@dataclass
class Profile:
    name: str
    settings: Settings
    layouts: list[dict[str, Any]]
    controls: dict[str, Control]

    def to_client(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "layouts": [
                {
                    "id": layout["id"],
                    "name": layout["name"],
                    "controls": [self.controls[c["id"]].to_client() for c in layout["controls"]],
                }
                for layout in self.layouts
            ],
        }

    def apply_tuning(self, tuning: dict[str, dict[str, Any]]) -> list[str]:
        """Apply saved curve overrides {control_id: {field: value}}; return warnings, never raise."""
        warnings = []
        for control_id, changes in tuning.items():
            control = self.controls.get(control_id)
            if control is None or control.curve is None:
                warnings.append(f"tuning for unknown or non-analog control '{control_id}' ignored")
                continue
            try:
                curve = control.curve.updated(changes)
            except (KeyError, ValueError, TypeError) as exc:
                warnings.append(f"tuning for '{control_id}' ignored: {exc}")
                continue
            problems = curve.validate()
            if problems:
                warnings.append(f"tuning for '{control_id}' ignored: {'; '.join(problems)}")
                continue
            control.curve = curve
        return warnings


def load_profile(path: Path) -> Profile:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{path}: not valid JSON: {exc}") from exc
    return parse_profile(data, source=str(path))


def parse_profile(data: Any, source: str = "profile") -> Profile:
    problems: list[str] = []

    def bad(where: str, what: str) -> None:
        problems.append(f"{where}: {what}")

    if not isinstance(data, dict):
        raise ProfileError(f"{source}: the top level must be an object")

    settings = _parse_settings(data.get("settings", {}), bad)
    layouts_raw = data.get("layouts")
    controls: dict[str, Control] = {}
    layouts: list[dict[str, Any]] = []
    if not isinstance(layouts_raw, list) or not layouts_raw:
        bad("layouts", "must be a non-empty list")
        layouts_raw = []

    layout_ids: set[str] = set()
    for li, layout in enumerate(layouts_raw):
        where = f"layouts[{li}]"
        if not isinstance(layout, dict):
            bad(where, "must be an object")
            continue
        layout_id = layout.get("id")
        if not isinstance(layout_id, str) or not layout_id:
            bad(f"{where}.id", "must be a non-empty string")
            continue
        if layout_id in layout_ids:
            bad(f"{where}.id", f"duplicate layout id '{layout_id}'")
        layout_ids.add(layout_id)
        items = layout.get("controls")
        if not isinstance(items, list):
            bad(f"{where}.controls", "must be a list")
            items = []
        kept = []
        for ci, item in enumerate(items):
            control = _parse_control(item, layout_id, f"{where}.controls[{ci}]", bad)
            if control is None:
                continue
            if control.id in controls:
                bad(f"{where}.controls[{ci}].id", f"duplicate control id '{control.id}' (ids are global)")
                continue
            controls[control.id] = control
            kept.append(item)
        layouts.append({"id": layout_id, "name": str(layout.get("name", layout_id)), "controls": kept})

    if problems:
        raise ProfileError(f"{source} has {len(problems)} problem(s):\n  " + "\n  ".join(problems))
    return Profile(str(data.get("name", "Unnamed profile")), settings, layouts, controls)


def _parse_settings(raw: Any, bad) -> Settings:
    s = Settings()
    if not isinstance(raw, dict):
        bad("settings", "must be an object")
        return s
    for name, low, high in (("min_hold_ms", 0, 1000), ("watchdog_ms", 100, 10000)):
        if name in raw:
            value = raw[name]
            if not isinstance(value, (int, float)) or not (low <= value <= high):
                bad(f"settings.{name}", f"must be a number in [{low}, {high}]")
            else:
                setattr(s, name, float(value))
    if "key_target_processes" in raw:
        value = raw["key_target_processes"]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            bad("settings.key_target_processes", "must be a list of process names (empty = any window)")
        else:
            s.key_target_processes = tuple(value)
    return s


def _parse_control(item: Any, layout_id: str, where: str, bad) -> Control | None:
    if not isinstance(item, dict):
        bad(where, "must be an object")
        return None
    control_id = item.get("id")
    if not isinstance(control_id, str) or not control_id:
        bad(f"{where}.id", "must be a non-empty string")
        return None
    ctype = item.get("type")
    if ctype not in CONTROL_TYPES:
        bad(f"{where}.type", f"must be one of {sorted(CONTROL_TYPES)}")
        return None
    for name in ("x", "y", "w", "h"):
        value = item.get(name)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not (0 <= value <= 100):
            bad(f"{where}.{name}", "must be a number in [0, 100] (percent of the screen)")
    control = Control(id=control_id, type=ctype, layout=layout_id, raw=item)
    bind = item.get("bind")
    if not isinstance(bind, dict) or len(bind) != 1:
        bad(f"{where}.bind", "must be an object with exactly one of pad, key, axis, stick")
        return control
    (kind, value), = bind.items()
    if ctype == "button":
        if kind == "pad":
            if value not in PAD_BUTTONS:
                bad(f"{where}.bind.pad", f"unknown pad button '{value}' (use {sorted(PAD_BUTTONS)})")
            control.pad = value
        elif kind == "key":
            names = value if isinstance(value, list) else [value]
            keys = []
            for name in names:
                try:
                    keys.append(normalize_key(str(name)))
                except KeyError:
                    bad(f"{where}.bind.key", f"unknown key '{name}' (see stusteer/keys.py)")
            if not names:
                bad(f"{where}.bind.key", "needs at least one key")
            control.keys = tuple(keys)
        else:
            bad(f"{where}.bind", "a button binds 'pad' or 'key'")
    elif ctype in ("pedal", "wheel"):
        if kind != "axis" or value not in AXES:
            bad(f"{where}.bind", f"a {ctype} binds 'axis' to one of {list(AXES)}")
        control.axis = value
    elif ctype == "stick":
        if kind != "stick" or value not in STICKS:
            bad(f"{where}.bind", "a stick binds 'stick' to 'L' or 'R'")
        control.stick = value
    if ctype != "button":
        try:
            control.curve = Curve.from_dict(item.get("curve"), DEFAULT_CURVES[ctype])
        except (KeyError, ValueError, TypeError) as exc:
            bad(f"{where}.curve", f"bad field: {exc}")
            control.curve = DEFAULT_CURVES[ctype]
        for problem in control.curve.validate():
            bad(f"{where}.curve", problem)
    return control
