"""Windows session/sleep watcher (DESIGN_V2 20; P0-E remediation).

Locks the visual clock when the session is locked, disconnected or the
machine sleeps, and releases it on return.

Native event contract (PySide6 on Windows): ``nativeEventFilter``
receives ``(b"windows_generic_MSG", Shiboken.VoidPtr)`` where the void
pointer IS the address of a win32 ``MSG`` struct.  Reading ``.message`` /
``.wParam`` off the VoidPtr raises AttributeError (the historical error
storm); the pointer must be converted to an integer address and mapped
onto a ctypes MSG (P0-E).

Handled messages:
- WM_POWERBROADCAST: PBT_APMSUSPEND / PBT_APMRESUMEAUTOMATIC
- WM_WTSSESSION_CHANGE: lock/unlock, remote connect/disconnect

``WTSRegisterSessionNotification`` must be bound to a top-level HWND; the
filter ignores messages until ``attach(window)`` succeeds.  On platforms
or sessions where registration is unavailable (offscreen tests), the
watcher degrades to a no-op and callers can drive ``set_suspended``
manually - correctness never depends on it.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging

from PySide6.QtCore import QAbstractNativeEventFilter

logger = logging.getLogger(__name__)

WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMEAUTOMATIC = 0x0012

WM_WTSSESSION_CHANGE = 0x0241
WTS_REMOTE_CONNECT = 0x0003
WTS_REMOTE_DISCONNECT = 0x0004
WTS_SESSION_LOCK = 0x0007
WTS_SESSION_UNLOCK = 0x0008

NOTIFY_FOR_THIS_SESSION = 0

_SUSPEND_EVENTS = {PBT_APMSUSPEND, WTS_REMOTE_DISCONNECT, WTS_SESSION_LOCK}
_RESUME_EVENTS = {PBT_APMRESUMEAUTOMATIC, WTS_REMOTE_CONNECT, WTS_SESSION_UNLOCK}

_HANDLED = (WM_POWERBROADCAST, WM_WTSSESSION_CHANGE)

#: total parse failures tolerated before the filter stops logging (an
#: unloggable message stream must not become an error storm either)
_MAX_PARSE_FAILURES = 5


class _MSG(ctypes.Structure):
    """win32 MSG layout (fixed by ABI; version independent)."""

    _fields_ = [
        ("hwnd", wt.HWND),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", wt.DWORD),
        ("pt", wt.POINT),
    ]


def _register_session_notification(hwnd: int) -> bool:
    try:
        wtsapi = ctypes.WinDLL("wtsapi32")
        return bool(wtsapi.WTSRegisterSessionNotification(
            ctypes.c_void_p(hwnd), NOTIFY_FOR_THIS_SESSION
        ))
    except Exception:  # noqa: BLE001 - degradation is acceptable
        logger.info("WTSRegisterSessionNotification unavailable;"
                    " lock/unlock events will not suspend the visual clock")
        return False


def _voidptr_address(message) -> int | None:
    """Best-effort VoidPtr -> int address across shiboken versions."""
    if message is None:
        return None
    try:
        return int(message)  # shiboken6 VoidPtr supports int()
    except (TypeError, ValueError):
        pass
    to_long = getattr(message, "toLongLong", None) \
        or getattr(message, "to_longlong", None)
    if to_long is not None:
        try:
            return int(to_long())
        except (TypeError, ValueError):
            return None
    return None


def parse_message(message):
    """Extract ``(hwnd, message_id, wparam)`` from a native MSG pointer.

    Returns None for anything unreadable (null pointer, wrong platform,
    short buffer) - callers treat None as "ignore this event", never as an
    error.
    """
    address = _voidptr_address(message)
    if not address or address < 0x10000:  # null/near-null pointer guard
        return None
    try:
        msg = _MSG.from_address(address)
        return int(msg.hwnd or 0), int(msg.message), int(msg.wParam)
    except Exception:  # noqa: BLE001 - never break the message pump
        return None


class SessionWatchFilter(QAbstractNativeEventFilter):
    """Translate native session messages into a suspend callback.

    ``on_change(suspended: bool)`` is called with True on lock/sleep/
    remote-disconnect and False on return.
    """

    def __init__(self, on_change):
        super().__init__()
        self._on_change = on_change
        self._hwnd: int | None = None
        self._suspended = False
        self._parse_failures = 0

    @property
    def suspended(self) -> bool:
        return self._suspended

    def attach(self, window) -> None:
        """Register for session notifications on the pet window's HWND."""
        try:
            hwnd = int(window.winId())
        except Exception:  # noqa: BLE001 - no native window (offscreen)
            return
        if hwnd == self._hwnd:
            return
        self._hwnd = hwnd
        _register_session_notification(hwnd)

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if self._hwnd is None:
            return False
        try:
            if bytes(event_type) != b"windows_generic_MSG":
                return False
            parsed = parse_message(message)
            if parsed is None:
                self._parse_failures += 1
                if self._parse_failures == _MAX_PARSE_FAILURES:
                    logger.warning("session watch: %d unreadable native"
                                   " messages; ignoring further parse"
                                   " failures", self._parse_failures)
                return False
            self._parse_failures = 0
            hwnd, message_id, wparam = parsed
            if hwnd != self._hwnd:
                return False
            if message_id not in _HANDLED:
                return False
            if message_id == WM_POWERBROADCAST and wparam in _SUSPEND_EVENTS:
                self._set_suspended(True)
            elif (message_id == WM_POWERBROADCAST
                  and wparam in _RESUME_EVENTS):
                self._set_suspended(False)
            elif (message_id == WM_WTSSESSION_CHANGE
                  and wparam in _SUSPEND_EVENTS):
                self._set_suspended(True)
            elif (message_id == WM_WTSSESSION_CHANGE
                  and wparam in _RESUME_EVENTS):
                self._set_suspended(False)
        except Exception:  # noqa: BLE001 - never break the message pump
            logger.exception("session watch filter failed")
        return False

    def _set_suspended(self, suspended: bool) -> None:
        if suspended == self._suspended:
            return
        self._suspended = suspended
        logger.info("session %s", "suspended (lock/sleep/remote-disconnect)"
                    if suspended else "resumed")
        try:
            self._on_change(suspended)
        except Exception:  # noqa: BLE001
            logger.exception("session suspend/resume callback failed")
