"""Where STUSteer keeps its own files, the pairing PIN and saved tuning.

Everything lives in one folder: %APPDATA%\\STUSteer on Windows, ~/.config/stusteer elsewhere, or
the folder given by the STUSTEER_HOME environment variable (the tests use that). Nothing is written
anywhere else; STUSteer never touches the game folder.

    pin.txt                  the 6-digit pairing PIN the phone must send
    ca.crt / ca.key          the local certificate authority (only for HTTPS, see certs.py)
    server.crt / server.key  the receiver's HTTPS certificate, signed by that CA
    tuning-<profile>.json    curve and option changes saved from the phone's tuning panel
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any


def app_dir() -> Path:
    override = os.environ.get("STUSTEER_HOME")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", Path.home())) / "STUSteer"
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "stusteer"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_atomic(path: Path, text: str) -> None:
    """Write to a temp file next to the target, then rename over it: a crash never leaves half a file."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_pin(folder: Path, regenerate: bool = False) -> str:
    path = folder / "pin.txt"
    if not regenerate and path.exists():
        pin = path.read_text(encoding="utf-8").strip()
        if len(pin) == 6 and pin.isdigit():
            return pin
    pin = f"{secrets.randbelow(10**6):06d}"
    write_atomic(path, pin + "\n")
    return pin


class TuningStore:
    """Curve and option changes saved from the phone, one JSON file per profile.

    {"schema": 2, "curves": {"phone/gas": {"shape": "linear"}}, "options": {"phone/gas": {"mode": "relative"}}}
    Keys are "device/control"; only the fields that differ from the profile are stored.
    """

    def __init__(self, folder: Path, profile_stem: str) -> None:
        self.path = folder / f"tuning-{profile_stem}.json"

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict) or data.get("schema") != 2:
            return {}  # an older format: ignored, the profile's own values apply
        return {"curves": data.get("curves") or {}, "options": data.get("options") or {}}

    def save(self, curves: dict[str, dict[str, Any]], options: dict[str, dict[str, Any]]) -> None:
        text = json.dumps({"schema": 2, "curves": curves, "options": options}, indent=2, sort_keys=True)
        write_atomic(self.path, text + "\n")
