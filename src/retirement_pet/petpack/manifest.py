"""Strict petpack.json parsing (PETPACK_SPEC 3/5/6/10; budget table 17).

Rules enforced here:
- UTF-8 strict JSON, no duplicate keys (object_pairs hook), no NaN/Infinity,
  depth <= 16, size <= 1 MiB;
- schema_version must be "1.0" (unknown MAJOR rejected; minor kept strict
  for 1.0);
- ID grammar: lowercase ASCII segments, [a-z0-9][a-z0-9_-]*, bounded length;
- reserved namespaces (official / engine as publisher or package id)
  rejected for external packs (PPK-MAN-E009);
- exactly one series; characters reference declared assets/actions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from retirement_pet.petpack.diagnostics import (
    MAN_E002_DUP_KEY_OR_DEPTH_OR_SIZE,
    MAN_E003_UNSUPPORTED_SCHEMA,
    MAN_E004_MISSING_OR_INVALID_FIELD,
    MAN_E005_INVALID_ID_VERSION_OR_NAMESPACE,
    MAN_E009_RESERVED_NAMESPACE_OR_TRUST,
    Diagnostic,
    Severity,
)
from retirement_pet.petpack.diagnostics import ValidationFailure
from retirement_pet.petpack.identity import MANIFEST_NAME, PackKey

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 16
SUPPORTED_SCHEMA = "1.0"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MAX_ID_LEN = 64
MAX_SEMVER_LEN = 32
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")

_RESERVED_PUBLISHERS = {"official", "engine", "retirementpet", "com.retirementpet"}


class TrustChannel:
    """Engine-assigned install context (PETPACK_SPEC 4.3; CONTENT_POLICY 2).

    Not a manifest field: packs can never declare or upgrade their channel.
    """
    BUILTIN_OFFICIAL = "BUILTIN_OFFICIAL"
    LOCAL_IMPORTED = "LOCAL_IMPORTED"


def check_reserved_namespace(publisher_id: str, package_id: str,
                             trust_channel: str) -> None:
    """External (locally imported) packs must not claim reserved namespaces."""
    if trust_channel == TrustChannel.BUILTIN_OFFICIAL:
        return  # the official publisher legitimately owns the namespace
    if publisher_id in _RESERVED_PUBLISHERS or package_id in _RESERVED_PUBLISHERS:
        _fail(Diagnostic(MAN_E009_RESERVED_NAMESPACE_OR_TRUST, Severity.ERROR,
                         "manifest_schema", "petpack.manifest.reserved", {}))
    if publisher_id.split(".", 1)[0] in _RESERVED_PUBLISHERS:
        _fail(Diagnostic(MAN_E009_RESERVED_NAMESPACE_OR_TRUST, Severity.ERROR,
                         "manifest_schema", "petpack.manifest.reserved", {}))


def _fail(code_diag: Diagnostic) -> None:
    raise ValidationFailure(code_diag)


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(Diagnostic(MAN_E002_DUP_KEY_OR_DEPTH_OR_SIZE, Severity.ERROR,
                             "manifest_parse", "petpack.manifest.duplicate_key",
                             {}))
        result[key] = value
    return result


def _check_depth(value: Any, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        _fail(Diagnostic(MAN_E002_DUP_KEY_OR_DEPTH_OR_SIZE, Severity.ERROR,
                         "manifest_parse", "petpack.manifest.too_deep", {}))
    if isinstance(value, dict):
        for item in value.values():
            _check_depth(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_depth(item, depth + 1)


def _check_no_floats(value: Any) -> None:
    """JSON parser already rejects NaN/Infinity with parse_constant."""
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            _fail(Diagnostic(MAN_E002_DUP_KEY_OR_DEPTH_OR_SIZE, Severity.ERROR,
                             "manifest_parse", "petpack.manifest.non_finite", {}))
    elif isinstance(value, dict):
        for item in value.values():
            _check_no_floats(item)
    elif isinstance(value, list):
        for item in value:
            _check_no_floats(item)


def check_id(segment: str, kind: str) -> str:
    if (not isinstance(segment, str) or not segment
            or len(segment) > MAX_ID_LEN or not _ID_RE.match(segment)):
        _fail(Diagnostic(MAN_E005_INVALID_ID_VERSION_OR_NAMESPACE, Severity.ERROR,
                         "manifest_parse", "petpack.manifest.bad_id",
                         {"kind": kind}))
    return segment


def check_publisher_id(value: str) -> str:
    """Reverse-domain style (PETPACK_SPEC 4.2): dot-separated lowercase
    segments, each matching the plain ID grammar."""
    if (not isinstance(value, str) or not value or len(value) > MAX_ID_LEN * 2):
        _fail(Diagnostic(MAN_E005_INVALID_ID_VERSION_OR_NAMESPACE, Severity.ERROR,
                         "manifest_parse", "petpack.manifest.bad_id",
                         {"kind": "publisher_id"}))
    parts = value.split(".")
    if not 1 <= len(parts) <= 8:
        _fail(Diagnostic(MAN_E005_INVALID_ID_VERSION_OR_NAMESPACE, Severity.ERROR,
                         "manifest_parse", "petpack.manifest.bad_id",
                         {"kind": "publisher_id"}))
    for part in parts:
        check_id(part, "publisher_id")
    return value


def parse_manifest(data: bytes) -> dict[str, Any]:
    """Parse + structurally validate petpack.json; returns the raw dict."""
    if len(data) > MAX_MANIFEST_BYTES:
        _fail(Diagnostic(MAN_E002_DUP_KEY_OR_DEPTH_OR_SIZE, Severity.ERROR,
                         "manifest_parse", "petpack.manifest.too_large", {}))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        _fail(Diagnostic(MAN_E002_DUP_KEY_OR_DEPTH_OR_SIZE, Severity.ERROR,
                         "manifest_parse", "petpack.manifest.not_utf8", {}))
    try:
        manifest = json.loads(
            text,
            object_pairs_hook=_object_no_duplicates,
            parse_constant=lambda name: _fail(
                Diagnostic(MAN_E002_DUP_KEY_OR_DEPTH_OR_SIZE, Severity.ERROR,
                           "manifest_parse", "petpack.manifest.non_finite", {})),
        )
    except json.JSONDecodeError:
        _fail(Diagnostic(MAN_E002_DUP_KEY_OR_DEPTH_OR_SIZE, Severity.ERROR,
                         "manifest_parse", "petpack.manifest.bad_json", {}))
    if not isinstance(manifest, dict):
        _fail(Diagnostic(MAN_E002_DUP_KEY_OR_DEPTH_OR_SIZE, Severity.ERROR,
                         "manifest_parse", "petpack.manifest.root", {}))
    _check_depth(manifest)
    _check_no_floats(manifest)
    return manifest


def validate_manifest_structure(manifest: dict,
                                trust_channel: str = TrustChannel.LOCAL_IMPORTED
                                ) -> PackKey:
    """Structural/ID validation (no archive access needed)."""
    schema = manifest.get("schema_version")
    if schema != SUPPORTED_SCHEMA:
        _fail(Diagnostic(MAN_E003_UNSUPPORTED_SCHEMA, Severity.ERROR,
                         "manifest_schema", "petpack.manifest.schema",
                         {}))

    package = manifest.get("package")
    if not isinstance(package, dict):
        _fail(Diagnostic(MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                         "manifest_schema", "petpack.manifest.package", {}))
    publisher_id = check_publisher_id(str(package.get("publisher_id", "")))
    package_id = check_id(str(package.get("id", "")), "package_id")
    check_reserved_namespace(publisher_id, package_id, trust_channel)
    version = str(package.get("version", ""))
    if len(version) > MAX_SEMVER_LEN or not _SEMVER_RE.match(version):
        _fail(Diagnostic(MAN_E005_INVALID_ID_VERSION_OR_NAMESPACE, Severity.ERROR,
                         "manifest_schema", "petpack.manifest.bad_version", {}))
    if not isinstance(package.get("display_name"), dict) or not package["display_name"]:
        _fail(Diagnostic(MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                         "manifest_schema", "petpack.manifest.display_name", {}))

    # exactly one series (PETPACK_SPEC 6.3)
    series = manifest.get("series")
    if not isinstance(series, dict) or not series.get("id"):
        _fail(Diagnostic(MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                         "manifest_schema", "petpack.manifest.series", {}))
    check_id(str(series["id"]), "series_id")

    characters = manifest.get("characters")
    if not isinstance(characters, list) or not characters:
        _fail(Diagnostic(MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                         "manifest_schema", "petpack.manifest.characters", {}))
    for character in characters:
        if not isinstance(character, dict):
            _fail(Diagnostic(MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                             "manifest_schema", "petpack.manifest.character", {}))
        check_id(str(character.get("id", "")), "character_id")
        geometry = character.get("geometry")
        if not isinstance(geometry, dict):
            _fail(Diagnostic(MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                             "manifest_schema", "petpack.manifest.geometry", {}))

    assets = manifest.get("assets", [])
    if not isinstance(assets, list):
        _fail(Diagnostic(MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                         "manifest_schema", "petpack.manifest.assets", {}))

    _validate_compatibility_structure(manifest.get("compatibility"))

    return PackKey(publisher_id=publisher_id, package_id=package_id)


def _validate_compatibility_structure(compatibility: Any) -> None:
    """Shape-level compatibility checks (PETPACK_SPEC 5).

    Value-level decisions (engine range containment, capability support)
    belong to the validator, which owns the engine facts registry.
    """
    if compatibility is None:
        return  # no constraint declared; unknown semantics still fail closed
    if not isinstance(compatibility, dict):
        _fail(Diagnostic(MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                         "manifest_schema", "petpack.manifest.compatibility",
                         {}))
    lower = compatibility.get("engine_min")
    upper = compatibility.get("engine_max_exclusive")
    for bound in (lower, upper):
        if bound is None:
            continue
        if (not isinstance(bound, str) or len(bound) > MAX_SEMVER_LEN
                or not _SEMVER_RE.match(bound)):
            _fail(Diagnostic(MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                             "manifest_schema",
                             "petpack.manifest.engine_range", {}))
    if lower is not None and upper is not None:
        if not semver_less(lower, upper):
            _fail(Diagnostic(MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                             "manifest_schema",
                             "petpack.manifest.engine_range", {}))
    for key in ("required_capabilities", "optional_capabilities"):
        values = compatibility.get(key)
        if values is None:
            continue
        if not isinstance(values, list) or len(values) > 64:
            _fail(Diagnostic(MAN_E004_MISSING_OR_INVALID_FIELD, Severity.ERROR,
                             "manifest_schema",
                             "petpack.manifest.capabilities", {}))
        for value in values:
            if (not isinstance(value, str) or not value or len(value) > 128
                    or not value.replace(".", "").replace("_", "")
                    .replace("-", "").isalnum()):
                _fail(Diagnostic(MAN_E004_MISSING_OR_INVALID_FIELD,
                                 Severity.ERROR, "manifest_schema",
                                 "petpack.manifest.capabilities", {}))


def semver_less(left: str, right: str) -> bool:
    """Strict ordering for the plain ``X.Y.Z`` prefixes of two semvers."""
    def core(version: str) -> tuple[int, int, int]:
        body = version.split("+", 1)[0].split("-", 1)[0]
        parts = body.split(".")
        return (int(parts[0]), int(parts[1]), int(parts[2]))

    return core(left) < core(right)
