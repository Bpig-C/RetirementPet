"""Validated identity for a frozen RetirementPet artifact.

The release build embeds ``build-info.json`` beside the bundled resources.
Source runs deliberately return a small non-release descriptor instead of
claiming an artifact identity.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from retirement_pet import APP_NAME, __version__
from retirement_pet.resource_path import resource_path

BUILD_INFO_SCHEMA = 1
BUILD_INFO_FILENAME = "build-info.json"
BUILD_ATTESTATION_PROTOCOL = 2
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID_RE = re.compile(r"^[0-9a-f]{40}$")
_NON_IDENTITY_FIELDS = frozenset({"build_id", "built_at_utc"})


class BuildInfoError(ValueError):
    """The embedded artifact identity is missing or invalid."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def identity_payload(info: Mapping[str, Any]) -> dict[str, Any]:
    """Return the deterministic portion used to derive ``build_id``."""
    return {
        key: value for key, value in info.items()
        if key not in _NON_IDENTITY_FIELDS
    }


def compute_build_id(info: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(identity_payload(info))).hexdigest()


def validate_build_info(value: object) -> dict[str, Any]:
    """Validate and return a defensive copy of embedded build metadata."""
    if not isinstance(value, dict):
        raise BuildInfoError("build info must be an object")
    info = dict(value)
    if info.get("schema") != BUILD_INFO_SCHEMA:
        raise BuildInfoError("unsupported build info schema")
    if info.get("artifact") != APP_NAME or info.get("artifact_kind") != "onedir":
        raise BuildInfoError("unexpected artifact identity")
    if info.get("version") != __version__:
        raise BuildInfoError("artifact version does not match runtime")
    if not isinstance(info.get("commit"), str) \
            or not _GIT_OID_RE.fullmatch(info["commit"]):
        raise BuildInfoError("invalid build commit")
    if not isinstance(info.get("git_tree"), str) \
            or not _GIT_OID_RE.fullmatch(info["git_tree"]):
        raise BuildInfoError("invalid build tree")
    if info.get("source_clean") is not True:
        raise BuildInfoError("release source was not clean")
    inputs = info.get("inputs")
    if not isinstance(inputs, dict) \
            or not isinstance(inputs.get("inventory_sha256"), str) \
            or not _SHA256_RE.fullmatch(inputs["inventory_sha256"]):
        raise BuildInfoError("invalid build input inventory")
    files = inputs.get("files")
    if not isinstance(files, dict):
        raise BuildInfoError("build input files are missing")
    canonical_lines = []
    for name, digest in sorted(files.items()):
        if not isinstance(name, str) or not name or "\\" in name \
                or name.startswith("/") or ".." in Path(name).parts:
            raise BuildInfoError("invalid build input path")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise BuildInfoError("invalid build input digest")
        canonical_lines.append(f"{digest}  {name}\n")
    inventory_digest = hashlib.sha256(
        "".join(canonical_lines).encode("utf-8")).hexdigest()
    if inventory_digest != inputs["inventory_sha256"]:
        raise BuildInfoError("build input inventory digest does not match")
    toolchain = info.get("toolchain")
    expected_toolchain_fields = {
        "public_lock_sha256", "attestation_sha256", "python_version",
        "python_implementation", "python_architecture", "qt_version",
        "packages",
    }
    if not isinstance(toolchain, dict) \
            or set(toolchain) != expected_toolchain_fields \
            or not isinstance(toolchain.get("packages"), dict) \
            or not toolchain.get("packages") \
            or any(not isinstance(name, str) or not name
                   or not isinstance(version, str) or not version
                   for name, version in toolchain.get("packages", {}).items()) \
            or not isinstance(toolchain.get("public_lock_sha256"), str) \
            or not _SHA256_RE.fullmatch(toolchain["public_lock_sha256"]) \
            or not isinstance(toolchain.get("attestation_sha256"), str) \
            or not _SHA256_RE.fullmatch(toolchain["attestation_sha256"]) \
            or any(not isinstance(toolchain.get(field), str)
                   or not toolchain[field]
                   for field in (
                       "python_version", "python_implementation",
                       "python_architecture", "qt_version")):
        raise BuildInfoError("invalid build toolchain")
    packaging = info.get("packaging")
    if not isinstance(packaging, dict) \
            or packaging.get("mode") != "onedir" \
            or packaging.get("upx") is not False \
            or packaging.get("spec") != "RetirementPet.spec" \
            or packaging.get("source") != "git-archive":
        raise BuildInfoError("unsupported packaging configuration")
    built_at = info.get("built_at_utc")
    try:
        parsed_time = datetime.fromisoformat(str(built_at))
    except ValueError as exc:
        raise BuildInfoError("invalid build timestamp") from exc
    if parsed_time.tzinfo is None:
        raise BuildInfoError("build timestamp must include a timezone")
    build_id = info.get("build_id")
    if not isinstance(build_id, str) or not _SHA256_RE.fullmatch(build_id):
        raise BuildInfoError("invalid build id")
    if build_id != compute_build_id(info):
        raise BuildInfoError("build id does not match metadata")
    return info


def source_build_info() -> dict[str, Any]:
    """Honest descriptor for a source run (never a release identity)."""
    return {
        "schema": BUILD_INFO_SCHEMA,
        "artifact": APP_NAME,
        "artifact_kind": "source",
        "version": __version__,
        "release_identity": False,
    }


def load_build_info(*, require_embedded: bool | None = None) -> dict[str, Any]:
    """Load the embedded identity, failing closed for frozen executions."""
    if require_embedded is None:
        require_embedded = bool(getattr(sys, "frozen", False))
    if not require_embedded:
        return source_build_info()

    # The generated module is stored in PyInstaller's PYZ archive, while the
    # JSON copy is inventory-visible.  Requiring exact equality binds the
    # visible metadata to code packaged into this executable; replacing an
    # old EXE beside a new JSON file cannot acquire the new identity.
    try:
        generated = importlib.import_module("retirement_pet_build_info")
        compiled = validate_build_info(generated.BUILD_INFO)
    except (ImportError, AttributeError, BuildInfoError) as exc:
        raise BuildInfoError("compiled build identity is missing") from exc
    path = resource_path(BUILD_INFO_FILENAME)
    if not path.is_file():
        raise BuildInfoError("embedded build info is missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildInfoError("embedded build info is unreadable") from exc
    external = validate_build_info(raw)
    if external != compiled:
        raise BuildInfoError("compiled and external build identity differ")
    return compiled


def write_build_info(path: Path, info: Mapping[str, Any]) -> None:
    """Atomically write a checked descriptor for a diagnostic harness."""
    candidate = dict(info)
    if candidate.get("artifact_kind") == "source":
        if candidate != source_build_info():
            raise BuildInfoError("invalid source build descriptor")
        validated = candidate
    else:
        validated = validate_build_info(candidate)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_attestation() -> dict[str, Any]:
    """Return the v2 proof only code with compiled identity can produce."""
    return {
        "protocol": BUILD_ATTESTATION_PROTOCOL,
        "compiled_identity": load_build_info(require_embedded=True),
        "external_match": True,
    }


def validate_build_attestation(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
            "protocol", "compiled_identity", "external_match"}:
        raise BuildInfoError("invalid build attestation shape")
    if value.get("protocol") != BUILD_ATTESTATION_PROTOCOL \
            or value.get("external_match") is not True:
        raise BuildInfoError("unsupported build attestation")
    return {
        "protocol": BUILD_ATTESTATION_PROTOCOL,
        "compiled_identity": validate_build_info(value["compiled_identity"]),
        "external_match": True,
    }


def write_build_attestation(path: Path, value: object) -> None:
    validated = validate_build_attestation(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = [
    "BUILD_INFO_FILENAME",
    "BUILD_INFO_SCHEMA",
    "BUILD_ATTESTATION_PROTOCOL",
    "BuildInfoError",
    "compute_build_id",
    "build_attestation",
    "identity_payload",
    "load_build_info",
    "source_build_info",
    "validate_build_info",
    "validate_build_attestation",
    "write_build_attestation",
    "write_build_info",
]
