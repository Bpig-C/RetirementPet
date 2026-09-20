"""EmbeddedOfficialPack registration (DESIGN_V2 22; P0 remediation A).

The official retirement-cat pack ships with the application (source tree
``assets/petpack/`` and the frozen ``_internal/assets/petpack/``).  On every
startup the engine validates the immutable release media and reconciles one
BUILTIN catalog record that points directly at that media:

- idempotent by content digest;
- marked ``builtin`` -> never uninstallable (PPK-LCY-E005);
- eligible as an explicit ACTIVE/LKG target only after validation.

The programmatic CatRenderer remains available as the disaster-recovery
Bootstrap Renderer only; it is NEVER persisted as a fake selection.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from retirement_pet.lifecycle import LifecycleError, PackLibrary, RevisionRecord
from retirement_pet.petpack.validator import validate_petpack
from retirement_pet.resource_path import asset_path

logger = logging.getLogger(__name__)

EMBEDDED_PACK_RELPATH = (
    "petpack", "retirement-cat-official-1.0.2.petpack")
LEGACY_EMBEDDED_PACK_RELPATH = (
    "petpack", "retirement-cat-official.petpack")
EMBEDDED_TRUST_CHANNEL = "BUILTIN_OFFICIAL"

# Frozen v1.0.0 facts.  They are deliberately not inferred from whichever
# file happens to occupy the legacy path: only this exact released Revision is
# eligible for the one-time quality migration.
LEGACY_ARCHIVE_SHA256 = \
    "7aaa6416620eea9d8be7df3cb22c2c0ae2e3b069bc04226f21b393a5e746ed7b"
LEGACY_CONTENT_DIGEST = \
    "86684d32136c205a043995f4889b857c08d6496743047a77165ad72895bdb1ce"
LEGACY_PACKAGE_VERSION = "1.0.0"
CURRENT_PACKAGE_VERSION = "1.0.2"
OFFICIAL_CHARACTER_FQID = \
    "official.retirement-cat-official.retirement-cat.cat"


def embedded_pack_path() -> Path:
    return asset_path(*EMBEDDED_PACK_RELPATH)


def legacy_embedded_pack_path() -> Path:
    return asset_path(*LEGACY_EMBEDDED_PACK_RELPATH)


def _assert_legacy_media_is_frozen(path: Path) -> None:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise LifecycleError(
            "PPK-LCY-E001", "legacy embedded official pack is missing") from exc
    if digest != LEGACY_ARCHIVE_SHA256:
        raise LifecycleError(
            "PPK-LCY-E001", "legacy embedded official pack digest changed")


def ensure_builtin_official(library: PackLibrary) -> RevisionRecord:
    """Validate/register the embedded release-media pack; return its record.

    Raises LifecycleError when the embedded media itself is missing or
    invalid - in that case the caller must fall back to the Bootstrap
    Renderer (programmatic cat) WITHOUT persisting any selection.
    """
    legacy_path = legacy_embedded_pack_path()
    current_path = embedded_pack_path()
    if not current_path.is_file():
        raise LifecycleError(
            "PPK-LCY-E001",
            "embedded official pack missing from application media")
    # Keep the released v1.0.0 Revision discoverable as an immutable fact even
    # on a clean installation.  Catalog presentation may hide it when v1.0.1
    # is available, but catalog storage and exact old selections remain valid.
    _assert_legacy_media_is_frozen(legacy_path)
    legacy = library.register_builtin_release(
        legacy_path, trust_channel=EMBEDDED_TRUST_CHANNEL)
    if legacy.revision_key.content_digest != LEGACY_CONTENT_DIGEST:
        raise LifecycleError(
            "PPK-LCY-E001", "legacy official Revision identity changed")
    record = library.register_builtin_release(
        current_path, trust_channel=EMBEDDED_TRUST_CHANNEL)
    if record.revision_key.package_version != CURRENT_PACKAGE_VERSION:
        raise LifecycleError(
            "PPK-LCY-E001", "current embedded official version is invalid")
    logger.info("builtin official pack READY: %s", record.revision_key)
    return record


def builtin_official_revision(library: PackLibrary) -> RevisionRecord | None:
    """The safe target for selections; None only if embedded media is broken."""
    path = embedded_pack_path()
    if not path.is_file():
        return None
    report = validate_petpack(
        path.read_bytes(), trust_channel=EMBEDDED_TRUST_CHANNEL)
    if not report.accepted:
        return None
    record = library.get_revision(report.revision_key)
    if record is None or not record.builtin:
        return None
    if record.trust_channel != EMBEDDED_TRUST_CHANNEL:
        return None
    try:
        if record.pack_path.resolve(strict=True) != path.resolve(strict=True):
            return None
    except OSError:
        return None
    return record


def is_exact_legacy_builtin_selection(library: PackLibrary, selection) -> bool:
    """Whether ``selection`` is the sole released tuple eligible to migrate."""
    if selection is None:
        return False
    if (
        selection.publisher_id != "official"
        or selection.package_id != "retirement-cat-official"
        or selection.package_version != LEGACY_PACKAGE_VERSION
        or selection.content_digest != LEGACY_CONTENT_DIGEST
        or selection.character_fqid != OFFICIAL_CHARACTER_FQID
        or selection.variant_id is not None
    ):
        return False
    try:
        record = library.get_revision(selection.revision_key())
        if record is None or not record.builtin \
                or record.trust_channel != EMBEDDED_TRUST_CHANNEL:
            return False
        return record.pack_path.resolve(strict=True) == \
            legacy_embedded_pack_path().resolve(strict=True)
    except (OSError, LifecycleError):
        return False


__all__ = [
    "CURRENT_PACKAGE_VERSION",
    "EMBEDDED_PACK_RELPATH",
    "EMBEDDED_TRUST_CHANNEL",
    "LEGACY_ARCHIVE_SHA256",
    "LEGACY_CONTENT_DIGEST",
    "LEGACY_EMBEDDED_PACK_RELPATH",
    "LEGACY_PACKAGE_VERSION",
    "OFFICIAL_CHARACTER_FQID",
    "builtin_official_revision",
    "embedded_pack_path",
    "ensure_builtin_official",
    "is_exact_legacy_builtin_selection",
    "legacy_embedded_pack_path",
]
