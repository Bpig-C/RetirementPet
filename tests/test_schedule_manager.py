"""Meal/exercise/meeting schedules: dedup per day, snooze caps, restart safety."""

from __future__ import annotations

import random
from datetime import datetime

import pytest

from retirement_pet.action_controller import ActionController
from retirement_pet.action_registry import ActionRegistry
from retirement_pet.clock import FakeClock
from retirement_pet.models import ActionId
from retirement_pet.overlay import OverlayController
from retirement_pet.schedule_manager import ScheduleManager
from retirement_pet.state_store import StateStore


@pytest.fixture()
def clock():
    return FakeClock(datetime(2026, 8, 23, 7, 55, 0))


@pytest.fixture()
def controller(clock):
    return ActionController(ActionRegistry(), clock, random.Random(2))


@pytest.fixture()
def state(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.load()
    return store


@pytest.fixture()
def overlay(clock):
    return OverlayController(clock, random.Random(4))


def make_manager(controller, clock, state, overlay, settings=None):
    settings = settings or {
        "meal_times": ["08:00", "12:00", "18:30"],
        "exercise_times": ["16:00"],
        "meeting_windows": [],
    }
    return ScheduleManager(controller, clock, lambda: settings, state, overlay)


def test_meal_fires_once_within_window(controller, clock, state, overlay):
    mgr = make_manager(controller, clock, state, overlay)
    mgr.tick()
    assert controller.current_action() is None  # 07:55, not yet

    clock.advance_s(5 * 60)  # 08:00
    mgr.tick()
    assert controller.current_action() is ActionId.EAT

    # Natural end of EAT, then repeated ticks must NOT re-trigger.
    runtime = controller.current
    clock.advance_ms(runtime.duration_ms + 10)
    controller.tick()
    assert controller.current_action() is None
    for _ in range(10):
        mgr.tick()
        clock.advance_s(60)
    assert controller.current_action() is None


def test_no_retrigger_across_midnight(controller, clock, state, overlay):
    mgr = make_manager(controller, clock, state, overlay)
    clock.advance_s(5 * 60)  # 08:00 day 1
    mgr.tick()
    assert controller.current_action() is ActionId.EAT

    # Jump to next day 08:00 - state persisted across "restart".
    clock._now = clock.now().replace(day=24)
    clock._ms += 10_000
    controller.reset()
    mgr2 = make_manager(controller, clock, state, overlay)
    mgr2.tick()
    assert controller.current_action() is ActionId.EAT  # new day, new reminder


def test_restart_same_day_does_not_repeat(controller, clock, state, overlay):
    mgr = make_manager(controller, clock, state, overlay)
    clock.advance_s(5 * 60 + 30)  # 08:00:30
    mgr.tick()
    assert controller.current_action() is ActionId.EAT
    runtime = controller.current
    clock.advance_ms(runtime.duration_ms + 10)
    controller.tick()

    # Simulate app restart: fresh manager, same state file, still in window.
    controller.reset()
    mgr2 = make_manager(controller, clock, state, overlay)
    mgr2.tick()
    assert controller.current_action() is None  # already reminded today


def test_snooze_meal_once(controller, clock, state, overlay):
    mgr = make_manager(controller, clock, state, overlay)
    clock.advance_s(5 * 60)  # 08:00
    mgr.tick()
    runtime = controller.current
    clock.advance_ms(runtime.duration_ms + 10)
    controller.tick()

    assert mgr.snooze("meal")
    clock.advance_s(10 * 60 + 5)  # snooze delay over
    mgr.tick()
    assert controller.current_action() is ActionId.EAT

    # Second snooze on the same slot exceeds the meal cap.
    runtime = controller.current
    clock.advance_ms(runtime.duration_ms + 10)
    controller.tick()
    assert not mgr.snooze("meal")


def test_exercise_snooze_allows_two(tmp_path):
    clock2 = FakeClock(datetime(2026, 8, 23, 15, 55, 0))
    ctrl2 = ActionController(ActionRegistry(), clock2, random.Random(1))
    state2 = StateStore(tmp_path / "state.json")
    state2.load()
    ov2 = OverlayController(clock2, random.Random(1))
    mgr = make_manager(ctrl2, clock2, state2, ov2)

    clock2.advance_s(5 * 60)  # 16:00
    mgr.tick()
    assert ctrl2.current_action() is ActionId.EXERCISE
    rt = ctrl2.current
    clock2.advance_ms(rt.duration_ms + 10)
    ctrl2.tick()

    assert mgr.snooze("exercise")
    clock2.advance_s(10 * 60 + 5)
    mgr.tick()
    rt = ctrl2.current
    clock2.advance_ms(rt.duration_ms + 10)
    ctrl2.tick()

    assert mgr.snooze("exercise")  # second snooze allowed
    clock2.advance_s(10 * 60 + 5)
    mgr.tick()
    rt = ctrl2.current
    clock2.advance_ms(rt.duration_ms + 10)
    ctrl2.tick()

    assert not mgr.snooze("exercise")  # cap reached


def test_skip_today_silences_slot(controller, clock, state, overlay):
    mgr = make_manager(controller, clock, state, overlay)
    clock.advance_s(5 * 60)  # 08:00
    mgr.tick()
    assert mgr.skip_today("meal")
    runtime = controller.current
    clock.advance_ms(runtime.duration_ms + 10)
    controller.tick()
    for _ in range(5):
        mgr.tick()
        clock.advance_s(60)
    assert controller.current_action() is None


def test_manual_start_meal_forces_and_records(controller, clock, state, overlay):
    mgr = make_manager(controller, clock, state, overlay)
    clock.advance_s(5 * 60)
    mgr.tick()
    runtime = controller.current
    clock.advance_ms(runtime.duration_ms + 10)
    controller.tick()
    assert mgr.mark_started("meal")  # force restarts EAT
    assert controller.current_action() is ActionId.EAT
    # After completed, ticking never re-reminds this slot today.
    rt = controller.current
    clock.advance_ms(rt.duration_ms + 10)
    controller.tick()
    for _ in range(5):
        mgr.tick()
    assert controller.current_action() is None


def test_meeting_window_enter_and_exit(controller, clock, state, overlay):
    settings = {
        "meal_times": [],
        "exercise_times": [],
        "meeting_windows": [{"start": "09:00", "end": "10:00"}],
    }
    mgr = make_manager(controller, clock, state, overlay, settings)
    clock.advance_s(65 * 60)  # 09:00
    mgr.tick()
    assert controller.current_action() is ActionId.MEETING
    assert mgr.is_meeting()

    clock.advance_s(61 * 60)  # 10:01, window over
    mgr.tick()
    assert controller.current_action() is None
    assert not mgr.is_meeting()


def test_random_cannot_override_meeting_window(controller, clock, state, overlay):
    settings = {
        "meal_times": [],
        "exercise_times": [],
        "meeting_windows": [{"start": "09:00", "end": "10:00"}],
    }
    from retirement_pet.random_actions import RandomActionScheduler

    mgr = make_manager(controller, clock, state, overlay, settings)
    clock.advance_s(65 * 60)
    mgr.tick()
    assert controller.current_action() is ActionId.MEETING

    sched = RandomActionScheduler(controller, clock, random.Random(6),
                                  min_interval_s=lambda: 0.01,
                                  max_interval_s=lambda: 0.01)
    for _ in range(20):
        clock.advance_s(1)
        sched.tick()
        mgr.tick()
        controller.tick()
    assert controller.current_action() is ActionId.MEETING


def test_manual_meeting_toggle(controller, clock, state, overlay):
    mgr = make_manager(controller, clock, state, overlay)
    mgr.set_manual_meeting(True)
    assert controller.current_action() is ActionId.MEETING
    assert mgr.is_meeting()

    # Schedule tick must not end a manual meeting.
    mgr.tick()
    assert controller.current_action() is ActionId.MEETING

    mgr.set_manual_meeting(False)
    assert controller.current_action() is None
    assert not mgr.is_meeting()


def test_meeting_blocks_meal_reminder(controller, clock, state, overlay):
    settings = {
        "meal_times": ["08:00"],
        "exercise_times": [],
        "meeting_windows": [{"start": "07:00", "end": "09:00"}],
    }
    mgr = make_manager(controller, clock, state, overlay, settings)
    clock.advance_s(5 * 60)  # 08:00 inside meeting
    mgr.tick()
    assert controller.current_action() is ActionId.MEETING  # EAT rejected (75<90)
    entry = state.reminder_entry("meal", clock.now())
    assert entry["0"]["reminded"] is True  # but the day's reminder was consumed
