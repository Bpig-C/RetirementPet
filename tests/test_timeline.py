"""IdleTimeline semantics (V12-01 requirement 1)."""

from __future__ import annotations

from retirement_pet.clock import FakeClock
from retirement_pet.timeline import IdleResetReason, IdleTimeline


def test_idle_advances_on_monotonic_clock():
    clock = FakeClock()
    timeline = IdleTimeline(clock)
    clock.advance_ms(0)
    assert timeline.elapsed_ms() == 0
    clock.advance_ms(1_500)
    assert timeline.elapsed_ms() == 1_500
    clock.advance_ms(700)
    assert timeline.elapsed_ms() == 2_200


def test_wall_clock_change_never_rewinds_or_jumps_animation():
    """System date adjustments are invisible: only the monotonic clock
    feeds the timeline, and a backwards monotonic sample clamps to 0."""
    clock = FakeClock()
    timeline = IdleTimeline(clock)
    clock.advance_ms(2_000)
    assert timeline.elapsed_ms() == 2_000
    # A wall-clock "adjustment" moves the datetime but not the monotonic
    # source; elapsed must stay continuous.
    clock._now = clock._now.replace(year=2000)
    assert timeline.elapsed_ms() == 2_000


def test_backwards_monotonic_sample_never_produces_negative_elapsed():
    clock = FakeClock()
    timeline = IdleTimeline(clock)
    clock.advance_ms(1_000)
    clock._ms = clock._ms - 5_000  # hybrid-sleep style monotonic regression
    assert timeline.elapsed_ms() == 0
    clock.advance_ms(4_100)  # monotonic time climbs back past the anchor
    assert timeline.elapsed_ms() == 100


def test_reset_restarts_loop_from_zero():
    clock = FakeClock()
    timeline = IdleTimeline(clock)
    clock.advance_ms(9_000)
    assert timeline.elapsed_ms() == 9_000
    timeline.reset(IdleResetReason.ACTION_END)
    assert timeline.elapsed_ms() == 0
    clock.advance_ms(120)
    assert timeline.elapsed_ms() == 120


def test_pause_freezes_and_resume_continues_without_replay():
    clock = FakeClock()
    timeline = IdleTimeline(clock)
    clock.advance_ms(400)
    timeline.pause()
    assert timeline.paused
    assert timeline.elapsed_ms() == 400
    clock.advance_ms(600_000)  # ten hidden minutes
    assert timeline.elapsed_ms() == 400, "hidden time must not be replayed"
    timeline.resume()
    assert not timeline.paused
    assert timeline.elapsed_ms() == 400
    clock.advance_ms(50)
    assert timeline.elapsed_ms() == 450


def test_pause_is_idempotent_and_resume_without_pause_is_noop():
    clock = FakeClock()
    timeline = IdleTimeline(clock)
    timeline.resume()
    clock.advance_ms(100)
    timeline.pause()
    clock.advance_ms(100)
    timeline.pause()
    assert timeline.elapsed_ms() == 100


def test_reset_while_paused_starts_fresh_and_stays_paused():
    clock = FakeClock()
    timeline = IdleTimeline(clock)
    clock.advance_ms(300)
    timeline.pause()
    timeline.reset(IdleResetReason.CHARACTER_SWITCH)
    assert timeline.paused
    assert timeline.elapsed_ms() == 0
    timeline.resume()
    clock.advance_ms(70)
    assert timeline.elapsed_ms() == 70


def test_elapsed_accepts_injected_now_for_frame_composition():
    clock = FakeClock()
    timeline = IdleTimeline(clock)
    timeline.reset(IdleResetReason.ACTION_END)
    assert timeline.elapsed_ms(250) == 250
    assert timeline.elapsed_ms(0) == 0
