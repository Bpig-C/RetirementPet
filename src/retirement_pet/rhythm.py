"""Work/rest rhythm derived from system idle time (design 8.4 / 8.5).

- sustained keyboard/mouse activity  -> WORK (after ACTIVE_STABLE_S);
- idle beyond ``work_idle_threshold_s`` -> REST;
- resumed input after rest -> brief delay, then back to idle/work;
- rhythm switches are debounced by MIN_SWITCH_GAP_S;
- continuous-work reminder fires once per continuous work session;
- idle-time unknown never implies rest.
"""

from __future__ import annotations

import logging
from typing import Callable

from retirement_pet.action_controller import ActionController
from retirement_pet.activity_monitor import ActivityMonitor
from retirement_pet.clock import Clock
from retirement_pet.models import ActionId
from retirement_pet.overlay import OverlayController

logger = logging.getLogger(__name__)

ACTIVE_STABLE_S = 30.0      # continuous activity before entering WORK
ACTIVE_THRESHOLD_S = 5.0    # idle below this counts as "active"
RESUME_DELAY_S = 5.0        # wait after input resumes before leaving REST
MIN_SWITCH_GAP_S = 10.0     # work/rest stability window (design 7.3)
WORK_SESSION_RESET_IDLE_S = 120.0


class RhythmController:
    def __init__(
        self,
        controller: ActionController,
        clock: Clock,
        monitor: ActivityMonitor,
        settings: dict,
        overlay: OverlayController | None = None,
        settings_provider: Callable[[], dict] | None = None,
    ):
        self._controller = controller
        self._clock = clock
        self._monitor = monitor
        self._settings_provider = settings_provider or (lambda: settings)
        self._overlay = overlay

        self._active_since: float | None = None   # wall seconds of continuous activity
        self._idle_since: float | None = None
        self._last_switch_s: float = -1e9
        self._work_session_start: float | None = None
        self._reminded_this_session = False
        self._resume_at: float | None = None

    # -- helpers -----------------------------------------------------------

    def _now_s(self) -> float:
        return self._clock.now().timestamp()

    def _cfg(self, key: str, default):
        try:
            return self._settings_provider().get(key, default)
        except Exception:  # noqa: BLE001
            return default

    def _rhythm_switch_allowed(self, now_s: float) -> bool:
        return (now_s - self._last_switch_s) >= MIN_SWITCH_GAP_S

    def _mark_switch(self, now_s: float) -> None:
        self._last_switch_s = now_s

    def _bubble(self, text: str, seconds: float = 6.0) -> None:
        if self._overlay is not None:
            self._overlay.show_bubble(text, int(seconds * 1000))

    # -- main entry --------------------------------------------------------

    def activity_state(self) -> str:
        """Explainable aggregate activity fact (V13-06).

        ``active`` = recent keyboard/mouse input (below the threshold);
        ``idle`` = no input for a while; ``unknown`` = the system idle
        provider is unavailable (never guessed as rest).  Contains no
        information about WHAT was typed or which window was focused.
        """
        idle = self._monitor.idle_seconds()
        if idle is None:
            return "unknown"
        return "active" if idle <= ACTIVE_THRESHOLD_S else "idle"

    def link_enabled(self) -> bool:
        return bool(self._cfg("activity_link_enabled", True))

    def tick(self) -> None:
        # V13-06: the user may switch the activity linking off entirely;
        # disabled means this controller does literally nothing (no
        # timers, no subscriptions - it only ever ran inside the 1 Hz
        # service tick anyway, which continues for other services)
        if not self.link_enabled():
            return
        idle = self._monitor.idle_seconds()
        now_s = self._now_s()
        if idle is None:
            return  # unknown idle -> assume active, never force rest

        active = idle <= ACTIVE_THRESHOLD_S
        current = self._controller.current_action()

        if active:
            self._on_active(now_s, current)
        else:
            self._on_idle(now_s, idle, current)

    def _on_active(self, now_s: float, current: ActionId | None) -> None:
        self._idle_since = None
        if current is not ActionId.REST:
            self._resume_at = None
        if self._active_since is None:
            self._active_since = now_s
            self._work_session_start = now_s
            self._reminded_this_session = False

        # Leaving REST after resumed input (design 8.5: delay a few seconds).
        if current is ActionId.REST and self._resume_at is None:
            self._resume_at = now_s + RESUME_DELAY_S
        if current is ActionId.REST and self._resume_at is not None and now_s >= self._resume_at:
            # The premise of REST (user idle) is gone; end it explicitly since
            # IDLE cannot out-prioritize REST (both handled by rhythm).
            self._controller.end_if_action(ActionId.REST, "input_resumed")
            self._mark_switch(now_s)

        # Sustained activity -> WORK.
        active_for = now_s - self._active_since
        if (
            current in (None, ActionId.IDLE)
            and active_for >= ACTIVE_STABLE_S
            and self._rhythm_switch_allowed(now_s)
        ):
            if self._controller.request(ActionId.WORK, "rhythm"):
                self._mark_switch(now_s)

        self._maybe_remind_work(now_s, current)

    def _on_idle(self, now_s: float, idle_s: float, current: ActionId | None) -> None:
        self._active_since = None
        if self._idle_since is None:
            self._idle_since = now_s
        # A long idle stretch ends the current work session bookkeeping.
        if idle_s >= WORK_SESSION_RESET_IDLE_S:
            self._work_session_start = None
            self._reminded_this_session = False

        threshold = float(self._cfg("work_idle_threshold_s", 120))
        idle_long_enough = idle_s >= threshold or (
            self._idle_since is not None and now_s - self._idle_since >= threshold
        )
        if idle_long_enough and current in (None, ActionId.IDLE, ActionId.WORK):
            if self._rhythm_switch_allowed(now_s):
                if current is ActionId.WORK:
                    # The premise of WORK (user activity) is gone: end it
                    # explicitly, REST cannot out-prioritize WORK (equal 50).
                    self._controller.end_if_action(ActionId.WORK, "work_invalidated")
                if self._controller.request(ActionId.REST, "rhythm"):
                    self._mark_switch(now_s)

    def _maybe_remind_work(self, now_s: float, current: ActionId | None) -> None:
        """Gentle rest reminder after long continuous activity (design 8.4)."""
        if current is not ActionId.WORK or self._work_session_start is None:
            return
        if self._reminded_this_session:
            return
        remind_minutes = float(self._cfg("rest_reminder_minutes", 50))
        worked_minutes = (now_s - self._work_session_start) / 60.0
        if worked_minutes >= remind_minutes:
            self._reminded_this_session = True
            minutes = int(worked_minutes)
            self._bubble(f"已经工作 {minutes} 分钟啦，起来走走吧～")
            logger.info("rest reminder after %d minutes of continuous work", minutes)
