"""Multi-character shared-rig corpus (PETPACK_SPEC 9/11.4; ADR-V2-005).

Shared raw action assets between characters of one pack require an EXPLICIT,
identical rig_contract on every participating character AND a parameterized
layered motion template marked with the same rig_contract_ref.  Static /
sequence BODY frames must never be shared; body_independent effects may be.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from retirement_pet.petpack.validator import validate_petpack

ROOT = Path(__file__).resolve().parent.parent


_MIN_GEOMETRY = {
    "logical_canvas": {"width": 256, "height": 256},
    "content_bounds": {"x": 32, "y": 32, "width": 192, "height": 192},
    "motion_bounds": {"x": 16, "y": 16, "width": 224, "height": 224},
    "base_anchor": {"x": 128, "y": 236},
    "bubble_anchor": {"x": 128, "y": 64},
    "reference_height": 192,
}


def _manifest(shared: bool) -> dict:
    """Two characters, one series.  When ``shared``: both additionally bind
    the same parameterized LAYERED template via rig_contract - bound to the
    degradable non-idle semantic ``core.work``, because runtime admission
    requires a drawable static/sequence core.idle per character (review
    P-2).  Otherwise each owns all of its frames."""
    if shared:
        # drawable per-character idles keep runtime admission open
        idle_a = {"id": "action.boy.idle", "semantic": "core.idle",
                  "lifecycle": {"loop": {"renderer": {
                      "type": "sequence", "frames": [
                          {"asset": "asset.body.boy", "duration_ms": 200}]}}}}
        idle_b = {"id": "action.girl.idle", "semantic": "core.idle",
                  "lifecycle": {"loop": {"renderer": {
                      "type": "sequence", "frames": [
                          {"asset": "asset.body.girl", "duration_ms": 200}]}}}}
        # the SHARED layered template (rig rules need >=2 users of one
        # action carrying an identical rig_contract_ref); undrawable
        # non-idle actions degrade to PPK-ACT-W002 with idle fallback
        shared_layered = {
            "id": "action.shared.work",
            "semantic": "core.work",
            "rig_contract_ref": "rig.chibi.v1",
            "lifecycle": {"loop": {"renderer": {"type": "layered",
                                                "template": "tmpl.shared.work"}}},
        }
        # rig-contract characters bind their own body slot; the template is
        # parameterized and never references a concrete body asset
        a = {"id": "chiboy", "rig_contract_ref": "rig.chibi.v1",
             "rig_bindings": {"body": "asset.body.boy"},
             "thumbnail_asset": "asset.body.boy",
             "actions": {"core.idle": "action.boy.idle",
                         "core.work": "action.shared.work"},
             "geometry": _MIN_GEOMETRY}
        b = {"id": "chigirl", "rig_contract_ref": "rig.chibi.v1",
             "rig_bindings": {"body": "asset.body.girl"},
             "thumbnail_asset": "asset.body.girl",
             "actions": {"core.idle": "action.girl.idle",
                         "core.work": "action.shared.work"},
             "geometry": _MIN_GEOMETRY}
        actions = [idle_a, idle_b, shared_layered]
    else:
        idle_a = {"id": "action.boy.idle", "semantic": "core.idle",
                  "lifecycle": {"loop": {"renderer": {
                      "type": "sequence", "frames": [
                          {"asset": "asset.body.boy", "duration_ms": 200}]}}}}
        idle_b = {"id": "action.girl.idle", "semantic": "core.idle",
                  "lifecycle": {"loop": {"renderer": {
                      "type": "sequence", "frames": [
                          {"asset": "asset.body.girl", "duration_ms": 200}]}}}}
        a = {"id": "chiboy", "rig_contract_ref": None, "rig_bindings": {},
             "thumbnail_asset": "asset.body.boy",
             "actions": {"core.idle": "action.boy.idle"},
             "geometry": _MIN_GEOMETRY}
        b = {"id": "chigirl", "rig_contract_ref": None, "rig_bindings": {},
             "thumbnail_asset": "asset.body.girl",
             "actions": {"core.idle": "action.girl.idle"},
             "geometry": _MIN_GEOMETRY}
        actions = [idle_a, idle_b]

    manifest = {
        "schema_version": "1.0",
        "package": {"publisher_id": "community.example",
                    "publisher_ref": "community.example", "id": "duo-pack",
                    "version": "1.0.0", "display_name": {"zh-CN": "双子"}},
        "series": {"id": "duo", "display_name": {"zh-CN": "双子系列"}},
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
             "creator": "community.example", "title": "duo sprites",
             "locator": None, "accessed_at": None},
        ],
        "assets": [],
        "actions": actions,
        "characters": [a, b],
    }
    if shared:
        manifest["rig_contracts"] = [{
            "id": "rig.chibi.v1",
            "logical_canvas": {"width": 256, "height": 256},
            "required_anchors": ["base"],
            "slots": [{"id": "body", "media_kind": "image", "required": True}],
            "transform_profile": "layered.v1",
        }]
    return manifest


def _build(manifest: dict) -> bytes:
    files = {
        "assets/boy.png": b"\x89PNG\r\n\x1a\n" + b"0" * 60,
        "assets/girl.png": b"\x89PNG\r\n\x1a\n" + b"0" * 60,
    }
    for asset in manifest.get("assets", []):
        path = asset["path"]
        data = files[path]
        asset["byte_size"] = len(data)
        asset["sha256"] = __import__("hashlib").sha256(data).hexdigest()
        asset.setdefault("media_type", "image/png")
        asset.setdefault("rights_ref", "rights.original")
        asset.setdefault("source_ref", "source.original")
    # NOTE: the fixtures above intentionally skip full PNG magic validity for
    # brevity?  No - the validator enforces magic; use tiny real PNGs.
    from test_petpack import png_bytes
    files["assets/boy.png"] = png_bytes(8, 8)
    files["assets/girl.png"] = png_bytes(8, 8)
    for asset in manifest.get("assets", []):
        data = files[asset["path"]]
        asset["byte_size"] = len(data)
        asset["sha256"] = __import__("hashlib").sha256(data).hexdigest()
        asset["properties"] = {"width": 8, "height": 8}
    manifest["assets"].extend([
        {"id": "asset.body.boy", "path": "assets/boy.png",
         "media_type": "image/png", "byte_size": 0, "sha256": "",
         "rights_ref": "rights.original", "source_ref": "source.original",
         "properties": {"width": 8, "height": 8}},
        {"id": "asset.body.girl", "path": "assets/girl.png",
         "media_type": "image/png", "byte_size": 0, "sha256": "",
         "rights_ref": "rights.original", "source_ref": "source.original",
         "properties": {"width": 8, "height": 8}},
    ])
    for asset in manifest["assets"]:
        data = files[asset["path"]]
        asset["byte_size"] = len(data)
        asset["sha256"] = __import__("hashlib").sha256(data).hexdigest()

    manifest_bytes = json.dumps(manifest, ensure_ascii=False,
                                sort_keys=True).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("petpack.json", manifest_bytes)
        for rel in sorted(files):
            zf.writestr(rel, files[rel])
    return buffer.getvalue()


def test_multi_character_per_character_assets_accepted():
    pack = _build(_manifest(shared=False))
    report = validate_petpack(pack)
    assert report.accepted, [d.code for d in report.diagnostics]


def test_shared_layered_template_with_rig_contract_installable():
    """The shared layered template is bound to a NON-idle semantic, so the
    pack stays installable with a stable degradation (the per-character
    sequence idles keep runtime admission open)."""
    pack = _build(_manifest(shared=True))
    report = validate_petpack(pack)
    assert report.accepted, [d.code for d in report.diagnostics]
    assert report.result == "ACCEPT_WITH_DEGRADATION"
    assert "renderer_unsupported:action.shared.work" in report.degraded
    assert any(d.code == "PPK-ACT-W002" for d in report.diagnostics)


def test_rig_mismatch_rejected():
    manifest = _manifest(shared=True)
    # character B declares a DIFFERENT rig contract than the shared action
    manifest["characters"][1]["rig_contract_ref"] = "rig.other.v1"
    pack = _build(manifest)
    report = validate_petpack(pack)
    assert not report.accepted
    assert any(d.code == "PPK-ACT-E005" for d in report.diagnostics)


def test_shared_sequence_body_frames_rejected():
    """Static/sequence body frames must NOT be shared between characters."""
    manifest = _manifest(shared=False)
    manifest["characters"][1]["actions"] = {"core.idle": "action.boy.idle"}
    pack = _build(manifest)
    report = validate_petpack(pack)
    assert not report.accepted
    assert any(d.code == "PPK-ACT-E005" for d in report.diagnostics)
