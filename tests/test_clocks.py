"""VisualClock / ServiceClock (M1): split clocks with load discipline."""

from __future__ import annotations

import pytest

from retirement_pet.clock import FakeClock
from retirement_pet.clocks import ServiceClock, VisualClock


@pytest.fixture()
def clock():
    return FakeClock()


# -- VisualClock ---------------------------------------------------------------


def test_visual_clock_never_ticks_while_hidden(clock, qt_application):
    visual = VisualClock(fps=16, clock=clock)
    ticks = []
    visual.tick.connect(ticks.append)
    assert not visual.running  # starts hidden

    clock.advance_ms(5000)
    visual.pump()
    assert ticks == []  # hidden: zero visual ticks (PERFORMANCE_BUDGET 7)

    visual.set_visible(True)
    assert visual.running
    clock.advance_ms(62)
    visual.pump()
    assert len(ticks) == 1


def test_visual_clock_stops_on_suspend(clock, qt_application):
    visual = VisualClock(fps=16, clock=clock)
    visual.set_visible(True)
    ticks = []
    visual.tick.connect(ticks.append)

    visual.set_suspended(True)  # lock screen / session disconnect / sleep
    assert not visual.running
    clock.advance_ms(3000)
    visual.pump()
    assert ticks == []

    visual.set_suspended(False)
    assert visual.running
    clock.advance_ms(62)
    visual.pump()
    assert len(ticks) == 1


def test_visual_clock_pump_ignores_manual_calls_when_hidden(clock, qt_application):
    visual = VisualClock(fps=16, clock=clock)
    ticks = []
    visual.tick.connect(ticks.append)
    visual.pump()  # even a manual pump must not compose hidden frames
    assert ticks == []


def test_visual_clock_fps_interval(clock, qt_application):
    visual = VisualClock(fps=16, clock=clock)
    assert visual.interval_ms() == 62
    visual.set_fps(30)
    assert visual.interval_ms() == 33


# -- ServiceClock --------------------------------------------------------------


def test_service_clock_runs_regardless_of_visibility(clock, qt_application):
    """Facts (schedules, countdown) keep advancing while hidden."""
    service = ServiceClock(clock=clock)
    runs = []
    service.register(1000, lambda now: runs.append(now), run_immediately=True)
    service.start()
    for _ in range(5):
        clock.advance_ms(1000)
        service.pump()
    assert len(runs) == 5


def test_service_clock_subscriber_cadence_and_isolation(clock, qt_application):
    service = ServiceClock(clock=clock)
    slow, fast = [], []
    service.register(1000, slow.append, run_immediately=False)
    service.register(100, fast.append, run_immediately=False)
    for _ in range(30):
        clock.advance_ms(100)
        service.pump()
    assert len(slow) == 3  # 1s cadence over 3s
    assert len(fast) == 30


def test_service_clock_survives_failing_subscriber(clock, qt_application):
    service = ServiceClock(clock=clock)
    good = []
    service.register(100, lambda now: (_ for _ in ()).throw(RuntimeError("boom")))
    service.register(100, good.append)
    for _ in range(3):
        clock.advance_ms(100)
        service.pump()
    assert len(good) == 3


def test_service_clock_unsubscribe(clock, qt_application):
    service = ServiceClock(clock=clock)
    runs = []
    unsub = service.register(100, runs.append, run_immediately=True)
    service.pump()
    unsub()
    clock.advance_ms(500)
    service.pump()
    assert len(runs) == 1
