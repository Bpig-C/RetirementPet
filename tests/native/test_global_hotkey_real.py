"""Real Windows contract for the optional Ctrl+Alt+T Todo hotkey.

This tier deliberately uses ``user32.SendInput`` instead of calling the
native-event filter directly.  A passing test therefore proves the complete
path used by the product::

    RegisterHotKey -> Windows WM_HOTKEY -> Qt native event filter -> callback

Run only in the opt-in native tier::

    RP_RUN_NATIVE=1 python -m pytest tests/native/test_global_hotkey_real.py -q

The chord is owned only for the duration of one test and every registration
is released in ``finally`` blocks, including assertion/error paths.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os

import pytest

# This must be set before importing QApplication/PySide6 in this process.
if os.environ.get("RP_RUN_NATIVE") == "1":
    os.environ["QT_QPA_PLATFORM"] = "windows"

pytestmark = pytest.mark.skipif(
    os.environ.get("RP_RUN_NATIVE") != "1",
    reason="native tier is opt-in: RP_RUN_NATIVE=1 python -m pytest tests/native",
)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
ERROR_HOTKEY_ALREADY_REGISTERED = 1409


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wt.DWORD),
        ("wParamL", wt.WORD),
        ("wParamH", wt.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wt.DWORD), ("value", _INPUT_UNION)]


def _keyboard_input(virtual_key: int, *, key_up: bool = False) -> _INPUT:
    item = _INPUT()
    item.type = INPUT_KEYBOARD
    item.ki = _KEYBDINPUT(
        virtual_key,
        0,
        KEYEVENTF_KEYUP if key_up else 0,
        0,
        0,
    )
    return item


def _send_ctrl_alt_t() -> None:
    """Press and release Ctrl+Alt+T as one user32 SendInput batch."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
    user32.SendInput.restype = wt.UINT

    sequence = (_INPUT * 6)(
        _keyboard_input(VK_CONTROL),
        _keyboard_input(VK_MENU),
        _keyboard_input(0x54),  # T
        _keyboard_input(0x54, key_up=True),
        _keyboard_input(VK_MENU, key_up=True),
        _keyboard_input(VK_CONTROL, key_up=True),
    )
    sent = int(user32.SendInput(len(sequence), sequence, ctypes.sizeof(_INPUT)))
    assert sent == len(sequence), (
        f"SendInput injected {sent}/{len(sequence)} events; "
        f"winerror={ctypes.get_last_error()}"
    )


@pytest.fixture(scope="session")
def hotkey_qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":  # pragma: no cover - environment
        pytest.skip(f"native tier needs the windows platform, got {app.platformName()}")
    return app


def _wait_for_activation(app, activations: list[str]) -> None:
    """Inject the chord after Qt enters its real Windows message loop."""
    from PySide6.QtCore import QEventLoop, QTimer

    previous = len(activations)
    loop = QEventLoop()
    QTimer.singleShot(0, _send_ctrl_alt_t)
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(loop.quit)
    watchdog.start(2000)

    poll = QTimer()
    poll.setInterval(10)
    poll.timeout.connect(lambda: loop.quit() if len(activations) > previous else None)
    poll.start()
    loop.exec()
    poll.stop()
    watchdog.stop()

    # SendInput can yield more than one qualifying notification on some real
    # Windows/Qt combinations.  The product callback is idempotent; this
    # contract freezes the meaningful requirement that delivery occurred.
    assert len(activations) > previous, (
        "Windows did not deliver Ctrl+Alt+T through Qt's native event filter"
    )


def test_real_ctrl_alt_t_delivery_conflict_and_release(hotkey_qt_app):
    """Exercise delivery, duplicate-registration isolation and detach release."""
    from PySide6.QtWidgets import QWidget

    from retirement_pet.global_hotkey import (
        TODO_HOTKEY_ID,
        TODO_HOTKEY_MODIFIERS,
        VK_T,
        TodoHotkeyFilter,
        WindowsHotkeyBackend,
    )

    owner_window = QWidget()
    competing_window = QWidget()
    owner_hwnd = int(owner_window.winId())
    competing_hwnd = int(competing_window.winId())
    assert owner_hwnd and competing_hwnd and owner_hwnd != competing_hwnd

    activations: list[str] = []
    hotkey_filter = TodoHotkeyFilter(lambda: activations.append("todo"))
    competing_backend = WindowsHotkeyBackend()
    competing_id = TODO_HOTKEY_ID + 1
    competing_owned = False

    hotkey_qt_app.installNativeEventFilter(hotkey_filter)
    try:
        assert hotkey_filter.attach(owner_window), (
            "Ctrl+Alt+T was already owned by another process; close that "
            "application before running the native conformance tier"
        )

        # The first activation proves the complete real Windows delivery path.
        _wait_for_activation(hotkey_qt_app, activations)

        # A second real registration for the same chord must fail globally.
        ctypes.set_last_error(0)
        assert not competing_backend.register(
            competing_hwnd,
            competing_id,
            TODO_HOTKEY_MODIFIERS,
            VK_T,
        ), "Windows unexpectedly allowed two owners for Ctrl+Alt+T"
        assert ctypes.get_last_error() == ERROR_HOTKEY_ALREADY_REGISTERED

        # Failed competing registration must not disturb the existing owner.
        _wait_for_activation(hotkey_qt_app, activations)

        hotkey_filter.detach()
        assert not hotkey_filter.registered

        # Successful acquisition by another HWND is the observable proof that
        # detach released the system-wide registration.
        competing_owned = competing_backend.register(
            competing_hwnd,
            competing_id,
            TODO_HOTKEY_MODIFIERS,
            VK_T,
        )
        assert competing_owned, "detach did not release Ctrl+Alt+T"
    finally:
        hotkey_filter.detach()
        if competing_owned:
            competing_backend.unregister(competing_hwnd, competing_id)
        hotkey_qt_app.removeNativeEventFilter(hotkey_filter)
        owner_window.close()
        competing_window.close()
        hotkey_qt_app.processEvents()
