"""Todo store schema declarations.

Alpha deliberately has no data-bearing migration path.  Only when the whole
SQLite file family is absent does the repository create a same-directory
owned partial and initialise it as v1 inside a controlled transaction.
Existing empty, unknown, malformed and newer databases are never rewritten.
"""

from __future__ import annotations

import sqlite3

from retirement_pet.todo.errors import SchemaTooNew

SCHEMA_VERSION = 1

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


def initialize_v1(db: sqlite3.Connection) -> int:
    """Create the exact v1 schema inside the caller's active transaction."""
    if not db.in_transaction:
        raise RuntimeError("schema initialisation requires a transaction")
    for statement in _V1_STATEMENTS:
        db.execute(statement)
    db.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    return SCHEMA_VERSION


__all__ = [
    "SCHEMA_VERSION",
    "SchemaTooNew",
    "V1_TABLE_SQL",
    "V1_INDEX_SQL",
    "declared_version",
    "initialize_v1",
]
