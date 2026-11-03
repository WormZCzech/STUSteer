"""Profiles: the configuration of devices, outputs and what every control does.

A profile is one JSON file (stusteer/profiles/gta5_lspdfr.json, schema 2):

    {
      "schema": 2,
      "name": "GTA V Legacy + LSPDFR",
      "settings": {"min_hold_ms": 60, "watchdog_ms": 500},            timing and safety
      "outputs": {                                                     what the PC emulates
        "gamepad":  {"type": "xbox360"},                               or "none"
        "keyboard": {"target_processes": ["GTA5.exe"]}                 [] = any window
      },
      "devices": [                                                     input hardware (devices.py)
        {"id": "phone", "type": "phone", "name": "...", "layouts": [...]},
        {"id": "box", "type": "network", "enabled": false, "controls": [...]}
      ]
    }

A phone device has layouts (the screens you switch between), each a list of controls placed in
percent of the screen (x, y, w, h from the top-left corner). Other devices have a flat "controls"
list. Every control has a "bind": what it drives on the PC. One of

    {"pad": "RB"}                 an Xbox 360 button
    {"key": "F13"}                any key, mouse button or raw code (keys.py); a list = a combination
    {"axis": "RT"}                an Xbox axis (pedals, wheels)
    {"stick": "L"}                both axes of a thumbstick

Analog controls also have a "curve" (mapping.py). Pedals have options the phone uses: "mode"
("absolute": thumb height = amount; "relative": touch = 0, slide up for more; "digital": on/off)
and "travel" (relative mode: share of the pedal height that gives full).

Devices only ever send "control id -> value". The PC looks the binding up here, so a device cannot
press keys that are not in the profile.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .devices import DEVICE_TYPES, PLANNED_TYPES
from .keys import normalize_key
from .mapping import Curve

SCHEMA = 2
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
GAMEPAD_TYPES = frozenset({"xbox360", "none"})

DEFAULT_CURVES = {
    "wheel": Curve(range=90.0, deadzone=0.5),
    "pedal": Curve(range=1.0, deadzone=0.02, shape="scurve"),
    "stick": Curve(range=1.0, deadzone=0.08),
}

# Options a control type has, with their defaults and checks; tunable from the phone.
OPTION_SPECS: dict[str, dict[str, Any]] = {
    "pedal": {
        "mode": ("absolute", ("absolute", "relative", "digital")),
        "travel": (0.45, (0.15, 1.0)),
    },
}


class ProfileError(ValueError):
    """The profile file is invalid; the message lists every problem with its JSON path."""


def check_options(ctype: str, changes: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate option changes for a control type; return (clean values, problems)."""
    specs = OPTION_SPECS.get(ctype, {})
    clean: dict[str, Any] = {}
    problems: list[str] = []
    for name, value in changes.items():
        if name not in specs:
            problems.append(f"'{name}' is not an option of a {ctype}")
            continue
        _, rule = specs[name]
        if isinstance(rule[0], str):
            if value not in rule:
                problems.append(f"{name} must be one of {list(rule)}")
                continue
        else:
            low, high = rule
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not (low <= value <= high):
                problems.append(f"{name} must be a number in [{low}, {high}]")
                continue
            value = float(value)
        clean[name] = value
    return clean, problems


@dataclass
class Control:
    id: str
    type: str
    device: str
    layout: str | None = None
    pad: str | None = None
    keys: tuple[str, ...] = ()
    axis: str | None = None
    stick: str | None = None
    curve: Curve | None = None
    options: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def address(self) -> str:
        """'device/control', the key used for tuning."""
        return f"{self.device}/{self.id}"

    def to_client(self) -> dict[str, Any]:
        """What the device needs (the phone draws it): the profile entry with current curve/options."""
        data = dict(self.raw)
        data.update(self.options)
        if self.curve is not None:
            data["curve"] = self.curve.to_dict()
        return data


@dataclass
class Device:
    id: str
    type: str
    name: str
    enabled: bool
    layouts: list[dict[str, Any]]  # phone: [{"id", "name", "controls": [raw items]}]; others: []
    controls: dict[str, Control]

    def to_client(self) -> dict[str, Any]:
        if DEVICE_TYPES[self.type].has_layouts:
            return {
                "name": self.name,
                "layouts": [
                    {"id": l["id"], "name": l["name"], "controls": [self.controls[c["id"]].to_client() for c in l["controls"]]}
                    for l in self.layouts
                ],
            }
        return {"name": self.name, "controls": [c.to_client() for c in self.controls.values()]}


@dataclass
class Settings:
    min_hold_ms: float = 60.0
    watchdog_ms: float = 500.0


@dataclass
class Outputs:
    gamepad: str = "xbox360"
    key_targets: tuple[str, ...] = ("GTA5.exe",)


@dataclass
class Profile:
    name: str
    settings: Settings
    outputs: Outputs
    devices: dict[str, Device]

    def device(self, device_id: str | None) -> Device | None:
        """The enabled device with this id; None = the first enabled phone (the default)."""
        if device_id is None:
            return next((d for d in self.devices.values() if d.enabled and d.type == "phone"), None)
        device = self.devices.get(device_id)
        return device if device is not None and device.enabled else None

    def control(self, address: str) -> Control | None:
        device_id, _, control_id = address.partition("/")
        device = self.devices.get(device_id)
        return device.controls.get(control_id) if device else None

    def apply_tuning(self, tuning: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
        """Apply saved overrides {"curves": {address: {...}}, "options": {...}}; return warnings."""
        warnings = []
        for address, changes in (tuning.get("curves") or {}).items():
            control = self.control(address)
            if control is None or control.curve is None:
                warnings.append(f"saved curve for unknown or non-analog control '{address}' ignored")
                continue
            try:
                curve = control.curve.updated(changes)
            except (KeyError, ValueError, TypeError) as exc:
                warnings.append(f"saved curve for '{address}' ignored: {exc}")
                continue
            problems = curve.validate()
            if problems:
                warnings.append(f"saved curve for '{address}' ignored: {'; '.join(problems)}")
                continue
            control.curve = curve
        for address, changes in (tuning.get("options") or {}).items():
            control = self.control(address)
            if control is None or not isinstance(changes, dict):
                warnings.append(f"saved options for unknown control '{address}' ignored")
                continue
            clean, problems = check_options(control.type, changes)
            if problems:
                warnings.append(f"saved options for '{address}': {'; '.join(problems)}")
            control.options.update(clean)
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
    if data.get("schema") != SCHEMA:
        raise ProfileError(f"{source}: expected \"schema\": {SCHEMA} (see stusteer/profile.py for the format)")

    settings = _parse_settings(data.get("settings", {}), bad)
    outputs = _parse_outputs(data.get("outputs", {}), bad)
    devices: dict[str, Device] = {}
    raw_devices = data.get("devices")
    if not isinstance(raw_devices, list) or not raw_devices:
        bad("devices", "must be a non-empty list")
        raw_devices = []
    for di, raw in enumerate(raw_devices):
        device = _parse_device(raw, f"devices[{di}]", bad)
        if device is None:
            continue
        if device.id in devices:
            bad(f"devices[{di}].id", f"duplicate device id '{device.id}'")
            continue
        devices[device.id] = device
    if devices and not any(d.enabled for d in devices.values()):
        bad("devices", "no device is enabled")

    if problems:
        raise ProfileError(f"{source} has {len(problems)} problem(s):\n  " + "\n  ".join(problems))
    return Profile(str(data.get("name", "Unnamed profile")), settings, outputs, devices)


def _parse_settings(raw: Any, bad) -> Settings:
    s = Settings()
    if not isinstance(raw, dict):
        bad("settings", "must be an object")
        return s
    for name, low, high in (("min_hold_ms", 0, 1000), ("watchdog_ms", 100, 10000)):
        if name in raw:
            value = raw[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not (low <= value <= high):
                bad(f"settings.{name}", f"must be a number in [{low}, {high}]")
            else:
                setattr(s, name, float(value))
    return s


def _parse_outputs(raw: Any, bad) -> Outputs:
    o = Outputs()
    if not isinstance(raw, dict):
        bad("outputs", "must be an object")
        return o
    gamepad = raw.get("gamepad", {})
    if isinstance(gamepad, dict) and "type" in gamepad:
        if gamepad["type"] not in GAMEPAD_TYPES:
            bad("outputs.gamepad.type", f"must be one of {sorted(GAMEPAD_TYPES)}")
        else:
            o.gamepad = gamepad["type"]
    keyboard = raw.get("keyboard", {})
    if isinstance(keyboard, dict) and "target_processes" in keyboard:
        value = keyboard["target_processes"]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            bad("outputs.keyboard.target_processes", "must be a list of process names (empty = any window)")
        else:
            o.key_targets = tuple(value)
    return o


def _parse_device(raw: Any, where: str, bad) -> Device | None:
    if not isinstance(raw, dict):
        bad(where, "must be an object")
        return None
    device_id = raw.get("id")
    if not isinstance(device_id, str) or not device_id or "/" in device_id:
        bad(f"{where}.id", "must be a non-empty string without '/'")
        return None
    dtype = raw.get("type")
    if dtype not in DEVICE_TYPES:
        hint = f"planned, not available yet: {PLANNED_TYPES[dtype]}" if dtype in PLANNED_TYPES else (
            f"must be one of {sorted(DEVICE_TYPES)}")
        bad(f"{where}.type", hint)
        return None
    kind = DEVICE_TYPES[dtype]
    device = Device(device_id, dtype, str(raw.get("name", device_id)), raw.get("enabled", True) is not False, [], {})

    def add(item: Any, cwhere: str, layout: str | None) -> bool:
        control = _parse_control(item, device_id, layout, cwhere, bad, placed=kind.has_layouts)
        if control is None:
            return False
        if control.id in device.controls:
            bad(f"{cwhere}.id", f"duplicate control id '{control.id}' in device '{device_id}'")
            return False
        device.controls[control.id] = control
        return True

    if kind.has_layouts:
        layouts = raw.get("layouts")
        if not isinstance(layouts, list) or not layouts:
            bad(f"{where}.layouts", "must be a non-empty list")
            layouts = []
        layout_ids: set[str] = set()
        for li, layout in enumerate(layouts):
            lwhere = f"{where}.layouts[{li}]"
            if not isinstance(layout, dict) or not isinstance(layout.get("id"), str) or not layout["id"]:
                bad(lwhere, "must be an object with a non-empty string id")
                continue
            if layout["id"] in layout_ids:
                bad(f"{lwhere}.id", f"duplicate layout id '{layout['id']}'")
            layout_ids.add(layout["id"])
            items = layout.get("controls")
            if not isinstance(items, list):
                bad(f"{lwhere}.controls", "must be a list")
                items = []
            kept = [item for ci, item in enumerate(items) if add(item, f"{lwhere}.controls[{ci}]", layout["id"])]
            device.layouts.append({"id": layout["id"], "name": str(layout.get("name", layout["id"])), "controls": kept})
    else:
        items = raw.get("controls")
        if not isinstance(items, list) or not items:
            bad(f"{where}.controls", "must be a non-empty list")
            items = []
        for ci, item in enumerate(items):
            add(item, f"{where}.controls[{ci}]", None)
    return device


def _parse_control(item: Any, device: str, layout: str | None, where: str, bad, placed: bool) -> Control | None:
    if not isinstance(item, dict):
        bad(where, "must be an object")
        return None
    control_id = item.get("id")
    if not isinstance(control_id, str) or not control_id or "/" in control_id:
        bad(f"{where}.id", "must be a non-empty string without '/'")
        return None
    ctype = item.get("type")
    if ctype not in CONTROL_TYPES:
        bad(f"{where}.type", f"must be one of {sorted(CONTROL_TYPES)}")
        return None
    if placed:
        for name in ("x", "y", "w", "h"):
            value = item.get(name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not (0 <= value <= 100):
                bad(f"{where}.{name}", "must be a number in [0, 100] (percent of the screen)")
    raw = {k: v for k, v in item.items() if k not in OPTION_SPECS.get(ctype, {})}
    control = Control(id=control_id, type=ctype, device=device, layout=layout, raw=raw)

    specs = OPTION_SPECS.get(ctype, {})
    control.options = {name: default for name, (default, _) in specs.items()}
    given = {k: v for k, v in item.items() if k in specs}
    clean, option_problems = check_options(ctype, given)
    for problem in option_problems:
        bad(where, problem)
    control.options.update(clean)

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
            if not names:
                bad(f"{where}.bind.key", "needs at least one key")
            keys = []
            for name in names:
                try:
                    keys.append(normalize_key(str(name)))
                except KeyError:
                    bad(f"{where}.bind.key", f"unknown key '{name}' (python -m stusteer keys lists them)")
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
