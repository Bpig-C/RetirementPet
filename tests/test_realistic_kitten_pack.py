"""Release checks for the repaired realistic-kitten character pack."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from PySide6.QtGui import QImage

from retirement_pet.petpack.validator import validate_petpack


ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "assets" / "petpack" / "realistic-kitten-0.1.1.petpack"
ARCHIVE_SHA256 = "36860d1fc6e469cf7371214863bbd3e040ce6c3fb960be6374e80ed6b682b351"
CONTENT_DIGEST = "b2e3027e98eecd53a918ecf27908e4f09730f11aff3623d1e5ab5ecadbfe535d"


def test_repaired_kitten_pack_identity_and_runtime_contract():
    payload = PACK.read_bytes()
    report = validate_petpack(payload, trust_channel="LOCAL_IMPORTED")

    assert report.accepted
    assert report.content_digest == CONTENT_DIGEST
    assert hashlib.sha256(payload).hexdigest() == ARCHIVE_SHA256
    with zipfile.ZipFile(PACK) as archive:
        manifest = json.loads(archive.read("petpack.json"))
    assert manifest["package"]["id"] == "realistic-kitten"
    assert manifest["package"]["version"] == "0.1.1"


def test_repaired_kitten_assets_are_transparent_rgba_without_clipped_edges():
    with zipfile.ZipFile(PACK) as archive:
        names = sorted(name for name in archive.namelist()
                       if name.startswith("assets/") and name.endswith(".png"))
        assert len(names) == 8
        for name in names:
            image = QImage.fromData(archive.read(name), "PNG")
            assert not image.isNull(), name
            assert (image.width(), image.height()) == (512, 512), name
            assert image.hasAlphaChannel(), name
            for x, y in ((0, 0), (511, 0), (0, 511), (511, 511)):
                assert image.pixelColor(x, y).alpha() <= 8, (name, x, y)


def test_work_sprite_keeps_the_left_face_neck_and_chest_opaque():
    """Regression for the 0.1.0 flood-fill leak reported from the live UI."""
    with zipfile.ZipFile(PACK) as archive:
        image = QImage.fromData(archive.read("assets/work.png"), "PNG")

    repaired_region = ((180, 200), (180, 240), (200, 260),
                       (200, 320), (180, 360))
    assert all(image.pixelColor(x, y).alpha() >= 240
               for x, y in repaired_region)
