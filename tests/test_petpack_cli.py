"""Author toolchain: deterministic build, validate, inspect (PETPACK_SPEC 23)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

CLI_PATH = Path(__file__).resolve().parent.parent / "scripts" / "petpack_cli.py"
REF_PACK = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / \
    "petpack" / "minimal-static.petpack"


@pytest.fixture(scope="module")
def cli_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("petpack_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cli_main(cli_module):
    return cli_module.main


def test_reference_pack_validates(cli_main):
    assert cli_main(["validate", str(REF_PACK)]) == 0


def test_author_preflight_matches_gui_import_gate(
        cli_main, qt_application, capsys):
    preview = (Path(__file__).resolve().parent.parent / "assets" / "petpack" /
               "examples" / "realistic-retirement-cat-0.1.1.petpack")

    assert cli_main(["preflight", str(preview)]) == 0
    output = capsys.readouterr().out
    assert "preflight      ACCEPT" in output
    assert "realistic-cat" in output


def test_deterministic_build(tmp_path, cli_main):
    source = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / \
        "petpack" / "minimal-static"
    out_a = tmp_path / "a.petpack"
    out_b = tmp_path / "b.petpack"
    cli_main(["build", str(source), str(out_a)])
    cli_main(["build", str(source), str(out_b)])
    hash_a = hashlib.sha256(out_a.read_bytes()).hexdigest()
    hash_b = hashlib.sha256(out_b.read_bytes()).hexdigest()
    assert hash_a == hash_b
    # and it matches the committed reference artifact
    assert hash_a == hashlib.sha256(REF_PACK.read_bytes()).hexdigest()


def test_inspect_reports_identity(cli_main, capsys):
    assert cli_main(["inspect", str(REF_PACK)]) == 0
    out = capsys.readouterr().out
    assert "community.retirementpet/minimal-static" in out
    assert "demo" in out


def test_validate_rejects_corrupted_pack(cli_main, tmp_path, capsys):
    data = bytearray(REF_PACK.read_bytes())
    data[len(data) // 2] ^= 0xFF  # flip a byte mid-archive
    corrupt = tmp_path / "corrupt.petpack"
    corrupt.write_bytes(bytes(data))
    assert cli_main(["validate", str(corrupt)]) == 1
    out = capsys.readouterr().out
    assert "REJECT" in out


def test_init_creates_nested_directory_and_refuses_manifest_overwrite(
        cli_main, tmp_path):
    target = tmp_path / "new" / "character-pack"

    assert cli_main(["init", str(target)]) == 0
    manifest = target / "petpack.json"
    original = manifest.read_bytes()
    assert (target / "assets").is_dir()

    assert cli_main(["init", str(target)]) == 1
    assert manifest.read_bytes() == original


def test_build_rejects_frozen_output_without_touching_it(
        cli_module, cli_main, monkeypatch, tmp_path):
    source = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / \
        "petpack" / "minimal-static"
    frozen = tmp_path / "frozen.petpack"
    frozen.write_bytes(b"frozen-canary")
    monkeypatch.setattr(cli_module, "FROZEN_OUTPUTS", frozenset({frozen.resolve()}))

    assert cli_main(["build", str(source), str(frozen)]) == 2
    assert frozen.read_bytes() == b"frozen-canary"


# -- V12-03: complete author workflow (work order section 5) --------------------

def _write_init_template(cli_main, target):
    assert cli_main(["init", str(target)]) == 0
    return target


def test_init_to_preview_workflow_from_empty_directory(
        cli_main, qt_application, capsys, tmp_path):
    """Acceptance: from an empty directory, init walks the whole chain and
    the built pack passes validate + preflight with zero warnings."""
    target = tmp_path / "new" / "character-pack"
    _write_init_template(cli_main, target)

    assert cli_main(["lint", str(target)]) == 0
    lint_out = capsys.readouterr().out
    assert "lint result   clean" in lint_out
    assert "contact" in lint_out and "budget" in lint_out

    out = tmp_path / "out" / "my-pack.petpack"
    assert cli_main(["build", str(target), str(out)]) == 0
    capsys.readouterr()

    assert cli_main(["validate", str(out)]) == 0
    assert "ACCEPT" in capsys.readouterr().out

    assert cli_main(["preflight", str(out)]) == 0
    assert "preflight      ACCEPT" in capsys.readouterr().out
    assert "warnings" not in capsys.readouterr().out

    frames = tmp_path / "frames"
    assert cli_main(["preview", str(out), str(frames)]) == 0
    preview_out = capsys.readouterr().out
    assert "core.idle: static" in preview_out
    rendered = list(frames.glob("demo-core.idle-*.png"))
    assert len(rendered) == 1


def test_templates_build_byte_identical_in_independent_locations(
        cli_main, tmp_path):
    """Acceptance: same input in two independent output locations yields
    byte-identical packs - for the reference fixture and for two fresh
    init trees alike."""
    source = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / \
        "petpack" / "minimal-static"
    out_a = tmp_path / "location-a" / "ref.petpack"
    out_b = tmp_path / "location-b" / "ref.petpack"
    cli_main(["build", str(source), str(out_a)])
    cli_main(["build", str(source), str(out_b)])
    assert out_a.read_bytes() == out_b.read_bytes()

    tree_a = tmp_path / "trees" / "a"
    tree_b = tmp_path / "trees" / "b"
    _write_init_template(cli_main, tree_a)
    _write_init_template(cli_main, tree_b)
    out_c = tmp_path / "location-c" / "tpl.petpack"
    out_d = tmp_path / "location-d" / "tpl.petpack"
    cli_main(["build", str(tree_a), str(out_c)])
    cli_main(["build", str(tree_b), str(out_d)])
    assert out_c.read_bytes() == out_d.read_bytes()


def test_zip_members_are_actually_deflate_compressed(cli_main, tmp_path):
    """V12-03: a bare ZipInfo defaults to ZIP_STORED per member, so the
    archive used to claim DEFLATE while every member stayed uncompressed.
    Every member must now carry the real compression type, and a
    compressible member must actually shrink."""
    import zipfile

    target = tmp_path / "tpl"
    _write_init_template(cli_main, target)
    out = tmp_path / "tpl.petpack"
    cli_main(["build", str(target), str(out)])

    with zipfile.ZipFile(out) as zf:
        infos = zf.infolist()
        assert infos, "pack has no members"
        for info in infos:
            assert info.compress_type == zipfile.ZIP_DEFLATED, info.filename
        license_info = next(i for i in infos
                            if i.filename == "legal/license.txt")
        assert license_info.compress_size < license_info.file_size


def test_recompression_changes_archive_hash_not_content_digest(
        cli_module, cli_main, tmp_path):
    """archive hash covers the container; the content digest covers only
    canonical manifest + asset bytes.  Recompression must not - and does
    not - change the identity that pins a Revision."""
    import io
    import zipfile as zf_module

    from retirement_pet.petpack.validator import validate_petpack

    target = tmp_path / "tpl"
    _write_init_template(cli_main, target)
    out = tmp_path / "tpl.petpack"
    cli_main(["build", str(target), str(out)])

    # rebuild the same canonical tree with STORED members (the old bug)
    manifest = cli_module._canonicalize_manifest(
        cli_module._source_manifest(target), cli_module._collect_files(target))
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
    buffer = io.BytesIO()
    with zf_module.ZipFile(buffer, "w", zf_module.ZIP_STORED) as zf:
        info = zf_module.ZipInfo(cli_module.MANIFEST_NAME,
                                 date_time=cli_module.FIXED_DATE)
        zf.writestr(info, manifest_bytes)
        for rel in sorted(cli_module._collect_files(target)):
            info = zf_module.ZipInfo(rel, date_time=cli_module.FIXED_DATE)
            zf.writestr(info, cli_module._collect_files(target)[rel])
    stored_pack = tmp_path / "stored.petpack"
    stored_pack.write_bytes(buffer.getvalue())

    deflated = validate_petpack(out.read_bytes())
    stored = validate_petpack(stored_pack.read_bytes())
    assert deflated.accepted and stored.accepted
    assert deflated.content_digest == stored.content_digest
    assert deflated.archive_sha256 != stored.archive_sha256


def test_build_refuses_undeclared_source_files(cli_main, capsys, tmp_path):
    """Unsupported files from the author's project must never silently
    end up inside - or be silently dropped from - a valid release pack."""
    target = tmp_path / "tpl"
    _write_init_template(cli_main, target)
    (target / "scratch-notes.txt").write_text("author junk", encoding="utf-8")

    assert cli_main(["lint", str(target)]) == 1
    lint_out = capsys.readouterr().out
    assert "undeclared files" in lint_out
    assert "scratch-notes.txt" in lint_out
    out = tmp_path / "tpl.petpack"
    assert cli_main(["build", str(target), str(out)]) == 2
    assert not out.exists()

    (target / "scratch-notes.txt").unlink()
    assert cli_main(["build", str(target), str(out)]) == 0


def test_lint_reports_missing_declared_file_and_stale_metadata(
        cli_main, capsys, tmp_path):
    target = tmp_path / "tpl"
    _write_init_template(cli_main, target)
    sprite = target / "assets" / "idle.png"
    original = sprite.read_bytes()
    sprite.unlink()
    assert cli_main(["lint", str(target)]) == 1
    assert "missing from the source tree" in capsys.readouterr().out

    sprite.write_bytes(original)
    manifest_path = target / "petpack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"][0]["byte_size"] = 12345
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                             encoding="utf-8")
    assert cli_main(["lint", str(target)]) == 1
    assert "stale" in capsys.readouterr().out


def test_lint_reports_missing_publisher_ref(cli_main, capsys, tmp_path):
    target = tmp_path / "tpl"
    _write_init_template(cli_main, target)
    manifest_path = target / "petpack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["package"]["publisher_ref"]
    del manifest["publishers"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                             encoding="utf-8")
    assert cli_main(["lint", str(target)]) == 1
    out = capsys.readouterr().out
    assert "publisher_ref is required" in out


def test_preview_uses_real_runtime_without_touching_any_library(
        cli_main, qt_application, tmp_path):
    """Preview decodes the sprite through PackCharacterRuntime with a
    private cache: a real frame with transparency comes out, and no
    library root, receipts or journal appear anywhere."""
    from PySide6.QtGui import QImage

    target = tmp_path / "tpl"
    _write_init_template(cli_main, target)
    out = tmp_path / "tpl.petpack"
    cli_main(["build", str(target), str(out)])

    frames = tmp_path / "frames"
    assert cli_main(["preview", str(out), str(frames)]) == 0
    rendered = next(frames.glob("demo-core.idle-*.png"))
    image = QImage(str(rendered))
    assert not image.isNull()
    assert image.hasAlphaChannel()
    pixels = [image.pixel(x, y) for y in range(image.height())
              for x in range(image.width())]
    assert 0 in {p & 0xFFFFFFFF for p in pixels}             # real alpha
    assert any((p & 0xFF000000) != 0 for p in pixels)        # visible body

    # nothing but the author tree, the pack and the frames were produced
    produced = {p.name for p in tmp_path.iterdir()}
    assert produced == {"tpl", "tpl.petpack", "frames"}


def test_frozen_examples_are_protected_like_official_media(
        cli_module, cli_main, tmp_path):
    """The canonical example packs are frozen release media too: a build
    that targets them is refused before anything is written."""
    examples = cli_module._ROOT / "assets" / "petpack" / "examples"
    frozen = examples / "realistic-retirement-cat-0.1.1.petpack"
    assert frozen.resolve() in cli_module.FROZEN_OUTPUTS
    canary = frozen.read_bytes()

    target = tmp_path / "tpl"
    _write_init_template(cli_main, target)
    assert cli_main(["build", str(target), str(frozen)]) == 2
    assert frozen.read_bytes() == canary


# -- CR-T03 continued / CR-T04: readable captions, bounded sampling -------------

def _n_frame_variant(cli_module, cli_main, tmp_path, name, frame_count,
                     durations_ms=33):
    """Template pack whose core.idle is a frame_count sequence: green and
    blue bodies alternate, and the LAST frame is a clearly different red
    body so its presence is provable in exports and the contact sheet."""
    target = tmp_path / name
    _write_init_template(cli_main, target)

    def body(color):
        return cli_module._png_bytes(
            64, 64, lambda x, y: color
            if 16 <= x < 48 and 18 <= y < 56 else (0, 0, 0, 0))

    green, blue, red = body((0x5A, 0xA0, 0x6E, 0xEB)), \
        body((0x40, 0x60, 0xC0, 0xEB)), body((0xC0, 0x40, 0x45, 0xEB))
    (target / "assets" / "idle.png").write_bytes(green)
    (target / "assets" / "idle2.png").write_bytes(blue)
    (target / "assets" / "final.png").write_bytes(red)
    manifest_path = target / "petpack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for asset_id, path in (("asset.frame2", "assets/idle2.png"),
                           ("asset.final", "assets/final.png")):
        manifest["assets"].append({
            "id": asset_id, "path": path, "media_type": "image/png",
            "rights_ref": "rights.original", "source_ref": "source.original",
            "properties": {"width": 64, "height": 64}})
    assets = ["asset.idle", "asset.frame2"] * ((frame_count + 1) // 2)
    frames = [{"asset": asset_id, "duration_ms": durations_ms}
              for asset_id in assets[:frame_count - 1]]
    frames.append({"asset": "asset.final", "duration_ms": durations_ms})
    manifest["actions"][0]["lifecycle"]["loop"]["renderer"] = {
        "type": "sequence", "frames": frames}
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    out = tmp_path / f"{name}.petpack"
    assert cli_main(["build", str(target), str(out)]) == 0
    return target, out


def test_contact_sheet_captions_render_distinct_bounded_text(
        cli_module, cli_main, qt_application, tmp_path):
    """CR-T03: captions are drawn with a font verified to cover their
    glyphs (no tofu), each caption stays inside its own clipped strip,
    and different frames really show different text."""
    import os

    from PySide6.QtGui import QImage, QRawFont

    _target, out = _sequence_variant(cli_module, cli_main, tmp_path)
    frames = tmp_path / "frames"
    assert cli_main(["preview", str(out), str(frames)]) == 0

    # the selected font really covers every caption codepoint
    labels = {"core.idle", "f0 500ms", "f1 700ms", "seam 1200ms",
              "sequence 2f 1200ms", "fallback -> this row paints core.idle"}
    font = cli_module._contact_font(labels)
    assert font is not None
    raw = QRawFont.fromFont(font)
    assert all(raw.supportsCharacter(ord(ch))
               for label in labels for ch in label if not ch.isspace())

    sheet = QImage(str(frames / "contact-demo.png"))
    top = cli_module.CONTACT_PAD
    caption_y = top + cli_module.CONTACT_CELL
    caption_h = cli_module.CONTACT_CAPTION

    def strip_pixels(x0, x1):
        return [sheet.pixel(x, y) & 0xFFFFFF
                for y in range(caption_y, caption_y + caption_h)
                for x in range(x0, x1)]

    columns = [cli_module.CONTACT_LABEL_WIDTH + cli_module.CONTACT_PAD
               + i * (cli_module.CONTACT_CELL + cli_module.CONTACT_PAD)
               for i in range(3)]
    strips = [strip_pixels(x, x + cli_module.CONTACT_CELL)
              for x in columns]
    for strip in strips:
        assert any(p != 0xFFFFFF for p in strip)      # caption drawn
    assert strips[0] != strips[1] != strips[2]        # f0 vs f1 vs seam
    for i in range(2):
        gap = strip_pixels(columns[i] + cli_module.CONTACT_CELL,
                           columns[i] + cli_module.CONTACT_CELL
                           + cli_module.CONTACT_PAD)
        assert set(gap) == {0xFFFFFF}                 # no bleed across cells

    # Tofu control: private-use codepoints are covered by no installed
    # family, so painting them shows what unreadable .notdef boxes look
    # like in this exact paint stack.  The real caption strip must differ
    # - dark ink alone cannot prove readability (CR-T03).
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter

    assert not raw.supportsCharacter(0xE000)
    tofu = QImage(cli_module.CONTACT_CELL, caption_h,
                  QImage.Format.Format_ARGB32)
    tofu.fill(0xFFFFFFFF)
    painter = QPainter(tofu)
    painter.setFont(font)
    painter.setPen(QColor(0x40, 0x40, 0x40, 0xFF))
    painter.drawText(tofu.rect(), Qt.AlignmentFlag.AlignCenter,
                     "".join(chr(0xE000 + i) for i in range(7)))
    painter.end()
    tofu_pixels = [tofu.pixel(x, y) & 0xFFFFFF
                   for y in range(caption_h)
                   for x in range(cli_module.CONTACT_CELL)]
    assert any(p != 0xFFFFFF for p in tofu_pixels)    # control really drew
    assert strips[1] != tofu_pixels                   # captions are glyphs


def test_contact_sheet_refuses_to_write_without_glyph_coverage(
        cli_module, cli_main, monkeypatch, tmp_path):
    """CR-T03: an environment without a usable font yields NO sheet and a
    clear failure - never an unreadable image presented as success."""
    from retirement_pet.petpack.archive import PetpackArchive
    from retirement_pet.petpack.manifest import parse_manifest
    from retirement_pet.petpack.runtime import PackCharacterRuntime

    _target, out = _sequence_variant(cli_module, cli_main, tmp_path)
    archive = PetpackArchive(out.read_bytes())
    runtime = PackCharacterRuntime(archive,
                                   parse_manifest(archive.manifest_bytes),
                                   "demo")
    idle = next(row for row in runtime.preview_schedule()
                if row.semantic == "core.idle")
    rows = [(idle, [None] * len(idle.points))]
    destination = tmp_path / "contact.png"
    monkeypatch.setattr(cli_module, "_contact_font", lambda labels: None)

    assert cli_module._write_contact_sheet(destination, rows) is False
    assert not destination.exists()


def test_preview_schedule_keeps_last_frame_and_seam_for_long_sequences(
        cli_module, cli_main, tmp_path):
    """CR-T04: a 16-frame MVP sequence plus seam fits whole; a 40-frame
    sequence is bounded to first frames + LAST frame + seam and reports
    exactly how many frames were omitted."""
    from retirement_pet.petpack.archive import PetpackArchive
    from retirement_pet.petpack.manifest import parse_manifest
    from retirement_pet.petpack.runtime import PackCharacterRuntime

    def schedule_for(name, count):
        _target, out = _n_frame_variant(cli_module, cli_main, tmp_path,
                                        name, count)
        archive = PetpackArchive(out.read_bytes())
        runtime = PackCharacterRuntime(
            archive, parse_manifest(archive.manifest_bytes), "demo")
        return next(row for row in runtime.preview_schedule()
                    if row.semantic == "core.idle")

    row = schedule_for("sixteen", 16)
    assert (row.frame_count, row.total_ms, row.omitted_frames) == \
        (16, 16 * 33, 0)
    assert [p.frame_index for p in row.points] == \
        list(range(16)) + [None]
    assert row.points[15].label == f"f15 {33}ms"
    assert row.points[16].label == f"seam {16 * 33}ms"

    row = schedule_for("forty", 40)
    assert (row.frame_count, row.omitted_frames) == (40, 23)
    assert [p.frame_index for p in row.points] == \
        list(range(16)) + [39, None]
    assert row.points[-1].label == f"seam {40 * 33}ms"


def test_sixteen_frame_export_and_sheet_include_distinct_last_frame(
        cli_module, cli_main, tmp_path):
    """CR-T04 acceptance, run as an independent CLI process: all 16 frames
    plus the seam are exported, the red final body shows up in the export
    and in the contact sheet, and the sheet wraps into 8-column bands."""
    import os
    import subprocess
    import sys

    from PySide6.QtGui import QImage

    _target, out = _n_frame_variant(cli_module, cli_main, tmp_path,
                                    "sixteen", 16)
    frames = tmp_path / "frames"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [sys.executable, str(CLI_PATH),
         "preview", str(out), str(frames)],
        capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr + result.stdout

    exported = sorted(frames.glob("demo-core.idle-*.png"))
    assert len(exported) == 17                      # 16 frames + seam
    f0 = QImage(str(frames / "demo-core.idle-000.png"))
    f15 = QImage(str(frames / "demo-core.idle-015.png"))
    seam = QImage(str(frames / "demo-core.idle-016.png"))
    assert f15.pixel(116, 135) != f0.pixel(116, 135)   # red vs green body
    assert all(seam.pixel(x, y) == f0.pixel(x, y)
               for y in range(0, 236, 5) for x in range(0, 232, 5))

    sheet = QImage(str(frames / "contact-demo.png"))
    columns = 8                                     # 17 points -> 3 bands
    bands = 3 + 6                                   # idle chunks + fallbacks
    assert sheet.width() == cli_module.CONTACT_LABEL_WIDTH \
        + cli_module.CONTACT_PAD + columns * \
        (cli_module.CONTACT_CELL + cli_module.CONTACT_PAD)
    assert sheet.height() == cli_module.CONTACT_PAD + bands * \
        (cli_module.CONTACT_CELL + cli_module.CONTACT_CAPTION
         + cli_module.CONTACT_PAD)

    def band_has(color_test):
        pitch = (cli_module.CONTACT_CELL + cli_module.CONTACT_CAPTION
                 + cli_module.CONTACT_PAD)
        for band in range(3):                       # idle chunk bands
            for y in range(cli_module.CONTACT_PAD + band * pitch,
                           cli_module.CONTACT_PAD + band * pitch
                           + cli_module.CONTACT_CELL):
                for x in range(cli_module.CONTACT_LABEL_WIDTH,
                               sheet.width()):
                    # QImage.pixel is 0xAARRGGBB
                    r = (sheet.pixel(x, y) >> 16) & 0xFF
                    g = (sheet.pixel(x, y) >> 8) & 0xFF
                    b = sheet.pixel(x, y) & 0xFF
                    if color_test(r, g, b):
                        return True
        return False

    assert band_has(lambda r, g, b: r > 0x90 and g < 0x70 and b < 0x70)
    assert band_has(lambda r, g, b: g > 0x70 and r < 0x90)   # green frames
    assert band_has(lambda r, g, b: b > 0x90 and g < 0x80)   # blue frames


def test_init_refuses_partial_existing_template(cli_main, tmp_path):
    """A half-finished tree is never clobbered file by file."""
    target = tmp_path / "tpl"
    _write_init_template(cli_main, target)
    (target / "petpack.json").unlink()
    keep = (target / "assets" / "idle.png").read_bytes()

    assert cli_main(["init", str(target)]) == 1
    assert (target / "assets" / "idle.png").read_bytes() == keep
    assert not (target / "petpack.json").exists()


# -- CR-T01: sequence frames live in lifecycle.loop.renderer.frames -------------

def _sequence_variant(cli_module, cli_main, tmp_path, name="seq",
                      durations_ms=(500, 700)):
    """Template pack whose core.idle is a 2-frame sequence of distinct
    64x64 sprites (green body frame + blue body frame)."""
    target = tmp_path / name
    _write_init_template(cli_main, target)
    second = cli_module._png_bytes(
        64, 64, lambda x, y: (0x40, 0x60, 0xC0, 0xEB)
        if 16 <= x < 48 and 18 <= y < 56 else (0, 0, 0, 0))
    (target / "assets" / "idle2.png").write_bytes(second)
    manifest_path = target / "petpack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"].append({
        "id": "asset.frame2", "path": "assets/idle2.png",
        "media_type": "image/png", "rights_ref": "rights.original",
        "source_ref": "source.original",
        "properties": {"width": 64, "height": 64}})
    manifest["actions"][0]["lifecycle"]["loop"]["renderer"] = {
        "type": "sequence",
        "frames": [{"asset": "asset.idle", "duration_ms": durations_ms[0]},
                   {"asset": "asset.frame2", "duration_ms": durations_ms[1]}]}
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    out = tmp_path / f"{name}.petpack"
    assert cli_main(["build", str(target), str(out)]) == 0
    return target, out


def test_lint_reports_sequence_frames_durations_and_decode_budget(
        cli_module, cli_main, capsys, tmp_path):
    """CR-T01: valid multi-frame sequences report real counts against
    independent expected values: 2 frames, 500+700=1200 ms total,
    (4096+4096)*4 = 32768 decoded bytes = 32 KiB."""
    target, _out = _sequence_variant(cli_module, cli_main, tmp_path)

    assert cli_main(["lint", str(target)]) == 0
    out = capsys.readouterr().out
    contact = next(line for line in out.splitlines()
                   if line.startswith("contact"))
    assert "core.idle" in contact and "sequence" in contact
    assert "frames=2" in contact
    assert "total_ms=1200" in contact
    assert "decoded~32 KiB" in contact
    budget = next(line for line in out.splitlines()
                  if line.startswith("budget"))
    assert "~0.0 MiB" in budget and "48 MiB" in budget


@pytest.mark.parametrize("durations,needle", [
    ((1, 700), "min 33ms"),
    ((0, 700), "non-positive"),
])
def test_lint_rejects_invalid_frame_durations_like_the_validator(
        cli_module, cli_main, capsys, tmp_path, durations, needle):
    """CR-T01: over-short and non-positive durations are lint errors, and
    the validator rejects the same pack - lint never stays clean for a
    pack the GUI gate refuses."""
    target, out = _sequence_variant(cli_module, cli_main, tmp_path,
                                    durations_ms=durations)

    assert cli_main(["lint", str(target)]) == 1
    assert needle in capsys.readouterr().out
    assert cli_main(["validate", str(out)]) == 1


def test_lint_rejects_empty_sequence_renderer(
        cli_module, cli_main, capsys, tmp_path):
    target, _stale_out = _sequence_variant(cli_module, cli_main, tmp_path)
    manifest_path = target / "petpack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["actions"][0]["lifecycle"]["loop"]["renderer"]["frames"] = []
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    out = tmp_path / "empty.petpack"
    assert cli_main(["build", str(target), str(out)]) == 0

    assert cli_main(["lint", str(target)]) == 1
    assert "no frames" in capsys.readouterr().out
    assert cli_main(["validate", str(out)]) == 1


def test_preview_schedule_exposes_frame_starts_seam_and_fallback(
        cli_module, cli_main, tmp_path):
    """The sampling plan is derived where the painter lives: frame starts
    at cumulative durations, the loop seam at total_ms, and one fallback
    row per unbound core semantic."""
    from retirement_pet.petpack.archive import PetpackArchive
    from retirement_pet.petpack.manifest import parse_manifest
    from retirement_pet.petpack.runtime import PackCharacterRuntime

    _target, out = _sequence_variant(cli_module, cli_main, tmp_path)
    archive = PetpackArchive(out.read_bytes())
    runtime = PackCharacterRuntime(archive,
                                   parse_manifest(archive.manifest_bytes),
                                   "demo")
    rows = {row.semantic: row for row in runtime.preview_schedule()}

    idle = rows["core.idle"]
    assert (idle.kind, idle.frame_count, idle.total_ms) == \
        ("sequence", 2, 1200)
    assert [(p.elapsed_ms, p.label, p.frame_index) for p in idle.points] == [
        (0, "f0 500ms", 0), (500, "f1 700ms", 1), (1200, "seam 1200ms", None)]
    fallback = [rows[s] for s in ("core.work", "core.rest", "core.eat",
                                  "core.exercise", "core.meeting",
                                  "core.music")]
    assert all(row.kind == "fallback"
               and row.points[0].label == "->idle" for row in fallback)


# -- CR-T02: preview paints through compute_body_layout + render_body -----------

def _alpha_bbox(image):
    xs, ys = [], []
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixel(x, y) & 0xFF000000:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def test_preview_paints_through_body_layout_with_geometry_report(
        cli_main, qt_application, capsys, tmp_path):
    """CR-T02: preview composes through the window's own layout transform
    (232x236 viewport, geometry scale, foot anchor) instead of dumping
    decoded originals.  Template constants predict, independently: scale
    min(230/52, 224/56, 230/58) = 3.9655; body pixels (canvas 16..48 x
    18..56) map to ~(52, 61)..(179, 212) with the foot at x=116."""
    from PySide6.QtGui import QImage

    target = tmp_path / "tpl"
    _write_init_template(cli_main, target)
    out = tmp_path / "tpl.petpack"
    cli_main(["build", str(target), str(out)])

    frames = tmp_path / "frames"
    assert cli_main(["preview", str(out), str(frames)]) == 0
    text = capsys.readouterr().out
    assert "viewport 232x236" in text
    assert "scale 3.9655" in text
    assert "foot (116.0, 228.1)" in text
    assert "clamped=True" in text

    image = QImage(str(next(frames.glob("demo-core.idle-*.png"))))
    assert (image.width(), image.height()) == (232, 236)
    left, top, right, bottom = _alpha_bbox(image)
    assert abs(left - 52) <= 3 and abs(right - 179) <= 3
    assert abs(top - 61) <= 3 and abs(bottom - 212) <= 3
    assert abs((left + right) / 2 - 116.0) <= 3     # foot x stays centered


def _geometry_variant(cli_main, tmp_path, name, geometry):
    target = tmp_path / name
    _write_init_template(cli_main, target)
    manifest_path = target / "petpack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["characters"][0]["geometry"] = geometry
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    out = tmp_path / f"{name}.petpack"
    assert cli_main(["build", str(target), str(out)]) == 0
    return out


def test_preview_geometry_reports_portrait_margins_and_biased_anchor(
        cli_main, qt_application, capsys, tmp_path):
    """CR-T02: portrait canvases, wide transparent margins and off-centre
    anchors each produce the geometry numbers the desktop layout rule
    dictates - hand-derived here, not read back from the layout code."""
    cases = [
        # portrait 64x128: scale = 230/128 = 1.7969, foot on the bottom line
        ("portrait", {
            "logical_canvas": {"width": 64, "height": 128},
            "base_anchor": {"x": 32, "y": 128},
            "bubble_anchor": {"x": 32, "y": 4},
            "content_bounds": {"x": 8, "y": 8, "width": 48, "height": 116},
            "motion_bounds": {"x": 0, "y": 0, "width": 64, "height": 128},
            "hit_regions": [
                {"shape": "rect", "x": 8, "y": 8, "width": 48,
                 "height": 116}],
            "reference_height": 128,
        }, "scale 1.7969", "foot (116.0, 234.0)", "clamped=False"),
        # 128x128 canvas, sprite only in the middle 64x64: scale 3.5
        ("margins", {
            "logical_canvas": {"width": 128, "height": 128},
            "base_anchor": {"x": 64, "y": 96},
            "bubble_anchor": {"x": 64, "y": 28},
            "content_bounds": {"x": 32, "y": 32, "width": 64, "height": 64},
            "motion_bounds": {"x": 32, "y": 32, "width": 64, "height": 64},
            "hit_regions": [
                {"shape": "rect", "x": 32, "y": 32, "width": 64,
                 "height": 64}],
            "reference_height": 60,
        }, "scale 3.5000", "foot (116.0, 234.0)", "clamped=False"),
        # base anchor at canvas x=60: the whole body shifts right edge-ward
        ("biased", {
            "logical_canvas": {"width": 64, "height": 64},
            "base_anchor": {"x": 60, "y": 60},
            "bubble_anchor": {"x": 32, "y": 10},
            "content_bounds": {"x": 8, "y": 8, "width": 48, "height": 52},
            "motion_bounds": {"x": 4, "y": 4, "width": 56, "height": 58},
            "hit_regions": [
                {"shape": "rect", "x": 8, "y": 8, "width": 48,
                 "height": 52}],
            "reference_height": 52,
        }, "clamped=True", "foot (222.1, 228.1)"),
    ]
    for name, geometry, *needles in cases:
        out = _geometry_variant(cli_main, tmp_path, name, geometry)
        frames = tmp_path / f"{name}-frames"
        assert cli_main(["preview", str(out), str(frames)]) == 0, name
        text = capsys.readouterr().out
        for needle in needles:
            if needle is not None:
                assert needle in text, (name, needle)


def test_preview_sequence_samples_frame_starts_and_loop_seam(
        cli_module, cli_main, qt_application, capsys, tmp_path):
    """CR-T02: sequence previews use real frame timing - the seam sample
    (t=total) wraps to frame 0 and paints pixel-identical to t=0, while
    the second frame really shows the second sprite."""
    from PySide6.QtGui import QImage

    _target, out = _sequence_variant(cli_module, cli_main, tmp_path)
    frames = tmp_path / "frames"
    assert cli_main(["preview", str(out), str(frames)]) == 0
    text = capsys.readouterr().out
    assert "total 1200ms -> 3 sample(s)" in text

    f0 = QImage(str(frames / "demo-core.idle-000.png"))
    f1 = QImage(str(frames / "demo-core.idle-001.png"))
    f2 = QImage(str(frames / "demo-core.idle-002.png"))
    assert f1.pixel(116, 135) != f0.pixel(116, 135)   # different sprite body
    assert all(f2.pixel(x, y) == f0.pixel(x, y)
               for y in range(0, 236, 5) for x in range(0, 232, 5))


# -- CR-T03: the contact sheet is a real image artefact --------------------------

def test_contact_sheet_labels_actions_frames_and_fallback(
        cli_module, cli_main, qt_application, tmp_path):
    """CR-T03: preview writes one contact sheet per character; the 2-frame
    sequence layout is 3 columns by 7 rows (idle + 6 fallback rows), the
    row labels and per-cell captions carry ink, and painted samples show
    up in both the action row and the fallback rows."""
    from PySide6.QtGui import QImage

    _target, out = _sequence_variant(cli_module, cli_main, tmp_path)
    frames = tmp_path / "frames"
    assert cli_main(["preview", str(out), str(frames)]) == 0

    cli = cli_module
    sheet = QImage(str(frames / "contact-demo.png"))
    columns = 3
    rows = 7
    assert sheet.width() == cli.CONTACT_LABEL_WIDTH + cli.CONTACT_PAD + \
        columns * (cli.CONTACT_CELL + cli.CONTACT_PAD)
    assert sheet.height() == cli.CONTACT_PAD + rows * \
        (cli.CONTACT_CELL + cli.CONTACT_CAPTION + cli.CONTACT_PAD)

    def band_ink(x0, x1, y0, y1, dark=False):
        for y in range(y0, min(y1, sheet.height())):
            for x in range(x0, min(x1, sheet.width())):
                pixel = sheet.pixel(x, y) & 0xFFFFFF
                if (pixel != 0xFFFFFF) and (not dark or pixel < 0x909090):
                    return True
        return False

    row_pitch = cli.CONTACT_CELL + cli.CONTACT_CAPTION + cli.CONTACT_PAD
    assert band_ink(0, cli.CONTACT_LABEL_WIDTH, 4, 4 + cli.CONTACT_CELL,
                    dark=True)          # row label text: semantic + kind
    caption_top = 4 + cli.CONTACT_CELL
    assert band_ink(cli.CONTACT_LABEL_WIDTH, sheet.width(),
                    caption_top, caption_top + cli.CONTACT_CAPTION,
                    dark=True)          # per-cell caption: f0/f1/seam
    assert band_ink(cli.CONTACT_LABEL_WIDTH, sheet.width(), 4,
                    4 + cli.CONTACT_CELL)          # painted idle frames
    assert band_ink(cli.CONTACT_LABEL_WIDTH, sheet.width(),
                    4 + row_pitch, 4 + row_pitch + cli.CONTACT_CELL)


# -- CR-T04: continuation bands keep GLOBAL frame labels -------------------------


def _sheet_rows_with_placeholders(cli_module, rows):
    """Pair every PreviewRow with one blank cell per sample, exactly the
    shapes _write_contact_sheet bands and captions are computed from."""
    from PySide6.QtGui import QImage

    rows_cells = []
    for row in rows:
        cells = [QImage(8, 8, QImage.Format.Format_ARGB32)
                 for _point in row.points]
        rows_cells.append((row, cells))
    return rows_cells


def test_contact_bands_and_captions_keep_global_frame_labels(cli_module,
                                                             cli_main,
                                                             tmp_path):
    """CR-T04: the labels fed to the painter must show the frame each
    cell really holds - band two continues f8.., the seam says seam."""
    from retirement_pet.petpack.archive import PetpackArchive
    from retirement_pet.petpack.manifest import parse_manifest
    from retirement_pet.petpack.runtime import PackCharacterRuntime

    _target, out = _n_frame_variant(cli_module, cli_main, tmp_path,
                                    "sixteen", 16)
    archive = PetpackArchive(out.read_bytes())
    runtime = PackCharacterRuntime(archive,
                                   parse_manifest(archive.manifest_bytes),
                                   "demo")
    row = next(r for r in runtime.preview_schedule() if r.kind == "sequence")
    assert (row.frame_count, row.omitted_frames) == (16, 0)
    assert len(row.points) == 17              # 16 frames + loop seam

    bands = cli_module._contact_bands(_sheet_rows_with_placeholders(
        cli_module, [row]))
    assert [chunk for _r, chunk, _cells in bands] == [0, 8, 16]
    captions = [cli_module._band_caption(band_row, chunk, column)
                for band_row, chunk, cells in bands
                for column in range(len(cells))]
    assert captions == [f"f{i} 33ms" for i in range(16)] + ["seam 528ms"]
    assert captions[8:16] == [f"f{i} 33ms" for i in range(8, 16)]
    assert captions[-1] == "seam 528ms"


def test_truncated_rows_keep_last_frame_seam_and_omission_labels(
        cli_module, cli_main, tmp_path):
    from retirement_pet.petpack.archive import PetpackArchive
    from retirement_pet.petpack.manifest import parse_manifest
    from retirement_pet.petpack.runtime import PackCharacterRuntime

    _target, out = _n_frame_variant(cli_module, cli_main, tmp_path,
                                    "forty", 40)
    archive = PetpackArchive(out.read_bytes())
    runtime = PackCharacterRuntime(archive,
                                   parse_manifest(archive.manifest_bytes),
                                   "demo")
    row = next(r for r in runtime.preview_schedule() if r.kind == "sequence")
    assert (row.frame_count, row.omitted_frames) == (40, 23)
    assert len(row.points) == 18              # f0..f15, last frame, seam

    bands = cli_module._contact_bands(_sheet_rows_with_placeholders(
        cli_module, [row]))
    captions = [cli_module._band_caption(band_row, chunk, column)
                for band_row, chunk, cells in bands
                for column in range(len(cells))]
    assert captions[:8] == [f"f{i} 33ms" for i in range(8)]
    assert captions[8:16] == [f"f{i} 33ms" for i in range(8, 16)]
    assert captions[-2] == "f39 33ms"
    assert captions[-1] == "seam 1320ms"

    lines = cli_module._row_sub_lines(row)
    assert lines == ["seq 40f 1320ms", "+23 frames omitted"]


def test_omission_note_line_fits_the_label_column_unelided(cli_module):
    """CR-T04: the omission note lives on its own short line, and that
    line provably fits without elision (the old combined sub-label lost
    "+23 omitted" to the ellipsis)."""
    from PySide6.QtGui import QFontMetrics

    from retirement_pet.petpack.runtime import PreviewRow

    row = PreviewRow(semantic="x", action_value="core.idle",
                     kind="sequence", frame_count=40, total_ms=1320,
                     points=(), omitted_frames=23)
    lines = cli_module._row_sub_lines(row)
    assert lines == ["seq 40f 1320ms", "+23 frames omitted"]

    font = cli_module._contact_font(set("".join(lines)))
    assert font is not None
    metrics = QFontMetrics(font)
    available = cli_module.CONTACT_LABEL_WIDTH - 8
    assert metrics.horizontalAdvance(lines[0]) <= available
    assert metrics.horizontalAdvance(lines[1]) <= available
