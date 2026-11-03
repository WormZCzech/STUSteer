"""Dry-run sinks: log what would be sent to the game instead of sending it.

`stusteer run --dry-run` uses these. Nothing is installed or injected, so it runs on any OS and is
the first thing to try. The log lines are also what the end-to-end test reads.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

log = logging.getLogger("stusteer.dryrun")


class DryRunGamepad:
    def __init__(self) -> None:
        self.history: list[tuple[frozenset[str], dict[str, float]]] = []

    def set_state(self, buttons: frozenset[str], axes: Mapping[str, float]) -> None:
        self.history.append((buttons, dict(axes)))
        moved = " ".join(f"{name}={value:+.3f}" for name, value in axes.items() if value)
        log.info("PAD buttons=[%s] %s", ",".join(sorted(buttons)), moved or "axes=0")

    def close(self) -> None:
        log.info("PAD closed")


class DryRunKeyboard:
    def __init__(self) -> None:
        self.history: list[tuple[str, bool]] = []

    def key(self, name: str, down: bool) -> bool:
        self.history.append((name, down))
        log.info("KEY %s %s", name, "down" if down else "up")
        return True

    def close(self) -> None:
        pass


class NullGamepad:
    """outputs.gamepad.type = "none": no virtual pad at all (keyboard-only profiles)."""

    def set_state(self, buttons: frozenset[str], axes: Mapping[str, float]) -> None:
        pass

    def close(self) -> None:
        pass
