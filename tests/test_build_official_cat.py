"""Deterministic v1.0.1 official-pack builder contracts."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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
    assert manifest["package"]["version"] == "1.0.2"
    assert manifest["characters"][0]["geometry"]["logical_canvas"] == {
        "width": 256, "height": 256,
    }


def test_builder_refuses_to_overwrite_frozen_v100_media():
    with pytest.raises(ValueError, match="frozen official v1.0.0"):
        validate_output_path(Path(FROZEN_LEGACY_OUTPUT))


# -- V13-07: seamless idle/work sequences -----------------------------------------


def _pack_frames(pack_path, prefix):
    import zipfile

    with zipfile.ZipFile(pack_path) as archive:
        manifest = json.loads(archive.read("petpack.json"))
        names = sorted(
            name for name in archive.namelist()
            if name.startswith(prefix))
        images = {name: QImage.fromData(archive.read(name))
                  for name in names}
    return manifest, images


def test_v13_07_official_pack_has_seamless_sequences():
    """The 1.0.2 revision ships a 16-frame 3.2s idle loop and a 4-frame
    work loop - both seamless by construction (shared period sampling)."""
    pack_path = ROOT / "assets" / "petpack" / \
        "retirement-cat-official-1.0.2.petpack"
    manifest, images = _pack_frames(pack_path, "assets/idle_")
    idle = next(a for a in manifest["actions"]
                if a["semantic"] == "core.idle")
    renderer_spec = idle["lifecycle"]["loop"]["renderer"]
    assert renderer_spec["type"] == "sequence"
    frames = renderer_spec["frames"]
    assert 8 <= len(frames) <= 16
    total_ms = sum(frame["duration_ms"] for frame in frames)
    assert 2000 <= total_ms <= 4000

    work = next(a for a in manifest["actions"]
                if a["semantic"] == "core.work")
    work_spec = work["lifecycle"]["loop"]["renderer"]
    assert work_spec["type"] == "sequence"
    assert 2 <= len(work_spec["frames"]) <= 16

    # frames really differ across the sequence (not one still repeated)
    def sampled_diff(left, right):
        return sum(
            1 for x in range(0, left.width(), 4)
            for y in range(0, left.height(), 4)
            if left.pixel(x, y) != right.pixel(x, y))

    ordered = [images[f"assets/idle_{i}.png"] for i in range(len(frames))]
    pair_diffs = [sampled_diff(ordered[i], ordered[i + 1])
                  for i in range(len(ordered) - 1)]
    assert all(diff > 0 for diff in pair_diffs), "frames must differ"
    seam = sampled_diff(ordered[-1], ordered[0])
    adjacent_mean = sum(pair_diffs) / len(pair_diffs)
    # the seam transition matches a normal frame transition: the loop
    # returns to the first frame without a visible jump
    assert seam <= max(2.0 * adjacent_mean, adjacent_mean + 40)


def test_v13_07_frames_are_512_rgba_with_stable_transparent_corners():
    pack_path = ROOT / "assets" / "petpack" / \
        "retirement-cat-official-1.0.2.petpack"
    _manifest, images = _pack_frames(pack_path, "assets/idle_")
    for name, image in images.items():
        assert image.size().toTuple() == (512, 512), name
        assert image.pixelColor(0, 0).alpha() == 0, name
        assert image.pixelColor(511, 0).alpha() == 0, name
        assert image.pixelColor(0, 511).alpha() == 0, name
        assert image.pixelColor(511, 511).alpha() == 0, name


def test_v13_07_app_plays_the_sequence_revision(qt_application, tmp_path,
                                                monkeypatch):
    """A real application restores the 1.0.2 revision and its idle
    runtime advances through sequence frames over time."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock
    from retirement_pet.models import ActionId

    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-v13seq-{tmp_path.name}")
    try:
        active = pet._selection_store.get("active")
        assert active.package_version == "1.0.2"
        runtime = pet.switcher.current_runtime
        assert runtime is not None
        assert runtime.character_fqid() == \
            "official.retirement-cat-official.retirement-cat.cat"
        # sequence frames advance with the timeline: render the ACTIVE
        # runtime at successive times and compare real pixels
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter

        def render_idle(elapsed_ms: int) -> QImage:
            image = QImage(128, 128, QImage.Format.Format_ARGB32)
            image.fill(0)
            painter = QPainter(image)
            try:
                from retirement_pet.models import RenderSnapshot, LifeStage

                pet.switcher.current_runtime.render_body(
                    painter, QRectF(0, 0, 128, 128),
                    RenderSnapshot(action=ActionId.IDLE,
                                   stage=LifeStage.YOUNG,
                                   elapsed_ms=elapsed_ms, frame=0,
                                   time_ms=elapsed_ms))
            finally:
                painter.end()
            return image

        seen = set()
        for step in range(8):
            image = render_idle(step * 400)
            seen.add(bytes(image.bits()))
        assert len(seen) > 1, "idle sequence must draw distinct frames"
    finally:
        pet.shutdown()
