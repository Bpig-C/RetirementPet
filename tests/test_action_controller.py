"""Deterministic state-machine tests for ActionController (design 7.2/7.3)."""

from __future__ import annotations

import random
from datetime import timedelta

import pytest

from retirement_pet.action_controller import ActionController
from retirement_pet.action_registry import ActionRegistry, DEFAULT_SPECS
from retirement_pet.clock import FakeClock
from retirement_pet.models import ActionId


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def controller(clock):
    rng = random.Random(1234)
    return ActionController(ActionRegistry(), clock, rng)


def step(ctrl, clock, ms=1000):
    clock.advance_ms(ms)
    ctrl.tick()


def test_first_request_always_accepted(controller, clock):
    assert controller.request(ActionId.WORK, "test")
    assert controller.current_action() is ActionId.WORK


def test_higher_priority_interrupts_lower(controller, clock):
    controller.request(ActionId.WORK, "test")  # 50, interruptible
    assert controller.request(ActionId.MEETING, "test")  # 90 > 50
    assert controller.current_action() is ActionId.MEETING


def test_lower_priority_cannot_interrupt(controller):
    controller.request(ActionId.MEETING, "test")  # 90
    assert not controller.request(ActionId.WORK, "test")  # 50
    assert not controller.request(ActionId.STRETCH, "random")  # 20
    assert controller.current_action() is ActionId.MEETING


def test_meeting_blocks_meal_and_random(controller):
    """Random and scheduled actions must never override a meeting."""
    controller.request(ActionId.MEETING, "schedule")
    assert not controller.request(ActionId.EAT, "schedule")  # 75 < 90
    assert not controller.request(ActionId.YAWN, "random")  # 20 < 90
    assert controller.current_action() is ActionId.MEETING


def test_non_interruptible_rejects_higher_priority(controller, clock):
    controller.request(ActionId.EAT, "schedule")  # 75, interruptible=False
    clock.advance_ms(100)  # still within min duration anyway
    assert not controller.request(ActionId.MEETING, "test")  # 90 > 75 but locked
    assert controller.current_action() is ActionId.EAT


def test_force_overrides_everything(controller):
    controller.request(ActionId.MEETING, "schedule")
    assert controller.request(ActionId.EXERCISE, "menu", force=True)
    assert controller.current_action() is ActionId.EXERCISE


def test_natural_end_within_duration_bounds(clock):
    rng = random.Random(7)
    ctrl = ActionController(ActionRegistry(), clock, rng)
    ctrl.request(ActionId.EAT, "schedule")
    spec = DEFAULT_SPECS[ActionId.EAT]
    duration = ctrl.current.duration_ms
    assert spec.min_duration_ms <= duration <= spec.max_duration_ms

    clock.advance_ms(duration - 100)
    ctrl.tick()
    assert ctrl.current_action() is ActionId.EAT

    clock.advance_ms(200)  # past duration
    ctrl.tick()
    assert ctrl.current_action() is None  # back to idle


def test_loop_action_never_naturally_ends(controller, clock):
    controller.request(ActionId.WORK, "test")
    clock.advance_ms(3_600_000)  # one hour
    controller.tick()
    assert controller.current_action() is ActionId.WORK


def test_min_duration_honored_via_chosen_duration(controller, clock):
    for seed in range(20):
        rng = random.Random(seed)
        ctrl = ActionController(ActionRegistry(), clock, rng)
        ctrl.request(ActionId.STRETCH, "random")
        assert ctrl.current.duration_ms >= DEFAULT_SPECS[ActionId.STRETCH].min_duration_ms


def test_cooldown_after_natural_end(controller, clock):
    controller.request(ActionId.STRETCH, "random")
    clock.advance_ms(controller.current.duration_ms)
    controller.tick()
    assert controller.current_action() is None
    assert controller.is_on_cooldown(ActionId.STRETCH)
    assert controller.cooldown_remaining_ms(ActionId.STRETCH) > 0

    clock.advance_ms(DEFAULT_SPECS[ActionId.STRETCH].cooldown_ms + 10)
    assert not controller.is_on_cooldown(ActionId.STRETCH)


def test_expired_request_dropped(controller, clock):
    from datetime import datetime

    stale = clock.now() - timedelta(seconds=30)
    assert not controller.request(ActionId.WORK, "schedule", requested_at=stale)
    assert controller.current_action() is None


def test_same_action_request_is_idempotent(controller):
    assert controller.request(ActionId.WORK, "a")
    assert controller.request(ActionId.WORK, "b")  # no restart
    assert controller.current.payload == {}


def test_end_if_action_only_ends_matching(controller):
    controller.request(ActionId.WORK, "test")
    assert not controller.end_if_action(ActionId.MEETING)
    assert controller.current_action() is ActionId.WORK
    assert controller.end_if_action(ActionId.WORK)
    assert controller.current_action() is None


def test_change_events_fire_on_start_replace_end(controller, clock):
    events = []
    controller.on_change(events.append)

    controller.request(ActionId.WORK, "test")
    controller.request(ActionId.MEETING, "schedule")
    clock.advance_ms(10)
    controller.end_current("meeting_over")

    assert [e.reason for e in events] == ["request", "request", "meeting_over"]
    assert [e.action_id for e in events] == [ActionId.WORK, ActionId.MEETING, None]
    assert events[1].previous.spec.action_id is ActionId.WORK


def test_frame_index_advances(controller, clock):
    controller.request(ActionId.WORK, "test")
    clock.advance_ms(1000)
    controller.tick()
    assert controller.current.frame_index >= 15  # ~16 fps


def test_duplicate_random_rejected_while_active(controller, clock):
    assert controller.request(ActionId.LICK_PAW, "random")
    assert controller.request(ActionId.LICK_PAW, "random")  # idempotent True
    clock.advance_ms(1)
    controller.tick()
    assert controller.current_action() is ActionId.LICK_PAW


def test_unknown_action_rejected(controller):
    from retirement_pet.models import ActionId as A

    assert not controller.request(A.BLINK, "test")  # blink not a main action


# -- CR-C06: a global request gate implements app-level "disabled" mode ------


def test_request_gate_rejects_every_source(controller):
    controller.set_request_gate(
        lambda action_id, source, force: action_id is not ActionId.STRETCH)

    assert controller.request(ActionId.STRETCH, "random") is False
    assert controller.request(ActionId.STRETCH, "resolve:context", force=True) \
        is False
    assert controller.current_action() is None

    # other semantics pass, from any source, gated args stay intact
    assert controller.request(ActionId.WORK, "resolve:context", force=True)
    assert controller.current_action() is ActionId.WORK
    assert controller.request(ActionId.STRETCH, "random", force=True) is False
    assert controller.current_action() is ActionId.WORK


def test_request_gate_can_be_cleared(controller):
    controller.set_request_gate(lambda action_id, source, force: False)
    assert controller.request(ActionId.WORK, "test") is False
    controller.set_request_gate(None)
    assert controller.request(ActionId.WORK, "test") is True


def test_gate_does_not_block_ending(controller):
    controller.request(ActionId.WORK, "test")
    controller.set_request_gate(lambda action_id, source, force: False)
    assert controller.end_current("gate-test") is True
    assert controller.current_action() is None


# -- C06-R2: loop=False plays ONE material pass, timed by the caller ---------


def test_single_pass_material_duration(controller):
    # the caller measures the ACTIVE material and supplies its full-pass
    # duration; the engine does not guess timing from frame counts
    assert controller.request(
        ActionId.WORK, "panel", force=True,
        payload={"loop": False, "single_pass_ms": 1200})
    assert controller.current.duration_ms == 1200


def test_single_pass_without_material_falls_back_to_minimum(controller):
    assert controller.request(
        ActionId.WORK, "panel", force=True, payload={"loop": False})
    assert controller.current.duration_ms == 10_000


def test_single_pass_rejects_non_positive_material_duration(controller):
    assert controller.request(
        ActionId.WORK, "panel", force=True,
        payload={"loop": False, "single_pass_ms": 0})
    assert controller.current.duration_ms == 10_000
