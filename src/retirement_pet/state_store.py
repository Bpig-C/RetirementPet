"""Runtime state persistence (window position, per-day reminder bookkeeping).

Lives in ``state.json`` inside the user data directory.  Same atomic-write
and broken-backup discipline as settings.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class StateStore:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        if self._path.is_file():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
            except Exception as exc:  # noqa: BLE001
                self._backup_broken(exc)
                self._data = {}
        return dict(self._data)

    def _backup_broken(self, exc: Exception) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self._path.with_name(self._path.name + f".broken-{stamp}")
        try:
            os.replace(self._path, backup)
            logger.error("state corrupted (%s); backed up to %s", exc, backup.name)
        except OSError:
            logger.error("state corrupted (%s) and not backupable", exc)

    def save(self) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=".state-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._data, handle, ensure_ascii=False, indent=2)
                os.replace(tmp_name, self._path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            return True
        except Exception:  # noqa: BLE001
            logger.exception("failed to save state")
            return False

    # -- access -----------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    # -- reminder bookkeeping ---------------------------------------------

    @staticmethod
    def day_key(now: datetime) -> str:
        return now.strftime("%Y-%m-%d")

    def reminder_entry(self, kind: str, now: datetime) -> dict[str, Any]:
        """Per-day record for a reminder kind ('meal', 'exercise', ...)."""
        day = self.day_key(now)
        days: dict[str, Any] = self._data.setdefault("reminders", {})
        day_map: dict[str, Any] = days.setdefault(day, {})
        entry = day_map.get(kind)
        if not isinstance(entry, dict):
            entry = {}
            day_map[kind] = entry
        return entry

    def prune_old_reminder_days(self, now: datetime, keep_days: int = 7) -> None:
        days = self._data.get("reminders")
        if not isinstance(days, dict):
            return
        try:
            current = datetime.strptime(self.day_key(now), "%Y-%m-%d")
        except ValueError:
            return
        stale = []
        for key in days:
            try:
                day = datetime.strptime(key, "%Y-%m-%d")
            except ValueError:
                continue
            if (current - day).days > keep_days:
                stale.append(key)
        for key in stale:
            days.pop(key, None)
