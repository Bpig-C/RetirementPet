"""Countdown correctness: fixed target minus current time, never decrements."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from retirement_pet.countdown import (
    DEFAULT_TARGET,
    compute_countdown,
    parse_target,
    stage_for,
)
from retirement_pet.models import LifeStage


def test_before_target_counts_down():
    now = DEFAULT_TARGET - timedelta(days=1234, seconds=3661)
    snap = compute_countdown(now, DEFAULT_TARGET)
    assert (snap.days, snap.hours, snap.minutes, snap.seconds) == (1234, 1, 1, 1)
    assert not snap.is_past
    assert snap.total_seconds == 1234 * 86400 + 3661


def test_exactly_at_target_is_retired():
    snap = compute_countdown(DEFAULT_TARGET, DEFAULT_TARGET)
    assert snap.is_past
    assert snap.stage is LifeStage.RETIRED
    assert snap.days == 0 and snap.hours == 0


def test_after_target_clamps_to_zero():
    snap = compute_countdown(DEFAULT_TARGET + timedelta(days=3), DEFAULT_TARGET)
    assert snap.days == 0
    assert snap.total_seconds == 0
    assert snap.stage is LifeStage.RETIRED


def test_stage_boundaries():
    assert stage_for(DEFAULT_TARGET - timedelta(seconds=1), DEFAULT_TARGET) is LifeStage.OLD
    assert stage_for(datetime(2030, 7, 7, 21, 31, 59), DEFAULT_TARGET) is LifeStage.YOUNG
    assert stage_for(datetime(2030, 7, 7, 21, 32, 0), DEFAULT_TARGET) is LifeStage.MIDDLE
    assert stage_for(datetime(2050, 7, 7, 21, 32, 0), DEFAULT_TARGET) is LifeStage.OLD
    assert stage_for(datetime(2026, 1, 1), DEFAULT_TARGET) is LifeStage.YOUNG


def test_stage_derives_from_custom_target():
    target = datetime(2040, 3, 1, 10, 0, 0)
    assert stage_for(datetime(2010, 3, 1, 9, 59), target) is LifeStage.YOUNG
    assert stage_for(datetime(2010, 3, 1, 10, 0), target) is LifeStage.MIDDLE
    assert stage_for(datetime(2030, 3, 1, 10, 0), target) is LifeStage.OLD
    assert stage_for(datetime(2040, 3, 1, 10, 0), target) is LifeStage.RETIRED


def test_large_time_jump_stays_accurate():
    """Simulates sleep/hibernation: wall time jumps, no drift allowed."""
    before = compute_countdown(datetime(2026, 8, 23, 12, 0, 0), DEFAULT_TARGET)
    after = compute_countdown(datetime(2027, 8, 23, 12, 0, 0), DEFAULT_TARGET)
    assert before.total_seconds - after.total_seconds == 365 * 86400


def test_parse_target_accepts_iso():
    assert parse_target("2060-07-07T21:32:00") == DEFAULT_TARGET


def test_parse_target_rejects_garbage():
    with pytest.raises(ValueError):
        parse_target("not-a-date")


def test_formatting_helpers():
    snap = compute_countdown(
        DEFAULT_TARGET - timedelta(days=12345, hours=4, minutes=56, seconds=7),
        DEFAULT_TARGET,
    )
    assert snap.clock_text == "04:56:07"
    assert snap.days_text == "12,345 DAYS"


def test_young_stage_copy_theme():
    """青年期文案应聚焦“趁年轻/时间流逝”，不再出现“靠近自由”式叙事。"""
    from retirement_pet.countdown import STAGE_SUBTITLES, STAGE_TEXT

    pool = STAGE_SUBTITLES[LifeStage.YOUNG]
    assert len(pool) >= 5
    assert all("自由" not in line for line in pool)
    assert "学生" not in STAGE_TEXT[LifeStage.YOUNG]


def test_subtitle_stable_within_day_and_rotates_across_days():
    from retirement_pet.countdown import STAGE_SUBTITLES, subtitle_for

    pool = STAGE_SUBTITLES[LifeStage.YOUNG]
    day = datetime(2026, 8, 23)
    # Same day (any hour) -> same line.
    morning = subtitle_for(day.replace(hour=8), LifeStage.YOUNG)
    night = subtitle_for(day.replace(hour=23), LifeStage.YOUNG)
    assert morning == night
    assert morning in pool
    # Consecutive days eventually cover the whole pool (gcd(1, len)=1).
    seen = {subtitle_for(day + timedelta(days=i), LifeStage.YOUNG) for i in range(len(pool))}
    assert seen == set(pool)


def test_snapshot_subtitle_uses_pool_for_current_stage():
    from retirement_pet.countdown import STAGE_SUBTITLES

    snap = compute_countdown(datetime(2026, 8, 23), DEFAULT_TARGET)
    assert snap.stage is LifeStage.YOUNG
    assert snap.stage_subtitle in STAGE_SUBTITLES[LifeStage.YOUNG]

    old = compute_countdown(datetime(2055, 1, 1), DEFAULT_TARGET)
    assert old.stage is LifeStage.OLD
    assert old.stage_subtitle in STAGE_SUBTITLES[LifeStage.OLD]
