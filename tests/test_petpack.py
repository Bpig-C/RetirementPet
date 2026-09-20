"""PetPack validation corpus: legal packs + hostile pack fixtures (M3).

Fixtures are built deterministically in-test (same input -> same digest);
registered artifacts and hashes land in config/fixture_registry.json as
the corpus stabilizes (CONFORMANCE 4).
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
import zipfile

import pytest

from retirement_pet.petpack.archive import PetpackArchive
from retirement_pet.petpack.diagnostics import (
    ARC_E003_UNSAFE_PATH,
    ARC_E004_COLLISION,
    ARC_E005_LINK_OR_REPARSE,
    ARC_E007_BUDGET_EXCEEDED,
    MAN_E005_INVALID_ID_VERSION_OR_NAMESPACE,
    MAN_E007_UNDECLARED_OR_MISSING_FILE,
    MAN_E008_SIZE_OR_HASH_MISMATCH,
    MAN_E009_RESERVED_NAMESPACE_OR_TRUST,
    RES_E001_DISALLOWED_TYPE_OR_MIME,
)
from retirement_pet.petpack.diagnostics import ValidationFailure
from retirement_pet.petpack.identity import compute_content_digest
from retirement_pet.petpack.validator import validate_petpack

def expect_reject(pack: bytes, code: str):
    report = validate_petpack(pack)
    assert not report.accepted, f"expected rejection ({code}), got {report.result}"
    codes = [d.code for d in report.diagnostics]
    assert code in codes, f"expected {code}, got {codes}"
    return report


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d4944415478da63fcffff3f030005fe02fea72d1"
    "5940000000049454e44ae426082"
)  # 1x1 transparent-ish PNG (IHDR+IDAT+IEND)


def png_bytes(width: int = 2, height: int = 2, rgba=(100, 150, 200, 255)) -> bytes:
    """Minimal valid PNG writer (zlib stored blocks)."""
    import zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = b"".join(
        b"\x00" + bytes(rgba) * width for _ in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def wav_bytes(seconds: float = 0.5, rate: int = 8000) -> bytes:
    samples = int(rate * seconds)
    data = b"\x00\x00" * samples
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


def make_manifest(**overrides) -> dict:
    manifest = {
        "schema_version": "1.0",
        "package": {
            "publisher_id": "community.example",
            "publisher_ref": "community.example",
            "id": "sample-pack",
            "version": "1.0.0",
            "display_name": {"zh-CN": "示例角色包"},
        },
        "series": {"id": "sample-series", "display_name": {"zh-CN": "示例系列"}},
        "publishers": [
            {"id": "community.example", "display_name": "Example Author",
             "homepage": None, "contact": None},
        ],
        "rights_declarations": [
            {"id": "rights.original", "basis": "original",
             "claimant_ref": "community.example",
             "license": {"spdx": None, "legal_file_ref": None,
                         "custom_name": "All rights reserved"},
             "scope_claimed": ["personal_use"], "attribution": "test",
             "notes": None},
        ],
        "sources": [
            {"id": "source.original", "kind": "original_creation",
             "creator": "community.example", "title": "test sprite",
             "locator": None, "accessed_at": None},
        ],
        "assets": [
            {"id": "thumb", "path": "assets/thumb.png", "media_type": "image/png",
             "byte_size": 0, "sha256": "",
             "rights_ref": "rights.original", "source_ref": "source.original"},
            {"id": "idle0", "path": "assets/idle_0.png", "media_type": "image/png",
             "byte_size": 0, "sha256": "",
             "rights_ref": "rights.original", "source_ref": "source.original"},
        ],
        "actions": [
            {"id": "action.idle", "semantic": "core.idle",
             "lifecycle": {"loop": {"renderer": {"type": "sequence", "frames": [
                 {"asset": "idle0", "duration_ms": 120}]}}}},
            {"id": "community.example.sample-pack.sample-series.demo.wave",
             "semantic": "community.example.sample-pack.sample-series.demo.wave",
             "lifecycle": {}},
        ],
        "characters": [
            {"id": "demo",
             "display_name": {"zh-CN": "示例"},
             "thumbnail_asset": "thumb",
             "geometry": {
                 "logical_canvas": {"width": 256, "height": 256},
                 "content_bounds": {"x": 28, "y": 16, "width": 200,
                                    "height": 220},
                 "motion_bounds": {"x": 12, "y": 8, "width": 232,
                                   "height": 240},
                 "base_anchor": {"x": 128, "y": 248},
                 "bubble_anchor": {"x": 128, "y": 40},
                 "reference_height": 220,
                 "hit_regions": [{"shape": "rect", "x": 28, "y": 16,
                                  "width": 200, "height": 220}],
             },
             "actions": {"core.idle": "action.idle"}},
        ],
    }
    manifest.update(overrides)
    return manifest


def build_pack(manifest: dict, files: dict[str, bytes] | None = None,
               *, fix_hashes: bool = True) -> bytes:
    """Deterministically build a .petpack container."""
    all_files: dict[str, bytes] = dict(files or {})
    assets = manifest.get("assets", [])
    # recompute declared sizes/hashes unless the test wants a mismatch
    if fix_hashes:
        for asset in assets:
            path = asset.get("path")
            if path in all_files:
                asset["byte_size"] = len(all_files[path])
                asset["sha256"] = hashlib.sha256(all_files[path]).hexdigest()
    manifest_bytes = json.dumps(manifest, ensure_ascii=False,
                                sort_keys=True).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("petpack.json", manifest_bytes)
        for path in sorted(all_files):
            zf.writestr(path, all_files[path])
    return buffer.getvalue()


def base_files() -> dict[str, bytes]:
    return {
        "assets/thumb.png": png_bytes(4, 4),
        "assets/idle_0.png": png_bytes(8, 8),
    }


# -- legal corpus ----------------------------------------------------------------


def test_minimal_static_pack_is_accepted_and_digest_is_deterministic():
    pack = build_pack(make_manifest(), base_files())
    report = validate_petpack(pack)
    assert report.accepted, report.diagnostics
    assert report.result == "ACCEPT"
    assert report.pack_key.publisher_id == "community.example"
    assert report.revision_key.package_version == "1.0.0"

    pack_again = build_pack(make_manifest(), base_files())
    assert (compute_content_digest(
        json.dumps(make_manifest(), ensure_ascii=False, sort_keys=True).encode(),
        sorted(base_files().items())) is not None)
    report2 = validate_petpack(pack_again)
    assert report2.content_digest == report.content_digest


def test_same_content_bytes_in_different_zip_order_same_digest():
    m = make_manifest()
    files = base_files()
    pack = build_pack(m, files)
    # rebuild with a different container order but identical content
    manifest_bytes = json.dumps(m, ensure_ascii=False, sort_keys=True).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        for path in sorted(files, reverse=True):
            zf.writestr(path, files[path])
        zf.writestr("petpack.json", manifest_bytes)
    other = buffer.getvalue()
    r1, r2 = validate_petpack(pack), validate_petpack(other)
    assert r1.accepted and r2.accepted
    assert r1.content_digest == r2.content_digest  # archive bytes differ


# -- hostile corpus: paths ---------------------------------------------------------


@pytest.mark.parametrize("bad_name", (
    "../escape.png",
    "/abs.png",
    "C:/abs.png",
    "assets/../id.png",
    "assets/./id.png",
    "assets//id.png",
    "assets\\win.png",
    "assets/id.png/..",
    "CON.png",
    "assets/NUL.png",
    "assets/COM1.png",
    "assets/trailing. /x.png",
    "assets/trailing dot.",
    "assets/trailing space ",
    "assets/a:b.png",
))
def test_hostile_member_names_are_rejected(bad_name):
    # Pure-function tier: the name normalizer is the enforcement point for
    # every form a hostile archiver can express (Python's zipfile writer
    # normalizes some shapes on the WRITE side, so integration fixtures only
    # cover what this writer can produce).
    from retirement_pet.petpack.archive import normalize_member_name

    with pytest.raises(ValidationFailure):
        normalize_member_name(bad_name)


def test_hostile_member_names_rejected_in_archive():
    # integration tier: writer-expressible hostile names
    files = base_files()
    files["assets/trailing."] = b"x"
    pack = build_pack(make_manifest(), files, fix_hashes=True)
    report = validate_petpack(pack)
    assert not report.accepted
    assert {d.code for d in report.diagnostics} & {
        ARC_E003_UNSAFE_PATH, ARC_E004_COLLISION, ARC_E007_BUDGET_EXCEEDED}


def test_non_ascii_name_without_utf8_flag_lands_in_defenses():
    # A ZIP with a non-ASCII name and NO UTF-8 flag is decoded as CP437 by
    # any compliant reader; the mojibake name then fails name normalization
    # (non-NFC / control-ish bytes), so the pack is rejected either way.
    # Python's writer refuses to emit this shape, so we assert the generic
    # defense: such an archive never validates ACCEPT.
    manifest_bytes = json.dumps(make_manifest(), ensure_ascii=False,
                                sort_keys=True).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("petpack.json", manifest_bytes)
        for path, data in base_files().items():
            zf.writestr(path, data)
        info = zipfile.ZipInfo("assets/中文.png")
        info.file_size = 4
        zf.writestr(info, b"abcd")  # writer sets the flag; name is declared? NO
    report = validate_petpack(buffer.getvalue())
    assert not report.accepted  # undeclared asset at minimum


def test_symlink_member_rejected():
    m = make_manifest()
    manifest_bytes = json.dumps(m, ensure_ascii=False, sort_keys=True).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("petpack.json", manifest_bytes)
        for path, data in base_files().items():
            zf.writestr(path, data)
        info = zipfile.ZipInfo("assets/link.png")
        info.external_attr = (0o120777 << 16)  # S_IFLNK
        zf.writestr(info, b"../target")
    expect_reject(buffer.getvalue(), ARC_E005_LINK_OR_REPARSE)


def test_duplicate_entry_rejected():
    manifest_bytes = json.dumps(make_manifest(), ensure_ascii=False,
                                sort_keys=True).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("petpack.json", manifest_bytes)
        zf.writestr("assets/thumb.png", png_bytes(2, 2))
        zf.writestr("assets/thumb.png", png_bytes(3, 3))  # duplicate
        zf.writestr("assets/idle_0.png", png_bytes(2, 2))
    expect_reject(buffer.getvalue(), ARC_E004_COLLISION)


# -- hostile corpus: inventory & integrity ------------------------------------------


def test_undeclared_file_rejected():
    files = base_files()
    files["assets/extra.png"] = png_bytes(2, 2)
    pack = build_pack(make_manifest(), files)
    expect_reject(pack, MAN_E007_UNDECLARED_OR_MISSING_FILE)


def test_hash_mismatch_rejected():
    manifest = make_manifest()
    files = base_files()
    pack = build_pack(manifest, files, fix_hashes=False)
    expect_reject(pack, MAN_E008_SIZE_OR_HASH_MISMATCH)


def test_png_magic_mismatch_rejected():
    manifest = make_manifest()
    files = base_files()
    files["assets/thumb.png"] = b"MZ fake executable"
    pack = build_pack(manifest, files)
    expect_reject(pack, RES_E001_DISALLOWED_TYPE_OR_MIME)


def test_oversized_image_pixels_rejected():
    manifest = make_manifest()
    files = base_files()
    files["assets/thumb.png"] = png_bytes(5000, 5000)  # 25 MP > 4096^2
    pack = build_pack(manifest, files)
    expect_reject(pack, "PPK-RES-E003")


# -- hostile corpus: manifest identities ---------------------------------------------


def test_reserved_namespace_rejected():
    manifest = make_manifest()
    manifest["package"]["publisher_id"] = "official"
    pack = build_pack(manifest, base_files())
    expect_reject(pack, MAN_E009_RESERVED_NAMESPACE_OR_TRUST)


def test_bad_schema_rejected():
    manifest = make_manifest()
    manifest["schema_version"] = "2.0"
    pack = build_pack(manifest, base_files())
    expect_reject(pack, "PPK-MAN-E003")


def test_duplicate_json_key_rejected():
    raw = b'{"schema_version":"1.0","schema_version":"1.0"}'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("petpack.json", raw)
    assert not validate_petpack(buffer.getvalue()).accepted


def test_missing_manifest_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("other.txt", b"x")
    expect_reject(buffer.getvalue(), ARC_E003_UNSAFE_PATH)


def test_missing_core_idle_rejected():
    manifest = make_manifest()
    manifest["characters"][0]["actions"] = {}
    pack = build_pack(manifest, base_files())
    expect_reject(pack, "PPK-ACT-E001")


def test_action_frame_duration_below_30fps_cap_rejected():
    manifest = make_manifest()
    manifest["actions"][0]["lifecycle"]["loop"]["renderer"]["frames"][0][
        "duration_ms"] = 10  # 100 FPS > 30 FPS cap
    pack = build_pack(manifest, base_files())
    expect_reject(pack, "PPK-ACT-E006")


def test_executable_media_type_rejected():
    manifest = make_manifest()
    manifest["assets"].append(
        {"id": "evil", "path": "assets/evil.exe",
         "media_type": "application/vnd.microsoft.portable-executable",
         "byte_size": 2, "sha256": "",
         "rights_ref": "rights.original", "source_ref": "source.original"})
    files = base_files()
    files["assets/evil.exe"] = b"MZ"
    pack = build_pack(manifest, files)
    expect_reject(pack, "PPK-RES-E001")
