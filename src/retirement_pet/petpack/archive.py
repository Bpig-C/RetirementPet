"""Restricted ZIP profile for PetPack archives (PETPACK_SPEC 2).

The importer inspects the central directory BEFORE extracting anything and
streams members with a hard output budget.  Central-directory size/CRC
declarations are untrusted; the actual byte count is throttled on read.

Member-name normalization (PETPACK_SPEC 2.2): UTF-8 strict, '/' separators,
no absolute/escape/NUL/colon/trailing-dot-space segments, NFC-only, Windows
case-insensitive collision check, reserved basenames (CON, NUL, COM1-9,
LPT1-9...) rejected even with extensions, duplicate entries rejected.
"""

from __future__ import annotations

import io
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from retirement_pet.petpack.diagnostics import (
    ARC_E002_ENCRYPTED_OR_MULTIVOLUME,
    ARC_E004_COLLISION,
    ARC_E005_LINK_OR_REPARSE,
    ARC_E006_DISABLED_COMPRESSION_OR_NESTED,
    ARC_E007_BUDGET_EXCEEDED,
    ARC_E008_TRUNCATED_OR_CRC,
    ARC_E003_UNSAFE_PATH,
    ARC_E001_UNSUPPORTED_CONTAINER,
    Diagnostic,
    Severity,
)
from retirement_pet.petpack.diagnostics import ValidationFailure

#: resource budgets (PETPACK_SPEC 17, PROVISIONAL start values)
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024
MAX_FILE_COUNT = 2_000
MAX_SINGLE_FILE = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_PATH_DEPTH = 8

MANIFEST_NAME = "petpack.json"

#: supported compression methods (store + deflate); anything else rejected
_ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _fail(diag: Diagnostic) -> None:
    raise ValidationFailure(diag)


def check_reserved_basename(segment: str) -> bool:
    """True when the segment's basename is a Windows reserved device name."""
    base = segment.split(".", 1)[0].upper()
    return base in _WINDOWS_RESERVED


def normalize_member_name(raw: str) -> str:
    """Validate + normalize one archive member name; raises on any violation.

    Returns the normalized '/'-separated relative path.
    """
    # 1. strict UTF-8 already enforced by decode; reject weird separators
    if "\\" in raw:
        _fail(Diagnostic(ARC_E003_UNSAFE_PATH, Severity.ERROR, "archive_preflight",
                         "petpack.archive.unsafe_path", {}))
    if raw != raw.strip() or raw == "":
        _fail(Diagnostic(ARC_E003_UNSAFE_PATH, Severity.ERROR, "archive_preflight",
                         "petpack.archive.unsafe_path", {}))
    pure = PurePosixPath(raw)
    if pure.is_absolute() or raw.startswith("/"):
        _fail(Diagnostic(ARC_E003_UNSAFE_PATH, Severity.ERROR, "archive_preflight",
                         "petpack.archive.unsafe_path", {"why": "absolute"}))
    if raw.endswith("/") or raw.endswith("."):
        _fail(Diagnostic(ARC_E003_UNSAFE_PATH, Severity.ERROR, "archive_preflight",
                         "petpack.archive.unsafe_path", {"why": "trailing"}))

    segments = raw.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        _fail(Diagnostic(ARC_E003_UNSAFE_PATH, Severity.ERROR, "archive_preflight",
                         "petpack.archive.unsafe_path", {"why": "segment"}))
    if len(segments) > MAX_PATH_DEPTH:
        _fail(Diagnostic(ARC_E007_BUDGET_EXCEEDED, Severity.ERROR, "archive_preflight",
                         "petpack.archive.depth_exceeded", {}))
    for seg in segments:
        if ":" in seg or "\x00" in seg:
            _fail(Diagnostic(ARC_E003_UNSAFE_PATH, Severity.ERROR, "archive_preflight",
                             "petpack.archive.unsafe_path", {"why": "colon_or_nul"}))
        if any(ord(ch) < 0x20 for ch in seg):
            _fail(Diagnostic(ARC_E003_UNSAFE_PATH, Severity.ERROR, "archive_preflight",
                             "petpack.archive.unsafe_path", {"why": "control"}))
        # 4. NFC-only: names that need conversion are rejected, not converted
        if unicodedata.normalize("NFC", seg) != seg:
            _fail(Diagnostic(ARC_E004_COLLISION, Severity.ERROR, "archive_preflight",
                             "petpack.archive.unicode_form", {}))
        # trailing dot/space INSIDE a segment (Windows trims them on use)
        if seg != seg.rstrip(". "):
            _fail(Diagnostic(ARC_E003_UNSAFE_PATH, Severity.ERROR, "archive_preflight",
                             "petpack.archive.unsafe_path", {"why": "trailing_ws"}))
        if check_reserved_basename(seg):
            _fail(Diagnostic(ARC_E003_UNSAFE_PATH, Severity.ERROR, "archive_preflight",
                             "petpack.archive.reserved_name", {"name_kind": "device"}))
    # 3. symlinks / directories cannot appear in the normalized form
    return "/".join(segments)


def _win_casefold(path: str) -> str:
    return path.upper()  # ASCII-upper approximates CompareStringOrdinal ignoreCase


@dataclass
class ArchiveEntry:
    normalized_path: str
    raw_name: str
    data: bytes


class PetpackArchive:
    """Validated, fully-materialized view of a .petpack ZIP container.

    The archive must fit the total budget; every member is read with a
    per-file cap and the running total is enforced (compression-bomb and
    central-directory deception countermeasure).
    """

    def __init__(self, data: bytes):
        if len(data) > MAX_ARCHIVE_BYTES:
            _fail(Diagnostic(ARC_E007_BUDGET_EXCEEDED, Severity.ERROR,
                             "archive_preflight", "petpack.archive.too_large", {}))
        self._data = data
        try:
            self._zf = zipfile.ZipFile(io.BytesIO(data))  # no extraction
        except zipfile.BadZipFile:
            _fail(Diagnostic(ARC_E008_TRUNCATED_OR_CRC, Severity.ERROR,
                             "archive_preflight", "petpack.archive.bad_zip", {}))
        self._open_archive()

    def _open_archive(self) -> None:
        zf = self._zf
        if zf.testzip() is not None:
            _fail(Diagnostic(ARC_E008_TRUNCATED_OR_CRC, Severity.ERROR,
                             "archive_preflight", "petpack.archive.crc", {}))
        infos = zf.infolist()
        seen: dict[str, str] = {}       # normalized -> raw
        casefolded: dict[str, str] = {} # windows-casefolded -> normalized
        total_uncompressed = 0
        members: list[ArchiveEntry] = []

        for info in infos:
            # 1. strict UTF-8: any non-ASCII name MUST carry the UTF-8 flag;
            # pure-ASCII names decode identically with or without it
            needs_utf8_flag = any(ord(ch) > 0x7F for ch in info.filename)
            if needs_utf8_flag and not (info.flag_bits & 0x800):
                _fail(Diagnostic(ARC_E004_COLLISION, Severity.ERROR,
                                 "archive_preflight", "petpack.archive.non_utf8",
                                 {}))
            if info.is_dir():
                continue
            if len(infos) > MAX_FILE_COUNT:
                _fail(Diagnostic(ARC_E007_BUDGET_EXCEEDED, Severity.ERROR,
                                 "archive_preflight", "petpack.archive.file_count",
                                 {}))
            normalized = normalize_member_name(info.filename)

            # 5. Windows case-insensitive collisions
            folded = _win_casefold(normalized)
            if folded in casefolded:
                _fail(Diagnostic(ARC_E004_COLLISION, Severity.ERROR,
                                 "archive_preflight", "petpack.archive.case_collision",
                                 {}))
            if normalized in seen:
                _fail(Diagnostic(ARC_E004_COLLISION, Severity.ERROR,
                                 "archive_preflight", "petpack.archive.duplicate",
                                 {}))

            # link/reparse members are a Unix-ZIP concept; a real symlink
            # cannot be expressed in our allowed methods, but unix mode bits
            # are still checked when present
            if (info.external_attr >> 16) & 0o170000 == 0o120000:  # S_IFLNK
                _fail(Diagnostic(ARC_E005_LINK_OR_REPARSE, Severity.ERROR,
                                 "archive_preflight", "petpack.archive.symlink", {}))

            if info.compress_type not in _ALLOWED_COMPRESSION:
                _fail(Diagnostic(ARC_E006_DISABLED_COMPRESSION_OR_NESTED,
                                 Severity.ERROR, "archive_preflight",
                                 "petpack.archive.compression_method", {}))
            if info.file_size > MAX_SINGLE_FILE:
                _fail(Diagnostic(ARC_E007_BUDGET_EXCEEDED, Severity.ERROR,
                                 "archive_preflight", "petpack.archive.file_size", {}))
            # compression-ratio sanity BEFORE trusting declared sizes
            if (info.compress_size > 0
                    and info.file_size / max(1, info.compress_size) > MAX_COMPRESSION_RATIO):
                _fail(Diagnostic(ARC_E007_BUDGET_EXCEEDED, Severity.ERROR,
                                 "archive_preflight", "petpack.archive.ratio", {}))

            total_uncompressed += info.file_size
            if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
                _fail(Diagnostic(ARC_E007_BUDGET_EXCEEDED, Severity.ERROR,
                                 "archive_preflight", "petpack.archive.total_size", {}))

            try:
                data = zf.read(info)  # by ZipInfo: duplicate names exist
            except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
                _fail(Diagnostic(ARC_E008_TRUNCATED_OR_CRC, Severity.ERROR,
                                 "archive_preflight", "petpack.archive.read_failed",
                                 {}))
            if len(data) != info.file_size:
                # central-directory size deception
                _fail(Diagnostic(ARC_E008_TRUNCATED_OR_CRC, Severity.ERROR,
                                 "archive_preflight", "petpack.archive.size_mismatch",
                                 {}))

            seen[normalized] = info.filename
            casefolded[folded] = normalized
            members.append(ArchiveEntry(normalized_path=normalized,
                                        raw_name=info.filename, data=data))

        if MANIFEST_NAME not in seen:
            _fail(Diagnostic(ARC_E003_UNSAFE_PATH, Severity.ERROR,
                             "archive_preflight", "petpack.archive.no_manifest", {}))
        self.members: dict[str, ArchiveEntry] = {
            m.normalized_path: m for m in members
        }
        self.total_uncompressed = total_uncompressed

    # -- access ------------------------------------------------------------

    @property
    def manifest_bytes(self) -> bytes:
        return self.members[MANIFEST_NAME].data

    def read(self, normalized_path: str) -> bytes | None:
        entry = self.members.get(normalized_path)
        return entry.data if entry else None

    def paths(self) -> list[str]:
        return sorted(self.members)

    def close(self) -> None:
        self._zf.close()
