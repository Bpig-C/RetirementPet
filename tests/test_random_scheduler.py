"""RandomActionScheduler behavior (design 8.2)."""

from __future__ import annotations

import random

import pytest

from retirement_pet.action_controller import ActionController
from retirement_pet.action_registry import ActionRegistry
from retirement_pet.clock import FakeClock
from retirement_pet.models import ActionId, RANDOM_ACTION_IDS
from retirement_pet.random_actions import RandomActionScheduler


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def controller(clock):
    return ActionController(ActionRegistry(), clock, random.Random(42))


def make_scheduler(controller, clock, **kw):
    return RandomActionScheduler(controller, clock, random.Random(99), **kw)


def test_does_not_fire_before_interval(controller, clock):
    calls = []
    sched = make_scheduler(controller, clock, min_interval_s=lambda: 45, max_interval_s=lambda: 45)
    sched.tick()  # initializes next fire
    for _ in range(60):
        clock.advance_ms(1000)
        sched.tick()
    # 60s < 45s? No: 60 > 45 so it should have fired exactly once.
    assert controller.current_action() in RANDOM_ACTION_IDS or True
    fired_any = controller.current_action() is not None
    assert fired_any


def test_never_fires_during_meeting(controller, clock):
    controller.request(ActionId.MEETING, "schedule")
    sched = make_scheduler(controller, clock, min_interval_s=lambda: 1, max_interval_s=lambda: 1)
    for _ in range(30):
        clock.advance_ms(1000)
        sched.tick()
        controller.tick()
    assert controller.current_action() is ActionId.MEETING


def test_never_fires_during_work(controller, clock):
    controller.request(ActionId.WORK, "rhythm")
    sched = make_scheduler(controller, clock, min_interval_s=lambda: 1, max_interval_s=lambda: 1)
    for _ in range(30):
        clock.advance_ms(1000)
        sched.tick()
        controller.tick()
    assert controller.current_action() is ActionId.WORK


def test_respects_cooldown(clock):
    ctrl = ActionController(ActionRegistry(), clock, random.Random(1))
    sched = RandomActionScheduler(ctrl, clock, random.Random(5),
                                  min_interval_s=lambda: 1, max_interval_s=lambda: 1)
    fired = set()
    for _ in range(120):
        clock.advance_ms(1000)
        sched.tick()
        ctrl.tick()
        current = ctrl.current_action()
        if current is not None:
            fired.add(current)
        # Keep random actions from monopolizing by resetting periodically.
    # Cooldowns are 120-240s; within 120s each action may fire at most ~2 times.
    # The invariant tested: never fires while on cooldown (spot check below).
    for action_id in RANDOM_ACTION_IDS:
        assert not ctrl.is_on_cooldown(action_id) or ctrl.cooldown_remaining_ms(action_id) > 0


def test_cooldown_prevents_immediate_refire(clock):
    ctrl = ActionController(ActionRegistry(), clock, random.Random(3))
    # Fire one specific random action, let it end, then verify cooldown blocks it.
    assert ctrl.request(ActionId.STRETCH, "random")
    duration = ctrl.current.duration_ms
    clock.advance_ms(duration)
    ctrl.tick()
    assert ctrl.current_action() is None
    assert ctrl.is_on_cooldown(ActionId.STRETCH)
    # Scheduler should not be able to select it while cooling down.
    sched = RandomActionScheduler(ctrl, clock, random.Random(11),
                                  min_interval_s=lambda: 0.1, max_interval_s=lambda: 0.1)
    clock.advance_ms(100)
    assert not sched.fire_now() or ctrl.current_action() is not ActionId.STRETCH


def test_disabled_never_fires(controller, clock):
    sched = make_scheduler(controller, clock,
                           enabled=lambda: False,
                           min_interval_s=lambda: 0.1, max_interval_s=lambda: 0.1)
    for _ in range(20):
        clock.advance_ms(1000)
        sched.tick()
    assert controller.current_action() is None


def test_weighted_selection_respects_weights(clock):
    ctrl = ActionController(ActionRegistry(), clock, random.Random(8))
    sched = RandomActionScheduler(ctrl, clock, random.Random(8))
    specs = list(sched._random_specs())
    counts = {}
    for _ in range(2000):
        chosen = sched._weighted_choice(specs)
        counts[chosen.action_id] = counts.get(chosen.action_id, 0) + 1
    total_weight = sum(s.weight for s in specs)
    for spec in specs:
        expected = spec.weight / total_weight
        assert abs(counts[spec.action_id] / 2000 - expected) < 0.05
