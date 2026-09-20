"""V13-03: explicit-scope completion/restore, subtree summaries, delete preview.

隔离约定：全部使用 pytest 临时目录与合成任务，不触碰日用数据库。
"""

from __future__ import annotations

import pytest

from retirement_pet.todo import (
    Horizon,
    Status,
    SubtreeSummary,
    TaskRepository,
    TodoError,
    TodoService,
)


@pytest.fixture()
def service(tmp_path):
    repository = TaskRepository(tmp_path / "tasks.db")
    events: list[str] = []
    svc = TodoService(repository, on_event=events.append)
    yield svc, events
    svc.close()


def _tree(service):
    """Mixed-state tree:

    root
      done_child
        open_grandchild
      open_child
    other (independent root)
    """
    root = service.add_task("root", Horizon.MEDIUM)
    done_child = service.add_subtask(root.id, "done-child")
    open_grandchild = service.add_subtask(done_child.id, "open-grandchild")
    open_child = service.add_subtask(root.id, "open-child")
    service.complete(done_child.id)
    other = service.add_task("other", Horizon.SHORT)
    return root, done_child, open_grandchild, open_child, other


# -- scope validation --------------------------------------------------------------


def test_invalid_scope_rejected_before_any_change(service):
    svc, events = service
    root = svc.add_task("root", Horizon.SHORT)
    for bad in ("", "children", "Subtree", "all", None, 1):
        with pytest.raises(TodoError):
            svc.complete(root.id, scope=bad)
        with pytest.raises(TodoError):
            svc.restore(root.id, scope=bad)
    assert svc.get(root.id).status is Status.OPEN
    assert events == ["changed"]  # only add_task emitted


# -- complete(scope=self) keeps the frozen v1 behaviour -----------------------------


def test_complete_self_scope_still_never_cascades(service):
    svc, _ = service
    root, done_child, open_grandchild, open_child, _other = _tree(svc)
    assert svc.complete(root.id, scope="self") == 1
    assert svc.get(root.id).status is Status.DONE
    assert svc.get(done_child.id).status is Status.DONE
    assert svc.get(open_grandchild.id).status is Status.OPEN
    assert svc.get(open_child.id).status is Status.OPEN


def test_complete_self_on_done_task_is_zero(service):
    svc, _ = service
    root = svc.add_task("root", Horizon.SHORT)
    assert svc.complete(root.id) == 1
    assert svc.complete(root.id, scope="self") == 0


# -- complete(scope=subtree) --------------------------------------------------------


def test_complete_subtree_finishes_all_open_in_one_call(service):
    svc, _ = service
    root, done_child, open_grandchild, open_child, other = _tree(svc)
    assert svc.complete(root.id, scope="subtree") == 3  # root + grandchild + child
    for task_id in (root.id, done_child.id, open_grandchild.id,
                    open_child.id):
        assert svc.get(task_id).status is Status.DONE
    # the independent tree is untouched
    assert svc.get(other.id).status is Status.OPEN


def test_complete_subtree_keeps_hierarchy_and_order(service):
    svc, _ = service
    root, done_child, open_grandchild, open_child, _other = _tree(svc)
    svc.complete(root.id, scope="subtree")
    assert svc.get(open_grandchild.id).parent_id == done_child.id
    assert svc.get(done_child.id).parent_id == root.id
    assert [t.id for t in svc.children_of(root.id)] == [
        done_child.id, open_child.id]
    assert svc.get(open_child.id).sort_key == 1
    assert svc.get(open_grandchild.id).completed_at is not None


def test_complete_subtree_is_idempotent(service):
    svc, _ = service
    root, *_ = _tree(svc)
    assert svc.complete(root.id, scope="subtree") == 3
    assert svc.complete(root.id, scope="subtree") == 0


def test_complete_subtree_includes_archived_open_descendants(service):
    svc, _ = service
    root = svc.add_task("root", Horizon.SHORT)
    archived_child = svc.add_subtask(root.id, "archived-child")
    svc.archive_subtree(archived_child.id)
    assert svc.complete(root.id, scope="subtree") == 2
    assert svc.get(archived_child.id).status is Status.DONE
    assert svc.get(archived_child.id).archived  # archive flag untouched


def test_complete_subtree_clears_focus_inside_but_not_outside(service):
    svc, events = service
    root, _dc, open_grandchild, _oc, other = _tree(svc)
    # focus OUTSIDE the completed subtree survives the transaction
    svc.start_focus(other.id)
    assert svc.complete(root.id, scope="subtree") == 3
    assert svc.focus_task_id() == other.id

    # focus INSIDE the completed subtree clears in the same transaction
    svc.restore(root.id, scope="subtree")
    svc.start_focus(open_grandchild.id)
    assert svc.complete(root.id, scope="subtree") == 4  # whole tree reopened
    assert svc.focus_task_id() is None
    assert "focus_stopped" in events


def test_complete_subtree_missing_task_rejected(service):
    svc, _ = service
    with pytest.raises(TodoError):
        svc.complete("no-such-id", scope="subtree")


def test_complete_subtree_write_failure_keeps_tree_and_cache(service):
    svc, events = service
    root, done_child, open_grandchild, open_child, _other = _tree(svc)
    events.clear()
    original = svc._repo.apply_batch

    def failing(*_args, **_kwargs):
        raise RuntimeError("disk full")

    svc._repo.apply_batch = failing
    with pytest.raises(RuntimeError):
        svc.complete(root.id, scope="subtree")
    svc._repo.apply_batch = original
    # cache still shows the pre-failure state for every task
    assert svc.get(root.id).status is Status.OPEN
    assert svc.get(open_grandchild.id).status is Status.OPEN
    assert svc.get(open_child.id).status is Status.OPEN
    assert events == []  # no change event for a failed transaction


# -- restore(scope=...) -------------------------------------------------------------


def test_restore_self_default_reopens_only_the_target(service):
    svc, _ = service
    root, done_child, open_grandchild, open_child, _other = _tree(svc)
    svc.complete(root.id, scope="subtree")
    assert svc.restore(root.id) == 1
    assert svc.get(root.id).status is Status.OPEN
    # descendants keep their completed state: restore never guesses
    assert svc.get(done_child.id).status is Status.DONE
    assert svc.get(open_grandchild.id).status is Status.DONE
    assert svc.get(open_child.id).status is Status.DONE


def test_restore_subtree_reopens_all_done_descendants(service):
    svc, _ = service
    root, done_child, open_grandchild, open_child, other = _tree(svc)
    svc.complete(root.id, scope="subtree")
    assert svc.restore(root.id, scope="subtree") == 4
    for task_id in (root.id, done_child.id, open_grandchild.id,
                    open_child.id):
        assert svc.get(task_id).status is Status.OPEN
    assert svc.get(other.id).status is Status.OPEN  # never touched anyway


def test_restore_subtree_keeps_archive_flags(service):
    svc, _ = service
    root = svc.add_task("root", Horizon.SHORT)
    child = svc.add_subtask(root.id, "child")
    svc.complete(root.id, scope="subtree")
    svc.archive_subtree(child.id)
    svc.restore(root.id, scope="subtree")
    assert svc.get(child.id).archived  # restore never un-archives


def test_restore_on_open_task_is_zero(service):
    svc, _ = service
    root = svc.add_task("root", Horizon.SHORT)
    assert svc.restore(root.id) == 0
    assert svc.restore(root.id, scope="subtree") == 0


# -- subtree summaries (delete/complete previews) -----------------------------------


def test_subtree_summary_counts_mixed_states_and_focus(service):
    svc, _ = service
    root, done_child, open_grandchild, open_child, other = _tree(svc)
    summary = svc.subtree_summary(root.id)
    assert isinstance(summary, SubtreeSummary)
    assert summary.total == 4
    assert summary.open == 3  # root + grandchild + child
    assert summary.done == 1  # done_child
    assert summary.archived == 0
    assert not summary.focus_included
    assert summary.ids == frozenset({root.id, done_child.id,
                                     open_grandchild.id, open_child.id})
    assert other.id not in summary.ids

    svc.start_focus(open_grandchild.id)
    assert svc.subtree_summary(root.id).focus_included
    assert not svc.subtree_summary(other.id).focus_included


def test_subtree_summary_reports_archived_descendants(service):
    svc, _ = service
    root = svc.add_task("root", Horizon.SHORT)
    child = svc.add_subtask(root.id, "child")
    svc.archive_subtree(child.id)
    summary = svc.subtree_summary(root.id)
    assert summary.archived == 1
    assert summary.total == 2
    svc.start_focus(root.id)  # open root inside its own subtree
    assert svc.subtree_summary(root.id).focus_included


def test_subtree_summary_missing_task_rejected(service):
    svc, _ = service
    with pytest.raises(TodoError):
        svc.subtree_summary("no-such-id")


def test_subtree_summary_contains_no_titles_or_notes(service):
    svc, _ = service
    root = svc.add_task("隐私标题", Horizon.SHORT)
    child = svc.add_subtask(root.id, "子任务隐私")
    svc.set_note(child.id, "秘密备注内容")
    summary = svc.subtree_summary(root.id)
    import dataclasses
    payload = dataclasses.asdict(summary)
    assert "隐私标题" not in repr(payload)
    assert "秘密备注内容" not in repr(payload)


# -- persistence across restart (UI/CLI/DB consistency) -----------------------------


def test_subtree_semantics_survive_repository_reopen(tmp_path):
    svc = TodoService(TaskRepository(tmp_path / "tasks.db"))
    root = svc.add_task("root", Horizon.MEDIUM)
    child = svc.add_subtask(root.id, "child")
    grandchild = svc.add_subtask(child.id, "grandchild")
    svc.complete(root.id, scope="subtree")
    svc.restore(child.id, scope="subtree")
    svc.close()

    reopened = TodoService(TaskRepository(tmp_path / "tasks.db"))
    try:
        assert reopened.get(root.id).status is Status.DONE
        assert reopened.get(child.id).status is Status.OPEN
        assert reopened.get(grandchild.id).status is Status.OPEN
        assert reopened.subtree_summary(root.id).total == 3
    finally:
        reopened.close()


def test_v2_store_schema_unchanged_by_subtree_operations(tmp_path):
    """No schema v3: subtree semantics live entirely in service logic."""
    import sqlite3

    svc = TodoService(TaskRepository(tmp_path / "tasks.db"))
    root = svc.add_task("root", Horizon.SHORT)
    child = svc.add_subtask(root.id, "child")
    svc.complete(root.id, scope="subtree")
    svc.close()

    db = sqlite3.connect(tmp_path / "tasks.db")
    try:
        version = db.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        columns = [row[1] for row in
                   db.execute('PRAGMA table_xinfo("tasks")')]
        focus_rows = db.execute("SELECT COUNT(*) FROM focus").fetchone()[0]
    finally:
        db.close()
    assert version == "2"
    assert "note" in columns  # v2 shape intact, no new columns
    assert focus_rows == 0    # subtree completion cleared the focus row
    del child
