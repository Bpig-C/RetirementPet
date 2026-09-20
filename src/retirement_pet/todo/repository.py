"""Fail-closed SQLite repository for the user-global TodoModule store.

The repository accepts exactly three on-disk states: no SQLite family at
all (creates a fresh v2 store), the exact v1 schema (auto-migrates to v2
after a verified pre-migration backup succeeds), or the exact v2 schema.
Every other state (including an empty SQLite file, corruption, foreign
tables and newer schemas) is preserved and reported as unavailable.  A hot
rollback journal is verified on an isolated copy before SQLite is allowed
to recover the live store.

If the pre-migration backup cannot be produced, the store stays untouched
and closed: backup failure must never lead to migrating an unbacked store.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

from retirement_pet.todo import migrations
from retirement_pet.todo import backup as backup_module
from retirement_pet.todo.domain import Horizon, Level, Status, Task, TodoError
from retirement_pet.todo.errors import SchemaTooNew, TaskStoreUnavailable

logger = logging.getLogger(__name__)

_REPARSE_POINT = 0x400
_KEEP_FOCUS = object()
_COPY_CHUNK_BYTES = 1024 * 1024
_ROLLBACK_JOURNAL_MAGIC = bytes.fromhex("d9d505f920a163d7")

_OBJECTS_BY_VERSION = {
    1: {
        ("table", "meta"),
        ("table", "tasks"),
        ("table", "focus"),
        ("index", "idx_tasks_parent"),
        ("index", "idx_tasks_status_horizon"),
        ("index", "idx_tasks_due"),
    },
    2: {
        ("table", "meta"),
        ("table", "tasks"),
        ("table", "focus"),
        ("index", "idx_tasks_parent"),
        ("index", "idx_tasks_status_horizon"),
        ("index", "idx_tasks_due"),
        ("index", "idx_tasks_archived"),
    },
}

_V1_TASK_COLUMNS = (
    ("id", "TEXT", 0, None, 1, 0),
    ("parent_id", "TEXT", 0, None, 0, 0),
    ("title", "TEXT", 1, None, 0, 0),
    ("horizon", "TEXT", 1, None, 0, 0),
    ("status", "TEXT", 1, None, 0, 0),
    ("sort_key", "INTEGER", 1, "0", 0, 0),
    ("due_date", "TEXT", 0, None, 0, 0),
    ("created_at", "TEXT", 1, None, 0, 0),
    ("updated_at", "TEXT", 1, None, 0, 0),
    ("completed_at", "TEXT", 0, None, 0, 0),
)
_V2_TASK_COLUMNS = _V1_TASK_COLUMNS + (
    ("importance", "TEXT", 0, None, 0, 0),
    ("urgency", "TEXT", 0, None, 0, 0),
    ("archived", "INTEGER", 1, "0", 0, 0),
    ("archived_at", "TEXT", 0, None, 0, 0),
    ("note", "TEXT", 1, "''", 0, 0),
)

# name, declared type, not-null, default SQL, primary-key order, hidden
_EXPECTED_COLUMNS_BY_VERSION = {
    1: {
        "meta": (
            ("key", "TEXT", 0, None, 1, 0),
            ("value", "TEXT", 1, None, 0, 0),
        ),
        "tasks": _V1_TASK_COLUMNS,
        "focus": (
            ("id", "INTEGER", 0, None, 1, 0),
            ("task_id", "TEXT", 1, None, 0, 0),
            ("started_at", "TEXT", 1, None, 0, 0),
        ),
    },
    2: {
        "meta": (
            ("key", "TEXT", 0, None, 1, 0),
            ("value", "TEXT", 1, None, 0, 0),
        ),
        "tasks": _V2_TASK_COLUMNS,
        "focus": (
            ("id", "INTEGER", 0, None, 1, 0),
            ("task_id", "TEXT", 1, None, 0, 0),
            ("started_at", "TEXT", 1, None, 0, 0),
        ),
    },
}

_EXPECTED_FOREIGN_KEYS = {
    "meta": (),
    "tasks": (("tasks", "parent_id", "id", "NO ACTION", "NO ACTION", "NONE"),),
    "focus": (("tasks", "task_id", "id", "NO ACTION", "NO ACTION", "NONE"),),
}

_INDEXES_BY_VERSION = {
    1: {
        "idx_tasks_parent": (("parent_id", 0, "BINARY"),),
        "idx_tasks_status_horizon": (
            ("status", 0, "BINARY"),
            ("horizon", 0, "BINARY"),
        ),
        "idx_tasks_due": (("due_date", 0, "BINARY"),),
    },
    2: {
        "idx_tasks_parent": (("parent_id", 0, "BINARY"),),
        "idx_tasks_status_horizon": (
            ("status", 0, "BINARY"),
            ("horizon", 0, "BINARY"),
        ),
        "idx_tasks_due": (("due_date", 0, "BINARY"),),
        "idx_tasks_archived": (("archived", 0, "BINARY"),),
    },
}

# meta keys required per version, in sorted key order
_META_KEYS_BY_VERSION = {
    1: [("schema_version",)],
    2: [("established_at",), ("schema_version",)],
}


@dataclass(frozen=True)
class _FileStamp:
    device: int
    inode: int
    size: int
    mtime_ns: int
    sha256: str | None = None


def _to_iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


class TaskRepository:
    """Single-connection task store with strict preflight and live validation."""

    def __init__(self, db_path: Path, *,
                 connection_factory: Callable[..., sqlite3.Connection] | None = None,
                 protected_backups: frozenset[Path] = frozenset()):
        self.path = Path(os.path.abspath(os.fspath(db_path)))
        # Directories (e.g. an in-flight restore input) that the migration
        # pre-backup's retention prune must never remove.
        self._protected_backups = frozenset(
            Path(os.path.abspath(os.fspath(p))) for p in protected_backups)
        self._db: sqlite3.Connection | None = None
        self._connect = connection_factory or sqlite3.connect
        self._open()

    # -- lifecycle and safety boundary -------------------------------------

    def _open(self) -> None:
        try:
            expected = self._capture_group()
            if self.path not in expected:
                if expected:
                    raise TaskStoreUnavailable(
                        "orphan_sidecar", stage="preflight")
                self._create_and_publish()
                return

            sidecars = set(expected) - {self.path}
            if sidecars:
                kind, expected = self._inspect_sidecar_snapshot(expected)
            else:
                kind = self._inspect_main_only(expected)
            if kind == 0:
                raise TaskStoreUnavailable(
                    "empty_store_unsupported", stage="preflight")
            if kind not in migrations.SUPPORTED_VERSIONS:
                raise TaskStoreUnavailable(
                    "unsupported_schema", stage="preflight")
            self._open_existing(
                expected,
                kind,
                recover_hot_journal=Path(f"{self.path}-journal") in expected,
            )
            if kind == 1:
                self._backup_and_migrate_live_v1()
        except (SchemaTooNew, TaskStoreUnavailable):
            raise
        except sqlite3.DatabaseError as exc:
            raise self._unavailable(exc, "preflight") from exc
        except OSError as exc:
            raise TaskStoreUnavailable(
                "filesystem_unavailable", stage="preflight") from exc

    def _backup_and_migrate_live_v1(self) -> None:
        """Back up the verified v1 store, then migrate it to v2 in place.

        A failed backup leaves the store closed and untouched: this build
        never migrates a store it could not first preserve.  A failed
        migration rolls its transaction back, which always leaves the exact
        pre-migration v1 bytes on disk.
        """
        try:
            backup_module.create_backup(
                self.path, "pre-migration", source_connection=self._db,
                protected=self._protected_backups)
        except backup_module.BackupError as exc:
            self._close_failed(self._db)
            self._db = None
            raise TaskStoreUnavailable(
                "backup_failed", stage="migrate") from exc
        db = self._db
        try:
            db.execute("PRAGMA foreign_keys=OFF")
            if db.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
                raise sqlite3.DatabaseError("foreign key controls unavailable")
            db.execute("BEGIN IMMEDIATE")
            if self._inspect_database(db) != 1:
                raise TaskStoreUnavailable(
                    "schema_changed_before_migration", stage="migrate")
            migrations.migrate_v1_to_v2(db)
            if self._inspect_database(db) != migrations.SCHEMA_VERSION:
                raise TaskStoreUnavailable(
                    "migrated_schema_invalid", stage="migrate")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise TaskStoreUnavailable(
                    "migrated_foreign_keys", stage="migrate")
            db.commit()
            db.execute("PRAGMA foreign_keys=ON")
            if db.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise sqlite3.DatabaseError(
                    "foreign key enforcement unavailable")
            if self._inspect_database(
                    db, full_integrity=True) != migrations.SCHEMA_VERSION:
                raise TaskStoreUnavailable(
                    "migrated_schema_invalid", stage="migrate")
            logger.info("todo store migrated to schema %s",
                        migrations.SCHEMA_VERSION)
        except (SchemaTooNew, TaskStoreUnavailable):
            self._abort_migration(db)
            raise
        except sqlite3.DatabaseError as exc:
            self._abort_migration(db)
            raise self._unavailable(exc, "migrate") from exc
        except OSError as exc:
            self._abort_migration(db)
            raise TaskStoreUnavailable(
                "migration_failed", stage="migrate") from exc
        except Exception as exc:
            # Any unexpected failure must still leave the exact pre-migration
            # v1 store on disk with no locks held.
            self._abort_migration(db)
            raise TaskStoreUnavailable(
                "migration_failed", stage="migrate") from exc

    def _abort_migration(self, db: sqlite3.Connection) -> None:
        try:
            if db.in_transaction:
                db.rollback()
        except sqlite3.Error:
            pass
        try:
            db.execute("PRAGMA foreign_keys=ON")
        except sqlite3.Error:
            pass
        self._close_failed(db)
        self._db = None

    @staticmethod
    def _unavailable(exc: BaseException,
                     stage: str) -> TaskStoreUnavailable:
        code = getattr(exc, "sqlite_errorcode", None)
        name = getattr(exc, "sqlite_errorname", None)
        logger.error("task store unavailable stage=%s code=%s name=%s",
                     stage, code, name)
        return TaskStoreUnavailable(
            "sqlite_error",
            stage=stage,
            sqlite_code=int(code) if isinstance(code, int) else None,
            sqlite_name=str(name) if name is not None else None,
        )

    def _members(self) -> tuple[Path, Path, Path, Path]:
        return (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
            Path(f"{self.path}-journal"),
        )

    def _assert_parent_safe(self) -> None:
        parent = self.path.parent
        anchor = Path(parent.anchor)
        current = anchor
        try:
            relative_parts = parent.relative_to(anchor).parts
        except ValueError as exc:
            raise TaskStoreUnavailable(
                "unsafe_parent", stage="path") from exc
        for part in relative_parts:
            current = current / part
            if not _lexists(current):
                raise TaskStoreUnavailable(
                    "missing_parent", stage="path")
            info = os.lstat(current)
            if _is_reparse(info):
                raise TaskStoreUnavailable(
                    "reparse_parent", stage="path")
        info = os.lstat(parent)
        if not stat.S_ISDIR(info.st_mode):
            raise TaskStoreUnavailable("unsafe_parent", stage="path")
        if os.name == "nt" and info.st_nlink != 1:
            raise TaskStoreUnavailable("linked_parent", stage="path")

    @staticmethod
    def _assert_regular_unlinked(path: Path) -> os.stat_result:
        info = os.lstat(path)
        if _is_reparse(info) or not stat.S_ISREG(info.st_mode):
            raise TaskStoreUnavailable("unsafe_member", stage="path")
        if info.st_nlink != 1:
            raise TaskStoreUnavailable("linked_member", stage="path")
        return info

    @classmethod
    def _metadata(cls, path: Path) -> _FileStamp:
        before = cls._assert_regular_unlinked(path)
        with open(path, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not os.path.samestat(before, opened) or opened.st_nlink != 1:
                raise TaskStoreUnavailable(
                    "member_identity_changed", stage="path")
            finished = os.fstat(handle.fileno())
        after = os.lstat(path)
        if (not os.path.samestat(before, finished)
                or not os.path.samestat(before, after)
                or finished.st_nlink != 1
                or after.st_nlink != 1
                or finished.st_size != before.st_size
                or finished.st_mtime_ns != before.st_mtime_ns
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns):
            raise TaskStoreUnavailable(
                "member_changed_during_read", stage="path")
        return _FileStamp(
            device=int(before.st_dev),
            inode=int(before.st_ino),
            size=int(before.st_size),
            mtime_ns=int(before.st_mtime_ns),
        )

    @classmethod
    def _fingerprint(cls, path: Path) -> _FileStamp:
        before = cls._assert_regular_unlinked(path)
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not os.path.samestat(before, opened) or opened.st_nlink != 1:
                raise TaskStoreUnavailable(
                    "member_identity_changed", stage="path")
            while chunk := handle.read(_COPY_CHUNK_BYTES):
                digest.update(chunk)
            finished = os.fstat(handle.fileno())
        after = os.lstat(path)
        if (not os.path.samestat(before, finished)
                or not os.path.samestat(before, after)
                or finished.st_nlink != 1
                or after.st_nlink != 1
                or finished.st_size != before.st_size
                or finished.st_mtime_ns != before.st_mtime_ns
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns):
            raise TaskStoreUnavailable(
                "member_changed_during_read", stage="path")
        return _FileStamp(
            device=int(before.st_dev),
            inode=int(before.st_ino),
            size=int(before.st_size),
            mtime_ns=int(before.st_mtime_ns),
            sha256=digest.hexdigest(),
        )

    def _capture_group(self, *, full_hash: bool = False
                       ) -> dict[Path, _FileStamp]:
        self._assert_parent_safe()
        members = self._members()
        names_before = tuple(member for member in members if _lexists(member))
        inspect = self._fingerprint if full_hash else self._metadata
        captured = {member: inspect(member)
                    for member in names_before}
        names_after = tuple(member for member in members if _lexists(member))
        if names_after != names_before:
            raise TaskStoreUnavailable(
                "member_set_changed", stage="path")
        return captured

    @staticmethod
    def _same_metadata(left: dict[Path, _FileStamp],
                       right: dict[Path, _FileStamp]) -> bool:
        if set(left) != set(right):
            return False
        return all(
            (left[path].device, left[path].inode, left[path].size,
             left[path].mtime_ns)
            == (right[path].device, right[path].inode, right[path].size,
                right[path].mtime_ns)
            for path in left
        )

    @staticmethod
    def _readonly_uri(path: Path) -> str:
        return f"{path.as_uri()}?mode=ro&immutable=1"

    @classmethod
    def _copy_member(cls, source: Path, destination: Path,
                     expected: _FileStamp) -> None:
        if expected.sha256 is None:
            raise TaskStoreUnavailable(
                "snapshot_digest_missing", stage="snapshot")
        before = cls._assert_regular_unlinked(source)
        digest = hashlib.sha256()
        with open(source, "rb") as reader, open(destination, "xb") as writer:
            opened = os.fstat(reader.fileno())
            if (not os.path.samestat(before, opened)
                    or opened.st_dev != expected.device
                    or opened.st_ino != expected.inode
                    or opened.st_nlink != 1):
                raise TaskStoreUnavailable(
                    "member_identity_changed", stage="snapshot")
            while True:
                chunk = reader.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                writer.write(chunk)
            finished = os.fstat(reader.fileno())
        after = os.lstat(source)
        if (not os.path.samestat(before, finished)
                or not os.path.samestat(before, after)
                or finished.st_nlink != 1
                or after.st_nlink != 1
                or finished.st_size != expected.size
                or finished.st_mtime_ns != expected.mtime_ns
                or after.st_size != expected.size
                or after.st_mtime_ns != expected.mtime_ns
                or digest.hexdigest() != expected.sha256):
            raise TaskStoreUnavailable(
                "member_changed_during_copy", stage="snapshot")

    def _inspect_main_only(self,
                           expected: dict[Path, _FileStamp]) -> int:
        """Inspect ordinary stores without hashing or reading task rows."""
        if set(expected) != {self.path}:
            raise TaskStoreUnavailable(
                "unexpected_sidecar", stage="preflight")
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(
                self._readonly_uri(self.path), uri=True)
            self._verify_connection_identity(
                db, self.path, expected[self.path])
            version = self._inspect_database(db, full_integrity=False)
        finally:
            if db is not None:
                db.close()
        if not self._same_metadata(self._capture_group(), expected):
            raise TaskStoreUnavailable(
                "store_changed_during_inspection", stage="preflight")
        return version

    def _inspect_sidecar_snapshot(
            self, expected: dict[Path, _FileStamp]
            ) -> tuple[int, dict[Path, _FileStamp]]:
        """Validate WAL/hot-journal state on a writable isolated copy."""
        wal = Path(f"{self.path}-wal")
        shm = Path(f"{self.path}-shm")
        journal = Path(f"{self.path}-journal")
        if shm in expected and wal not in expected:
            raise TaskStoreUnavailable(
                "orphan_shm", stage="snapshot")
        if wal in expected and journal in expected:
            raise TaskStoreUnavailable(
                "ambiguous_journal_state", stage="snapshot")

        frozen = self._capture_group(full_hash=True)
        if not self._same_metadata(frozen, expected):
            raise TaskStoreUnavailable(
                "store_changed_before_snapshot", stage="snapshot")
        with tempfile.TemporaryDirectory(
                prefix="retirement-pet-task-inspect-") as raw:
            snapshot_dir = Path(raw)
            # SHM is only part of the source identity/fingerprint.  SQLite
            # rebuilds it from the copied main/WAL in this writable directory.
            copied = {self.path, wal, journal}
            for member, stamp in frozen.items():
                if member not in copied:
                    continue
                self._copy_member(
                    member, snapshot_dir / member.name, stamp)
            if self._capture_group(full_hash=True) != frozen:
                raise TaskStoreUnavailable(
                    "store_changed_during_snapshot", stage="snapshot")
            snapshot = snapshot_dir / self.path.name
            snapshot_journal = snapshot_dir / journal.name
            if journal in frozen:
                with open(snapshot_journal, "rb") as handle:
                    header = handle.read(len(_ROLLBACK_JOURNAL_MAGIC))
                if (frozen[journal].size <= 512
                        or header != _ROLLBACK_JOURNAL_MAGIC):
                    raise TaskStoreUnavailable(
                        "invalid_rollback_journal", stage="snapshot")
            db: sqlite3.Connection | None = None
            try:
                db = sqlite3.connect(snapshot)
                version = self._inspect_database(
                    db, full_integrity=True)
            finally:
                if db is not None:
                    db.close()
                if self._capture_group(full_hash=True) != frozen:
                    raise TaskStoreUnavailable(
                        "store_changed_during_inspection", stage="snapshot")
            if journal in frozen and _lexists(snapshot_journal):
                raise TaskStoreUnavailable(
                    "unrecoverable_rollback_journal", stage="snapshot")
            return version, frozen

    @staticmethod
    def _normalize_create_sql(sql: str) -> str:
        """Normalize formatting while preserving quoted literal contents."""
        output: list[str] = []
        quote: str | None = None
        index = 0
        while index < len(sql):
            char = sql[index]
            if quote is not None:
                output.append(char)
                if char == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        output.append(sql[index + 1])
                        index += 1
                    else:
                        quote = None
            elif char in {"'", '"', "`"}:
                quote = char
                output.append(char)
            elif char.isspace() or char == ";":
                pass
            else:
                output.append(char.casefold())
            index += 1
        return "".join(output).replace("ifnotexists", "")

    @classmethod
    def _inspect_database(cls, db: sqlite3.Connection, *,
                          full_integrity: bool = False) -> int:
        """Validate an open database and return its declared version."""
        object_rows = db.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%'").fetchall()
        objects = {(str(row[0]), str(row[1])) for row in object_rows}
        if not objects:
            if db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] != 0:
                raise TaskStoreUnavailable(
                    "foreign_schema", stage="schema")
            return 0
        if ("table", "meta") not in objects:
            raise TaskStoreUnavailable("foreign_schema", stage="schema")
        try:
            version = migrations.declared_version(db)
        except SchemaTooNew:
            raise
        except (ValueError, TypeError) as exc:
            raise TaskStoreUnavailable(
                "invalid_schema_version", stage="schema") from exc
        cls._validate_shape(db, version, objects)
        if full_integrity:
            check = db.execute("PRAGMA quick_check").fetchall()
            if check != [("ok",)]:
                raise TaskStoreUnavailable(
                    "integrity_check", stage="schema")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise TaskStoreUnavailable(
                    "foreign_key_check", stage="schema")
        return version

    @classmethod
    def _validate_shape(cls, db: sqlite3.Connection, version: int,
                        objects: set[tuple[str, str]]) -> None:
        """Check the exact schema shape declared for ``version``."""
        if objects != _OBJECTS_BY_VERSION[version]:
            raise TaskStoreUnavailable("foreign_schema", stage="schema")

        table_sql = ({1: migrations.V1_TABLE_SQL,
                      2: migrations.V2_TABLE_SQL})[version]
        for table, expected_sql in table_sql.items():
            row = db.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name=?", (table,)).fetchone()
            if (row is None or not isinstance(row[0], str)
                    or cls._normalize_create_sql(row[0])
                    != cls._normalize_create_sql(expected_sql)):
                raise TaskStoreUnavailable(
                    "invalid_table_constraints", stage="schema")

        meta_keys = db.execute("SELECT key FROM meta ORDER BY key").fetchall()
        if meta_keys != _META_KEYS_BY_VERSION[version]:
            raise TaskStoreUnavailable("invalid_meta", stage="schema")

        expected_columns_by_table = _EXPECTED_COLUMNS_BY_VERSION[version]
        for table, expected_columns in expected_columns_by_table.items():
            rows = db.execute(f'PRAGMA table_xinfo("{table}")').fetchall()
            columns = tuple(
                (str(row[1]), str(row[2]).upper(), int(row[3]), row[4],
                 int(row[5]), int(row[6]))
                for row in rows
            )
            if columns != expected_columns:
                raise TaskStoreUnavailable(
                    "invalid_table_shape", stage="schema")

            foreign_rows = db.execute(
                f'PRAGMA foreign_key_list("{table}")').fetchall()
            foreign_keys = tuple(sorted(
                (str(row[2]), str(row[3]), str(row[4]), str(row[5]),
                 str(row[6]), str(row[7]))
                for row in foreign_rows
            ))
            if foreign_keys != _EXPECTED_FOREIGN_KEYS[table]:
                raise TaskStoreUnavailable(
                    "invalid_foreign_keys", stage="schema")

        expected_indexes = _INDEXES_BY_VERSION[version]
        task_indexes = {
            str(row[1]): (int(row[2]), str(row[3]), int(row[4]))
            for row in db.execute('PRAGMA index_list("tasks")').fetchall()
            if not str(row[1]).startswith("sqlite_")
        }
        if set(task_indexes) != set(expected_indexes):
            raise TaskStoreUnavailable(
                "invalid_indexes", stage="schema")
        for name, expected_keys in expected_indexes.items():
            if task_indexes[name] != (0, "c", 0):
                raise TaskStoreUnavailable(
                    "invalid_indexes", stage="schema")
            xinfo = db.execute(
                f'PRAGMA index_xinfo("{name}")').fetchall()
            keys = tuple(
                (str(row[2]), int(row[3]), str(row[4]))
                for row in xinfo if int(row[5]) == 1)
            auxiliaries = tuple(
                (row[2], int(row[3]), str(row[4]), int(row[5]))
                for row in xinfo if int(row[5]) == 0)
            if (keys != expected_keys
                    or auxiliaries != ((None, 0, "BINARY", 0),)):
                raise TaskStoreUnavailable(
                    "invalid_indexes", stage="schema")

    def _verify_connection_identity(self, db: sqlite3.Connection,
                                    expected_path: Path,
                                    expected: _FileStamp) -> None:
        expected_path = Path(os.path.abspath(os.fspath(expected_path)))
        official_lstat = self._assert_regular_unlinked(expected_path)
        official_stat = os.stat(expected_path)
        if (not os.path.samestat(official_lstat, official_stat)
                or int(official_lstat.st_dev) != expected.device
                or int(official_lstat.st_ino) != expected.inode):
            raise TaskStoreUnavailable(
                "connection_identity", stage="live")

        rows = db.execute("PRAGMA database_list").fetchall()
        main_rows = [row for row in rows if row[1] == "main"]
        if len(main_rows) != 1 or not main_rows[0][2]:
            raise TaskStoreUnavailable(
                "connection_identity", stage="live")
        reported = Path(os.path.abspath(os.fspath(main_rows[0][2])))
        reported_lstat = self._assert_regular_unlinked(reported)
        reported_stat = os.stat(reported)
        if (not os.path.samestat(reported_lstat, reported_stat)
                or not os.path.samestat(official_lstat, reported_lstat)
                or not os.path.samestat(official_stat, reported_stat)):
            raise TaskStoreUnavailable(
                "connection_identity", stage="live")

    def _open_connection(self, path: Path) -> sqlite3.Connection:
        try:
            return self._connect(path)
        except (SchemaTooNew, TaskStoreUnavailable):
            raise
        except sqlite3.DatabaseError as exc:
            raise self._unavailable(exc, "connect") from exc
        except OSError as exc:
            raise TaskStoreUnavailable(
                "connect_failed", stage="connect") from exc

    @staticmethod
    def _close_failed(db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        try:
            if db.in_transaction:
                db.rollback()
        except sqlite3.Error:
            pass
        try:
            db.close()
        except sqlite3.Error:
            pass

    def _open_existing(self,
                       expected: dict[Path, _FileStamp],
                       expected_version: int, *,
                       recover_hot_journal: bool = False) -> None:
        current = self._capture_group(
            full_hash=any(stamp.sha256 is not None
                          for stamp in expected.values()))
        if current != expected:
            raise TaskStoreUnavailable(
                "store_changed_before_open", stage="live")
        db: sqlite3.Connection | None = None
        try:
            db = self._open_connection(self.path)
            self._verify_connection_identity(
                db, self.path, expected[self.path])
            db.execute("BEGIN IMMEDIATE")
            live_kind = self._inspect_database(
                db, full_integrity=False)
            if live_kind != expected_version:
                raise TaskStoreUnavailable(
                    "schema_changed_before_open", stage="live")
            db.rollback()
            if recover_hot_journal \
                    and _lexists(Path(f"{self.path}-journal")):
                raise TaskStoreUnavailable(
                    "live_journal_not_recovered", stage="live")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            if db.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise sqlite3.DatabaseError("foreign key enforcement unavailable")
            self._db = db
        except (SchemaTooNew, TaskStoreUnavailable):
            self._close_failed(db)
            raise
        except sqlite3.DatabaseError as exc:
            self._close_failed(db)
            raise self._unavailable(exc, "live") from exc
        except OSError as exc:
            self._close_failed(db)
            raise TaskStoreUnavailable(
                "live_validation_failed", stage="live") from exc

    def _partial_members(self, partial: Path) -> tuple[Path, Path, Path, Path]:
        return (
            partial,
            Path(f"{partial}-wal"),
            Path(f"{partial}-shm"),
            Path(f"{partial}-journal"),
        )

    @staticmethod
    def _fsync_file(path: Path) -> None:
        # Windows' CRT rejects fsync() on a read-only descriptor.
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_parent(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _build_partial_v2(self, partial: Path) -> _FileStamp:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(partial, flags, 0o600)
        os.close(descriptor)
        created = self._metadata(partial)
        db: sqlite3.Connection | None = None
        try:
            db = self._open_connection(partial)
            self._verify_connection_identity(db, partial, created)
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            if self._inspect_database(
                    db, full_integrity=False) != 0:
                raise TaskStoreUnavailable(
                    "partial_not_empty", stage="create")
            migrations.initialize_v2(db)
            if self._inspect_database(
                    db, full_integrity=False) != migrations.SCHEMA_VERSION:
                raise TaskStoreUnavailable(
                    "partial_schema_invalid", stage="create")
            db.commit()
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            if self._inspect_database(
                    db, full_integrity=True) != migrations.SCHEMA_VERSION:
                raise TaskStoreUnavailable(
                    "partial_schema_invalid", stage="create")
            db.close()
            db = None

            unexpected = [member for member in self._partial_members(partial)[1:]
                          if _lexists(member)]
            if unexpected:
                raise TaskStoreUnavailable(
                    "partial_sidecar_remaining", stage="create")
            built = self._metadata(partial)
            verification = sqlite3.connect(
                self._readonly_uri(partial), uri=True)
            try:
                self._verify_connection_identity(
                    verification, partial, built)
                if self._inspect_database(
                        verification,
                        full_integrity=True) != migrations.SCHEMA_VERSION:
                    raise TaskStoreUnavailable(
                        "partial_schema_invalid", stage="create")
            finally:
                verification.close()
            if self._metadata(partial) != built:
                raise TaskStoreUnavailable(
                    "partial_changed_during_verify", stage="create")
            self._fsync_file(partial)
            return self._metadata(partial)
        except (SchemaTooNew, TaskStoreUnavailable):
            self._close_failed(db)
            raise
        except sqlite3.DatabaseError as exc:
            self._close_failed(db)
            raise self._unavailable(exc, "create") from exc
        except OSError as exc:
            self._close_failed(db)
            raise TaskStoreUnavailable(
                "create_failed", stage="create") from exc

    def _create_and_publish(self) -> None:
        partial = self.path.with_name(
            f".{self.path.name}.create-partial-{uuid.uuid4().hex}")
        try:
            if self._capture_group():
                raise TaskStoreUnavailable(
                    "store_appeared", stage="publish")
            built = self._build_partial_v2(partial)
            if self._capture_group():
                raise TaskStoreUnavailable(
                    "store_appeared", stage="publish")
            if self._metadata(partial) != built:
                raise TaskStoreUnavailable(
                    "partial_changed_before_publish", stage="publish")

            if os.name == "nt":
                self._windows_rename_no_replace(partial, self.path)
            else:
                os.link(partial, self.path)
                partial.unlink()
            self._fsync_file(self.path)
            self._fsync_parent(self.path.parent)

            final = self._capture_group()
            if self._inspect_main_only(final) != migrations.SCHEMA_VERSION:
                raise TaskStoreUnavailable(
                    "published_schema_invalid", stage="publish")
            self._open_existing(final, migrations.SCHEMA_VERSION)
        except (SchemaTooNew, TaskStoreUnavailable):
            raise
        except sqlite3.DatabaseError as exc:
            raise self._unavailable(exc, "publish") from exc
        except OSError as exc:
            raise TaskStoreUnavailable(
                "publish_failed", stage="publish") from exc

    @staticmethod
    def _windows_rename_no_replace(source: Path, destination: Path) -> None:
        """Publish durably on Windows without replacing an existing target."""
        import ctypes

        move_file = ctypes.windll.kernel32.MoveFileExW
        move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                              ctypes.c_ulong]
        move_file.restype = ctypes.c_int
        movefile_write_through = 0x00000008
        if not move_file(str(source), str(destination),
                         movefile_write_through):
            raise ctypes.WinError()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            raise TodoError("repository is closed")
        return self._db

    # -- task rows ----------------------------------------------------------

    # note is deliberately NOT part of the shared column list: the main
    # tree must not preload every long note (V12-05), and generic upserts
    # must never clobber a stored note with a stale cache value.  Notes
    # are read on demand (note_of) and written only by write_note.
    _TASK_COLUMNS_SQL = (
        "id, parent_id, title, horizon, status, sort_key,"
        " due_date, created_at, updated_at, completed_at,"
        " importance, urgency, archived, archived_at")

    @staticmethod
    def _row_to_task(row) -> Task:
        return Task(
            id=row[0],
            parent_id=row[1],
            title=row[2],
            horizon=Horizon(row[3]),
            status=Status(row[4]),
            sort_key=int(row[5]),
            due_date=date.fromisoformat(row[6]) if row[6] else None,
            created_at=_from_iso(row[7]),
            updated_at=_from_iso(row[8]),
            completed_at=_from_iso(row[9]) if row[9] else None,
            importance=Level(row[10]) if row[10] is not None else None,
            urgency=Level(row[11]) if row[11] is not None else None,
            archived=bool(row[12]),
            archived_at=_from_iso(row[13]) if row[13] else None,
        )

    def note_of(self, task_id: str) -> str:
        """Read one note on demand (notes side page; never the tree)."""
        row = self.db.execute(
            "SELECT note FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise TodoError("task does not exist")
        return str(row[0])

    def write_note(self, task_id: str, note: str, updated_at) -> None:
        """Persist one note in its own transaction; nothing else changes."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.db.execute(
                "UPDATE tasks SET note=?, updated_at=? WHERE id=?",
                (note, _to_iso(updated_at), task_id))
            if cursor.rowcount == 0:
                raise TodoError("task does not exist")
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def load_all(self) -> dict[str, Task]:
        rows = self.db.execute(
            f"SELECT {self._TASK_COLUMNS_SQL}"
            " FROM tasks ORDER BY sort_key, created_at").fetchall()
        return {row[0]: self._row_to_task(row) for row in rows}

    def upsert(self, task: Task) -> None:
        self.apply_batch(upserts=(task,))

    def _write_task(self, task: Task) -> None:
        self.db.execute(
            "INSERT INTO tasks (id, parent_id, title, horizon, status,"
            " sort_key, due_date, created_at, updated_at, completed_at,"
            " importance, urgency, archived, archived_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET parent_id=excluded.parent_id,"
            " title=excluded.title, horizon=excluded.horizon,"
            " status=excluded.status, sort_key=excluded.sort_key,"
            " due_date=excluded.due_date, updated_at=excluded.updated_at,"
            " completed_at=excluded.completed_at,"
            " importance=excluded.importance, urgency=excluded.urgency,"
            " archived=excluded.archived, archived_at=excluded.archived_at",
            (task.id, task.parent_id, task.title, task.horizon.value,
             task.status.value, task.sort_key,
             task.due_date.isoformat() if task.due_date else None,
             _to_iso(task.created_at), _to_iso(task.updated_at),
             _to_iso(task.completed_at) if task.completed_at else None,
             (task.importance.value
              if task.importance is not None else None),
             (task.urgency.value if task.urgency is not None else None),
             1 if task.archived else 0,
             _to_iso(task.archived_at) if task.archived_at else None),
        )

    def delete_many(self, ids: list[str]) -> bool:
        """Delete rows and conditionally clear their focus in one transaction."""
        return self.apply_batch(
            delete_ids=ids,
            clear_focus_for=ids,
        )

    def _delete_task(self, task_id: str) -> None:
        self.db.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def _clear_focus_row(self) -> bool:
        cursor = self.db.execute("DELETE FROM focus WHERE id=1")
        return cursor.rowcount > 0

    def _set_focus_row(self, task_id: str) -> None:
        self._clear_focus_row()
        self.db.execute(
            "INSERT INTO focus (id, task_id, started_at) VALUES (1, ?, ?)",
            (task_id, _to_iso(datetime.now())),
        )

    def apply_batch(self, *, upserts: Iterable[Task] = (),
                    delete_ids: Iterable[str] = (),
                    focus=_KEEP_FOCUS,
                    clear_focus_for: Iterable[str] = ()) -> bool:
        """Apply changes atomically and report a conditional focus clear.

        The current focus row is inspected only after ``BEGIN IMMEDIATE``.
        This avoids a pre-transaction check/use race when deleting a subtree
        or completing a task.
        """
        delete_ids = tuple(delete_ids)
        conditional_clear = frozenset(clear_focus_for)
        if focus is None and conditional_clear:
            raise ValueError("focus clear modes are mutually exclusive")
        db = self.db
        db.execute("BEGIN IMMEDIATE")
        try:
            for task in upserts:
                self._write_task(task)
            cleared = False
            if focus is None:
                cleared = self._clear_focus_row()
            elif conditional_clear:
                row = db.execute(
                    "SELECT task_id FROM focus WHERE id=1").fetchone()
                if row is not None and row[0] in conditional_clear:
                    cleared = self._clear_focus_row()
            for task_id in delete_ids:
                self._delete_task(task_id)
            if focus is not None and focus is not _KEEP_FOCUS:
                self._set_focus_row(str(focus))
            db.commit()
            return cleared
        except Exception:
            db.rollback()
            raise

    # -- focus --------------------------------------------------------------

    def get_focus(self) -> tuple[str, str] | None:
        row = self.db.execute(
            "SELECT task_id, started_at FROM focus WHERE id=1").fetchone()
        return (row[0], row[1]) if row else None

    def load_focus_summary(self) -> tuple[str, Status, Horizon] | None:
        """Load only the three internal fields needed for safe projection."""
        row = self.db.execute(
            "SELECT t.id, t.status, t.horizon FROM focus AS f "
            "JOIN tasks AS t ON t.id=f.task_id WHERE f.id=1").fetchone()
        if row is None:
            return None
        return str(row[0]), Status(row[1]), Horizon(row[2])

    def load_focus_task(self) -> Task | None:
        columns = ", ".join(f"t.{name}" for name in
                            self._TASK_COLUMNS_SQL.split(", "))
        row = self.db.execute(
            f"SELECT {columns}"
            " FROM focus AS f"
            " JOIN tasks AS t ON t.id=f.task_id WHERE f.id=1").fetchone()
        return self._row_to_task(row) if row is not None else None

    def set_focus(self, task_id: str) -> None:
        self.apply_batch(focus=task_id)

    def clear_focus(self) -> bool:
        return self.apply_batch(focus=None)

    # -- snapshot inspection (used by the backup module) ---------------------

    @classmethod
    def inspect_snapshot(cls, path: Path) -> tuple[int, int]:
        """Fully verify a standalone store file (e.g. a backup copy).

        Returns ``(declared_version, task_count)`` for an exact known
        schema; anything else raises instead of trusting the file.
        """
        db = sqlite3.connect(
            f"{Path(path).as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            declared = cls._inspect_database(db, full_integrity=True)
            if declared == 0:
                raise TaskStoreUnavailable(
                    "empty_store_unsupported", stage="snapshot")
            count = int(db.execute(
                "SELECT COUNT(*) FROM tasks").fetchone()[0])
            return declared, count
        finally:
            db.close()
