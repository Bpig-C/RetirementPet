"""Structured identity and the canonical content digest (PETPACK_SPEC 4).

Keys are structured tuples - NEVER bare concatenated strings (ADR-V2-021).
The content digest is the single RevisionKey algorithm:

    SHA-256(
      UTF8("RetirementPet-PetPackTree-v1") || NUL
      || raw_32_bytes(manifest_sha256)
      || for each declared non-manifest file, sorted by normalized path
         UTF-8 bytes: path_utf8 || NUL || uint64_be(byte_size)
         || raw_32_bytes(file_sha256)
    )

Every byte of petpack.json changes the digest; archive bytes do not
(archive_sha256 is container evidence only).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

DIGEST_DOMAIN = b"RetirementPet-PetPackTree-v1"
MANIFEST_NAME = "petpack.json"


@dataclass(frozen=True, order=True)
class PackKey:
    publisher_id: str
    package_id: str

    def __str__(self) -> str:  # display only - never used for identity
        return f"{self.publisher_id}/{self.package_id}"


@dataclass(frozen=True, order=True)
class RevisionKey:
    pack: PackKey
    package_version: str
    content_digest: str

    def __str__(self) -> str:  # display only
        return f"{self.pack}@{self.package_version}#{self.content_digest[:12]}"


def sha256_file(path_bytes: bytes) -> str:
    return hashlib.sha256(path_bytes).hexdigest()


def compute_content_digest(manifest_bytes: bytes,
                           files: list[tuple[str, bytes]]) -> str:
    """Canonical tree digest.

    ``files`` entries are (normalized_path, content_bytes) for every
    non-manifest file.  Sorting is by UTF-8 bytes of the normalized path.
    """
    h = hashlib.sha256()
    h.update(DIGEST_DOMAIN)
    h.update(b"\x00")
    h.update(hashlib.sha256(manifest_bytes).digest())
    for path, content in sorted(files, key=lambda item: item[0].encode("utf-8")):
        h.update(path.encode("utf-8"))
        h.update(b"\x00")
        h.update(len(content).to_bytes(8, "big", signed=False))
        h.update(hashlib.sha256(content).digest())
    return h.hexdigest()
