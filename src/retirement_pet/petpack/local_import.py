"""Strict GUI preflight for locally imported PetPacks.

The archive validator is the hostile-input boundary, but a desktop-pet import
also needs to prove that the currently implemented renderer can actually draw
every character's idle frame.  This module deliberately accepts a narrower
subset than the PetPack 1.0 schema: PNG body assets rendered by ``static`` or
``sequence`` loop profiles.  A future runtime may widen that allowlist only
after it can render the additional profile types.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import multiprocessing
import os
import re
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from retirement_pet.petpack.diagnostics import Diagnostic, Severity
from retirement_pet.petpack.identity import PackKey, RevisionKey
from retirement_pet.petpack.runtime import (
    MIN_FRAME_DURATION_MS,
    load_pack,
)
from retirement_pet.petpack.validator import ValidationReport, validate_petpack


# The PetPack container contract permits larger archives, but the interactive
# local-import path is intentionally narrower.  Checking this limit before and
# during the streaming snapshot prevents a sparse/oversized user file from
# being read wholesale into the GUI process.
MAX_LOCAL_IMPORT_BYTES = 64 * 1024 * 1024
MAX_LOCAL_UNCOMPRESSED_BYTES = 96 * 1024 * 1024
MAX_LOCAL_CHARACTERS = 16
MAX_LOCAL_BODY_PIXELS = 24 * 1024 * 1024
ISOLATED_IMPORT_CHUNK_BYTES = 1024 * 1024
# The first frame is one fixed-schema JSON document; subsequent frames are the
# exact archive bytes.  No Python object serialization crosses child -> GUI.
ISOLATED_IMPORT_PROTOCOL = "retirementpet.local-import"
ISOLATED_IMPORT_PROTOCOL_VERSION = 1
MAX_ISOLATED_IMPORT_METADATA_BYTES = 2 * 1024 * 1024
MAX_ISOLATED_IMPORT_TRANSFER_BYTES = (
    MAX_LOCAL_IMPORT_BYTES + MAX_ISOLATED_IMPORT_METADATA_BYTES
)
DEFAULT_ISOLATED_IMPORT_TIMEOUT_SECONDS = 60.0

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PUBLISHER_RE = re.compile(
    r"^[a-z0-9][a-z0-9_-]{0,63}(?:\.[a-z0-9][a-z0-9_-]{0,63})*$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")


class LocalImportError(ValueError):
    """A local pack is valid ZIP/JSON but unsafe or unusable in this build."""

    def __init__(self, message: str):
        super().__init__(message)
        self.child_reaped: bool | None = None
        self.child_exit_code: int | None = None


@dataclass(frozen=True)
class LocalImportPreview:
    revision_key: RevisionKey
    character_ids: tuple[str, ...]
    package_name: str
    publisher_id: str
    archive_sha256: str
    archive_size: int
    action_semantics: tuple[str, ...]
    rights_bases: tuple[str, ...]
    warning_codes: tuple[str, ...]
    payload: bytes = field(repr=False)
    validation_report: ValidationReport = field(repr=False)
    first_frames_verified: bool = False
    # These fields exist only inside the disposable inspector.  They are never
    # serialized into child -> GUI metadata, so untrusted PNG bytes cross the
    # boundary only as part of the archive snapshot and are never decoded by
    # the GUI process.
    idle_first_assets: tuple[tuple[str, str], ...] = field(
        default=(), repr=False)
    idle_asset_payloads: tuple[tuple[str, bytes], ...] = field(
        default=(), repr=False)


@dataclass(frozen=True)
class IsolatedImportEnvelope:
    """Strictly parsed metadata for one pending raw archive transfer."""

    payload_size: int
    payload_sha256: str
    preview_metadata: dict[str, Any] = field(repr=False)


def _cancel_if_requested(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise LocalImportError("导入已取消")


def _read_bounded_snapshot(
        pack_path: Path,
        cancelled: Callable[[], bool] | None = None) -> bytes:
    path = Path(pack_path)
    if path.suffix.lower() != ".petpack" or not path.is_file():
        raise LocalImportError("请选择存在的 .petpack 文件")
    try:
        declared_size = path.stat().st_size
    except OSError as exc:
        raise LocalImportError("无法检查角色包大小") from exc
    if declared_size > MAX_LOCAL_IMPORT_BYTES:
        raise LocalImportError("角色包超过本地导入 64 MiB 上限")
    chunks: list[bytes] = []
    total = 0
    try:
        with path.open("rb") as handle:
            while True:
                _cancel_if_requested(cancelled)
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_LOCAL_IMPORT_BYTES:
                    raise LocalImportError("角色包超过本地导入 64 MiB 上限")
                chunks.append(chunk)
    except LocalImportError:
        raise
    except OSError as exc:
        raise LocalImportError("无法读取角色包") from exc
    return b"".join(chunks)


def _check_local_uncompressed_budget(data: bytes) -> None:
    """Reject locally excessive expansion before the validator materializes it."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            total = sum(
                info.file_size for info in archive.infolist()
                if not info.is_dir())
    except (zipfile.BadZipFile, OSError) as exc:
        raise LocalImportError("角色包不是完整的 ZIP 归档") from exc
    if total > MAX_LOCAL_UNCOMPRESSED_BYTES:
        raise LocalImportError("角色包解压内容超过本地导入 96 MiB 上限")


def _unique_by_id(items: list, kind: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            raise LocalImportError(f"{kind} 条目格式无效")
        item_id = str(item.get("id", ""))
        if not item_id or item_id in result:
            raise LocalImportError(f"{kind} ID 缺失或重复：{item_id or '<empty>'}")
        result[item_id] = item
    return result


def _renderer_asset_ids(action: dict) -> tuple[str, ...]:
    lifecycle = action.get("lifecycle")
    if not isinstance(lifecycle, dict):
        raise LocalImportError(f"动作 {action.get('id')} 缺少 lifecycle")
    loop = lifecycle.get("loop")
    if not isinstance(loop, dict):
        raise LocalImportError(f"动作 {action.get('id')} 当前必须提供 loop")
    renderer = loop.get("renderer")
    if not isinstance(renderer, dict):
        raise LocalImportError(f"动作 {action.get('id')} 缺少 renderer")
    kind = renderer.get("type")
    if kind == "static":
        asset_id = str(renderer.get("asset", ""))
        if not asset_id:
            raise LocalImportError(f"动作 {action.get('id')} 缺少静态素材")
        return (asset_id,)
    if kind == "sequence":
        frames = renderer.get("frames")
        if not isinstance(frames, list) or not frames:
            raise LocalImportError(f"动作 {action.get('id')} 的序列为空")
        result = []
        for frame in frames:
            if not isinstance(frame, dict):
                raise LocalImportError(f"动作 {action.get('id')} 含无效帧")
            asset_id = str(frame.get("asset", ""))
            duration = frame.get("duration_ms")
            if not asset_id or type(duration) is not int \
                    or duration < MIN_FRAME_DURATION_MS:
                raise LocalImportError(
                    f"动作 {action.get('id')} 含无效素材或过短帧")
            result.append(asset_id)
        return tuple(result)
    raise LocalImportError(
        f"动作 {action.get('id')} 使用当前不支持的渲染器：{kind}")


def _decode_png(archive, asset: dict, *, require_transparency: bool) -> QImage:
    if asset.get("media_type") != "image/png":
        raise LocalImportError(f"身体素材必须是 PNG：{asset.get('id')}")
    try:
        image = QImage.fromData(archive.read(str(asset.get("path", ""))), "PNG")
    except Exception as exc:  # noqa: BLE001 - archive decoder boundary
        raise LocalImportError(f"无法读取素材：{asset.get('id')}") from exc
    if image.isNull():
        raise LocalImportError(f"无法解码素材：{asset.get('id')}")
    properties = asset.get("properties")
    if not isinstance(properties, dict) \
            or properties.get("width") != image.width() \
            or properties.get("height") != image.height():
        raise LocalImportError(f"素材尺寸声明不一致：{asset.get('id')}")
    if not require_transparency:
        return image
    if not image.hasAlphaChannel():
        raise LocalImportError(f"身体素材没有 Alpha 通道：{asset.get('id')}")
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    raw = bytes(rgba.constBits())
    alpha = raw[3::4]
    if not alpha or max(alpha) == 0:
        raise LocalImportError(f"身体素材完全透明：{asset.get('id')}")
    if min(alpha) == 255:
        raise LocalImportError(f"身体素材没有透明背景：{asset.get('id')}")
    corners = (
        rgba.pixelColor(0, 0).alpha(),
        rgba.pixelColor(rgba.width() - 1, 0).alpha(),
        rgba.pixelColor(0, rgba.height() - 1).alpha(),
        rgba.pixelColor(rgba.width() - 1, rgba.height() - 1).alpha(),
    )
    if any(value > 8 for value in corners):
        raise LocalImportError(f"身体素材四角必须透明：{asset.get('id')}")
    visible_pixels = sum(value > 8 for value in alpha)
    if visible_pixels < max(64, image.width() * image.height() // 1000):
        raise LocalImportError(f"身体素材几乎完全透明：{asset.get('id')}")
    return image


def inspect_local_pack(
        pack_path: Path,
        *,
        cancelled: Callable[[], bool] | None = None) -> LocalImportPreview:
    """Take one bounded byte snapshot inside the disposable inspector.

    This low-level function decodes QImage data and must only be called by the
    child target (or focused tests).  Product callers use
    :func:`preflight_local_pack`, which never decodes pack images in its parent.
    """
    data = _read_bounded_snapshot(Path(pack_path), cancelled)
    _check_local_uncompressed_budget(data)
    _cancel_if_requested(cancelled)
    report = validate_petpack(data, trust_channel="LOCAL_IMPORTED")
    _cancel_if_requested(cancelled)
    if not report.accepted:
        codes = ", ".join(d.code for d in report.diagnostics) or "unknown"
        raise LocalImportError(f"角色包协议验证失败：{codes}")

    archive, manifest = load_pack(data)
    assets = _unique_by_id(list(manifest.get("assets", [])), "asset")
    actions = _unique_by_id(list(manifest.get("actions", [])), "action")
    characters = _unique_by_id(list(manifest.get("characters", [])), "character")
    if len(characters) > MAX_LOCAL_CHARACTERS:
        raise LocalImportError("本地角色包最多包含 16 个角色")
    body_asset_ids: set[str] = set()
    idle_first_assets: list[tuple[str, str]] = []

    for character_id, character in characters.items():
        _cancel_if_requested(cancelled)
        thumbnail = str(character.get("thumbnail_asset", ""))
        if thumbnail not in assets:
            raise LocalImportError(f"角色 {character_id} 缺少缩略图")
        _decode_png(archive, assets[thumbnail], require_transparency=False)

        bindings = character.get("actions")
        if not isinstance(bindings, dict) or "core.idle" not in bindings:
            raise LocalImportError(f"角色 {character_id} 缺少 core.idle")
        idle_action_id = str(bindings["core.idle"])
        idle_action = actions.get(idle_action_id)
        if idle_action is None:
            raise LocalImportError(
                f"角色 {character_id} 引用了不存在的动作 {idle_action_id}")
        idle_assets = _renderer_asset_ids(idle_action)
        if not idle_assets or idle_assets[0] not in assets:
            raise LocalImportError(f"角色 {character_id} 缺少可绘制的 idle 首帧")
        idle_first_assets.append((character_id, idle_assets[0]))
        for action_id in bindings.values():
            action_id = str(action_id)
            action = actions.get(action_id)
            if action is None:
                raise LocalImportError(
                    f"角色 {character_id} 引用了不存在的动作 {action_id}")
            for asset_id in _renderer_asset_ids(action):
                if asset_id not in assets:
                    raise LocalImportError(
                        f"动作 {action_id} 引用了不存在的素材 {asset_id}")
                body_asset_ids.add(asset_id)

    body_pixels = 0
    for asset_id in sorted(body_asset_ids):
        _cancel_if_requested(cancelled)
        image = _decode_png(
            archive, assets[asset_id], require_transparency=True)
        body_pixels += image.width() * image.height()
        if body_pixels > MAX_LOCAL_BODY_PIXELS:
            raise LocalImportError("角色主体解码预算超过 24 百万像素上限")

    package = manifest.get("package", {})
    display = package.get("display_name", {})
    package_name = (display.get("zh-CN") if isinstance(display, dict) else None) \
        or str(package.get("id", "角色包"))
    warning_codes = tuple(sorted({
        diagnostic.code for diagnostic in report.diagnostics
        if getattr(diagnostic.severity, "value", diagnostic.severity)
        == "WARNING"
    }))
    rights_bases = tuple(sorted({
        str(item.get("basis", "unknown"))
        for item in manifest.get("rights_declarations", [])
        if isinstance(item, dict)
    }))
    return LocalImportPreview(
        revision_key=report.revision_key,
        character_ids=tuple(characters),
        package_name=str(package_name),
        publisher_id=str(package.get("publisher_id", "")),
        archive_sha256=hashlib.sha256(data).hexdigest(),
        archive_size=len(data),
        action_semantics=tuple(sorted({
            str(action.get("semantic", "")) for action in actions.values()
            if action.get("semantic")
        })),
        rights_bases=rights_bases,
        warning_codes=warning_codes,
        payload=data,
        validation_report=report,
        idle_first_assets=tuple(idle_first_assets),
        idle_asset_payloads=tuple(
            (asset_id, archive.read(str(assets[asset_id].get("path", ""))))
            for asset_id in sorted({
                asset_id for _, asset_id in idle_first_assets
            })
        ),
    )


def validate_local_pack_first_frames(preview: LocalImportPreview) -> None:
    """Exercise QPixmap inside the child; product parents must never call it."""
    if QApplication.instance() is None:
        raise LocalImportError("角色首帧检查需要图形应用环境")
    if hashlib.sha256(preview.payload).hexdigest() != preview.archive_sha256:
        raise LocalImportError("角色包预检快照完整性检查失败")
    payloads = dict(preview.idle_asset_payloads)
    bindings = dict(preview.idle_first_assets)
    if tuple(bindings) != preview.character_ids:
        raise LocalImportError("角色包 idle 首帧绑定不完整")
    for character_id in preview.character_ids:
        data = payloads.get(bindings.get(character_id, ""))
        pixmap = QPixmap()
        try:
            decoded = bool(data) and pixmap.loadFromData(data, "PNG")
        except Exception:  # noqa: BLE001 - Qt decoder boundary
            decoded = False
        if not decoded or pixmap.isNull():
            raise LocalImportError(f"角色 {character_id} 无法绘制 idle 首帧")


def _diagnostic_to_json(diagnostic: Diagnostic) -> dict[str, Any]:
    return {
        "code": diagnostic.code,
        "severity": diagnostic.severity.value,
        "phase": diagnostic.phase,
        "message_key": diagnostic.message_key,
        "params": diagnostic.params,
        "recoverable": diagnostic.recoverable,
        "user_action": diagnostic.user_action,
        "cause_code": diagnostic.cause_code,
    }


def _revision_to_json(revision: RevisionKey) -> dict[str, str]:
    return {
        "publisher_id": revision.pack.publisher_id,
        "package_id": revision.pack.package_id,
        "package_version": revision.package_version,
        "content_digest": revision.content_digest,
    }


def _preview_metadata(preview: LocalImportPreview) -> dict[str, Any]:
    report = preview.validation_report
    if report.pack_key is None or report.revision_key is None:
        raise LocalImportError("角色包预检结果缺少版本身份")
    return {
        "revision": _revision_to_json(preview.revision_key),
        "character_ids": list(preview.character_ids),
        "package_name": preview.package_name,
        "publisher_id": preview.publisher_id,
        "archive_sha256": preview.archive_sha256,
        "archive_size": preview.archive_size,
        "action_semantics": list(preview.action_semantics),
        "rights_bases": list(preview.rights_bases),
        "warning_codes": list(preview.warning_codes),
        "first_frames_verified": preview.first_frames_verified,
        "validation_report": {
            "archive_sha256": report.archive_sha256,
            "trust_channel": report.trust_channel,
            "accepted": report.accepted,
            "pack_key": {
                "publisher_id": report.pack_key.publisher_id,
                "package_id": report.pack_key.package_id,
            },
            "revision": _revision_to_json(report.revision_key),
            "content_digest": report.content_digest,
            "manifest": report.manifest,
            "diagnostics": [
                _diagnostic_to_json(item) for item in report.diagnostics
            ],
            "degraded": list(report.degraded),
        },
    }


def _json_frame(value: dict[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LocalImportError("角色包预检元数据无法安全编码") from exc
    if not encoded or len(encoded) > MAX_ISOLATED_IMPORT_METADATA_BYTES:
        raise LocalImportError("角色包预检元数据超过安全传输上限")
    return encoded


def _success_frame(preview: LocalImportPreview) -> bytes:
    return _json_frame({
        "protocol": ISOLATED_IMPORT_PROTOCOL,
        "version": ISOLATED_IMPORT_PROTOCOL_VERSION,
        "status": "ok",
        "payload": {
            "size": len(preview.payload),
            "sha256": preview.archive_sha256,
        },
        "preview": _preview_metadata(preview),
    })


def _error_frame(message: str) -> bytes:
    safe = "".join(
        char for char in str(message)[:256]
        if ord(char) >= 32 and ord(char) != 127
    ) or "无法安全检查角色包"
    return _json_frame({
        "protocol": ISOLATED_IMPORT_PROTOCOL,
        "version": ISOLATED_IMPORT_PROTOCOL_VERSION,
        "status": "error",
        "error": safe,
    })


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _exact_keys(value: object, expected: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"invalid {label}")
    return value


def _safe_string(
        value: object, label: str, *, maximum: int = 256,
        allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty) \
            or len(value) > maximum \
            or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"invalid {label}")
    return value


def _sha256(value: object, label: str) -> str:
    text = _safe_string(value, label, maximum=64)
    if _SHA256_RE.fullmatch(text) is None:
        raise ValueError(f"invalid {label}")
    return text


def _string_list(
        value: object, label: str, *, maximum_items: int,
        maximum_length: int = 256) -> tuple[str, ...]:
    if type(value) is not list or len(value) > maximum_items:
        raise ValueError(f"invalid {label}")
    result = tuple(
        _safe_string(item, label, maximum=maximum_length)
        for item in value
    )
    if len(set(result)) != len(result):
        raise ValueError(f"duplicate {label}")
    return result


def _safe_json_value(value: object, *, depth: int = 0) -> int:
    """Validate plain JSON values and return their bounded node count."""
    if depth > 32:
        raise ValueError("JSON nesting too deep")
    if value is None or type(value) in (bool, int):
        return 1
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return 1
    if isinstance(value, str):
        _safe_string(value, "JSON string", maximum=1024 * 1024,
                     allow_empty=True)
        return 1
    if type(value) is list:
        count = 1
        for item in value:
            count += _safe_json_value(item, depth=depth + 1)
            if count > 100_000:
                raise ValueError("JSON node budget exceeded")
        return count
    if type(value) is dict:
        count = 1
        for key, item in value.items():
            _safe_string(key, "JSON key", maximum=256, allow_empty=True)
            count += _safe_json_value(item, depth=depth + 1)
            if count > 100_000:
                raise ValueError("JSON node budget exceeded")
        return count
    raise ValueError("non-JSON value")


def _parse_revision(value: object, label: str) -> RevisionKey:
    item = _exact_keys(value, {
        "publisher_id", "package_id", "package_version", "content_digest",
    }, label)
    publisher_id = _safe_string(
        item["publisher_id"], "publisher id", maximum=129)
    package_id = _safe_string(item["package_id"], "package id", maximum=64)
    package_version = _safe_string(
        item["package_version"], "package version", maximum=32)
    if _PUBLISHER_RE.fullmatch(publisher_id) is None \
            or _ID_RE.fullmatch(package_id) is None \
            or _SEMVER_RE.fullmatch(package_version) is None:
        raise ValueError("invalid revision identity")
    return RevisionKey(
        pack=PackKey(publisher_id, package_id),
        package_version=package_version,
        content_digest=_sha256(item["content_digest"], "content digest"),
    )


def _parse_diagnostic(value: object) -> Diagnostic:
    item = _exact_keys(value, {
        "code", "severity", "phase", "message_key", "params",
        "recoverable", "user_action", "cause_code",
    }, "diagnostic")
    severity = _safe_string(item["severity"], "diagnostic severity", maximum=7)
    if severity not in {member.value for member in Severity}:
        raise ValueError("invalid diagnostic severity")
    if type(item["params"]) is not dict:
        raise ValueError("invalid diagnostic params")
    _safe_json_value(item["params"])
    if type(item["recoverable"]) is not bool:
        raise ValueError("invalid diagnostic recovery flag")
    optional = {}
    for name in ("user_action", "cause_code"):
        raw = item[name]
        optional[name] = None if raw is None else _safe_string(
            raw, name, maximum=128)
    return Diagnostic(
        code=_safe_string(item["code"], "diagnostic code", maximum=64),
        severity=Severity(severity),
        phase=_safe_string(item["phase"], "diagnostic phase", maximum=64),
        message_key=_safe_string(
            item["message_key"], "message key", maximum=128),
        params=item["params"],
        recoverable=item["recoverable"],
        user_action=optional["user_action"],
        cause_code=optional["cause_code"],
    )


def parse_isolated_import_metadata(
        frame: bytes) -> IsolatedImportEnvelope | str:
    """Parse one child frame without ever invoking Python deserialization."""
    if not isinstance(frame, bytes) or not frame \
            or len(frame) > MAX_ISOLATED_IMPORT_METADATA_BYTES:
        raise ValueError("invalid isolated-import metadata length")
    try:
        value = json.loads(
            frame.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite JSON number")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError,
            RecursionError) as exc:
        raise ValueError("invalid isolated-import JSON") from exc
    if type(value) is not dict:
        raise ValueError("invalid isolated-import metadata")
    if type(value.get("protocol")) is not str \
            or value.get("protocol") != ISOLATED_IMPORT_PROTOCOL \
            or type(value.get("version")) is not int \
            or value.get("version") != ISOLATED_IMPORT_PROTOCOL_VERSION:
        raise ValueError("unsupported isolated-import protocol")
    if value.get("status") == "error":
        item = _exact_keys(
            value, {"protocol", "version", "status", "error"},
            "error metadata")
        return _safe_string(item["error"], "error", maximum=256)
    item = _exact_keys(
        value, {"protocol", "version", "status", "payload", "preview"},
        "success metadata")
    if item["status"] != "ok":
        raise ValueError("invalid isolated-import status")
    payload = _exact_keys(item["payload"], {"size", "sha256"}, "payload")
    size = payload["size"]
    if type(size) is not int or not (0 < size <= MAX_LOCAL_IMPORT_BYTES):
        raise ValueError("invalid isolated-import payload size")
    digest = _sha256(payload["sha256"], "payload digest")
    if size + len(frame) > MAX_ISOLATED_IMPORT_TRANSFER_BYTES:
        raise ValueError("isolated-import transfer budget exceeded")
    if type(item["preview"]) is not dict:
        raise ValueError("invalid isolated-import preview")
    return IsolatedImportEnvelope(size, digest, item["preview"])


def preview_from_isolated_transfer(
        envelope: IsolatedImportEnvelope, payload: bytes) -> LocalImportPreview:
    """Construct owned domain objects after strict length/hash/type checks."""
    if type(payload) is not bytes or len(payload) != envelope.payload_size \
            or hashlib.sha256(payload).hexdigest() != envelope.payload_sha256:
        raise ValueError("isolated-import payload binding mismatch")
    item = _exact_keys(envelope.preview_metadata, {
        "revision", "character_ids", "package_name", "publisher_id",
        "archive_sha256", "archive_size", "action_semantics",
        "rights_bases", "warning_codes", "first_frames_verified",
        "validation_report",
    }, "preview")
    revision = _parse_revision(item["revision"], "preview revision")
    character_ids = _string_list(
        item["character_ids"], "character id", maximum_items=MAX_LOCAL_CHARACTERS,
        maximum_length=64)
    if not character_ids or any(
            _ID_RE.fullmatch(value) is None for value in character_ids):
        raise ValueError("invalid character identifiers")
    archive_size = item["archive_size"]
    if type(archive_size) is not int or archive_size != len(payload):
        raise ValueError("invalid preview archive size")
    archive_sha256 = _sha256(item["archive_sha256"], "archive digest")
    if archive_sha256 != envelope.payload_sha256:
        raise ValueError("preview archive digest mismatch")
    if item["first_frames_verified"] is not True:
        raise ValueError("idle first-frame verification missing")

    report_item = _exact_keys(item["validation_report"], {
        "archive_sha256", "trust_channel", "accepted", "pack_key",
        "revision", "content_digest", "manifest", "diagnostics", "degraded",
    }, "validation report")
    pack_item = _exact_keys(
        report_item["pack_key"], {"publisher_id", "package_id"}, "pack key")
    report_pack = PackKey(
        _safe_string(pack_item["publisher_id"], "publisher id", maximum=129),
        _safe_string(pack_item["package_id"], "package id", maximum=64),
    )
    report_revision = _parse_revision(
        report_item["revision"], "report revision")
    if report_item["accepted"] is not True \
            or report_item["trust_channel"] != "LOCAL_IMPORTED" \
            or _sha256(report_item["archive_sha256"], "report archive digest") \
            != archive_sha256 \
            or report_revision != revision \
            or report_pack != revision.pack \
            or _sha256(report_item["content_digest"], "report content digest") \
            != revision.content_digest:
        raise ValueError("validation report binding mismatch")
    manifest = report_item["manifest"]
    if type(manifest) is not dict:
        raise ValueError("invalid validation manifest")
    _safe_json_value(manifest)
    diagnostics_raw = report_item["diagnostics"]
    if type(diagnostics_raw) is not list or len(diagnostics_raw) > 256:
        raise ValueError("invalid validation diagnostics")
    diagnostics = [_parse_diagnostic(value) for value in diagnostics_raw]
    if any(item.severity is Severity.ERROR for item in diagnostics):
        raise ValueError("accepted report contains an error diagnostic")
    degraded = _string_list(
        report_item["degraded"], "degradation", maximum_items=256,
        maximum_length=128)
    warning_codes = _string_list(
        item["warning_codes"], "warning code", maximum_items=256,
        maximum_length=64)
    expected_warnings = tuple(sorted({
        diagnostic.code for diagnostic in diagnostics
        if diagnostic.severity is Severity.WARNING
    }))
    if warning_codes != expected_warnings:
        raise ValueError("warning acknowledgement binding mismatch")

    package = manifest.get("package")
    if type(package) is not dict \
            or package.get("publisher_id") != revision.pack.publisher_id \
            or package.get("id") != revision.pack.package_id \
            or package.get("version") != revision.package_version:
        raise ValueError("manifest package binding mismatch")
    manifest_characters = manifest.get("characters")
    if type(manifest_characters) is not list \
            or not all(type(value) is dict for value in manifest_characters) \
            or tuple(value.get("id") for value in manifest_characters) \
            != character_ids:
        raise ValueError("manifest character binding mismatch")
    manifest_actions = manifest.get("actions")
    if type(manifest_actions) is not list \
            or not all(type(value) is dict for value in manifest_actions):
        raise ValueError("invalid manifest actions")
    action_semantics = _string_list(
        item["action_semantics"], "action semantic", maximum_items=1024,
        maximum_length=128)
    expected_semantics = tuple(sorted({
        str(action.get("semantic", "")) for action in manifest_actions
        if action.get("semantic")
    }))
    if action_semantics != expected_semantics:
        raise ValueError("manifest action binding mismatch")
    rights_declarations = manifest.get("rights_declarations")
    if type(rights_declarations) is not list \
            or not all(type(value) is dict for value in rights_declarations):
        raise ValueError("invalid manifest rights declarations")
    rights_bases = _string_list(
        item["rights_bases"], "rights basis", maximum_items=256,
        maximum_length=128)
    expected_rights = tuple(sorted({
        str(value.get("basis", "unknown"))
        for value in rights_declarations
    }))
    if rights_bases != expected_rights:
        raise ValueError("manifest rights binding mismatch")

    publisher_id = _safe_string(
        item["publisher_id"], "publisher id", maximum=129)
    if publisher_id != revision.pack.publisher_id:
        raise ValueError("preview publisher binding mismatch")
    package_name = _safe_string(
        item["package_name"], "package name", maximum=256)
    display = package.get("display_name", {})
    expected_name = (
        display.get("zh-CN") if type(display) is dict else None
    ) or str(package.get("id", "角色包"))
    if package_name != expected_name:
        raise ValueError("preview package-name binding mismatch")
    report = ValidationReport(
        archive_sha256=archive_sha256,
        trust_channel="LOCAL_IMPORTED",
        accepted=True,
        pack_key=report_pack,
        revision_key=report_revision,
        content_digest=revision.content_digest,
        manifest=manifest,
        diagnostics=diagnostics,
        degraded=list(degraded),
    )
    return LocalImportPreview(
        revision_key=revision,
        character_ids=character_ids,
        package_name=package_name,
        publisher_id=publisher_id,
        archive_sha256=archive_sha256,
        archive_size=archive_size,
        action_semantics=action_semantics,
        rights_bases=rights_bases,
        warning_codes=warning_codes,
        payload=payload,
        validation_report=report,
        first_frames_verified=True,
    )


def isolated_import_process_entry(pack_path: str, send_connection) -> None:
    """Top-level ``spawn`` target for hostile local-pack inspection.

    Child -> GUI uses only ``send_bytes``.  The first frame is strict JSON and
    every later frame is a raw chunk of the immutable archive snapshot.
    """
    try:
        # QPixmap requires a GUI application, but an imported PNG decoder must
        # remain killable.  The parent never creates a pixmap from pack bytes.
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        qt_app = QApplication.instance()
        if qt_app is None:
            qt_app = QApplication([
                "RetirementPet-local-import", "-platform", "offscreen",
            ])
        preview = inspect_local_pack(Path(pack_path))
        validate_local_pack_first_frames(preview)
        preview = replace(preview, first_frames_verified=True)
        send_connection.send_bytes(_success_frame(preview))
        for offset in range(0, len(preview.payload), ISOLATED_IMPORT_CHUNK_BYTES):
            send_connection.send_bytes(
                preview.payload[offset:offset + ISOLATED_IMPORT_CHUNK_BYTES])
    except LocalImportError as exc:
        try:
            send_connection.send_bytes(_error_frame(str(exc)))
        except (BrokenPipeError, EOFError, OSError):
            pass
    except Exception:  # noqa: BLE001 - untrusted archive/process boundary
        try:
            send_connection.send_bytes(_error_frame("无法安全检查角色包"))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        try:
            send_connection.close()
        except OSError:
            pass


def _stop_spawned_process(process) -> bool:
    try:
        process.join(timeout=0.1)
    except (AssertionError, OSError, ValueError):
        pass
    if process.is_alive():
        try:
            process.terminate()
        except (AttributeError, OSError):
            pass
        try:
            process.join(timeout=0.3)
        except (AssertionError, OSError, ValueError):
            pass
    if process.is_alive() and hasattr(process, "kill"):
        try:
            process.kill()
        except (AttributeError, OSError):
            pass
        try:
            process.join(timeout=0.3)
        except (AssertionError, OSError, ValueError):
            pass
    try:
        return not process.is_alive()
    except (AssertionError, OSError, ValueError):
        return False


def _has_extra_pipe_frame(connection) -> bool:
    """Distinguish a queued frame from normal Windows named-pipe EOF."""
    try:
        if not connection.poll():
            return False
        try:
            connection.recv_bytes(maxlength=1)
        except (EOFError, BrokenPipeError):
            return False
        except OSError:
            # A real frame larger than one byte triggers the length guard.
            return True
        return True
    except (BrokenPipeError, OSError, ValueError):
        return False


def preflight_local_pack(
        pack_path: Path, *,
        timeout_seconds: float = DEFAULT_ISOLATED_IMPORT_TIMEOUT_SECONDS,
        _context=None, _target=None) -> LocalImportPreview:
    """Run the entire PNG/Qt preflight in one timeout-bounded child."""
    try:
        timeout_value = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise LocalImportError("角色包检查超时设置无效") from exc
    if not math.isfinite(timeout_value) or not 0 < timeout_value <= 60.0:
        raise LocalImportError("角色包检查超时设置无效")
    context = _context or multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    target = _target or isolated_import_process_entry
    process = context.Process(
        target=target,
        args=(str(Path(pack_path)), send_connection),
        name="RetirementPet-local-import-sync",
        daemon=True,
    )
    deadline = time.monotonic() + timeout_value
    started = False
    result = None
    failure: LocalImportError | None = None
    child_reaped = True
    child_exit_code = None
    try:
        process.start()
        started = True
        send_connection.close()

        remaining = deadline - time.monotonic()
        if remaining <= 0 or not receive_connection.poll(remaining):
            raise LocalImportError("角色包检查超时，已安全终止")
        try:
            frame = receive_connection.recv_bytes(
                maxlength=MAX_ISOLATED_IMPORT_METADATA_BYTES)
        except (EOFError, OSError) as exc:
            raise LocalImportError("角色包检查进程异常退出") from exc
        try:
            parsed = parse_isolated_import_metadata(frame)
        except ValueError as exc:
            raise LocalImportError(
                "角色包检查进程返回了无效数据") from exc
        if isinstance(parsed, str):
            raise LocalImportError(parsed)

        buffer = bytearray()
        while len(buffer) < parsed.payload_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not receive_connection.poll(remaining):
                raise LocalImportError("角色包检查超时，已安全终止")
            try:
                chunk = receive_connection.recv_bytes(
                    maxlength=ISOLATED_IMPORT_CHUNK_BYTES)
            except (EOFError, OSError) as exc:
                raise LocalImportError("角色包检查进程异常退出") from exc
            bytes_remaining = parsed.payload_size - len(buffer)
            if not chunk or len(chunk) > bytes_remaining:
                raise LocalImportError("角色包检查进程返回了无效数据")
            buffer.extend(chunk)

        process.join(timeout=max(0.0, deadline - time.monotonic()))
        if process.is_alive():
            raise LocalImportError("角色包检查超时，已安全终止")
        if process.exitcode != 0 or _has_extra_pipe_frame(receive_connection):
            raise LocalImportError("角色包检查进程异常退出")
        try:
            result = preview_from_isolated_transfer(parsed, bytes(buffer))
        except ValueError as exc:
            raise LocalImportError("角色包检查进程返回了无效数据") from exc
    except LocalImportError as exc:
        failure = exc
    except Exception as exc:  # noqa: BLE001 - process-launch boundary
        failure = LocalImportError("无法启动安全检查进程")
        failure.__cause__ = exc
    finally:
        try:
            send_connection.close()
        except OSError:
            pass
        if started:
            child_reaped = _stop_spawned_process(process)
            if child_reaped:
                child_exit_code = process.exitcode
                try:
                    process.close()
                except (AttributeError, OSError, ValueError):
                    pass
        try:
            receive_connection.close()
        except OSError:
            pass
    if not child_reaped and failure is None:
        failure = LocalImportError("角色包检查进程无法安全终止")
    if failure is not None:
        failure.child_reaped = child_reaped
        failure.child_exit_code = child_exit_code
        raise failure
    if result is None:
        failure = LocalImportError("角色包检查进程返回了无效数据")
        failure.child_reaped = child_reaped
        failure.child_exit_code = child_exit_code
        raise failure
    return result


__all__ = [
    "LocalImportError", "LocalImportPreview", "MAX_LOCAL_IMPORT_BYTES",
    "MAX_LOCAL_UNCOMPRESSED_BYTES",
    "MAX_LOCAL_BODY_PIXELS",
    "ISOLATED_IMPORT_CHUNK_BYTES", "MAX_ISOLATED_IMPORT_METADATA_BYTES",
    "MAX_ISOLATED_IMPORT_TRANSFER_BYTES", "ISOLATED_IMPORT_PROTOCOL",
    "ISOLATED_IMPORT_PROTOCOL_VERSION", "IsolatedImportEnvelope",
    "inspect_local_pack", "isolated_import_process_entry",
    "parse_isolated_import_metadata", "preflight_local_pack",
    "preview_from_isolated_transfer",
    "validate_local_pack_first_frames",
]
