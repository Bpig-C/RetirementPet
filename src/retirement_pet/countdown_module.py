"""RetirementCountdownModule: the built-in countdown module (DESIGN_V2 9).

The retirement target is USER global data.  It is owned by this module -
never by a character or pack - so switching characters, upgrading packs or
uninstalling content can never move the target.  Configuration sources are
only: user, module default, engine default (DESIGN_V2 9.1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol

from retirement_pet.countdown import CountdownSnapshot, compute_countdown, parse_target

#: module default target (also the v1 default; migrated user values win)
DEFAULT_TARGET_TEXT = "2060-07-07T21:32:00"


class _SettingsView(Protocol):
    def get(self, key: str, default=None): ...


class RetirementCountdownModule:
    """Computes countdown snapshots from the user-owned retirement target."""

    def __init__(self, settings: _SettingsView, clock: Callable[[], datetime]):
        self._settings = settings
        self._clock = clock
        self._last_snapshot: CountdownSnapshot | None = None
        self._last_target_text: str | None = None

    @property
    def target(self) -> datetime:
        """The effective target: user value, else module default.

        An invalid user value falls back to the default and is ignored -
        the module never crashes on user input and never invents a target.
        """
        text = str(self._settings.get("target_datetime", DEFAULT_TARGET_TEXT))
        try:
            return parse_target(text)
        except ValueError:
            return parse_target(DEFAULT_TARGET_TEXT)

    def refresh(self) -> CountdownSnapshot:
        """Recompute once per service tick (never per animation frame)."""
        self._last_target_text = str(
            self._settings.get("target_datetime", DEFAULT_TARGET_TEXT))
        self._last_snapshot = compute_countdown(self._clock(), self.target)
        return self._last_snapshot

    @property
    def snapshot(self) -> CountdownSnapshot | None:
        return self._last_snapshot
