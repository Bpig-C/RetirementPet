"""Immutable library and pack lifecycle (M4; DESIGN_V2 17/18; ADR-V2-014).

Storage model (LibraryRoot):
    state.db                SQLite catalog - the ONLY READY authority
    packs/revisions/<rk>/   immutable revision trees (read-only after publish)
    packs/staging/<txn>/    in-flight installs (same volume as revisions)
    packs/trash/            uninstalls land here before physical delete
    receipts/<install>.json engine-written validation receipts (immutable)
    journal/                append-only lifecycle events + PUBLISH_INTENT

Invariants enforced here:
- INSTALL publishes an immutable Revision and NEVER auto-activates;
- SQLite is the only READY authority (a directory alone proves nothing);
- same PackKey+version with a DIFFERENT content digest is an unconfirmed
  bypass attempt -> PPK-LCY-E007, never overwritten (any user confirmation
  cannot install it);
- identical digest re-install is idempotent;
- crash-safety: PUBLISH_INTENT is durable BEFORE the same-volume rename and
  the catalog commit; recovery replays intents idempotently;
- uninstall of a NON-ACTIVE revision moves it to trash (M4 tier); the
  active-selection swap is M5.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import stat
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from retirement_pet.petpack.identity import PackKey, RevisionKey
from retirement_pet.petpack.validator import validate_petpack

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

LCY_E007_DIGEST_CONFLICT = "PPK-LCY-E007"
LCY_E002_PACKAGE_ID_CONFLICT = "PPK-LCY-E002"

_REVISION_COLUMNS = {
    "publisher_id", "package_id", "package_version", "content_digest",
    "pack_path", "installed_at", "character_count", "trust_channel",
}
_SELECTION_COLUMNS = {
    "slot", "publisher_id", "package_id", "package_version",
    "content_digest", "character_fqid", "variant_id",
    "config_revision_id", "generation", "commit_sequence",
}
_INTENT_FIELDS = {
    "transaction_id", "staging", "target", "publisher_id", "package_id",
    "package_version", "content_digest", "character_count", "trust_channel",
    "archive_sha256", "receipt_id", "receipt_sha256",
}


def _is_lower_hex(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(
        char in "0123456789abcdef" for char in value)


def _path_is_reparse(path: Path) -> bool:
    """Detect every Windows reparse type without following the target."""
    candidate = Path(path)
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise LifecycleError(
            "PPK-LCY-E001", "managed path cannot be inspected") from exc
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_flag = int(getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    is_junction = getattr(candidate, "is_junction", lambda: False)
    return bool(attributes & reparse_flag) or candidate.is_symlink() \
        or bool(is_junction())


class LifecycleError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _lexical_absolute(path: Path) -> Path:
    """Return an absolute normalized path without resolving any link."""
    return Path(os.path.abspath(os.fspath(path)))


def _inspect_lexical_chain(path: Path, *, exact_file: bool = False) -> Path:
    """Fail closed on reparses in any existing lexical ancestor.

    ``Path.resolve`` must not run before this walk: a missing LibraryRoot may
    sit below an existing junction.  The optional link-count rule applies only
    to the exact regular-file leaf, never to directories.
    """
    absolute = _lexical_absolute(path)
    if not absolute.anchor:
        raise LifecycleError(
            "PPK-LCY-E001", "managed path has no lexical anchor")
    current = Path(absolute.anchor)
    components = absolute.parts[1:]
    candidates = [current]
    for component in components:
        current = current / component
        candidates.append(current)

    leaf_info = None
    for candidate in candidates:
        # Call the non-following detector before lstat so tests can model a
        # disappearing/replaced component without causing us to follow it.
        if _path_is_reparse(candidate):
            raise LifecycleError(
                "PPK-LCY-E001", "managed path ancestry contains a reparse")
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            raise LifecycleError(
                "PPK-LCY-E001", "managed path ancestry cannot be inspected"
            ) from exc
        if candidate == absolute:
            leaf_info = info

    if (exact_file and leaf_info is not None
            and stat.S_ISREG(leaf_info.st_mode)
            and int(getattr(leaf_info, "st_nlink", 1)) != 1):
        raise LifecycleError(
            "PPK-LCY-E001", "managed regular file has multiple hard links")
    return absolute


@dataclass
class RevisionRecord:
    revision_key: RevisionKey
    pack_path: Path
    installed_at: str
    character_count: int
    trust_channel: str
    builtin: bool = False


@dataclass
class LifecycleEvent:
    event: str            # INSTALL_COMMITTED | UNINSTALL_TRASHED | ...
    revision: str         # revision key string
    at_utc: str
    details: dict = field(default_factory=dict)


class PackLibrary:
    """Crash-consistent immutable pack library rooted at ``library_root``."""

    def __init__(self, library_root: Path):
        self.degraded = False
        self._configure_paths(library_root, validate_trust_anchor=True)
        self._db: sqlite3.Connection | None = None
        try:
            # Existing state and recovery instructions are inspected through
            # read-only handles before any directory, PRAGMA, DDL or catalog
            # reconciliation is allowed to mutate them.
            self._preflight_existing_database()
            self._preflight_intents()
            for directory in (
                    self.revisions_dir, self.staging_dir, self.trash_dir,
                    self.receipts_dir, self.journal_dir):
                directory.mkdir(parents=True, exist_ok=True)
            self._preflight_managed_paths()
            self._db = sqlite3.connect(self.root / "state.db")
            # A replacement between preflight and connect must be caught
            # before migration or any PRAGMA can write through this handle.
            self._preflight_managed_paths()
            self._migrate()
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self.recover_pending_intents()
        except LifecycleError:
            self._close_failed_connection()
            raise
        except Exception as exc:  # noqa: BLE001 - normalize init failures
            self._close_failed_connection()
            raise LifecycleError(
                "PPK-LCY-E001",
                f"library initialization failed: {type(exc).__name__}") from exc

    def _configure_paths(self, library_root: Path, *,
                         validate_trust_anchor: bool) -> None:
        # Keep the lexical absolute path until the trust anchor itself has
        # been inspected.  Resolving first would follow a junction/symlink
        # into an attacker-controlled tree before we had a chance to reject.
        requested = _lexical_absolute(library_root)
        self.requested_root = requested
        if validate_trust_anchor:
            _inspect_lexical_chain(requested)
            if requested.exists():
                if not requested.is_dir():
                    raise LifecycleError(
                        "PPK-LCY-E001",
                        "LibraryRoot is not a trusted plain directory")
            else:
                requested.mkdir(parents=True, exist_ok=True)
                _inspect_lexical_chain(requested)
                if not requested.is_dir():
                    raise LifecycleError(
                        "PPK-LCY-E001",
                        "created LibraryRoot is not a trusted plain directory")
            # No ancestor can be a link, so retaining the lexical absolute
            # root avoids introducing a later resolve/follow boundary.
            self.root = requested
        else:
            # Bootstrap uses only an in-memory database and must remain
            # available even when the disk trust anchor is rejected.
            self.root = requested
        self.revisions_dir = self.root / "packs" / "revisions"
        self.staging_dir = self.root / "packs" / "staging"
        self.trash_dir = self.root / "packs" / "trash"
        self.receipts_dir = self.root / "receipts"
        self.journal_dir = self.root / "journal"

    def _preflight_managed_paths(self) -> None:
        """Reject every existing reparse boundary before opening storage."""
        _inspect_lexical_chain(self.requested_root)
        _inspect_lexical_chain(self.root)
        if not self.root.is_dir():
            raise LifecycleError(
                "PPK-LCY-E001", "LibraryRoot trust anchor changed")
        file_anchors = (
            self.root / "state.db",
            self.root / "state.db-journal",
            self.root / "state.db-wal",
            self.root / "state.db-shm",
        )
        directory_anchors = (
            self.root / "packs",
            self.journal_dir,
            self.staging_dir,
            self.revisions_dir,
            self.trash_dir,
            self.receipts_dir,
        )
        for path in file_anchors:
            _inspect_lexical_chain(path, exact_file=True)
            if path.exists() and not path.is_file():
                raise LifecycleError(
                    "PPK-LCY-E001", "managed database path is not a file")
        for path in directory_anchors:
            _inspect_lexical_chain(path)
            if path.exists() and not path.is_dir():
                raise LifecycleError(
                    "PPK-LCY-E001", "managed storage path is not a directory")

    def _close_failed_connection(self) -> None:
        connection = getattr(self, "_db", None)
        if connection is not None:
            try:
                connection.close()
            finally:
                self._db = None

    def _preflight_existing_database(self) -> None:
        self._preflight_managed_paths()
        database = self.root / "state.db"
        if not database.is_file() or database.stat().st_size == 0:
            return
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                database.as_uri() + "?mode=ro", uri=True)
            self._preflight_managed_paths()
            check = connection.execute("PRAGMA quick_check").fetchall()
            if not check or any(row[0] != "ok" for row in check):
                raise LifecycleError(
                    "PPK-LCY-E001", "state.db integrity check failed")
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            if "meta" not in tables or "revisions" not in tables:
                raise LifecycleError(
                    "PPK-LCY-E001", "state.db catalog schema is incomplete")
            meta_info = list(
                connection.execute("PRAGMA table_info(meta)"))
            meta_columns = {row[1] for row in meta_info}
            meta_pk = [row[1] for row in sorted(
                (row for row in meta_info if row[5]), key=lambda row: row[5])]
            if meta_columns != {"key", "value"} or meta_pk != ["key"]:
                raise LifecycleError(
                    "PPK-LCY-E001", "state.db meta schema is malformed")
            version_row = connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version_row is None:
                raise LifecycleError(
                    "PPK-LCY-E001", "state.db schema version is missing")
            try:
                version = int(version_row[0])
            except (TypeError, ValueError) as exc:
                raise LifecycleError(
                    "PPK-LCY-E001",
                    "state.db schema version is malformed") from exc
            if version > SCHEMA_VERSION:
                raise LifecycleError(
                    "PPK-LCY-E001",
                    "state.db schema is newer than this build")
            revision_info = list(
                connection.execute("PRAGMA table_info(revisions)"))
            revision_columns = {row[1] for row in revision_info}
            revision_pk = [row[1] for row in sorted(
                (row for row in revision_info if row[5]),
                key=lambda row: row[5])]
            allowed_revision_columns = (
                _REVISION_COLUMNS, _REVISION_COLUMNS | {"builtin"})
            if (revision_columns not in allowed_revision_columns
                    or revision_pk != [
                        "publisher_id", "package_id", "package_version",
                        "content_digest"]):
                raise LifecycleError(
                    "PPK-LCY-E001", "revision catalog schema is malformed")
            if "active_selection" in tables:
                selection_info = list(connection.execute(
                    "PRAGMA table_info(active_selection)"))
                selection_columns = {row[1] for row in selection_info}
                selection_pk = [row[1] for row in sorted(
                    (row for row in selection_info if row[5]),
                    key=lambda row: row[5])]
                if (selection_columns != _SELECTION_COLUMNS
                        or selection_pk != ["slot"]):
                    raise LifecycleError(
                        "PPK-LCY-E001",
                        "active selection schema is malformed")
                rows = connection.execute(
                    "SELECT slot, publisher_id, package_id, package_version,"
                    " content_digest, character_fqid, variant_id,"
                    " config_revision_id, generation, commit_sequence"
                    " FROM active_selection").fetchall()
                for row in rows:
                    self._validate_selection_row(row)
        except LifecycleError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise LifecycleError(
                "PPK-LCY-E001", "state.db preflight failed") from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _validate_selection_row(row: tuple) -> None:
        slot, publisher, package, version, digest, fqid, variant, config, \
            generation, sequence = row
        if slot not in {"active", "last_known_good"}:
            raise LifecycleError(
                "PPK-LCY-E001", "active selection slot is invalid")
        required_strings = (publisher, package, version, digest, fqid)
        if any(not isinstance(value, str) or not value
               for value in required_strings):
            raise LifecycleError(
                "PPK-LCY-E001", "active selection identity is malformed")
        if len(digest) != 64 or any(
                char not in "0123456789abcdef" for char in digest.lower()):
            raise LifecycleError(
                "PPK-LCY-E001", "active selection digest is malformed")
        if variant is not None and not isinstance(variant, str):
            raise LifecycleError(
                "PPK-LCY-E001", "active selection variant is malformed")
        if config is not None and not isinstance(config, str):
            raise LifecycleError(
                "PPK-LCY-E001", "active selection config is malformed")
        if (not isinstance(generation, int) or isinstance(generation, bool)
                or generation < 0
                or not isinstance(sequence, int)
                or isinstance(sequence, bool) or sequence < 0):
            raise LifecycleError(
                "PPK-LCY-E001", "active selection counters are malformed")

    def _preflight_intents(self) -> None:
        if not self.journal_dir.is_dir():
            return
        for intent_path in sorted(self.journal_dir.glob("intent-*.json")):
            self._validated_intent(intent_path)

    def _validated_intent(self, intent_path: Path) -> dict:
        """Return canonical trusted recovery fields or fail without mutation."""
        try:
            intent = json.loads(
                self._read_managed_bytes(intent_path).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LifecycleError(
                "PPK-LCY-E001", "install intent is unreadable") from exc
        if not isinstance(intent, dict) \
                or not _INTENT_FIELDS.issubset(intent):
            raise LifecycleError(
                "PPK-LCY-E001", "install intent schema is malformed")
        if any(not isinstance(intent[field], str) or not intent[field]
               for field in (
                   "transaction_id", "staging", "target", "publisher_id",
                   "package_id", "package_version", "content_digest",
                   "trust_channel", "archive_sha256", "receipt_id",
                   "receipt_sha256")):
            raise LifecycleError(
                "PPK-LCY-E001", "install intent value is malformed")
        transaction_id = intent["transaction_id"]
        if not _is_lower_hex(transaction_id, 32):
            raise LifecycleError(
                "PPK-LCY-E001", "install intent transaction id is malformed")
        if intent_path.name != f"intent-{transaction_id}.json":
            raise LifecycleError(
                "PPK-LCY-E001", "install intent identity is malformed")
        if intent.get("expected_mutation") != "INSERT_REVISION":
            raise LifecycleError(
                "PPK-LCY-E001", "install intent mutation is malformed")
        character_count = intent.get("character_count")
        if not isinstance(character_count, int) \
                or isinstance(character_count, bool) or character_count < 0:
            raise LifecycleError(
                "PPK-LCY-E001", "install intent character count is malformed")
        if "builtin" in intent and not isinstance(intent["builtin"], bool):
            raise LifecycleError(
                "PPK-LCY-E001", "install intent builtin flag is malformed")
        if (intent["trust_channel"] != "LOCAL_IMPORTED"
                or intent.get("builtin", False) is not False):
            raise LifecycleError(
                "PPK-LCY-E001",
                "install intent cannot assign privileged trust")
        digest = intent["content_digest"]
        if not _is_lower_hex(digest, 64):
            raise LifecycleError(
                "PPK-LCY-E001", "install intent digest is malformed")
        archive_sha256 = intent["archive_sha256"]
        receipt_id = intent["receipt_id"]
        receipt_sha256 = intent["receipt_sha256"]
        if (not _is_lower_hex(archive_sha256, 64)
                or not _is_lower_hex(receipt_sha256, 64)
                or not _is_lower_hex(receipt_id, 32)):
            raise LifecycleError(
                "PPK-LCY-E001", "install intent receipt binding is malformed")

        rk = RevisionKey(
            pack=PackKey(intent["publisher_id"], intent["package_id"]),
            package_version=intent["package_version"],
            content_digest=digest,
        )
        expected_staging = (
            self.staging_dir / transaction_id / "pack.petpack")
        expected_target = self._path_of(rk)
        expected_receipt = self.receipts_dir / f"{receipt_id}.json"
        staging = Path(intent["staging"])
        target = Path(intent["target"])
        if not staging.is_absolute() or not target.is_absolute():
            raise LifecycleError(
                "PPK-LCY-E001", "install intent paths must be absolute")
        if staging.resolve(strict=False) != \
                expected_staging.resolve(strict=False):
            raise LifecycleError(
                "PPK-LCY-E001", "install intent staging path escaped root")
        if target.resolve(strict=False) != expected_target.resolve(strict=False):
            raise LifecycleError(
                "PPK-LCY-E001", "install intent target path escaped root")
        self._recheck_action_paths(
            intent_path, expected_staging, expected_target, expected_receipt)
        if any(path.exists() and not path.is_file()
               for path in (expected_staging, expected_target)):
            raise LifecycleError(
                "PPK-LCY-E001", "install intent media path is not a file")

        try:
            receipt_bytes = self._read_managed_bytes(expected_receipt)
            receipt = json.loads(receipt_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LifecycleError(
                "PPK-LCY-E001", "install receipt is unreadable") from exc
        if hashlib.sha256(receipt_bytes).hexdigest() != receipt_sha256:
            raise LifecycleError(
                "PPK-LCY-E001", "install receipt hash does not match intent")
        if not isinstance(receipt, dict):
            raise LifecycleError(
                "PPK-LCY-E001", "install receipt schema is malformed")
        expected_receipt_values = {
            "install_id": receipt_id,
            "publisher_id": intent["publisher_id"],
            "package_id": intent["package_id"],
            "package_version": intent["package_version"],
            "content_digest": digest,
            "archive_sha256": archive_sha256,
            "trust_channel": intent["trust_channel"],
            "publisher_verification_status": "UNVERIFIED",
        }
        if (type(receipt.get("receipt_version")) is not int
                or receipt.get("receipt_version") != 1
                or any(receipt.get(key) != value
                       for key, value in expected_receipt_values.items())
                or not _is_lower_hex(receipt.get("manifest_sha256"), 64)):
            raise LifecycleError(
                "PPK-LCY-E001", "install receipt does not match intent")

        media = [
            path for path in (expected_staging, expected_target)
            if path.is_file()
        ]
        if not media:
            raise LifecycleError(
                "PPK-LCY-E001", "install intent has no recoverable media")
        for media_path in media:
            data = self._read_managed_bytes(media_path)
            if hashlib.sha256(data).hexdigest() != archive_sha256:
                raise LifecycleError(
                    "PPK-LCY-E001",
                    "install intent archive hash does not match media")
            report = validate_petpack(
                data, trust_channel=intent["trust_channel"])
            if not report.accepted or report.revision_key != rk:
                raise LifecycleError(
                    "PPK-LCY-E001",
                    "install intent media does not match revision tuple")
            if len(report.manifest.get("characters", [])) != character_count:
                raise LifecycleError(
                    "PPK-LCY-E001",
                    "install intent character count does not match media")
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    manifest_bytes = archive.read("petpack.json")
            except (zipfile.BadZipFile, KeyError, OSError) as exc:
                raise LifecycleError(
                    "PPK-LCY-E001",
                    "install intent manifest snapshot is unavailable") from exc
            if (hashlib.sha256(manifest_bytes).hexdigest()
                    != receipt["manifest_sha256"]):
                raise LifecycleError(
                    "PPK-LCY-E001",
                    "install receipt manifest does not match media")
            warning_codes = sorted({
                diagnostic.code for diagnostic in report.diagnostics
                if getattr(diagnostic.severity, "value", diagnostic.severity)
                == "WARNING"
            })
            if receipt.get("warnings_acknowledged") != warning_codes:
                raise LifecycleError(
                    "PPK-LCY-E001",
                    "install receipt warning decision does not match media")
        trusted = dict(intent)
        trusted["staging"] = str(expected_staging)
        trusted["target"] = str(expected_target)
        return trusted

    def _reject_reparse_path(self, path: Path) -> None:
        _inspect_lexical_chain(self.requested_root)
        _inspect_lexical_chain(self.root)
        root = _lexical_absolute(self.root)
        candidate = _inspect_lexical_chain(path, exact_file=True)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise LifecycleError(
                "PPK-LCY-E001", "recovery path escaped LibraryRoot") from exc
        # Canonical containment is a second defence.  The lexical chain is
        # checked again afterwards so a concurrent junction replacement is
        # never accepted merely because root and child resolved together.
        canonical_root = root.resolve(strict=False)
        canonical_candidate = candidate.resolve(strict=False)
        if (canonical_candidate != canonical_root
                and canonical_root not in canonical_candidate.parents):
            raise LifecycleError(
                "PPK-LCY-E001", "recovery path escaped LibraryRoot")
        _inspect_lexical_chain(root)
        _inspect_lexical_chain(candidate, exact_file=True)

    def _recheck_action_paths(self, *paths: Path) -> None:
        """Recheck the trust anchor and exact lexical parents before I/O."""
        self._preflight_managed_paths()
        for path in paths:
            self._reject_reparse_path(Path(path))

    def _verify_open_managed_file(self, handle, path: Path) -> None:
        """Bind an opened handle to the checked single-link lexical file."""
        self._reject_reparse_path(path)
        try:
            opened = os.fstat(handle.fileno())
            lexical = Path(path).lstat()
        except OSError as exc:
            raise LifecycleError(
                "PPK-LCY-E001", "managed file handle cannot be inspected"
            ) from exc
        if (not stat.S_ISREG(opened.st_mode)
                or int(getattr(opened, "st_nlink", 1)) != 1
                or not stat.S_ISREG(lexical.st_mode)
                or int(getattr(lexical, "st_nlink", 1)) != 1
                or not os.path.samestat(opened, lexical)):
            raise LifecycleError(
                "PPK-LCY-E001", "managed file handle is not a safe leaf")

    def _read_managed_bytes(self, path: Path) -> bytes:
        target = Path(path)
        self._recheck_action_paths(target)
        try:
            with open(target, "rb") as handle:
                self._verify_open_managed_file(handle, target)
                return handle.read()
        except LifecycleError:
            raise
        except OSError as exc:
            raise LifecycleError(
                "PPK-LCY-E001", "managed file cannot be read") from exc

    def _cleanup_staging_copy(self, staging: Path, staged_file: Path) -> None:
        """Best-effort cleanup of only the exact transaction leaves.

        Unknown children, reparses and hard links are deliberately left for
        diagnosis instead of giving failure cleanup a recursive delete power.
        """
        try:
            self._recheck_action_paths(
                self.staging_dir, staging, staged_file)
            if staged_file.is_file():
                staged_file.unlink()
            elif staged_file.exists():
                return
            self._recheck_action_paths(self.staging_dir, staging)
            staging.rmdir()
        except (LifecycleError, OSError):
            pass

    @classmethod
    def bootstrap(cls, library_root: Path) -> "PackLibrary":
        """Create an in-memory recovery catalog without touching state.db.

        This is deliberately separate from the normal constructor: callers
        still see corrupt/newer catalogs as failures, while the application
        can keep its Bootstrap pet and recovery UI alive.  The broken disk
        database remains frozen for a newer build or explicit repair.
        """
        library = cls.__new__(cls)
        library.degraded = True
        library._configure_paths(
            library_root, validate_trust_anchor=False)
        library._db = sqlite3.connect(":memory:")
        library._migrate()
        library._db.execute("PRAGMA synchronous=FULL")
        return library

    # -- schema -----------------------------------------------------------------

    def _migrate(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS meta ("
                " key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = self._db.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO meta VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),))
            elif int(row[0]) > SCHEMA_VERSION:
                raise LifecycleError(
                    "PPK-LCY-E001",
                    "state.db schema is newer than this build")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS revisions ("
                " publisher_id TEXT NOT NULL,"
                " package_id TEXT NOT NULL,"
                " package_version TEXT NOT NULL,"
                " content_digest TEXT NOT NULL,"
                " pack_path TEXT NOT NULL,"
                " installed_at TEXT NOT NULL,"
                " character_count INTEGER NOT NULL,"
                " trust_channel TEXT NOT NULL,"
                " builtin INTEGER NOT NULL DEFAULT 0,"
                " PRIMARY KEY (publisher_id, package_id, package_version,"
                "              content_digest))")
            columns = {row[1] for row in
                       self._db.execute("PRAGMA table_info(revisions)")}
            if "builtin" not in columns:
                self._db.execute(
                    "ALTER TABLE revisions ADD COLUMN builtin INTEGER NOT NULL"
                    " DEFAULT 0")
            self._db.execute("COMMIT")
        except Exception:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # -- keys -------------------------------------------------------------------

    @staticmethod
    def _row_of(rk: RevisionKey) -> tuple:
        return (rk.pack.publisher_id, rk.pack.package_id,
                rk.package_version, rk.content_digest)

    def _path_of(self, rk: RevisionKey) -> Path:
        return (self.revisions_dir / rk.pack.publisher_id / rk.pack.package_id
                / f"{rk.package_version}-{rk.content_digest[:16]}")

    # -- journal ------------------------------------------------------------------

    def _append_event(self, event: LifecycleEvent) -> None:
        path = self.journal_dir / "events.jsonl"
        self._recheck_action_paths(self.journal_dir, path)
        payload = json.dumps({
            "event": event.event, "revision": event.revision,
            "at_utc": event.at_utc, **event.details,
        }, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            # Binary append lets us preserve a torn legacy tail byte-for-byte
            # while adding one newline boundary.  Prefix and event are a
            # single write, then one durability barrier.
            with open(path, "a+b") as handle:
                self._verify_open_managed_file(handle, path)
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                separator = b""
                if size:
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) != b"\n":
                        separator = b"\n"
                    handle.seek(0, os.SEEK_END)
                handle.write(separator + payload)
                handle.flush()
                os.fsync(handle.fileno())
        except LifecycleError:
            raise
        except OSError as exc:
            raise LifecycleError(
                "PPK-LCY-E001", "lifecycle event cannot be appended") from exc

    def _install_event_exists(self, transaction_id: str,
                              revision: str) -> bool:
        path = self.journal_dir / "events.jsonl"
        self._recheck_action_paths(self.journal_dir, path)
        if not path.is_file():
            return False
        try:
            data = self._read_managed_bytes(path)
        except LifecycleError:
            raise

        exact_match = False
        latest_relevant = None
        for line_number, raw_line in enumerate(data.splitlines(), start=1):
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.warning(
                    "ignoring malformed lifecycle event line %d",
                    line_number,
                )
                continue
            if not isinstance(event, dict) or event.get("revision") != revision:
                continue
            event_name = event.get("event")
            if event_name not in {"INSTALL_COMMITTED", "UNINSTALL_TRASHED"}:
                continue
            latest_relevant = event
            if (event_name == "INSTALL_COMMITTED"
                    and event.get("transaction_id") == transaction_id):
                exact_match = True

        if exact_match:
            return True
        return bool(
            latest_relevant
            and latest_relevant.get("event") == "INSTALL_COMMITTED"
            and "transaction_id" not in latest_relevant
        )

    def _ensure_install_event(self, intent: dict, rk: RevisionKey,
                              at_utc: str) -> None:
        transaction_id = intent["transaction_id"]
        revision = str(rk)
        if self._install_event_exists(transaction_id, revision):
            return
        self._append_event(LifecycleEvent(
            "INSTALL_COMMITTED", revision, at_utc,
            {
                "builtin": False,
                "transaction_id": transaction_id,
                "receipt_id": intent["receipt_id"],
                "receipt_sha256": intent["receipt_sha256"],
            },
        ))

    def _write_intent(self, intent: dict) -> Path:
        path = self.journal_dir / f"intent-{intent['transaction_id']}.json"
        tmp = path.with_suffix(".tmp")
        self._recheck_action_paths(self.journal_dir, path, tmp)
        payload = json.dumps(intent, ensure_ascii=False).encode("utf-8")
        try:
            # A unique transaction must never inherit/truncate an existing
            # tmp leaf, which could have been replaced by a hard link.
            handle = open(tmp, "xb")
        except OSError as exc:
            raise LifecycleError(
                "PPK-LCY-E001", "install intent temp file cannot be created"
            ) from exc
        try:
            with handle:
                self._verify_open_managed_file(handle, tmp)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._recheck_action_paths(self.journal_dir, path, tmp)
            tmp.replace(path)
        except Exception:
            try:
                self._recheck_action_paths(self.journal_dir, tmp)
                tmp.unlink(missing_ok=True)
            except (LifecycleError, OSError):
                pass
            raise
        return path

    # -- install ----------------------------------------------------------------

    def install(self, pack_path: Path, *,
                trust_channel: str = "LOCAL_IMPORTED",
                builtin: bool = False,
                warnings_acknowledged: tuple[str, ...] = ()) -> RevisionRecord:
        """Validate, stage, publish and register one .petpack (never activates).

        This journal-backed entry is deliberately unprivileged.  Builtin
        release media must use :meth:`register_builtin_release` and never
        enters the user-writable recovery journal.
        """
        data = Path(pack_path).read_bytes()
        return self.install_bytes(
            data, trust_channel=trust_channel, builtin=builtin,
            warnings_acknowledged=warnings_acknowledged)

    def install_bytes(self, data: bytes, *,
                      trust_channel: str = "LOCAL_IMPORTED",
                      builtin: bool = False,
                      warnings_acknowledged: tuple[str, ...] = (),
                      expected_revision: RevisionKey | None = None,
                      expected_archive_sha256: str | None = None,
                      validation_report=None,
                      ) -> RevisionRecord:
        """Install one immutable in-memory snapshot of a local PetPack.

        GUI import uses this entry point after engine validation so the bytes
        checked in the background are exactly the bytes staged into the
        library.  Other callers are validated here; a supplied engine report
        is accepted only with explicit revision and archive-hash bindings.
        """
        if trust_channel != "LOCAL_IMPORTED" or builtin:
            raise LifecycleError(
                "PPK-LCY-E001",
                "general install accepts only LOCAL_IMPORTED non-builtin"
                " packs")
        if not isinstance(data, bytes):
            raise TypeError("install_bytes requires immutable bytes")
        archive_sha256 = hashlib.sha256(data).hexdigest()
        # Interactive import performs the expensive pure validation in a
        # background worker and passes its report with two byte bindings.  All
        # other callers are validated here.  The report is engine-generated;
        # it is never read from the package.
        report = validation_report
        if report is not None and (
                expected_revision is None or expected_archive_sha256 is None):
            raise LifecycleError(
                "PPK-LCY-E001",
                "prevalidated install requires revision and archive bindings")
        if report is None:
            report = validate_petpack(data, trust_channel=trust_channel)
        if (getattr(report, "archive_sha256", None) != archive_sha256
                or getattr(report, "trust_channel", None) != trust_channel):
            raise LifecycleError(
                "PPK-LCY-E001",
                "validation report does not bind this archive and trust"
                " channel")
        if not report.accepted:
            raise LifecycleError(
                "PPK-LCY-E001",
                "pack rejected: " + ",".join(d.code for d in report.diagnostics))
        rk = report.revision_key
        manifest = report.manifest
        if expected_revision is not None and rk != expected_revision:
            raise LifecycleError(
                "PPK-LCY-E001", "preflight revision binding mismatch")
        if (expected_archive_sha256 is not None
                and archive_sha256 != expected_archive_sha256):
            raise LifecycleError(
                "PPK-LCY-E001", "preflight archive binding mismatch")
        warning_codes = tuple(sorted({
            d.code for d in report.diagnostics
            if getattr(d.severity, "value", d.severity) == "WARNING"
        }))
        acknowledged = tuple(sorted(set(warnings_acknowledged)))
        if acknowledged != warning_codes:
            raise LifecycleError(
                "PPK-LCY-E003",
                "validation warnings were not exactly acknowledged")

        existing = self.get_revision(rk)
        if existing is not None:
            try:
                if existing.builtin:
                    existing_data = existing.pack_path.read_bytes()
                else:
                    existing_data = self._read_managed_bytes(
                        existing.pack_path)
                existing_report = validate_petpack(
                    existing_data, trust_channel=existing.trust_channel)
                media_is_valid = (
                    existing_report.accepted
                    and existing_report.revision_key == rk
                )
            except (LifecycleError, OSError):
                media_is_valid = False
            if media_is_valid:
                # ZIP order/compression metadata is not revision identity.
                # The incoming archive already validated to the same canonical
                # RevisionKey, so a healthy READY copy makes this idempotent.
                return existing  # identical digest + media -> idempotent
            raise LifecycleError(
                "PPK-LCY-E003",
                "registered revision media is missing or changed")

        conflict = self._db.execute(
            "SELECT content_digest FROM revisions WHERE publisher_id=? AND"
            " package_id=? AND package_version=?",
            (rk.pack.publisher_id, rk.pack.package_id, rk.package_version),
        ).fetchone()
        if conflict is not None:
            raise LifecycleError(
                LCY_E007_DIGEST_CONFLICT,
                "same PackKey+version already registered with a different"
                " digest; bump the version or change the package id")

        transaction_id = uuid.uuid4().hex
        staging = self.staging_dir / transaction_id
        self._recheck_action_paths(self.staging_dir, staging)
        staging.mkdir(parents=True)
        staged_file = staging / "pack.petpack"
        self._recheck_action_paths(self.staging_dir, staging, staged_file)
        try:
            staged_handle = open(staged_file, "xb")
        except OSError as exc:
            self._cleanup_staging_copy(staging, staged_file)
            raise LifecycleError(
                "PPK-LCY-E001", "staging media cannot be created") from exc
        try:
            with staged_handle:
                self._verify_open_managed_file(staged_handle, staged_file)
                staged_handle.write(data)
                staged_handle.flush()
                os.fsync(staged_handle.fileno())
        except Exception:
            self._cleanup_staging_copy(staging, staged_file)
            raise

        # The immutable validation receipt is published before PUBLISH_INTENT
        # and READY.  It describes only this exact byte snapshot and does not
        # claim installation success.
        try:
            receipt = self._write_receipt_data(
                data, report,
                trust_channel=trust_channel,
                warnings_acknowledged=acknowledged,
                archive_sha256=archive_sha256,
            )
            receipt_path = (
                self.receipts_dir / f"{receipt['install_id']}.json")
            receipt_sha256 = hashlib.sha256(
                self._read_managed_bytes(receipt_path)).hexdigest()
        except Exception:
            self._cleanup_staging_copy(staging, staged_file)
            raise

        target = self._path_of(rk)
        intent = {
            "transaction_id": transaction_id,
            "staging": str(staged_file),
            "target": str(target),
            "publisher_id": rk.pack.publisher_id,
            "package_id": rk.pack.package_id,
            "package_version": rk.package_version,
            "content_digest": rk.content_digest,
            "character_count": len(manifest.get("characters", [])),
            "trust_channel": trust_channel,
            "archive_sha256": archive_sha256,
            "receipt_id": receipt["install_id"],
            "receipt_sha256": receipt_sha256,
            "builtin": False,
            "expected_mutation": "INSERT_REVISION",
        }
        try:
            self._write_intent(intent)
        except Exception:
            # The immutable receipt remains a truthful validation fact, but
            # without a durable intent neither staged bytes nor READY state
            # may survive this failed transaction.
            self._cleanup_staging_copy(staging, staged_file)
            raise

        # same-volume atomic publish
        if target.exists():
            self._recheck_action_paths(
                staging, staged_file, target.parent, target)
            self._cleanup_staging_copy(staging, staged_file)
            self._delete_intent(transaction_id)
            existing = self.get_revision(rk)
            if existing is not None:
                return existing
            raise LifecycleError(LCY_E007_DIGEST_CONFLICT,
                                 "target exists but catalog is missing it")
        self._recheck_action_paths(
            staging, staged_file, target.parent, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._recheck_action_paths(
            staging, staged_file, target.parent, target)
        shutil.move(str(staged_file), str(target))
        self._cleanup_staging_copy(staging, staged_file)

        self._commit_install(intent, rk)
        return self.get_revision(rk)

    def register_builtin_release(
            self, pack_path: Path, *,
            trust_channel: str = "BUILTIN_OFFICIAL") -> RevisionRecord:
        """Register immutable release media without copying it to LibraryRoot.

        This dedicated path also reconciles legacy rows that pointed at a
        writable install copy.  Reconciliation changes only catalog metadata;
        it never relies on, rewrites, or deletes that mutable copy.
        """
        if trust_channel != "BUILTIN_OFFICIAL":
            raise LifecycleError(
                "PPK-LCY-E001",
                "builtin release registration requires BUILTIN_OFFICIAL")
        source = Path(pack_path).resolve(strict=True)
        data = source.read_bytes()
        report = validate_petpack(data, trust_channel=trust_channel)
        if not report.accepted:
            raise LifecycleError(
                "PPK-LCY-E001",
                "embedded pack rejected: "
                + ",".join(d.code for d in report.diagnostics))
        rk = report.revision_key
        character_count = len(report.manifest.get("characters", []))

        conflict = self._db.execute(
            "SELECT content_digest FROM revisions WHERE publisher_id=? AND"
            " package_id=? AND package_version=? AND content_digest<>?",
            (rk.pack.publisher_id, rk.pack.package_id, rk.package_version,
             rk.content_digest),
        ).fetchone()
        if conflict is not None:
            raise LifecycleError(
                LCY_E007_DIGEST_CONFLICT,
                "builtin PackKey+version conflicts with the release digest")

        existing = self.get_revision(rk)
        installed_at = (existing.installed_at if existing is not None else
                        datetime.now(timezone.utc).isoformat(timespec="seconds"))
        with self._db:
            self._db.execute(
                "INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,1)"
                " ON CONFLICT(publisher_id, package_id, package_version,"
                " content_digest) DO UPDATE SET"
                " pack_path=excluded.pack_path,"
                " character_count=excluded.character_count,"
                " trust_channel=excluded.trust_channel, builtin=1",
                (*self._row_of(rk), str(source), installed_at,
                 character_count, trust_channel),
            )
        record = self.get_revision(rk)
        if record is None:  # defensive: transaction above is authoritative
            raise LifecycleError("PPK-LCY-E001",
                                 "builtin release registration disappeared")
        return record

    def _mark_builtin(self, rk: RevisionKey) -> None:
        with self._db:
            self._db.execute(
                "UPDATE revisions SET builtin=1 WHERE publisher_id=? AND"
                " package_id=? AND package_version=? AND content_digest=?",
                self._row_of(rk))

    def is_builtin(self, rk: RevisionKey) -> bool:
        row = self._db.execute(
            "SELECT builtin FROM revisions WHERE publisher_id=? AND"
            " package_id=? AND package_version=? AND content_digest=?",
            self._row_of(rk)).fetchone()
        return bool(row and row[0])

    def builtin_revisions(self) -> list[RevisionRecord]:
        return [r for r in self.list_revisions() if self.is_builtin(r.revision_key)]

    def _commit_install(self, intent: dict, rk: RevisionKey) -> None:
        # Bind the database mutation to the durable intent, receipt and media
        # at the final action boundary.  The in-memory dict is advisory only.
        transaction_id = intent.get("transaction_id")
        if not _is_lower_hex(transaction_id, 32):
            raise LifecycleError(
                "PPK-LCY-E001", "install commit identity is malformed")
        intent_path = self.journal_dir / f"intent-{transaction_id}.json"
        intent = self._validated_intent(intent_path)
        bound_rk = RevisionKey(
            pack=PackKey(intent["publisher_id"], intent["package_id"]),
            package_version=intent["package_version"],
            content_digest=intent["content_digest"],
        )
        if bound_rk != rk:
            raise LifecycleError(
                "PPK-LCY-E001", "install commit revision binding mismatch")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO revisions VALUES (?,?,?,?,?,?,?,?,?)",
                (*self._row_of(rk), str(self._path_of(rk)), now,
                 intent["character_count"], intent["trust_channel"],
                 1 if intent.get("builtin") else 0))
        self._ensure_install_event(intent, rk, now)
        self._delete_intent(intent["transaction_id"])

    def _delete_intent(self, transaction_id: str) -> None:
        path = self.journal_dir / f"intent-{transaction_id}.json"
        self._recheck_action_paths(self.journal_dir, path)
        path.unlink(missing_ok=True)

    # -- recovery --------------------------------------------------------------

    def recover_pending_intents(self) -> int:
        """Idempotent crash recovery: finish or roll back pending intents."""
        recovered = 0
        for intent_path in sorted(self.journal_dir.glob("intent-*.json")):
            # Revalidate immediately before every recovery action.  No raw
            # user-writable JSON path is ever passed to unlink/move/rmtree.
            intent = self._validated_intent(intent_path)
            rk = RevisionKey(
                pack=PackKey(publisher_id=intent["publisher_id"],
                             package_id=intent["package_id"]),
                package_version=intent["package_version"],
                content_digest=intent["content_digest"],
            )
            staging_file = Path(intent["staging"])
            target = Path(intent["target"])
            published = target.is_file()
            committed_record = self.get_revision(rk)
            committed = committed_record is not None
            if published and not committed:
                # rename survived, catalog commit did not: finish it
                self._commit_install(intent, rk)
                recovered += 1
            elif committed and not published:
                # The catalog commit survived but the published media did
                # not.  The already-validated staging file is the sole good
                # copy: restore it to the canonical target before cleanup.
                self._recheck_action_paths(
                    staging_file.parent, staging_file, target.parent, target)
                target.parent.mkdir(parents=True, exist_ok=True)
                self._recheck_action_paths(
                    staging_file.parent, staging_file, target.parent, target)
                shutil.move(str(staging_file), str(target))
                self._cleanup_staging_copy(
                    staging_file.parent, staging_file)
                # Re-read and validate the actual published bytes after the
                # move, closing the last race before catalog reconciliation.
                self._validated_intent(intent_path)
                with self._db:
                    self._db.execute(
                        "UPDATE revisions SET pack_path=?,"
                        " character_count=?, trust_channel=?, builtin=0"
                        " WHERE publisher_id=? AND package_id=?"
                        " AND package_version=? AND content_digest=?",
                        (str(target), intent["character_count"],
                         "LOCAL_IMPORTED", *self._row_of(rk)),
                    )
                self._ensure_install_event(
                    intent, rk, committed_record.installed_at)
                self._delete_intent(intent["transaction_id"])
                recovered += 1
            elif committed:
                # The catalog+media landed.  Compensate an event-append crash
                # before deleting the durable recovery intent.
                self._ensure_install_event(
                    intent, rk, committed_record.installed_at)
                self._cleanup_staging_copy(
                    staging_file.parent, staging_file)
                self._delete_intent(intent["transaction_id"])
            else:
                # nothing published: roll back the staging copy
                if staging_file.exists():
                    self._cleanup_staging_copy(
                        staging_file.parent, staging_file)
                else:
                    raise LifecycleError(
                        "PPK-LCY-E001",
                        "recovery media disappeared after validation")
                self._delete_intent(intent["transaction_id"])
            recovered += 1 if not intent_path.exists() else 0
        return recovered

    # -- queries ------------------------------------------------------------------

    def get_revision(self, rk: RevisionKey) -> RevisionRecord | None:
        row = self._db.execute(
            "SELECT pack_path, installed_at, character_count, trust_channel,"
            " builtin FROM revisions WHERE publisher_id=? AND package_id=? AND"
            " package_version=? AND content_digest=?",
            self._row_of(rk),
        ).fetchone()
        if row is None:
            return None
        return RevisionRecord(
            revision_key=rk, pack_path=Path(row[0]), installed_at=row[1],
            character_count=row[2], trust_channel=row[3], builtin=bool(row[4]))

    def list_revisions(self) -> list[RevisionRecord]:
        out = []
        for row in self._db.execute(
                "SELECT publisher_id, package_id, package_version,"
                " content_digest, pack_path, installed_at, character_count,"
                " trust_channel, builtin FROM revisions ORDER BY installed_at"):
            rk = RevisionKey(
                pack=PackKey(row[0], row[1]), package_version=row[2],
                content_digest=row[3])
            out.append(RevisionRecord(
                revision_key=rk, pack_path=Path(row[4]), installed_at=row[5],
                character_count=row[6], trust_channel=row[7],
                builtin=bool(row[8])))
        return out

    # -- uninstall (non-active revisions only in M4) -----------------------------

    def uninstall_revision(self, rk: RevisionKey,
                           *, active_guard=None) -> bool:
        """Move one revision to trash (atomically) + tombstone event.

        ``active_guard`` (M5) is a callable returning True when this revision
        is the ACTIVE selection; uninstalling the active revision without a
        safe switch is refused (PPK-LCY-E005).
        """
        record = self.get_revision(rk)
        if record is None:
            return False
        if record.builtin or self.is_builtin(rk):
            # EmbeddedOfficialPack: read-only release media, never removable
            raise LifecycleError("PPK-LCY-E005",
                                 "refusing to uninstall a builtin official"
                                 " revision")
        if active_guard is not None and active_guard(rk):
            raise LifecycleError("PPK-LCY-E005",
                                 "refusing to uninstall the active revision")
        target = self._path_of(rk)
        trash_target = self.trash_dir / target.name
        if trash_target.exists():
            self._recheck_action_paths(
                self.trash_dir, trash_target)
            shutil.rmtree(trash_target, ignore_errors=True)
        if target.is_dir():
            self._recheck_action_paths(
                target.parent, target, self.trash_dir, trash_target)
            shutil.move(str(target), str(trash_target))
        elif target.is_file():
            self._recheck_action_paths(
                target.parent, target, self.trash_dir, trash_target)
            shutil.move(str(target), str(trash_target))
        with self._db:
            self._db.execute(
                "DELETE FROM revisions WHERE publisher_id=? AND package_id=?"
                " AND package_version=? AND content_digest=?",
                self._row_of(rk))
        self._append_event(LifecycleEvent(
            "UNINSTALL_TRASHED", str(rk),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            {"trash": trash_target.name}))
        return True

    # -- receipts -----------------------------------------------------------------

    def write_receipt(self, pack_path: Path, *, trust_channel: str,
                      warnings_acknowledged: tuple[str, ...] = ()) -> dict:
        """Engine-generated immutable validation receipt (PETPACK_SPEC 20)."""
        data = Path(pack_path).read_bytes()
        report = validate_petpack(data, trust_channel=trust_channel)
        if not report.accepted:
            raise LifecycleError("PPK-LCY-E001", "pack not acceptable")
        warning_codes = tuple(sorted({
            d.code for d in report.diagnostics
            if getattr(d.severity, "value", d.severity) == "WARNING"
        }))
        acknowledged = tuple(sorted(set(warnings_acknowledged)))
        if acknowledged != warning_codes:
            raise LifecycleError(
                "PPK-LCY-E003",
                "validation warnings were not exactly acknowledged")
        return self._write_receipt_data(
            data, report,
            trust_channel=trust_channel,
            warnings_acknowledged=acknowledged,
        )

    def _write_receipt_data(self, data: bytes, report, *,
                            trust_channel: str,
                            warnings_acknowledged: tuple[str, ...],
                            archive_sha256: str | None = None) -> dict:
        """Write a receipt for the exact already-validated byte snapshot."""
        # The snapshot has already passed the hostile archive validator.  Read
        # only the bounded manifest member here; constructing PetpackArchive a
        # second time would redundantly materialize every media file on the GUI
        # commit path.
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                manifest_bytes = archive.read("petpack.json")
        except (zipfile.BadZipFile, KeyError, OSError) as exc:
            raise LifecycleError(
                "PPK-LCY-E003", "validated manifest snapshot is unavailable"
            ) from exc
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        manifest = report.manifest
        publishers = [
            {
                "id": str(item.get("id", "")),
            }
            for item in manifest.get("publishers", [])
            if isinstance(item, dict)
        ]
        rights = [
            {
                "id": str(item.get("id", "")),
                "basis": str(item.get("basis", "")),
                "claimant_ref": str(item.get("claimant_ref", "")),
                "scope_claimed": sorted(
                    str(value) for value in item.get("scope_claimed", [])
                    if isinstance(value, str)),
            }
            for item in manifest.get("rights_declarations", [])
            if isinstance(item, dict)
        ]
        receipt = {
            "receipt_version": 1,
            "install_id": uuid.uuid4().hex,
            "publisher_id": report.pack_key.publisher_id,
            "package_id": report.pack_key.package_id,
            "package_version": report.revision_key.package_version,
            "content_digest": report.content_digest,
            "archive_sha256": archive_sha256 or hashlib.sha256(data).hexdigest(),
            "manifest_sha256": manifest_sha,
            "engine_version": __import__("retirement_pet").__version__,
            "validator_version": "petpack-1.0",
            "validated_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "import_mode": "local_file",
            "trust_channel": trust_channel,
            "publisher_verification_status": (
                "VERIFIED_BY_DISTRIBUTION" if trust_channel == "BUILTIN_OFFICIAL"
                else "UNVERIFIED"),
            "declared_publisher_snapshot": publishers,
            "rights_snapshot": rights,
            "warnings_acknowledged": list(warnings_acknowledged),
        }
        path = self.receipts_dir / f"{receipt['install_id']}.json"
        tmp = path.with_suffix(".tmp")
        self._recheck_action_paths(self.receipts_dir, path, tmp)
        payload = json.dumps(
            receipt, ensure_ascii=False, indent=1).encode("utf-8")
        try:
            receipt_handle = open(tmp, "xb")
        except OSError as exc:
            raise LifecycleError(
                "PPK-LCY-E001", "receipt temp file cannot be created") from exc
        try:
            with receipt_handle:
                self._verify_open_managed_file(receipt_handle, tmp)
                receipt_handle.write(payload)
                receipt_handle.flush()
                os.fsync(receipt_handle.fileno())
            self._recheck_action_paths(self.receipts_dir, path, tmp)
            # The UUID destination is unique.  Windows rename is atomic and
            # refuses an existing destination, so readers see all or nothing.
            tmp.rename(path)
        except Exception:
            try:
                self._recheck_action_paths(self.receipts_dir, tmp)
                tmp.unlink(missing_ok=True)
            except (LifecycleError, OSError):
                pass
            raise
        return receipt
