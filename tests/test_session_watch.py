"""P0-E session watcher: ctypes MSG parsing, no AttributeError storm."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging

from retirement_pet.session_watch import (
    PBT_APMSUSPEND,
    PBT_APMRESUMEAUTOMATIC,
    WM_POWERBROADCAST,
    WM_WTSSESSION_CHANGE,
    WTS_SESSION_LOCK,
    WTS_SESSION_UNLOCK,
    SessionWatchFilter,
    _MSG,
    parse_message,
)


class FakeVoidPtr:
    """Mimics shiboken6.VoidPtr: int() works, no .message/.wParam attrs."""

    def __init__(self, address: int):
        self._address = address

    def __int__(self) -> int:
        return self._address


def make_msg(hwnd: int, message_id: int, wparam: int) -> FakeVoidPtr:
    buffer = ctypes.create_string_buffer(ctypes.sizeof(_MSG))
    msg = _MSG.from_buffer(buffer)
    msg.hwnd = ctypes.c_void_p(hwnd)
    msg.message = message_id
    msg.wParam = wparam
    ptr = FakeVoidPtr(ctypes.addressof(msg))
    ptr._keepalive = (buffer, msg)  # the MSG must outlive the pointer
    return ptr


# -- parse_message ----------------------------------------------------------------


def test_parse_extracts_hwnd_message_wparam():
    ptr = make_msg(hwnd=0x1234, message_id=WM_POWERBROADCAST,
                   wparam=PBT_APMSUSPEND)
    assert parse_message(ptr) == (0x1234, WM_POWERBROADCAST, PBT_APMSUSPEND)


def test_parse_null_pointer_returns_none():
    assert parse_message(FakeVoidPtr(0)) is None
    assert parse_message(None) is None
    assert parse_message(FakeVoidPtr(0x10)) is None  # near-null guard


def test_parse_unconvertible_object_returns_none():
    class Opaque:
        pass

    assert parse_message(Opaque()) is None


def test_parse_tolerates_tollong_variant():
    class ToLongPtr:
        def toLongLong(self) -> int:
            ptr = make_msg(0x100, WM_POWERBROADCAST, PBT_APMSUSPEND)
            return int(ptr)

    # toLongLong variant produces a usable address
    address = ToLongPtr().toLongLong()
    assert parse_message(FakeVoidPtr(address)) is not None


# -- filter behavior ----------------------------------------------------------------


def make_filter(hwnd=0x5001):
    changes: list[bool] = []
    filt = SessionWatchFilter(changes.append)
    filt._hwnd = hwnd  # simulate a successful attach
    return filt, changes


def test_suspend_and_resume_fire_callback():
    filt, changes = make_filter()
    filt.nativeEventFilter(
        b"windows_generic_MSG",
        make_msg(0x5001, WM_POWERBROADCAST, PBT_APMSUSPEND))
    assert changes == [True]
    filt.nativeEventFilter(
        b"windows_generic_MSG",
        make_msg(0x5001, WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC))
    assert changes == [True, False]


def test_lock_unlock_fire_callback():
    filt, changes = make_filter()
    filt.nativeEventFilter(
        b"windows_generic_MSG",
        make_msg(0x5001, WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK))
    assert changes == [True] and filt.suspended
    filt.nativeEventFilter(
        b"windows_generic_MSG",
        make_msg(0x5001, WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK))
    assert changes == [True, False] and not filt.suspended


def test_other_windows_messages_ignored():
    filt, changes = make_filter()
    for msg_id in (0x000F, 0x0200, 0x0100):
        filt.nativeEventFilter(
            b"windows_generic_MSG", make_msg(0x5001, msg_id, 0))
    assert changes == []


def test_foreign_hwnd_ignored():
    filt, changes = make_filter(hwnd=0x5001)
    filt.nativeEventFilter(
        b"windows_generic_MSG",
        make_msg(0x9999, WM_POWERBROADCAST, PBT_APMSUSPEND))
    assert changes == []


def test_non_windows_event_type_ignored():
    filt, changes = make_filter()
    filt.nativeEventFilter(b"mac_generic_MSG", make_msg(0x5001, 0x218, 4))
    assert changes == []


def test_no_attributeerror_storm(caplog):
    """The P0-E regression: parsing must never raise (or log) errors."""
    filt, changes = make_filter()
    with caplog.at_level(logging.ERROR):
        for _ in range(100):
            filt.nativeEventFilter(
                b"windows_generic_MSG",
                make_msg(0x5001, WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK))
            filt.nativeEventFilter(b"windows_generic_MSG", None)
            filt.nativeEventFilter(b"windows_generic_MSG", object())
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [], "session watcher must not produce an error storm"
