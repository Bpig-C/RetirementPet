"""Native Windows integration tier (CONFORMANCE 13.1: WINDOWS_INTEGRATION).

These tests run against a REAL ``windows`` QPA platform and real HWNDs -
offscreen unit tests cannot see native title bars, taskbar semantics or
focus stealing (ADR-014).  They are opt-in so the default offscreen suite
stays hermetic:

    RP_RUN_NATIVE=1 python -m pytest tests/native -q

Every check here mirrors scripts/verify_windows.ps1 for the frozen EXE.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import uuid

import pytest

# Must happen before any QApplication exists in this process.
if os.environ.get("RP_RUN_NATIVE") == "1":
    os.environ["QT_QPA_PLATFORM"] = "windows"

pytestmark = pytest.mark.skipif(
    os.environ.get("RP_RUN_NATIVE") != "1",
    reason="native tier is opt-in: RP_RUN_NATIVE=1 python -m pytest tests/native",
)

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_STYLE = -16
GWL_EXSTYLE = -20

WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020


def get_window_long(hwnd: int, index: int) -> int:
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
    return int(user32.GetWindowLongPtrW(hwnd, index))


def get_foreground_hwnd() -> int:
    return int(user32.GetForegroundWindow() or 0)


@pytest.fixture(scope="session")
def native_qt_app():
    """Real (non-offscreen) QApplication; skipped when unavailable."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    if app.platformName() != "windows":  # pragma: no cover - environment
        pytest.skip(f"native tier needs the windows platform, got {app.platformName()}")
    yield app


@pytest.fixture()
def app(native_qt_app, tmp_path):
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    pet = PetApplication(
        argv=["retirement-pet"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-native-{uuid.uuid4().hex[:12]}",
    )
    pet.tray.show()
    yield pet
    pet.shutdown()


def process_events(app, ms: int = 400) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def hwnd_of(window) -> int:
    assert window.isVisible(), "window must be shown to own an HWND"
    return int(window.winId())


def sync_window_state(window) -> int:
    """Touch winId() to force Qt to flush pending native flag changes.

    Exstyle updates after setWindowFlags land asynchronously; reading
    GetWindowLongPtrW without this sync can observe the previous state.
    """
    return int(window.winId())


def find_action(actions, text):
    for action in actions:
        if action.text() == text:
            return action
    return None


# -- P0 window contract (CONFORMANCE 7.1) -------------------------------------


def test_hwnd_is_frameless_tool_window(app, native_qt_app):
    app._show_window()  # normal startup path: show + raise, no activate
    process_events(native_qt_app)
    hwnd = hwnd_of(app.window)

    style = get_window_long(hwnd, GWL_STYLE)
    exstyle = get_window_long(hwnd, GWL_EXSTYLE)
    assert not (style & WS_CAPTION), "native title bar present"
    assert not (style & WS_THICKFRAME), "native resize border present"
    assert not (style & WS_MINIMIZEBOX), "minimize box present"
    assert not (style & WS_MAXIMIZEBOX), "maximize box present"
    assert exstyle & WS_EX_TOOLWINDOW, "not a tool window (taskbar/Alt+Tab risk)"
    assert exstyle & WS_EX_LAYERED, "transparency not active"


def test_normal_startup_does_not_steal_foreground(app, native_qt_app):
    # Exercise the real first-run path as well as the transparent pet: the
    # one-time standard panel may appear, but neither window may take focus.
    app._headless = False
    before = get_foreground_hwnd()
    app._show_window()
    app._maybe_open_character_onboarding()
    process_events(native_qt_app)
    hwnd = hwnd_of(app.window)
    panel = app._panel
    assert panel is not None and panel.isVisible()
    panel_hwnd = int(panel.winId())
    after = get_foreground_hwnd()
    assert after not in (hwnd, panel_hwnd), (
        "normal startup pet/onboarding window stole the foreground")


# -- P0 tray recovery under click-through (CONFORMANCE 7.2) --------------------


def test_click_through_sets_native_exstyle(app, native_qt_app):
    app._show_window()
    process_events(native_qt_app)
    hwnd = hwnd_of(app.window)

    app._set_click_through(True)
    process_events(native_qt_app, 200)
    sync_window_state(app.window)  # force pending native flag sync
    assert app.window.isVisible(), "flag toggle must not hide the pet window"
    exstyle = get_window_long(hwnd, GWL_EXSTYLE)
    assert exstyle & WS_EX_TRANSPARENT, "click-through not visible in native exstyle"
    assert exstyle & WS_EX_LAYERED

    # The tray menu is the only remaining input path in this state; drive
    # the exact connected action a right-click would trigger.
    app.tray.rebuild_menu()
    action = find_action(app.tray.menu.actions(), "关闭鼠标穿透")
    assert action is not None, "tray menu lacks the fixed recovery command"
    action.trigger()
    process_events(native_qt_app, 200)
    sync_window_state(app.window)
    assert app.window.isVisible(), "recovery must not hide the pet window"

    exstyle = get_window_long(hwnd, GWL_EXSTYLE)
    assert not (exstyle & WS_EX_TRANSPARENT), "tray recovery failed to clear click-through"
    assert get_window_long(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW


def test_persisted_click_through_restored_on_launch(app, native_qt_app):
    app.settings.set("click_through", True)
    app.settings.save()
    app.shutdown()

    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    second = PetApplication(
        argv=["retirement-pet"],
        data_dir=app._data_dir,
        clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-native-{uuid.uuid4().hex[:12]}",
    )
    try:
        second._show_window()
        process_events(native_qt_app)
        hwnd = int(second.window.winId())
        exstyle = get_window_long(hwnd, GWL_EXSTYLE)
        assert exstyle & WS_EX_TRANSPARENT, "persisted click-through not restored"
        # ...and the tray recovery path still clears it.
        second.tray.rebuild_menu()
        action = find_action(second.tray.menu.actions(), "关闭鼠标穿透")
        action.trigger()
        process_events(native_qt_app, 200)
        assert not (get_window_long(hwnd, GWL_EXSTYLE) & WS_EX_TRANSPARENT)
    finally:
        second.shutdown()


# -- P0 normal exit (CONFORMANCE 7.4) -------------------------------------------


def test_tray_quit_exits_event_loop_cleanly(app, native_qt_app):
    from PySide6.QtCore import QTimer

    app._show_window()
    process_events(native_qt_app)

    events = {"shutdown": False}
    original_shutdown = app.shutdown

    def traced_shutdown():
        events["shutdown"] = True
        original_shutdown()

    app.shutdown = traced_shutdown
    # aboutToQuit is already connected to the original bound method; swap it.
    app.qt_app.aboutToQuit.disconnect()
    app.qt_app.aboutToQuit.connect(traced_shutdown)

    # Watchdog: fail instead of hanging if quit never lands.
    QTimer.singleShot(5000, app.qt_app.quit)

    app.tray.rebuild_menu()
    action = find_action(app.tray.menu.actions(), "退出")
    assert action is not None
    action.trigger()
    app.qt_app.exec()  # returns when quit() runs

    assert events["shutdown"], "shutdown sequence did not run on quit"
    assert not app.window.isVisible()
    # State must have been persisted by the shutdown path.
    assert (app._data_dir / "state.json").exists()


def test_native_action_caption_uses_real_chinese_glyphs(native_qt_app):
    """The normal Windows QPA must not accept tofu for engine captions."""
    from PySide6.QtGui import QFontMetrics

    from retirement_pet.models import ActionId
    from retirement_pet.ui.overlay_renderer import resolved_caption

    text, font = resolved_caption(ActionId.WORK)
    assert text == "工作中"
    metrics = QFontMetrics(font)
    assert all(metrics.inFontUcs4(ord(character)) for character in text)
