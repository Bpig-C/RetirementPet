"""M8 autostart REAL registry integration (CONFORMANCE 13.1 WINDOWS_INTEGRATION).

Runs against the REAL HKCU Run key but under a SANDBOXED value name
(``RetirementPetTest-<uuid>``) so the user's actual autostart entry is
never touched.  Verifies: quoting of the frozen command, enable/disable/
repair round-trips, and that disabling only removes OUR value.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows registry test"),
    pytest.mark.skipif(
        os.environ.get("RP_RUN_NATIVE") != "1",
        reason="registry tier is opt-in: RP_RUN_NATIVE=1 python -m pytest tests/native"),
]

APP_NAME_SANDBOX = f"RetirementPetTest-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def backend():
    import winreg

    from retirement_pet.startup import RUN_KEY, WinRegBackend

    # Fresh Windows profiles are allowed to have no Run key.  The production
    # backend creates it when enabling autostart; create it here as well so the
    # foreign-value isolation assertion can exercise the real key directly.
    with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
            winreg.KEY_SET_VALUE):
        pass
    sandbox = WinRegBackend(app_name=APP_NAME_SANDBOX)
    yield sandbox
    # cleanup: remove ONLY our sandboxed value
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            "Software\\Microsoft\\Windows\\CurrentVersion\\Run",
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME_SANDBOX)
    except FileNotFoundError:
        pass


def test_real_registry_roundtrip(backend):
    command = '"C:\\Some Path With Spaces\\RetirementPet.exe" --startup'
    backend.write(command)
    assert backend.read() == command  # quoting survives the registry
    backend.delete()
    assert backend.read() is None


def test_backend_creates_a_missing_hkcu_key():
    """A fresh user profile need not already contain the configured key."""
    import winreg

    from retirement_pet.startup import WinRegBackend

    key_path = f"Software\\RetirementPetNativeTest-{uuid.uuid4().hex}"
    backend = WinRegBackend(app_name="autostart", key_path=key_path)
    try:
        backend.write('"C:\\RetirementPet.exe" --startup')
        assert backend.read() == '"C:\\RetirementPet.exe" --startup'
        backend.delete()
        assert backend.read() is None
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except FileNotFoundError:
            pass


def test_startup_manager_enable_disable_real(backend):
    from retirement_pet.startup import StartupManager

    manager = StartupManager(backend)
    assert manager.set_enabled(True) is True
    stored = backend.read()
    assert stored is not None
    assert stored.startswith('"')  # quoted path, spaces safe
    assert "--startup" in stored

    assert manager.is_enabled() is True
    assert manager.set_enabled(False) is True
    assert backend.read() is None  # only our own value removed


def test_repair_rewrites_stale_command(backend):
    from retirement_pet.startup import StartupManager, build_startup_command

    manager = StartupManager(backend)
    backend.write('"C:\\Old Location\\RetirementPet.exe" --startup')
    assert manager.needs_repair() is True  # stale path detected
    assert manager.repair() is True
    assert manager.needs_repair() is False
    assert backend.read() == build_startup_command()


def test_disable_never_touches_foreign_values(backend):
    """Deleting our entry must not disturb other Run values."""
    import winreg

    foreign = f"{APP_NAME_SANDBOX}-foreign"
    with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Software\\Microsoft\\Windows\\CurrentVersion\\Run",
            0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, foreign, 0, winreg.REG_SZ, "keep-me")
    try:
        from retirement_pet.startup import StartupManager

        manager = StartupManager(backend)
        manager.set_enabled(True)
        manager.set_enabled(False)
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                "Software\\Microsoft\\Windows\\CurrentVersion\\Run") as key:
            value, _ = winreg.QueryValueEx(key, foreign)
        assert value == "keep-me"
    finally:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                "Software\\Microsoft\\Windows\\CurrentVersion\\Run",
                0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, foreign)
