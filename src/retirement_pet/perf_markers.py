"""Structured performance markers (DESIGN_V2 23; PERFORMANCE_BUDGET 3.3).

One monotonic timeline of named events, appended as JSONL next to the run
logs.  Markers carry no user content (privacy boundary).  The external perf
harness joins them with its own CreateProcess T0 to produce launch
latencies; in-app process_start is analysis-only and never the official T0.

Canonical names (extend, do not reuse):
    process_start, qt_ready, tray_ready, first_pet_paint,
    panel_requested, panel_first_paint,
    character_switch_requested, first_character_paint,
    hidden, resumed
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

#: marker names used by the application itself (harness-only names omitted)
PROCESS_START = "process_start"
QT_READY = "qt_ready"
TRAY_READY = "tray_ready"
FIRST_PET_PAINT = "first_pet_paint"


class _Monotonic(Protocol):
    def monotonic_ms(self) -> int: ...


class PerfMarkers:
    """Append-only JSONL marker log; failures never affect the app."""

    def __init__(self, path: Path | None = None, clock: _Monotonic | None = None):
        self._path = path
        self._clock = clock
        self._events: list[dict] = []
        if path is not None:
            self.mark(PROCESS_START)

    def mark(self, name: str, **fields) -> int:
        now = self._clock.monotonic_ms() if self._clock else 0
        entry = {"marker": name, "t_ms": now, **fields}
        self._events.append(entry)
        self._append(entry)
        return now

    def _append(self, entry: dict) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 - diagnostics must never crash
            logger.debug("failed to append perf marker", exc_info=True)

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def get(self, name: str) -> int | None:
        for entry in self._events:
            if entry["marker"] == name:
                return entry["t_ms"]
        return None
