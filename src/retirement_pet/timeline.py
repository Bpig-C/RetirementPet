"""Idle animation timeline (V12-01).

A monotonic-clock elapsed-time source for the idle loop.  The action
controller only tracks *explicit* actions; while none is running the UI
previously composed ``elapsed_ms=0``, which froze every pack idle on its
first frame.  This timeline gives idle a first-class clock.

Semantics (V12-01 requirement 1):

- monotonic clock only - system date changes can never rewind or jump the
  animation;
- ``reset`` starts a fresh loop: entering idle after an action ends, after
  a character switch, or at startup;
- ``pause``/``resume`` freeze elapsed while the pet is hidden or the
  session is suspended, so restore neither replays history nor jumps
  position inside the loop;
- explicit action durations keep their existing ActionRuntime semantics;
  this class never measures them.
"""

from __future__ import annotations

from enum import Enum

from retirement_pet.clock import Clock, SystemClock


class IdleResetReason(str, Enum):
    STARTUP = "startup"
    ACTION_END = "action_end"
    CHARACTER_SWITCH = "character_switch"


class IdleTimeline:
    """Freezeable monotonic stopwatch for the idle loop."""

    def __init__(self, clock: Clock | None = None):
        self._clock = clock or SystemClock()
        self._anchor_ms = self._clock.monotonic_ms()
        # None = running; an int = elapsed frozen at pause time.
        self._frozen_ms: int | None = None

    def reset(self, reason: IdleResetReason | str = IdleResetReason.STARTUP) -> None:
        """Restart the loop from zero (entering idle / character switch)."""
        self._anchor_ms = self._clock.monotonic_ms()
        if self._frozen_ms is not None:
            self._frozen_ms = 0

    def pause(self) -> None:
        """Freeze elapsed at the current value (hide / suspend)."""
        if self._frozen_ms is None:
            self._frozen_ms = self.elapsed_ms()

    def resume(self) -> None:
        """Continue from the frozen value; no history is replayed."""
        if self._frozen_ms is not None:
            self._anchor_ms = self._clock.monotonic_ms() - self._frozen_ms
            self._frozen_ms = None

    @property
    def paused(self) -> bool:
        return self._frozen_ms is not None

    def elapsed_ms(self, now_ms: int | None = None) -> int:
        """Monotonic idle elapsed time; never negative."""
        now = self._clock.monotonic_ms() if now_ms is None else now_ms
        if self._frozen_ms is not None:
            return self._frozen_ms
        return max(0, now - self._anchor_ms)


__all__ = ["IdleTimeline", "IdleResetReason"]
