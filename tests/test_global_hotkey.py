"""Optional Ctrl+Alt+T native hotkey: registration and message routing."""

from __future__ import annotations

import ctypes

from retirement_pet.global_hotkey import (
    TODO_HOTKEY_ID,
    TODO_HOTKEY_MODIFIERS,
    VK_T,
    WM_HOTKEY,
    TodoHotkeyFilter,
    WindowsHotkeyBackend,
)
from retirement_pet.session_watch import _MSG


class FakeBackend:
    def __init__(self, *, available=True, registration=True):
        self.available = available
        self.registration = registration
        self.register_calls = []
        self.unregister_calls = []

    def register(self, hwnd, hotkey_id, modifiers, virtual_key):
        self.register_calls.append((hwnd, hotkey_id, modifiers, virtual_key))
        return self.registration

    def unregister(self, hwnd, hotkey_id):
        self.unregister_calls.append((hwnd, hotkey_id))
        return True


class FakeWindow:
    def __init__(self, hwnd=0x5001):
        self.hwnd = hwnd

    def winId(self):  # noqa: N802 - mirrors Qt
        return self.hwnd


class FakeVoidPtr:
    def __init__(self, address):
        self.address = address

    def __int__(self):
        return self.address


def make_msg(hwnd, message_id, wparam):
    buffer = ctypes.create_string_buffer(ctypes.sizeof(_MSG))
    msg = _MSG.from_buffer(buffer)
    msg.hwnd = ctypes.c_void_p(hwnd)
    msg.message = message_id
    msg.wParam = wparam
    ptr = FakeVoidPtr(ctypes.addressof(msg))
    ptr.keepalive = (buffer, msg)
    return ptr


def test_registers_default_chord_once_and_detaches_once(qt_application):
    backend = FakeBackend()
    hotkey = TodoHotkeyFilter(lambda: None, backend=backend)
    window = FakeWindow()

    assert hotkey.attach(window)
    assert hotkey.registered
    assert backend.register_calls == [(
        window.hwnd, TODO_HOTKEY_ID, TODO_HOTKEY_MODIFIERS, VK_T)]

    assert hotkey.attach(window)  # same HWND is idempotent
    assert len(backend.register_calls) == 1

    hotkey.detach()
    hotkey.detach()
    assert not hotkey.registered
    assert backend.unregister_calls == [(window.hwnd, TODO_HOTKEY_ID)]


def test_attach_rebinds_when_qt_recreates_native_window(qt_application):
    backend = FakeBackend()
    hotkey = TodoHotkeyFilter(lambda: None, backend=backend)

    assert hotkey.attach(FakeWindow(0x5001))
    assert hotkey.attach(FakeWindow(0x5002))

    assert backend.unregister_calls == [(0x5001, TODO_HOTKEY_ID)]
    assert [call[0] for call in backend.register_calls] == [0x5001, 0x5002]


def test_matching_wm_hotkey_invokes_callback_exactly_once(qt_application):
    backend = FakeBackend()
    activations = []
    hotkey = TodoHotkeyFilter(lambda: activations.append("todo"), backend=backend)
    assert hotkey.attach(FakeWindow())

    hotkey.nativeEventFilter(
        b"windows_generic_MSG", make_msg(0x5001, WM_HOTKEY, TODO_HOTKEY_ID))
    hotkey.nativeEventFilter(
        b"windows_generic_MSG", make_msg(0x5001, WM_HOTKEY, TODO_HOTKEY_ID + 1))
    hotkey.nativeEventFilter(
        b"windows_generic_MSG", make_msg(0x9999, WM_HOTKEY, TODO_HOTKEY_ID))
    hotkey.nativeEventFilter(
        b"windows_generic_MSG", make_msg(0x5001, 0x000F, TODO_HOTKEY_ID))
    hotkey.nativeEventFilter(
        b"mac_generic_MSG", make_msg(0x5001, WM_HOTKEY, TODO_HOTKEY_ID))

    assert activations == ["todo"]


def test_registration_conflict_degrades_without_unregister(qt_application, caplog):
    backend = FakeBackend(registration=False)
    hotkey = TodoHotkeyFilter(lambda: None, backend=backend)

    assert not hotkey.attach(FakeWindow())
    assert not hotkey.registered
    hotkey.detach()
    assert backend.unregister_calls == []
    assert "continuing without global Todo hotkey" in caplog.text


def test_unavailable_platform_does_not_touch_native_api(qt_application):
    backend = FakeBackend(available=False)
    hotkey = TodoHotkeyFilter(lambda: None, backend=backend)

    assert not hotkey.supported
    assert not hotkey.attach(FakeWindow())
    assert backend.register_calls == []
    assert backend.unregister_calls == []

    native_backend = WindowsHotkeyBackend(platform="linux")
    assert not native_backend.available
    assert not native_backend.register(0x5001, TODO_HOTKEY_ID,
                                       TODO_HOTKEY_MODIFIERS, VK_T)


def test_callback_failure_is_isolated_from_native_message_pump(
        qt_application, caplog):
    backend = FakeBackend()

    def fail():
        raise RuntimeError("callback canary")

    hotkey = TodoHotkeyFilter(fail, backend=backend)
    assert hotkey.attach(FakeWindow())
    assert hotkey.nativeEventFilter(
        b"windows_generic_MSG",
        make_msg(0x5001, WM_HOTKEY, TODO_HOTKEY_ID)) is False
    assert "global Todo hotkey callback failed" in caplog.text


def test_application_shutdown_unregisters_owned_hotkey(
        qt_application, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    backend = FakeBackend()
    pet = PetApplication(
        argv=["retirement-pet"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-hotkey-lifecycle-{tmp_path.name}",
        todo_hotkey_backend=backend,
    )
    try:
        # Headless verification deliberately does not reserve a user chord;
        # simulate the same attachment performed by the interactive run path.
        assert pet._todo_hotkey.attach(pet.window)
        assert pet._todo_hotkey_filter_installed

        pet.shutdown()

        assert backend.unregister_calls == [
            (int(pet.window.winId()), TODO_HOTKEY_ID)]
        assert not pet._todo_hotkey.registered
        assert not pet._todo_hotkey_filter_installed
    finally:
        pet.shutdown()


def test_application_does_not_register_hotkey_without_native_filter(
        qt_application, tmp_path, monkeypatch):
    """A failed native-filter install must not reserve an unhandled chord."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    backend = FakeBackend()
    pet = PetApplication(
        argv=["retirement-pet"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-hotkey-filter-failure-{tmp_path.name}",
        todo_hotkey_backend=backend,
    )
    try:
        qt_application.removeNativeEventFilter(pet._todo_hotkey)
        pet._todo_hotkey_filter_installed = False
        pet._headless = False
        monkeypatch.setattr(type(qt_application), "exec", lambda _self: 0)

        assert pet.run() == 0
        assert backend.register_calls == []
        assert not pet._todo_hotkey.registered
    finally:
        pet.shutdown()
