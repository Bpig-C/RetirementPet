"""Overlay layer: blinking, gaze, bubbles and effects (design 7.4 / 8.1).

Blink and gaze never interrupt the main action; they are sampled by the
renderer each frame from :meth:`OverlayController.snapshot`.
"""

from __future__ import annotations

import random
from dataclasses import replace

from retirement_pet.clock import Clock
from retirement_pet.models import OverlaySnapshot

BLINK_DURATION_MS = 120
DOUBLE_BLINK_GAP_MS = 180
DOUBLE_BLINK_CHANCE = 0.15
GAZE_MAX = 1.0


class OverlayController:
    def __init__(self, clock: Clock, rng: random.Random | None = None):
        self._clock = clock
        self._rng = rng or random.Random()
        self._next_blink_ms: int | None = None
        self._blink_until_ms: int = 0
        self._double_blink_at_ms: int | None = None
        self._gaze_x = 0.0
        self._gaze_y = 0.0
        self._effects: dict[str, int] = {}  # kind -> expire monotonic ms
        self._bubble_text: str | None = None
        self._bubble_until_ms: int = 0

    # -- gaze ---------------------------------------------------------------

    def set_gaze_target(self, dx: float, dy: float) -> None:
        """Normalized gaze direction; clamped so eyes never leave the face."""
        magnitude = (dx * dx + dy * dy) ** 0.5
        if magnitude > 1.0:
            dx, dy = dx / magnitude, dy / magnitude
        self._gaze_x = max(-GAZE_MAX, min(GAZE_MAX, dx))
        self._gaze_y = max(-GAZE_MAX, min(GAZE_MAX, dy))

    # -- bubbles & effects ----------------------------------------------------

    def show_bubble(self, text: str, duration_ms: int = 4_000) -> None:
        now = self._clock.monotonic_ms()
        self._bubble_text = text
        self._bubble_until_ms = now + duration_ms

    def show_effect(self, kind: str, duration_ms: int = 4_000) -> None:
        now = self._clock.monotonic_ms()
        self._effects[kind] = now + duration_ms

    def clear_bubble(self) -> None:
        self._bubble_text = None
        self._bubble_until_ms = 0

    # -- main tick ---------------------------------------------------------------

    def tick(self, now_ms: int | None = None) -> None:
        now = now_ms if now_ms is not None else self._clock.monotonic_ms()

        # Blink scheduling: next blink 3-8s after the previous one settles.
        if self._next_blink_ms is None:
            self._next_blink_ms = now + int(self._rng.uniform(3_000, 8_000))
        if self._double_blink_at_ms is not None and now >= self._double_blink_at_ms:
            self._blink_until_ms = now + BLINK_DURATION_MS
            self._double_blink_at_ms = None
        if now >= self._next_blink_ms and now >= self._blink_until_ms:
            self._blink_until_ms = now + BLINK_DURATION_MS
            self._next_blink_ms = now + int(self._rng.uniform(3_000, 8_000))
            if self._rng.random() < DOUBLE_BLINK_CHANCE:
                self._double_blink_at_ms = now + BLINK_DURATION_MS + DOUBLE_BLINK_GAP_MS

        if self._bubble_text is not None and now > self._bubble_until_ms:
            self.clear_bubble()
        expired = [k for k, until in self._effects.items() if now > until]
        for kind in expired:
            self._effects.pop(kind, None)

    def snapshot(self, now_ms: int | None = None) -> OverlaySnapshot:
        now = now_ms if now_ms is not None else self._clock.monotonic_ms()
        return OverlaySnapshot(
            blink=now < self._blink_until_ms,
            gaze_x=self._gaze_x,
            gaze_y=self._gaze_y,
            effects=tuple(sorted(self._effects)),
            bubble_text=self._bubble_text,
        )

    # -- test helpers -----------------------------------------------------------

    def force_blink(self, now_ms: int) -> None:
        self._blink_until_ms = now_ms + BLINK_DURATION_MS
