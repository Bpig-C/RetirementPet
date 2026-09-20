"""V12-02: PetPack consistency closure and compatibility policy.

Every fixture class from the work order acceptance list gets a stable
diagnostic result here: valid packs, missing required fields, wrong types,
NaN/illegal sizes, dangling references, unknown required capabilities,
corrupt or mismatched legal files, unknown semantics, and renderer
honesty.  The frozen official packs and the local 0.1.x previews are
audited against the strictened pipeline as real regression fixtures.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from retirement_pet.petpack.diagnostics import Severity
from retirement_pet.petpack.engine_profile import ENGINE_VERSION
from retirement_pet.petpack.validator import validate_petpack

from test_petpack import base_files, build_pack, make_manifest, png_bytes

GOOD_GEOMETRY = {
    "logical_canvas": {"width": 256, "height": 256},
    "content_bounds": {"x": 28, "y": 16, "width": 200, "height": 220},
    "motion_bounds": {"x": 12, "y": 8, "width": 232, "height": 240},
    "base_anchor": {"x": 128, "y": 248},
    "bubble_anchor": {"x": 128, "y": 40},
    "reference_height": 220,
    "hit_regions": [{"shape": "rect", "x": 28, "y": 16,
                     "width": 200, "height": 220}],
}


def validate(manifest: dict, files: dict | None = None, **kw) -> object:
    return validate_petpack(build_pack(manifest, files or base_files(), **kw))


def code_of(report) -> str:
    (diagnostic,) = report.diagnostics
    return diagnostic.code


def with_geometry(manifest: dict, **geometry) -> dict:
    for character in manifest["characters"]:
        character["geometry"] = GOOD_GEOMETRY | geometry
    return manifest


# -- baseline ------------------------------------------------------------------

def test_complete_pack_is_accepted_without_degradation():
    report = validate_petpack(build_pack(make_manifest(), base_files()))
    assert report.accepted
    assert report.result == "ACCEPT"
    assert report.degraded == []


def test_required_capability_without_runtime_consumer_rejected():
    """Review P-1 evidence rule: format-validated is not runtime-consumed.

    Pack audio and pack text profiles are format-checked by the validator
    (PPK-RES-E005 / PPK-TXT-*), but nothing in the runtime loads pack audio
    or renders pack text profiles, so a pack REQUIRING those capabilities
    could never render as authored and is rejected with PPK-MAN-E006.
    """
    for capability in ("asset.wav.v1", "asset.ogg.v1", "text.plaintext.v1"):
        manifest = make_manifest()
        manifest["compatibility"] = {
            "engine_min": ENGINE_VERSION,
            "engine_max_exclusive": "3.0.0",
            "required_capabilities": [capability],
        }
        report = validate(manifest)
        assert not report.accepted, capability
        assert code_of(report) == "PPK-MAN-E006", capability
        assert report.diagnostics[0].params == {"capability": capability}


def test_consumed_capability_remains_admissible():
    manifest = make_manifest()
    manifest["compatibility"] = {
        "engine_min": ENGINE_VERSION,
        "engine_max_exclusive": "3.0.0",
        "required_capabilities": ["renderer.sequence.v1",
                                  "semantic.core.idle.v1"],
    }
    report = validate(manifest)
    assert report.accepted, [d.code for d in report.diagnostics]


# -- geometry (spec 13) ----------------------------------------------------------

def test_geometry_missing_content_bounds_rejected():
    manifest = make_manifest()
    geometry = {k: v for k, v in GOOD_GEOMETRY.items()
                if k != "content_bounds"}
    for character in manifest["characters"]:
        character["geometry"] = geometry
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E004"
    assert report.diagnostics[0].params == {"field": "content_bounds"}


@pytest.mark.parametrize("mutation", [
    {"reference_height": "220"},                       # wrong type
    {"reference_height": True},                        # bool is not a number
    {"logical_canvas": {"width": 0, "height": 256}},   # non-positive size
    {"logical_canvas": {"width": 256, "height": 99999}},  # budget
    {"content_bounds": {"x": 28, "y": 16, "width": 0, "height": 220}},
    {"content_bounds": {"x": 28, "y": 16, "width": 400, "height": 220}},
    {"content_bounds": {"x": -5, "y": 16, "width": 200, "height": 220}},
    {"motion_bounds": {"x": 12, "y": 8, "width": 232, "height": 999}},
    {"base_anchor": {"x": 300, "y": 248}},
    {"bubble_anchor": {"x": 128, "y": -1}},
    {"hit_regions": [{"shape": "ellipse", "x": 0, "y": 0, "width": 2,
                      "height": 2}]},
    {"hit_regions": [{"shape": "circle", "center": {"x": 10, "y": 10},
                      "radius": -2}]},
    {"hit_regions": [{"shape": "circle", "center": {"x": 250, "y": 10},
                      "radius": 20}]},                 # leaves the canvas
    {"hit_regions": [{"shape": "polygon",
                      "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}]},
    {"hit_regions": [{"shape": "rect", "x": 0, "y": 0, "width": 2,
                      "height": 2}] * 17},             # region budget
])
def test_geometry_illegal_values_rejected(mutation):
    manifest = with_geometry(make_manifest(), **mutation)
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E004"
    assert report.diagnostics[0].phase == "geometry"


def test_geometry_nan_manifest_number_rejected_at_parse():
    manifest = with_geometry(make_manifest(), reference_height=float("nan"))
    pack = build_pack(manifest, base_files())  # dumps NaN into petpack.json
    report = validate_petpack(pack)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E002"


def test_variant_geometry_override_must_share_canvas():
    manifest = make_manifest()
    character = manifest["characters"][0]
    character["variants"] = [{
        "id": "winter",
        "display_name": {"zh-CN": "冬装"},
        "thumbnail_asset": "thumb",
        "action_overrides": {},
        "geometry_override": GOOD_GEOMETRY | {
            "logical_canvas": {"width": 512, "height": 512}},
        "text_profile_refs": [],
    }]
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E004"
    assert report.diagnostics[0].params == {"field": "geometry_override"}

    character["variants"][0]["geometry_override"] = dict(GOOD_GEOMETRY)
    assert validate(manifest).accepted


# -- compatibility (spec 5 / 11) ---------------------------------------------------

def test_engine_below_pack_minimum_rejected():
    manifest = make_manifest()
    manifest["compatibility"] = {"engine_min": "3.0.0",
                                 "engine_max_exclusive": "4.0.0"}
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E006"
    assert report.diagnostics[0].message_key == \
        "petpack.compatibility.engine_below_min"


def test_engine_at_or_above_pack_exclusive_max_rejected():
    manifest = make_manifest()
    manifest["compatibility"] = {"engine_min": "1.0.0",
                                 "engine_max_exclusive": "2.0.0"}
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E006"


def test_engine_inside_declared_range_accepted():
    manifest = make_manifest()
    manifest["compatibility"] = {
        "engine_min": "2.0.0", "engine_max_exclusive": "3.0.0",
        "required_capabilities": ["renderer.sequence.v1"],
        "optional_capabilities": [],
    }
    assert ENGINE_VERSION == "2.0.0"
    assert validate(manifest).accepted


def test_unsupported_required_capability_rejected():
    manifest = make_manifest()
    manifest["compatibility"] = {"required_capabilities": ["asset.webp.v1"]}
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E006"
    assert report.diagnostics[0].params == {"capability": "asset.webp.v1"}


def test_malformed_engine_range_rejected_as_field_error():
    manifest = make_manifest()
    manifest["compatibility"] = {"engine_min": "not-a-version"}
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E004"


# -- unknown core semantics (spec 11) ----------------------------------------------

def _bind_extra_semantic(manifest: dict, semantic: str) -> dict:
    manifest["actions"].append({
        "id": f"action.extra.{semantic.replace('.', '_')}",
        "semantic": semantic,
        "lifecycle": {"loop": {"renderer": {
            "type": "static", "asset": "idle0"}}},
    })
    manifest["characters"][0].setdefault("actions", {})[semantic] = \
        manifest["actions"][-1]["id"]
    return manifest


def test_unknown_core_semantic_without_declaration_rejected():
    manifest = _bind_extra_semantic(make_manifest(), "core.fly")
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-ACT-E002"
    assert report.diagnostics[0].params == {"semantic": "core.fly"}


def test_unknown_core_semantic_with_optional_capability_degrades():
    manifest = _bind_extra_semantic(make_manifest(), "core.fly")
    manifest["compatibility"] = {
        "optional_capabilities": ["semantic.core.fly.v1"]}
    report = validate(manifest)
    assert report.accepted
    assert report.result == "ACCEPT_WITH_DEGRADATION"
    assert "semantic_disabled:core.fly" in report.degraded
    warning = [d for d in report.diagnostics
               if d.code == "PPK-ACT-W001" and d.severity is Severity.WARNING]
    assert [d.params["semantic"] for d in warning] == ["core.fly"]


# -- renderer honesty (spec 12 vs engine_profile) ------------------------------------

def _idle_with_renderer(manifest: dict, renderer: dict) -> dict:
    manifest["actions"][0]["lifecycle"] = {"loop": {"renderer": renderer}}
    return manifest


def test_idle_bound_to_builtin_effect_rejected():
    manifest = _idle_with_renderer(make_manifest(), {
        "type": "builtin_effect", "effect": "sparkle"})
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-ACT-E004"


def test_idle_without_enter_or_loop_rejected():
    manifest = _idle_with_renderer(make_manifest(), None) \
        if False else make_manifest()
    manifest["actions"][0]["lifecycle"] = {
        "exit": {"renderer": {"type": "static", "asset": "idle0"}}}
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-ACT-E004"


def test_layered_idle_rejected_at_runtime_admission():
    """Ruling P-2: an undrawable core.idle never becomes an
    installable-with-warning pack.  Layered idles fail runtime admission
    with PPK-ACT-E004; W002 is reserved for non-idle actions that keep a
    usable idle fallback."""
    manifest = _idle_with_renderer(make_manifest(), {
        "type": "layered", "template": "tmpl.idle"})
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-ACT-E004"
    assert "renderer_unsupported:action.idle" not in report.degraded
    assert not any(d.code == "PPK-ACT-W002" for d in report.diagnostics)


def test_layered_idle_structurally_parseable_before_admission():
    """Test layering (review 3.1): the same manifest is structurally sound
    (the manifest/inventory phases raise nothing) and is rejected exactly
    at the runtime-admission layer (actions phase, renderer honesty)."""
    manifest = _idle_with_renderer(make_manifest(), {
        "type": "layered", "template": "tmpl.idle"})
    report = validate(manifest)
    assert not report.accepted
    rejection = report.diagnostics[0]
    assert rejection.code == "PPK-ACT-E004"
    assert rejection.phase == "actions"
    assert rejection.severity is Severity.ERROR
    # no earlier (structural) phase contributed a diagnostic
    assert [d.phase for d in report.diagnostics] == ["actions"]


def test_layered_action_on_non_idle_semantic_degrades_not_rejected():
    """The P-2 W002 semantic: layered/non-drawable actions bound to
    non-idle semantics degrade with a usable idle fallback."""
    manifest = make_manifest()
    manifest["actions"].append({
        "id": "action.fx", "semantic": "core.work",
        "lifecycle": {"loop": {"renderer": {
            "type": "layered", "template": "tmpl.work"}}},
    })
    manifest["characters"][0]["actions"]["core.work"] = "action.fx"
    report = validate(manifest)
    assert report.accepted
    assert report.result == "ACCEPT_WITH_DEGRADATION"
    assert "renderer_unsupported:action.fx" in report.degraded
    warning = [d for d in report.diagnostics if d.code == "PPK-ACT-W002"]
    assert warning and warning[0].params == {"action": "action.fx"}


def test_idle_undrawable_loop_not_saved_by_drawable_enter():
    """Acceptance round-2 probe: the runtime resolves ``loop or enter``
    (loop first).  An idle whose loop is layered must fail admission even
    when its enter stage is a drawable static - a drawable enter does not
    make the action drawable."""
    manifest = make_manifest()
    for character in manifest["characters"]:
        for action in manifest["actions"]:
            if action["id"] != "action.idle":
                continue
            action["lifecycle"] = {
                "enter": {"renderer": {"type": "static",
                                       "asset": "idle0"}},
                "loop": {"renderer": {"type": "layered",
                                      "template": "tmpl.idle"}},
            }
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-ACT-E004"
    assert report.diagnostics[0].message_key == \
        "petpack.actions.idle_undrawable"


def test_bound_nondrawable_action_degrades_but_pack_accepted():
    manifest = make_manifest()
    manifest["actions"].append({
        "id": "action.fx", "semantic": None,
        "lifecycle": {"loop": {"renderer": {
            "type": "builtin_effect", "effect": "sparkle"}}},
    })
    manifest["characters"][0]["actions"]["core.work"] = "action.fx"
    report = validate(manifest)
    assert report.accepted
    assert "renderer_unsupported:action.fx" in report.degraded


def test_static_renderer_with_dangling_asset_rejected():
    manifest = _idle_with_renderer(make_manifest(), {
        "type": "static", "asset": "asset.missing"})
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-ACT-E005"


# -- reference closure (spec 6/7/10/14) -----------------------------------------------

def test_dangling_action_binding_rejected():
    manifest = make_manifest()
    manifest["characters"][0]["actions"]["core.work"] = "action.missing"
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-ACT-E005"
    assert report.diagnostics[0].message_key == \
        "petpack.actions.dangling_binding"


def test_thumbnail_required_and_dangling_rejected():
    manifest = make_manifest()
    del manifest["characters"][0]["thumbnail_asset"]
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E004"

    manifest["characters"][0]["thumbnail_asset"] = "asset.missing"
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-ACT-E005"


def test_duplicate_asset_and_action_ids_rejected():
    manifest = make_manifest()
    duplicate = dict(manifest["assets"][0])
    duplicate["path"] = "assets/duplicate.png"
    manifest["assets"].append(duplicate)
    report = validate(manifest, base_files() |
                      {"assets/duplicate.png": png_bytes(2, 2)})
    assert code_of(report) == "PPK-MAN-E004"

    manifest = make_manifest()
    manifest["actions"].append(dict(manifest["actions"][0]))
    assert code_of(validate(manifest)) == "PPK-MAN-E004"


def test_publisher_ref_must_resolve():
    manifest = make_manifest()
    manifest["package"]["publisher_ref"] = "publisher.main"
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E004"

    manifest["publishers"] = [{"id": "publisher.main",
                               "display_name": "Example"}]
    assert validate(manifest).accepted


# -- text profiles (spec 14) -----------------------------------------------------------

def _text_profile(entries, **overrides) -> dict:
    profile = {
        "id": "text.demo.default",
        "locale": "zh-CN",
        "rights_ref": "rights.original",
        "source_ref": "source.original",
        "entries": entries,
    }
    profile.update(overrides)
    return profile


def test_text_profile_with_safe_token_accepted():
    manifest = make_manifest()
    manifest["text_profiles"] = [
        _text_profile({"action.core.work.caption": "一起专心 {days} 天"})]
    manifest["characters"][0]["text_profile_refs"] = ["text.demo.default"]
    assert validate(manifest).accepted


@pytest.mark.parametrize("entries", [
    {"bad": "在呢\x00"},                  # control character
    {"bad": "next\u202eevil"},            # bidi override
    {"bad": "<b>rich</b>"},               # markup lookalike
    {"bad": "${secret}"},                 # expression
    {"bad": "x {not_allowlisted} y"},     # token outside engine allowlist
    {"x": 5},                             # non-string entry
    {"bad": []},                          # empty candidate pool
    {"bad": [{"text": "a"}, {"text": "b"}, {"text": "c"}, {"text": "d"},
             {"text": "e"}, {"text": "f"}, {"text": "g"}, {"text": "h"},
             {"text": "i"}]},             # candidate budget
])
def test_text_profile_violations_rejected(entries):
    manifest = make_manifest()
    manifest["text_profiles"] = [_text_profile(entries)]
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) in ("PPK-TXT-E001", "PPK-TXT-E002",
                               "PPK-TXT-E003")


def test_text_profile_defaults_must_exist_and_resolve():
    manifest = make_manifest()
    manifest["text_profiles"] = [
        _text_profile({"k": "hi"}, rights_ref=None)]
    assert code_of(validate(manifest)) == "PPK-TXT-E002"

    manifest["text_profiles"] = [
        _text_profile({"k": "hi"}, rights_ref="rights.missing")]
    assert code_of(validate(manifest)) == "PPK-RGT-E002"


def test_dangling_text_profile_ref_rejected():
    manifest = make_manifest()
    manifest["characters"][0]["text_profile_refs"] = ["text.missing"]
    assert code_of(validate(manifest)) == "PPK-TXT-E002"


# -- legal files (spec 7.1) --------------------------------------------------------------

LICENSE_TEXT = "Retirement Cat sample license line.\n"


def _with_legal(manifest: dict, data: bytes = None, **legal_overrides) -> dict:
    data = LICENSE_TEXT.encode("utf-8") if data is None else data
    legal = {
        "id": "legal.license.main",
        "path": "legal/license.txt",
        "media_type": "text/plain",
        "byte_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "purpose": "license_text",
    }
    legal.update(legal_overrides)
    manifest["legal_files"] = [legal]
    return manifest


def _legal_files(license_bytes: bytes) -> dict:
    return base_files() | {"legal/license.txt": license_bytes}


def test_valid_legal_file_accepted():
    assert validate(_with_legal(make_manifest()),
                    _legal_files(LICENSE_TEXT.encode("utf-8"))).accepted


def test_legal_file_hash_and_size_mismatch_rejected():
    manifest = _with_legal(make_manifest())
    report = validate(manifest, _legal_files(LICENSE_TEXT + "extra\n"))
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E008"

    manifest = _with_legal(make_manifest(), sha256="0" * 64)
    report = validate(manifest, _legal_files(LICENSE_TEXT.encode("utf-8")))
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E008"


def test_legal_file_wrong_media_or_encoding_rejected():
    manifest = _with_legal(make_manifest(), media_type="application/pdf")
    report = validate(manifest, _legal_files(LICENSE_TEXT.encode("utf-8")))
    assert not report.accepted
    assert code_of(report) == "PPK-RES-E001"

    utf16 = LICENSE_TEXT.encode("utf-16")
    manifest = _with_legal(make_manifest(), data=utf16)
    report = validate(manifest, _legal_files(utf16))
    assert not report.accepted
    assert code_of(report) == "PPK-RES-E001"


def test_legal_file_missing_purpose_rejected():
    manifest = _with_legal(make_manifest(), purpose="")
    report = validate(manifest, _legal_files(LICENSE_TEXT.encode("utf-8")))
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E004"


def test_rights_license_legal_file_ref_must_resolve():
    manifest = make_manifest()
    manifest["rights_declarations"][0]["license"] = {
        "spdx": None, "legal_file_ref": "legal.missing"}
    manifest = _with_legal(manifest)
    report = validate(manifest, _legal_files(LICENSE_TEXT.encode("utf-8")))
    assert not report.accepted
    assert code_of(report) == "PPK-RGT-E002"
    assert report.diagnostics[0].params == {"kind": "legal_file"}

    manifest["rights_declarations"][0]["license"] = {
        "spdx": None, "legal_file_ref": "legal.license.main"}
    assert validate(manifest,
                    _legal_files(LICENSE_TEXT.encode("utf-8"))).accepted


# -- frozen pack audit (per-pack results over the real shipping content) ---------

ROOT = Path(__file__).resolve().parent.parent
FROZEN_PACKS = [
    ("assets/petpack/retirement-cat-official.petpack", "BUILTIN_OFFICIAL"),
    ("assets/petpack/retirement-cat-official-1.0.1.petpack",
     "BUILTIN_OFFICIAL"),
    ("assets/petpack/examples/realistic-retirement-cat-0.1.0.petpack",
     "LOCAL_IMPORTED"),
    ("assets/petpack/examples/realistic-retirement-cat-0.1.1.petpack",
     "LOCAL_IMPORTED"),
]


@pytest.mark.parametrize(
    "relative_path,trust_channel",
    [(path, channel) for path, channel in FROZEN_PACKS
     if (ROOT / path).is_file()],
)
def test_frozen_and_local_packs_survive_strictened_validation(
        relative_path, trust_channel):
    data = (ROOT / relative_path).read_bytes()
    report = validate_petpack(data, trust_channel=trust_channel)
    assert report.accepted, [d.code for d in report.diagnostics]
    assert report.result == "ACCEPT_WITH_DEGRADATION"


@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent / "assets" / "petpack"
         / "examples" / "realistic-retirement-cat-0.1.0.petpack").is_file(),
    reason="internal frozen pack is excluded from the public snapshot")


def test_frozen_pack_audit_is_deterministic_about_degradation():
    """CR-P01: all four internally retained historical/current packs miss
    the spec-6.1 required publisher_ref.  They stay loadable ONLY through
    the exact-revision legacy exemption, and each load carries exactly one
    PPK-MAN-W002 warning plus its degradation entry - never silently."""
    for relative_path, trust_channel in FROZEN_PACKS:
        data = (ROOT / relative_path).read_bytes()
        report = validate_petpack(data, trust_channel=trust_channel)
        assert report.degraded == ["publisher_ref_exempt:historical"], \
            (relative_path, report.degraded)
        warnings = [d for d in report.diagnostics
                    if d.code == "PPK-MAN-W002"]
        assert len(warnings) == 1, (relative_path, report.diagnostics)
        assert warnings[0].severity is Severity.WARNING
        assert warnings[0].recoverable


# -- publisher_ref closure (spec 6.1; review CR-P01) -----------------------------

def test_publisher_ref_dangling_rejected():
    manifest = make_manifest()
    manifest["package"]["publisher_ref"] = "someone.else"
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E004"
    assert report.diagnostics[0].message_key == \
        "petpack.manifest.publisher_ref_dangling"


def test_publisher_ref_non_string_rejected():
    manifest = make_manifest()
    manifest["package"]["publisher_ref"] = 7
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E004"
    assert report.diagnostics[0].message_key == \
        "petpack.manifest.publisher_ref_invalid"


def test_publisher_ref_missing_on_new_pack_rejected():
    """A pack outside the frozen historical pin cannot omit the required
    publisher_ref - including rebuilt variants of the historical packs."""
    manifest = make_manifest()
    del manifest["package"]["publisher_ref"]
    report = validate(manifest)
    assert not report.accepted
    assert code_of(report) == "PPK-MAN-E004"
    assert report.diagnostics[0].message_key == \
        "petpack.manifest.publisher_ref_required"


def test_publisher_ref_resolving_pack_accepts_without_warning():
    report = validate(make_manifest())
    assert report.accepted
    assert report.result == "ACCEPT"
    assert not any(d.code == "PPK-MAN-W002" for d in report.diagnostics)
