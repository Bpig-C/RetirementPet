"""Optional, event-driven Windows hotkey for opening the Todo page.

``RegisterHotKey`` asks Windows to post one ``WM_HOTKEY`` message when the
configured chord is pressed.  It therefore adds no polling timer and no idle
CPU work.  Registration is deliberately best-effort: another application may
already own Ctrl+Alt+T, or the API may be unavailable.  Either case leaves the
desktop pet fully usable through its context and tray menus.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Protocol

from PySide6.QtCore import QAbstractNativeEventFilter

from retirement_pet.session_watch import parse_message

logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_T = 0x54

# Application-owned IDs may use 0x0000..0xBFFF.  ``RP`` is memorable in a
# debugger and does not enter the GlobalAddAtom-reserved range.
TODO_HOTKEY_ID = 0x5250
TODO_HOTKEY_MODIFIERS = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT


class HotkeyBackend(Protocol):
    """Small seam around user32, primarily for deterministic tests."""

    @property
    def available(self) -> bool: ...

    def register(self, hwnd: int, hotkey_id: int,
                 modifiers: int, virtual_key: int) -> bool: ...

    def unregister(self, hwnd: int, hotkey_id: int) -> bool: ...


class WindowsHotkeyBackend:
    """Lazy ``user32.RegisterHotKey`` adapter.

    Loading user32 only on Windows keeps importing the package safe on every
    supported development/test platform.
    """

    def __init__(self, platform: str | None = None):
        self._platform = sys.platform if platform is None else platform
        self._user32 = None

    @property
    def available(self) -> bool:
        return self._platform == "win32"

    def _api(self):
        if not self.available:
            return None
        if self._user32 is None:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.RegisterHotKey.argtypes = (
                ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint)
            user32.RegisterHotKey.restype = ctypes.c_int
            user32.UnregisterHotKey.argtypes = (ctypes.c_void_p, ctypes.c_int)
            user32.UnregisterHotKey.restype = ctypes.c_int
            self._user32 = user32
        return self._user32

    def register(self, hwnd: int, hotkey_id: int,
                 modifiers: int, virtual_key: int) -> bool:
        api = self._api()
        if api is None:
            return False
        return bool(api.RegisterHotKey(
            ctypes.c_void_p(hwnd), hotkey_id, modifiers, virtual_key))

    def unregister(self, hwnd: int, hotkey_id: int) -> bool:
        api = self._api()
        if api is None:
            return False
        return bool(api.UnregisterHotKey(ctypes.c_void_p(hwnd), hotkey_id))


class TodoHotkeyFilter(QAbstractNativeEventFilter):
    """Receive the registered Ctrl+Alt+T ``WM_HOTKEY`` message.

    The callback runs on Qt's UI thread because the event comes through the
    native Qt message pump.  ``attach`` and ``detach`` are idempotent so partial
    startup and repeated shutdown remain safe.
    """

    def __init__(self, on_activate, backend: HotkeyBackend | None = None):
        super().__init__()
        self._on_activate = on_activate
        self._backend = backend or WindowsHotkeyBackend()
        self._hwnd: int | None = None
        self._registered = False

    @property
    def registered(self) -> bool:
        return self._registered

    @property
    def supported(self) -> bool:
        return bool(self._backend.available)

    def attach(self, window) -> bool:
        """Register Ctrl+Alt+T against ``window``; failure is non-fatal."""
        if not self.supported:
            logger.debug("global Todo hotkey unavailable on this platform")
            return False
        try:
            hwnd = int(window.winId())
        except Exception:  # noqa: BLE001 - optional native integration
            logger.info("global Todo hotkey skipped: no native window")
            return False
        if not hwnd:
            logger.info("global Todo hotkey skipped: invalid native window")
            return False
        if self._registered and self._hwnd == hwnd:
            return True
        self.detach()
        try:
            registered = self._backend.register(
                hwnd, TODO_HOTKEY_ID, TODO_HOTKEY_MODIFIERS, VK_T)
        except Exception:  # noqa: BLE001 - shortcut must never block startup
            logger.exception("global Todo hotkey registration failed")
            return False
        if not registered:
            logger.warning(
                "Ctrl+Alt+T is unavailable; continuing without global Todo hotkey")
            return False
        self._hwnd = hwnd
        self._registered = True
        logger.info("global Todo hotkey registered: Ctrl+Alt+T")
        return True

    def detach(self) -> None:
        """Release the system-wide registration once, if it is owned."""
        if not self._registered or self._hwnd is None:
            self._registered = False
            self._hwnd = None
            return
        hwnd = self._hwnd
        self._registered = False
        self._hwnd = None
        try:
            if not self._backend.unregister(hwnd, TODO_HOTKEY_ID):
                logger.info("global Todo hotkey was already unavailable on detach")
        except Exception:  # noqa: BLE001 - process shutdown must continue
            logger.exception("global Todo hotkey unregister failed")

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if not self._registered or self._hwnd is None:
            return False
        try:
            if bytes(event_type) != b"windows_generic_MSG":
                return False
            parsed = parse_message(message)
            if parsed is None:
                return False
            hwnd, message_id, wparam = parsed
            if (hwnd != self._hwnd or message_id != WM_HOTKEY
                    or wparam != TODO_HOTKEY_ID):
                return False
            try:
                self._on_activate()
            except Exception:  # noqa: BLE001 - never break the message pump
                logger.exception("global Todo hotkey callback failed")
        except Exception:  # noqa: BLE001 - never break the message pump
            logger.exception("global Todo hotkey filter failed")
        return False
