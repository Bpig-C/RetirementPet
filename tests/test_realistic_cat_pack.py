"""Release and runtime gates for the semi-realistic local preview pack."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from retirement_pet.petpack.local_import import (
    LocalImportError,
    MAX_LOCAL_IMPORT_BYTES,
    preflight_local_pack,
)
from retirement_pet.petpack.runtime import PackCharacterRuntime, load_pack
from retirement_pet.petpack.validator import validate_petpack


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "character-work" / "realistic-retirement-cat"
PACK = (ROOT / "assets" / "petpack" / "examples" /
        "realistic-retirement-cat-0.1.1.petpack")
LEGACY_PACK = (ROOT / "assets" / "petpack" / "examples" /
               "realistic-retirement-cat-0.1.0.petpack")
ARCHIVE_SHA256 = "ce80e7dd73726cdbb252f21d65538014fe10790dce7a44816ebb80bc26c2a964"
CONTENT_DIGEST = "6c4b368ad79124bf5bc48e6d8190917c7031f923b2cf00f728c4c17b9c21260c"
BODY_SHA256 = "1a6351e3aaaae76d0d393c2debea9e5a35751ac355f1346edc55fadb4fb2d6bf"
RGBA_SHA256 = "d0f48290f7ea8ed4d3155fa6733fb16db768cb2440866519f72c839b514d3eee"
LEGACY_ARCHIVE_SHA256 = (
    "2c77efbc0b99143f673acd849693d25e34fa87283a9c797621699ecc8189ce2e"
)
FQID = (
    "community.retirementpet.realistic-retirement-cat."
    "retirement-cat.realistic-cat"
)


def _load_cli_module():
    path = ROOT / "scripts" / "petpack_cli.py"
    spec = importlib.util.spec_from_file_location("realistic_petpack_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_realistic_preview_release_identity_is_frozen():
    data = PACK.read_bytes()
    report = validate_petpack(data, trust_channel="LOCAL_IMPORTED")

    assert report.accepted
    assert report.degraded == []
    assert hashlib.sha256(data).hexdigest() == ARCHIVE_SHA256
    assert report.content_digest == CONTENT_DIGEST
    archive, manifest = load_pack(data)
    runtime = PackCharacterRuntime(
        archive, manifest, "realistic-cat",
        content_digest=report.content_digest,
    )
    assert runtime.character_fqid() == FQID


def test_preview_has_current_canonical_only_in_release_tree():
    duplicate = (ROOT / "character-work" /
                 "realistic-retirement-cat-0.1.1.petpack")
    assert PACK.is_file()
    assert not duplicate.exists()


def test_public_snapshot_excludes_only_the_frozen_legacy_preview():
    config = json.loads(
        (ROOT / "config" / "public_snapshot.json").read_text(encoding="utf-8")
    )
    assert config["additional_exclude_paths"] == [
        "assets/petpack/examples/realistic-retirement-cat-0.1.0.petpack"
    ]
    assert "assets/petpack/examples/realistic-retirement-cat-0.1.1.petpack" \
        not in config["additional_exclude_paths"]


def test_legacy_preview_is_unchanged_when_retained_in_private_history():
    """The public export omits 0.1.0; an internal checkout keeps it frozen."""
    if not LEGACY_PACK.exists():
        pytest.skip("legacy 0.1.0 is intentionally absent from public source")
    assert hashlib.sha256(LEGACY_PACK.read_bytes()).hexdigest() == \
        LEGACY_ARCHIVE_SHA256


def test_realistic_preview_body_has_real_alpha_and_declared_dimensions():
    with zipfile.ZipFile(PACK) as archive:
        payload = archive.read("assets/characters/realistic-cat/body.png")
    image = QImage.fromData(payload, "PNG")

    assert hashlib.sha256(payload).hexdigest() == BODY_SHA256
    assert b"caBX" not in payload
    assert not image.isNull()
    assert (image.width(), image.height()) == (1024, 1536)
    assert image.hasAlphaChannel()
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    rgba_bytes = bytes(rgba.constBits())
    alpha = rgba_bytes[3::4]
    assert hashlib.sha256(rgba_bytes).hexdigest() == RGBA_SHA256
    assert min(alpha) == 0
    assert max(alpha) > 0
    assert all(rgba.pixelColor(x, y).alpha() <= 8 for x, y in (
        (0, 0), (rgba.width() - 1, 0),
        (0, rgba.height() - 1),
        (rgba.width() - 1, rgba.height() - 1),
    ))
    if LEGACY_PACK.exists():
        with zipfile.ZipFile(LEGACY_PACK) as archive:
            legacy_payload = archive.read(
                "assets/characters/realistic-cat/body.png")
        legacy = QImage.fromData(legacy_payload, "PNG").convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        assert bytes(legacy.constBits()) == rgba_bytes


def test_realistic_preview_preflight_and_runtime_fallback(qt_application):
    preview = preflight_local_pack(PACK)
    assert preview.character_ids == ("realistic-cat",)
    assert preview.revision_key.content_digest == CONTENT_DIGEST

    report = validate_petpack(PACK.read_bytes())
    archive, manifest = load_pack(PACK.read_bytes())
    runtime = PackCharacterRuntime(
        archive, manifest, "realistic-cat",
        content_digest=report.content_digest,
    )
    assert runtime.prepare()
    assert set(runtime.capabilities().semantics) == {
        "core.idle", "core.work", "core.rest",
    }
    assert set(runtime.missing_semantics) == {
        "core.eat", "core.exercise", "core.meeting", "core.music",
    }
    assert runtime.first_frame_image() is not None


def test_realistic_preview_build_is_deterministic(tmp_path):
    cli = _load_cli_module()
    first = tmp_path / "first.petpack"
    second = tmp_path / "second.petpack"

    cli.build(SOURCE, first)
    cli.build(SOURCE, second)

    assert first.read_bytes() == second.read_bytes() == PACK.read_bytes()


def test_local_import_rejects_oversized_source_before_materializing_it(tmp_path):
    oversized = tmp_path / "oversized.petpack"
    with oversized.open("wb") as handle:
        handle.seek(MAX_LOCAL_IMPORT_BYTES)
        handle.write(b"x")

    with pytest.raises(LocalImportError, match="64 MiB"):
        preflight_local_pack(oversized)
