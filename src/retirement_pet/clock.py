"""Injectable time sources.

All time-dependent components accept a ``Clock`` so tests never depend on the
real system time.  ``SystemClock`` is the production implementation.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Minimal time source used across the app."""

    def now(self) -> datetime:
        """Wall-clock local time."""

    def monotonic_ms(self) -> int:
        """Monotonic milliseconds, immune to system clock changes."""


class SystemClock:
    """Production clock backed by ``datetime.now`` and ``time.monotonic_ns``."""

    def now(self) -> datetime:
        return datetime.now()

    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000


class FakeClock:
    """Manually advanced clock for deterministic tests."""

    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 8, 23, 12, 0, 0)
        self._ms = 0

    def now(self) -> datetime:
        return self._now

    def monotonic_ms(self) -> int:
        return self._ms

    def advance_ms(self, ms: int) -> None:
        self._ms += int(ms)
        self._now += timedelta(milliseconds=ms)

    def advance_s(self, seconds: float) -> None:
        self.advance_ms(int(seconds * 1000))
