"""Meal / exercise schedules and meeting windows (design 8.6-8.8, ADR-005).

All times are local "HH:MM" strings from settings.  Reminder bookkeeping is
per-day and persisted in ``state.json`` so restarts, hibernation and crossing
midnight never re-trigger the same slot.  Snoozing is user-requested and
capped; auto-reminders fire at most once per slot per day.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from retirement_pet.action_controller import ActionController
from retirement_pet.clock import Clock
from retirement_pet.models import ActionId
from retirement_pet.overlay import OverlayController
from retirement_pet.state_store import StateStore

logger = logging.getLogger(__name__)

REMINDER_WINDOW_MIN = 30     # how long after the slot time we may auto-remind
MEAL_SNOOZE_LIMIT = 1
EXERCISE_SNOOZE_LIMIT = 2
SNOOZE_DELAY_MIN = 10

MEAL_LABELS = ("早餐", "午餐", "晚餐")


def _parse_hhmm(text: str) -> tuple[int, int] | None:
    try:
        hh, mm = str(text).strip().split(":")
        hh, mm = int(hh), int(mm)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
    except (ValueError, AttributeError):
        pass
    return None


def _today_at(now: datetime, hh: int, mm: int) -> datetime:
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0)


class ScheduleManager:
    def __init__(
        self,
        controller: ActionController,
        clock: Clock,
        settings_provider: Callable[[], dict],
        state: StateStore,
        overlay: OverlayController | None = None,
    ):
        self._controller = controller
        self._clock = clock
        self._settings = settings_provider
        self._state = state
        self._overlay = overlay
        self._manual_meeting = False
        self._meeting_from_schedule = False
        self._last_day = None

    # -- config helpers ------------------------------------------------------

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            return self._settings().get(key, default)
        except Exception:  # noqa: BLE001
            return default

    def _times(self, key: str) -> list[tuple[int, int, str]]:
        result = []
        raw = self._cfg(key, [])
        if isinstance(raw, list):
            for item in raw:
                parsed = _parse_hhmm(item)
                if parsed:
                    result.append((parsed[0], parsed[1], str(item)))
        return result

    def meeting_windows(self) -> list[tuple[int, int, int, int]]:
        """Configured daily meeting windows as (sh, sm, eh, em)."""
        result = []
        raw = self._cfg("meeting_windows", [])
        if not isinstance(raw, list):
            return result
        for item in raw:
            if not isinstance(item, dict):
                continue
            start = _parse_hhmm(item.get("start", ""))
            end = _parse_hhmm(item.get("end", ""))
            if start and end:
                result.append((start[0], start[1], end[0], end[1]))
        return result

    # -- main tick -------------------------------------------------------------

    def tick(self) -> None:
        now = self._clock.now()
        day = self._state.day_key(now)
        if day != self._last_day:
            self._state.prune_old_reminder_days(now)
            self._last_day = day
        # Meeting windows first: their priority (90) must own the pet before
        # lower-priority reminders (meal 75) fire within the same tick.
        self._check_meeting_windows(now)
        self._check_kind(now, "meal", "meal_times", ActionId.EAT,
                         MEAL_SNOOZE_LIMIT, MEAL_LABELS)
        self._check_kind(now, "exercise", "exercise_times", ActionId.EXERCISE,
                         EXERCISE_SNOOZE_LIMIT, None)

    def _check_kind(
        self,
        now: datetime,
        kind: str,
        cfg_key: str,
        action_id: ActionId,
        snooze_limit: int,
        labels: tuple[str, ...] | None,
    ) -> None:
        day_entry = self._state.reminder_entry(kind, now)
        dirty = False
        for index, (hh, mm, raw) in enumerate(self._times(cfg_key)):
            slot = day_entry.setdefault(str(index), {})
            slot_time = _today_at(now, hh, mm)
            window_end = slot_time + timedelta(minutes=REMINDER_WINDOW_MIN)

            in_window = slot_time <= now <= window_end
            if in_window and not slot.get("reminded"):
                slot["reminded"] = True
                self._auto_remind(kind, index, action_id, labels)
                dirty = True

            snooze_at = slot.get("snooze_at")
            if (
                snooze_at
                and not slot.get("completed")
                and now >= datetime.fromisoformat(snooze_at)
            ):
                if slot.get("snooze_count", 0) < snooze_limit:
                    slot["snooze_count"] = slot.get("snooze_count", 0) + 1
                    slot.pop("snooze_at", None)
                    self._auto_remind(kind, index, action_id, labels)
                else:
                    slot.pop("snooze_at", None)  # snooze budget exhausted
                dirty = True
        if dirty:
            self._state.save()

    def _slot_label(self, kind: str, index: int, labels) -> str:
        if labels and 0 <= index < len(labels):
            return labels[index]
        if labels:
            return labels[min(index, len(labels) - 1)]
        return "健身"

    def _auto_remind(self, kind: str, index: int, action_id: ActionId, labels) -> None:
        label = self._slot_label(kind, index, labels)
        accepted = self._controller.request(action_id, "schedule")
        if self._overlay is not None:
            verb = "干饭时间到" if kind == "meal" else "该动一动啦"
            self._overlay.show_bubble(f"{label} · {verb}！", 5000)
        logger.info("%s slot %d reminded (accepted=%s)", kind, index, accepted)

    # -- user commands (menus) ---------------------------------------------------

    def _pending_slot(self, kind: str, now: datetime) -> tuple[dict, int] | None:
        """The slot to act on: reminded-but-not-completed, else current/next."""
        day_entry = self._state.reminder_entry(kind, now)
        times = self._times("meal_times" if kind == "meal" else "exercise_times")
        if not times:
            return None
        pending = None
        current_or_next = None
        for index, (hh, mm, _raw) in enumerate(times):
            slot = day_entry.setdefault(str(index), {})
            if slot.get("reminded") and not slot.get("completed"):
                pending = (slot, index)
                break
            if current_or_next is None and _today_at(now, hh, mm) <= now:
                current_or_next = (slot, index)
        return pending or current_or_next

    def mark_started(self, kind: str) -> bool:
        """User confirmed 'start now': force the action and record completion."""
        now = self._clock.now()
        found = self._pending_slot(kind, now)
        action_id = ActionId.EAT if kind == "meal" else ActionId.EXERCISE
        forced = self._controller.request(action_id, "menu", force=True)
        if found:
            slot, index = found
            slot["reminded"] = True
            slot["completed"] = True
            slot.pop("snooze_at", None)
            self._state.save()
            logger.info("%s slot %d started by user", kind, index)
        return forced

    def snooze(self, kind: str) -> bool:
        now = self._clock.now()
        found = self._pending_slot(kind, now)
        if not found:
            return False
        slot, _index = found
        limit = MEAL_SNOOZE_LIMIT if kind == "meal" else EXERCISE_SNOOZE_LIMIT
        if slot.get("snooze_count", 0) >= limit:
            return False  # snooze budget exhausted (design 8.7)
        slot["reminded"] = True
        slot["snooze_at"] = (now + timedelta(minutes=SNOOZE_DELAY_MIN)).isoformat()
        self._state.save()
        if self._overlay is not None:
            self._overlay.show_bubble("好，稍后再提醒你～", 3000)
        return True

    def skip_today(self, kind: str) -> bool:
        now = self._clock.now()
        found = self._pending_slot(kind, now)
        if not found:
            return False
        slot, _index = found
        slot["reminded"] = True
        slot["completed"] = True  # user chose to skip: no more prompts today
        slot.pop("snooze_at", None)
        self._state.save()
        return True

    # -- meeting mode ----------------------------------------------------------

    def set_manual_meeting(self, enabled: bool) -> None:
        if enabled:
            self._manual_meeting = True
            self._controller.request(ActionId.MEETING, "menu", force=True)
        else:
            self._manual_meeting = False
            if self._controller.current_action() is ActionId.MEETING:
                self._controller.end_current("manual_meeting_off")

    @property
    def manual_meeting(self) -> bool:
        return self._manual_meeting

    def is_meeting(self) -> bool:
        if self._manual_meeting:
            return True
        now = self._clock.now()
        return self._in_meeting_window(now)

    def _in_meeting_window(self, now: datetime) -> bool:
        for sh, sm, eh, em in self.meeting_windows():
            start = _today_at(now, sh, sm)
            end = _today_at(now, eh, em)
            if start <= end and start <= now < end:
                return True
        return False

    def _check_meeting_windows(self, now: datetime) -> None:
        if self._manual_meeting:
            return
        in_window = self._in_meeting_window(now)
        current = self._controller.current_action()
        if in_window and current is not ActionId.MEETING:
            if self._controller.request(ActionId.MEETING, "schedule"):
                self._meeting_from_schedule = True
        elif not in_window and current is ActionId.MEETING and self._meeting_from_schedule:
            self._controller.end_current("meeting_window_over")
            self._meeting_from_schedule = False
