"""AssetBundle: manifest-driven visuals with graceful missing-file fallback."""

from __future__ import annotations

import json

import pytest


def write_png(path, width=16, height=16) -> None:
    from PySide6.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(200, 100, 100, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(path), "PNG")


def make_manifest(root, spec: dict) -> None:
    root.joinpath("manifest.json").write_text(
        json.dumps(spec, ensure_ascii=False), encoding="utf-8"
    )


def test_missing_manifest_is_unavailable(tmp_path):
    from retirement_pet.assets import AssetBundle

    bundle = AssetBundle(root=tmp_path)
    assert not bundle.available
    assert bundle.action_visual("idle") is None
    assert bundle.canvas_size() == (256, 256)


def test_missing_files_fall_back(tmp_path):
    from retirement_pet.assets import AssetBundle

    make_manifest(tmp_path, {
        "actions": {"idle": {"mode": "parts", "attachments": []}},
        "parts": {"body": {"path": "nope/body.png"}},
    })
    bundle = AssetBundle(root=tmp_path)
    assert bundle.available
    assert bundle.action_visual("idle") is None  # required parts missing


def test_parts_visual_loads(qt_application, tmp_path):
    from retirement_pet.assets import AssetBundle

    write_png(tmp_path / "sprites" / "common" / "body.png")
    write_png(tmp_path / "sprites" / "common" / "head.png")
    write_png(tmp_path / "sprites" / "common" / "face_open.png")
    write_png(tmp_path / "sprites" / "common" / "laptop.png")
    make_manifest(tmp_path, {
        "required_parts": ["body", "head", "face_open"],
        "parts": {
            "body": {"path": "sprites/common/body.png"},
            "head": {"path": "sprites/common/head.png"},
            "face_open": {"path": "sprites/common/face_open.png"},
            "laptop": {"path": "sprites/common/laptop.png"},
        },
        "actions": {"work": {"mode": "parts", "attachments": ["laptop", "missing_thing"]}},
    })
    bundle = AssetBundle(root=tmp_path)
    visual = bundle.action_visual("work")
    assert visual is not None
    assert "body" in visual.parts
    assert visual.attachments == ("laptop",)  # missing attachment dropped


def test_sequence_drops_missing_frames(qt_application, tmp_path):
    from retirement_pet.assets import AssetBundle

    write_png(tmp_path / "sprites" / "eat" / "000.png")
    # 001 deliberately missing
    write_png(tmp_path / "sprites" / "eat" / "002.png")
    make_manifest(tmp_path, {
        "actions": {
            "eat": {
                "mode": "sequence",
                "frames": ["sprites/eat/000.png", "sprites/eat/001.png", "sprites/eat/002.png"],
                "fps": 6,
                "loop": True,
            }
        }
    })
    bundle = AssetBundle(root=tmp_path)
    visual = bundle.action_visual("eat")
    assert visual is not None
    assert len(visual.frames) == 2  # missing frame skipped, no crash


def test_sequence_all_missing_falls_back(tmp_path):
    from retirement_pet.assets import AssetBundle

    make_manifest(tmp_path, {
        "actions": {"eat": {"mode": "sequence", "frames": ["a.png", "b.png"], "fps": 6}}
    })
    bundle = AssetBundle(root=tmp_path)
    assert bundle.action_visual("eat") is None


def test_renderer_uses_sprites_when_available(qt_application, tmp_path):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from retirement_pet.assets import AssetBundle
    from retirement_pet.models import ActionId, LifeStage, RenderSnapshot
    from retirement_pet.ui.renderer import CatRenderer

    write_png(tmp_path / "sprites" / "common" / "body.png")
    write_png(tmp_path / "sprites" / "common" / "head.png")
    write_png(tmp_path / "sprites" / "common" / "face_open.png")
    write_png(tmp_path / "sprites" / "common" / "tail.png")
    write_png(tmp_path / "sprites" / "common" / "shadow.png")
    make_manifest(tmp_path, {
        "required_parts": ["body", "head", "face_open"],
        "parts": {
            "body": {"path": "sprites/common/body.png"},
            "head": {"path": "sprites/common/head.png"},
            "face_open": {"path": "sprites/common/face_open.png"},
            "face_closed": {"path": "sprites/common/face_closed.png"},
            "tail": {"path": "sprites/common/tail.png"},
            "shadow": {"path": "sprites/common/shadow.png"},
        },
        "actions": {"idle": {"mode": "parts", "attachments": []}},
    })
    bundle = AssetBundle(root=tmp_path)
    renderer = CatRenderer(bundle)

    image = QImage(232, 236, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    snap = RenderSnapshot(action=ActionId.IDLE, stage=LifeStage.YOUNG,
                          elapsed_ms=500, frame=0, time_ms=1000,
                          assets_available=True)
    renderer.render(painter, QRectF(0, 0, 232, 236), snap)
    painter.end()
    assert not image.isNull()

    visual = bundle.action_visual("idle")
    assert visual is not None and "face_open" in visual.parts


def test_broken_manifest_falls_back(tmp_path):
    from retirement_pet.assets import AssetBundle

    tmp_path.joinpath("manifest.json").write_text("{broken", encoding="utf-8")
    bundle = AssetBundle(root=tmp_path)
    assert not bundle.available


def test_real_assets_manifest_parses():
    from retirement_pet.assets import AssetBundle

    bundle = AssetBundle()  # project assets/ dir
    assert bundle.available
    assert bundle.canvas_size() == (256, 256)
    # No PNGs ship yet - every action must gracefully return None.
    assert bundle.action_visual("idle") is None
    assert bundle.action_visual("eat") is None
