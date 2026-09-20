"""Shared pytest fixtures."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def isolate_pet_application_startup_registry(request, monkeypatch):
    """PetApplication tests must never read or write the user's real HKCU."""
    app_test_files = {
        "test_agent_cli.py",
        "test_agent_protocol.py",
        "test_app_smoke.py",
        "test_character_onboarding.py",
        "test_control_panel.py",
        "test_media_bridge.py",
        "test_v13_06_activity.py",
        "test_native_window.py",
        "test_todo.py",
        "test_todo_subtree_semantics.py",
        "test_todo_v13_04_ui.py",
        "test_todo_v13_ui.py",
        "test_tray.py",
    }
    if request.node.path.name not in app_test_files:
        return
    if not ({"qt_application", "native_qt_app"}
            & set(request.fixturenames)):
        return
    from retirement_pet.startup import MemoryBackend

    monkeypatch.setattr("retirement_pet.app.WinRegBackend", MemoryBackend)


@pytest.fixture()
def fake_clock():
    from retirement_pet.clock import FakeClock

    return FakeClock()


@pytest.fixture()
def qt_application():
    """Shared QApplication with the offscreen platform."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    yield app
