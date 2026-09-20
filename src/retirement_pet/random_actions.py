"""Random ambient actions (design 8.2).

Fires on average every ``random_action_min/max_interval_s`` (default 45-120s).
The evaluation cadence (8-15s per design) is realized by the animation
clock's 1s tick + the next-fire timestamp; nothing fires per frame.
"""

from __future__ import annotations

import logging
import random
from typing import Callable

from retirement_pet.action_controller import ActionController
from retirement_pet.clock import Clock
from retirement_pet.models import RANDOM_ACTION_IDS, ActionId

logger = logging.getLogger(__name__)


class RandomActionScheduler:
    def __init__(
        self,
        controller: ActionController,
        clock: Clock,
        rng: random.Random | None = None,
        enabled: Callable[[], bool] | None = None,
        min_interval_s: Callable[[], float] | None = None,
        max_interval_s: Callable[[], float] | None = None,
        mode_allowed: Callable[[str], bool] | None = None,
    ):
        self._controller = controller
        self._clock = clock
        self._rng = rng or random.Random()
        self._enabled = enabled or (lambda: True)
        self._min_interval = min_interval_s or (lambda: 45.0)
        self._max_interval = max_interval_s or (lambda: 120.0)
        # V12-06 per-action mode gate: only "auto" semantics may fire here;
        # "manual"/"disabled" are excluded from random proposals.
        self._mode_allowed = mode_allowed or (lambda semantic: True)
        self._next_fire_ms: int | None = None

    # -- helpers -------------------------------------------------------------

    def _random_specs(self):
        from retirement_pet.action_registry import ActionRegistry

        registry: ActionRegistry = self._controller._registry  # noqa: SLF001
        for action_id in RANDOM_ACTION_IDS:
            if registry.has(action_id):
                yield registry.get(action_id)

    def _draw_next_fire(self, now_ms: int) -> int:
        lo = max(1.0, float(self._min_interval()))
        hi = max(lo, float(self._max_interval()))
        return now_ms + int(self._rng.uniform(lo, hi) * 1000)

    @property
    def next_fire_ms(self) -> int | None:
        return self._next_fire_ms

    # -- main entry ------------------------------------------------------------

    def tick(self, now_ms: int | None = None) -> None:
        now = now_ms if now_ms is not None else self._clock.monotonic_ms()
        if not self._enabled():
            self._next_fire_ms = None
            return
        if self._next_fire_ms is None:
            self._next_fire_ms = self._draw_next_fire(now)
            return
        if now < self._next_fire_ms:
            return

        fired = self._try_fire(now)
        self._next_fire_ms = self._draw_next_fire(now)
        if not fired:
            # Blocked (busy with meeting/work/user action): retry sooner.
            self._next_fire_ms = now + int(self._rng.uniform(8, 15) * 1000)

    def _try_fire(self, now_ms: int) -> bool:
        current = self._controller.current
        if current is not None and current.spec.priority >= 50:
            return False  # work/rest/meeting/music/user action owns the pet
        candidates = [
            spec
            for spec in self._random_specs()
            if self._mode_allowed(spec.action_id.value)
            and not self._controller.is_on_cooldown(spec.action_id, now_ms)
            and spec.weight > 0
        ]
        if not candidates:
            return False
        chosen = self._weighted_choice(candidates)
        accepted = self._controller.request(chosen.action_id, source="random")
        if accepted:
            logger.debug("random action fired: %s", chosen.action_id.value)
        return accepted

    def _weighted_choice(self, specs):
        total = sum(spec.weight for spec in specs)
        pick = self._rng.uniform(0, total)
        acc = 0.0
        for spec in specs:
            acc += spec.weight
            if pick <= acc:
                return spec
        return specs[-1]

    def fire_now(self) -> bool:
        """Manual trigger (debug/tests)."""
        return self._try_fire(self._clock.monotonic_ms())
