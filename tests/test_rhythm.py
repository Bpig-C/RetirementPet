"""Work/rest rhythm derived from injectable idle time (design 8.4/8.5)."""

from __future__ import annotations

import random

import pytest

from retirement_pet.action_controller import ActionController
from retirement_pet.action_registry import ActionRegistry
from retirement_pet.activity_monitor import ActivityMonitor
from retirement_pet.clock import FakeClock
from retirement_pet.models import ActionId
from retirement_pet.overlay import OverlayController
from retirement_pet.rhythm import RhythmController


class FakeIdle:
    def __init__(self):
        self.idle = 0.0

    def __call__(self):
        return self.idle


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def idle():
    return FakeIdle()


@pytest.fixture()
def controller(clock):
    return ActionController(ActionRegistry(), clock, random.Random(5))


@pytest.fixture()
def overlay(clock):
    return OverlayController(clock, random.Random(3))


@pytest.fixture()
def rhythm(controller, clock, idle, overlay):
    settings = {"work_idle_threshold_s": 120, "rest_reminder_minutes": 50}
    monitor = ActivityMonitor(idle)
    rc = RhythmController(controller, clock, monitor, settings, overlay)
    return rc


def step(clock, rhythm, seconds=1.0):
    clock.advance_s(seconds)
    rhythm.tick()
    controller_tick_holder = rhythm._controller
    controller_tick_holder.tick()


def test_sustained_activity_enters_work(rhythm, controller, clock, idle):
    idle.idle = 0.5
    for _ in range(45):
        step(clock, rhythm)
    assert controller.current_action() is ActionId.WORK


def test_brief_activity_stays_idle(rhythm, controller, clock, idle):
    idle.idle = 0.5
    for _ in range(10):
        step(clock, rhythm)
    assert controller.current_action() is None


def test_idle_over_threshold_enters_rest(rhythm, controller, clock, idle):
    idle.idle = 0.5
    for _ in range(40):
        step(clock, rhythm)
    assert controller.current_action() is ActionId.WORK

    idle.idle = 200.0
    for _ in range(15):
        step(clock, rhythm)
    assert controller.current_action() is ActionId.REST


def test_unknown_idle_never_forces_rest(rhythm, controller, clock, idle):
    idle.idle = None
    for _ in range(300):
        step(clock, rhythm)
    assert controller.current_action() is None


def test_resume_from_rest_delays_then_idles(rhythm, controller, clock, idle):
    idle.idle = 300.0
    for _ in range(15):
        step(clock, rhythm)
    assert controller.current_action() is ActionId.REST

    idle.idle = 0.5
    steps = 0
    while controller.current_action() is ActionId.REST and steps < 4:
        step(clock, rhythm)
        steps += 1
    assert controller.current_action() is ActionId.REST  # still resting (delay)

    step(clock, rhythm, seconds=3.0)
    assert controller.current_action() is None  # back to idle after delay+gap


def test_rapid_activity_idle_flapping_keeps_stability(rhythm, controller, clock, idle):
    """Input flapping must not cause work/rest thrash (design 7.3)."""
    for i in range(60):
        idle.idle = 0.5 if i % 2 == 0 else 7.0  # 7s idle = not active
        step(clock, rhythm)
    assert controller.current_action() is None  # never stayed active long enough


def test_work_reminder_once_per_session(rhythm, controller, clock, idle, overlay):
    idle.idle = 0.5
    minutes = 0
    bubbles = []
    for minute in range(60):
        step(clock, rhythm, seconds=60.0)
    text = overlay.snapshot().bubble_text
    assert text is not None and "工作" in text

    overlay.clear_bubble()
    for _ in range(20):  # another 20 minutes of work
        step(clock, rhythm, seconds=60.0)
    assert overlay.snapshot().bubble_text is None  # no second nag


def test_work_reminder_resets_after_long_idle(rhythm, controller, clock, idle, overlay):
    idle.idle = 0.5
    for _ in range(55):
        step(clock, rhythm, seconds=60.0)
    assert overlay.snapshot().bubble_text is not None

    idle.idle = 300.0
    for _ in range(20):
        step(clock, rhythm)
    idle.idle = 0.5
    for _ in range(55):
        step(clock, rhythm, seconds=60.0)
    assert overlay.snapshot().bubble_text is not None  # new session reminds again
