"""Unified resource lookup that works in source and PyInstaller frozen mode.

Importing this module has no side effects; all resolution is lazy.
"""

from __future__ import annotations

import sys
from pathlib import Path

_MEIPASS_ATTR = "_MEIPASS"


def app_root() -> Path:
    """Directory that contains bundled read-only resources.

    - frozen (PyInstaller): ``sys._MEIPASS`` (onedir ``_internal`` folder);
    - source: the project root (this file lives at ``src/retirement_pet/``).
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, _MEIPASS_ATTR, None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    return app_root().joinpath(*parts)


def asset_path(*parts: str) -> Path:
    return resource_path("assets", *parts)


def package_root() -> Path:
    """Directory of the ``retirement_pet`` package itself (source or frozen)."""
    return Path(__file__).resolve().parent


def exe_path() -> Path:
    """Path of the running executable (frozen) or interpreter (source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(sys.executable).resolve()


__all__ = ["app_root", "resource_path", "asset_path", "package_root", "exe_path"]
