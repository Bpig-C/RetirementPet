"""System idle detection via GetLastInputInfo (design 8.3, ADR-004).

Only the *idle duration* is queried - never which keys, text or windows.
On any failure the result is ``None`` (unknown), which callers must treat as
"not idle" rather than long-idle.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import Structure, byref
from ctypes.wintypes import DWORD, UINT
from typing import Callable

logger = logging.getLogger(__name__)


class _LASTINPUTINFO(Structure):
    _fields_ = [
        ("cbSize", UINT),
        ("dwTime", DWORD),
    ]


def _last_input_idle_seconds() -> float | None:
    """Idle seconds from GetLastInputInfo, or None when unavailable."""
    try:
        info = _LASTINPUTINFO()
        info.cbSize = UINT(ctypes.sizeof(_LASTINPUTINFO))
        if not ctypes.windll.user32.GetLastInputInfo(byref(info)):
            return None
        now_tick = int(ctypes.windll.kernel32.GetTickCount()) & 0xFFFFFFFF
        idle_ms = (now_tick - int(info.dwTime)) & 0xFFFFFFFF
        if idle_ms > 0x7FFFFFFF:  # tick wraparound guard
            return None
        return idle_ms / 1000.0
    except Exception:  # noqa: BLE001 - probe must never raise
        logger.debug("GetLastInputInfo failed", exc_info=True)
        return None


IdleProvider = Callable[[], "float | None"]


class ActivityMonitor:
    """Polls system idle time.  The provider is injectable for tests."""

    def __init__(self, idle_provider: IdleProvider | None = None):
        self._provider = idle_provider or _last_input_idle_seconds
        self._available: bool | None = None

    def idle_seconds(self) -> float | None:
        value = self._provider()
        if value is None:
            if self._available is not False:
                logger.warning("idle detection unavailable; assuming user active")
                self._available = False
            return None
        self._available = True
        return max(0.0, float(value))

    @property
    def is_available(self) -> bool | None:
        return self._available

    def is_user_active(self, threshold_s: float = 5.0) -> bool | None:
        """True when recent input; None when idle time unknown."""
        idle = self.idle_seconds()
        if idle is None:
            return None
        return idle <= threshold_s
