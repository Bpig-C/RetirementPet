"""Two clocks, one job each (DESIGN_V2 20, ADR-V2-017; M1).

- **VisualClock** drives frame composition for *visible, changing* content.
  Hidden, locked or session-disconnected states produce ZERO ticks - not a
  reduced rate (the old 2 FPS hidden mode still burned the render pipeline).
- **ServiceClock** runs the low-frequency business services (rhythm,
  schedules, countdown refresh, screen checks).  It keeps ticking while the
  pet is hidden so facts stay fresh; it never touches the render path.

Both accept a ``Clock`` and expose ``pump()`` so tests drive deterministic
ticks without an event loop.
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from retirement_pet.clock import Clock, SystemClock

logger = logging.getLogger(__name__)

DEFAULT_FPS = 16

Subscriber = Callable[[int], None]


class _Registration:
    __slots__ = ("interval_ms", "callback", "last_run_ms", "run_immediately")

    def __init__(self, interval_ms: int, callback: Subscriber, run_immediately: bool):
        self.interval_ms = max(1, int(interval_ms))
        self.callback = callback
        self.last_run_ms: int | None = None
        self.run_immediately = run_immediately


class VisualClock(QObject):
    """Frame clock; ticks only while visible and not suspended."""

    #: Base-rate tick with the monotonic time in ms.
    tick = Signal(int)

    def __init__(
        self,
        fps: int = DEFAULT_FPS,
        clock: Clock | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._clock = clock or SystemClock()
        self._fps = max(1, int(fps))
        self._visible = False  # an invisible pet must never compose frames
        self._suspended = False  # lock screen / session disconnected
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.CoarseTimer)
        self._timer.timeout.connect(self._on_timeout)
        self._apply_state()

    # -- lifecycle ---------------------------------------------------------

    def interval_ms(self) -> int:
        return max(15, int(1000 / max(1, self._fps)))

    def set_fps(self, fps: int) -> None:
        fps = max(1, int(fps))
        if fps == self._fps:
            return
        self._fps = fps
        if self._timer.isActive():
            self._timer.start(self.interval_ms())

    @property
    def fps(self) -> int:
        return self._fps

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)
        self._apply_state()

    def set_suspended(self, suspended: bool) -> None:
        """Lock screen, session disconnect or sleep."""
        self._suspended = bool(suspended)
        self._apply_state()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def _apply_state(self) -> None:
        should_run = self._visible and not self._suspended
        if should_run and not self._timer.isActive():
            self._timer.start(self.interval_ms())
        elif not should_run and self._timer.isActive():
            self._timer.stop()

    # -- driving -------------------------------------------------------------

    def _on_timeout(self) -> None:
        self.pump()

    def pump(self, now_ms: int | None = None) -> None:
        """One base tick.  Test-safe; respects visibility/suspension."""
        if not self._visible or self._suspended:
            return
        now = now_ms if now_ms is not None else self._clock.monotonic_ms()
        self.tick.emit(now)


class ServiceClock(QObject):
    """Low-frequency business clock; independent of the visual frame rate."""

    def __init__(
        self,
        clock: Clock | None = None,
        base_interval_ms: int = 1000,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._clock = clock or SystemClock()
        self._registrations: list[_Registration] = []
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.CoarseTimer)
        self._timer.setInterval(max(100, int(base_interval_ms)))
        self._timer.timeout.connect(self._on_timeout)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    # -- subscriptions -------------------------------------------------------

    def register(
        self, interval_ms: int, callback: Subscriber, run_immediately: bool = False
    ) -> Callable[[], None]:
        reg = _Registration(interval_ms, callback, run_immediately)
        self._registrations.append(reg)

        def _unsubscribe() -> None:
            try:
                self._registrations.remove(reg)
            except ValueError:
                pass

        return _unsubscribe

    # -- driving -------------------------------------------------------------

    def _on_timeout(self) -> None:
        self.pump()

    def pump(self, now_ms: int | None = None) -> None:
        """Run due subscribers.  Test-safe."""
        now = now_ms if now_ms is not None else self._clock.monotonic_ms()
        for reg in list(self._registrations):
            if reg.run_immediately and reg.last_run_ms is None:
                reg.last_run_ms = now
                try:
                    reg.callback(now)
                except Exception:  # noqa: BLE001
                    logger.exception("service clock subscriber failed")
                continue
            if reg.last_run_ms is None or now - reg.last_run_ms >= reg.interval_ms:
                reg.last_run_ms = now
                try:
                    reg.callback(now)
                except Exception:  # noqa: BLE001
                    logger.exception("service clock subscriber failed")
