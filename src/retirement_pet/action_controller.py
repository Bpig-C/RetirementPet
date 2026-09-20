"""The single authority that changes the main action (design 7.2).

Pure Python with injectable clock and RNG: no Qt dependency, fully
deterministic under test.  The Qt layer adapts change events to signals.

Switch rules (design 7.2) - a request is accepted when:
1. no current action;
2. its priority beats the current action and the current is interruptible;
3. the current action ended naturally (handled in :meth:`tick`);
4. ``force=True`` (explicit user command);
5. the current action was invalidated (``end_current``).

Every action ends back to *no action*; the UI treats "no action" as IDLE.
Expired requests are dropped, never replayed later.
"""

from __future__ import annotations

import logging
import random
from typing import Callable

from retirement_pet.clock import Clock
from retirement_pet.models import ActionId, ActionRequest, ActionRuntime, ActionSpec
from retirement_pet.action_registry import ActionRegistry

logger = logging.getLogger(__name__)

#: Requests older than this are considered stale and dropped (design 7.2).
REQUEST_TTL_S = 5.0

ChangeCallback = Callable[["ActionChangeEvent"], None]

#: A global veto consulted by EVERY request path (V12-06 CR-C06).  Returns
#: False to reject the request regardless of source or force.  This is how
#: app-level policy (a semantic set to "disabled") reaches the engine
#: without the engine reading settings.
RequestGate = Callable[[ActionId, str, bool], bool]


class ActionChangeEvent:
    """Fired on every main-action transition (start, replace, end)."""

    __slots__ = ("previous", "current", "reason", "source")

    def __init__(
        self,
        previous: ActionRuntime | None,
        current: ActionRuntime | None,
        reason: str,
        source: str | None = None,
    ):
        self.previous = previous
        self.current = current
        # provenance of the request ("panel", "random", "resolve:...", ...);
        # None when the transition had no originating request (e.g. an end)
        self.source = source
        self.reason = reason  # "request" | "force" | "natural_end" | "invalidated"

    @property
    def action_id(self) -> ActionId | None:
        return self.current.spec.action_id if self.current else None


class ActionController:
    def __init__(
        self,
        registry: ActionRegistry | None = None,
        clock: Clock | None = None,
        rng: random.Random | None = None,
    ):
        from retirement_pet.clock import SystemClock

        self._registry = registry or ActionRegistry()
        self._clock = clock or SystemClock()
        self._rng = rng or random.Random()
        self._current: ActionRuntime | None = None
        self._listeners: list[ChangeCallback] = []
        self._cooldown_until: dict[ActionId, int] = {}
        self._request_gate: RequestGate | None = None

    # -- observation --------------------------------------------------------

    def on_change(self, callback: ChangeCallback) -> Callable[[], None]:
        """Subscribe; returns an unsubscribe function."""
        self._listeners.append(callback)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def _emit(self, previous, current, reason: str,
              source: str | None = None) -> None:
        event = ActionChangeEvent(previous, current, reason, source)
        for callback in list(self._listeners):
            try:
                callback(event)
            except Exception:  # noqa: BLE001 - listeners must not break control
                logger.exception("action change listener failed")

    @property
    def current(self) -> ActionRuntime | None:
        return self._current

    def set_request_gate(self, gate: RequestGate | None) -> None:
        """Install (or clear with None) the global request veto."""
        self._request_gate = gate

    def has_action(self, action_id: ActionId) -> bool:
        """Public registry lookup for UI-level gating (V12-06)."""
        return self._registry.has(action_id)

    def spec_of(self, action_id: ActionId) -> ActionSpec:
        return self._registry.get(action_id)

    def current_action(self) -> ActionId | None:
        return self._current.spec.action_id if self._current else None

    def is_on_cooldown(self, action_id: ActionId, now_ms: int | None = None) -> bool:
        now = now_ms if now_ms is not None else self._clock.monotonic_ms()
        return now < self._cooldown_until.get(action_id, 0)

    def cooldown_remaining_ms(self, action_id: ActionId, now_ms: int | None = None) -> int:
        now = now_ms if now_ms is not None else self._clock.monotonic_ms()
        return max(0, self._cooldown_until.get(action_id, 0) - now)

    # -- requests ------------------------------------------------------------

    def request(
        self,
        action_id: ActionId,
        source: str,
        force: bool = False,
        payload: dict | None = None,
        requested_at=None,
    ) -> bool:
        """Try to make ``action_id`` the main action.  True when accepted."""
        if not self._registry.has(action_id):
            logger.warning("unknown action requested: %s", action_id)
            return False
        if self._request_gate is not None                 and not self._request_gate(action_id, source, force):
            return False

        now = self._clock.now()
        stamp = requested_at or now
        age_s = (now - stamp).total_seconds()
        if age_s > REQUEST_TTL_S:
            logger.debug("dropping expired %s request from %s", action_id, source)
            return False

        spec = self._registry.get(action_id)
        current = self._current
        if current is not None:
            if current.spec.action_id == action_id and not force:
                return True  # already active; nothing to do
            if not force:
                higher = spec.priority > current.spec.priority
                if not (higher and current.spec.interruptible):
                    return False

        runtime = self._activate(spec, payload)
        self._current = runtime
        self._emit(current, runtime, "force" if force else "request",
                   source=source)
        logger.info(
            "action %s -> %s (source=%s, force=%s)",
            current.spec.action_id.value if current else "none",
            action_id.value,
            source,
            force,
        )
        return True

    def _activate(self, spec: ActionSpec, payload: dict | None) -> ActionRuntime:
        now_ms = self._clock.monotonic_ms()
        duration: int | None
        # V12-06 loop control: an explicit loop=False payload plays a
        # loop-capable (unbounded) action once.  The caller measures the
        # ACTIVE material and supplies its full-pass duration
        # (single_pass_ms, C06-R2); without a measured material this
        # stays the honest minimum-duration fallback.
        if spec.max_duration_ms is None:
            if payload and payload.get("loop") is False:
                material_ms = payload.get("single_pass_ms")
                if isinstance(material_ms, int) and material_ms > 0:
                    duration = material_ms
                else:
                    duration = max(1, spec.min_duration_ms)
            else:
                duration = None
        elif spec.max_duration_ms <= spec.min_duration_ms:
            duration = spec.max_duration_ms
        else:
            duration = int(
                self._rng.uniform(spec.min_duration_ms, spec.max_duration_ms)
            )
        return ActionRuntime(
            spec=spec,
            started_at_ms=now_ms,
            duration_ms=duration,
            payload=dict(payload or {}),
        )

    # -- lifecycle ------------------------------------------------------------

    def tick(self, now_ms: int | None = None) -> None:
        """Advance the state machine; ends actions that ran their course."""
        now = now_ms if now_ms is not None else self._clock.monotonic_ms()
        current = self._current
        if current is None:
            return
        current.frame_index = current.frame(now)
        duration = current.duration_ms
        if duration is not None and now - current.started_at_ms >= duration:
            # Natural end honors min_duration implicitly: duration >= min.
            self._end("natural_end", now)

    def end_current(self, reason: str = "invalidated") -> bool:
        """Force-end the current action (e.g. meeting window over)."""
        if self._current is None:
            return False
        self._end(reason)
        return True

    def end_if_action(self, action_id: ActionId, reason: str = "invalidated") -> bool:
        if self._current and self._current.spec.action_id == action_id:
            return self.end_current(reason)
        return False

    def _end(self, reason: str, now_ms: int | None = None) -> None:
        now = now_ms if now_ms is not None else self._clock.monotonic_ms()
        ended = self._current
        self._current = None
        if ended is not None and ended.spec.cooldown_ms > 0:
            self._cooldown_until[ended.spec.action_id] = now + ended.spec.cooldown_ms
        self._emit(ended, None, reason)

    # -- context helpers -------------------------------------------------------

    def is_busy_with_higher_priority(self, priority: int) -> bool:
        """True when something at or above ``priority`` is active."""
        return self._current is not None and self._current.spec.priority >= priority

    def reset(self) -> None:
        """Drop all state (used by tests and teardown)."""
        self._current = None
        self._cooldown_until.clear()
