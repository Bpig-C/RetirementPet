"""Stable Todo storage errors and availability state.

Messages deliberately contain no database path, SQL text, task id, title or
due date.  UI code may branch on ``reason``; logs may use only the same coarse
fields plus SQLite's numeric/name identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass


class SchemaTooNew(RuntimeError):
    """A newer build owns this store; callers must leave every file untouched."""

    def __init__(self, found: int, supported: int):
        self.found = int(found)
        self.supported = int(supported)
        super().__init__(
            f"task store schema {self.found} is newer than supported "
            f"schema {self.supported}")


class TaskStoreUnavailable(RuntimeError):
    """Stable fail-closed boundary for a store that must not be replaced."""

    def __init__(self, reason: str = "unavailable", *, stage: str | None = None,
                 sqlite_code: int | None = None,
                 sqlite_name: str | None = None):
        self.reason = str(reason)
        self.stage = stage
        self.sqlite_code = sqlite_code
        self.sqlite_name = sqlite_name
        super().__init__(f"task store unavailable ({self.reason})")


@dataclass(frozen=True)
class TaskStoreAvailability:
    available: bool
    reason: str | None = None

    @classmethod
    def ready(cls) -> "TaskStoreAvailability":
        return cls(True, None)

    @classmethod
    def unavailable(cls, reason: str) -> "TaskStoreAvailability":
        return cls(False, str(reason))
