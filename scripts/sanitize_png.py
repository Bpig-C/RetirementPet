"""Deterministically remove non-public ancillary chunks from a PNG.

The sanitizer is deliberately narrow.  It preserves every approved chunk
byte-for-byte (including IDAT data and CRCs), removes only unapproved
*ancillary* chunks, and rejects unapproved critical chunks or malformed PNGs.
This makes it suitable for preparing public source assets without re-encoding
their pixels.

Usage::

    python scripts/sanitize_png.py INPUT OUTPUT
    python scripts/sanitize_png.py INPUT --in-place
    python scripts/sanitize_png.py INPUT --check
"""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
APPROVED_CHUNKS = frozenset(
    {
        b"IHDR",
        b"PLTE",
        b"IDAT",
        b"IEND",
        b"bKGD",
        b"cHRM",
        b"gAMA",
        b"hIST",
        b"pHYs",
        b"sBIT",
        b"sRGB",
        b"tRNS",
    }
)


class PngSanitizationError(ValueError):
    """The input is malformed or cannot be sanitized without re-encoding."""


@dataclass(frozen=True)
class SanitizationResult:
    payload: bytes
    removed_chunks: tuple[bytes, ...]


def _is_chunk_type(chunk_type: bytes) -> bool:
    return len(chunk_type) == 4 and all(
        65 <= byte <= 90 or 97 <= byte <= 122 for byte in chunk_type
    )


def sanitize_png(data: bytes) -> SanitizationResult:
    """Return allowlisted PNG bytes without decoding or recompressing them."""
    if not data.startswith(PNG_SIGNATURE):
        raise PngSanitizationError("PNG signature is missing")

    output = bytearray(PNG_SIGNATURE)
    removed: list[bytes] = []
    offset = len(PNG_SIGNATURE)
    chunk_index = 0
    saw_ihdr = False
    saw_idat = False
    saw_iend = False

    while offset < len(data):
        if len(data) - offset < 12:
            raise PngSanitizationError("PNG chunk framing is truncated")
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        payload_end = offset + 8 + length
        chunk_end = payload_end + 4
        if payload_end < offset or chunk_end > len(data):
            raise PngSanitizationError("PNG chunk exceeds the file boundary")
        if not _is_chunk_type(chunk_type):
            raise PngSanitizationError("PNG chunk type is not alphabetic ASCII")
        expected_crc = int.from_bytes(data[payload_end:chunk_end], "big")
        actual_crc = zlib.crc32(data[offset + 4 : payload_end]) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise PngSanitizationError("PNG chunk CRC does not match")

        if chunk_index == 0 and (chunk_type != b"IHDR" or length != 13):
            raise PngSanitizationError(
                "PNG must begin with one 13-byte IHDR chunk"
            )
        if chunk_type == b"IHDR":
            if saw_ihdr or chunk_index != 0:
                raise PngSanitizationError("PNG contains a misplaced IHDR chunk")
            saw_ihdr = True
        elif chunk_type == b"IDAT":
            saw_idat = True
        elif chunk_type == b"IEND":
            if length != 0 or saw_iend or chunk_end != len(data):
                raise PngSanitizationError("PNG IEND is malformed or not final")
            saw_iend = True

        raw_chunk = data[offset:chunk_end]
        if chunk_type in APPROVED_CHUNKS:
            output.extend(raw_chunk)
        elif chunk_type[0] & 0x20:
            # Lowercase first letter means ancillary.  It is safe to discard
            # without changing the interpretation of the image data.
            removed.append(chunk_type)
        else:
            name = chunk_type.decode("ascii")
            raise PngSanitizationError(
                f"unapproved critical PNG chunk cannot be removed: {name}"
            )

        offset = chunk_end
        chunk_index += 1

    if not (saw_ihdr and saw_idat and saw_iend):
        raise PngSanitizationError("PNG is missing IHDR, IDAT, or IEND")
    return SanitizationResult(bytes(output), tuple(removed))


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("output", type=Path, nargs="?")
    destination.add_argument("--in-place", action="store_true")
    destination.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    source = args.input
    try:
        original = source.read_bytes()
        result = sanitize_png(original)
    except (OSError, PngSanitizationError) as exc:
        print(f"error: {exc}")
        return 2

    changed = result.payload != original
    if args.check:
        if changed:
            names = ", ".join(
                chunk.decode("ascii") for chunk in result.removed_chunks
            )
            print(f"needs sanitization: {names}")
            return 1
        print("PNG already satisfies the public chunk allowlist")
        return 0

    target = source if args.in_place else args.output
    assert target is not None
    try:
        _atomic_write(target, result.payload)
    except OSError as exc:
        print(f"error: {exc}")
        return 2
    names = ", ".join(
        chunk.decode("ascii") for chunk in result.removed_chunks
    ) or "none"
    print(f"removed_chunks {names}")
    print(f"input_sha256  {hashlib.sha256(original).hexdigest()}")
    print(f"output_sha256 {hashlib.sha256(result.payload).hexdigest()}")
    print(f"output_bytes  {len(result.payload)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
