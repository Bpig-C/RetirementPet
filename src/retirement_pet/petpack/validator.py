"""PetPack validation pipeline (PETPACK_SPEC 18; pure-logic tier).

Phase order (fail-closed on first ERROR):
    1. archive preflight (restricted ZIP profile)
    2. strict manifest parse + structure/IDs + compatibility shape
    3. inventory closure: every non-manifest file declared, every declared
       file present; asset/legal IDs unique
    4. per-asset verification: byte_size, sha256, media type, budgets
    5. semantic checks: rights & provenance, engine compatibility
       (engine_profile registry), geometry, text profiles, legal files,
       action timelines, reference closure, renderer honesty
    6. canonical content_digest -> RevisionKey

The offscreen core.idle first-frame render (spec 18 step 10) runs in the
Qt tier and is layered on top of this report by the caller.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
from dataclasses import dataclass, field
from typing import Any

from retirement_pet.petpack.archive import (
    MANIFEST_NAME,
    PetpackArchive,
    check_reserved_basename,
)
from retirement_pet.petpack.diagnostics import (
    RGT_E001_INVALID_RIGHTS,
    RGT_E002_MISSING_REF,
    RGT_E003_OFFICIAL_UNKNOWN_RIGHTS,
    RGT_W001_UNKNOWN_BASIS,
    ACT_E001_CORE_IDLE_MISSING,
    ACT_E002_UNKNOWN_CORE_SEMANTIC,
    ACT_E003_INVALID_NAMESPACE,
    ACT_E004_UNSUPPORTED_RENDERER,
    ACT_E005_MISSING_ASSET_OR_TYPE,
    ACT_E006_TIMING_LOOP_OR_BUDGET,
    ACT_W001_SEMANTIC_DISABLED,
    ACT_W002_RENDERER_UNSUPPORTED,
    MAN_E004_MISSING_OR_INVALID_FIELD,
    MAN_E006_INCOMPATIBLE_ENGINE,
    MAN_E007_UNDECLARED_OR_MISSING_FILE,
    MAN_E008_SIZE_OR_HASH_MISMATCH,
    MAN_W001_UNKNOWN_RECOMMENDATION,
    MAN_W002_HISTORICAL_PUBLISHER_REF_EXEMPT,
    RES_E001_DISALLOWED_TYPE_OR_MIME,
    RES_E003_IMAGE_DECODE_OR_PIXELS,
    RES_E004_FRAMES_OR_DECODE_MEMORY,
    RES_E005_AUDIO_FORMAT_OR_DURATION,
    TXT_E001_FORBIDDEN_TEMPLATE_OR_EXPR,
    TXT_E002_INVALID_PROFILE,
    TXT_E003_CONTROL_OR_RICH_TEXT,
    Diagnostic,
    Severity,
)
from retirement_pet.petpack.diagnostics import ValidationFailure
from retirement_pet.petpack.engine_profile import (
    ENGINE_VERSION,
    KNOWN_CORE_SEMANTICS,
    SUPPORTED_CAPABILITIES,
    SUPPORTED_RENDERER_KINDS,
    semantic_capability,
)
from retirement_pet.petpack.identity import (
    PackKey,
    RevisionKey,
    compute_content_digest,
    sha256_file,
)
from retirement_pet.petpack.manifest import (
    check_id,
    parse_manifest,
    semver_less,
    validate_manifest_structure,
)
from retirement_pet.text_profile import (
    SAFE_VARIABLES,
    TextSafetyError,
    check_plain_text,
)

# -- budgets (PETPACK_SPEC 17) --------------------------------------------------

MAX_IMAGE_PIXELS = 4096 * 4096
MAX_FRAMES_PER_ACTION = 300
MIN_FRAME_DURATION_MS = 33   # visible refresh capped at 30 FPS
MAX_SFX_SECONDS = 60.0
MAX_TOTAL_SFX_SECONDS = 600.0

_ALLOWED_MEDIA = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "audio/wav": b"RIFF",
    "audio/ogg": b"OggS",
    "text/plain": None,  # UTF-8 enforced, no magic
}

# -- geometry (PETPACK_SPEC 13) -------------------------------------------------

MAX_CANVAS_SIDE = 4096      # spec 17 has no canvas row; the single-image
_MAX_GEOMETRY_REGIONS = 16  # 4096x4096 budget is the closest traceable bound
_MAX_POLYGON_POINTS = 32
_GEOMETRY_TOLERANCE = 1e-6  # same bound the runtime layout parser uses

# -- text profiles (PETPACK_SPEC 14) ---------------------------------------------

MAX_PROFILE_ENTRIES = 256
MAX_PROFILE_CANDIDATES = 8
_TOKEN_STRAY = re.compile(r"\{([^{}]*)\}")
_KNOWN_RECOMMENDED_LAYOUTS = frozenset({
    "pet_only", "compact", "standard", "hover_expand", "focus",
})

# -- legacy publisher_ref exemption (PETPACK_SPEC 6.1; review CR-P01) -----------
#
# Spec 6.1 makes ``package.publisher_ref`` REQUIRED and resolvable.  The four
# internally retained historical/current packs listed here were frozen before
# that requirement was enforced, so they alone stay loadable WITHOUT one -
# each load surfaces PPK-MAN-W002 and a degradation entry.  The pin is the
# exact RevisionKey (publisher, package, version, canonical content digest):
# any rebuilt or re-exported variant of these packs - and every other pack -
# must declare a resolvable publisher_ref.  Frozen inputs are never rewritten
# to backfill the field.

_HISTORICAL_PUBLISHER_REF_EXEMPT = frozenset({
    RevisionKey(PackKey("official", "retirement-cat-official"), "1.0.0",
                "86684d32136c205a043995f4889b857c08d6496743047a77165ad72895bdb1ce"),
    RevisionKey(PackKey("official", "retirement-cat-official"), "1.0.1",
                "97caa2d3e253546dec06534fc627ffc1393558e7d9e88aae5433758cca0fa402"),
    RevisionKey(PackKey("community.retirementpet",
                        "realistic-retirement-cat"), "0.1.0",
                "472dfacfce41b4ca381ebfec9150f48c8531fde9c7eef0ce66484e6d527efb7b"),
    RevisionKey(PackKey("community.retirementpet",
                        "realistic-retirement-cat"), "0.1.1",
                "6c4b368ad79124bf5bc48e6d8190917c7031f923b2cf00f728c4c17b9c21260c"),
})


@dataclass
class ValidationReport:
    # These two fields bind a reusable engine report to the exact archive
    # snapshot and trust decision it validated.  A caller must never be able
    # to pair a report for archive A with bytes from archive B.
    archive_sha256: str | None = None
    trust_channel: str | None = None
    accepted: bool = False
    pack_key: PackKey | None = None
    revision_key: RevisionKey | None = None
    content_digest: str | None = None
    manifest: dict = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)

    @property
    def result(self) -> str:
        if not self.accepted:
            return "REJECT"
        return "ACCEPT_WITH_DEGRADATION" if self.degraded else "ACCEPT"


# -- helpers ---------------------------------------------------------------------

def _is_png(data: bytes) -> bool:
    return data.startswith(_ALLOWED_MEDIA["image/png"])


def _is_wav(data: bytes) -> bool:
    return data.startswith(b"RIFF") and data[8:12] == b"WAVE"


def _is_ogg(data: bytes) -> bool:
    return data.startswith(b"OggS")


def wav_duration_seconds(data: bytes) -> float | None:
    """Minimal RIFF/WAVE parsing for byte_size-based duration validation."""
    if not _is_wav(data) or len(data) < 44:
        return None
    try:
        # find fmt + data chunks (canonical layout first)
        byte_rate = struct.unpack_from("<I", data, 28)[0]
        data_size = struct.unpack_from("<I", data, 40)[0]
        if byte_rate == 0:
            return None
        return data_size / byte_rate
    except struct.error:
        return None


# -- pipeline ---------------------------------------------------------------------

def validate_petpack(data: bytes,
                     trust_channel: str = "LOCAL_IMPORTED") -> ValidationReport:
    """Validate one .petpack container (pure logic; no Qt).

    ``trust_channel`` is assigned by the ENGINE install entry, never by the
    pack: only BUILTIN_OFFICIAL may use reserved publisher namespaces.
    """
    report = ValidationReport(
        archive_sha256=hashlib.sha256(data).hexdigest(),
        trust_channel=trust_channel,
    )
    try:
        _validate(data, report, trust_channel)
    except ValidationFailure as failure:
        report.diagnostics.append(failure.diagnostic)
        report.accepted = False
    return report


def _validate(data: bytes, report: ValidationReport,
              trust_channel: str) -> None:
    archive = PetpackArchive(data)
    manifest = parse_manifest(archive.manifest_bytes)
    pack_key = validate_manifest_structure(manifest, trust_channel)
    report.manifest = manifest
    report.pack_key = pack_key

    # -- inventory closure ----------------------------------------------------
    declared_paths: set[str] = set()
    asset_by_path: dict[str, dict] = {}
    legal_by_path: dict[str, dict] = {}
    asset_ids: set[str] = set()
    for asset in manifest.get("assets", []):
        path = str(asset.get("path", ""))
        _ensure_safe_declared_path(path)
        if path in declared_paths:
            raise ValidationFailure(Diagnostic(
                MAN_E007_UNDECLARED_OR_MISSING_FILE, Severity.ERROR,
                "inventory", "petpack.inventory.duplicate_declaration", {}))
        declared_paths.add(path)
        asset_by_path[path] = asset
        asset_id = _declared_id(asset.get("id"), "asset_id")
        if asset_id in asset_ids:
            raise ValidationFailure(Diagnostic(
                MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                "inventory", "petpack.inventory.duplicate_id",
                {"kind": "asset"}))
        asset_ids.add(asset_id)
    legal_ids: set[str] = set()
    for legal in manifest.get("legal_files", []):
        path = str(legal.get("path", ""))
        _ensure_safe_declared_path(path)
        declared_paths.add(path)
        legal_by_path[path] = legal
        legal_id = _declared_id(legal.get("id"), "legal_id")
        if legal_id in legal_ids:
            raise ValidationFailure(Diagnostic(
                MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                "inventory", "petpack.inventory.duplicate_id",
                {"kind": "legal"}))
        legal_ids.add(legal_id)

    actual_paths = set(archive.paths()) - {MANIFEST_NAME}
    undeclared = actual_paths - declared_paths
    if undeclared:
        raise ValidationFailure(Diagnostic(
            MAN_E007_UNDECLARED_OR_MISSING_FILE, Severity.ERROR, "inventory",
            "petpack.inventory.undeclared_file", {"count": len(undeclared)}))
    missing = declared_paths - actual_paths
    if missing:
        raise ValidationFailure(Diagnostic(
            MAN_E007_UNDECLARED_OR_MISSING_FILE, Severity.ERROR, "inventory",
            "petpack.inventory.missing_file", {"count": len(missing)}))

    # -- per-asset verification ------------------------------------------------
    total_sfx_seconds = 0.0
    for path in sorted(asset_by_path):
        asset = asset_by_path[path]
        data_bytes = archive.read(path) or b""
        declared_size = asset.get("byte_size")
        declared_hash = asset.get("sha256")
        media = str(asset.get("media_type", ""))
        computed_hash = sha256_file(data_bytes)

        if not isinstance(declared_size, int) or declared_size != len(data_bytes):
            raise ValidationFailure(Diagnostic(
                MAN_E008_SIZE_OR_HASH_MISMATCH, Severity.ERROR, "assets",
                "petpack.assets.size_mismatch", {"kind": "size"}))
        if declared_hash != computed_hash:
            raise ValidationFailure(Diagnostic(
                MAN_E008_SIZE_OR_HASH_MISMATCH, Severity.ERROR, "assets",
                "petpack.assets.hash_mismatch", {"kind": "sha256"}))

        if media == "image/png":
            if not _is_png(data_bytes):
                raise ValidationFailure(Diagnostic(
                    RES_E001_DISALLOWED_TYPE_OR_MIME, Severity.ERROR, "assets",
                    "petpack.assets.magic_mismatch", {"kind": "png"}))
            width, height = _png_dimensions(data_bytes)
            if (width, height) == (0, 0) or width * height > MAX_IMAGE_PIXELS:
                raise ValidationFailure(Diagnostic(
                    RES_E003_IMAGE_DECODE_OR_PIXELS, Severity.ERROR, "assets",
                    "petpack.assets.pixels", {}))
        elif media == "audio/wav":
            if not _is_wav(data_bytes):
                raise ValidationFailure(Diagnostic(
                    RES_E001_DISALLOWED_TYPE_OR_MIME, Severity.ERROR, "assets",
                    "petpack.assets.magic_mismatch", {"kind": "wav"}))
            duration = wav_duration_seconds(data_bytes)
            if duration is None or duration > MAX_SFX_SECONDS:
                raise ValidationFailure(Diagnostic(
                    RES_E005_AUDIO_FORMAT_OR_DURATION, Severity.ERROR, "assets",
                    "petpack.assets.audio_duration", {}))
            total_sfx_seconds += duration
        elif media == "audio/ogg":
            if not _is_ogg(data_bytes):
                raise ValidationFailure(Diagnostic(
                    RES_E001_DISALLOWED_TYPE_OR_MIME, Severity.ERROR, "assets",
                    "petpack.assets.magic_mismatch", {"kind": "ogg"}))
        elif media == "text/plain":
            try:
                data_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValidationFailure(Diagnostic(
                    RES_E001_DISALLOWED_TYPE_OR_MIME, Severity.ERROR, "assets",
                    "petpack.assets.text_utf8", {})) from exc
        else:
            raise ValidationFailure(Diagnostic(
                RES_E001_DISALLOWED_TYPE_OR_MIME, Severity.ERROR, "assets",
                "petpack.assets.media_type", {}))

    if total_sfx_seconds > MAX_TOTAL_SFX_SECONDS:
        raise ValidationFailure(Diagnostic(
            RES_E004_FRAMES_OR_DECODE_MEMORY, Severity.ERROR, "assets",
            "petpack.assets.sfx_total", {}))

    # -- rights & provenance (PETPACK_SPEC 8; CONTENT_POLICY 5) -----------------
    _validate_rights(manifest, asset_by_path, trust_channel, report, legal_ids)

    # -- publishers (PETPACK_SPEC 6) ----------------------------------------------
    publisher_ids = _validate_publishers(manifest)

    # -- engine compatibility (PETPACK_SPEC 5/11; engine_profile registry) ------
    _validate_compatibility(manifest)

    # -- geometry (PETPACK_SPEC 13; mirrors runtime CharacterGeometry) -----------
    _validate_character_geometry(manifest)

    # -- text profiles (PETPACK_SPEC 14; text_profile.py is the same boundary) ---
    rights_ids = {str(r.get("id")) for r in manifest.get("rights_declarations", [])
                  if isinstance(r, dict)}
    sources_ids = {str(s.get("id")) for s in manifest.get("sources", [])
                   if isinstance(s, dict)}
    text_profile_ids = _validate_text_profiles(manifest, rights_ids, sources_ids)

    # -- legal files (PETPACK_SPEC 7.1) ------------------------------------------
    _validate_legal_files(manifest, legal_by_path, archive)

    # -- actions & reference closure ---------------------------------------------
    _validate_actions(manifest, asset_by_path, asset_ids, report,
                      text_profile_ids)

    # -- digest ------------------------------------------------------------------
    files = [(path, archive.read(path) or b"")
             for path in sorted(actual_paths)]
    digest = compute_content_digest(archive.manifest_bytes, files)
    report.content_digest = digest
    report.revision_key = RevisionKey(
        pack=pack_key,
        package_version=str(manifest["package"]["version"]),
        content_digest=digest,
    )

    # -- publisher_ref closure (PETPACK_SPEC 6.1; needs the digest) --------------
    _validate_publisher_ref(manifest, publisher_ids, report)

    report.accepted = True


def _ensure_safe_declared_path(path: str) -> None:
    from retirement_pet.petpack.archive import normalize_member_name
    normalized = normalize_member_name(path)
    if normalized != path:
        raise ValidationFailure(Diagnostic(
            MAN_E007_UNDECLARED_OR_MISSING_FILE, Severity.ERROR, "inventory",
            "petpack.inventory.non_canonical_path", {}))


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 33 or not _is_png(data):
        return (0, 0)
    width, height = struct.unpack_from(">II", data, 16)
    return (width, height)


def _validate_actions(manifest: dict, asset_by_path: dict[str, dict],
                      asset_ids: set[str], report: ValidationReport,
                      text_profile_ids: set[str]) -> None:
    actions = manifest.get("actions", [])
    if not isinstance(actions, list):
        raise ValidationFailure(Diagnostic(
            ACT_E006_TIMING_LOOP_OR_BUDGET, Severity.ERROR, "actions",
            "petpack.actions.container", {}))

    character_ids = {str(c.get("id", "")) for c in manifest.get("characters", [])}
    series_id = str(manifest.get("series", {}).get("id", ""))
    publisher_id = str(manifest.get("package", {}).get("publisher_id", ""))
    package_id = str(manifest.get("package", {}).get("id", ""))
    fq_prefix = f"{publisher_id}.{package_id}.{series_id}."

    optional_caps = set((manifest.get("compatibility") or {})
                        .get("optional_capabilities") or [])
    disabled_semantics: set[str] = set()
    undrawable_actions: set[str] = set()
    action_by_id: dict[str, dict] = {}
    for action in actions:
        if not isinstance(action, dict):
            raise ValidationFailure(Diagnostic(
                ACT_E006_TIMING_LOOP_OR_BUDGET, Severity.ERROR, "actions",
                "petpack.actions.entry", {}))
        action_id = _declared_id(action.get("id"), "action_id")
        if action_id in action_by_id:
            raise ValidationFailure(Diagnostic(
                MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR, "actions",
                "petpack.actions.duplicate_id", {}))
        action_by_id[action_id] = action
        semantic = action.get("semantic")
        if semantic is not None:
            semantic = str(semantic)
            if semantic.startswith("core."):
                # Unknown core semantics are only tolerable when the exact
                # mapped capability is declared optional (PETPACK_SPEC 11):
                # the action is then DISABLED with a visible degradation.
                if semantic not in KNOWN_CORE_SEMANTICS:
                    if semantic_capability(semantic) in optional_caps:
                        disabled_semantics.add(semantic)
                    else:
                        raise ValidationFailure(Diagnostic(
                            ACT_E002_UNKNOWN_CORE_SEMANTIC, Severity.ERROR,
                            "actions", "petpack.actions.unknown_semantic",
                            {"semantic": semantic}))
                # known semantics need no namespace
            elif not any(action_id.startswith(fq_prefix + cid + ".")
                         for cid in character_ids):
                raise ValidationFailure(Diagnostic(
                    ACT_E003_INVALID_NAMESPACE, Severity.ERROR, "actions",
                    "petpack.actions.namespace", {}))
        _validate_action_timeline(action, asset_by_path, asset_ids)

    # -- per-character bindings, thumbnails, variants, recommendations --------
    characters = manifest.get("characters", [])
    for character in characters:
        cid = str(character.get("id", ""))
        bindings = character.get("actions")
        merged: dict[str, str] = {}
        if isinstance(bindings, dict):
            for semantic_key, bound_id in bindings.items():
                bound_id = str(bound_id)
                if bound_id not in action_by_id:
                    raise ValidationFailure(Diagnostic(
                        ACT_E005_MISSING_ASSET_OR_TYPE, Severity.ERROR,
                        "actions", "petpack.actions.dangling_binding",
                        {"character": cid}))
                merged[str(semantic_key)] = bound_id

        idle_ref = merged.get("core.idle")
        if not idle_ref:
            raise ValidationFailure(Diagnostic(
                ACT_E001_CORE_IDLE_MISSING, Severity.ERROR, "actions",
                "petpack.actions.idle_missing", {"character": cid}))
        _check_drawable_bindings(merged, action_by_id, cid,
                                 undrawable_actions)

        thumbnail = character.get("thumbnail_asset")
        if not isinstance(thumbnail, str) or not thumbnail:
            raise ValidationFailure(Diagnostic(
                MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                "actions", "petpack.actions.thumbnail_missing",
                {"character": cid}))
        if thumbnail not in asset_ids:
            raise ValidationFailure(Diagnostic(
                ACT_E005_MISSING_ASSET_OR_TYPE, Severity.ERROR, "actions",
                "petpack.actions.dangling_thumbnail", {"character": cid}))

        for ref in character.get("text_profile_refs", []) or []:
            if str(ref) not in text_profile_ids:
                raise ValidationFailure(Diagnostic(
                    TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
                    "petpack.text.dangling_ref", {}))
        _validate_recommendations(character.get("recommended_profiles"),
                                  text_profile_ids, report)

        seen_variants: set[str] = set()
        for variant in character.get("variants", []) or []:
            if not isinstance(variant, dict):
                raise ValidationFailure(Diagnostic(
                    MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                    "actions", "petpack.actions.variant", {}))
            variant_id = check_id(str(variant.get("id", "")), "variant_id")
            if variant_id in seen_variants:
                raise ValidationFailure(Diagnostic(
                    MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                    "actions", "petpack.actions.variant_duplicate", {}))
            seen_variants.add(variant_id)
            variant_merged = dict(merged)
            overrides = variant.get("action_overrides")
            if overrides is not None:
                if not isinstance(overrides, dict):
                    raise ValidationFailure(Diagnostic(
                        MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                        "actions", "petpack.actions.variant_overrides", {}))
                for semantic_key, bound_id in overrides.items():
                    bound_id = str(bound_id)
                    if bound_id not in action_by_id:
                        raise ValidationFailure(Diagnostic(
                            ACT_E005_MISSING_ASSET_OR_TYPE, Severity.ERROR,
                            "actions", "petpack.actions.dangling_binding",
                            {"character": cid}))
                    variant_merged[str(semantic_key)] = bound_id
            if not variant_merged.get("core.idle"):
                raise ValidationFailure(Diagnostic(
                    ACT_E001_CORE_IDLE_MISSING, Severity.ERROR, "actions",
                    "petpack.actions.idle_missing",
                    {"character": f"{cid}.{variant_id}"}))
            _check_drawable_bindings(variant_merged, action_by_id,
                                     f"{cid}.{variant_id}",
                                     undrawable_actions)
            variant_thumbnail = variant.get("thumbnail_asset")
            if variant_thumbnail is not None \
                    and str(variant_thumbnail) not in asset_ids:
                raise ValidationFailure(Diagnostic(
                    ACT_E005_MISSING_ASSET_OR_TYPE, Severity.ERROR,
                    "actions", "petpack.actions.dangling_thumbnail",
                    {"character": f"{cid}.{variant_id}"}))
            for ref in variant.get("text_profile_refs", []) or []:
                if str(ref) not in text_profile_ids:
                    raise ValidationFailure(Diagnostic(
                        TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
                        "petpack.text.dangling_ref", {}))

    # -- degradable facts become visible, stable warnings (never silent) ------
    for semantic in sorted(disabled_semantics):
        report.diagnostics.append(Diagnostic(
            ACT_W001_SEMANTIC_DISABLED, Severity.WARNING, "actions",
            "petpack.actions.semantic_disabled",
            {"semantic": semantic}, recoverable=True))
        report.degraded.append(f"semantic_disabled:{semantic}")
    for action_id in sorted(undrawable_actions):
        report.diagnostics.append(Diagnostic(
            ACT_W002_RENDERER_UNSUPPORTED, Severity.WARNING, "actions",
            "petpack.actions.renderer_unsupported",
            {"action": action_id}, recoverable=True))
        report.degraded.append(f"renderer_unsupported:{action_id}")

    # -- rig sharing rules (PETPACK_SPEC 9 / 11.4) ----------------------------
    _validate_rig_sharing(manifest, characters, action_by_id)


def _check_drawable_bindings(bindings: dict[str, str],
                             action_by_id: dict[str, dict],
                             owner: str,
                             undrawable_actions: set[str]) -> None:
    """Renderer honesty: runtime admission draws static/sequence only.

    core.idle is the admission floor: an idle the runtime cannot draw -
    layered templates included - is a hard PPK-ACT-E004 reject.  A
    character that could never render its idle must not obtain runtime
    admission, and PPK-ACT-W002 must never convert that into an
    installable-with-warning pack (controller ruling P-2).  The action
    itself stays structurally parseable; the two layers are tested
    separately and one must not lower the other.

    Any OTHER bound action the runtime cannot draw is disabled with a
    stable PPK-ACT-W002 warning instead of silently pretending support;
    a drawable core.idle is guaranteed to exist by the check above, so
    such an action always has a usable fallback.
    """
    for semantic_key, action_id in bindings.items():
        if semantic_key == "core.idle":
            continue  # idle drawability decided below
        if not _action_drawable(action_by_id[action_id]):
            undrawable_actions.add(action_id)
    idle_id = bindings["core.idle"]
    if not _action_drawable(action_by_id[idle_id]):
        raise ValidationFailure(Diagnostic(
            ACT_E004_UNSUPPORTED_RENDERER, Severity.ERROR, "actions",
            "petpack.actions.idle_undrawable", {"character": owner}))


def _action_drawable(action: dict) -> bool:
    """Mirror the runtime's stage selection (petpack.runtime._resolve_profile:
    ``lifecycle.loop or lifecycle.enter``) - loop first, ONE section decides.

    A present but undrawable loop is not saved by a drawable enter: the
    runtime would resolve the loop and draw nothing, so admitting such an
    idle would break the P-2 admission floor (acceptance round-2 probe).
    """
    lifecycle = action.get("lifecycle")
    if not isinstance(lifecycle, dict):
        return False
    section = lifecycle.get("loop") or lifecycle.get("enter")
    if not isinstance(section, dict):
        return False
    renderer = section.get("renderer")
    return isinstance(renderer, dict) and \
        renderer.get("type") in SUPPORTED_RENDERER_KINDS


def _validate_recommendations(recommended: Any, text_profile_ids: set[str],
                              report: ValidationReport) -> None:
    if recommended is None:
        return
    if not isinstance(recommended, dict):
        raise ValidationFailure(Diagnostic(
            MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR, "manifest",
            "petpack.manifest.recommendations", {}))
    display = recommended.get("display")
    if (isinstance(display, str) and display
            and display not in _KNOWN_RECOMMENDED_LAYOUTS):
        # spec 13: unknown recommended layout is a WARNING and is ignored
        report.diagnostics.append(Diagnostic(
            MAN_W001_UNKNOWN_RECOMMENDATION, Severity.WARNING, "manifest",
            "petpack.manifest.recommendation_ignored",
            {"layout": display}, recoverable=True))
        report.degraded.append("recommendation_ignored")
    text_ref = recommended.get("text")
    if text_ref is not None and str(text_ref) not in text_profile_ids:
        raise ValidationFailure(Diagnostic(
            TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
            "petpack.text.dangling_ref", {}))


def _validate_rig_sharing(manifest: dict, characters: list,
                          action_by_id: dict[str, dict]) -> None:
    """Shared raw action assets need an explicit identical rig_contract, and
    the shared action must be a parameterized layered TEMPLATE - static /
    sequence body frames are never shared across characters."""
    declared_contracts = {c.get("id")
                          for c in manifest.get("rig_contracts", []) or []}

    # usage: action_id -> [character ids binding it]
    usage: dict[str, list[str]] = {}
    character_contract: dict[str, str | None] = {}
    for character in characters:
        cid = str(character.get("id", ""))
        contract = character.get("rig_contract_ref")
        if contract is not None and contract not in declared_contracts:
            raise ValidationFailure(Diagnostic(
                ACT_E005_MISSING_ASSET_OR_TYPE, Severity.ERROR, "rig",
                "petpack.rig.contract_undeclared", {}))
        character_contract[cid] = contract
        for bound in (character.get("actions", {}) or {}).values():
            usage.setdefault(str(bound), []).append(cid)

    for action_id, users in usage.items():
        if len(users) < 2:
            continue
        action = action_by_id.get(action_id)
        if action is None:
            continue
        action_contract = action.get("rig_contract_ref")
        if not action_contract:
            raise ValidationFailure(Diagnostic(
                ACT_E005_MISSING_ASSET_OR_TYPE, Severity.ERROR, "rig",
                "petpack.rig.shared_without_contract",
                {"kind": "no_contract"}))
        if any(character_contract[u] != action_contract for u in users):
            raise ValidationFailure(Diagnostic(
                ACT_E005_MISSING_ASSET_OR_TYPE, Severity.ERROR, "rig",
                "petpack.rig.contract_mismatch", {"kind": "mismatch"}))
        # shared raw material must be a layered TEMPLATE, not body frames
        lifecycle = action.get("lifecycle", {})
        renderer = ((lifecycle.get("loop") or lifecycle.get("enter") or {})
                    .get("renderer") or {})
        if renderer.get("type") != "layered":
            raise ValidationFailure(Diagnostic(
                ACT_E005_MISSING_ASSET_OR_TYPE, Severity.ERROR, "rig",
                "petpack.rig.shared_body_frames", {"kind": "body_frames"}))


def _validate_action_timeline(action: dict,
                              asset_by_path: dict[str, dict],
                              asset_ids: set[str]) -> None:
    lifecycle = action.get("lifecycle")
    if not isinstance(lifecycle, dict):
        raise ValidationFailure(Diagnostic(
            ACT_E006_TIMING_LOOP_OR_BUDGET, Severity.ERROR, "actions",
            "petpack.actions.lifecycle", {}))
    for stage in ("enter", "loop", "exit"):
        section = lifecycle.get(stage)
        if not section:
            continue
        renderer = section.get("renderer") if isinstance(section, dict) else None
        if not isinstance(renderer, dict):
            continue
        kind = renderer.get("type")
        if kind not in ("static", "sequence", "builtin_effect", "layered"):
            raise ValidationFailure(Diagnostic(
                ACT_E006_TIMING_LOOP_OR_BUDGET, Severity.ERROR, "actions",
                "petpack.actions.renderer", {}))
        if kind == "layered":
            # parameterized template reference; full keyframe validation
            # arrives with the layered runtime increment
            template = renderer.get("template")
            if not isinstance(template, str) or not template:
                raise ValidationFailure(Diagnostic(
                    ACT_E005_MISSING_ASSET_OR_TYPE, Severity.ERROR,
                    "actions", "petpack.actions.layered_template", {}))
        if kind == "static":
            asset_id = renderer.get("asset")
            if not isinstance(asset_id, str) or asset_id not in asset_ids:
                raise ValidationFailure(Diagnostic(
                    ACT_E005_MISSING_ASSET_OR_TYPE, Severity.ERROR,
                    "actions", "petpack.actions.static_asset", {}))
        if kind == "sequence":
            frames = renderer.get("frames", [])
            if not frames or len(frames) > MAX_FRAMES_PER_ACTION:
                raise ValidationFailure(Diagnostic(
                    RES_E004_FRAMES_OR_DECODE_MEMORY, Severity.ERROR,
                    "actions", "petpack.actions.frame_count", {}))
            for frame in frames:
                asset_id = str(frame.get("asset", ""))
                if asset_id not in asset_ids:
                    raise ValidationFailure(Diagnostic(
                        ACT_E005_MISSING_ASSET_OR_TYPE, Severity.ERROR,
                        "actions", "petpack.actions.frame_asset", {}))
                duration = frame.get("duration_ms")
                if (not isinstance(duration, int) or duration < MIN_FRAME_DURATION_MS):
                    raise ValidationFailure(Diagnostic(
                        ACT_E006_TIMING_LOOP_OR_BUDGET, Severity.ERROR,
                        "actions", "petpack.actions.frame_duration", {}))


_ALLOWED_BASES = {"original", "licensed", "open_license", "public_domain",
                  "permission_claimed", "unknown"}
_NEEDS_LICENSE_EVIDENCE = {"licensed", "open_license"}


def _validate_rights(manifest: dict, asset_by_path: dict[str, dict],
                     trust_channel: str, report: ValidationReport,
                     legal_ids: set[str]) -> None:
    """Every media asset carries rights_ref + source_ref; declarations are
    structurally valid; basis rules hold per channel (PETPACK_SPEC 8).

    Errors reject; an `unknown` basis on a LOCAL import is a WARNING the
    user must acknowledge (recorded into the receipt); on the OFFICIAL
    channel it is an ERROR (no unknown rights ship officially).
    """
    rights = {str(r.get("id")): r for r in manifest.get("rights_declarations", [])
              if isinstance(r, dict)}
    sources = {str(s.get("id")): s for s in manifest.get("sources", [])
               if isinstance(s, dict)}

    for rid, declaration in rights.items():
        basis = declaration.get("basis")
        if basis not in _ALLOWED_BASES:
            raise ValidationFailure(Diagnostic(
                RGT_E001_INVALID_RIGHTS, Severity.ERROR, "rights",
                "petpack.rights.basis", {}))
        license_ = declaration.get("license") or {}
        legal_ref = license_.get("legal_file_ref") if isinstance(license_, dict) else None
        if legal_ref is not None and str(legal_ref) not in legal_ids:
            # license evidence may only point at the legal_files table
            raise ValidationFailure(Diagnostic(
                RGT_E002_MISSING_REF, Severity.ERROR, "rights",
                "petpack.rights.missing_ref", {"kind": "legal_file"}))
        if basis in _NEEDS_LICENSE_EVIDENCE:
            has_spdx = bool(license_.get("spdx")) if isinstance(license_, dict) else False
            has_file = isinstance(legal_ref, str) and bool(legal_ref)
            if not (has_spdx or has_file):
                raise ValidationFailure(Diagnostic(
                    RGT_E001_INVALID_RIGHTS, Severity.ERROR, "rights",
                    "petpack.rights.license_evidence", {}))
        scope = declaration.get("scope_claimed") or []
        if basis == "unknown" and "official_distribution" in scope:
            raise ValidationFailure(Diagnostic(
                RGT_E001_INVALID_RIGHTS, Severity.ERROR, "rights",
                "petpack.rights.unknown_claims_official", {}))

    unknown_assets = 0
    for path, asset in sorted(asset_by_path.items()):
        rights_ref = asset.get("rights_ref")
        source_ref = asset.get("source_ref")
        if not rights_ref or rights_ref not in rights:
            raise ValidationFailure(Diagnostic(
                RGT_E002_MISSING_REF, Severity.ERROR, "rights",
                "petpack.rights.missing_ref", {"kind": "rights"}))
        if not source_ref or source_ref not in sources:
            raise ValidationFailure(Diagnostic(
                RGT_E002_MISSING_REF, Severity.ERROR, "rights",
                "petpack.rights.missing_ref", {"kind": "source"}))
        if rights[rights_ref].get("basis") == "unknown":
            if trust_channel == "BUILTIN_OFFICIAL":
                raise ValidationFailure(Diagnostic(
                    RGT_E003_OFFICIAL_UNKNOWN_RIGHTS, Severity.ERROR,
                    "rights", "petpack.rights.official_unknown", {}))
            unknown_assets += 1
    if unknown_assets:
        # ONE warning per pack (aggregated count), user acknowledges it once
        report.diagnostics.append(Diagnostic(
            RGT_W001_UNKNOWN_BASIS, Severity.WARNING, "rights",
            "petpack.rights.unknown_basis",
            {"count": unknown_assets}, recoverable=True))
        report.degraded.append("rights_unknown")


# -- engine compatibility (PETPACK_SPEC 5 / 11) ---------------------------------

def _validate_publishers(manifest: dict) -> set[str]:
    """Publisher entries are structural; returns their declared ids for the
    publisher_ref closure (PETPACK_SPEC 6)."""
    publishers = manifest.get("publishers", [])
    if not isinstance(publishers, list):
        raise ValidationFailure(Diagnostic(
            MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR, "manifest",
            "petpack.manifest.publishers", {}))
    ids: set[str] = set()
    for publisher in publishers:
        if not isinstance(publisher, dict):
            raise ValidationFailure(Diagnostic(
                MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR, "manifest",
                "petpack.manifest.publisher", {}))
        ids.add(_declared_id(publisher.get("id"), "publisher_id"))
    return ids


def _validate_publisher_ref(manifest: dict, ids: set[str],
                            report: ValidationReport) -> None:
    """publisher_ref closure (PETPACK_SPEC 6.1: REQUIRED, runs after the
    content digest is known so the legacy exemption can pin exact
    revisions).

    A declared publisher_ref must be a string resolving to a declared
    publisher.  When it is ABSENT, only the exact frozen historical
    revisions in ``_HISTORICAL_PUBLISHER_REF_EXEMPT`` stay loadable, each
    with a stable PPK-MAN-W002 warning and degradation entry; every other
    pack is rejected (PPK-MAN-E004).
    """
    publisher_ref = manifest.get("package", {}).get("publisher_ref")
    if publisher_ref is not None:
        if not isinstance(publisher_ref, str):
            raise ValidationFailure(Diagnostic(
                MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                "manifest", "petpack.manifest.publisher_ref_invalid", {}))
        if publisher_ref not in ids:
            raise ValidationFailure(Diagnostic(
                MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR, "manifest",
                "petpack.manifest.publisher_ref_dangling", {}))
        return
    assert report.revision_key is not None
    if report.revision_key in _HISTORICAL_PUBLISHER_REF_EXEMPT:
        report.diagnostics.append(Diagnostic(
            MAN_W002_HISTORICAL_PUBLISHER_REF_EXEMPT, Severity.WARNING,
            "manifest", "petpack.manifest.publisher_ref_historical_exempt",
            {"revision": str(report.revision_key)}, recoverable=True))
        report.degraded.append("publisher_ref_exempt:historical")
        return
    raise ValidationFailure(Diagnostic(
        MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR, "manifest",
        "petpack.manifest.publisher_ref_required", {}))


def _validate_compatibility(manifest: dict) -> None:
    """Value-level compatibility against THIS engine's facts registry.

    A pack whose engine range excludes the running engine, or whose
    required capability the engine cannot satisfy, could never render as
    authored: PPK-MAN-E006 rejects it (spec 19.1 "Engine 或必需能力不兼容").
    """
    compatibility = manifest.get("compatibility") or {}
    lower = compatibility.get("engine_min")
    upper = compatibility.get("engine_max_exclusive")
    if lower is not None and semver_less(ENGINE_VERSION, str(lower)):
        raise ValidationFailure(Diagnostic(
            MAN_E006_INCOMPATIBLE_ENGINE, Severity.ERROR, "compatibility",
            "petpack.compatibility.engine_below_min", {}))
    if upper is not None and not semver_less(ENGINE_VERSION, str(upper)):
        raise ValidationFailure(Diagnostic(
            MAN_E006_INCOMPATIBLE_ENGINE, Severity.ERROR, "compatibility",
            "petpack.compatibility.engine_above_max", {}))
    for capability in compatibility.get("required_capabilities") or []:
        if capability not in SUPPORTED_CAPABILITIES:
            raise ValidationFailure(Diagnostic(
                MAN_E006_INCOMPATIBLE_ENGINE, Severity.ERROR, "compatibility",
                "petpack.compatibility.required_capability",
                {"capability": str(capability)}))


# -- geometry (PETPACK_SPEC 13; mirrors runtime CharacterGeometry) ---------------

_DECLARED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _declared_id(value: Any, kind: str) -> str:
    """Light grammar for dotted entity IDs (asset/action/legal/profile)."""
    if (not isinstance(value, str) or not value or len(value) > 128
            or not _DECLARED_ID_RE.match(value)):
        raise ValidationFailure(Diagnostic(
            MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR, "manifest",
            "petpack.manifest.bad_declared_id", {"kind": kind}))
    return value


def _geometry_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _fail_geometry(field: str) -> None:
    raise ValidationFailure(Diagnostic(
        MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR, "geometry",
        "petpack.geometry.invalid", {"field": field}))


def _geometry_size(value: Any, field: str) -> tuple[float, float]:
    if not isinstance(value, dict):
        _fail_geometry(field)
    width = _geometry_number(value.get("width"))
    height = _geometry_number(value.get("height"))
    if (width is None or height is None or width <= 0 or height <= 0
            or width > MAX_CANVAS_SIDE or height > MAX_CANVAS_SIDE):
        _fail_geometry(field)
    return (width, height)


def _geometry_rect(value: Any, field: str,
                   canvas_w: float, canvas_h: float) -> None:
    if not isinstance(value, dict):
        _fail_geometry(field)
    x = _geometry_number(value.get("x"))
    y = _geometry_number(value.get("y"))
    width = _geometry_number(value.get("width"))
    height = _geometry_number(value.get("height"))
    if None in (x, y, width, height) or width <= 0 or height <= 0:
        _fail_geometry(field)
    tol = _GEOMETRY_TOLERANCE
    if (x < -tol or y < -tol
            or x + width > canvas_w + tol or y + height > canvas_h + tol):
        _fail_geometry(field)


def _geometry_point(value: Any, field: str,
                    canvas_w: float, canvas_h: float) -> None:
    if not isinstance(value, dict):
        _fail_geometry(field)
    x = _geometry_number(value.get("x"))
    y = _geometry_number(value.get("y"))
    if x is None or y is None:
        _fail_geometry(field)
    tol = _GEOMETRY_TOLERANCE
    if not (0 <= x <= canvas_w + tol and 0 <= y <= canvas_h + tol):
        _fail_geometry(field)


def _validate_hit_region(region: Any,
                         canvas_w: float, canvas_h: float) -> None:
    if not isinstance(region, dict):
        _fail_geometry("hit_regions")
    shape = region.get("shape")
    if shape == "rect":
        _geometry_rect(region, "hit_regions", canvas_w, canvas_h)
    elif shape == "circle":
        _geometry_point(region.get("center"), "hit_regions", canvas_w, canvas_h)
        radius = _geometry_number(region.get("radius"))
        if radius is None or radius <= 0:
            _fail_geometry("hit_regions")
        center = region.get("center")
        cx = _geometry_number(center.get("x"))
        cy = _geometry_number(center.get("y"))
        tol = _GEOMETRY_TOLERANCE
        if (cx - radius < -tol or cy - radius < -tol
                or cx + radius > canvas_w + tol or cy + radius > canvas_h + tol):
            _fail_geometry("hit_regions")
    elif shape == "polygon":
        points = region.get("points")
        if (not isinstance(points, list)
                or not 3 <= len(points) <= _MAX_POLYGON_POINTS):
            _fail_geometry("hit_regions")
        for point in points:
            _geometry_point(point, "hit_regions", canvas_w, canvas_h)
    else:
        _fail_geometry("hit_regions")


def _validate_geometry(geometry: dict) -> tuple[float, float]:
    canvas_w, canvas_h = _geometry_size(geometry.get("logical_canvas"),
                                        "logical_canvas")
    _geometry_rect(geometry.get("content_bounds"), "content_bounds",
                   canvas_w, canvas_h)
    _geometry_rect(geometry.get("motion_bounds"), "motion_bounds",
                   canvas_w, canvas_h)
    _geometry_point(geometry.get("base_anchor"), "base_anchor",
                    canvas_w, canvas_h)
    _geometry_point(geometry.get("bubble_anchor"), "bubble_anchor",
                    canvas_w, canvas_h)
    reference_height = _geometry_number(geometry.get("reference_height"))
    if reference_height is None or reference_height <= 0:
        _fail_geometry("reference_height")
    regions = geometry.get("hit_regions")
    if regions is not None:
        if not isinstance(regions, list) or len(regions) > _MAX_GEOMETRY_REGIONS:
            _fail_geometry("hit_regions")
        for region in regions:
            _validate_hit_region(region, canvas_w, canvas_h)
    return (canvas_w, canvas_h)


def _validate_character_geometry(manifest: dict) -> None:
    for character in manifest.get("characters", []):
        geometry = character.get("geometry")
        if not isinstance(geometry, dict):
            _fail_geometry("geometry")
        canvas = _validate_geometry(geometry)
        for variant in character.get("variants", []) or []:
            if not isinstance(variant, dict):
                continue  # structure reported by the actions closure pass
            override = variant.get("geometry_override")
            if override is None:
                continue
            if not isinstance(override, dict):
                _fail_geometry("geometry_override")
            # spec 10: an override must share the character's canvas and
            # base-anchor coordinate system
            if _validate_geometry(override) != canvas:
                _fail_geometry("geometry_override")


# -- text profiles (PETPACK_SPEC 14) ----------------------------------------------

def _validate_text_profiles(manifest: dict, rights_ids: set[str],
                            sources_ids: set[str]) -> set[str]:
    profiles = manifest.get("text_profiles", [])
    if not isinstance(profiles, list):
        raise ValidationFailure(Diagnostic(
            TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
            "petpack.text.container", {}))
    ids: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValidationFailure(Diagnostic(
                TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
                "petpack.text.profile", {}))
        profile_id = _declared_id(profile.get("id"), "text_profile_id")
        if profile_id in ids:
            raise ValidationFailure(Diagnostic(
                TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
                "petpack.text.duplicate_profile", {}))
        ids.add(profile_id)
        if not isinstance(profile.get("locale"), str) or not profile["locale"]:
            raise ValidationFailure(Diagnostic(
                TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
                "petpack.text.locale", {}))
        # spec 14: a profile MUST provide default rights/source references
        for ref_kind, declared in (("rights_ref", rights_ids),
                                   ("source_ref", sources_ids)):
            ref = profile.get(ref_kind)
            if not isinstance(ref, str) or not ref:
                raise ValidationFailure(Diagnostic(
                    TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
                    "petpack.text.missing_ref", {"kind": ref_kind}))
            if ref not in declared:
                raise ValidationFailure(Diagnostic(
                    RGT_E002_MISSING_REF, Severity.ERROR, "rights",
                    "petpack.rights.missing_ref", {"kind": ref_kind}))
        entries = profile.get("entries", {})
        if not isinstance(entries, dict) or len(entries) > MAX_PROFILE_ENTRIES:
            raise ValidationFailure(Diagnostic(
                TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
                "petpack.text.entries", {}))
        for key, value in entries.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValidationFailure(Diagnostic(
                    TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
                    "petpack.text.entry_key", {}))
            _validate_profile_value(value)
    return ids


def _validate_profile_value(value: Any) -> None:
    if isinstance(value, str):
        _validate_profile_text(value)
        return
    if isinstance(value, list):
        if not value or len(value) > MAX_PROFILE_CANDIDATES:
            raise ValidationFailure(Diagnostic(
                TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
                "petpack.text.candidates", {}))
        for candidate in value:
            if not isinstance(candidate, dict) or "text" not in candidate:
                raise ValidationFailure(Diagnostic(
                    TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
                    "petpack.text.candidate", {}))
            _validate_profile_text(candidate.get("text"))
            for ref_kind in ("rights_ref", "source_ref"):
                ref = candidate.get(ref_kind)
                if ref is not None and (not isinstance(ref, str) or not ref):
                    raise ValidationFailure(Diagnostic(
                        TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
                        "petpack.text.bad_ref", {"kind": ref_kind}))
        return
    raise ValidationFailure(Diagnostic(
        TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
        "petpack.text.entry", {}))


def _validate_profile_text(text: Any) -> None:
    if not isinstance(text, str):
        raise ValidationFailure(Diagnostic(
            TXT_E002_INVALID_PROFILE, Severity.ERROR, "text",
            "petpack.text.entry", {}))
    try:
        # the exact runtime boundary: length, control/bidi, markup lookalikes
        check_plain_text(text)
    except TextSafetyError:
        raise ValidationFailure(Diagnostic(
            TXT_E003_CONTROL_OR_RICH_TEXT, Severity.ERROR, "text",
            "petpack.text.control", {}))
    for marker in ("${", "{{", "<%"):
        if marker in text:
            raise ValidationFailure(Diagnostic(
                TXT_E001_FORBIDDEN_TEMPLATE_OR_EXPR, Severity.ERROR, "text",
                "petpack.text.expression", {}))
    for match in _TOKEN_STRAY.finditer(text):
        name = match.group(1)
        # template tokens MUST come from the engine allowlist (spec 14);
        # anything else is a forbidden token, not a harmless literal
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name) \
                or name not in SAFE_VARIABLES:
            raise ValidationFailure(Diagnostic(
                TXT_E001_FORBIDDEN_TEMPLATE_OR_EXPR, Severity.ERROR, "text",
                "petpack.text.template_token", {"token": name}))


# -- legal files (PETPACK_SPEC 7.1) -------------------------------------------------

def _validate_legal_files(manifest: dict, legal_by_path: dict[str, dict],
                          archive: PetpackArchive) -> None:
    for path, legal in sorted(legal_by_path.items()):
        _declared_id(legal.get("id"), "legal_id")
        purpose = legal.get("purpose")
        if not isinstance(purpose, str) or not purpose:
            raise ValidationFailure(Diagnostic(
                MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR, "legal",
                "petpack.legal.purpose", {}))
        if legal.get("media_type") != "text/plain":
            raise ValidationFailure(Diagnostic(
                RES_E001_DISALLOWED_TYPE_OR_MIME, Severity.ERROR, "legal",
                "petpack.legal.media_type", {}))
        data = archive.read(path) or b""
        declared_size = legal.get("byte_size")
        if not isinstance(declared_size, int) or declared_size != len(data):
            raise ValidationFailure(Diagnostic(
                MAN_E008_SIZE_OR_HASH_MISMATCH, Severity.ERROR, "legal",
                "petpack.legal.size_mismatch", {"kind": "size"}))
        if legal.get("sha256") != sha256_file(data):
            raise ValidationFailure(Diagnostic(
                MAN_E008_SIZE_OR_HASH_MISMATCH, Severity.ERROR, "legal",
                "petpack.legal.hash_mismatch", {"kind": "sha256"}))
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationFailure(Diagnostic(
                RES_E001_DISALLOWED_TYPE_OR_MIME, Severity.ERROR, "legal",
                "petpack.legal.text_utf8", {})) from exc
