"""Settings persistence with safe defaults.

- missing fields fall back to defaults;
- unknown fields are preserved for forward compatibility;
- saves are atomic (temp file + ``os.replace``);
- a broken file is backed up as ``.broken-<timestamp>`` and defaults restored;
- no secrets ever live here.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from retirement_pet.resource_path import resource_path

logger = logging.getLogger(__name__)

DEFAULTS_FILE = ("config", "defaults.json")

# Embedded fallback used when config/defaults.json is missing (e.g. stripped
# builds).  Must stay in sync with config/defaults.json.
EMBEDDED_DEFAULTS: dict[str, Any] = {
    "schema_version": 1,
    "target_datetime": "2060-07-07T21:32:00",
    "always_on_top": True,
    "startup_enabled": False,
    "countdown_panel_expanded": True,
    "animation_fps": 16,
    "random_actions_enabled": True,
    "random_action_min_interval_s": 45,
    "random_action_max_interval_s": 120,
    "sound_enabled": True,
    "volume": 0.35,
    "work_idle_threshold_s": 120,
    "rest_reminder_minutes": 50,
    "meal_times": ["08:00", "12:00", "18:30"],
    "exercise_times": ["16:00"],
    "meeting_windows": [],
    "music_paths": [],
    "wheel_controls_volume": True,
    "click_through": False,
    "startup_delay_s": 8,
    "pomodoro_enabled": False,
    "character_onboarding_campaign": "",
}


def load_defaults() -> dict[str, Any]:
    """Defaults from bundled ``config/defaults.json``, embedded fallback."""
    try:
        path = resource_path(*DEFAULTS_FILE)
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001 - resource issues must never be fatal
        logger.warning("defaults.json unreadable, using embedded defaults")
    return dict(EMBEDDED_DEFAULTS)


class SettingsStore:
    """JSON settings file with default merging and atomic writes."""

    def __init__(self, path: Path, defaults: dict[str, Any] | None = None):
        self._path = Path(path)
        self._defaults = dict(defaults or load_defaults())
        self._data: dict[str, Any] = dict(self._defaults)
        self._validators: dict[str, Callable[[Any], Any]] = {
            "volume": self._clamp01,
            "animation_fps": self._clamp_fps,
            "meal_times": self._validate_time_list,
            "exercise_times": self._validate_time_list,
        }

    # -- persistence ------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        merged = dict(self._defaults)
        if self._path.is_file():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("settings root is not an object")
            except Exception as exc:  # noqa: BLE001
                self._backup_broken(exc)
                raw = {}
            for key, value in raw.items():
                merged[key] = self._validate(key, value)
        self._data = merged
        return dict(self._data)

    def _backup_broken(self, exc: Exception) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self._path.with_name(self._path.name + f".broken-{stamp}")
        try:
            os.replace(self._path, backup)
            logger.error("settings corrupted (%s); backed up to %s", exc, backup.name)
        except OSError:
            logger.error("settings corrupted (%s) and not backupable", exc)

    def save(self) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=".settings-", suffix=".tmp"
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
        except Exception:  # noqa: BLE001 - persistence failure must not crash
            logger.exception("failed to save settings")
            return False

    # -- access -----------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = self._validate(key, value)

    def update(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            self.set(key, value)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def reset_to_defaults(self) -> None:
        self._data = dict(self._defaults)

    # -- validation -------------------------------------------------------

    def _validate(self, key: str, value: Any) -> Any:
        validator = self._validators.get(key)
        if validator is None:
            return value
        try:
            return validator(value)
        except Exception:  # noqa: BLE001 - invalid value falls back to default
            logger.warning("invalid settings value for %r; using default", key)
            return self._defaults.get(key)

    @staticmethod
    def _clamp01(value: Any) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _clamp_fps(value: Any) -> int:
        return max(4, min(60, int(value)))

    @staticmethod
    def _validate_time_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("expected list")
        result = []
        for item in value:
            text = str(item).strip()
            parts = text.split(":")
            if len(parts) != 2:
                raise ValueError(f"bad time {item!r}")
            hh, mm = int(parts[0]), int(parts[1])
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                raise ValueError(f"bad time {item!r}")
            result.append(f"{hh:02d}:{mm:02d}")
        return result
