"""Overlay layer: blink cadence, gaze clamping, bubble/effect expiry."""

from __future__ import annotations

import random

import pytest

from retirement_pet.clock import FakeClock
from retirement_pet.overlay import OverlayController
from retirement_pet.models import ActionId  # noqa: F401 (keeps import parity)


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def overlay(clock):
    return OverlayController(clock, random.Random(7))


def tick_many(overlay, clock, seconds, step_ms=100):
    for _ in range(int(seconds * 1000 / step_ms)):
        clock.advance_ms(step_ms)
        overlay.tick()


def test_blink_happens_within_window(overlay, clock):
    saw_blink = False
    for _ in range(100):  # 10s at 100ms steps
        clock.advance_ms(100)
        overlay.tick()
        if overlay.snapshot().blink:
            saw_blink = True
    assert saw_blink


def test_blink_is_brief(overlay, clock):
    overlay.tick()
    now = clock.monotonic_ms()
    overlay.force_blink(now)
    assert overlay.snapshot().blink
    clock.advance_ms(121)
    overlay.tick()
    assert not overlay.snapshot().blink


def test_gaze_clamped_to_unit_circle(overlay):
    overlay.set_gaze_target(5.0, 0.0)
    snap = overlay.snapshot()
    assert abs(snap.gaze_x) <= 1.0 and abs(snap.gaze_y) <= 1.0

    overlay.set_gaze_target(0.3, 0.4)
    snap = overlay.snapshot()
    assert abs(snap.gaze_x - 0.3) < 1e-9 and abs(snap.gaze_y - 0.4) < 1e-9


def test_bubble_expires(overlay, clock):
    overlay.tick()
    overlay.show_bubble("干饭啦", duration_ms=2000)
    assert overlay.snapshot().bubble_text == "干饭啦"
    clock.advance_ms(2100)
    overlay.tick()
    assert overlay.snapshot().bubble_text is None


def test_effect_expires(overlay, clock):
    overlay.tick()
    overlay.show_effect("zzz", duration_ms=1500)
    assert "zzz" in overlay.snapshot().effects
    clock.advance_ms(1600)
    overlay.tick()
    assert "zzz" not in overlay.snapshot().effects


def test_multiple_effects_coexist(overlay, clock):
    overlay.tick()
    overlay.show_effect("zzz", 3000)
    overlay.show_effect("notes", 1000)
    snap = overlay.snapshot()
    assert set(snap.effects) == {"zzz", "notes"}
    clock.advance_ms(1100)
    overlay.tick()
    snap = overlay.snapshot()
    assert snap.effects == ("zzz",)
