"""Startup command generation and registry adapter behavior.

Never touches the real registry: all tests use MemoryBackend.
"""

from __future__ import annotations

import sys
from pathlib import Path

from retirement_pet.startup import (
    MemoryBackend,
    StartupManager,
    build_startup_command,
)


def test_source_command_uses_package_main():
    cmd = build_startup_command()
    assert cmd.endswith('main.py" --startup')
    assert cmd.startswith('"')
    assert "--startup" in cmd


def test_frozen_command_uses_executable(tmp_path, monkeypatch):
    exe = tmp_path / "RetirementPet.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)
    try:
        cmd = build_startup_command()
    finally:
        monkeypatch.delattr(sys, "frozen", raising=False)
    assert cmd == f'"{exe.resolve()}" --startup'


def test_main_can_report_exact_startup_command(tmp_path, monkeypatch):
    from retirement_pet.main import main

    output = tmp_path / "startup-command.txt"
    monkeypatch.setattr(
        sys, "argv", ["retirement-pet", "--startup-command-out", str(output)])
    assert main() == 0
    assert output.read_text(encoding="utf-8") == build_startup_command()


def test_command_exe_extracts_quoted_path():
    exe = StartupManager.command_exe('"C:\\Apps\\Pet.exe" --startup')
    assert exe == Path("C:\\Apps\\Pet.exe")
    assert StartupManager.command_exe("") is None
    assert StartupManager.command_exe(None) is None


APP_VALUE = "RetirementPet"


def test_enable_disable_roundtrip_with_fake_backend():
    backend = MemoryBackend()
    manager = StartupManager(backend)
    assert not manager.is_enabled()

    assert manager.set_enabled(True)
    assert manager.is_enabled()
    stored = backend.read()
    assert stored.endswith("--startup")

    assert manager.set_enabled(False)
    assert not manager.is_enabled()
    assert APP_VALUE not in backend.values


def test_delete_only_removes_own_value():
    backend = MemoryBackend()
    backend.values["SomeOtherApp"] = "keep me"
    manager = StartupManager(backend)
    manager.set_enabled(True)
    manager.set_enabled(False)
    assert backend.values.get("SomeOtherApp") == "keep me"


def test_needs_repair_when_path_differs(tmp_path, monkeypatch):
    backend = MemoryBackend(initial=f'"{tmp_path / "old" / "Pet.exe"}" --startup')
    manager = StartupManager(backend)
    current = tmp_path / "new" / "Pet.exe"
    monkeypatch.setattr(sys, "executable", str(current), raising=False)
    try:
        assert manager.needs_repair()
        # a match means the stored command equals what we would write now
        from retirement_pet.startup import build_startup_command
        backend.values["RetirementPet"] = build_startup_command()
        assert not manager.needs_repair()
    finally:
        monkeypatch.delattr(sys, "executable", raising=False)


def test_repair_rewrites_command(tmp_path, monkeypatch):
    backend = MemoryBackend(initial='"C:\\old\\Pet.exe" --startup')
    manager = StartupManager(backend)
    current = tmp_path / "Pet.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(current), raising=False)
    try:
        assert manager.needs_repair()
        assert manager.repair()
        assert not manager.needs_repair()
    finally:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.delattr(sys, "executable", raising=False)
