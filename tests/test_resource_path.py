"""resource_path works in source mode and simulated frozen mode."""

from __future__ import annotations

import sys
from pathlib import Path


def test_source_mode_app_root_is_project_root():
    from retirement_pet.resource_path import app_root

    root = app_root()
    assert (root / "config" / "defaults.json").is_file()
    assert (root / "src" / "retirement_pet" / "resource_path.py").is_file()


def test_source_mode_asset_path():
    from retirement_pet.resource_path import asset_path

    assert asset_path("manifest.json").name == "manifest.json"
    assert asset_path("manifest.json").parent.name == "assets"


def test_frozen_mode_uses_meipass(tmp_path, monkeypatch):
    fake_internal = tmp_path / "_internal"
    (fake_internal / "assets").mkdir(parents=True)
    (fake_internal / "assets" / "manifest.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_internal), raising=False)
    try:
        from retirement_pet.resource_path import app_root, resource_path

        assert app_root() == fake_internal
        manifest = resource_path("assets", "manifest.json")
        assert manifest.is_file()
    finally:
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.delattr(sys, "frozen", raising=False)


def test_frozen_without_meipass_falls_back_to_exe_dir(tmp_path, monkeypatch):
    exe = tmp_path / "RetirementPet.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)
    try:
        from retirement_pet.resource_path import app_root

        assert app_root() == tmp_path
    finally:
        monkeypatch.delattr(sys, "frozen", raising=False)
