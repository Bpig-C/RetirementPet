"""M6.5 TodoModule: domain rules, transactions, focus, privacy, UI lazy."""

from __future__ import annotations

import errno
import logging
import os
import sqlite3
import subprocess
import sys
import time
import traceback
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
from pathlib import Path

import pytest

import retirement_pet.todo.domain as todo_domain
from retirement_pet.runtime_state import ContextId, ContextStore
from retirement_pet.todo import (
    Horizon,
    Status,
    TaskRepository,
    TodoError,
    TodoService,
)
from retirement_pet.todo.context_bridge import TodoContextBridge
from retirement_pet.todo.domain import check_parenting, subtree_ids
from retirement_pet.todo.errors import SchemaTooNew, TaskStoreUnavailable


def _store_snapshot(path: Path) -> dict[str, tuple[bytes, int]]:
    """Capture bytes and nanosecond mtime for the complete SQLite file set."""
    result = {}
    for member in (path, Path(f"{path}-wal"), Path(f"{path}-shm"),
                   Path(f"{path}-journal")):
        if member.exists():
            stat = member.stat()
            result[member.name] = (member.read_bytes(), stat.st_mtime_ns)
    return result


def _sqlite_error(code: int, message: str = "injected sqlite failure"):
    error = sqlite3.OperationalError(message)
    error.sqlite_errorcode = code
    error.sqlite_errorname = "INJECTED"
    return error


def _safe_context_view(store: ContextStore) -> dict:
    """Public, privacy-safe ContextStore view used by canary assertions."""
    return {
        "active": sorted(context.value for context in store.active()),
        "working_owner": store.owner_of(ContextId.WORKING),
    }


def _isolate_startup_registry(monkeypatch) -> None:
    """App integration tests must never read the user's real HKCU state."""
    from retirement_pet.startup import MemoryBackend

    monkeypatch.setattr("retirement_pet.app.WinRegBackend", MemoryBackend)


def _tree_item_by_id(tree, task_id: str):
    """Find a Todo item without depending on its current hierarchy."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTreeWidgetItemIterator

    iterator = QTreeWidgetItemIterator(tree)
    while iterator.value() is not None:
        item = iterator.value()
        if item.data(0, Qt.UserRole) == task_id:
            return item
        iterator += 1
    raise AssertionError(f"task item not found: {task_id}")


def _todo_button(page, text: str):
    from PySide6.QtWidgets import QPushButton

    matches = [button for button in page.findChildren(QPushButton)
               if button.text() == text]
    assert len(matches) == 1, (text, [button.text() for button
                                      in page.findChildren(QPushButton)])
    return matches[0]


def _flush_deferred_deletes(qt_application) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_application.processEvents()


@pytest.fixture()
def service(tmp_path):
    repo = TaskRepository(tmp_path / "tasks.db")
    svc = TodoService(repo)
    yield svc
    svc.close()


@pytest.fixture()
def service_harness(tmp_path):
    repository = TaskRepository(tmp_path / "tasks.db")
    events: list[str] = []
    svc = TodoService(repository, on_event=events.append)
    yield svc, repository, events
    svc.close()


@pytest.fixture()
def bridge(tmp_path):
    repository = TaskRepository(tmp_path / "tasks.db")
    holder = {}

    def on_event(_event: str) -> None:
        holder["bridge"].sync_focus(service.load_focus_projection())

    service = TodoService(repository, on_event=on_event)
    store = ContextStore()
    todo_bridge = TodoContextBridge(store)
    holder["bridge"] = todo_bridge
    todo_bridge.sync_focus(service.load_focus_projection())
    yield service, store, todo_bridge
    service.close()


# -- CRUD ---------------------------------------------------------------------------


def test_crud_roundtrip(service):
    task = service.add_task("写周报", Horizon.SHORT)
    assert task.status is Status.OPEN
    service.rename(task.id, "写月报")
    assert service.get(task.id).title == "写月报"
    service.set_due_date(task.id, date(2026, 9, 30))
    assert service.get(task.id).due_date == date(2026, 9, 30)
    service.set_horizon(task.id, Horizon.MEDIUM)
    assert service.get(task.id).horizon is Horizon.MEDIUM
    service.complete(task.id)
    assert service.get(task.id).status is Status.DONE
    assert service.get(task.id).completed_at is not None
    service.restore(task.id)
    assert service.get(task.id).status is Status.OPEN


def test_empty_and_blank_titles_rejected(service):
    with pytest.raises(TodoError):
        service.add_task("   ", Horizon.SHORT)
    with pytest.raises(TodoError):
        service.add_task("x" * 201, Horizon.SHORT)


# -- tree rules ------------------------------------------------------------------------


def test_children_read_back_as_tree(service):
    root = service.add_task("搬家", Horizon.MEDIUM)
    child = service.add_subtask(root.id, "打包书房")
    grand = service.add_subtask(child.id, "书装箱")
    assert service.children_of(root.id)[0].id == child.id
    assert service.children_of(child.id)[0].id == grand.id
    assert service.children_of(None)[0].id == root.id


def test_cycle_rejected(service):
    a = service.add_task("A", Horizon.SHORT)
    b = service.add_subtask(a.id, "B")
    tasks = {t.id: t for t in service.all_tasks()}
    with pytest.raises(TodoError):
        # would make A a child of its own descendant B
        check_parenting(tasks, a.id, b.id)


def test_self_parent_rejected(service):
    a = service.add_task("A", Horizon.SHORT)
    tasks = {t.id: t for t in service.all_tasks()}
    with pytest.raises(TodoError):
        check_parenting(tasks, a.id, a.id)


def test_missing_parent_rejected(service):
    a = service.add_task("A", Horizon.SHORT)
    tasks = {t.id: t for t in service.all_tasks()}
    with pytest.raises(TodoError):
        check_parenting(tasks, a.id, "nonexistent")


def test_max_depth_enforced(service):
    parent = service.add_task("L0", Horizon.LONG)
    for depth in range(1, 6):  # up to depth 6 is allowed
        parent = service.add_subtask(parent.id, f"L{depth}")
    with pytest.raises(TodoError):
        service.add_subtask(parent.id, "L7-toodeep")


def test_child_inherits_parent_horizon_by_default(service):
    root = service.add_task("装修", Horizon.LONG)
    child = service.add_subtask(root.id, "选瓷砖")
    assert child.horizon is Horizon.LONG
    # explicit override allowed (user choice only)
    service.set_horizon(child.id, Horizon.SHORT)
    assert service.get(child.id).horizon is Horizon.SHORT


def test_sibling_order_and_move(service):
    first = service.add_task("一", Horizon.SHORT)
    second = service.add_task("二", Horizon.SHORT)
    third = service.add_task("三", Horizon.SHORT)
    assert [t.id for t in service.children_of(None)] == \
        [first.id, second.id, third.id]
    service.move_within_siblings(third.id, -2)
    assert [t.id for t in service.children_of(None)] == \
        [third.id, first.id, second.id]
    service.move_within_siblings(third.id, -1)  # out of range: no-op
    assert [t.id for t in service.children_of(None)] == \
        [third.id, first.id, second.id]


def test_horizon_filter_with_ancestor_context(service):
    root = service.add_task("换工作", Horizon.MEDIUM)  # ancestor
    child = service.add_subtask(root.id, "更新简历")   # inherits MEDIUM
    service.add_task("买牛奶", Horizon.SHORT)
    only = service.by_horizon(Horizon.MEDIUM)
    assert {t.id for t in only} == {root.id, child.id}
    with_ancestors = service.by_horizon(
        Horizon.MEDIUM, include_ancestors=True)
    assert {t.id for t in with_ancestors} == {root.id, child.id}
    # a SHORT child under a MEDIUM root shows with its ancestor chain
    service.set_horizon(child.id, Horizon.SHORT)
    expanded = service.by_horizon(Horizon.SHORT, include_ancestors=True)
    assert root.id in {t.id for t in expanded}


def test_completion_does_not_cascade(service):
    root = service.add_task("项目", Horizon.MEDIUM)
    child = service.add_subtask(root.id, "子任务")
    service.complete(root.id)
    assert service.get(root.id).status is Status.DONE
    assert service.get(child.id).status is Status.OPEN  # independent


def add_subtree_branch(service, parent_id):
    child = service.add_subtask(parent_id, "branch-child")
    service.add_subtask(child.id, "branch-grandchild")
    return child.id


def test_delete_subtree_is_atomic(service):
    root = service.add_task("R", Horizon.SHORT)
    a = service.add_subtask(root.id, "A")
    b = add_subtree_branch(service, a.id)
    survivor = service.add_task("存活", Horizon.SHORT)

    removed = service.delete_subtree(root.id)
    assert removed == 4  # R + A + A's branch
    assert service.get(root.id) is None
    assert service.get(a.id) is None
    assert service.get(b) is None
    assert service.get(survivor.id) is not None


def test_subtree_ids_helper(service):
    root = service.add_task("R", Horizon.SHORT)
    a = service.add_subtask(root.id, "A")
    b = add_subtree_branch(service, a.id)
    other = service.add_task("other", Horizon.SHORT)
    ids = subtree_ids({t.id: t for t in service.all_tasks()}, root.id)
    assert ids == {root.id, a.id, b} | {t.id for t in service.all_tasks()
                                        if t.parent_id == b}
    assert other.id not in ids


# -- focus -----------------------------------------------------------------------------


def test_only_one_active_focus(service):
    first = service.add_task("F1", Horizon.SHORT)
    second = service.add_task("F2", Horizon.SHORT)
    service.start_focus(first.id)
    service.start_focus(second.id)  # switching replaces, never two
    assert service.focus_task_id() == second.id
    service.stop_focus()
    assert service.focus_task_id() is None


def test_focus_completed_task_rejected_and_clears(service):
    task = service.add_task("F", Horizon.SHORT)
    service.start_focus(task.id)
    service.complete(task.id)  # completing the focus clears it
    assert service.focus_task_id() is None
    with pytest.raises(TodoError):
        service.start_focus(task.id)  # done tasks cannot be focused


def test_focus_restart_restore(tmp_path):
    repo = TaskRepository(tmp_path / "tasks.db")
    service = TodoService(repo)
    task = service.add_task("跨重启", Horizon.MEDIUM)
    service.start_focus(task.id)

    service.close()
    repo2 = TaskRepository(tmp_path / "tasks.db")
    service2 = TodoService(repo2)
    restored = service2.load_focus_only()  # single-row restore path
    assert restored is not None and restored.id == task.id
    assert service2.load_focus_only().title == "跨重启"
    service2.close()


def test_focus_only_query_does_not_load_tree(tmp_path):
    repo = TaskRepository(tmp_path / "tasks.db")
    service = TodoService(repo)
    service.add_task("T1", Horizon.SHORT)
    service.add_task("T2", Horizon.SHORT)
    service.close()

    repo2 = TaskRepository(tmp_path / "tasks.db")
    service2 = TodoService(repo2)
    assert service2.load_focus_only() is None
    # tree still lazy: all_tasks() not called
    assert service2._loaded is False
    service2.close()


# -- context bridge + privacy -------------------------------------------------------------


def test_focus_sets_and_clears_working_context(bridge):
    service, store, todo_bridge = bridge

    task = service.add_task("专注任务", Horizon.SHORT)
    service.start_focus(task.id)  # bridge hears focus_started via service
    assert store.is_active(ContextId.WORKING)
    assert todo_bridge.projection == todo_domain.FocusProjection(
        focusing=True, horizon=Horizon.SHORT)

    service.set_horizon(task.id, Horizon.LONG)
    assert todo_bridge.projection == todo_domain.FocusProjection(
        focusing=True, horizon=Horizon.LONG)

    service.stop_focus()
    assert not store.is_active(ContextId.WORKING)
    assert todo_bridge.projection == todo_domain.FocusProjection(
        focusing=False, horizon=None)


def test_mere_open_tasks_never_set_working(bridge):
    service, store, _todo_bridge = bridge
    service.add_task("一堆未完成任务", Horizon.LONG)
    service.add_task("更多", Horizon.SHORT)
    assert not store.is_active(ContextId.WORKING)


def test_projection_contains_no_task_titles(bridge):
    service, _store, todo_bridge = bridge
    service.add_task("SECRET-TITLE-XYZ", Horizon.SHORT)
    task = service.all_tasks()[0]
    service.start_focus(task.id)
    assert "SECRET-TITLE-XYZ" not in repr(todo_bridge.projection)
    assert todo_bridge.projection.horizon is Horizon.SHORT
    assert set(todo_bridge.projection.__dataclass_fields__) == {
        "focusing", "horizon"}


def test_context_snapshot_never_carries_titles(bridge, caplog):
    service, _store, _todo_bridge = bridge
    task = service.add_task("CANARY-NEVER-LOG", Horizon.SHORT)
    with caplog.at_level(logging.DEBUG):
        service.start_focus(task.id)
        service.complete(task.id)
        service.delete_subtree(task.id)
    dumped = "\n".join(r.getMessage() for r in caplog.records)
    assert "CANARY-NEVER-LOG" not in dumped


# -- fail-closed schema and store safety ---------------------------------------------


def test_schema_version_written(tmp_path):
    repo = TaskRepository(tmp_path / "tasks.db")
    version = repo.db.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert version[0] == "1"
    repo.close()


def test_previous_release_v1_schema_is_accepted_without_rewrite(tmp_path):
    """The v1 emitted before strict preflight remains an exact v1 store."""
    path = tmp_path / "tasks.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            parent_id TEXT REFERENCES tasks(id),
            title TEXT NOT NULL,
            horizon TEXT NOT NULL CHECK (
                horizon IN ('short','medium','long')),
            status TEXT NOT NULL CHECK (status IN ('open','done')),
            sort_key INTEGER NOT NULL DEFAULT 0,
            due_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_status_horizon
            ON tasks(status, horizon);
        CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
        CREATE TABLE IF NOT EXISTS focus (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            task_id TEXT NOT NULL REFERENCES tasks(id),
            started_at TEXT NOT NULL
        );
        INSERT INTO meta VALUES ('schema_version', '1');
    """)
    db.close()
    before = _store_snapshot(path)

    repository = TaskRepository(path)
    try:
        assert repository.load_all() == {}
    finally:
        repository.close()

    assert _store_snapshot(path) == before


def test_newer_schema_refused_without_touching_files(tmp_path):
    """An old app must refuse a NEWER store and never modify its files
    (refusal is not permission to replace user data)."""
    from retirement_pet.todo.migrations import SchemaTooNew

    path = tmp_path / "tasks.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta VALUES ('schema_version','99')")
    db.execute("CREATE TABLE future_data (x TEXT)")
    db.execute("INSERT INTO future_data VALUES ('keep-me')")
    db.commit()
    db.close()
    before = path.read_bytes()

    with pytest.raises(SchemaTooNew):
        TaskRepository(path)

    assert path.read_bytes() == before  # untouched
    assert not list(tmp_path.glob("tasks.db-wal"))


def test_corrupted_db_fails_closed_without_touching_store(tmp_path):
    path = tmp_path / "tasks.db"
    path.write_bytes(b"this is definitely not a sqlite database" * 100)
    before = _store_snapshot(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)

    assert _store_snapshot(path) == before


def test_empty_sqlite_file_is_refused_and_untouched(tmp_path):
    path = tmp_path / "tasks.db"
    sqlite3.connect(path).close()
    before = _store_snapshot(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)

    assert _store_snapshot(path) == before


# -- Phase 2A storage safety ---------------------------------------------------------


def test_schema_too_new_in_wal_preserves_complete_store(tmp_path):
    """Inspection must see WAL metadata without touching main/WAL/SHM."""
    path = tmp_path / "tasks.db"
    writer = sqlite3.connect(path)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    writer.execute("INSERT INTO meta VALUES ('schema_version','1')")
    writer.execute("CREATE TABLE future_data (value TEXT)")
    writer.commit()
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    writer.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    writer.execute("INSERT INTO future_data VALUES ('keep-wal')")
    writer.commit()
    before = _store_snapshot(path)
    assert {"tasks.db", "tasks.db-wal", "tasks.db-shm"} <= set(before)
    try:
        with pytest.raises(SchemaTooNew):
            TaskRepository(path)
        assert _store_snapshot(path) == before
    finally:
        writer.close()


@pytest.mark.parametrize("error_code", [
    sqlite3.SQLITE_BUSY | 0x100,
    sqlite3.SQLITE_LOCKED | 0x200,
    sqlite3.SQLITE_READONLY | 0x300,
    sqlite3.SQLITE_FULL,
    sqlite3.SQLITE_IOERR | 0x500,
])
def test_non_corruption_sqlite_errors_never_replace_store(tmp_path, error_code):
    path = tmp_path / "tasks.db"
    TaskRepository(path).close()
    before = _store_snapshot(path)

    def fail_connect(*_args, **_kwargs):
        raise _sqlite_error(error_code)

    with pytest.raises(Exception) as caught:
        TaskRepository(path, connection_factory=fail_connect)
    assert type(caught.value).__name__ == "TaskStoreUnavailable"
    assert _store_snapshot(path) == before


def test_invalid_schema_version_is_controlled_and_untouched(tmp_path):
    path = tmp_path / "tasks.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta VALUES ('schema_version','not-an-integer')")
    db.commit()
    db.close()
    before = _store_snapshot(path)

    with pytest.raises(Exception) as caught:
        TaskRepository(path)
    assert type(caught.value).__name__ == "TaskStoreUnavailable"
    assert _store_snapshot(path) == before


def test_notadb_complete_file_set_fails_closed_and_is_preserved(tmp_path):
    path = tmp_path / "tasks.db"
    path.write_bytes(b"NOTADB-main-CANARY")
    Path(f"{path}-wal").write_bytes(b"NOTADB-wal-CANARY")
    Path(f"{path}-shm").write_bytes(b"NOTADB-shm-CANARY")
    Path(f"{path}-journal").write_bytes(b"NOTADB-journal-CANARY")
    before = _store_snapshot(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)

    assert _store_snapshot(path) == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_orphan_sidecar_is_refused_without_creating_main(tmp_path, suffix):
    path = tmp_path / "tasks.db"
    Path(f"{path}{suffix}").write_bytes(b"ORPHAN-SIDECAR-CANARY")
    before = _store_snapshot(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)

    assert _store_snapshot(path) == before
    assert not path.exists()


def test_foreign_sqlite_database_in_wal_is_refused_and_unchanged(tmp_path):
    path = tmp_path / "tasks.db"
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE legacy_probe (value TEXT)")
    writer.commit()
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    writer.execute("INSERT INTO legacy_probe VALUES ('committed-in-wal')")
    writer.commit()
    before = _store_snapshot(path)
    try:
        with pytest.raises(TaskStoreUnavailable):
            TaskRepository(path)
        assert _store_snapshot(path) == before
    finally:
        writer.close()


def test_malformed_v1_missing_column_is_refused_and_unchanged(tmp_path):
    path = tmp_path / "tasks.db"
    TaskRepository(path).close()
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE tasks DROP COLUMN completed_at")
    db.commit()
    db.close()
    before = _store_snapshot(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)

    assert _store_snapshot(path) == before


def test_malformed_v1_wrong_primary_key_is_refused_and_unchanged(tmp_path):
    path = tmp_path / "tasks.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '1');
        CREATE TABLE tasks (
            id TEXT,
            parent_id TEXT REFERENCES tasks(id),
            title TEXT NOT NULL,
            horizon TEXT NOT NULL CHECK (
                horizon IN ('short','medium','long')),
            status TEXT NOT NULL CHECK (status IN ('open','done')),
            sort_key INTEGER NOT NULL DEFAULT 0,
            due_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE INDEX idx_tasks_parent ON tasks(parent_id);
        CREATE INDEX idx_tasks_status_horizon ON tasks(status, horizon);
        CREATE INDEX idx_tasks_due ON tasks(due_date);
        CREATE TABLE focus (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            task_id TEXT NOT NULL REFERENCES tasks(id),
            started_at TEXT NOT NULL
        );
    """)
    db.close()
    before = _store_snapshot(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)

    assert _store_snapshot(path) == before


def test_v1_with_foreign_table_is_refused_and_unchanged(tmp_path):
    path = tmp_path / "tasks.db"
    TaskRepository(path).close()
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE foreign_payload (value TEXT)")
    db.execute("INSERT INTO foreign_payload VALUES ('KEEP-FOREIGN')")
    db.commit()
    db.close()
    before = _store_snapshot(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)

    assert _store_snapshot(path) == before


def test_v1_to_future_schema_connect_race_is_never_downgraded(tmp_path):
    path = tmp_path / "tasks.db"
    TaskRepository(path).close()
    raced = False

    def racing_connect(target, *args, **kwargs):
        nonlocal raced
        if not raced:
            raced = True
            writer = sqlite3.connect(target)
            writer.execute(
                "UPDATE meta SET value='99' WHERE key='schema_version'")
            writer.execute("CREATE TABLE future_payload (value TEXT)")
            writer.execute("INSERT INTO future_payload VALUES ('KEEP-RACE')")
            writer.commit()
            writer.close()
        return sqlite3.connect(target, *args, **kwargs)

    with pytest.raises(SchemaTooNew):
        TaskRepository(path, connection_factory=racing_connect)

    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] \
            == "99"
        assert db.execute("SELECT value FROM future_payload").fetchone()[0] \
            == "KEEP-RACE"
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='tasks'").fetchone() == (1,)
    finally:
        db.close()


def test_hardlinked_store_is_refused_and_unchanged(tmp_path):
    path = tmp_path / "tasks.db"
    TaskRepository(path).close()
    peer = tmp_path / "peer.db"
    try:
        os.link(path, peer)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    before = _store_snapshot(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)

    assert _store_snapshot(path) == before


def test_reparse_parent_is_refused_without_touching_target(tmp_path):
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    real_path = real_parent / "tasks.db"
    TaskRepository(real_path).close()
    link_parent = tmp_path / "linked"
    try:
        link_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError as exc:
        permission_or_unsupported = (
            getattr(exc, "winerror", None) in {1, 50, 1314}
            or exc.errno in {
                errno.EPERM, errno.EACCES, errno.ENOSYS,
                getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
            }
        )
        if permission_or_unsupported:
            pytest.skip(f"directory symlinks unavailable: {exc}")
        raise
    before = _store_snapshot(real_path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(link_parent / "tasks.db")

    assert _store_snapshot(real_path) == before


def test_noncanonical_schema_version_is_refused_and_unchanged(tmp_path):
    path = tmp_path / "tasks.db"
    TaskRepository(path).close()
    db = sqlite3.connect(path)
    db.execute("UPDATE meta SET value='01' WHERE key='schema_version'")
    db.commit()
    db.close()
    before = _store_snapshot(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)

    assert _store_snapshot(path) == before


@pytest.mark.parametrize(("table", "constraint"), [
    ("tasks", " CHECK (horizon IN ('short','medium','long'))"),
    ("tasks", " CHECK (status IN ('open','done'))"),
    ("focus", " CHECK (id = 1)"),
])
def test_v1_missing_check_constraints_is_refused_and_unchanged(
        tmp_path, table, constraint):
    path = tmp_path / "tasks.db"
    TaskRepository(path).close()
    db = sqlite3.connect(path)
    db.execute("PRAGMA writable_schema=ON")
    original_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()[0]
    assert constraint in original_sql
    db.execute(
        "UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?",
        (original_sql.replace(constraint, ""), table))
    db.execute("PRAGMA writable_schema=OFF")
    db.commit()
    db.close()
    before = _store_snapshot(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)

    assert _store_snapshot(path) == before


@pytest.mark.parametrize("indexed_expression", [
    "due_date DESC",
    "due_date COLLATE NOCASE",
])
def test_v1_index_sort_or_collation_mismatch_is_refused(
        tmp_path, indexed_expression):
    path = tmp_path / "tasks.db"
    TaskRepository(path).close()
    db = sqlite3.connect(path)
    db.execute("DROP INDEX idx_tasks_due")
    db.execute(
        f"CREATE INDEX idx_tasks_due ON tasks({indexed_expression})")
    db.commit()
    db.close()
    before = _store_snapshot(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)

    assert _store_snapshot(path) == before


def test_live_hardlink_swap_is_refused_before_schema_use(tmp_path):
    path = tmp_path / "tasks.db"
    TaskRepository(path).close()
    peer = tmp_path / "live-peer.db"
    before = _store_snapshot(path)
    swapped = False
    traced: list[str] = []
    authorized: list[tuple[int, str | None, str | None]] = []

    def hardlinking_connect(target, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            try:
                os.link(path, peer)
            except OSError as exc:
                pytest.skip(f"hard links unavailable: {exc}")
        db = sqlite3.connect(target, *args, **kwargs)
        db.set_trace_callback(traced.append)

        def reject_sql(action, arg1, arg2, _database, _trigger):
            authorized.append((action, arg1, arg2))
            return sqlite3.SQLITE_DENY

        db.set_authorizer(reject_sql)
        return db

    try:
        with pytest.raises(TaskStoreUnavailable):
            TaskRepository(path, connection_factory=hardlinking_connect)
        assert _store_snapshot(path) == before
        assert traced == []
        assert authorized == []
    finally:
        peer.unlink(missing_ok=True)


def test_replaced_create_partial_is_left_untouched(tmp_path):
    path = tmp_path / "tasks.db"
    foreign = b"FOREIGN-PARTIAL-CANARY"
    observed: list[Path] = []

    def replacing_connect(target, *_args, **_kwargs):
        partial = Path(target)
        observed.append(partial)
        partial.unlink()
        partial.write_bytes(foreign)
        raise sqlite3.OperationalError("injected connect failure")

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path, connection_factory=replacing_connect)

    assert not path.exists()
    assert len(observed) == 1
    assert observed[0].read_bytes() == foreign


def test_malformed_rollback_journal_is_fail_closed_and_unchanged(tmp_path):
    path = tmp_path / "tasks.db"
    TaskRepository(path).close()
    Path(f"{path}-journal").write_bytes(b"MALFORMED-JOURNAL-CANARY")
    before = _store_snapshot(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)

    assert _store_snapshot(path) == before


def test_real_hot_rollback_journal_is_validated_then_recovered(tmp_path):
    path = tmp_path / "tasks.db"
    repo = TaskRepository(path)
    timestamp = datetime.now().isoformat(timespec="seconds")
    rows = [
        (f"hot-{index}", None, "x" * 512, "short", "open", index,
         None, timestamp, timestamp, None)
        for index in range(2_000)
    ]
    repo.db.executemany(
        "INSERT INTO tasks (id,parent_id,title,horizon,status,sort_key,"
        "due_date,created_at,updated_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows)
    repo.db.commit()
    repo.close()

    ready = tmp_path / "hot-ready"
    child_code = r"""
import os, sqlite3, sys, time
db_path, ready_path = sys.argv[1], sys.argv[2]
db = sqlite3.connect(db_path)
db.execute('PRAGMA journal_mode=DELETE')
db.execute('PRAGMA synchronous=FULL')
db.execute('PRAGMA cache_size=1')
db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
db.execute("UPDATE tasks SET title=title || '-UNCOMMITTED'")
journal = db_path + '-journal'
deadline = time.time() + 10
while time.time() < deadline:
    if os.path.exists(journal) and os.path.getsize(journal) > 512:
        with open(journal, 'rb') as handle:
            if handle.read(8) == bytes.fromhex('d9d505f920a163d7'):
                with open(ready_path, 'w', encoding='ascii') as signal:
                    signal.write('ready')
                break
    time.sleep(0.01)
time.sleep(60)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(path), str(ready)])
    try:
        deadline = time.monotonic() + 12
        while not ready.exists() and child.poll() is None \
                and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "child did not produce a real hot journal"
        child.kill()
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)

    journal = Path(f"{path}-journal")
    assert journal.is_file()
    recovered = TaskRepository(path)
    try:
        assert recovered.db.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] \
            == "1"
        assert recovered.db.execute(
            "SELECT COUNT(*) FROM tasks WHERE title LIKE '%UNCOMMITTED%'"
        ).fetchone()[0] == 0
    finally:
        recovered.close()
    assert not journal.exists()


def test_main_only_startup_is_constant_shape_for_10k_tasks(
        tmp_path, monkeypatch):
    import retirement_pet.todo.repository as repository_module

    path = tmp_path / "tasks.db"
    repo = TaskRepository(path)
    timestamp = datetime.now().isoformat(timespec="seconds")
    repo.db.executemany(
        "INSERT INTO tasks (id,parent_id,title,horizon,status,sort_key,"
        "due_date,created_at,updated_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (f"bulk-{index}", None, f"private-{index}", "short", "open",
             index, None, timestamp, timestamp, None)
            for index in range(10_000)
        ])
    repo.db.commit()
    repo.close()

    real_connect = sqlite3.connect
    traced: list[str] = []
    task_reads: list[tuple[str | None, str | None]] = []

    def tracing_connect(*args, **kwargs):
        db = real_connect(*args, **kwargs)
        db.set_trace_callback(traced.append)

        def deny_task_rows(action, arg1, arg2, _database, _trigger):
            if action == sqlite3.SQLITE_READ and arg1 == "tasks":
                task_reads.append((arg1, arg2))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(deny_task_rows)
        return db

    monkeypatch.setattr(repository_module.sqlite3, "connect", tracing_connect)
    started = time.perf_counter()
    reopened = TaskRepository(path, connection_factory=tracing_connect)
    reopened.close()
    elapsed = time.perf_counter() - started
    statements = "\n".join(traced).lower()
    assert task_reads == []
    assert "quick_check" not in statements
    assert "foreign_key_check" not in statements
    assert " from tasks" not in statements
    assert elapsed < 2.0


# -- Phase 2A service transactions --------------------------------------------------


def test_rename_failure_does_not_mutate_cached_task(service_harness):
    service, repository, events = service_harness
    task = service.add_task("original", Horizon.SHORT)
    events.clear()
    before_ref = service.get(task.id)
    before = replace(before_ref)
    repository.db.execute(
        "CREATE TRIGGER fail_rename BEFORE UPDATE ON tasks "
        "BEGIN SELECT RAISE(ABORT, 'injected rename failure'); END")

    with pytest.raises(sqlite3.DatabaseError):
        service.rename(task.id, "must-not-stick")
    assert service.get(task.id) is before_ref
    assert service.get(task.id) == before
    assert repository.db.execute(
        "SELECT title FROM tasks WHERE id=?", (task.id,)).fetchone() == \
        ("original",)
    assert events == []


@pytest.mark.parametrize("operation", ["horizon", "due", "restore"])
def test_scalar_update_failure_preserves_before_image_and_events(
        service_harness, operation):
    service, repository, events = service_harness
    task = service.add_task("before-image", Horizon.SHORT)
    if operation == "restore":
        service.complete(task.id)
    events.clear()
    before_ref = service.get(task.id)
    before = replace(before_ref)

    if operation == "horizon":
        repository.db.execute(
            "CREATE TRIGGER fail_horizon BEFORE UPDATE OF horizon ON tasks "
            "BEGIN SELECT RAISE(ABORT, 'injected horizon failure'); END")
        mutate = lambda: service.set_horizon(task.id, Horizon.LONG)
    elif operation == "due":
        repository.db.execute(
            "CREATE TRIGGER fail_due BEFORE UPDATE OF due_date ON tasks "
            "BEGIN SELECT RAISE(ABORT, 'injected due failure'); END")
        mutate = lambda: service.set_due_date(task.id, date(2035, 7, 8))
    else:
        repository.db.execute(
            "CREATE TRIGGER fail_restore BEFORE UPDATE OF status ON tasks "
            "WHEN NEW.status='open' "
            "BEGIN SELECT RAISE(ABORT, 'injected restore failure'); END")
        mutate = lambda: service.restore(task.id)

    with pytest.raises(sqlite3.DatabaseError):
        mutate()
    assert service.get(task.id) is before_ref
    assert service.get(task.id) == before
    assert events == []


def test_set_horizon_focus_read_failure_precedes_database_and_cache_publish(
        service_harness, monkeypatch):
    """A failed focus read must not turn set_horizon into a partial success."""
    service, repository, events = service_harness
    task = service.add_task("focus-read-before-write", Horizon.SHORT)
    service.start_focus(task.id)
    before_ref = service.get(task.id)
    before_projection = service.load_focus_projection()
    events.clear()

    def fail_focus_read():
        raise sqlite3.OperationalError("injected focus read failure")

    monkeypatch.setattr(repository, "get_focus", fail_focus_read)
    with pytest.raises(sqlite3.OperationalError):
        service.set_horizon(task.id, Horizon.LONG)

    assert service.get(task.id) is before_ref
    assert service.get(task.id).horizon is Horizon.SHORT
    assert service.load_focus_projection() == before_projection
    assert repository.db.execute(
        "SELECT horizon FROM tasks WHERE id=?", (task.id,)).fetchone() == \
        ("short",)
    assert events == []


def test_reorder_failure_rolls_back_db_cache_and_event(service_harness):
    service, repository, events = service_harness
    tasks = [service.add_task(title, Horizon.SHORT)
             for title in ("one", "two", "three")]
    before_refs = tuple(service.get(task.id) for task in tasks)
    before_cache = [(t.id, t.sort_key) for t in service.children_of(None)]
    before_db = repository.db.execute(
        "SELECT id, sort_key FROM tasks ORDER BY sort_key").fetchall()
    events.clear()
    repository.db.execute(
        "CREATE TRIGGER fail_second_reorder "
        "BEFORE UPDATE OF sort_key ON tasks WHEN NEW.sort_key = 1 "
        "BEGIN SELECT RAISE(ABORT, 'injected reorder failure'); END")

    with pytest.raises(sqlite3.DatabaseError):
        service.move_within_siblings(tasks[1].id, 1)
    assert [(t.id, t.sort_key) for t in service.children_of(None)] == \
        before_cache
    assert all(service.get(task.id) is old
               for task, old in zip(tasks, before_refs, strict=True))
    assert repository.db.execute(
        "SELECT id, sort_key FROM tasks ORDER BY sort_key").fetchall() == \
        before_db
    assert events == []


def test_delete_failure_preserves_tree_focus_cache_and_event(service_harness):
    service, repository, events = service_harness
    root = service.add_task("root", Horizon.SHORT)
    child = service.add_subtask(root.id, "child")
    service.start_focus(child.id)
    events.clear()
    before_ids = {t.id for t in service.all_tasks()}
    before_db = repository.db.execute(
        "SELECT id FROM tasks ORDER BY id").fetchall()
    repository.db.execute(
        "CREATE TRIGGER fail_root_delete BEFORE DELETE ON tasks "
        "WHEN OLD.parent_id IS NULL "
        "BEGIN SELECT RAISE(ABORT, 'injected delete failure'); END")

    with pytest.raises(sqlite3.DatabaseError):
        service.delete_subtree(root.id)
    assert {t.id for t in service.all_tasks()} == before_ids
    assert service.focus_task_id() == child.id
    assert repository.db.execute(
        "SELECT id FROM tasks ORDER BY id").fetchall() == before_db
    assert events == []


def test_complete_focus_clear_failure_is_fully_atomic(service_harness):
    service, repository, events = service_harness
    task = service.add_task("focus", Horizon.MEDIUM)
    service.start_focus(task.id)
    events.clear()
    before_ref = service.get(task.id)
    repository.db.execute(
        "CREATE TRIGGER fail_focus_clear BEFORE DELETE ON focus "
        "BEGIN SELECT RAISE(ABORT, 'injected focus clear failure'); END")

    with pytest.raises(sqlite3.DatabaseError):
        service.complete(task.id)
    assert service.get(task.id) is before_ref
    assert service.get(task.id).status is Status.OPEN
    assert service.focus_task_id() == task.id
    assert repository.db.execute(
        "SELECT status FROM tasks WHERE id=?", (task.id,)).fetchone()[0] \
        == "open"
    assert events == []


def test_focused_complete_emits_changed_once(service_harness):
    service, _repository, events = service_harness
    task = service.add_task("focus", Horizon.MEDIUM)
    service.start_focus(task.id)
    events.clear()
    service.complete(task.id)
    assert events == ["focus_stopped", "changed"]


def test_focused_delete_emits_after_atomic_final_state(service_harness):
    service, repository, events = service_harness
    root = service.add_task("delete-root", Horizon.SHORT)
    child = service.add_subtask(root.id, "delete-child")
    service.start_focus(child.id)
    events.clear()

    assert service.delete_subtree(root.id) == 2
    assert events == ["focus_stopped", "changed"]
    assert service.focus_task_id() is None
    assert service.load_focus_projection() == todo_domain.FocusProjection(
        focusing=False, horizon=None)
    assert service.get(root.id) is None
    assert service.get(child.id) is None
    assert repository.db.execute("SELECT COUNT(*) FROM tasks").fetchone() == (0,)


def test_reorder_success_publishes_one_complete_batch(service_harness):
    service, repository, events = service_harness
    tasks = [service.add_task(title, Horizon.SHORT)
             for title in ("one", "two", "three")]
    before_refs = tuple(service.get(task.id) for task in tasks)
    events.clear()

    service.move_within_siblings(tasks[0].id, 2)

    assert events == ["changed"]
    cache_order = [(task.id, task.sort_key)
                   for task in service.children_of(None)]
    db_order = repository.db.execute(
        "SELECT id, sort_key FROM tasks ORDER BY sort_key").fetchall()
    assert cache_order == db_order == [
        (tasks[1].id, 0), (tasks[2].id, 1), (tasks[0].id, 2)]
    assert all(service.get(task.id) is not old
               for task, old in zip(tasks, before_refs, strict=True))
    assert [old.sort_key for old in before_refs] == [0, 1, 2]


def test_adjacent_reorder_replaces_every_sibling_after_commit(service_harness):
    service, repository, events = service_harness
    tasks = [service.add_task(title, Horizon.SHORT)
             for title in ("one", "two", "three", "four")]
    before_refs = tuple(service.get(task.id) for task in tasks)
    events.clear()

    service.move_within_siblings(tasks[1].id, 1)

    assert events == ["changed"]
    assert [(task.id, task.sort_key)
            for task in service.children_of(None)] == [
                (tasks[0].id, 0), (tasks[2].id, 1),
                (tasks[1].id, 2), (tasks[3].id, 3)]
    assert repository.db.execute(
        "SELECT id, sort_key FROM tasks ORDER BY sort_key").fetchall() == [
            (tasks[0].id, 0), (tasks[2].id, 1),
            (tasks[1].id, 2), (tasks[3].id, 3)]
    assert all(service.get(task.id) is not old
               for task, old in zip(tasks, before_refs, strict=True))
    assert [old.sort_key for old in before_refs] == [0, 1, 2, 3]


def test_delete_many_checks_focus_only_inside_immediate_transaction(tmp_path):
    repository = TaskRepository(tmp_path / "tasks.db")
    survivor = todo_domain.Task.create("survivor", Horizon.SHORT)
    doomed = todo_domain.Task.create("doomed", Horizon.SHORT)
    repository.apply_batch(upserts=(survivor, doomed), focus=survivor.id)
    traced: list[str] = []
    repository.db.set_trace_callback(traced.append)

    try:
        cleared = repository.delete_many([doomed.id])
    finally:
        repository.db.set_trace_callback(None)

    normalized = [statement.strip().upper() for statement in traced]
    begin_index = next(index for index, statement in enumerate(normalized)
                       if statement.startswith("BEGIN IMMEDIATE"))
    focus_index = next(index for index, statement in enumerate(normalized)
                       if "SELECT TASK_ID FROM FOCUS" in statement)
    assert begin_index < focus_index
    assert cleared is False
    assert repository.get_focus()[0] == survivor.id
    assert repository.db.execute(
        "SELECT COUNT(*) FROM tasks WHERE id=?", (doomed.id,)).fetchone() == (0,)
    repository.close()


def test_success_listener_observes_committed_db_cache_and_projection(tmp_path):
    repository = TaskRepository(tmp_path / "tasks.db")
    observed = []
    holder = {}
    watching = False

    def listener(event: str) -> None:
        if not watching:
            return
        service = holder["service"]
        cached = service.get(holder["task_id"])
        db_status = repository.db.execute(
            "SELECT status FROM tasks WHERE id=?",
            (holder["task_id"],)).fetchone()[0]
        observed.append((event, cached.status, db_status,
                         service.focus_task_id(),
                         service.load_focus_projection()))

    service = TodoService(repository, on_event=listener)
    holder["service"] = service
    task = service.add_task("callback-consistency", Horizon.MEDIUM)
    holder["task_id"] = task.id
    service.start_focus(task.id)
    old_ref = service.get(task.id)
    watching = True
    try:
        service.complete(task.id)
        assert [entry[0] for entry in observed] == [
            "focus_stopped", "changed"]
        assert all(entry[1] is Status.DONE for entry in observed)
        assert all(entry[2] == "done" for entry in observed)
        assert all(entry[3] is None for entry in observed)
        assert all(entry[4] == todo_domain.FocusProjection(
            focusing=False, horizon=None) for entry in observed)
        assert service.get(task.id) is not old_ref
        assert old_ref.status is Status.OPEN
    finally:
        service.close()


def test_logical_operations_emit_exact_events_and_noops_emit_none(
        service_harness):
    service, _repository, events = service_harness
    task = service.add_task("event-contract", Horizon.SHORT)
    assert events == ["changed"]

    noops = (
        lambda: service.rename(task.id, " event-contract "),
        lambda: service.set_horizon(task.id, Horizon.SHORT),
        lambda: service.set_due_date(task.id, None),
        lambda: service.restore(task.id),
        lambda: service.move_within_siblings(task.id, -1),
        lambda: service.move_within_siblings(task.id, 0),
        lambda: service.stop_focus(),
    )
    for no_op in noops:
        events.clear()
        no_op()
        assert events == []

    events.clear()
    assert service.start_focus(task.id) is True
    assert events == ["focus_started", "changed"]
    events.clear()
    assert service.start_focus(task.id) is False
    assert events == []

    service.complete(task.id)
    assert events == ["focus_stopped", "changed"]
    events.clear()
    service.complete(task.id)
    assert events == []

    service.restore(task.id)
    assert events == ["changed"]
    events.clear()
    service.restore(task.id)
    assert events == []


def test_sync_focus_and_projection_never_load_full_tree(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    first = TaskRepository(path)
    timestamp = datetime.now().isoformat(timespec="seconds")
    focus_id = "safe-focus-row"
    first.db.executemany(
        "INSERT INTO tasks (id,parent_id,title,horizon,status,sort_key,"
        "due_date,created_at,updated_at,completed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (focus_id if index == 9_999 else f"projection-{index}",
             None, f"PRIVATE-TITLE-{index}",
             "long" if index == 9_999 else "short", "open", index,
             "2099-12-31" if index == 9_999 else None,
             timestamp, timestamp, None)
            for index in range(10_000)
        ])
    first.db.commit()
    first.set_focus(focus_id)
    first.close()

    repository = TaskRepository(path)
    service = TodoService(repository)
    task_columns: list[str | None] = []

    def forbidden_load_all():
        raise AssertionError("focus projection must not load the task tree")

    def allow_safe_focus_columns(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and table == "tasks":
            task_columns.append(column)
            if column not in {"id", "status", "horizon"}:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    monkeypatch.setattr(repository, "load_all", forbidden_load_all)
    repository.db.set_authorizer(allow_safe_focus_columns)
    vm_steps = 0

    def bound_vm_work() -> int:
        nonlocal vm_steps
        vm_steps += 1
        return 1 if vm_steps > 5_000 else 0

    repository.db.set_progress_handler(bound_vm_work, 1)
    projection = service.load_focus_projection()
    repository.db.set_progress_handler(None, 0)
    store = ContextStore()
    todo_bridge = TodoContextBridge(store)
    todo_bridge.sync_focus(projection)
    assert service._loaded is False
    assert set(task_columns) <= {"id", "status", "horizon"}
    assert "title" not in task_columns
    assert "due_date" not in task_columns
    assert vm_steps < 5_000
    assert store.is_active(ContextId.WORKING)
    assert todo_bridge.projection == projection
    assert projection == todo_domain.FocusProjection(
        focusing=True, horizon=Horizon.LONG)
    assert not hasattr(todo_bridge, "_service")
    service.close()


def test_focus_projection_privacy_canary_includes_no_id_due_or_title(
        tmp_path, caplog):
    canaries = ("TITLE-CANARY", "ID-CANARY", "2099-12-31")
    service = TodoService(TaskRepository(tmp_path / "tasks.db"))
    task = service.add_task(canaries[0], Horizon.SHORT,
                            due_date=date.fromisoformat(canaries[2]))
    service.start_focus(task.id)
    projection = service.load_focus_projection()
    store = ContextStore()
    bridge = TodoContextBridge(store)
    with caplog.at_level(logging.DEBUG):
        bridge.sync_focus(projection)
        safe_surfaces = repr((_safe_context_view(store), bridge.projection)) + \
            "\n".join(record.getMessage() for record in caplog.records)
    for canary in (canaries[0], task.id, canaries[2]):
        assert canary not in safe_surfaces
    service.close()


def test_bridge_accepts_only_safe_projection_and_keeps_cached_value():
    store = ContextStore()
    bridge = TodoContextBridge(store)
    projection = todo_domain.FocusProjection(
        focusing=True, horizon=Horizon.MEDIUM)
    bridge.sync_focus(projection)
    assert bridge.projection == projection
    assert store.is_active(ContextId.WORKING)
    context_before = _safe_context_view(store)

    private_task = todo_domain.Task.create(
        "PRIVATE-TASK-CANARY", Horizon.SHORT,
        due_date=date(2099, 1, 2))
    with pytest.raises(TypeError):
        bridge.sync_focus(private_task)
    assert bridge.projection == projection
    assert _safe_context_view(store) == context_before
    assert "PRIVATE-TASK-CANARY" not in repr(bridge.projection)


def test_focus_projection_has_read_only_panel_compatibility():
    projection = todo_domain.FocusProjection(
        focusing=True, horizon=Horizon.MEDIUM)
    assert projection["focusing"] is True
    assert projection["horizon"] == "medium"
    assert todo_domain.FocusProjection(
        focusing=False, horizon=None)["horizon"] is None
    with pytest.raises(KeyError):
        _ = projection["title"]
    with pytest.raises(TypeError):
        projection["focusing"] = False
    with pytest.raises(FrozenInstanceError):
        projection.focusing = False


def test_listener_failure_log_is_fixed_and_contains_no_exception(tmp_path, caplog):
    repository = TaskRepository(tmp_path / "tasks.db")
    holder = {}

    def malicious_listener(_event: str) -> None:
        task = holder["service"].all_tasks()[0]
        raise RuntimeError(
            f"{task.title}|{task.id}|{task.due_date.isoformat()}")

    service = TodoService(repository, on_event=malicious_listener)
    holder["service"] = service
    with caplog.at_level(logging.ERROR):
        task = service.add_task(
            "LISTENER-TITLE-CANARY", Horizon.LONG,
            due_date=date(2099, 12, 30))
    records = [record for record in caplog.records
               if record.name == "retirement_pet.todo.service"]
    rendered = []
    for record in records:
        rendered.append(record.getMessage())
        rendered.append(repr(record.args))
        rendered.append(repr(record.exc_info))
        rendered.append(repr(record.exc_text))
        rendered.append(repr(record.stack_info))
        if record.exc_info is not None:
            rendered.extend(traceback.format_exception(*record.exc_info))
    dumped = "\n".join(rendered)
    for canary in (task.title, task.id, task.due_date.isoformat()):
        assert canary not in dumped
    assert records
    assert {record.getMessage() for record in records} == {
        "todo event listener failed"}
    assert all(record.exc_info is None for record in records)
    service.close()


# -- UI integration ------------------------------------------------------------------------------


@pytest.fixture()
def app(qt_application, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    _isolate_startup_registry(monkeypatch)

    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-todo-{tmp_path.name}")
    yield pet
    pet.shutdown()


def test_todo_service_lazy_until_needed(app, qt_application):
    assert app._todo_service is None  # startup loads nothing
    assert not (app._data_dir / "tasks.db").exists()


def test_todo_page_lazy_and_event_driven(app, qt_application):
    app._open_control_panel("todo")
    assert app._todo_service is not None  # created on first page visit
    page = app._panel._built["todo"]
    events_before = page._todo_service_events

    app.todo.add_task("任务A", Horizon.SHORT)
    assert page._todo_service_events == events_before + 1  # event, no poll


def test_panel_closed_no_polling(app, qt_application):
    app._open_control_panel("todo")
    page = app._panel._built["todo"]
    app._panel.accept()
    _flush_deferred_deletes(qt_application)
    events_before = page._todo_service_events
    app.todo.add_task("关闭后真实事件", Horizon.SHORT)
    assert page._todo_service_events == events_before


def test_todo_db_location_isolated(app, qt_application, tmp_path):
    app.todo.add_task("隔离检查", Horizon.SHORT)
    db_path = tmp_path / "tasks.db"
    assert db_path.is_file()
    # not inside the pack library, settings or evidence
    assert not (app.library.root / "tasks.db").exists()
    assert not (tmp_path / "evidence").exists() or \
        not (tmp_path / "evidence" / "tasks.db").exists()


def test_todo_focus_drives_pet_context(app, qt_application):
    from retirement_pet.models import ActionId

    task = app.todo.add_task("专注开发", Horizon.SHORT)
    app.todo.start_focus(task.id)
    assert app.contexts.is_active(ContextId.WORKING)
    assert app.controller.current_action() is ActionId.WORK

    app.todo.complete(task.id)  # completing focus re-resolves
    assert not app.contexts.is_active(ContextId.WORKING)


def test_todo_survives_character_switch(app, qt_application):
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    app.library.install(root / "tests/fixtures/petpack/minimal-static.petpack")
    task = app.todo.add_task("跨角色任务", Horizon.MEDIUM)
    app.todo.start_focus(task.id)

    entry = next(e for e in app.catalog.entries() if e.character_id == "demo")
    assert app._switch_character(entry) is True

    # task store and focus survive the switch untouched
    assert app.todo.get(task.id).title == "跨角色任务"
    assert app.todo.focus_task_id() == task.id
    app.todo.stop_focus()


def test_todo_title_privacy_canary(
        app, qt_application, caplog, tmp_path):
    title = "CANARY-TODO-隐私标题"
    due = date(2099, 12, 31)
    task = app.todo.add_task(title, Horizon.SHORT, due_date=due)
    with caplog.at_level(logging.DEBUG):
        app.todo.start_focus(task.id)
        app._open_control_panel("todo")
        app._panel.accept()
    safe_surfaces = [
        repr(_safe_context_view(app.contexts)),
        repr(app.todo_bridge.projection),
        repr(app.perf.events),
        "\n".join(f"{r.name}: {r.getMessage()}" for r in caplog.records),
    ]
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.suffix in {".log", ".jsonl"}:
            safe_surfaces.append(path.read_text(encoding="utf-8"))
    dumped = "\n".join(safe_surfaces)
    for canary in (title, task.id, due.isoformat()):
        assert canary not in dumped


def test_app_todo_listener_failures_are_private_and_do_not_rollback(
        app, qt_application, monkeypatch, caplog):
    """Bridge/UI listeners are post-commit and log only fixed error codes."""
    title = "APP-LISTENER-TITLE-CANARY"
    due = date(2099, 11, 29)
    task = app.todo.add_task(title, Horizon.SHORT, due_date=due)
    original_sync = app.todo_bridge.sync_focus
    ui_observations = []

    def malicious_sync(projection):
        original_sync(projection)
        raise RuntimeError(f"{title}|{task.id}|{due.isoformat()}")

    def malicious_ui_listener(_event):
        ui_observations.append(app.todo_bridge.projection)
        raise RuntimeError(f"{title}|{task.id}|{due.isoformat()}")

    monkeypatch.setattr(app.todo_bridge, "sync_focus", malicious_sync)
    unsubscribe = app.subscribe_todo_events(malicious_ui_listener)
    try:
        with caplog.at_level(logging.ERROR):
            assert app.todo.start_focus(task.id) is True
    finally:
        unsubscribe()

    assert app.todo.focus_task_id() == task.id
    assert app.contexts.owner_of(ContextId.WORKING) == "todo"
    assert ui_observations
    assert all(projection == todo_domain.FocusProjection(
        focusing=True, horizon=Horizon.SHORT)
        for projection in ui_observations)
    records = [record for record in caplog.records
               if record.name == "retirement_pet.app"
               and record.levelno >= logging.ERROR]
    assert records
    assert {record.getMessage() for record in records} == {
        "todo context synchronization failed",
        "todo UI listener failed",
    }
    rendered = []
    for record in records:
        rendered.extend((record.getMessage(), repr(record.args),
                         repr(record.exc_info), repr(record.exc_text),
                         repr(record.stack_info), repr(record.__dict__)))
        if record.exc_info is not None:
            rendered.extend(traceback.format_exception(*record.exc_info))
    dumped = "\n".join(rendered)
    for canary in (title, task.id, due.isoformat()):
        assert canary not in dumped
    assert all(record.exc_info is None for record in records)


def test_pet_surface_unchanged_by_todo_panel(app, qt_application):
    from PySide6.QtCore import Qt

    app._show_window()
    flags_before = app.window.windowFlags()
    app._open_control_panel("todo")
    app._panel.accept()
    assert app.window.windowFlags() == flags_before
    assert bool(app.window.testAttribute(Qt.WA_TranslucentBackground))
    app._hide_window()


# -- Phase 2B usable Todo panel ---------------------------------------------------


def test_inline_rename_persists_without_focus_marker_or_refresh_reentry(
        app, qt_application, monkeypatch, tmp_path):
    task = app.todo.add_task("真实原标题", Horizon.SHORT)
    app.todo.start_focus(task.id)
    app._open_control_panel("todo")
    page = app._panel._built["todo"]
    item = _tree_item_by_id(page.tree, task.id)

    # Focus is presentation state, never part of the editable title cell.
    assert item.text(0) == "真实原标题"
    calls = []
    real_rename = app.todo.rename

    def counted_rename(task_id, title):
        calls.append((task_id, title))
        return real_rename(task_id, title)

    monkeypatch.setattr(app.todo, "rename", counted_rename)
    item.setText(0, "持久化新标题")  # real itemChanged commit path
    qt_application.processEvents()
    assert calls == [(task.id, "持久化新标题")]
    assert app.todo.get(task.id).title == "持久化新标题"

    page.refresh()  # programmatic rebuilding must not recursively rename
    assert calls == [(task.id, "持久化新标题")]
    assert _tree_item_by_id(page.tree, task.id).text(0) == "持久化新标题"

    reader = TaskRepository(tmp_path / "tasks.db")
    try:
        assert reader.load_all()[task.id].title == "持久化新标题"
    finally:
        reader.close()

    app._panel.accept()
    _flush_deferred_deletes(qt_application)
    app._open_control_panel("todo")
    reopened = app._panel._built["todo"]
    assert _tree_item_by_id(reopened.tree, task.id).text(0) == \
        "持久化新标题"


@pytest.mark.parametrize("invalid_title", ["   ", "PRIVATE-" + "X" * 201])
def test_invalid_inline_rename_rolls_cell_back_without_writing_or_escaping(
        app, qt_application, monkeypatch, caplog, tmp_path, invalid_title):
    task = app.todo.add_task("authoritative-title", Horizon.SHORT)
    app._open_control_panel("todo")
    page = app._panel._built["todo"]
    before = _store_snapshot(tmp_path / "tasks.db")
    escaped = []
    monkeypatch.setattr(
        sys, "excepthook",
        lambda exc_type, exc, tb: escaped.append((exc_type, exc, tb)))

    with caplog.at_level(logging.ERROR):
        item = _tree_item_by_id(page.tree, task.id)
        item.setText(0, invalid_title)  # real itemChanged failure path
        qt_application.processEvents()

    assert escaped == []
    assert app.todo.get(task.id).title == "authoritative-title"
    assert _store_snapshot(tmp_path / "tasks.db") == before
    assert page.status.text() == "操作未完成，请检查任务状态"
    assert _tree_item_by_id(page.tree, task.id).text(0) == \
        "authoritative-title"
    reader = TaskRepository(tmp_path / "tasks.db")
    try:
        assert reader.load_all()[task.id].title == "authoritative-title"
    finally:
        reader.close()
    rendered = "\n".join(
        repr(value)
        for record in caplog.records
        if record.name == "retirement_pet.ui.panel.todo_page"
        for value in (record.getMessage(), record.args, record.exc_info,
                      record.exc_text, record.stack_info, record.__dict__))
    assert invalid_title not in rendered


def test_due_date_set_clear_and_cancel_are_distinct_persistent_actions(
        app, qt_application, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QDialog
    from retirement_pet.ui.panel.todo_page import TodoPage

    task = app.todo.add_task(
        "日期三态", Horizon.MEDIUM, due_date=date(2030, 1, 2))
    app._open_control_panel("todo")
    page = app._panel._built["todo"]
    outcomes = iter((
        ("set", date(2031, 2, 3)),
        ("clear", None),
        ("cancel", None),
    ))
    monkeypatch.setattr(
        TodoPage, "_ask_due_date", lambda _self, _task: next(outcomes),
        raising=False)

    def forbidden_modal_exec(_dialog):
        raise AssertionError("date handler bypassed the testable three-state seam")

    monkeypatch.setattr(QDialog, "exec", forbidden_modal_exec)

    page.tree.setCurrentItem(_tree_item_by_id(page.tree, task.id))
    _todo_button(page, "设截止").click()
    assert app.todo.get(task.id).due_date == date(2031, 2, 3)

    page.tree.setCurrentItem(_tree_item_by_id(page.tree, task.id))
    _todo_button(page, "设截止").click()
    assert app.todo.get(task.id).due_date is None

    app.todo.set_due_date(task.id, date(2032, 4, 5))
    page.tree.setCurrentItem(_tree_item_by_id(page.tree, task.id))
    _todo_button(page, "设截止").click()
    assert app.todo.get(task.id).due_date == date(2032, 4, 5)

    reader = TaskRepository(tmp_path / "tasks.db")
    try:
        assert reader.load_all()[task.id].due_date == date(2032, 4, 5)
    finally:
        reader.close()


@pytest.mark.parametrize(
    ("action", "expected"),
    (
        ("set", ("set", date(2034, 6, 7))),
        ("clear", ("clear", None)),
        ("cancel", ("cancel", None)),
    ),
)
def test_due_dialog_real_buttons_return_three_distinct_results(
        app, qt_application, action, expected):
    from PySide6.QtCore import QDate, QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QDateEdit,
        QDialog,
        QDialogButtonBox,
        QPushButton,
    )

    task = app.todo.add_task(
        f"real-due-{action}", Horizon.SHORT,
        due_date=date(2033, 1, 2))
    app._open_control_panel("todo")
    page = app._panel._built["todo"]
    assert hasattr(page, "_ask_due_date")
    callback_errors = []

    def drive_modal():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QDialog)
            assert dialog.windowTitle() == "设置截止日期"
            picker = dialog.findChild(QDateEdit)
            box = dialog.findChild(QDialogButtonBox)
            assert picker is not None and box is not None
            if action == "set":
                picker.setDate(QDate(2034, 6, 7))
                box.button(QDialogButtonBox.Ok).click()
            elif action == "clear":
                clear = dialog.findChild(QPushButton, "todo_due_clear")
                assert clear is not None
                clear.click()
            else:
                box.button(QDialogButtonBox.Cancel).click()
        except Exception as exc:  # callback failures must never hang exec()
            callback_errors.append(exc)
            if isinstance(dialog, QDialog):
                dialog.reject()

    QTimer.singleShot(0, drive_modal)
    result = page._ask_due_date(task)
    qt_application.processEvents()
    assert callback_errors == []
    assert result == expected


def test_every_todo_write_signal_contains_unknown_exceptions_privately(
        app, qt_application, monkeypatch, caplog):
    from PySide6.QtWidgets import QDialog, QMessageBox

    focused = app.todo.add_task("focused", Horizon.SHORT)
    other = app.todo.add_task("other", Horizon.SHORT)
    done = app.todo.add_task("done", Horizon.SHORT)
    app.todo.complete(done.id)
    app.todo.start_focus(focused.id)
    app._open_control_panel("todo")
    page = app._panel._built["todo"]
    hooked = []
    monkeypatch.setattr(
        sys, "excepthook",
        lambda exc_type, exc, tb: hooked.append((exc_type, exc, tb)))

    cases = (
        ("add_task", "添加", None),
        ("complete", "完成", focused.id),
        ("restore", "恢复", done.id),
        ("rename", "重命名", other.id),
        ("add_subtask", "加子任务", other.id),
        ("move_within_siblings", "下移", other.id),
        ("set_horizon", "改周期", other.id),
        ("set_due_date", "设截止", other.id),
        ("start_focus", "开始处理", other.id),
        ("stop_focus", "停止处理", focused.id),
        ("delete_subtree", "删除（含子任务）", other.id),
    )

    with caplog.at_level(logging.ERROR):
        for method_name, button_text, selected_id in cases:
            page.refresh()
            page.status.clear()
            if selected_id is not None:
                page.tree.setCurrentItem(
                    _tree_item_by_id(page.tree, selected_id))
            canary = f"SLOT-{method_name}-TITLE-ID-DUE-2099-12-31"

            def explode(*_args, _canary=canary, **_kwargs):
                raise RuntimeError(_canary)

            with monkeypatch.context() as scoped:
                scoped.setattr(app.todo, method_name, explode)
                if method_name in {"add_task", "add_subtask"}:
                    page.input.setText("slot input")
                if method_name == "set_due_date":
                    scoped.setattr(
                        page, "_ask_due_date",
                        lambda _task: ("set", date(2099, 12, 31)),
                        raising=False)
                    scoped.setattr(QDialog, "exec", lambda _dialog: 1)
                if method_name == "delete_subtree":
                    scoped.setattr(
                        QMessageBox, "question",
                        lambda *_args, **_kwargs: QMessageBox.Yes)

                button = _todo_button(page, button_text)
                assert button.isEnabled(), (method_name, button_text)
                button.click()  # real Qt clicked -> slot path
                if method_name == "rename":
                    _tree_item_by_id(page.tree, selected_id).setText(
                        0, "rename signal")  # real itemChanged path
                qt_application.processEvents()

            assert page.status.text() == "操作失败，请稍后重试"

    assert hooked == []
    records = [record for record in caplog.records
               if record.name == "retirement_pet.ui.panel.todo_page"
               and record.levelno >= logging.ERROR]
    assert records
    assert {record.getMessage() for record in records} == {
        "todo UI operation failed"}
    rendered = []
    for record in records:
        rendered.extend((record.getMessage(), repr(record.args),
                         repr(record.exc_info), repr(record.exc_text),
                         repr(record.stack_info), repr(record.__dict__)))
        if record.exc_info is not None:
            rendered.extend(traceback.format_exception(*record.exc_info))
    dumped = "\n".join(rendered)
    assert "SLOT-" not in dumped
    assert "2099-12-31" not in dumped
    assert all(record.exc_info is None for record in records)


@pytest.mark.parametrize(
    ("error", "expected_status"),
    (
        (TodoError("EXPECTED-DOMAIN-CANARY"),
         "操作未完成，请检查任务状态"),
        (TaskStoreUnavailable("EXPECTED-STORE-CANARY"),
         "待办暂不可用，任务文件未作改动"),
    ),
)
def test_todo_write_signal_maps_expected_failures_to_stable_status(
        app, qt_application, monkeypatch, error, expected_status):
    app._open_control_panel("todo")
    page = app._panel._built["todo"]
    hooked = []
    monkeypatch.setattr(
        sys, "excepthook",
        lambda exc_type, exc, tb: hooked.append((exc_type, exc, tb)))
    monkeypatch.setattr(
        app.todo, "add_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    page.input.setText("expected failure")
    _todo_button(page, "添加").click()
    qt_application.processEvents()
    assert hooked == []
    assert page.status.text() == expected_status
    assert "CANARY" not in page.status.text()


def test_unavailable_todo_page_is_read_only_and_preserves_store_family(
        tmp_path, qt_application, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractItemView, QLabel, QPushButton
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    _isolate_startup_registry(monkeypatch)
    path = tmp_path / "tasks.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta VALUES ('schema_version','99')")
    db.commit()
    db.close()
    before = _store_snapshot(path)

    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-todo-readonly-{tmp_path.name}",
    )
    try:
        pet._open_control_panel("todo")
        page = pet._panel._built["todo"]
        labels = "\n".join(label.text()
                           for label in page.findChildren(QLabel))
        assert "待办暂不可用，任务文件未作改动" in labels
        assert "已备份" not in labels
        assert not page.input.isEnabled()
        assert not page.horizon_box.isEnabled()
        assert all(not button.isEnabled()
                   for button in page.findChildren(QPushButton))
        assert page.tree.editTriggers() == QAbstractItemView.NoEditTriggers

        page.input.setText("MUST-NOT-WRITE")
        page.input.returnPressed.emit()  # programmatic bypass attempt
        page._on_add()
        page._on_move(1)
        QTest.keyClick(page.tree, Qt.Key_F2)
        QTest.keyClick(page.tree, Qt.Key_Delete)
        qt_application.processEvents()
        assert _store_snapshot(path) == before
    finally:
        pet.shutdown()


def test_delete_cancel_is_noop_and_confirm_deletes_whole_subtree_once(
        app, qt_application, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    root = app.todo.add_task("root", Horizon.SHORT)
    child = app.todo.add_subtask(root.id, "child")
    grandchild = app.todo.add_subtask(child.id, "grandchild")
    app._open_control_panel("todo")
    page = app._panel._built["todo"]
    page.tree.setCurrentItem(_tree_item_by_id(page.tree, root.id))
    calls = []
    prompts = []
    real_delete = app.todo.delete_subtree

    def counted_delete(task_id):
        calls.append(task_id)
        return real_delete(task_id)

    monkeypatch.setattr(app.todo, "delete_subtree", counted_delete)

    def cancel_question(_parent, _title, message):
        prompts.append(message)
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", cancel_question)
    _todo_button(page, "删除（含子任务）").click()
    assert calls == []
    assert {task.id for task in app.todo.all_tasks()} >= {
        root.id, child.id, grandchild.id}
    assert "该任务及全部子任务" in prompts[-1]
    assert "直接子任务" not in prompts[-1]

    page.tree.setCurrentItem(_tree_item_by_id(page.tree, root.id))
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args, **_kwargs: QMessageBox.Yes)
    _todo_button(page, "删除（含子任务）").click()
    qt_application.processEvents()
    assert calls == [root.id]
    assert app.todo.get(root.id) is None
    assert app.todo.get(child.id) is None
    assert app.todo.get(grandchild.id) is None
    assert page.status.text() == "已删除 3 个任务"


def test_todo_event_subscription_is_idempotent_and_page_lifecycle_is_active_only(
        app, qt_application, monkeypatch):
    observed = []
    unsubscribe = app.subscribe_todo_events(observed.append)
    app.todo.add_task("subscription-one", Horizon.SHORT)
    assert observed == ["changed"]
    unsubscribe()
    unsubscribe()
    app.todo.add_task("subscription-two", Horizon.SHORT)
    assert observed == ["changed"]

    app._open_control_panel("todo")
    panel = app._panel
    page = panel._built["todo"]
    assert len(app.todo_events) == 1
    refresh_calls = []
    real_refresh = page.refresh

    def counted_refresh():
        refresh_calls.append(1)
        return real_refresh()

    monkeypatch.setattr(page, "refresh", counted_refresh)
    event_count = page._todo_service_events
    app.todo.add_task("active-event", Horizon.SHORT)
    assert page._todo_service_events == event_count + 1
    assert len(refresh_calls) == 1

    assert panel.open_page("overview")
    hidden_events = page._todo_service_events
    hidden_refreshes = len(refresh_calls)
    app.todo.add_task("hidden-event", Horizon.SHORT)
    assert page._todo_service_events == hidden_events
    assert len(refresh_calls) == hidden_refreshes

    assert panel.open_page("todo")
    assert len(refresh_calls) == hidden_refreshes + 1
    closed_refreshes = len(refresh_calls)
    panel.accept()  # QDialog.accept hides without a closeEvent on Qt 6.8
    assert panel._built == {}
    assert panel._stack.count() == 0
    _flush_deferred_deletes(qt_application)
    import shiboken6

    assert not shiboken6.isValid(panel)
    assert not shiboken6.isValid(page)
    assert app._panel is None
    assert len(app.todo_events) == 0

    app.todo.add_task("closed-event", Horizon.SHORT)
    assert len(refresh_calls) == closed_refreshes

    app._open_control_panel("todo")
    reopened_page = app._panel._built["todo"]
    assert reopened_page is not page
    assert len(app.todo_events) == 1


def test_refresh_preserves_selection_expansion_and_scroll(
        app, qt_application):
    from PySide6.QtCore import Qt

    root = app.todo.add_task("root", Horizon.SHORT)
    child = app.todo.add_subtask(root.id, "child")
    for index in range(60):
        app.todo.add_task(f"row-{index:02d}", Horizon.SHORT)
    app._open_control_panel("todo")
    panel = app._panel
    panel.resize(720, 480)
    panel.show()
    qt_application.processEvents()
    page = panel._built["todo"]
    root_item = _tree_item_by_id(page.tree, root.id)
    child_item = _tree_item_by_id(page.tree, child.id)
    page.tree.setCurrentItem(child_item)
    # Selecting a hidden child causes Qt to reveal its ancestors.  Collapse
    # after selection so refresh receives an actual collapsed pre-state.
    root_item.setExpanded(False)
    scroll = page.tree.verticalScrollBar()
    assert scroll.maximum() > 0
    wanted_scroll = max(1, scroll.maximum() // 2)
    scroll.setValue(wanted_scroll)

    page.refresh()

    assert page.tree.currentItem().data(0, Qt.UserRole) == child.id
    assert not _tree_item_by_id(page.tree, root.id).isExpanded()
    assert page.tree.verticalScrollBar().value() == wanted_scroll


def test_refresh_consumes_task_collection_in_constant_multiple_of_n(
        app, monkeypatch):
    field_accesses: dict[str, int] = {}
    equality_checks = 0

    class TaskProbe:
        __slots__ = ("_task",)

        def __init__(self, task):
            self._task = task

        def __getattr__(self, name):
            field_accesses[name] = field_accesses.get(name, 0) + 1
            return getattr(self._task, name)

        def __eq__(self, other):
            nonlocal equality_checks
            equality_checks += 1
            return self is other

    class CountingTasks(list):
        def __init__(self, values):
            super().__init__(values)
            self.visits = 0

        def __iter__(self):
            for value in super().__iter__():
                self.visits += 1
                yield value

    count = 240
    roots = [todo_domain.Task.create(
        f"root-{index}", Horizon.SHORT, sort_key=index)
        for index in range(count // 2)]
    children = [todo_domain.Task.create(
        f"child-{index}", Horizon.SHORT, parent_id=root.id, sort_key=0)
        for index, root in enumerate(roots)]
    # This is a valid depth-one forest, but every child precedes its parent.
    # The old repeated-pending implementation scans all unresolved children
    # before each root and list.remove performs another linear search.
    tasks = CountingTasks([
        TaskProbe(task) for task in [*children, *roots]
    ])
    app._open_control_panel("todo")
    page = app._panel._built["todo"]
    monkeypatch.setattr(app.todo, "all_tasks", lambda: tasks)
    monkeypatch.setattr(app.todo, "focus_task_id", lambda: None)

    page.refresh()

    assert tasks.visits <= 8 * len(tasks)
    assert sum(field_accesses.values()) <= 30 * len(tasks)
    assert equality_checks <= 8 * len(tasks)
    assert page.tree.topLevelItemCount() == len(roots)
    for root in roots:
        assert _tree_item_by_id(page.tree, root.id).childCount() == 1


def test_filtered_view_disables_reorder_in_button_and_handler(
        app, qt_application, monkeypatch):
    first = app.todo.add_task("short-one", Horizon.SHORT)
    second = app.todo.add_task("short-two", Horizon.SHORT)
    app.todo.add_task("long", Horizon.LONG)
    app._open_control_panel("todo")
    page = app._panel._built["todo"]
    short_index = page.filter_box.findData(Horizon.SHORT)
    assert short_index >= 0
    page.filter_box.setCurrentIndex(short_index)
    qt_application.processEvents()
    page.tree.setCurrentItem(_tree_item_by_id(page.tree, second.id))
    qt_application.processEvents()
    up = _todo_button(page, "上移")
    down = _todo_button(page, "下移")
    assert not up.isEnabled()
    assert not down.isEnabled()

    calls = []
    monkeypatch.setattr(
        app.todo, "move_within_siblings",
        lambda *args: calls.append(args))
    down.click()
    page._on_move(1)  # double-reject programmatic/shortcut bypass
    assert calls == []

    page.filter_box.setCurrentIndex(0)
    qt_application.processEvents()
    page.tree.setCurrentItem(_tree_item_by_id(page.tree, first.id))
    qt_application.processEvents()
    assert down.isEnabled()


def test_keyboard_f2_delete_and_return_share_real_write_paths(
        app, qt_application, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit, QMessageBox

    task = app.todo.add_task("keyboard-original", Horizon.SHORT)
    app._open_control_panel("todo")
    page = app._panel._built["todo"]
    item = _tree_item_by_id(page.tree, task.id)
    page.tree.setCurrentItem(item)
    page.tree.setFocus()
    QTest.keyClick(page.tree, Qt.Key_F2)
    qt_application.processEvents()
    editor = page.tree.findChild(QLineEdit)
    assert editor is not None and editor.isVisible()
    QTest.keyClick(editor, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(editor, "keyboard-renamed")
    QTest.keyClick(editor, Qt.Key_Return)
    qt_application.processEvents()
    assert app.todo.get(task.id).title == "keyboard-renamed"

    page.input.setText("return-created")
    page.input.setFocus()
    QTest.keyClick(page.input, Qt.Key_Return)
    qt_application.processEvents()
    created = next(entry for entry in app.todo.all_tasks()
                   if entry.title == "return-created")
    assert created.horizon is Horizon.SHORT

    delete_calls = []
    real_delete = app.todo.delete_subtree

    def counted_delete(task_id):
        delete_calls.append(task_id)
        return real_delete(task_id)

    monkeypatch.setattr(app.todo, "delete_subtree", counted_delete)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args, **_kwargs: QMessageBox.Yes)
    page.tree.setCurrentItem(_tree_item_by_id(page.tree, task.id))
    page.tree.setFocus()
    QTest.keyClick(page.tree, Qt.Key_Delete)
    qt_application.processEvents()
    assert delete_calls == [task.id]


def test_action_states_and_two_row_layout_fit_720_by_480(
        app, qt_application):
    from PySide6.QtCore import QPoint, QRect

    open_task = app.todo.add_task("open", Horizon.SHORT)
    done_task = app.todo.add_task("done", Horizon.SHORT)
    app.todo.complete(done_task.id)
    app._open_control_panel("todo")
    panel = app._panel
    panel.resize(720, 480)
    panel.show()
    qt_application.processEvents()
    page = panel._built["todo"]
    labels = (
        "完成", "恢复", "重命名", "加子任务", "上移", "下移",
        "改周期", "设截止", "开始处理", "停止处理", "删除（含子任务）",
    )
    buttons = [_todo_button(page, label) for label in labels]
    assert panel.width() == 720
    assert panel.height() == 480
    assert panel.minimumSizeHint().width() <= 720
    page_rect = page.rect()
    button_rects = []
    for button in buttons:
        origin = button.mapTo(page, QPoint(0, 0))
        geometry = QRect(origin, button.size())
        assert page_rect.contains(geometry)
        assert button.width() >= min(72, button.sizeHint().width())
        button_rects.append(geometry)
    rows = {geometry.y() for geometry in button_rects}
    assert len(rows) == 2
    for index, left in enumerate(button_rects):
        for right in button_rects[index + 1:]:
            if left.y() == right.y():
                assert not left.intersects(right)

    assert all(not button.isEnabled() for button in buttons)
    page.tree.setCurrentItem(_tree_item_by_id(page.tree, open_task.id))
    qt_application.processEvents()
    assert _todo_button(page, "完成").isEnabled()
    assert not _todo_button(page, "恢复").isEnabled()
    assert _todo_button(page, "开始处理").isEnabled()
    assert not _todo_button(page, "停止处理").isEnabled()

    app.todo.start_focus(open_task.id)
    qt_application.processEvents()
    assert not _todo_button(page, "开始处理").isEnabled()
    assert _todo_button(page, "停止处理").isEnabled()

    page.tree.setCurrentItem(_tree_item_by_id(page.tree, done_task.id))
    qt_application.processEvents()
    assert not _todo_button(page, "完成").isEnabled()
    assert _todo_button(page, "恢复").isEnabled()
    assert not _todo_button(page, "开始处理").isEnabled()


# -- Phase 2A application startup ---------------------------------------------------


def test_existing_store_restores_focus_before_first_resolution_without_tree_load(
        tmp_path, qt_application, monkeypatch):
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock
    from retirement_pet.models import ActionId

    _isolate_startup_registry(monkeypatch)

    path = tmp_path / "tasks.db"
    repo = TaskRepository(path)
    timestamp = datetime.now().isoformat(timespec="seconds")
    rows = [
        (f"task-{index}", None, f"private-{index}", "short", "open",
         index, None, timestamp, timestamp, None)
        for index in range(10_000)
    ]
    repo.db.executemany(
        "INSERT INTO tasks (id,parent_id,title,horizon,status,sort_key,"
        "due_date,created_at,updated_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows)
    repo.db.execute(
        "INSERT INTO focus (id,task_id,started_at) VALUES (1,?,?)",
        ("task-9999", timestamp))
    repo.db.commit()
    repo.close()

    def forbidden_load_all(_repository):
        raise AssertionError("app startup loaded the complete task tree")

    monkeypatch.setattr(TaskRepository, "load_all", forbidden_load_all)
    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-focus-restore-{tmp_path.name}",
    )
    try:
        assert pet._todo_service is not None
        assert pet._todo_service._loaded is False
        assert pet.contexts.is_active(ContextId.WORKING)
        assert pet.controller.current_action() is ActionId.WORK
    finally:
        pet.shutdown()


def test_schema_too_new_keeps_pet_alive_and_todo_slot_safe(
        tmp_path, qt_application, monkeypatch):
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    _isolate_startup_registry(monkeypatch)

    path = tmp_path / "tasks.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta VALUES ('schema_version','99')")
    db.commit()
    db.close()
    before = _store_snapshot(path)
    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-schema-new-{tmp_path.name}",
    )
    try:
        assert pet.window is not None
        assert pet.todo_availability.available is False
        assert type(pet.todo_error).__name__ == "SchemaTooNew"
        assert pet.todo.all_tasks() == []
        pet._open_control_panel("todo")  # Qt slot boundary must not leak
        assert _store_snapshot(path) == before
    finally:
        pet.shutdown()


def test_unavailable_store_keeps_pet_alive_and_todo_slot_safe(
        tmp_path, qt_application, monkeypatch):
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock
    from retirement_pet.todo.errors import TaskStoreUnavailable

    _isolate_startup_registry(monkeypatch)

    (tmp_path / "tasks.db").write_bytes(b"placeholder")

    def fail_repository(*_args, **_kwargs):
        raise TaskStoreUnavailable("busy")

    monkeypatch.setattr("retirement_pet.todo.repository.TaskRepository.__init__",
                        fail_repository)
    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-store-down-{tmp_path.name}",
    )
    try:
        assert pet.window is not None
        assert pet.todo_availability.available is False
        assert type(pet.todo_error).__name__ == "TaskStoreUnavailable"
        assert pet.todo.all_tasks() == []
        pet._open_control_panel("todo")
    finally:
        pet.shutdown()


def test_missing_todo_family_stays_lazy_when_bridge_is_queried(
        tmp_path, qt_application, monkeypatch):
    """A bridge lookup is storage-free; first ``todo`` use owns creation."""
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock
    from retirement_pet.todo.domain import FocusProjection

    _isolate_startup_registry(monkeypatch)
    attempts = []
    real_init = TaskRepository.__init__

    def counted_init(repository, *args, **kwargs):
        attempts.append(1)
        real_init(repository, *args, **kwargs)

    monkeypatch.setattr(TaskRepository, "__init__", counted_init)
    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-todo-lazy-{tmp_path.name}",
    )
    try:
        family = [tmp_path / "tasks.db", tmp_path / "tasks.db-wal",
                  tmp_path / "tasks.db-shm", tmp_path / "tasks.db-journal"]
        assert attempts == []
        assert pet.todo_bridge.projection == FocusProjection(False, None)
        assert pet.todo_bridge is pet.todo_bridge
        assert attempts == []
        assert not any(os.path.lexists(member) for member in family)

        service = pet.todo
        assert pet.todo is service
        assert attempts == [1]
        assert (tmp_path / "tasks.db").is_file()
    finally:
        pet.shutdown()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_orphan_sidecar_eagerly_fails_once_without_touching_family(
        tmp_path, qt_application, monkeypatch, suffix):
    """Every lexically present SQLite family member triggers eager preflight."""
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    _isolate_startup_registry(monkeypatch)
    path = tmp_path / "tasks.db"
    sidecar = Path(f"{path}{suffix}")
    sidecar.write_bytes(f"ORPHAN{suffix}".encode("ascii"))
    before = _store_snapshot(path)
    attempts = []
    real_init = TaskRepository.__init__

    def counted_init(repository, *args, **kwargs):
        attempts.append(1)
        real_init(repository, *args, **kwargs)

    monkeypatch.setattr(TaskRepository, "__init__", counted_init)
    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-orphan-{suffix[1:]}-{tmp_path.name}",
    )
    try:
        assert attempts == [1]
        assert pet.todo_availability.available is False
        facade = pet.todo
        assert pet.todo is facade
        assert pet.todo_bridge is pet.todo_bridge
        pet._open_control_panel("todo")
        pet._panel.accept()
        assert attempts == [1]
        assert facade.all_tasks() == []
        assert _store_snapshot(path) == before
    finally:
        pet.shutdown()


def test_unknown_todo_initialization_failure_is_private_memoized_and_closes_repo(
        tmp_path, qt_application, monkeypatch):
    """An error after opening the repo degrades once without leaking canaries."""
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock
    from retirement_pet.todo.domain import FocusProjection

    _isolate_startup_registry(monkeypatch)
    path = tmp_path / "tasks.db"
    seed = TaskRepository(path)
    seed.close()

    canary = "TODO-INIT-TITLE-ID-DUE-CANARY-2099-12-31"
    injected = RuntimeError(canary)
    attempts = []
    closes = []
    real_close = TaskRepository.close

    def fail_service(*_args, **_kwargs):
        attempts.append(1)
        raise injected

    def counted_close(repository):
        closes.append(1)
        return real_close(repository)

    monkeypatch.setattr("retirement_pet.todo.TodoService", fail_service)
    monkeypatch.setattr(TaskRepository, "close", counted_close)
    captured_records = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            captured_records.append(record)

    capture = CaptureHandler()
    app_logger = logging.getLogger("retirement_pet.app")
    app_logger.addHandler(capture)
    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-todo-canary-{tmp_path.name}",
    )
    try:
        assert pet.window is not None
        assert pet.tray is not None
        assert attempts == [1]
        assert closes == [1]
        assert pet.todo_error is injected
        availability = pet.todo_availability
        assert availability.available is False
        assert canary not in repr(availability)

        facade = pet.todo
        assert pet.todo is facade
        assert pet.todo_availability is availability
        assert pet.todo_bridge.projection == FocusProjection(False, None)
        pet._open_control_panel("todo")
        pet._panel.accept()
        assert attempts == [1]
        assert closes == [1]

        assert facade.degraded is True
        assert facade.degraded_backup is None
        assert facade.all_tasks() == []
        assert facade.children_of(None) == []
        assert facade.by_horizon(
            Horizon.SHORT, include_ancestors=True) == []
        assert facade.get("private-id") is None
        assert facade.load_focus_only() is None
        assert facade.load_focus_projection() == FocusProjection(False, None)
        assert facade.focus_task_id() is None
        assert facade.focus_task() is None
        facade.close()
        facade.close()

        mutation_calls = (
            lambda: facade.add_task("private title", Horizon.SHORT),
            lambda: facade.rename("private-id", "private title"),
            lambda: facade.set_horizon("private-id", Horizon.LONG),
            lambda: facade.set_due_date("private-id", date(2099, 12, 31)),
            lambda: facade.complete("private-id"),
            lambda: facade.restore("private-id"),
            lambda: facade.add_subtask("private-id", "private child"),
            lambda: facade.move_within_siblings("private-id", 1),
            lambda: facade.delete_subtree("private-id"),
            lambda: facade.start_focus("private-id"),
            lambda: facade.stop_focus(),
        )
        stable_errors = []
        for mutate in mutation_calls:
            with pytest.raises(TaskStoreUnavailable) as raised:
                mutate()
            stable_errors.append((raised.value.reason, raised.value.stage,
                                  str(raised.value)))
        assert len(set(stable_errors)) == 1
        assert canary not in repr(stable_errors)

        rendered = []
        for record in captured_records:
            rendered.extend((record.getMessage(), repr(record.args),
                             repr(record.exc_info), repr(record.exc_text),
                             repr(record.stack_info)))
            if record.exc_info is not None:
                rendered.extend(traceback.format_exception(*record.exc_info))
        assert canary not in "\n".join(rendered)
        todo_records = [record for record in captured_records
                        if record.name == "retirement_pet.app"
                        and record.levelno >= logging.ERROR]
        assert todo_records
        assert all(record.exc_info is None for record in todo_records)
    finally:
        app_logger.removeHandler(capture)
        pet.shutdown()


def test_focus_start_causes_one_controller_change(
        tmp_path, qt_application, monkeypatch):
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock
    from retirement_pet.models import ActionId

    _isolate_startup_registry(monkeypatch)
    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-focus-single-resolve-{tmp_path.name}",
    )
    changes = []
    unsubscribe = pet.controller.on_change(changes.append)
    try:
        task = pet.todo.add_task("单次解析", Horizon.SHORT)
        changes.clear()
        assert pet.todo.start_focus(task.id) is True
        assert len(changes) == 1
        assert pet.controller.current_action() is ActionId.WORK
        assert pet.contexts.owner_of(ContextId.WORKING) == "todo"
    finally:
        unsubscribe()
        pet.shutdown()


def test_shutdown_runs_each_resource_once_even_when_multiple_steps_fail(
        tmp_path, qt_application, monkeypatch):
    """Shutdown is an idempotent best-effort sequence, including the guard."""
    from types import SimpleNamespace

    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    _isolate_startup_registry(monkeypatch)
    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-shutdown-isolated-{tmp_path.name}",
    )

    # Release/detach real resources first; the probes below solely exercise
    # the shutdown orchestration and cannot leak a QLocalServer or Qt timer.
    pet._health_observation_timer.stop()
    pet._startup_timer.stop()
    pet._detach_qt_lifecycle()
    pet.visual_clock.set_visible(False)
    pet.service_clock.stop()
    pet.audio.stop()
    pet.tray.hide()
    pet.switcher.shutdown()
    pet._selection_store.close()
    pet.library.close()
    pet._guard.release()

    calls = []

    def step(name, *, fail=False):
        def invoke(*_args, **_kwargs):
            calls.append(name)
            if fail:
                raise RuntimeError(f"shutdown-{name}-canary")
        return invoke

    pet._health_observation_timer = SimpleNamespace(
        stop=step("health_timer", fail=True))
    pet._startup_timer = SimpleNamespace(stop=step("startup_timer"))
    pet._detach_qt_lifecycle = step("detach", fail=True)
    pet.visual_clock = SimpleNamespace(
        set_visible=step("visual_clock"))
    pet.service_clock = SimpleNamespace(stop=step("service_clock"))
    pet._save_window_position = step("window_position")
    pet.state = SimpleNamespace(save=step("state", fail=True))
    pet.settings = SimpleNamespace(save=step("settings"))
    pet.audio = SimpleNamespace(stop=step("audio", fail=True))
    pet.tray = SimpleNamespace(hide=step("tray"))
    pet._todo_service = SimpleNamespace(close=step("todo", fail=True))
    pet.switcher = SimpleNamespace(shutdown=step("switcher"))
    pet._selection_store = SimpleNamespace(close=step("selection"))
    pet.library = SimpleNamespace(close=step("library"))
    pet._guard = SimpleNamespace(release=step("guard", fail=True))

    pet.shutdown()
    expected = [
        "health_timer", "startup_timer", "detach", "visual_clock",
        "service_clock", "window_position", "state", "settings", "audio",
        "tray", "todo", "switcher", "selection", "library", "guard",
    ]
    assert calls == expected
    pet.shutdown()
    assert calls == expected
