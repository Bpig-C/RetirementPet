"""V13-06: activity linking explainability, gating and privacy.

隔离约定：全部使用合成 idle provider 与临时数据目录；不读取真实输入。
"""

from __future__ import annotations

import json
import random

import pytest

from retirement_pet.clock import FakeClock


class FakeIdle:
    def __init__(self, seconds):
        self._seconds = seconds

    def __call__(self):
        return self._seconds


@pytest.fixture()
def app(qt_application, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    pet = PetApplication(
        argv=["retirement-pet"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=True,
        idle_provider=FakeIdle(2.0),
        instance_name=f"pytest-v13a-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


# -- aggregate activity state ------------------------------------------------------


def test_activity_state_active_idle_unknown(app):
    assert app.rhythm.activity_state() == "active"  # idle 2s < 5s

    app.activity._provider = FakeIdle(600.0)
    assert app.rhythm.activity_state() == "idle"

    app.activity._provider = FakeIdle(None)
    assert app.rhythm.activity_state() == "unknown"


def test_activity_status_shape_and_privacy(app, qt_application):
    from retirement_pet.todo import Horizon

    status = app.activity_status()
    assert set(status) == {"state", "link_enabled", "focused",
                           "working_on_focused_task"}
    assert status == {"state": "active", "link_enabled": True,
                      "focused": False, "working_on_focused_task": False}

    task = app.todo.add_task("绝密专注标题", Horizon.SHORT)
    app.todo.start_focus(task.id)
    qt_application.processEvents()
    status = app.activity_status()
    assert status["focused"] is True
    assert status["working_on_focused_task"] is True  # active + focused

    app.activity._provider = FakeIdle(None)
    status = app.activity_status()
    assert status["working_on_focused_task"] is False  # unknown ≠ working


# -- linking gate -------------------------------------------------------------------


def test_rhythm_disabled_does_nothing_even_with_sustained_input():
    import random

    from retirement_pet.action_controller import ActionController
    from retirement_pet.action_registry import ActionRegistry
    from retirement_pet.activity_monitor import ActivityMonitor
    from retirement_pet.rhythm import RhythmController

    clock = FakeClock()
    controller = ActionController(ActionRegistry(), clock, random.Random(1))
    monitor = ActivityMonitor(FakeIdle(1.0))
    rhythm = RhythmController(controller, clock, monitor,
                              {"activity_link_enabled": False}, None)
    assert rhythm.link_enabled() is False
    for _ in range(90):
        clock.advance_ms(1000)
        rhythm.tick()
    assert controller.current_action() is None  # never requested anything


def test_rhythm_enabled_still_requests_work():
    import random

    from retirement_pet.action_controller import ActionController
    from retirement_pet.action_registry import ActionRegistry
    from retirement_pet.activity_monitor import ActivityMonitor
    from retirement_pet.rhythm import RhythmController

    clock = FakeClock()
    controller = ActionController(ActionRegistry(), clock, random.Random(1))
    monitor = ActivityMonitor(FakeIdle(1.0))
    rhythm = RhythmController(controller, clock, monitor, {}, None)
    assert rhythm.link_enabled() is True
    for _ in range(45):
        clock.advance_ms(1000)
        rhythm.tick()
    assert controller.current_action() is not None
    assert controller.current_action().value == "work"


def test_unknown_provider_never_forces_rest(app):
    app.activity._provider = FakeIdle(None)
    for _ in range(200):
        app.rhythm.tick()
    assert app.controller.current_action() is None  # nothing was forced


def test_settings_toggle_reaches_rhythm(app, qt_application):
    from PySide6.QtWidgets import QCheckBox, QPushButton

    app._open_control_panel("sound_schedule")
    page = app._panel._built["sound_schedule"]
    toggle = page.findChild(QCheckBox, "schedule_activity_link")
    apply_btn = page.findChild(QPushButton, "schedule_apply")
    assert toggle is not None and toggle.isChecked()

    toggle.setChecked(False)
    apply_btn.click()
    qt_application.processEvents()
    assert app.settings.get("activity_link_enabled") is False
    assert app.rhythm.link_enabled() is False

    toggle.setChecked(True)
    apply_btn.click()
    qt_application.processEvents()
    assert app.rhythm.link_enabled() is True


# -- agent status integration ---------------------------------------------------------


def test_agent_status_reports_activity_consistently(app, qt_application,
                                                     tmp_path):
    from retirement_pet.todo import Horizon

    response = json.loads(app.agent_server.handle_line(
        b'{"protocol":"retirement-pet.agent.v1","request_id":"a1",'
        b'"operation":"status.get","args":{}}\n')[:-1].decode("utf-8"))
    activity = response["data"]["activity"]
    assert activity["state"] == "active"
    assert activity["link_enabled"] is True

    task = app.todo.add_task("代理一致性标题", Horizon.SHORT)
    app.todo.start_focus(task.id)
    response = json.loads(app.agent_server.handle_line(
        b'{"protocol":"retirement-pet.agent.v1","request_id":"a2",'
        b'"operation":"status.get","args":{}}\n')[:-1].decode("utf-8"))
    assert response["data"]["activity"]["focused"] is True
    assert response["data"]["todo"]["focusing"] is True  # consistent
    assert "代理一致性标题" not in json.dumps(response)  # title-free


def test_activity_facts_use_only_idle_time():
    """Privacy boundary: the monitor reads ONLY GetLastInputInfo-style
    aggregates; no hooks, keystrokes or window queries exist."""
    from pathlib import Path

    source = Path("src/retirement_pet/activity_monitor.py").read_text(
        encoding="utf-8")
    for forbidden in ("SetWindowsHookEx", "GetAsyncKeyState", "GetKeyState",
                      "GetForegroundWindow", "keybd_event", "SendInput"):
        assert forbidden not in source, forbidden

