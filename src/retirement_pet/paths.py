"""User data directory resolution.

Runtime data (settings, state, logs) lives under ``%APPDATA%\\RetirementPet``.
Never write runtime data next to the EXE.  ``RETIREMENT_PET_DATA_DIR`` may
override the location for tests and portable use.
"""

from __future__ import annotations

import os
from pathlib import Path

from retirement_pet import APP_NAME

ENV_OVERRIDE = "RETIREMENT_PET_DATA_DIR"


def user_data_dir() -> Path:
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / "AppData" / "Roaming" / APP_NAME


def ensure_user_data_dir(base: Path | None = None) -> Path:
    """Return (and create if needed) the user data directory."""
    target = base if base is not None else user_data_dir()
    target.mkdir(parents=True, exist_ok=True)
    return target


def settings_file(base: Path | None = None) -> Path:
    return (base if base is not None else user_data_dir()) / "settings.json"


def state_file(base: Path | None = None) -> Path:
    return (base if base is not None else user_data_dir()) / "state.json"


def log_dir(base: Path | None = None) -> Path:
    return (base if base is not None else user_data_dir()) / "logs"


__all__ = [
    "ENV_OVERRIDE",
    "user_data_dir",
    "ensure_user_data_dir",
    "settings_file",
    "state_file",
    "log_dir",
]
