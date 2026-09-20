"""Todo store schema declarations and the explicit v1 -> v2 migration.

Alpha v1 had no data-bearing migration path; 1.2.0 introduces exactly one:
v1 -> v2.  The migration adds three product dimensions (independent
importance/urgency for the quadrant view, recoverable archiving and a
plain-text long note) while preserving every v1 value byte-for-byte.  It runs
inside the caller's transaction so an interruption always leaves the old v1
store on disk.  Fresh stores are created directly as v2.

v1 stores remain first-class read targets (backup verification, the
old-version restore drill) and ``initialize_v1`` stays available for
synthetic fixtures.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from retirement_pet.todo.domain import TodoError
from retirement_pet.todo.errors import SchemaTooNew

SCHEMA_VERSION = 2
#: versions this build knows how to open (1 -> migrates, 2 -> direct)
SUPPORTED_VERSIONS = (1, 2)

V1_TABLE_SQL: dict[str, str] = {
    "meta": "CREATE TABLE meta ("
    " key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "tasks": "CREATE TABLE tasks ("
    " id TEXT PRIMARY KEY,"
    " parent_id TEXT REFERENCES tasks(id),"
    " title TEXT NOT NULL,"
    " horizon TEXT NOT NULL CHECK (horizon IN ('short','medium','long')),"
    " status TEXT NOT NULL CHECK (status IN ('open','done')),"
    " sort_key INTEGER NOT NULL DEFAULT 0,"
    " due_date TEXT,"
    " created_at TEXT NOT NULL,"
    " updated_at TEXT NOT NULL,"
    " completed_at TEXT)",
    "focus": "CREATE TABLE focus ("
    " id INTEGER PRIMARY KEY CHECK (id = 1),"
    " task_id TEXT NOT NULL REFERENCES tasks(id),"
    " started_at TEXT NOT NULL)",
}

V1_INDEX_SQL: dict[str, str] = {
    "idx_tasks_parent":
        "CREATE INDEX idx_tasks_parent ON tasks(parent_id)",
    "idx_tasks_status_horizon":
        "CREATE INDEX idx_tasks_status_horizon ON tasks(status, horizon)",
    "idx_tasks_due":
        "CREATE INDEX idx_tasks_due ON tasks(due_date)",
}

_V1_STATEMENTS: tuple[str, ...] = (
    *V1_TABLE_SQL.values(),
    *V1_INDEX_SQL.values(),
)

# v2 keeps every v1 column (same name, type, constraints, order) and appends
# the new dimensions at the end so the migration is a pure column addition.
V2_TABLE_SQL: dict[str, str] = {
    "meta": V1_TABLE_SQL["meta"],
    "tasks": "CREATE TABLE tasks ("
    " id TEXT PRIMARY KEY,"
    " parent_id TEXT REFERENCES tasks(id),"
    " title TEXT NOT NULL,"
    " horizon TEXT NOT NULL CHECK (horizon IN ('short','medium','long')),"
    " status TEXT NOT NULL CHECK (status IN ('open','done')),"
    " sort_key INTEGER NOT NULL DEFAULT 0,"
    " due_date TEXT,"
    " created_at TEXT NOT NULL,"
    " updated_at TEXT NOT NULL,"
    " completed_at TEXT,"
    " importance TEXT CHECK (importance IS NULL"
    " OR importance IN ('high','low')),"
    " urgency TEXT CHECK (urgency IS NULL OR urgency IN ('high','low')),"
    " archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),"
    " archived_at TEXT,"
    " note TEXT NOT NULL DEFAULT '')",
    "focus": V1_TABLE_SQL["focus"],
}

V2_INDEX_SQL: dict[str, str] = {
    **V1_INDEX_SQL,
    "idx_tasks_archived":
        "CREATE INDEX idx_tasks_archived ON tasks(archived)",
}

_V2_STATEMENTS: tuple[str, ...] = (
    *V2_TABLE_SQL.values(),
    *V2_INDEX_SQL.values(),
)

#: v1 columns carried through the migration unchanged, in copy order
_MIGRATED_COLUMNS: tuple[str, ...] = (
    "id", "parent_id", "title", "horizon", "status", "sort_key",
    "due_date", "created_at", "updated_at", "completed_at",
)


def declared_version(db: sqlite3.Connection) -> int:
    """Return the declared version of a database known to contain ``meta``.

    The parser is intentionally strict.  A missing row, booleans, signed
    strings, whitespace and non-decimal forms are invalid rather than aliases
    for an older schema.
    """
    row = db.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None or not isinstance(row[0], str):
        raise ValueError("missing schema version")
    raw = row[0]
    if not raw or not raw.isascii() or not raw.isdecimal():
        raise ValueError("invalid schema version")
    version = int(raw, 10)
    if raw != str(version):
        raise ValueError("noncanonical schema version")
    if version > SCHEMA_VERSION:
        raise SchemaTooNew(version, SCHEMA_VERSION)
    return version


def _write_meta_v2(db: sqlite3.Connection, established_at: str) -> None:
    # Upsert: fresh v2 creation starts from an empty meta table, while the
    # migration path already carries the v1 schema_version row.
    db.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),))
    db.execute(
        "INSERT INTO meta (key, value) VALUES ('established_at', ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (established_at,))


def initialize_v1(db: sqlite3.Connection) -> int:
    """Create the exact v1 schema inside the caller's active transaction."""
    if not db.in_transaction:
        raise RuntimeError("schema initialisation requires a transaction")
    for statement in _V1_STATEMENTS:
        db.execute(statement)
    db.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
    return 1


def initialize_v2(db: sqlite3.Connection, *,
                  established_at: str | None = None) -> int:
    """Create the exact v2 schema inside the caller's active transaction."""
    if not db.in_transaction:
        raise RuntimeError("schema initialisation requires a transaction")
    for statement in _V2_STATEMENTS:
        db.execute(statement)
    _write_meta_v2(
        db, established_at or datetime.now().isoformat(timespec="seconds"))
    return SCHEMA_VERSION


def _require_v1_preconditions(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("migration requires a transaction")
    if db.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
        raise RuntimeError("migration requires foreign_keys=OFF")
    declared = declared_version(db)
    if declared != 1:
        raise TodoError(f"migration expects a v1 store, found v{declared}")


def _verify_copied_rows(db: sqlite3.Connection) -> None:
    """Prove the shadow copy matches v1 before the shadow table is dropped."""
    columns = ", ".join(_MIGRATED_COLUMNS)
    shadow = [tuple(row) for row in db.execute(
        f"SELECT {columns} FROM tasks_v1_shadow")]
    migrated = [tuple(row) for row in db.execute(
        f"SELECT {columns} FROM tasks")]
    if len(shadow) != len(migrated):
        raise TodoError("migration row count mismatch")
    if shadow != migrated:
        raise TodoError("migration row content mismatch")
    bad = db.execute(
        "SELECT COUNT(*) FROM tasks WHERE importance IS NOT NULL"
        " OR urgency IS NOT NULL OR archived != 0 OR archived_at IS NOT NULL"
        " OR note != ''").fetchone()[0]
    if bad:
        raise TodoError("migration produced non-default v2 fields")
    focus = db.execute("SELECT COUNT(*) FROM focus").fetchone()[0]
    if focus > 1:
        raise TodoError("migration focus invariant broken")


def migrate_v1_to_v2(db: sqlite3.Connection, *,
                     established_at: str | None = None) -> int:
    """Migrate a verified v1 store to v2 inside the caller's transaction.

    Preconditions (enforced here and by the repository): an active
    transaction on a live v1 store with ``PRAGMA foreign_keys=OFF``.  Every
    step is ordinary transactional DDL/DML, so a crash before COMMIT rolls
    back to the untouched v1 store.  All v1 values are carried over
    unchanged; new fields take their documented defaults (unclassified,
    not archived, empty note).  Completion states and focus are never
    rewritten.
    """
    _require_v1_preconditions(db)
    # Stage the v1 rows in a plain shadow table (no constraints) so the
    # constrained ``tasks`` table can be dropped and recreated as v2 without
    # an ALTER TABLE rename dance.  The shadow is dropped only after the
    # row-for-row parity check below.
    db.execute(
        "CREATE TABLE tasks_v1_shadow AS SELECT"
        f" {', '.join(_MIGRATED_COLUMNS)} FROM tasks")
    db.execute("DROP TABLE tasks")
    db.execute(V2_TABLE_SQL["tasks"])
    db.execute(
        "INSERT INTO tasks"
        f" ({', '.join(_MIGRATED_COLUMNS)},"
        "  importance, urgency, archived, archived_at, note)"
        f" SELECT {', '.join(_MIGRATED_COLUMNS)},"
        "  NULL, NULL, 0, NULL, '' FROM tasks_v1_shadow")
    for statement in V2_INDEX_SQL.values():
        db.execute(statement)
    _verify_copied_rows(db)
    db.execute("DROP TABLE tasks_v1_shadow")
    _write_meta_v2(
        db, established_at or datetime.now().isoformat(timespec="seconds"))
    return SCHEMA_VERSION


__all__ = [
    "SCHEMA_VERSION",
    "SUPPORTED_VERSIONS",
    "SchemaTooNew",
    "V1_TABLE_SQL",
    "V1_INDEX_SQL",
    "V2_TABLE_SQL",
    "V2_INDEX_SQL",
    "declared_version",
    "initialize_v1",
    "initialize_v2",
    "migrate_v1_to_v2",
]
