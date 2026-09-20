"""V12-04: Todo schema v2 migration, verified backups and restore.

Fixtures are synthetic (Chinese/Emoji/multi-level trees/dates/focus) and
never contain personal data.  Every failure scenario asserts the store's
content is preserved - fail-closed means untouched, not rebuilt.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from retirement_pet.todo import (
    Horizon,
    Level,
    Status,
    TaskRepository,
    TodoError,
    TodoService,
)
from retirement_pet.todo import backup as backup_module
from retirement_pet.todo import migrations
from retirement_pet.todo.backup import BackupError
from retirement_pet.todo.errors import SchemaTooNew, TaskStoreUnavailable

V1_COLUMNS = ("id, parent_id, title, horizon, status, sort_key,"
              " due_date, created_at, updated_at, completed_at")


def _family_bytes(path: Path) -> dict[str, bytes]:
    result = {}
    for member in (path, Path(f"{path}-wal"), Path(f"{path}-shm"),
                   Path(f"{path}-journal")):
        if member.exists():
            result[member.name] = member.read_bytes()
    return result


def _declared(path: Path) -> int:
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        return migrations.declared_version(db)
    finally:
        db.close()


def _build_v1_store(path: Path, rows: list[tuple],
                    focus: tuple[str, str] | None = None) -> dict:
    """Create an exact v1 store; return the raw pre-migration table state."""
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        migrations.initialize_v1(db)
        for row in rows:
            db.execute(
                "INSERT INTO tasks (id, parent_id, title, horizon, status,"
                " sort_key, due_date, created_at, updated_at, completed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)", row)
        if focus is not None:
            db.execute(
                "INSERT INTO focus (id, task_id, started_at) VALUES (1,?,?)",
                focus)
        db.commit()
        shared = {
            row[0]: tuple(row) for row in db.execute(
                f"SELECT {V1_COLUMNS} FROM tasks")}
        focus_row = db.execute(
            "SELECT task_id, started_at FROM focus WHERE id=1").fetchone()
        return {"tasks": shared,
                "focus": tuple(focus_row) if focus_row else None}
    finally:
        db.close()


# A synthetic tree: Chinese, emoji, four levels, mixed completion, dates.
ROOT_A = ("a000000000000001", None, "写季度总结报告", "short", "open", 0,
          "2026-09-20", "2026-08-01T10:00:00", "2026-08-01T10:00:00", None)
CHILD_A1 = ("a000000000000002", "a000000000000001", "准备数据图表 ✨",
            "medium", "open", 0, None,
            "2026-08-02T11:30:00.123456", "2026-08-02T11:30:00.123456", None)
GRAND_A = ("a000000000000003", "a000000000000002", "清洗原始数据 🧪",
           "medium", "done", 0, None,
           "2026-08-03T09:00:00", "2026-08-04T09:00:00",
           "2026-08-04T09:00:00")
GREAT_A = ("a000000000000004", "a000000000000003", "校对数字 🀄",
           "long", "open", 1, None,
           "2026-08-05T09:00:00", "2026-08-05T09:00:00", None)
CHILD_A2 = ("a000000000000005", "a000000000000001", "安排'评审'会议",
            "short", "done", 1, "2026-08-15",
            "2026-08-06T09:00:00", "2026-08-10T09:00:00",
            "2026-08-10T09:00:00")
ROOT_B = ("b000000000000001", None, "个人学习计划 📚", "long", "open", 0,
          None, "2026-08-07T09:00:00", "2026-08-07T09:00:00", None)

FIXTURE_ROWS = [ROOT_A, CHILD_A1, GRAND_A, GREAT_A, CHILD_A2, ROOT_B]
FIXTURE_FOCUS = ("b000000000000001", "2026-09-10T09:00:00")


def _assert_v2_defaults(db: sqlite3.Connection) -> None:
    bad = db.execute(
        "SELECT COUNT(*) FROM tasks WHERE importance IS NOT NULL"
        " OR urgency IS NOT NULL OR archived != 0 OR archived_at IS NOT NULL"
        " OR note != ''").fetchone()[0]
    assert bad == 0


# -- migration parity ----------------------------------------------------------


def test_v1_fixture_migrates_with_field_parity(tmp_path):
    path = tmp_path / "tasks.db"
    before = _build_v1_store(path, FIXTURE_ROWS, FIXTURE_FOCUS)

    repo = TaskRepository(path)
    try:
        after = {
            row[0]: tuple(row) for row in repo.db.execute(
                f"SELECT {V1_COLUMNS} FROM tasks")}
        focus = repo.db.execute(
            "SELECT task_id, started_at FROM focus WHERE id=1").fetchone()
        meta = repo.db.execute(
            "SELECT key, value FROM meta ORDER BY key").fetchall()
        _assert_v2_defaults(repo.db)
        assert repo.db.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert repo.db.execute(
            "PRAGMA foreign_key_check").fetchone() is None
        tasks = repo.load_all()
    finally:
        repo.close()

    assert after == before["tasks"]
    assert tuple(focus) == before["focus"]
    assert [row[0] for row in meta] == ["established_at", "schema_version"]
    assert meta[1][1] == "2"
    # domain-level round trip keeps dates, times and tree links intact
    assert tasks[ROOT_A[0]].due_date == date(2026, 9, 20)
    assert tasks[CHILD_A1[0]].created_at == datetime(2026, 8, 2, 11, 30, 0,
                                                     123456)
    assert tasks[GRAND_A[0]].status is Status.DONE
    assert tasks[GREAT_A[0]].parent_id == GRAND_A[0]
    assert tasks[CHILD_A1[0]].importance is None  # 未分类, nothing guessed
    assert tasks[ROOT_B[0]].note == ""
    assert tasks[ROOT_B[0]].archived is False


def test_migrated_content_survives_a_restart_without_remigration(tmp_path):
    path = tmp_path / "tasks.db"
    _build_v1_store(path, FIXTURE_ROWS, FIXTURE_FOCUS)
    TaskRepository(path).close()  # first open migrates

    repo = TaskRepository(path)
    try:
        tasks = repo.load_all()
        assert tasks[CHILD_A1[0]].title == "准备数据图表 ✨"
        assert tasks[CHILD_A2[0]].title == "安排'评审'会议"
        established = repo.db.execute(
            "SELECT value FROM meta WHERE key='established_at'").fetchone()[0]
    finally:
        repo.close()

    repo = TaskRepository(path)
    try:
        assert repo.db.execute(
            "SELECT value FROM meta WHERE key='established_at'"
        ).fetchone()[0] == established
        # a repeated open must not produce another migration backup
        assert len(list((tmp_path / "backups").iterdir())) == 1
    finally:
        repo.close()


# -- fresh creation and exact v2 shape ------------------------------------------


def test_fresh_store_is_exact_v2(tmp_path):
    repo = TaskRepository(tmp_path / "tasks.db")
    try:
        objects = {(row[0], row[1]) for row in repo.db.execute(
            "SELECT type, name FROM sqlite_master WHERE"
            " name NOT LIKE 'sqlite_%'")}
        assert objects == {
            ("table", "meta"), ("table", "tasks"), ("table", "focus"),
            ("index", "idx_tasks_parent"),
            ("index", "idx_tasks_status_horizon"),
            ("index", "idx_tasks_due"),
            ("index", "idx_tasks_archived"),
        }
        names = [row[1] for row in repo.db.execute(
            'PRAGMA table_xinfo("tasks")')]
        assert names == [
            "id", "parent_id", "title", "horizon", "status", "sort_key",
            "due_date", "created_at", "updated_at", "completed_at",
            "importance", "urgency", "archived", "archived_at", "note"]
        assert not (tmp_path / "backups").exists()
        assert TaskRepository.inspect_snapshot(tmp_path / "tasks.db") == (2, 0)
    finally:
        repo.close()


def test_v1_backup_snapshot_verifies_as_v1(tmp_path):
    """Backups of a v1 store remain first-class v1 files (rollback path)."""
    path = tmp_path / "tasks.db"
    _build_v1_store(path, FIXTURE_ROWS[:1])
    repo = TaskRepository(path)  # migrates; the pre-migration copy is v1
    try:
        pre = backup_module.list_backups(path)[0]
        assert pre.kind == "pre-migration" and pre.valid
        assert pre.schema_version == 1
        assert TaskRepository.inspect_snapshot(
            pre.directory / "tasks.db") == (1, 1)
        # a manual backup taken after the migration snapshots the v2 store
        record = backup_module.create_backup(
            path, "manual", source_connection=repo.db)
    finally:
        repo.close()
    assert record.schema_version == 2
    assert TaskRepository.inspect_snapshot(record.directory / "tasks.db") \
        == (2, 1)


# -- backup failure blocks migration ---------------------------------------------


def test_backup_failure_blocks_migration_and_preserves_v1(
        tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    _build_v1_store(path, FIXTURE_ROWS, FIXTURE_FOCUS)
    before = _family_bytes(path)

    def failing_backup(*_args, **_kwargs):
        raise BackupError("snapshot_failed")

    monkeypatch.setattr(backup_module, "create_backup", failing_backup)
    with pytest.raises(TaskStoreUnavailable) as caught:
        TaskRepository(path)
    assert caught.value.reason == "backup_failed"
    assert _family_bytes(path) == before
    assert _declared(path) == 1
    monkeypatch.undo()

    repo = TaskRepository(path)
    try:
        assert len(repo.load_all()) == len(FIXTURE_ROWS)
    finally:
        repo.close()


def test_backup_failure_leaves_no_partial_backup_dir(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    _build_v1_store(path, FIXTURE_ROWS[:1])

    def failing_snapshot(_source, _target):
        raise sqlite3.OperationalError("injected disk failure")

    monkeypatch.setattr(backup_module, "_snapshot_into", failing_snapshot)
    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)
    monkeypatch.undo()
    backups = tmp_path / "backups"
    if backups.exists():
        assert list(backups.iterdir()) == []


# -- migration interruption and commit-boundary failure ---------------------------


def test_mid_migration_crash_rolls_back_to_v1(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    _build_v1_store(path, FIXTURE_ROWS, FIXTURE_FOCUS)
    before = _family_bytes(path)

    def half_way(db, **_kwargs):
        db.execute(
            "CREATE TABLE tasks_v1_shadow AS SELECT"
            f" {V1_COLUMNS} FROM tasks")
        db.execute("DROP TABLE tasks")
        raise RuntimeError("injected mid-migration crash")

    monkeypatch.setattr(migrations, "migrate_v1_to_v2", half_way)
    with pytest.raises(TaskStoreUnavailable) as caught:
        TaskRepository(path)
    assert caught.value.reason == "migration_failed"
    monkeypatch.undo()
    assert _family_bytes(path) == before
    assert _declared(path) == 1

    repo = TaskRepository(path)
    try:
        assert len(repo.load_all()) == len(FIXTURE_ROWS)
        assert _declared(path) == 2
    finally:
        repo.close()


def test_commit_boundary_failure_preserves_v1(tmp_path):
    path = tmp_path / "tasks.db"
    _build_v1_store(path, FIXTURE_ROWS, FIXTURE_FOCUS)
    before = _family_bytes(path)

    class _FailingCommit(sqlite3.Connection):
        fail_next = True

        def commit(self):
            if _FailingCommit.fail_next:
                _FailingCommit.fail_next = False
                raise sqlite3.OperationalError("injected commit failure")
            super().commit()

    def factory(pathobj, *args, **kwargs):
        return sqlite3.connect(pathobj, *args, factory=_FailingCommit,
                               **kwargs)

    with pytest.raises(TaskStoreUnavailable) as caught:
        TaskRepository(path, connection_factory=factory)
    assert caught.value.stage == "migrate"
    assert _family_bytes(path) == before
    assert _declared(path) == 1

    repo = TaskRepository(path)
    try:
        assert len(repo.load_all()) == len(FIXTURE_ROWS)
    finally:
        repo.close()


# -- hostile stores stay untouched ------------------------------------------------


def test_version_three_store_is_refused_and_untouched(tmp_path):
    path = tmp_path / "tasks.db"
    TaskRepository(path).close()
    db = sqlite3.connect(path)
    db.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    db.commit()
    db.close()
    before = _family_bytes(path)

    with pytest.raises(SchemaTooNew):
        TaskRepository(path)
    assert _family_bytes(path) == before


def test_corrupted_v2_store_is_never_rebuilt_for_a_new_user(tmp_path):
    path = tmp_path / "tasks.db"
    TaskRepository(path).close()
    path.write_bytes(b"CORRUPTED-V2-CANARY" * 40)
    before = _family_bytes(path)

    with pytest.raises(TaskStoreUnavailable):
        TaskRepository(path)
    assert _family_bytes(path) == before
    assert not (tmp_path / "backups").exists()


def test_wal_backed_v1_store_migrates_through_sidecars(tmp_path):
    """A v1 store with live WAL sidecars migrates and preserves its data."""
    path = tmp_path / "tasks.db"
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("BEGIN IMMEDIATE")
    migrations.initialize_v1(writer)
    for row in FIXTURE_ROWS:
        writer.execute(
            "INSERT INTO tasks (id, parent_id, title, horizon, status,"
            " sort_key, due_date, created_at, updated_at, completed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)", row)
    writer.execute(
        "INSERT INTO focus (id, task_id, started_at) VALUES (1,?,?)",
        FIXTURE_FOCUS)
    writer.commit()
    try:
        assert Path(f"{path}-wal").stat().st_size > 0
        repo = TaskRepository(path)
        try:
            tasks = repo.load_all()
            assert len(tasks) == len(FIXTURE_ROWS)
            assert tasks[CHILD_A1[0]].title == "准备数据图表 ✨"
            assert repo.get_focus()[0] == ROOT_B[0]
        finally:
            repo.close()
    finally:
        writer.close()

    repo = TaskRepository(path)
    try:
        assert len(repo.load_all()) == len(FIXTURE_ROWS)
        assert _declared(path) == 2
    finally:
        repo.close()


# -- retention policy ---------------------------------------------------------------


def test_retention_is_bounded_and_keeps_newest_pre_migration(tmp_path):
    path = tmp_path / "tasks.db"
    _build_v1_store(path, FIXTURE_ROWS[:1])
    repo = TaskRepository(path)
    try:
        pre_migration = backup_module.list_backups(path)[0]
        for _ in range(backup_module.RETENTION_LIMIT + 2):
            backup_module.create_backup(
                path, "manual", source_connection=repo.db)
    finally:
        repo.close()

    summaries = backup_module.list_backups(path)
    assert len(summaries) == backup_module.RETENTION_LIMIT
    # the newest usable pre-migration copy is always preserved
    assert pre_migration.directory in [s.directory for s in summaries]
    assert all(summary.valid for summary in summaries)


def test_retention_never_touches_foreign_entries(tmp_path):
    path = tmp_path / "tasks.db"
    _build_v1_store(path, FIXTURE_ROWS[:1])
    repo = TaskRepository(path)
    try:
        for _ in range(backup_module.RETENTION_LIMIT + 1):
            backup_module.create_backup(
                path, "manual", source_connection=repo.db)
    finally:
        repo.close()
    backups = tmp_path / "backups"
    keeper = backups / "todo-backup-KEEPME"
    keeper.mkdir()
    (keeper / "tasks.db").write_bytes(b"USER LANDS HERE")

    backup_module.prune_backups(path)
    assert keeper.exists()


# -- explicit backup and restore ------------------------------------------------


def _write_backup_layout(tmp_path: Path, name: str, db_path: Path,
                         live_name: str = "tasks.db") -> Path:
    """Package an existing store file as a well-formed manual backup."""
    directory = tmp_path / "backups" / name
    directory.mkdir(parents=True)
    (directory / live_name).write_bytes(db_path.read_bytes())
    manifest = {
        "format": 1,
        "app": "retirement-pet",
        "kind": "manual",
        "created_at": "2026-09-11T00:00:00+00:00",
        "schema_version": _declared(db_path),
        "database": live_name,
        "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
        "size": db_path.stat().st_size,
        "task_count": 0,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    return directory


def test_service_manual_backup_and_restore_roundtrip(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    task = service.add_task("迁移前任务 🌱", Horizon.SHORT)
    record = service.create_manual_backup()
    assert record.kind == "manual"
    assert record.schema_version == 2
    assert record.task_count == 1

    service.set_note(task.id, "第一段备注。\n第二段 🎈")
    service.set_importance(task.id, Level.HIGH)
    service.set_urgency(task.id, Level.LOW)
    extra = service.add_task("备份之后新增", Horizon.LONG)
    service.close()

    service = TodoService(TaskRepository(path))
    try:
        result = service.restore_from_backup(record.directory)
        assert result.restored_version == 2
        assert result.task_count == 1
        tasks = service.all_tasks()
        assert [t.id for t in tasks] == [task.id]
        assert tasks[0].note == ""  # backup predates the note
        assert tasks[0].importance is None
        assert service.get(extra.id) is None
        # the pre-restore copy of the replaced store exists and is valid
        kinds = [s.kind for s in service.list_backups()]
        assert kinds.count("pre-restore") == 1
        assert service.list_backups()[0].valid
        assert service.load_focus_projection().focusing is False
    finally:
        service.close()


def test_restore_failure_preserves_current_store(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    task = service.add_task("保留我", Horizon.MEDIUM)
    fake = tmp_path / "not-a-backup"
    fake.mkdir()
    (fake / "manifest.json").write_text("{\"format\": 1}")

    with pytest.raises(BackupError):
        service.restore_from_backup(fake)

    # still usable, cache rebuilt from the untouched store
    assert service.get(task.id).title == "保留我"
    assert service.load_focus_projection().focusing is False
    service.close()
    assert TaskRepository.inspect_snapshot(path) == (2, 1)


def test_restoring_v1_backup_reupgrades_in_this_build(tmp_path):
    """新版本恢复旧备份（restore-old-into-new）：a v1 backup restored by
    THIS build re-upgrades in place with its own pre-migration backup.

    Review §3.2 naming: this is NOT the downgrade-rollback drill.  It does
    not prove "the OLD program can open the restored data" - that real
    drill (offline restore of a v1 backup + launching the frozen 1.1.x
    build) is documented in the node report and is not executed here.
    """
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    service.add_task("v2 时代任务", Horizon.SHORT)
    service.close()

    v1_path = tmp_path / "old-v1.db"
    _build_v1_store(v1_path, FIXTURE_ROWS, FIXTURE_FOCUS)
    layout = _write_backup_layout(tmp_path, "old", v1_path)

    service = TodoService(TaskRepository(path))
    try:
        result = service.restore_from_backup(layout)
        assert result.restored_version == 1  # the candidate was a v1 store
        tasks = {t.id: t for t in service.all_tasks()}
        assert set(tasks) == {row[0] for row in FIXTURE_ROWS}
        assert tasks[ROOT_B[0]].title == "个人学习计划 📚"
        assert tasks[ROOT_B[0]].importance is None
        assert tasks[ROOT_B[0]].note == ""
        kinds = [s.kind for s in service.list_backups()]
        assert "pre-restore" in kinds
        assert "pre-migration" in kinds  # created by the re-upgrade
        assert TaskRepository.inspect_snapshot(path) == (2, len(FIXTURE_ROWS))
    finally:
        service.close()


# -- domain and service behaviour on the new fields -----------------------------


def test_level_and_note_validation():
    task = retirement_pet_task()
    task.set_classification(importance=Level.HIGH, urgency=Level.LOW)
    assert (task.importance, task.urgency) == (Level.HIGH, Level.LOW)
    task.set_classification(importance=None)
    assert task.importance is None and task.urgency is Level.LOW
    with pytest.raises(TodoError):
        task.set_classification(importance="high")  # type: ignore[arg-type]

    prefix = "段落一\n段落二 🎉"
    task.set_note(prefix + "字" * (10_000 - len(prefix)))
    assert len(task.note) == 10_000
    with pytest.raises(TodoError):
        task.set_note("字" * 10_001)
    with pytest.raises(TodoError):
        task.set_note(None)  # type: ignore[arg-type]


def retirement_pet_task():
    from retirement_pet.todo.domain import Task

    return Task.create("带备注任务 ✒️", Horizon.SHORT)


def test_archive_is_reversible_and_never_touches_status(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    task = service.add_task("要归档的任务", Horizon.MEDIUM)
    service.complete(task.id)
    done_at = service.get(task.id).completed_at

    service.archive_task(task.id)
    archived = service.get(task.id)
    assert archived.archived is True
    assert archived.archived_at is not None
    assert archived.status is Status.DONE
    assert archived.completed_at == done_at

    service.restore_archived(task.id)
    restored = service.get(task.id)
    assert restored.archived is False
    assert restored.archived_at is None
    assert restored.status is Status.DONE
    assert restored.completed_at == done_at
    service.close()


def test_archiving_the_focused_task_clears_focus(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    focused = service.add_task("聚焦任务", Horizon.SHORT)
    other = service.add_task("旁观的另一项", Horizon.LONG)
    service.start_focus(focused.id)
    assert service.load_focus_projection().focusing is True

    service.archive_task(focused.id)
    assert service.load_focus_projection().focusing is False

    # restoring never grabs focus back; another task can be focused
    service.start_focus(other.id)
    service.restore_archived(focused.id)
    assert service.focus_task_id() == other.id
    service.close()


def test_v2_fields_persist_across_reopen(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    task = service.add_task("持久化任务 💾", Horizon.MEDIUM)
    service.set_importance(task.id, Level.LOW)
    service.set_urgency(task.id, Level.HIGH)
    service.set_note(task.id, "重启后仍在的备注 ✅")
    service.close()

    service = TodoService(TaskRepository(path))
    try:
        loaded = service.get(task.id)
        assert loaded.importance is Level.LOW
        assert loaded.urgency is Level.HIGH
        # the main tree does not preload long notes (V12-05); the note
        # lives in the store and is read on demand
        assert loaded.note == ""
        assert service.note_for(task.id) == "重启后仍在的备注 ✅"
    finally:
        service.close()


# -- V12-05 service layer: subtree archive, restore semantics, lazy notes ------


def _subtree_fixture(service):
    """root -> (A -> G(done), B) plus an untouched outside task."""
    root = service.add_task("归档根任务", Horizon.SHORT)
    child_a = service.add_subtask(root.id, "子任务 A")
    child_b = service.add_subtask(root.id, "子任务 B")
    grandchild = service.add_subtask(child_a.id, "孙任务 G")
    service.complete(grandchild.id)
    outside = service.add_task("范围外的另一项", Horizon.LONG)
    return root, child_a, child_b, grandchild, outside


def test_archive_subtree_is_atomic_reports_count_and_keeps_status(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    root, child_a, child_b, grandchild, outside = _subtree_fixture(service)
    try:
        count = service.archive_subtree(root.id)
        assert count == 4                     # root, A, B, G
        for task_id in (root.id, child_a.id, child_b.id, grandchild.id):
            archived = service.get(task_id)
            assert archived.archived is True
            assert archived.archived_at is not None
        # completion states never change (frozen rule)
        assert service.get(grandchild.id).status is Status.DONE
        assert service.get(outside.id).archived is False

        # re-archiving the mixed/fully archived tree is a defined no-op
        assert service.archive_subtree(root.id) == 0
    finally:
        service.close()


def test_archive_subtree_failure_leaves_store_untouched(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    root, _a, _b, _g, _o = _subtree_fixture(service)
    repo = service._repo
    original_write = repo._write_task
    calls = {"n": 0}

    def failing_write(task):
        calls["n"] += 1
        if calls["n"] == 2:                   # blow up mid-batch
            raise sqlite3.OperationalError("injected disk failure")
        original_write(task)

    repo._write_task = failing_write
    try:
        with pytest.raises(sqlite3.OperationalError):
            service.archive_subtree(root.id)
    finally:
        repo._write_task = original_write
    try:
        for task_id in (root.id, _a.id, _b.id, _g.id):
            assert service.get(task_id).archived is False
    finally:
        service.close()


def test_archive_subtree_clears_only_focus_inside_the_subtree(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    root, child_a, _b, _g, outside = _subtree_fixture(service)
    try:
        service.start_focus(child_a.id)
        service.archive_subtree(root.id)
        assert service.load_focus_projection().focusing is False

        service.start_focus(outside.id)
        service.archive_subtree(outside.id)   # archiving the focus itself
        assert service.load_focus_projection().focusing is False
    finally:
        service.close()


def test_restore_archived_recovers_ancestor_path_and_own_subtree(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    root, child_a, child_b, grandchild, _outside = _subtree_fixture(service)
    try:
        service.archive_subtree(root.id)

        # restoring one child recovers the necessary ancestor path (root)
        # AND its own archived subtree (grandchild), original states kept;
        # the untouched sibling stays archived
        count = service.restore_archived(child_a.id)
        assert count == 3                     # A, root, G
        assert service.get(root.id).archived is False
        assert service.get(child_a.id).archived is False
        assert service.get(grandchild.id).archived is False
        assert service.get(grandchild.id).status is Status.DONE
        assert service.get(child_b.id).archived is True

        # restoring the remaining sibling keeps the tree consistent
        assert service.restore_archived(child_b.id) == 1
        assert service.get(root.id).archived is False
        assert service.get(child_b.id).archived is False

        # restoring a non-archived task is a defined no-op
        assert service.restore_archived(root.id) == 0
    finally:
        service.close()


def test_notes_are_lazy_and_never_clobbered_by_other_edits(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    task = service.add_task("带长备注的任务", Horizon.SHORT)
    long_note = "第一段\n第二段 🎈\n" + "字" * 900
    service.set_note(task.id, long_note)
    service.close()

    service = TodoService(TaskRepository(path))
    try:
        # the reloaded tree does not carry the note; reading it on demand
        # returns the persisted content
        assert service.get(task.id).note == ""
        assert service.note_for(task.id) == long_note

        # unrelated edits (rename, quadrant) never touch the stored note
        service.rename(task.id, "改名后的任务")
        service.set_importance(task.id, Level.HIGH)
        assert service.note_for(task.id) == long_note

        # rewriting the note persists and survives another reopen
        service.set_note(task.id, "替换后的备注 ✍️")
    finally:
        service.close()
    service = TodoService(TaskRepository(path))
    try:
        assert service.note_for(task.id) == "替换后的备注 ✍️"
        assert service.get(task.id).title == "改名后的任务"
        assert service.get(task.id).importance is Level.HIGH
    finally:
        service.close()


def test_set_note_rejects_over_limit_without_silent_truncation(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    task = service.add_task("限额任务", Horizon.SHORT)
    try:
        with pytest.raises(TodoError):
            service.set_note(task.id, "字" * 10_001)
        # the rejected write changed nothing
        assert service.note_for(task.id) == ""
    finally:
        service.close()


# -- CR-U06: writes never trust the unloaded note cache -------------------------


def test_set_note_clear_to_empty_survives_reopen_without_prior_read(tmp_path):
    """CR-U06: a fresh service must not treat the unloaded empty cache as
    a known empty note - clearing a non-empty note must really persist."""
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    task = service.add_task("清空备注任务", Horizon.SHORT)
    service.set_note(task.id, "旧的非空备注")
    service.close()

    service = TodoService(TaskRepository(path))
    try:
        # deliberately NO note_for first: this exact order used to skip
        # the write because the lazy cache looked empty already
        service.set_note(task.id, "")
        assert service.note_for(task.id) == ""
    finally:
        service.close()
    service = TodoService(TaskRepository(path))
    try:
        assert service.note_for(task.id) == ""
    finally:
        service.close()


def test_set_note_covers_empty_to_full_same_content_and_reload(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    task = service.add_task("各方向备注写入", Horizon.SHORT)
    try:
        # empty -> non-empty on a fresh store (cache also empty here)
        service.set_note(task.id, "新备注")
        # same content twice stays persisted and raises nothing
        service.set_note(task.id, "新备注")
        # non-empty -> different non-empty
        service.set_note(task.id, "改后的备注")
        assert service.note_for(task.id) == "改后的备注"
    finally:
        service.close()
    service = TodoService(TaskRepository(path))
    try:
        assert service.note_for(task.id) == "改后的备注"
    finally:
        service.close()


def test_note_reads_and_writes_keep_note_bodies_out_of_the_task_cache(tmp_path):
    """CR-U06 follow-up: visited note bodies must not accumulate in the
    long-term _tasks cache behind the panel."""
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    try:
        first = service.add_task("缓存任务一", Horizon.SHORT)
        second = service.add_task("缓存任务二", Horizon.SHORT)
        service.set_note(first.id, "很长的备注正文" * 50)
        service.set_note(second.id, "另一段备注正文" * 50)
        # reading on demand must not stuff bodies back into the cache
        assert service.note_for(first.id) == "很长的备注正文" * 50
        assert service.note_for(second.id) == "另一段备注正文" * 50
        assert service._tasks[first.id].note == ""
        assert service._tasks[second.id].note == ""
        # the dedicated write also leaves the cache note-free
        service.set_note(first.id, "重写后的正文")
        assert service._tasks[first.id].note == ""
        assert service.note_for(first.id) == "重写后的正文"
    finally:
        service.close()


def test_restore_rebuild_bumps_store_generation_for_ui_invalidation(tmp_path):
    path = tmp_path / "tasks.db"
    service = TodoService(TaskRepository(path))
    try:
        before = service.store_generation
        record = service.create_manual_backup()
        result = service.restore_from_backup(record.directory)
        assert result.store_available is True
        assert service.store_generation == before + 1
    finally:
        service.close()
