"""Tests for deterministic, non-reencoding PNG metadata removal."""

from __future__ import annotations

import importlib.util
import struct
import sys
import zlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    path = ROOT / "scripts" / "sanitize_png.py"
    spec = importlib.util.spec_from_file_location("sanitize_png", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _png(extra: tuple[bytes, bytes] | None = None) -> bytes:
    # One transparent RGBA pixel, filter byte followed by RGBA bytes.
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    chunks = [_chunk(b"IHDR", ihdr)]
    if extra is not None:
        chunks.append(_chunk(*extra))
    chunks.extend((_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")),
                   _chunk(b"IEND", b"")))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def test_sanitizer_removes_unknown_ancillary_without_reencoding() -> None:
    module = _load_module()
    source = _png((b"caBX", b"private generation metadata"))

    result = module.sanitize_png(source)

    assert result.removed_chunks == (b"caBX",)
    assert result.payload == _png()
    assert b"caBX" not in result.payload


def test_sanitizer_is_idempotent_for_allowlisted_png() -> None:
    module = _load_module()
    source = _png()

    first = module.sanitize_png(source)
    second = module.sanitize_png(first.payload)

    assert first.payload == second.payload == source
    assert first.removed_chunks == second.removed_chunks == ()


def test_sanitizer_rejects_unknown_critical_chunk() -> None:
    module = _load_module()

    with pytest.raises(module.PngSanitizationError, match="critical"):
        module.sanitize_png(_png((b"CaBX", b"cannot discard")))
