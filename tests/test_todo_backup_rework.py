"""Regression tests for the controller-review rework (CR-A01/A02/A03/A05).

Each test reproduces one finding from docs/V1_2_CONTROLLER_REVIEW_A.md
(or its recheck, docs/V1_2_PROGRESS_RECHECK.md) against isolated
synthetic stores:

- CR-A01: a restore operation must never let retention prune delete its
  own input (nor the pre-restore copy), at full retention pressure.
- CR-A02: retention may only delete directories whose manifest proves
  this application owns them for THIS database; anything else is kept.
- CR-A03: after a restore failure that already replaced the store file,
  the service must rebuild every cached view from the disk store - the
  next edit must never write stale tasks back.  When the disk store
  cannot be re-acquired the module enters the write-forbidden state.
- CR-A05: a restore whose ONLY failure is the post-replacement reopen
  must be reported as a partial success (data restored, store
  write-forbidden), never as an ordinary success; a degraded module
  stays a valid restore caller (self-repair entry) and every read,
  including ``focus_task``, serves safe-empty.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import retirement_pet.todo.backup as backup_module
import retirement_pet.todo.service as service_module
from retirement_pet.todo.domain import Horizon
from retirement_pet.todo.errors import TaskStoreUnavailable
from retirement_pet.todo.repository import TaskRepository
from retirement_pet.todo.service import TodoService

from test_todo_v2 import FIXTURE_ROWS, _build_v1_store


def _service(path: Path) -> TodoService:
    return TodoService(TaskRepository(path))


def _manual_backup(service: TodoService) -> object:
    return service.create_manual_backup()


# -- CR-A01: restore protects its input at full retention pressure ------------


def test_restore_oldest_backup_survives_full_quota(tmp_path):
    path = tmp_path / "tasks.db"
    service = _service(path)
    try:
        records = []
        for index in range(8):  # retention exactly full
            service.add_task(f"第 {index} 个任务 🌱", Horizon.SHORT)
            records.append(_manual_backup(service))
        oldest = records[0]
        assert oldest.directory.exists()
    finally:
        service.close()

    service = _service(path)
    try:
        result = service.restore_from_backup(oldest.directory)
        assert result.task_count == 1
        # the restore input survived the pre-restore prune
        assert oldest.directory.exists()
        # live state matches the restored backup, not the stale cache
        tasks = service.all_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "第 0 个任务 🌱"
        kinds = [s.kind for s in service.list_backups()]
        assert kinds.count("pre-restore") == 1
    finally:
        service.close()
    # store on disk holds exactly the restored single task
    version, count = TaskRepository.inspect_snapshot(path)
    assert (version, count) == (2, 1)


def test_restore_failure_then_retry_keeps_input_and_succeeds(tmp_path,
                                                             monkeypatch):
    path = tmp_path / "tasks.db"
    service = _service(path)
    try:
        service.add_task("最早的任务", Horizon.SHORT)
        oldest = _manual_backup(service)
        for index in range(7):  # fill the quota with newer backups
            service.add_task(f"后续 {index}", Horizon.SHORT)
            _manual_backup(service)
    finally:
        service.close()

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst, *args, **kwargs):
        # only the restore publish step uses os.replace (backup publishing
        # renames a staging directory instead)
        calls["n"] += 1
        if Path(dst) == path:
            raise OSError("injected publish failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(backup_module.os, "replace", flaky_replace)
    service = _service(path)
    try:
        with pytest.raises(backup_module.BackupError) as excinfo:
            service.restore_from_backup(oldest.directory)
        assert excinfo.value.reason == "restore_publish_failed"
        # failed attempt left the input and pre-restore copy in place
        assert oldest.directory.exists()
    finally:
        service.close()

    monkeypatch.undo()
    service = _service(path)
    try:
        result = service.restore_from_backup(oldest.directory)
        assert result.task_count == 1
        assert oldest.directory.exists()
        assert [t.title for t in service.all_tasks()] == ["最早的任务"]
    finally:
        service.close()


def test_restore_pre_migration_input_survives(tmp_path):
    path = tmp_path / "tasks.db"
    _build_v1_store(path, FIXTURE_ROWS)
    service = _service(path)  # opening migrates and creates the pre-migration backup
    try:
        for index in range(7):
            service.add_task(f"v2 之后 {index}", Horizon.SHORT)
            _manual_backup(service)
        summaries = service.list_backups()
        assert len(summaries) == backup_module.RETENTION_LIMIT
        pre_migration = summaries[-1]
        assert pre_migration.kind == "pre-migration"
    finally:
        service.close()

    service = _service(path)
    try:
        result = service.restore_from_backup(pre_migration.directory)
        assert result.restored_version == 1  # candidate was a v1 store
        tasks = {t.id for t in service.all_tasks()}
        assert tasks == {row[0] for row in FIXTURE_ROWS}
        # the pre-migration restore input still exists after the whole
        # restore + re-upgrade sequence
        assert pre_migration.directory.exists()
        assert service.degraded is False
    finally:
        service.close()
    version, count = TaskRepository.inspect_snapshot(path)
    assert (version, count) == (2, len(FIXTURE_ROWS))


def test_same_second_backups_are_independent_and_protected(tmp_path,
                                                           monkeypatch):
    """Backups created within the same second keep distinct identities and
    a restore among them never triggers retention collateral damage."""
    path = tmp_path / "tasks.db"
    fixed = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(backup_module, "_utc_now", lambda: fixed)
    service = _service(path)
    records = []
    try:
        service.add_task("同秒之前", Horizon.SHORT)
        for _ in range(backup_module.RETENTION_LIMIT):
            records.append(_manual_backup(service))
    finally:
        service.close()
    # every entry shares one created_at second but keeps a unique directory
    names = {record.directory.name for record in records}
    assert len(names) == backup_module.RETENTION_LIMIT

    target = records[0]
    service = _service(path)
    try:
        result = service.restore_from_backup(target.directory)
        assert result.task_count == 1
        assert [t.title for t in service.all_tasks()] == ["同秒之前"]
        # the protected input and every same-second sibling survive
        for record in records:
            assert record.directory.exists()
    finally:
        service.close()


# -- CR-A02: retention only deletes proven-owned directories -------------------


def _foreign_layout(backups: Path, name: str, **overrides) -> Path:
    directory = backups / name
    directory.mkdir(parents=True)
    (directory / "tasks.db").write_bytes(b"\x00" * 64)
    (directory / "canary.txt").write_text("keep me", encoding="utf-8")
    manifest = {
        "format": 1,
        "app": "another-app",
        "kind": "manual",
        # deliberately older than every real backup so that, without the
        # ownership gate, retention pressure would select this entry
        "created_at": "2020-01-01T00:00:00+00:00",
        "schema_version": 2,
        "database": "other.db",
        "sha256": hashlib.sha256(b"\x00" * 64).hexdigest(),
        "size": 64,
        "task_count": 0,
    }
    manifest.update(overrides)
    (directory / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    return directory


def _quota_of_backups(tmp_path: Path) -> tuple[Path, Path]:
    path = tmp_path / "tasks.db"
    service = _service(path)
    try:
        service.add_task("配额内", Horizon.SHORT)
        for _ in range(backup_module.RETENTION_LIMIT):
            _manual_backup(service)
    finally:
        service.close()
    return path, tmp_path / "backups"


@pytest.mark.parametrize("overrides", [
    {},                                        # foreign app + database
    {"app": "retirement-pet"},                 # right app, foreign database
    {"database": "tasks.db"},                  # right database, foreign app
    {"kind": "user-custom"},                   # unknown kind
    {"created_at": "not-a-timestamp"},         # unparseable timestamp
    {"sha256": "zzzz"},                        # malformed digest
    {"size": "large"},                         # non-integer size
    {"task_count": -1},                        # negative task count
])
def test_prune_keeps_directories_with_unproven_ownership(
        tmp_path, overrides):
    path, backups = _quota_of_backups(tmp_path)
    foreign = _foreign_layout(backups, "todo-backup-foreign", **overrides)

    removed = backup_module.prune_backups(path)
    assert foreign.exists()
    assert (foreign / "canary.txt").read_text(encoding="utf-8") == "keep me"
    assert foreign not in removed
    # unproven ownership is also never offered as a usable backup
    listed = {s.directory: s for s in backup_module.list_backups(path)}
    assert listed[foreign].valid is False
    assert listed[foreign].kind is None


@pytest.mark.parametrize("payload", [
    b"{\"format\": 1, \"app\": \"retirement-pet\"",   # invalid JSON
    b"{\"format\": 2}",                               # unsupported format
])
def test_prune_keeps_corrupt_manifest_directories(tmp_path, payload):
    path, backups = _quota_of_backups(tmp_path)
    directory = backups / "todo-backup-corrupt"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(payload)
    (directory / "canary.txt").write_text("keep me", encoding="utf-8")

    backup_module.prune_backups(path)
    assert (directory / "canary.txt").read_text(encoding="utf-8") == "keep me"


def test_owned_but_invalid_backup_is_listed_and_prunable(tmp_path):
    """A directory this app owns whose content no longer matches its
    manifest stays retention-manageable, but is flagged invalid."""
    path, backups = _quota_of_backups(tmp_path)
    service = _service(path)
    try:
        record = _manual_backup(service)
    finally:
        service.close()
    # corrupt the backup content behind the manifest
    (record.directory / "tasks.db").write_bytes(b"CORRUPTED")
    summaries = {s.directory: s for s in backup_module.list_backups(path)}
    assert summaries[record.directory].valid is False
    # retention may still reclaim owned-but-corrupt entries under pressure
    backup_module.prune_backups(path, limit=1)
    summaries = {s.directory: s for s in backup_module.list_backups(path)}
    assert record.directory not in summaries


def test_reparse_boundary_disqualifies_backup_entry(tmp_path, monkeypatch):
    """A directory that stops looking like a plain directory (reparse
    point) leaves app-managed scope before ownership is even considered."""
    path = tmp_path / "tasks.db"
    real_lstat = os.lstat

    def fake_lstat(p, *args, **kwargs):
        info = real_lstat(p, *args, **kwargs)
        if Path(p).name.startswith(backup_module.BACKUP_PREFIX):
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_file_attributes=0x400,  # FILE_ATTRIBUTE_REPARSE_POINT
            )
        return info

    monkeypatch.setattr(backup_module.os, "lstat", fake_lstat)
    service = _service(path)
    try:
        record = _manual_backup(service)
    finally:
        service.close()
    assert backup_module.list_backups(path) == []
    backup_module.prune_backups(path)
    assert record.directory.exists()  # out of scope, never touched


# -- CR-A03: failure after the file replacement rebuilds from disk -------------


def test_post_replace_failure_rebuilds_state_from_disk(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    service = _service(path)
    try:
        kept = service.add_task("备份里的任务", Horizon.SHORT)
        record = _manual_backup(service)
        gone = service.add_task("备份之后新增", Horizon.SHORT)
    finally:
        service.close()

    real_fsync_file = backup_module._fsync_file
    live_store = path.resolve()

    def flaky_fsync_file(target):
        if Path(target).resolve() == live_store:
            # only the post-replace durability barrier of the live store
            raise OSError("injected post-replace fsync failure")
        return real_fsync_file(target)

    monkeypatch.setattr(backup_module, "_fsync_file", flaky_fsync_file)
    service = _service(path)
    try:
        with pytest.raises(
                backup_module.BackupError) as excinfo:
            service.restore_from_backup(record.directory)
        # replaced == True: the report must not claim "store unchanged"
        assert excinfo.value.reason == "restore_publish_unknown"
        # cache was rebuilt from the trusted disk state, not the stale one
        assert [t.id for t in service.all_tasks()] == [kept.id]
        assert service.get(gone.id) is None
        assert service.degraded is False
    finally:
        monkeypatch.undo()
        service.close()
    # the next edit must not resurrect the removed stale task
    service = _service(path)
    try:
        service.rename(kept.id, "恢复后改名")
        service.add_task("恢复后再新增", Horizon.SHORT)
    finally:
        service.close()
    version, count = TaskRepository.inspect_snapshot(path)
    assert (version, count) == (2, 2)
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        titles = {row[0] for row in db.execute("SELECT title FROM tasks")}
    finally:
        db.close()
    assert titles == {"恢复后改名", "恢复后再新增"}


def test_pre_replace_failure_keeps_current_store_and_rebuilds(tmp_path):
    path = tmp_path / "tasks.db"
    service = _service(path)
    try:
        task = service.add_task("当前任务", Horizon.SHORT)
    finally:
        service.close()
    record_dir = tmp_path / "backups" / "broken"
    record_dir.mkdir(parents=True)
    (record_dir / "manifest.json").write_text(
        json.dumps({"format": 1, "sha256": "0" * 64, "size": 1}),
        encoding="utf-8")

    service = _service(path)
    try:
        with pytest.raises(backup_module.BackupError):
            service.restore_from_backup(record_dir)
        # publish never happened: current store intact, cache rebuilt
        assert [t.id for t in service.all_tasks()] == [task.id]
        assert service.degraded is False
    finally:
        service.close()


def test_reopen_failure_enters_write_forbidden_state(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    service = _service(path)
    try:
        service.add_task("开场任务", Horizon.SHORT)
        record = _manual_backup(service)
        service.add_task("之后新增", Horizon.SHORT)
    finally:
        service.close()

    real_fsync_file = backup_module._fsync_file
    live_store = path.resolve()

    def flaky_fsync_file(target):
        if Path(target).resolve() == live_store:
            raise OSError("injected post-replace fsync failure")
        return real_fsync_file(target)

    monkeypatch.setattr(backup_module, "_fsync_file", flaky_fsync_file)

    def broken_repository(_path):
        raise TaskStoreUnavailable("injected reopen failure",
                                   stage="application")

    monkeypatch.setattr(service_module, "TaskRepository", broken_repository)
    service = _service(path)
    try:
        with pytest.raises(backup_module.BackupError):
            service.restore_from_backup(record.directory)
        # state unprovable: fail closed instead of serving stale data
        assert service.degraded is True
        assert service.all_tasks() == []
        assert service.get("whatever") is None
        assert service.load_focus_projection().focusing is False
        assert service.focus_task_id() is None
        with pytest.raises(TaskStoreUnavailable):
            service.add_task("禁写", Horizon.SHORT)
        with pytest.raises(TaskStoreUnavailable):
            service.create_manual_backup()
    finally:
        monkeypatch.undo()
        service.close()

    # a fresh module over the intact disk store recovers normally; the
    # on-disk store already held the restored content (post-replace)
    version, count = TaskRepository.inspect_snapshot(path)
    assert (version, count) == (2, 1)
    service = _service(path)
    try:
        tasks = service.all_tasks()
        assert [t.title for t in tasks] == ["开场任务"]
    finally:
        service.close()


# -- CR-A05: reopen failure is a partial success, not an ordinary one ----------


def test_reopen_failure_alone_returns_partial_success_result(
        tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    service = _service(path)
    try:
        service.add_task("开场任务", Horizon.SHORT)
        record = _manual_backup(service)
    finally:
        service.close()

    # ONLY the fresh open of the replaced store fails: replacement and
    # fsync are untouched, the on-disk data is the restored content.
    def broken_repository(_path, **kwargs):
        raise TaskStoreUnavailable("injected reopen failure",
                                   stage="application")

    monkeypatch.setattr(service_module, "TaskRepository", broken_repository)
    service = _service(path)
    try:
        result = service.restore_from_backup(record.directory)
        assert result.store_available is False
        assert result.unavailable_reason == \
            "restore_reopen_failed:TaskStoreUnavailable"
        assert (result.restored_version, result.task_count) == (2, 1)
        assert result.preserved_backup is not None
        # the module is write-forbidden and every read serves safe-empty
        assert service.degraded is True
        assert service.all_tasks() == []
        assert service.get("whatever") is None
        assert service.focus_task() is None
        assert service.focus_task_id() is None
        with pytest.raises(TaskStoreUnavailable):
            service.add_task("禁写", Horizon.SHORT)
    finally:
        monkeypatch.undo()
        service.close()

    # the disk store really holds the restored data
    version, count = TaskRepository.inspect_snapshot(path)
    assert (version, count) == (2, 1)


def test_degraded_service_restores_again_as_self_repair(
        tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    service = _service(path)
    try:
        service.add_task("开场任务", Horizon.SHORT)
        first = _manual_backup(service)
        service.add_task("第二项", Horizon.SHORT)
        second = _manual_backup(service)
    finally:
        service.close()

    def broken_repository(_path, **kwargs):
        raise TaskStoreUnavailable("injected reopen failure",
                                   stage="application")

    monkeypatch.setattr(service_module, "TaskRepository", broken_repository)
    service = _service(path)
    result = service.restore_from_backup(first.directory)
    assert result.store_available is False
    assert service.degraded is True
    monkeypatch.undo()

    # the degraded module may still run a verified restore over the
    # remembered store path; a succeeding reopen makes it healthy again
    repaired = service.restore_from_backup(second.directory)
    assert repaired.store_available is True
    assert repaired.unavailable_reason is None
    assert service.degraded is False
    assert [t.title for t in service.all_tasks()] == ["开场任务", "第二项"]
    service.close()


# -- fourth-round hardening: same-second backups must sort deterministically --


def test_backups_in_same_second_prune_in_creation_order(tmp_path,
                                                        monkeypatch):
    """created_at used to be stored at second resolution, so two backups
    created within one wall-clock second had ambiguous newest-first order
    (retention pruning depended on directory iteration order under load).
    With microsecond manifests the later creation always wins."""
    path = tmp_path / "tasks.db"
    ticks = {"n": 0}

    def staged_now():
        ticks["n"] += 1
        return datetime(2026, 9, 12, 8, 30, 0, ticks["n"] * 400_000,
                        tzinfo=timezone.utc)

    monkeypatch.setattr(backup_module, "_utc_now", staged_now)
    service = _service(path)
    try:
        older = _manual_backup(service)
        newer = _manual_backup(service)
    finally:
        service.close()
    assert older.created_at != newer.created_at

    backup_module.prune_backups(path, limit=1)
    names = {s.directory.name for s in backup_module.list_backups(path)}
    assert newer.directory.name in names
    assert older.directory.name not in names
