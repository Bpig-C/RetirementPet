"""Build the official retirement-cat pack through the PetPack 1.0 pipeline.

Renders the v1 programmatic cat's core actions into PNG frames (original
project-owned art, generated at build time), assembles a manifest with
full rights/source declarations, builds a deterministic .petpack and
validates it on the BUILTIN_OFFICIAL channel.  The same archive must
REJECT on the LOCAL_IMPORTED channel - a local pack can never claim the
official namespace (CONFORMANCE: "本地包自称 official").

Usage:  python scripts/build_official_cat.py <out.petpack>
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
import zlib
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from retirement_pet.models import ActionId, LifeStage, RenderSnapshot
from retirement_pet.ui.renderer import CatRenderer

#: sampled frames per action (time in ms within the action loop)
FRAME_PLAN: dict[str, tuple[int, ...]] = {
    "core.idle": (0, 400, 800, 1200),
    "core.work": (0, 600),
    "core.rest": (0, 700),
    "core.eat": (0, 500),
    "core.exercise": (0, 500),
    "core.meeting": (0, 600),
    "core.music": (0, 500),
}
LOGICAL_CANVAS = 256
BODY_PIXELS = 512
THUMBNAIL_PIXELS = 128
FROZEN_LEGACY_OUTPUT = (
    _PROJECT_ROOT / "assets" / "petpack" /
    "retirement-cat-official.petpack"
)


def validate_output_path(out_path: Path) -> Path:
    """Reject the byte-frozen v1.0.0 release-media path."""
    resolved = out_path.resolve(strict=False)
    if resolved == FROZEN_LEGACY_OUTPUT.resolve(strict=False):
        raise ValueError("refusing to overwrite frozen official v1.0.0 media")
    return resolved


def _atomic_write(path: Path, payload: bytes) -> None:
    """Publish a fully-built pack without exposing a partial archive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def qimage_to_png(image: QImage) -> bytes:
    import io

    buffer = __import__("PySide6.QtCore", fromlist=["QBuffer"]).QBuffer()
    buffer.open(buffer.OpenModeFlag.ReadWrite)
    image.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return data


def render_action_frames(renderer: CatRenderer, action: ActionId,
                         times_ms: tuple[int, ...], *,
                         pixel_size: int = BODY_PIXELS) -> list[bytes]:
    frames = []
    for t in times_ms:
        image = QImage(pixel_size, pixel_size, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        try:
            renderer.render_body(
                painter, QRectF(0, 0, pixel_size, pixel_size),
                RenderSnapshot(action=action, stage=LifeStage.YOUNG,
                               elapsed_ms=t, frame=t // 64, time_ms=t),
            )
        finally:
            painter.end()
        frames.append(qimage_to_png(image))
    return frames


def build_manifest(files: dict[str, bytes]) -> dict:
    def asset_entry(asset_id: str, path: str) -> dict:
        data = files[path]
        return {
            "id": asset_id, "path": path, "media_type": "image/png",
            "byte_size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "rights_ref": "rights.original",
            "source_ref": "source.original",
            "properties": {"width": BODY_PIXELS, "height": BODY_PIXELS},
        }

    assets: list[dict] = []
    actions: list[dict] = []
    character_actions: dict[str, str] = {}
    for semantic, times in FRAME_PLAN.items():
        slug = semantic.removeprefix("core.")
        action_id = f"action.cat.{slug}"
        for i in range(len(times)):
            path = f"assets/{slug}_{i}.png"
            assets.append(asset_entry(f"asset.{slug}.{i}", path))
        if semantic == "core.idle":
            frames = [{"asset": f"asset.{slug}.{i}", "duration_ms": 250}
                      for i in range(len(times))]
            actions.append({
                "id": action_id, "semantic": semantic,
                "policy_tags": ["silent", "meeting_safe", "dnd_safe"],
                "lifecycle": {"loop": {"renderer": {
                    "type": "sequence", "frames": frames}}},
                "user_modes": ["auto", "manual", "disabled"],
                "loop_modes": ["repeat"],
                "interrupt": {"policy": "clip_boundary", "max_exit_ms": 500},
                "audio": None,
            })
        else:
            actions.append({
                "id": action_id, "semantic": semantic,
                "policy_tags": ["silent", "meeting_safe" if semantic == "core.meeting" else "silent"],
                "lifecycle": {"loop": {"renderer": {
                    "type": "static", "asset": f"asset.{slug}.0"}}},
                "user_modes": ["auto", "manual", "disabled"],
                "loop_modes": ["repeat"],
                "interrupt": {"policy": "clip_boundary", "max_exit_ms": 500},
                "audio": None,
            })
        character_actions[semantic] = action_id

    thumbnail = files["assets/thumbnail.png"]
    assets.append({
        "id": "asset.thumbnail", "path": "assets/thumbnail.png",
        "media_type": "image/png", "byte_size": len(thumbnail),
        "sha256": hashlib.sha256(thumbnail).hexdigest(),
        "rights_ref": "rights.original", "source_ref": "source.original",
        "properties": {"width": THUMBNAIL_PIXELS,
                       "height": THUMBNAIL_PIXELS},
    })

    license_text = (
        "RetirementPet official retirement-cat pack.\n"
        "Original programmatic artwork generated by the RetirementPet\n"
        "project; all rights reserved. Redistribution as part of\n"
        "RetirementPet permitted.\n")
    files["legal/license.txt"] = license_text.encode("utf-8")

    return {
        "schema_version": "1.0",
        "package": {
            "publisher_id": "official",
            "id": "retirement-cat-official",
            "version": "1.0.1",
            "display_name": {"zh-CN": "官方退休猫", "en": "Official Retirement Cat"},
        },
        "compatibility": {
            "engine_min": "2.0.0", "engine_max_exclusive": "3.0.0",
            "required_capabilities": ["renderer.sequence.v1"],
            "optional_capabilities": [],
        },
        "publishers": [
            {"id": "official", "display_name": "RetirementPet Project",
             "contact": None, "homepage": None}
        ],
        "series": {
            "id": "retirement-cat",
            "display_name": {"zh-CN": "退休猫"},
            "description": {"zh-CN": "内置官方参考角色（原创内容）"},
        },
        "rights_declarations": [
            {"id": "rights.original", "basis": "original",
             "claimant_ref": "official",
             "license": {"spdx": None, "legal_file_ref": "legal.cat",
                         "custom_name": None},
             "scope_claimed": ["personal_use", "redistribution"],
             "attribution": "RetirementPet Project", "notes": None}
        ],
        "sources": [
            {"id": "source.original", "kind": "original_creation",
             "creator": "official", "title": "Programmatic retirement cat",
             "locator": None, "accessed_at": None}
        ],
        "legal_files": [
            {"id": "legal.cat", "path": "legal/license.txt",
             "media_type": "text/plain",
             "byte_size": len(files["legal/license.txt"]),
             "sha256": hashlib.sha256(files["legal/license.txt"]).hexdigest(),
             "purpose": "license_text"}
        ],
        "assets": assets,
        "actions": actions,
        "characters": [
            {"id": "cat", "display_name": {"zh-CN": "退休猫"},
             "thumbnail_asset": "asset.thumbnail",
             "rig_contract_ref": None, "rig_bindings": {},
             "geometry": {
                 "logical_canvas": {"width": LOGICAL_CANVAS,
                                    "height": LOGICAL_CANVAS},
                 "content_bounds": {"x": 32, "y": 32, "width": 192, "height": 192},
                 "motion_bounds": {"x": 16, "y": 16, "width": 224, "height": 224},
                 "base_anchor": {"x": 128, "y": 236},
                 "bubble_anchor": {"x": 128, "y": 64},
                 "reference_height": 192,
                 "hit_regions": [{"shape": "rect", "x": 32, "y": 32,
                                  "width": 192, "height": 192}]},
             "actions": character_actions,
             "variants": [], "default_variant": None,
             "text_profile_refs": [], "recommended_profiles": {}}
        ],
    }


def deterministic_zip(manifest: dict, files: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                indent=1).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        info = zipfile.ZipInfo("petpack.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = 0o600 << 16
        zf.writestr(info, manifest_bytes)
        for rel in sorted(files):
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o600 << 16
            zf.writestr(info, files[rel])
    return buffer.getvalue()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path("assets/petpack/retirement-cat-official-1.0.1.petpack")
    try:
        out_path = validate_output_path(out_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    renderer = CatRenderer()
    files: dict[str, bytes] = {}
    for semantic, times in FRAME_PLAN.items():
        action = ActionId(semantic.removeprefix("core."))
        slug = semantic.removeprefix("core.")
        for i, png in enumerate(render_action_frames(renderer, action, times)):
            files[f"assets/{slug}_{i}.png"] = png
    files["assets/thumbnail.png"] = render_action_frames(
        renderer, ActionId.IDLE, (0,), pixel_size=THUMBNAIL_PIXELS)[0]

    manifest = build_manifest(files)
    pack = deterministic_zip(manifest, files)

    from retirement_pet.petpack.validator import validate_petpack

    official = validate_petpack(pack, trust_channel="BUILTIN_OFFICIAL")
    local = validate_petpack(pack, trust_channel="LOCAL_IMPORTED")
    if official.accepted and not local.accepted:
        _atomic_write(out_path, pack)
    print(f"built            {out_path}")
    print(f"archive_sha256   {hashlib.sha256(pack).hexdigest()}")
    print(f"official channel {official.result}"
          f"{' ' + str([d.code for d in official.diagnostics]) if not official.accepted else ''}")
    print(f"local channel    {local.result}"
          f"{' ' + str([d.code for d in local.diagnostics]) if not local.accepted else ''}")
    if official.accepted and not local.accepted:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
