"""PetPack validation pipeline (PETPACK_SPEC 18; pure-logic tier).

Phase order (fail-closed on first ERROR):
    1. archive preflight (restricted ZIP profile)
    2. strict manifest parse + structure/IDs
    3. inventory closure: every non-manifest file declared, every declared
       file present
    4. per-asset verification: byte_size, sha256, media type, budgets
    5. action/text/geometry semantic checks (renderer profiles static /
       sequence; layered in a later increment)
    6. canonical content_digest -> RevisionKey

The offscreen core.idle first-frame render (spec 18 step 10) runs in the
Qt tier and is layered on top of this report by the caller.
"""

from __future__ import annotations

import hashlib
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
    ACT_E003_INVALID_NAMESPACE,
    ACT_E005_MISSING_ASSET_OR_TYPE,
    ACT_E006_TIMING_LOOP_OR_BUDGET,
    MAN_E007_UNDECLARED_OR_MISSING_FILE,
    MAN_E008_SIZE_OR_HASH_MISMATCH,
    RES_E001_DISALLOWED_TYPE_OR_MIME,
    RES_E003_IMAGE_DECODE_OR_PIXELS,
    RES_E004_FRAMES_OR_DECODE_MEMORY,
    RES_E005_AUDIO_FORMAT_OR_DURATION,
    Diagnostic,
    Severity,
)
from retirement_pet.petpack.diagnostics import ValidationFailure
from retirement_pet.petpack.identity import (
    PackKey,
    RevisionKey,
    compute_content_digest,
    sha256_file,
)
from retirement_pet.petpack.manifest import (
    check_id,
    parse_manifest,
    validate_manifest_structure,
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
    for asset in manifest.get("assets", []):
        path = str(asset.get("path", ""))
        _ensure_safe_declared_path(path)
        if path in declared_paths:
            raise ValidationFailure(Diagnostic(
                MAN_E007_UNDECLARED_OR_MISSING_FILE, Severity.ERROR,
                "inventory", "petpack.inventory.duplicate_declaration", {}))
        declared_paths.add(path)
        asset_by_path[path] = asset
    for legal in manifest.get("legal_files", []):
        path = str(legal.get("path", ""))
        _ensure_safe_declared_path(path)
        declared_paths.add(path)
        legal_by_path[path] = legal

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
    _validate_rights(manifest, asset_by_path, trust_channel, report)

    # -- actions ----------------------------------------------------------------
    _validate_actions(manifest, asset_by_path, report)

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
                      report: ValidationReport) -> None:
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

    semantics_bound: set[str] = set()
    action_by_id: dict[str, dict] = {}
    for action in actions:
        if not isinstance(action, dict):
            raise ValidationFailure(Diagnostic(
                ACT_E006_TIMING_LOOP_OR_BUDGET, Severity.ERROR, "actions",
                "petpack.actions.entry", {}))
        action_id = str(action.get("id", ""))
        action_by_id[action_id] = action
        semantic = action.get("semantic")
        if semantic is not None:
            semantic = str(semantic)
            if not semantic.startswith("core."):
                # character-specific: MUST be namespaced under a declared
                # character FQID; multiple actions MAY share one core
                # semantic (one per character is decided per-character)
                if not any(action_id.startswith(fq_prefix + cid + ".")
                           for cid in character_ids):
                    raise ValidationFailure(Diagnostic(
                        ACT_E003_INVALID_NAMESPACE, Severity.ERROR, "actions",
                        "petpack.actions.namespace", {}))
        _validate_action_timeline(action, asset_by_path)

    # every character must bind a verifiable core.idle (its own or shared)
    characters = manifest.get("characters", [])
    for character in characters:
        bound = character.get("actions", {})
        idle_ref = bound.get("core.idle") if isinstance(bound, dict) else None
        if not idle_ref:
            raise ValidationFailure(Diagnostic(
                ACT_E001_CORE_IDLE_MISSING, Severity.ERROR, "actions",
                "petpack.actions.idle_missing",
                {"character": str(character.get("id", ""))}))

    # -- rig sharing rules (PETPACK_SPEC 9 / 11.4) ----------------------------
    _validate_rig_sharing(manifest, characters, action_by_id)


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
                              asset_by_path: dict[str, dict]) -> None:
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
        if kind == "sequence":
            frames = renderer.get("frames", [])
            if not frames or len(frames) > MAX_FRAMES_PER_ACTION:
                raise ValidationFailure(Diagnostic(
                    RES_E004_FRAMES_OR_DECODE_MEMORY, Severity.ERROR,
                    "actions", "petpack.actions.frame_count", {}))
            for frame in frames:
                asset_id = str(frame.get("asset", ""))
                if not any(a.get("id") == asset_id
                           for a in asset_by_path.values()):
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
                     trust_channel: str, report: ValidationReport) -> None:
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
        if basis in _NEEDS_LICENSE_EVIDENCE:
            has_spdx = bool(license_.get("spdx"))
            has_file = bool(license_.get("legal_file_ref"))
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
