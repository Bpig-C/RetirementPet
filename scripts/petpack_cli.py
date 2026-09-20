"""petpack author toolchain (PETPACK_SPEC 23; ADR-V2-018; work order V12-03).

The complete author workflow (GUI import closes the loop; there is no
``install-local``: the author tool never writes a LibraryRoot):

    python scripts/petpack_cli.py init <dir>                 # buildable template
    python scripts/petpack_cli.py lint <source_dir>          # actionable findings
    python scripts/petpack_cli.py build <source_dir> <out>   # deterministic pack
    python scripts/petpack_cli.py validate <out.petpack>     # full engine validator
    python scripts/petpack_cli.py preflight <out.petpack>    # GUI import gate
    python scripts/petpack_cli.py preview <out.petpack> <dir>[ <character>]
    python scripts/petpack_cli.py inspect <out.petpack>

``build`` is deterministic: members sorted, fixed timestamps, fixed
permissions, canonical JSON, DEFLATE-compressed members - identical input
trees produce identical archive hashes (PETPACK_SPEC 23).  Build REFUSES
undeclared source files: nothing from the author's project may silently
end up inside (or be silently dropped from) a valid release pack.

Two identities must not be confused (PETPACK_SPEC 6/12): the
``archive_sha256`` covers the whole container (framing + compression) and
changes with every rebuild of a byte-different archive; the
``content_digest`` covers only the canonical manifest + asset bytes and is
compression-independent.  A new Revision is required when the
``content_digest`` changes - never to accommodate a mere re-compression.

``preview`` renders through the REAL PackCharacterRuntime offscreen with a
private cache: it never touches the active character, ContextStore, user
configuration or the user library, and it never plays audio.

The tool never executes anything from the pack; ``validate`` re-runs the
FULL Runtime validator on the built container because author-tool reports
are not trusted by the Runtime.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

# src-layout bootstrap: the project package is not pip-installed
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

MANIFEST_NAME = "petpack.json"
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
_ROOT = Path(__file__).resolve().parent.parent
# Frozen release media (official packs + canonical examples) are never
# overwritten by a rebuild, however convenient that would be.
FROZEN_OUTPUTS = frozenset(
    (_ROOT / "assets" / "petpack" / rel).resolve(strict=False)
    for rel in (
        "retirement-cat-official.petpack",
        "retirement-cat-official-1.0.1.petpack",
        "examples/realistic-retirement-cat-0.1.0.petpack",
        "examples/realistic-retirement-cat-0.1.1.petpack",
    )
)

MAX_FRAMES_PER_ACTION = 300     # mirrors petpack.validator
MIN_FRAME_DURATION_MS = 33      # visible refresh capped at 30 FPS
DECODED_WORKING_SET_BUDGET = 48 * 1024 * 1024  # runtime global pixmap budget


# -- shared helpers ------------------------------------------------------------

def _source_manifest(source_dir: Path) -> dict:
    path = source_dir / MANIFEST_NAME
    if not path.is_file():
        raise SystemExit(f"missing {MANIFEST_NAME} in {source_dir}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"{MANIFEST_NAME} is not readable JSON: {exc}") \
            from exc


def _collect_files(source_dir: Path) -> dict[str, bytes]:
    """Every file under the source dir except the manifest itself."""
    files: dict[str, bytes] = {}
    for path in sorted(source_dir.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(source_dir).as_posix()
        if rel == MANIFEST_NAME:
            continue
        files[rel] = path.read_bytes()
    return files


def _declared_paths(manifest: dict) -> set[str]:
    declared: set[str] = set()
    for section in ("assets", "legal_files"):
        for entry in manifest.get(section) or []:
            if isinstance(entry, dict) and entry.get("path"):
                declared.add(str(entry["path"]))
    return declared


def _canonicalize_manifest(manifest: dict, files: dict[str, bytes]) -> dict:
    """Fill byte_size/sha256 for every declared asset/legal file."""
    for entry in manifest.get("assets", []) + manifest.get("legal_files", []):
        rel = str(entry.get("path", ""))
        if rel in files:
            entry["byte_size"] = len(files[rel])
            entry["sha256"] = hashlib.sha256(files[rel]).hexdigest()
    return manifest


def _member_info(name: str) -> zipfile.ZipInfo:
    # A bare ZipInfo defaults to ZIP_STORED per member - the archive would
    # claim DEFLATE while every member stayed uncompressed (V12-03).  The
    # member type is pinned explicitly; framing does not affect the
    # content_digest, only the archive hash.
    info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def _safe_output_path(out_path: Path) -> Path:
    resolved = out_path.resolve(strict=False)
    if resolved in FROZEN_OUTPUTS:
        raise ValueError("refusing to overwrite frozen official release media")
    if resolved.suffix.lower() != ".petpack":
        raise ValueError("output filename must end with .petpack")
    return resolved


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.",
                suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# -- template (init) ------------------------------------------------------------

def _png_bytes(width: int, height: int, pixel) -> bytes:
    """Deterministic minimal PNG writer (RGBA, filter 0, level 9)."""
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixel(x, y))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6,
                                       0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
            chunk(b"IEND", b""))


def _template_idle_png() -> bytes:
    """64x64 transparent sprite with an opaque rounded body the runtime can
    verify as a first frame."""
    body = (0x5A, 0xA0, 0x6E, 0xEB)  # RGBA, alpha 235

    def pixel(x: int, y: int) -> tuple[int, int, int, int]:
        if 16 <= x < 48 and 18 <= y < 56:
            # knock out the four corners for a rounded silhouette
            dx = min(x - 16, 47 - x)
            dy = min(y - 18, 55 - y)
            if dx + dy < 3:
                return (0, 0, 0, 0)
            return body
        return (0, 0, 0, 0)

    return _png_bytes(64, 64, pixel)


def _template_thumbnail_png() -> bytes:
    body = (0x5A, 0xA0, 0x6E, 0xEB)
    return _png_bytes(32, 32, lambda x, y: body)


def _template_license() -> str:
    return (
        "Pack license / 包许可说明\n"
        "========================\n\n"
        "All sprites, geometry and metadata in this pack are the original "
        "work of the pack author (community.example).  No third-party "
        "material is included.\n\n"
        "Permission is granted for personal use and redistribution as part "
        "of a RetirementPet character pack, provided this notice and "
        "attribution are preserved.\n\n"
        "本包内全部图像与几何数据均为作者原创内容，不含第三方素材；"
        "允许随 RetirementPet 角色包进行个人使用与再分发，须保留本声明与署名。\n"
    )


def _template_manifest() -> dict:
    return {
        "schema_version": "1.0",
        "package": {
            "publisher_id": "community.example",
            "publisher_ref": "community.example",
            "id": "my-pack",
            "version": "0.1.0",
            "display_name": {"zh-CN": "我的角色包", "en": "My Character Pack"},
        },
        "publishers": [
            {
                "id": "community.example",
                "display_name": "Example Author",
                "contact": None,
                "homepage": None,
            }
        ],
        "series": {
            "id": "main",
            "display_name": {"zh-CN": "主系列"},
        },
        "sources": [
            {
                "id": "source.original",
                "kind": "original_creation",
                "title": "My original sprite",
                "creator": "community.example",
                "locator": None,
                "accessed_at": None,
            }
        ],
        "rights_declarations": [
            {
                "id": "rights.original",
                "basis": "original",
                "claimant_ref": "community.example",
                "attribution": "Example Author",
                "license": {
                    "spdx": None,
                    "custom_name": None,
                    "legal_file_ref": "legal.license",
                },
                "scope_claimed": ["personal_use", "redistribution"],
                "notes": None,
            }
        ],
        "legal_files": [
            {
                "id": "legal.license",
                "path": "legal/license.txt",
                "media_type": "text/plain",
                "purpose": "license_text",
            }
        ],
        "assets": [
            {
                "id": "asset.idle",
                "path": "assets/idle.png",
                "media_type": "image/png",
                "rights_ref": "rights.original",
                "source_ref": "source.original",
                "properties": {"width": 64, "height": 64},
            },
            {
                "id": "asset.thumb",
                "path": "assets/thumbnail.png",
                "media_type": "image/png",
                "rights_ref": "rights.original",
                "source_ref": "source.original",
                "properties": {"width": 32, "height": 32},
            },
        ],
        "actions": [
            {
                "id": "action.idle",
                "semantic": "core.idle",
                "loop_modes": ["repeat"],
                "policy_tags": ["silent", "meeting_safe", "dnd_safe"],
                "audio": None,
                "interrupt": {"policy": "immediate", "max_exit_ms": 0},
                "user_modes": ["auto", "manual", "disabled"],
                "lifecycle": {
                    "loop": {
                        "renderer": {"type": "static", "asset": "asset.idle"},
                    },
                },
            }
        ],
        "characters": [
            {
                "id": "demo",
                "display_name": {"zh-CN": "示例角色"},
                "thumbnail_asset": "asset.thumb",
                "default_variant": None,
                "variants": [],
                "text_profile_refs": [],
                "recommended_profiles": {},
                "rig_bindings": {},
                "rig_contract_ref": None,
                "actions": {"core.idle": "action.idle"},
                "geometry": {
                    "logical_canvas": {"width": 64, "height": 64},
                    "base_anchor": {"x": 32, "y": 60},
                    "bubble_anchor": {"x": 32, "y": 10},
                    "content_bounds": {"x": 8, "y": 8, "width": 48,
                                       "height": 52},
                    "motion_bounds": {"x": 4, "y": 4, "width": 56,
                                      "height": 58},
                    "hit_regions": [
                        {"shape": "rect", "x": 8, "y": 8, "width": 48,
                         "height": 52},
                    ],
                    "reference_height": 52,
                },
            }
        ],
        "compatibility": {
            "engine_min": "2.0.0",
            "engine_max_exclusive": "3.0.0",
            "required_capabilities": ["renderer.static.v1", "asset.png.v1"],
            "optional_capabilities": [],
        },
    }


def init(target: Path) -> int:
    manifest_path = target / MANIFEST_NAME
    if manifest_path.exists():
        print(f"refusing to overwrite existing {manifest_path}",
              file=sys.stderr)
        return 1
    files = {
        MANIFEST_NAME: json.dumps(
            _template_manifest(), ensure_ascii=False, indent=2
        ).encode("utf-8") + b"\n",
        "assets/idle.png": _template_idle_png(),
        "assets/thumbnail.png": _template_thumbnail_png(),
        "legal/license.txt": _template_license().encode("utf-8"),
    }
    existing = [name for name in files
                if (target / name).exists()]
    if existing:
        print("refusing to overwrite existing template files: "
              + ", ".join(sorted(existing)), file=sys.stderr)
        return 1
    for name, payload in files.items():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    print(f"initialized buildable template pack in {target}")
    print("next: python scripts/petpack_cli.py lint " + str(target))
    return 0


# -- lint ------------------------------------------------------------------------

def _action_frames(action: dict) -> tuple[str, list[tuple[str, int]], str | None]:
    """(renderer kind, [(asset id, duration_ms)], error) for one action."""
    lifecycle = action.get("lifecycle")
    if not isinstance(lifecycle, dict):
        return "none", [], "no lifecycle section"
    section = lifecycle.get("loop") or lifecycle.get("enter")
    if not isinstance(section, dict):
        return "none", [], "lifecycle has neither loop nor enter"
    renderer = section.get("renderer")
    if not isinstance(renderer, dict):
        return "none", [], "lifecycle section has no renderer"
    kind = renderer.get("type")
    if kind == "static":
        return "static", [(str(renderer.get("asset")), 0)], None
    if kind == "sequence":
        # The protocol keeps frames INSIDE the renderer (validator.py and
        # runtime.py both read renderer["frames"]); reading them anywhere
        # else reported zero frames for valid packs (CR-T01).
        frames = []
        for frame in renderer.get("frames") or []:
            if not isinstance(frame, dict):
                continue
            raw = frame.get("duration_ms", MIN_FRAME_DURATION_MS)
            try:
                duration = int(raw)
            except (TypeError, ValueError):
                return ("sequence", [],
                        f"frame duration_ms {raw!r} is not an integer")
            frames.append((str(frame.get("asset")), duration))
        if not frames:
            return "sequence", [], "sequence renderer has no frames"
        return "sequence", frames, None
    return str(kind), [], f"unsupported renderer type {kind!r}"


def _rect_within(rect: dict, canvas: dict) -> bool:
    return (0 <= rect.get("x", -1)
            and 0 <= rect.get("y", -1)
            and rect.get("x", 0) + rect.get("width", 0) <= canvas.get("width", 0)
            and rect.get("y", 0) + rect.get("height", 0) <= canvas.get("height", 0))


def _budget_report(manifest: dict, findings: list[str]) -> list[str]:
    """Per-action contact map + frame/duration/decode-budget lines."""
    assets_by_id = {a.get("id"): a for a in manifest.get("assets", [])
                    if isinstance(a, dict)}
    actions_by_id = {a.get("id"): a for a in manifest.get("actions", [])
                     if isinstance(a, dict)}
    lines: list[str] = []
    total_decoded = 0
    for character in manifest.get("characters", []):
        if not isinstance(character, dict):
            continue
        for semantic, action_id in sorted(
                (character.get("actions") or {}).items()):
            action = actions_by_id.get(action_id)
            if action is None:
                continue
            kind, frames, error = _action_frames(action)
            if error is not None:
                findings.append(
                    f"E: action {action_id}: {error} "
                    f"(the runtime draws loop first, then enter)")
                continue
            if len(frames) > MAX_FRAMES_PER_ACTION:
                findings.append(
                    f"E: action {action_id} has {len(frames)} frames "
                    f"(max {MAX_FRAMES_PER_ACTION})")
            if kind == "sequence":
                # static actions carry a 0 sentinel duration; only sequence
                # frames are real wall-clock durations.
                non_positive = [d for _a, d in frames if d <= 0]
                if non_positive:
                    findings.append(
                        f"E: action {action_id} has non-positive frame "
                        f"duration(s) {non_positive}; the runtime cannot "
                        f"display them")
            durations = [d for _a, d in frames if d > 0]
            total_ms = sum(durations)
            if durations and min(durations) < MIN_FRAME_DURATION_MS:
                findings.append(
                    f"E: action {action_id} has a {min(durations)}ms "
                    f"frame (min {MIN_FRAME_DURATION_MS}ms)")
            decoded = 0
            for asset_id, _duration in frames:
                asset = assets_by_id.get(asset_id)
                if not isinstance(asset, dict):
                    findings.append(
                        f"E: action {action_id} references missing asset "
                        f"{asset_id!r}")
                    continue
                properties = asset.get("properties") or {}
                decoded += (int(properties.get("width", 0))
                            * int(properties.get("height", 0)) * 4)
            total_decoded += decoded
            lines.append(
                f"contact  {character.get('id')}/{semantic:<14} {kind:<8} "
                f"action={action_id}  frames={len(frames)}  "
                f"total_ms={total_ms}  decoded~{decoded / 1024:.0f} KiB")
    lines.append(f"budget   decoded working set ~"
                 f"{total_decoded / (1024 * 1024):.1f} MiB "
                 f"(runtime budget {DECODED_WORKING_SET_BUDGET // (1024 * 1024)} MiB)")
    if total_decoded > DECODED_WORKING_SET_BUDGET:
        findings.append(
            f"E: estimated decoded working set {total_decoded} bytes "
            f"exceeds the {DECODED_WORKING_SET_BUDGET}-byte runtime budget")
    return lines


def lint(source_dir: Path) -> int:
    """Actionable source-tree checks; never writes anything."""
    findings: list[str] = []
    try:
        manifest = _source_manifest(source_dir)
    except SystemExit as exc:
        print(f"lint E: {exc}")
        return 1
    if manifest.get("schema_version") != "1.0":
        findings.append(f"E: unsupported schema_version "
                        f"{manifest.get('schema_version')!r}")

    package = manifest.get("package") or {}
    publisher_ref = package.get("publisher_ref")
    publisher_ids = {p.get("id") for p in manifest.get("publishers", [])
                     if isinstance(p, dict)}
    if not isinstance(publisher_ref, str) or not publisher_ref:
        findings.append(
            "E: package.publisher_ref is required (PETPACK_SPEC 6.1); add it "
            "and declare the publisher under publishers[]")
    elif publisher_ref not in publisher_ids:
        findings.append(
            f"E: publisher_ref {publisher_ref!r} does not resolve to any "
            f"publishers[] entry")

    files = _collect_files(source_dir)
    declared = _declared_paths(manifest)
    for rel in sorted(declared - set(files)):
        findings.append(f"E: declared file is missing from the source "
                        f"tree: {rel}")
    undeclared = sorted(set(files) - declared)
    if undeclared:
        findings.append(
            "E: undeclared files would be silently left out of the pack; "
            "declare them under assets/legal_files or remove them: "
            + ", ".join(undeclared))
    for entry in (manifest.get("assets") or []) + \
            (manifest.get("legal_files") or []):
        if not isinstance(entry, dict):
            continue
        rel = str(entry.get("path", ""))
        payload = files.get(rel)
        if payload is None:
            continue
        # Absent metadata is what build fills in; only DECLARED but drifted
        # values are worth a finding.
        declared_hash = entry.get("sha256")
        declared_size = entry.get("byte_size")
        if (declared_hash is not None or declared_size is not None) and \
                (declared_hash != hashlib.sha256(payload).hexdigest()
                 or declared_size != len(payload)):
            findings.append(
                f"W: declared byte_size/sha256 for {rel} is stale; "
                f"build refreshes it")

    thumbnail_assets = {
        str(c.get("thumbnail_asset"))
        for c in manifest.get("characters", [])
        if isinstance(c, dict) and c.get("thumbnail_asset")
    }
    png_paths = [rel for rel in sorted(declared & set(files))
                 if rel.lower().endswith(".png")]
    pixels: dict[str, tuple[int, int, bool]] = {}
    if png_paths:
        pixels = _inspect_pngs(source_dir, png_paths, findings)
    for entry in manifest.get("assets") or []:
        if not isinstance(entry, dict):
            continue
        rel = str(entry.get("path", ""))
        properties = entry.get("properties") or {}
        actual = pixels.get(rel)
        if actual is not None:
            width, height, transparent = actual
            if (properties.get("width"), properties.get("height")) != \
                    (width, height):
                findings.append(
                    f"E: asset {rel} is {width}x{height} but declared "
                    f"{properties.get('width')}x{properties.get('height')}")
            if not transparent and entry.get("id") not in thumbnail_assets:
                findings.append(
                    f"W: asset {rel} has no transparency; it will draw as "
                    f"an opaque rectangle over the wallpaper")

    for character in manifest.get("characters", []):
        if not isinstance(character, dict):
            continue
        geometry = character.get("geometry") or {}
        canvas = geometry.get("logical_canvas") or {}
        for anchor in ("base_anchor", "bubble_anchor"):
            point = geometry.get(anchor) or {}
            if not (0 <= point.get("x", -1) <= canvas.get("width", 0)
                    and 0 <= point.get("y", -1) <= canvas.get("height", 0)):
                findings.append(
                    f"E: character {character.get('id')!r} {anchor} is "
                    f"outside the logical canvas")
        for bounds in ("content_bounds", "motion_bounds"):
            rect = geometry.get(bounds) or {}
            if not _rect_within(rect, canvas):
                findings.append(
                    f"E: character {character.get('id')!r} {bounds} is not "
                    f"inside the logical canvas")
        if not int(geometry.get("reference_height", 0) or 0) > 0:
            findings.append(
                f"E: character {character.get('id')!r} needs a positive "
                f"reference_height")
        if "core.idle" not in (character.get("actions") or {}):
            findings.append(
                f"E: character {character.get('id')!r} has no core.idle "
                f"binding; the runtime cannot fall back for it")

    lines = _budget_report(manifest, findings)
    for line in lines:
        print(line)
    if findings:
        for finding in findings:
            print(f"lint {finding}")
        print(f"lint result   {len(findings)} finding(s)")
        return 1
    print("lint result   clean")
    return 0


def _inspect_pngs(source_dir: Path, rels: list[str],
                  findings: list[str]) -> dict[str, tuple[int, int, bool]]:
    """Decode PNGs offscreen; returns {rel: (width, height, has_transparency)}."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage

    pixels: dict[str, tuple[int, int, bool]] = {}
    for rel in rels:
        image = QImage(str(source_dir / rel))
        if image.isNull():
            findings.append(f"E: asset {rel} is not a decodable PNG")
            continue
        transparent = image.hasAlphaChannel() and any(
            (image.pixel(x, y) & 0xFF000000) == 0
            for y in range(0, image.height(), max(1, image.height() // 16))
            for x in range(0, image.width(), max(1, image.width() // 16)))
        pixels[rel] = (image.width(), image.height(), transparent)
    return pixels


# -- build ------------------------------------------------------------------------

def build(source_dir: Path, out_path: Path) -> None:
    out_path = _safe_output_path(out_path)
    manifest = _source_manifest(source_dir)
    files = _collect_files(source_dir)
    undeclared = sorted(set(files) - _declared_paths(manifest))
    if undeclared:
        # Silent exclusion would ship a pack that lies about its source
        # tree; the author decides explicitly.
        raise ValueError(
            "undeclared files present in the source tree (declare them "
            "under assets/legal_files or remove them): "
            + ", ".join(undeclared))
    manifest = _canonicalize_manifest(manifest, files)
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=1
    ).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zf:
        zf.writestr(_member_info(MANIFEST_NAME), manifest_bytes)
        for rel in sorted(files):
            zf.writestr(_member_info(rel), files[rel])
    payload = buffer.getvalue()
    _atomic_write(out_path, payload)
    archive_hash = hashlib.sha256(payload).hexdigest()
    print(f"built {out_path}")
    print(f"archive_sha256 {archive_hash}")
    print(f"files          {len(files) + 1}")
    print("note           archive hash covers the container; a new Revision "
          "is only required when the content digest changes")


# -- validate / preflight / preview / inspect --------------------------------------

def validate(pack_path: Path) -> int:
    from retirement_pet.petpack.validator import validate_petpack

    report = validate_petpack(pack_path.read_bytes())
    print(f"result         {report.result}")
    if report.pack_key:
        print(f"pack           {report.pack_key}")
    if report.content_digest:
        print(f"digest         {report.content_digest}")
    for diag in report.diagnostics:
        print(f"  {diag.code} [{diag.phase}] {diag.message_key}")
    return 0 if report.accepted else 1


def preflight(pack_path: Path) -> int:
    """Run the same narrow PNG/renderer/Alpha gate used by GUI import."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from retirement_pet.petpack.local_import import (
        LocalImportError,
        preflight_local_pack,
    )

    app = QApplication.instance()
    owns_application = app is None
    if app is None:
        app = QApplication([])
    try:
        preview = preflight_local_pack(pack_path)
    except LocalImportError as exc:
        print(f"preflight      REJECT: {exc}")
        return 1
    finally:
        if owns_application:
            app.quit()
    print("preflight      ACCEPT")
    print(f"pack           {preview.revision_key.pack}")
    print(f"characters     {', '.join(preview.character_ids)}")
    print(f"archive_sha256 {preview.archive_sha256}")
    if preview.warning_codes:
        print(f"warnings       {', '.join(preview.warning_codes)}")
    return 0


def preview(pack_path: Path, out_dir: Path,
            character_id: str | None = None) -> int:
    """Paint through the REAL desktop path, offscreen (CR-T02).

    Frames are composed exactly like the pet window draws them: manifest
    geometry -> ``compute_body_layout`` on the pet-window viewport
    (232x236 DIP) -> ``PackCharacterRuntime.render_body`` driven by
    RenderSnapshot time samples from ``runtime.preview_schedule()``
    (frame starts, the loop seam, and idle fallback for unbound
    semantics).  A per-character contact sheet lays the samples out for
    comparison (CR-T03).  No library, Context, user configuration or
    audio is touched.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from retirement_pet.character_layout import compute_body_layout
    from retirement_pet.models import ActionId, LifeStage, RenderSnapshot
    from retirement_pet.petpack.archive import PetpackArchive
    from retirement_pet.petpack.manifest import parse_manifest
    from retirement_pet.petpack.runtime import PackCharacterRuntime
    from retirement_pet.petpack.validator import validate_petpack
    from retirement_pet.ui.pet_window import CAT_AREA_HEIGHT, WINDOW_WIDTH

    data = pack_path.read_bytes()
    report = validate_petpack(data)
    if not report.accepted:
        print("preview        REJECT: pack did not validate")
        for diag in report.diagnostics:
            print(f"  {diag.code} [{diag.phase}] {diag.message_key}")
        return 1
    archive = PetpackArchive(data)
    manifest = parse_manifest(archive.manifest_bytes)
    characters = [str(c.get("id")) for c in manifest.get("characters", [])
                  if isinstance(c, dict)]
    if character_id is None:
        character_id = characters[0]
    elif character_id not in characters:
        print(f"preview        REJECT: character {character_id!r} not in "
              f"pack ({', '.join(characters)})")
        return 1
    app = QApplication.instance()
    owns_application = app is None
    if app is None:
        app = QApplication([])
    try:
        runtime = PackCharacterRuntime(archive, manifest, character_id,
                                       content_digest=report.content_digest)
        viewport = QRectF(0.0, 0.0, float(WINDOW_WIDTH),
                          float(CAT_AREA_HEIGHT))
        geometry = runtime.character_geometry()
        layout = (compute_body_layout(viewport, geometry)
                  if geometry is not None else None)
        if layout is not None:
            print(f"geometry      canvas {geometry.canvas_size[0]:g}x"
                  f"{geometry.canvas_size[1]:g}  viewport "
                  f"{WINDOW_WIDTH}x{CAT_AREA_HEIGHT}  "
                  f"scale {layout.scale:.4f}")
            print(f"layout        body {layout.body_rect.width():.1f}x"
                  f"{layout.body_rect.height():.1f} at "
                  f"({layout.body_rect.x():.1f}, {layout.body_rect.y():.1f})"
                  f"  foot ({layout.foot_point.x():.1f}, "
                  f"{layout.foot_point.y():.1f})  bubble "
                  f"({layout.bubble_origin.x():.1f}, "
                  f"{layout.bubble_origin.y():.1f})  "
                  f"clamped={layout.clamped}")
        else:
            print(f"geometry      none: legacy whole-PNG fit in "
                  f"{WINDOW_WIDTH}x{CAT_AREA_HEIGHT}")
        out_dir.mkdir(parents=True, exist_ok=True)
        sheet_rows: list[tuple[object, list[QImage]]] = []
        saved = 0
        for row in runtime.preview_schedule():
            cells: list[QImage] = []
            for index, point in enumerate(row.points):
                image = QImage(WINDOW_WIDTH, CAT_AREA_HEIGHT,
                               QImage.Format.Format_ARGB32)
                image.fill(QColor(0, 0, 0, 0))
                painter = QPainter(image)
                try:
                    runtime.render_body(
                        painter, viewport,
                        RenderSnapshot(action=ActionId(row.action_value),
                                       stage=LifeStage.YOUNG,
                                       elapsed_ms=point.elapsed_ms,
                                       frame=0, time_ms=0),
                        layout)
                finally:
                    painter.end()
                destination = out_dir / \
                    f"{character_id}-{row.semantic}-{index:03d}.png"
                if not image.save(str(destination)):
                    print(f"preview        could not write {destination}")
                    return 1
                saved += 1
                cells.append(image)
            sheet_rows.append((row, cells))
            detail = (f"sequence {row.frame_count} frame(s) "
                      f"total {row.total_ms}ms"
                      if row.kind == "sequence" else row.kind)
            if row.omitted_frames:
                detail += f" ({row.omitted_frames} frame(s) not sampled)"
            print(f"preview        {row.semantic}: {detail} -> "
                  f"{len(row.points)} sample(s)")
        sheet_path = out_dir / f"contact-{character_id}.png"
        if not _write_contact_sheet(sheet_path, sheet_rows):
            print("preview        REJECT: no installed font renders the "
                  "contact-sheet caption glyphs; no readable sheet can be "
                  "written here (see the log above)")
            return 1
        print(f"preview        wrote {saved} frame(s) + "
              f"{sheet_path.name} to {out_dir}")
    finally:
        if owns_application:
            app.quit()
    return 0


CONTACT_CELL = 96       # thumbnail box for one painted sample
CONTACT_CAPTION = 16    # per-cell caption strip (frame index + duration)
CONTACT_PAD = 4
CONTACT_LABEL_WIDTH = 132
CONTACT_MAX_COLUMNS = 8  # bands wrap instead of widening the sheet forever


def _contact_bands(rows_cells) -> list[tuple[object, int, list]]:
    """Split every row into bands of at most CONTACT_MAX_COLUMNS cells.

    Each band records its chunk START index: continuation bands must keep
    counting frames globally, never restart at f0 (CR-T04).
    """
    bands: list[tuple[object, int, list]] = []
    for row, cells in rows_cells:
        for chunk in range(0, len(cells), CONTACT_MAX_COLUMNS):
            bands.append((row, chunk,
                          cells[chunk:chunk + CONTACT_MAX_COLUMNS]))
    return bands


def _band_caption(row, chunk: int, column: int) -> str:
    """The caption text for one cell: GLOBAL point index, always."""
    return str(row.points[chunk + column].label)


def _system_font_dirs() -> list[Path]:
    """Conventional per-OS font directories (CR-T03).

    Only standard system locations are scanned - never a personal font
    path.  QT_QPA_FONTDIR (the offscreen platform's own font-directory
    knob) is honoured first when the environment sets it.
    """
    dirs = []
    env_dir = os.environ.get("QT_QPA_FONTDIR")
    if env_dir:
        dirs.append(Path(env_dir))
    if sys.platform == "win32":
        dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
    elif sys.platform == "darwin":
        dirs.extend([Path("/System/Library/Fonts"), Path("/Library/Fonts")])
    else:
        dirs.extend([Path("/usr/share/fonts"), Path("/usr/local/share/fonts")])
    return [d for d in dirs if d.is_dir()]


def _contact_font(labels):
    """An installed (or loadable) font that renders every caption glyph.

    Draws nothing on trust: a font that merely exists is not proof its
    glyphs render, and an offscreen/headless platform may start with an
    EMPTY font database whose default font paints every label as a tofu
    box (CR-T03).  Families are verified per codepoint with QRawFont;
    when no installed family covers the labels, standard system font
    directories (see _system_font_dirs) are loaded incrementally with
    QFontDatabase.addApplicationFont and each newly available family is
    verified the same way.  ``None`` means this environment cannot
    produce a readable sheet - the caller must refuse to write one and
    say why.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QFont, QFontDatabase, QRawFont

    required = sorted({ch for label in labels for ch in label
                       if not ch.isspace()})
    if not required:
        return QFont()

    def covers(family: str):
        font = QFont(family)
        font.setPixelSize(12)
        raw = QRawFont.fromFont(font)
        if not raw.isValid():
            return None
        if all(raw.supportsCharacter(ord(ch)) for ch in required):
            return font
        return None

    families = list(QFontDatabase.families())
    preferred = ["Arial", "Segoe UI", "Helvetica", "DejaVu Sans",
                 "Liberation Sans", "Noto Sans", "Microsoft YaHei",
                 "Courier New"]
    ordered = ([f for f in preferred if f in families]
               + sorted(f for f in families if f not in preferred))
    for family in ordered:
        font = covers(family)
        if font is not None:
            return font

    seen = set(families)
    font_dirs = _system_font_dirs()
    for directory in font_dirs:
        for path in sorted(directory.rglob("*")):
            if path.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id < 0:
                continue
            for family in QFontDatabase.applicationFontFamilies(font_id):
                if family in seen:
                    continue
                seen.add(family)
                font = covers(family)
                if font is not None:
                    return font
    print(f"contact-sheet  font database holds no family that covers the "
          f"required glyphs; standard font directories scanned: "
          f"{[str(d) for d in font_dirs] or 'none found'}")
    return None


def _write_contact_sheet(path: Path, rows_cells) -> bool:
    """Contact map (CR-T03): one band per semantic, one column per sample.

    Rows are labelled with the semantic and its kind/frame-count/duration;
    every cell carries a caption with frame index and frame duration, so
    the author can compare foot position, character size and the loop
    first/last seam directly.  Rows wider than CONTACT_MAX_COLUMNS wrap
    into continuation bands instead of widening the sheet indefinitely.
    Text lives only in its own bounded, elided, clipped strips - it never
    overlaps a sample image.  Returns False (writing nothing) when no
    installed font can render the caption glyphs; an unreadable sheet is
    never presented as success.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter

    bands = _contact_bands(rows_cells)
    if not bands:
        return False

    labels = set()
    for row, _chunk, _cells in bands:
        labels.add(str(row.semantic))
    for row, _cells in rows_cells:
        labels.update(_row_sub_lines(row))
        # the glyph precheck sees EVERY point label, not just each
        # band's local window (CR-T04)
        for point in row.points:
            labels.add(str(point.label))
        if len(row.points) > CONTACT_MAX_COLUMNS:
            labels.add("(cont.)")
    font = _contact_font(labels)
    if font is None:
        print(f"contact-sheet  no installed font covers the caption "
              f"glyphs {sorted(labels)}")
        return False

    columns = max(len(cells) for _row, _chunk, cells in bands)
    width = CONTACT_LABEL_WIDTH + CONTACT_PAD + \
        columns * (CONTACT_CELL + CONTACT_PAD)
    height = CONTACT_PAD + len(bands) * \
        (CONTACT_CELL + CONTACT_CAPTION + CONTACT_PAD)
    sheet = QImage(width, height, QImage.Format.Format_ARGB32)
    sheet.fill(QColor(0xFF, 0xFF, 0xFF, 0xFF))
    metrics = QFontMetrics(font)
    painter = QPainter(sheet)
    try:
        painter.setFont(font)
        for index, (row, chunk, cells) in enumerate(bands):
            top = CONTACT_PAD + index * \
                (CONTACT_CELL + CONTACT_CAPTION + CONTACT_PAD)
            painter.setPen(QColor(0xD0, 0xD0, 0xD0, 0xFF))
            painter.drawLine(CONTACT_LABEL_WIDTH, top - 2, width, top - 2)
            # Row label: clipped to the label column, elided to fit.
            painter.setPen(QColor(0x20, 0x20, 0x20, 0xFF))
            painter.save()
            painter.setClipRect(QRect(0, top, CONTACT_LABEL_WIDTH,
                                      CONTACT_CELL))
            head = metrics.elidedText(
                str(row.semantic) + (" (cont.)" if chunk else ""),
                Qt.ElideRight, CONTACT_LABEL_WIDTH - 8)
            painter.drawText(QRect(4, top, CONTACT_LABEL_WIDTH - 8, 40),
                             Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter, head)
            painter.setPen(QColor(0x70, 0x70, 0x70, 0xFF))
            # Sub-label in its own short lines (CR-T04): the omission
            # note keeps its own line so eliding can never cut it.
            sub_lines = _row_sub_lines(row)
            first_height = 48 if len(sub_lines) == 1 else 22
            painter.drawText(
                QRect(4, top + 40, CONTACT_LABEL_WIDTH - 8, first_height),
                Qt.TextFlag.TextWordWrap,
                metrics.elidedText(sub_lines[0], Qt.ElideRight,
                                   CONTACT_LABEL_WIDTH - 8))
            if len(sub_lines) > 1:
                painter.drawText(
                    QRect(4, top + 64, CONTACT_LABEL_WIDTH - 8, 24),
                    Qt.TextFlag.TextWordWrap,
                    metrics.elidedText(sub_lines[1], Qt.ElideRight,
                                       CONTACT_LABEL_WIDTH - 8))
            painter.restore()
            for column, image in enumerate(cells):
                left = CONTACT_LABEL_WIDTH + CONTACT_PAD + \
                    column * (CONTACT_CELL + CONTACT_PAD)
                scaled = image.scaled(
                    CONTACT_CELL, CONTACT_CELL,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                painter.drawImage(
                    left + (CONTACT_CELL - scaled.width()) // 2,
                    top + (CONTACT_CELL - scaled.height()) // 2,
                    scaled)
                caption_rect = QRect(left, top + CONTACT_CELL,
                                     CONTACT_CELL, CONTACT_CAPTION)
                painter.save()
                painter.setClipRect(caption_rect)
                painter.setPen(QColor(0x40, 0x40, 0x40, 0xFF))
                painter.drawText(
                    caption_rect, Qt.AlignmentFlag.AlignCenter,
                    metrics.elidedText(
                        _band_caption(row, chunk, column),
                        Qt.ElideRight, CONTACT_CELL))
                painter.restore()
    finally:
        painter.end()
    return sheet.save(str(path))


def _row_sub_lines(row) -> list[str]:
    """Sub-label split so every part stays fully readable (CR-T04).

    The omission note is its own short line: eliding a combined line is
    exactly what cut off "+N omitted" in the reviewed 40-frame sheet.
    """
    if row.kind == "sequence":
        lines = [f"seq {row.frame_count}f {row.total_ms}ms"]
    elif row.kind == "fallback":
        lines = ["fallback -> this row paints core.idle"]
    else:
        lines = [str(row.kind)]
    if row.omitted_frames:
        lines.append(f"+{row.omitted_frames} frames omitted")
    return lines


def inspect(pack_path: Path) -> int:
    from retirement_pet.petpack.validator import validate_petpack

    data = pack_path.read_bytes()
    report = validate_petpack(data)
    m = report.manifest
    print(f"result         {report.result}")
    if not report.accepted:
        for diag in report.diagnostics:
            print(f"  {diag.code} [{diag.phase}] {diag.message_key}")
        return 1
    pkg = m.get("package", {})
    print(f"pack           {report.pack_key}")
    print(f"version        {pkg.get('version')}")
    print(f"series         {m.get('series', {}).get('id')}")
    print(f"characters     {', '.join(c.get('id', '') for c in m.get('characters', []))}")
    print(f"actions        {len(m.get('actions', []))}")
    print(f"assets         {len(m.get('assets', []))}")
    print(f"archive bytes  {len(data)}")
    print(f"digest         {report.content_digest}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    command, *rest = argv
    if command == "init" and rest:
        return init(Path(rest[0]))
    if command == "lint" and rest:
        try:
            return lint(Path(rest[0]))
        except SystemExit as exc:
            print(f"lint E: {exc}", file=sys.stderr)
            return 1
    if command == "build" and len(rest) == 2:
        try:
            build(Path(rest[0]), Path(rest[1]))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except SystemExit as exc:
            print(f"error: {exc.code if hasattr(exc, 'code') else exc}",
                  file=sys.stderr)
            return 2
        return 0
    if command == "validate" and rest:
        return validate(Path(rest[0]))
    if command == "preflight" and rest:
        return preflight(Path(rest[0]))
    if command == "preview" and len(rest) >= 2:
        return preview(Path(rest[0]), Path(rest[1]),
                       rest[2] if len(rest) > 2 else None)
    if command == "inspect" and rest:
        return inspect(Path(rest[0]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
