"""Autostart via the current user's HKCU Run key.

Only the app's own ``RetirementPet`` value is ever created or deleted.  The
registry backend is injectable so tests never touch the real registry.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol, Sequence

from retirement_pet import APP_NAME
from retirement_pet.resource_path import package_root

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class RegistryBackend(Protocol):
    """Minimal registry adapter used by :class:`StartupManager`."""

    def read(self) -> str | None: ...

    def write(self, value: str) -> None: ...

    def delete(self) -> None: ...


class WinRegBackend:
    """Real HKCU backend.  Never touches HKLM or other values."""

    def __init__(self, app_name: str = APP_NAME, key_path: str = RUN_KEY):
        self._app_name = app_name
        self._key_path = key_path

    def _open(self, access: int):
        import winreg  # imported lazily: no import-time registry access

        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._key_path, 0, access)

    def _create(self, access: int):
        import winreg

        return winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, self._key_path, 0, access)

    def read(self) -> str | None:
        if sys.platform != "win32":
            return None
        try:
            import winreg

            with self._open(winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, self._app_name)
                return str(value)
        except Exception:  # noqa: BLE001 - missing value / reg unavailable
            return None

    def write(self, value: str) -> None:
        if sys.platform != "win32":
            raise OSError("autostart requires Windows")
        import winreg

        # A fresh or policy-managed user profile may not have a Run key yet.
        # Creating the key is scoped to HKCU; only our named value is written.
        with self._create(winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, self._app_name, 0, winreg.REG_SZ, value)

    def delete(self) -> None:
        if sys.platform != "win32":
            return
        import winreg

        try:
            with self._open(winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self._app_name)
        except FileNotFoundError:
            pass


class MemoryBackend:
    """In-memory backend for tests and non-Windows dry runs."""

    def __init__(self, initial: str | None = None):
        self.values: dict[str, str] = {}
        if initial is not None:
            self.values[APP_NAME] = initial
        self.write_calls: list[str] = []
        self.delete_calls: list[str] = []

    def read(self) -> str | None:
        return self.values.get(APP_NAME)

    def write(self, value: str) -> None:
        self.values[APP_NAME] = value
        self.write_calls.append(value)

    def delete(self) -> None:
        self.delete_calls.append(APP_NAME)
        self.values.pop(APP_NAME, None)


def build_startup_command() -> str:
    """Command string stored in HKCU Run.

    - frozen: ``"<exe>" --startup``
    - source: prefer ``pythonw.exe`` (no console) running the package main.
    """
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" --startup'
    exe = Path(sys.executable).resolve()
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe
    entry = package_root() / "main.py"
    return f'"{runner}" "{entry}" --startup'


class StartupManager:
    def __init__(self, backend: RegistryBackend | None = None):
        self._backend = backend or WinRegBackend()

    # -- pure helpers -----------------------------------------------------

    @staticmethod
    def command_exe(command: str | None) -> Path | None:
        """Extract the leading quoted executable path from a Run command."""
        if not command:
            return None
        text = command.strip()
        if not text:
            return None
        if text.startswith('"'):
            end = text.find('"', 1)
            if end <= 0:
                return None
            return Path(text[1:end])
        return Path(text.split()[0])

    @classmethod
    def command_matches_current(cls, command: str | None) -> bool:
        """True when the stored command is EXACTLY what we would write now.

        Comparing against build_startup_command() (not bare sys.executable)
        keeps source runs stable: pythonw/python produce equivalent but
        textually different commands, and repair rewrites either form.
        """
        if not command:
            return False
        return command.strip() == build_startup_command().strip()

    # -- registry operations ----------------------------------------------

    def is_enabled(self) -> bool:
        return bool(self._backend.read())

    def set_enabled(self, enabled: bool) -> bool:
        try:
            if enabled:
                self._backend.write(build_startup_command())
            else:
                self._backend.delete()
            return True
        except Exception:  # noqa: BLE001 - registry may be unavailable
            return False

    def needs_repair(self) -> bool:
        """Enabled but pointing somewhere else (install dir moved)."""
        value = self._backend.read()
        return bool(value) and not self.command_matches_current(value)

    def repair(self) -> bool:
        return self.set_enabled(True)


def fake_registry_for_tests(initial: str | None = None) -> MemoryBackend:
    return MemoryBackend(initial)


__all__ = [
    "RUN_KEY",
    "RegistryBackend",
    "WinRegBackend",
    "MemoryBackend",
    "StartupManager",
    "build_startup_command",
    "fake_registry_for_tests",
]
