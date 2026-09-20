"""Verified backup and restore for the Todo SQLite store.

Every backup is a directory under ``<db parent>/backups`` named
``todo-backup-<UTC>-<kind>-<rand>`` and contains the database file plus a
``manifest.json`` recording the schema version and backup time.  Snapshots
are taken with SQLite's online backup API from either the live repository
connection or a fresh read-only connection - never by copying a live file
family by hand.

Retention is bounded and only ever touches directories whose manifest
proves this application owns them for THIS database (exact app name,
database name, known kind, parseable timestamp and typed size/hash/task
metadata) inside the module-owned backups directory.  Entries whose
ownership cannot be proven are kept and reported, never deleted.  The
newest pre-migration backup is always kept so a failed or abandoned
migration can always be rolled back to a compatible v1 store.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from retirement_pet.todo.errors import TaskStoreUnavailable

logger = logging.getLogger(__name__)

BACKUP_DIR_NAME = "backups"
BACKUP_PREFIX = "todo-backup-"
RETENTION_LIMIT = 8
_MANIFEST_NAME = "manifest.json"
_FORMAT_VERSION = 1
_KINDS = ("pre-migration", "manual", "pre-restore")
_OWNED_APP = "retirement-pet"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_COPY_CHUNK_BYTES = 1024 * 1024
_REPARSE_POINT = 0x400


class BackupError(RuntimeError):
    """Stable, coarse backup/restore failure (no user data in messages)."""

    def __init__(self, reason: str):
        self.reason = str(reason)
        super().__init__(f"todo backup operation failed ({self.reason})")


@dataclass(frozen=True)
class BackupRecord:
    directory: Path
    kind: str
    created_at: str
    schema_version: int
    sha256: str
    size: int
    task_count: int


@dataclass(frozen=True)
class BackupSummary:
    """Best-effort view of one on-disk backup for listings."""

    directory: Path
    kind: str | None
    created_at: str | None
    schema_version: int | None
    sha256: str | None
    size: int | None
    task_count: int | None
    valid: bool


@dataclass(frozen=True)
class RestoreResult:
    restored_version: int
    task_count: int
    preserved_backup: BackupRecord | None
    # Distinguishes the two successful-restore outcomes (review CR-A05):
    # True when the reopened store is fully usable, False (with
    # ``unavailable_reason``) when the on-disk data WAS restored but the
    # module had to enter its write-forbidden state because the fresh
    # open of the replaced store failed.
    store_available: bool = True
    unavailable_reason: str | None = None


def backups_dir_for(db_path: Path) -> Path:
    return Path(db_path).parent / BACKUP_DIR_NAME


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_app_backup_dir(path: Path) -> bool:
    """True only for directories this module created in its own directory."""
    if not path.name.startswith(BACKUP_PREFIX):
        return False
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0) & _REPARSE_POINT):
        return False
    return stat.S_ISDIR(info.st_mode)


def list_backups(db_path: Path) -> list[BackupSummary]:
    """App-owned backups for this store, newest valid first.

    Entries whose manifest does not prove ownership by this application
    for this database are skipped (and reported); they are never listed
    as backups and never touched by retention.
    """
    directory = backups_dir_for(db_path)
    if not directory.is_dir():
        return []
    db_name = Path(db_path).name
    summaries: list[BackupSummary] = []
    for entry in sorted(directory.iterdir()):
        if not _is_app_backup_dir(entry):
            continue
        manifest, error = _owned_manifest(entry, db_name)
        if manifest is None:
            logger.error(
                "todo backup entry kept with unverified ownership (%s)",
                error or "unknown")
            summaries.append(BackupSummary(
                directory=entry, kind=None, created_at=None,
                schema_version=None, sha256=None, size=None,
                task_count=None, valid=False))
            continue
        db_file = entry / db_name
        size = manifest.get("size")
        sha256 = manifest.get("sha256")
        matches = False
        if isinstance(size, int) and isinstance(sha256, str):
            try:
                matches = _hash_file(db_file) == (sha256, size)
            except (BackupError, OSError):
                matches = False
        summaries.append(BackupSummary(
            directory=entry,
            kind=_string_or_none(manifest.get("kind")),
            created_at=_string_or_none(manifest.get("created_at")),
            schema_version=(manifest["schema_version"]
                            if isinstance(manifest.get("schema_version"), int)
                            else None),
            sha256=sha256,
            size=size if isinstance(size, int) else None,
            task_count=(manifest["task_count"]
                        if isinstance(manifest.get("task_count"), int)
                        else None),
            valid=matches,
        ))
    # Newest valid first; entries with unusable manifests keep their place
    # at the end (stable two-pass sort).
    summaries.sort(key=lambda summary: summary.created_at or "", reverse=True)
    summaries.sort(key=lambda summary: 0 if summary.valid else 1)
    return summaries


def _string_or_none(value) -> str | None:
    return value if isinstance(value, str) else None


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _read_manifest(directory: Path) -> tuple[dict | None, str | None]:
    manifest_path = directory / _MANIFEST_NAME
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return None, "manifest_unreadable"
    try:
        manifest = json.loads(raw)
    except ValueError:
        return None, "manifest_invalid_json"
    if not isinstance(manifest, dict) or manifest.get("format") != _FORMAT_VERSION:
        return None, "manifest_unsupported"
    return manifest, None


def _owned_manifest(directory: Path,
                    db_name: str) -> tuple[dict | None, str | None]:
    """Full ownership proof for a candidate retention entry.

    A directory may be deleted by retention only when its manifest proves
    this application created it for THIS database: exact app name, exact
    database file name (no path separators can hide inside an exact name
    match), a known kind, a parseable UTC timestamp and typed size/hash/
    task-count metadata.  Anything else is "ownership unproven" and must
    never be removed, however well-formed it looks.
    """
    manifest, error = _read_manifest(directory)
    if manifest is None:
        return None, error
    if manifest.get("app") != _OWNED_APP:
        return None, "foreign_app"
    if manifest.get("database") != db_name:
        return None, "foreign_database"
    if manifest.get("kind") not in _KINDS:
        return None, "unknown_kind"
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str):
        return None, "invalid_created_at"
    try:
        datetime.fromisoformat(created_at)
    except ValueError:
        return None, "invalid_created_at"
    schema_version = manifest.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) \
            or schema_version < 1:
        return None, "invalid_schema_version"
    sha256 = manifest.get("sha256")
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        return None, "invalid_sha256"
    size = manifest.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return None, "invalid_size"
    task_count = manifest.get("task_count")
    if not isinstance(task_count, int) or isinstance(task_count, bool) \
            or task_count < 0:
        return None, "invalid_task_count"
    return manifest, None


def _fsync_file(path: Path) -> None:
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _snapshot_into(source: sqlite3.Connection, target: Path) -> None:
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()


def _verify_snapshot(db_file: Path) -> tuple[int, int]:
    """Full shape + integrity + FK verification of a standalone store file."""
    from retirement_pet.todo.repository import TaskRepository

    try:
        return TaskRepository.inspect_snapshot(db_file)
    except TaskStoreUnavailable as exc:
        raise BackupError("snapshot_invalid") from exc


def create_backup(db_path: Path, kind: str, *,
                  source_connection: sqlite3.Connection | None = None,
                  backups_dir: Path | None = None,
                  prune: bool = True,
                  protected: frozenset[Path] = frozenset()) -> BackupRecord:
    """Create one verified backup; on any failure leave nothing behind.

    ``source_connection`` (used by the migration path) guarantees a
    consistent read of the exact store the caller has open.  Without it a
    fresh read-only connection snapshots the current on-disk store.
    ``protected`` directories are never removed by the retention prune
    that follows publication.
    """
    db_path = Path(os.path.abspath(os.fspath(db_path)))
    if kind not in _KINDS:
        raise ValueError(f"unknown backup kind: {kind}")
    directory = backups_dir or backups_dir_for(db_path)
    if source_connection is None and not os.path.lexists(db_path):
        raise BackupError("store_missing")
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError("backup_dir_unavailable") from exc

    staging = directory / f".staging-{uuid.uuid4().hex}"
    try:
        try:
            staging.mkdir(exist_ok=False)
        except OSError as exc:
            raise BackupError("backup_dir_unavailable") from exc
        target = staging / db_path.name
        try:
            if source_connection is not None:
                _snapshot_into(source_connection, target)
            else:
                source = sqlite3.connect(
                    f"{db_path.as_uri()}?mode=ro", uri=True)
                try:
                    _snapshot_into(source, target)
                finally:
                    source.close()
        except (sqlite3.Error, OSError) as exc:
            raise BackupError("snapshot_failed") from exc
        try:
            _fsync_file(target)
        except OSError as exc:
            raise BackupError("snapshot_failed") from exc
        try:
            schema_version, task_count = _verify_snapshot(target)
        except BackupError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise BackupError("verify_failed") from exc
        try:
            sha256, size = _hash_file(target)
        except OSError as exc:
            raise BackupError("verify_failed") from exc

        created = _utc_now()
        manifest = {
            "format": _FORMAT_VERSION,
            "app": "retirement-pet",
            "kind": kind,
            # microsecond precision: second-resolution timestamps made two
            # backups created in the same wall-clock second sort ambiguous
            # (newest-first ordering drives retention pruning)
            "created_at": created.isoformat(timespec="microseconds"),
            "schema_version": schema_version,
            "database": db_path.name,
            "sha256": sha256,
            "size": size,
            "task_count": task_count,
        }
        try:
            (staging / _MANIFEST_NAME).write_text(
                json.dumps(manifest, indent=1, sort_keys=True),
                encoding="utf-8")
            _fsync_file(staging / _MANIFEST_NAME)
        except OSError as exc:
            raise BackupError("manifest_write_failed") from exc

        published = directory / (
            f"{BACKUP_PREFIX}{created.strftime('%Y%m%dT%H%M%SZ')}"
            f"-{kind}-{uuid.uuid4().hex[:8]}")
        try:
            staging.rename(published)
            _fsync_dir(directory)
        except OSError as exc:
            raise BackupError("publish_failed") from exc
    except BackupError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise BackupError("backup_dir_unavailable") from exc

    if prune:
        try:
            prune_backups(db_path, backups_dir=directory,
                          protected=protected)
        except OSError:
            # Retention is housekeeping; a failed prune never invalidates
            # the backup that was just written.
            logger.error("todo backup retention prune failed")
    return BackupRecord(
        directory=published,
        kind=kind,
        created_at=manifest["created_at"],
        schema_version=schema_version,
        sha256=sha256,
        size=size,
        task_count=task_count,
    )


def prune_backups(db_path: Path, *, backups_dir: Path | None = None,
                  limit: int = RETENTION_LIMIT,
                  protected: frozenset[Path] = frozenset()
                  ) -> list[Path]:
    """Bound retention over app-owned backups only.

    Keeps at most ``limit`` backups and always preserves the newest usable
    pre-migration copy within that cap, so an interrupted migration can
    always be rolled back to a compatible v1 store.  Only entries whose
    manifest proves ownership by this application for this database are
    ever deleted; anything else is kept and reported.  Directories listed
    in ``protected`` (e.g. the input of an in-flight restore) are excluded
    from both the keep accounting and removal this round.
    """
    directory = backups_dir or backups_dir_for(db_path)
    summaries = [summary for summary in list_backups(db_path)
                 if summary.directory.parent == directory
                 and summary.kind is not None
                 and summary.directory not in protected]
    keep: list[BackupSummary] = []
    for summary in summaries:  # newest -> oldest
        if len(keep) < limit:
            keep.append(summary)
            continue
        preserve_kind = (summary.kind == "pre-migration" and summary.valid
                         and not any(kept.kind == "pre-migration" and kept.valid
                                     for kept in keep))
        if preserve_kind:
            # Guarantee the pre-migration copy inside the cap by dropping
            # the oldest kept entry (keep is ordered newest -> oldest).
            keep.pop()
            keep.append(summary)
    keep_dirs = {kept.directory for kept in keep}
    removed: list[Path] = []
    for summary in summaries:
        if summary.directory in keep_dirs:
            continue
        try:
            shutil.rmtree(summary.directory)
        except OSError:
            logger.error("todo backup retention entry removal failed")
            continue
        removed.append(summary.directory)
    if removed:
        _fsync_dir(directory)
    return removed


def restore_backup(db_path: Path, backup_directory: Path) -> RestoreResult:
    """Restore a verified backup, preserving the current store first.

    The caller must have closed any live connection to ``db_path``.  The
    candidate is hash-checked against its manifest and fully re-verified as
    a standalone store before anything on disk changes; the current store is
    backed up (``pre-restore``) before the first destructive step, and the
    replacement itself is a single same-volume rename.  The candidate
    directory is protected from the retention prune of that pre-restore
    backup, so a restore can never destroy its own input, whatever the
    current retention pressure is.
    """
    db_path = Path(os.path.abspath(os.fspath(db_path)))
    backup_directory = Path(os.path.abspath(os.fspath(backup_directory)))
    manifest, error = _read_manifest(backup_directory)
    if manifest is None:
        raise BackupError(error or "manifest_unreadable")
    candidate = backup_directory / db_path.name
    if not candidate.is_file():
        raise BackupError("backup_content_missing")
    sha256 = manifest.get("sha256")
    size = manifest.get("size")
    if not isinstance(sha256, str) or not isinstance(size, int):
        raise BackupError("manifest_invalid")
    try:
        actual_sha, actual_size = _hash_file(candidate)
    except OSError as exc:
        raise BackupError("backup_content_missing") from exc
    if (actual_sha, actual_size) != (sha256, size):
        raise BackupError("backup_content_mismatch")
    restored_version, task_count = _verify_snapshot(candidate)

    preserved: BackupRecord | None = None
    protected = frozenset({backup_directory})
    if os.path.lexists(db_path):
        preserved = create_backup(db_path, "pre-restore",
                                  protected=protected)

    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(f"{db_path}{suffix}"):
            raise BackupError("store_not_quiesced")

    replaced = False
    partial = db_path.with_name(
        f".{db_path.name}.restore-partial-{uuid.uuid4().hex}")
    try:
        digest = hashlib.sha256()
        with open(candidate, "rb") as reader, open(partial, "xb") as writer:
            while chunk := reader.read(_COPY_CHUNK_BYTES):
                digest.update(chunk)
                writer.write(chunk)
        if digest.hexdigest() != sha256:
            raise BackupError("restore_copy_mismatch")
        _fsync_file(partial)
        verified_version, verified_count = _verify_snapshot(partial)
        if (verified_version, verified_count) != (restored_version, task_count):
            raise BackupError("restore_copy_mismatch")
        os.replace(partial, db_path)
        replaced = True
        _fsync_file(db_path)
        _fsync_dir(db_path.parent)
    except BackupError:
        partial.unlink(missing_ok=True)
        raise
    except OSError as exc:
        partial.unlink(missing_ok=True)
        # Once ``os.replace`` has completed the on-disk store already holds
        # the restored content; only the durability barrier failed.  That
        # state must stay distinguishable from a pre-publish failure so the
        # caller never claims "current store unchanged" and reuses stale
        # in-memory state.
        raise BackupError(
            "restore_publish_unknown" if replaced
            else "restore_publish_failed") from exc

    return RestoreResult(
        restored_version=restored_version,
        task_count=task_count,
        preserved_backup=preserved,
    )


__all__ = [
    "BACKUP_DIR_NAME",
    "BACKUP_PREFIX",
    "RETENTION_LIMIT",
    "BackupError",
    "BackupRecord",
    "BackupSummary",
    "RestoreResult",
    "backups_dir_for",
    "create_backup",
    "list_backups",
    "prune_backups",
    "restore_backup",
]
