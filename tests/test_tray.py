"""Tray recovery contract (DESIGN_V2 11.2 / ADR-V2-012, M0 P0).

The tray is the recovery path that can never be taken away: with the pet
hidden, click-through or lost off-screen, the tray context menu must still
offer 显示 / 关闭穿透 / 恢复主屏 / 控制面板 / 退出 - and actually perform
them.  These tests verify the wiring (menu attached + handlers connected);
the real-HWND behaviour runs in the native tier (tests/native/).
"""

from __future__ import annotations

import pytest


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
        instance_name=f"pytest-tray-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


def tray_actions(app):
    """Rebuild the tray menu (as aboutToShow does) and return its actions."""
    app.tray.rebuild_menu()
    return app.tray.menu.actions()


def find_action(actions, text):
    for action in actions:
        if action.text() == text:
            return action
    return None


def test_tray_icon_has_context_menu_attached(qt_application):
    from retirement_pet.ui.tray import TrayController

    tray = TrayController(lambda menu: None)
    # setContextMenu is what makes right-click work while the pet window
    # cannot receive input; without it there is no recovery path.
    assert tray.icon.contextMenu() is tray.menu


def test_tray_menu_offers_fixed_recovery_commands(app, qt_application):
    actions = tray_actions(app)
    labels = [a.text() for a in actions]
    for required in ("显示桌宠", "关闭鼠标穿透", "恢复到主屏幕",
                     "打开控制面板", "退出"):
        assert required in labels, f"tray menu missing fixed command {required!r}"


def test_tray_todo_entry_opens_existing_todo_page(app, qt_application,
                                                   monkeypatch):
    opened = []
    monkeypatch.setattr(app, "_open_control_panel", opened.append)

    action = find_action(tray_actions(app), "打开待办")
    assert action is not None
    action.trigger()

    assert opened == ["todo"]


def test_tray_menu_recovers_from_click_through(app, qt_application):
    from PySide6.QtCore import Qt

    app._set_click_through(True)
    assert app.window.is_click_through
    assert bool(app.window.windowFlags() & Qt.WindowTransparentForInput)

    action = find_action(tray_actions(app), "关闭鼠标穿透")
    assert action is not None
    action.trigger()

    assert not app.window.is_click_through
    assert not bool(app.window.windowFlags() & Qt.WindowTransparentForInput)
    assert not app.settings.get("click_through")
    # The base window contract survives the toggle (ADR-012).
    flags = app.window.windowFlags()
    assert bool(flags & Qt.FramelessWindowHint)
    assert bool(flags & Qt.Tool)


def test_tray_menu_recovery_works_while_hidden(app, qt_application):
    app._hide_window()
    assert not app.window.isVisible()

    action = find_action(tray_actions(app), "显示桌宠")
    action.trigger()
    assert app.window.isVisible()

    # Recovery must also clear click-through independently of visibility.
    app._set_click_through(True)
    action = find_action(tray_actions(app), "关闭鼠标穿透")
    action.trigger()
    assert not app.window.is_click_through


def test_tray_restore_to_primary_screen(app, qt_application):
    from PySide6.QtGui import QGuiApplication

    app.window.move(-20000, -20000)
    action = find_action(tray_actions(app), "恢复到主屏幕")
    action.trigger()

    screen = QGuiApplication.primaryScreen()
    assert screen is not None
    geo = app.window.frameGeometry()
    assert geo.intersects(screen.availableGeometry())

    import json

    data = json.loads(
        (app._data_dir / "state.json").read_text(encoding="utf-8")
    )
    assert data.get("window_pos") == [app.window.x(), app.window.y()]


def test_tray_menu_state_refreshes_on_rebuild(app, qt_application):
    app._set_click_through(True)
    actions = tray_actions(app)
    toggle = find_action(actions, "鼠标穿透")
    assert toggle is not None and toggle.isChecked()

    app._set_click_through(False)
    # A stale menu would still show checked; the contract requires refresh.
    toggle = find_action(tray_actions(app), "鼠标穿透")
    assert not toggle.isChecked()


def test_window_restore_to_primary_screen_offscreen_window(qt_application):
    from PySide6.QtGui import QGuiApplication

    from retirement_pet.ui.countdown_panel import CountdownPanel
    from retirement_pet.ui.pet_window import PetWindow
    from retirement_pet.ui.renderer import CatRenderer

    window = PetWindow(renderer=CatRenderer(), panel=CountdownPanel(),
                       always_on_top=False)
    window.move(-30000, -30000)
    moved = window.restore_to_primary_screen()
    assert moved
    screen = QGuiApplication.primaryScreen()
    assert screen is not None
    assert window.frameGeometry().intersects(screen.availableGeometry())


# -- sound_enabled reaches the playback path (M0 fact fix) -------------------


def test_sound_enabled_false_blocks_playback(qt_application, tmp_path):
    from retirement_pet.audio import AudioManager

    mgr = AudioManager(sound_enabled=False)
    track = tmp_path / "a.mp3"
    track.write_bytes(b"x")
    mgr.set_tracks([str(track)])
    assert mgr.sound_enabled is False
    assert mgr.play() is False
    assert mgr.toggle() is False


def test_sound_enabled_can_be_toggled_on(qt_application, tmp_path):
    from retirement_pet.audio import AudioManager

    mgr = AudioManager(sound_enabled=False)
    mgr.set_sound_enabled(True)
    assert mgr.sound_enabled is True


def test_app_settings_apply_sound_enabled(app, qt_application):
    app._apply_settings({"sound_enabled": False})
    assert app.audio.sound_enabled is False
    assert app.settings.get("sound_enabled") is False
    app._apply_settings({"sound_enabled": True})
    assert app.audio.sound_enabled is True
