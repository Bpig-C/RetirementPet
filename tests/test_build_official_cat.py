"""Deterministic v1.0.1 official-pack builder contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from retirement_pet.models import ActionId
from retirement_pet.ui.renderer import CatRenderer
from scripts.build_official_cat import (
    BODY_PIXELS,
    FROZEN_LEGACY_OUTPUT,
    THUMBNAIL_PIXELS,
    build_manifest,
    deterministic_zip,
    render_action_frames,
    validate_output_path,
)


def test_builder_outputs_body_only_512_and_real_128_thumbnail(qt_application):
    renderer = CatRenderer()
    body = render_action_frames(renderer, ActionId.WORK, (0,))[0]
    thumbnail = render_action_frames(
        renderer, ActionId.IDLE, (0,), pixel_size=THUMBNAIL_PIXELS)[0]
    body_image = QImage.fromData(body)
    thumbnail_image = QImage.fromData(thumbnail)
    assert body_image.size().toTuple() == (BODY_PIXELS, BODY_PIXELS)
    assert thumbnail_image.size().toTuple() == (
        THUMBNAIL_PIXELS, THUMBNAIL_PIXELS)
    assert all(
        body_image.pixelColor(x, y).alpha() == 0
        for y in range(32) for x in range(body_image.width())
    )


def test_builder_manifest_and_archive_are_deterministic(qt_application):
    renderer = CatRenderer()
    files = {
        "assets/idle_0.png": render_action_frames(
            renderer, ActionId.IDLE, (0,))[0],
        "assets/thumbnail.png": render_action_frames(
            renderer, ActionId.IDLE, (0,),
            pixel_size=THUMBNAIL_PIXELS)[0],
    }
    # Build a complete small fixture by pointing every planned frame at its
    # deterministic generated body.  The production main path uses distinct
    # frames; this test concerns metadata and ZIP determinism only.
    from scripts.build_official_cat import FRAME_PLAN

    idle = files["assets/idle_0.png"]
    for semantic, times in FRAME_PLAN.items():
        slug = semantic.removeprefix("core.")
        for index in range(len(times)):
            files.setdefault(f"assets/{slug}_{index}.png", idle)
    manifest = build_manifest(files)
    first = deterministic_zip(manifest, files)
    second = deterministic_zip(manifest, files)
    assert first == second
    assert manifest["package"]["version"] == "1.0.1"
    assert manifest["characters"][0]["geometry"]["logical_canvas"] == {
        "width": 256, "height": 256,
    }


def test_builder_refuses_to_overwrite_frozen_v100_media():
    with pytest.raises(ValueError, match="frozen official v1.0.0"):
        validate_output_path(Path(FROZEN_LEGACY_OUTPUT))
