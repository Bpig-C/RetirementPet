"""Official retirement-cat pack: same protocol, higher trust channel only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from PySide6.QtGui import QImage

from retirement_pet.petpack.diagnostics import MAN_E009_RESERVED_NAMESPACE_OR_TRUST
from retirement_pet.petpack.runtime import render_first_frame_offscreen
from retirement_pet.petpack.validator import validate_petpack

LEGACY_CAT_PACK = Path(__file__).resolve().parent.parent / "assets" / \
    "petpack" / "retirement-cat-official.petpack"
CAT_PACK = Path(__file__).resolve().parent.parent / "assets" / \
    "petpack" / "retirement-cat-official-1.0.1.petpack"

LEGACY_ARCHIVE_SHA256 = \
    "7aaa6416620eea9d8be7df3cb22c2c0ae2e3b069bc04226f21b393a5e746ed7b"
LEGACY_REVISION_DIGEST = \
    "86684d32136c205a043995f4889b857c08d6496743047a77165ad72895bdb1ce"


def test_official_pack_accepted_on_builtin_channel(qt_application):
    report = validate_petpack(CAT_PACK.read_bytes(), trust_channel="BUILTIN_OFFICIAL")
    assert report.accepted, [d.code for d in report.diagnostics]
    assert report.pack_key.publisher_id == "official"
    assert report.pack_key.package_id == "retirement-cat-official"
    assert report.revision_key.package_version == "1.0.1"


def test_official_pack_rejected_as_local_import(qt_application):
    """A locally imported pack claiming the official namespace is refused."""
    report = validate_petpack(CAT_PACK.read_bytes(), trust_channel="LOCAL_IMPORTED")
    assert not report.accepted
    codes = [d.code for d in report.diagnostics]
    assert MAN_E009_RESERVED_NAMESPACE_OR_TRUST in codes


def test_official_cat_renders_idle_offscreen(qt_application):
    from retirement_pet.petpack.runtime import load_pack

    archive, manifest = load_pack(CAT_PACK.read_bytes())
    image = render_first_frame_offscreen(archive, manifest, "cat")
    assert image is not None
    opaque = sum(1 for y in range(0, 256, 8)
                 for x in range(0, 256, 8)
                 if image.pixelColor(x, y).alpha() > 0)
    assert opaque > 10, "the cat's idle frame must contain visible content"


def test_official_cat_binds_all_core_semantics(qt_application):
    from retirement_pet.petpack.runtime import load_pack

    archive, manifest = load_pack(CAT_PACK.read_bytes())
    from retirement_pet.petpack.runtime import PackCharacterRuntime

    runtime = PackCharacterRuntime(archive, manifest, "cat")
    assert runtime.prepare()
    assert runtime.missing_semantics == ()  # the cat supports every core semantic
    caps = runtime.capabilities()
    for semantic in ("core.idle", "core.work", "core.rest", "core.eat",
                     "core.exercise", "core.meeting", "core.music"):
        assert caps.supports(semantic)


def test_legacy_official_pack_is_byte_exact_and_keeps_revision_fact():
    data = LEGACY_CAT_PACK.read_bytes()
    assert hashlib.sha256(data).hexdigest() == LEGACY_ARCHIVE_SHA256
    report = validate_petpack(data, trust_channel="BUILTIN_OFFICIAL")
    assert report.accepted
    assert report.revision_key.content_digest == LEGACY_REVISION_DIGEST
    assert report.revision_key.package_version == "1.0.0"


def test_v101_assets_are_real_size_and_caption_band_is_transparent(
        qt_application):
    with zipfile.ZipFile(CAT_PACK) as archive:
        manifest = json.loads(archive.read("petpack.json"))
        assert manifest["package"]["version"] == "1.0.1"
        assert manifest["characters"][0]["geometry"]["logical_canvas"] == {
            "width": 256, "height": 256,
        }
        for asset in manifest["assets"]:
            image = QImage.fromData(archive.read(asset["path"]))
            expected = 128 if asset["id"] == "asset.thumbnail" else 512
            assert (image.width(), image.height()) == (expected, expected)
            assert asset["properties"] == {
                "width": expected, "height": expected,
            }
            if expected == 512:
                assert all(
                    image.pixelColor(x, y).alpha() == 0
                    for y in range(32) for x in range(image.width())
                ), f"engine caption pixels leaked into {asset['path']}"
