"""ActivityMonitor behavior with injected providers."""

from __future__ import annotations

from retirement_pet.activity_monitor import ActivityMonitor


def test_returns_idle_seconds():
    monitor = ActivityMonitor(lambda: 42.5)
    assert monitor.idle_seconds() == 42.5
    assert monitor.is_available is True


def test_unknown_idle_returns_none():
    monitor = ActivityMonitor(lambda: None)
    assert monitor.idle_seconds() is None
    assert monitor.is_user_active() is None  # never "long idle" on failure


def test_is_user_active_threshold():
    monitor = ActivityMonitor(lambda: 3.0)
    assert monitor.is_user_active(threshold_s=5.0) is True
    monitor2 = ActivityMonitor(lambda: 30.0)
    assert monitor2.is_user_active(threshold_s=5.0) is False


def test_negative_clamped():
    monitor = ActivityMonitor(lambda: -5)
    assert monitor.idle_seconds() == 0.0


def test_real_provider_smoke():
    """The real GetLastInputInfo probe runs on this Windows machine."""
    from retirement_pet.activity_monitor import _last_input_idle_seconds

    value = _last_input_idle_seconds()
    assert value is None or value >= 0
