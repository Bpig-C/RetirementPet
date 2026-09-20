"""TodoModule service: use cases, transactions, events (M6.5).

The service owns the frozen rules; the UI never writes SQL.  Changes are
announced through an event callback (event-driven UI refresh, no polling).
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Callable

from retirement_pet.todo import backup as backup_module
from retirement_pet.todo.backup import BackupRecord, BackupSummary, RestoreResult
from retirement_pet.todo.domain import (
    FocusProjection,
    Horizon,
    Level,
    Status,
    SubtreeSummary,
    Task,
    TodoError,
    check_parenting,
    inherited_horizon,
    subtree_ids,
)
from retirement_pet.todo.errors import TaskStoreUnavailable
from retirement_pet.todo.repository import TaskRepository

logger = logging.getLogger(__name__)

#: coarse event kinds surfaced to the UI and the context bridge
EVENTS = ("changed", "focus_started", "focus_stopped")

#: valid explicit completion/restore scopes (V13-03)
SCOPES = ("self", "subtree")


def _check_scope(scope: str) -> None:
    if scope not in SCOPES:
        raise TodoError("scope must be 'self' or 'subtree'")


class TodoService:
    def __init__(self, repository: TaskRepository,
                 on_event: Callable[[str], None] | None = None):
        self._repo = repository
        # The store location outlives any single repository object: it is
        # what lets a write-forbidden (degraded) module still run a
        # verified restore as its documented self-repair entry.
        self._store_path = Path(repository.path)
        self._on_event = on_event
        self._tasks: dict[str, Task] = {}
        self._loaded = False
        self._focus_projection: FocusProjection | None = None
        # Set only when the on-disk store cannot be re-acquired after a
        # failed restore: the module then fails closed (stable write-
        # forbidden state, safe empty reads) instead of serving stale data.
        self._unavailable_reason: str | None = None
        # Bumped on every store rebuild so UI state tied to the previous
        # store (loaded note editors, kept drafts) can be invalidated.
        self._store_generation = 0
        # Bumped on every committed "changed" event (V13-01): the data
        # generation agents compare against for optimistic concurrency.
        # Per-process counter: an agent holding an old generation after an
        # app restart simply sees a mismatch and re-reads.
        self._data_generation = 0

    # -- loading ------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._unavailable_reason is not None:
            raise TaskStoreUnavailable(self._unavailable_reason,
                                       stage="application")
        if not self._loaded:
            self._tasks = self._repo.load_all()
            self._loaded = True

    @property
    def degraded(self) -> bool:
        # True only in the write-forbidden state entered after the on-disk
        # store became unprovable (CR-A03); a normally opened service is
        # backed by a healthy exact-schema store.
        return self._unavailable_reason is not None

    @property
    def store_generation(self) -> int:
        """Monotonic store-build counter; changes after every restore."""
        return self._store_generation

    @property
    def data_generation(self) -> int:
        """Monotonic counter of committed data changes (V13-01)."""
        return self._data_generation

    @property
    def degraded_backup(self):
        # Automatic backup/recovery was removed from the Alpha contract.
        return None

    # -- queries (tree read on demand; UI-only) ----------------------------------

    def _tasks_or_empty(self) -> dict[str, Task]:
        """Safe read path: the unavailable state serves empty, never stale."""
        if self._unavailable_reason is not None:
            return {}
        self._ensure_loaded()
        return self._tasks

    def all_tasks(self) -> list[Task]:
        return sorted(self._tasks_or_empty().values(),
                      key=lambda t: (t.sort_key, t.created_at))

    def children_of(self, parent_id: str | None) -> list[Task]:
        tasks = self._tasks_or_empty()
        return sorted((t for t in tasks.values()
                       if t.parent_id == parent_id),
                      key=lambda t: (t.sort_key, t.created_at))

    def by_horizon(self, horizon: Horizon, *,
                   include_ancestors: bool = False) -> list[Task]:
        """Tasks of one horizon; ancestor context optional (UI filter)."""
        tasks = self._tasks_or_empty()
        matched = [t for t in tasks.values() if t.horizon is horizon]
        if not include_ancestors:
            return sorted(matched, key=lambda t: (t.sort_key, t.created_at))
        include: set[str] = set()
        for task in matched:
            cursor = task
            while cursor is not None and cursor.id not in include:
                include.add(cursor.id)
                cursor = tasks.get(cursor.parent_id) \
                    if cursor.parent_id else None
        return sorted((tasks[i] for i in include),
                      key=lambda t: (t.sort_key, t.created_at))

    def get(self, task_id: str) -> Task | None:
        return self._tasks_or_empty().get(task_id)

    def load_focus_only(self) -> Task | None:
        """Compatibility read of one focused Task; never loads the tree."""
        if self._unavailable_reason is not None:
            return None
        if self._loaded:
            focus_id = self.focus_task_id()
            return self._tasks.get(focus_id) if focus_id else None
        return self._repo.load_focus_task()

    def load_focus_projection(self) -> FocusProjection:
        """Return the cached privacy-safe O(1) focus projection."""
        if self._unavailable_reason is not None:
            return FocusProjection(False, None)
        if self._focus_projection is None:
            self._refresh_focus_projection()
        return self._focus_projection

    def _refresh_focus_projection(self) -> None:
        summary = self._repo.load_focus_summary()
        if summary is None or summary[1] is Status.DONE:
            self._focus_projection = FocusProjection(False, None)
        else:
            self._focus_projection = FocusProjection(True, summary[2])

    # -- mutations ----------------------------------------------------------------

    def add_task(self, title: str, horizon: Horizon,
                 parent_id: str | None = None,
                 due_date: date | None = None) -> Task:
        self._ensure_loaded()
        if parent_id is not None:
            check_parenting(self._tasks, "new", parent_id)
            horizon = inherited_horizon(self._tasks, parent_id, horizon)
        task = Task.create(title, horizon, parent_id=parent_id,
                           due_date=due_date,
                           sort_key=self._next_sort_key(parent_id))
        self._repo.upsert(task)
        self._tasks[task.id] = task
        self._emit("changed")
        return task

    def _next_sort_key(self, parent_id: str | None) -> int:
        siblings = [t.sort_key for t in self._tasks.values()
                    if t.parent_id == parent_id]
        return (max(siblings) + 1) if siblings else 0

    def rename(self, task_id: str, title: str) -> None:
        task = self._require(task_id)
        candidate = replace(task)
        candidate.rename(title)
        if candidate.title == task.title:
            return
        self._repo.upsert(candidate)
        self._tasks[task_id] = candidate
        self._emit("changed")

    def set_horizon(self, task_id: str, horizon: Horizon) -> None:
        task = self._require(task_id)
        candidate = replace(task)
        candidate.set_horizon(horizon)  # explicit user action only
        if candidate.horizon is task.horizon:
            return
        # Resolve the fallible focus read before the durable write.  Once the
        # transaction commits, publishing cache/projection/events is local and
        # cannot turn a successful write into an apparent failed operation.
        focused = self.focus_task_id() == task_id
        self._repo.upsert(candidate)
        self._tasks[task_id] = candidate
        if focused:
            self._focus_projection = FocusProjection(
                True, candidate.horizon)
        self._emit("changed")

    def set_due_date(self, task_id: str, due_date: date | None) -> None:
        task = self._require(task_id)
        if due_date == task.due_date:
            return
        candidate = replace(task)
        candidate.set_due_date(due_date)
        self._repo.upsert(candidate)
        self._tasks[task_id] = candidate
        self._emit("changed")

    # -- v2 fields: independent quadrant axes, note, archive ----------------------

    def set_importance(self, task_id: str,
                       importance: Level | None) -> None:
        """Set one quadrant axis explicitly; ``None`` means unclassified."""
        task = self._require(task_id)
        if task.importance is importance:
            return
        candidate = replace(task)
        candidate.set_classification(importance=importance)
        self._repo.upsert(candidate)
        self._tasks[task_id] = candidate
        self._emit("changed")

    def set_urgency(self, task_id: str, urgency: Level | None) -> None:
        """Set one quadrant axis explicitly; ``None`` means unclassified."""
        task = self._require(task_id)
        if task.urgency is urgency:
            return
        candidate = replace(task)
        candidate.set_classification(urgency=urgency)
        self._repo.upsert(candidate)
        self._tasks[task_id] = candidate
        self._emit("changed")

    def set_note(self, task_id: str, note: str) -> None:
        """Persist the plain-text note (bounded length, content preserved).

        The note has its own write path: generic upserts never carry the
        note column, so other edits cannot clobber a long note that the
        main tree never loaded (V12-05 lazy notes).  There is deliberately
        NO equality short-cut against the cached task (CR-U06): the cache
        never holds loaded notes, so a cached empty note means "not
        loaded", not "known empty" - the dedicated UPDATE always decides.
        """
        task = self._require(task_id)
        candidate = replace(task)
        candidate.set_note(note)
        self._repo.write_note(task_id, note, candidate.updated_at)
        self._emit("changed")

    def note_for(self, task_id: str) -> str:
        """Read one note on demand for the notes side page.

        The main tree does not preload long notes, and the result is
        deliberately NOT written back into the long-term task cache
        (CR-U06): ``Task.note`` in the cache stays the "unloaded" empty
        marker forever, which also keeps visited note bodies from
        accumulating in memory behind the panel.
        """
        self._require(task_id)
        return self._repo.note_of(task_id)

    def archive_subtree(self, task_id: str) -> int:
        """Archive a task's whole subtree in ONE transaction (V12-05).

        Completion states never change.  Archiving an already partially
        archived subtree archives the remaining tasks, so the whole tree
        ends consistently archived; the return value is the number of
        tasks newly archived (0 means nothing was left to archive).  If
        the focused task is inside the subtree the focus clears in the
        same transaction.
        """
        self._ensure_loaded()
        self._require(task_id)
        ids = subtree_ids(self._tasks, task_id)
        candidates = []
        for tid in sorted(ids):
            task = self._tasks[tid]
            if task.archived:
                continue
            candidate = replace(task)
            candidate.archive()
            candidates.append(candidate)
        if not candidates:
            return 0
        cleared = self._repo.apply_batch(
            upserts=candidates, clear_focus_for=ids)
        for candidate in candidates:
            self._tasks[candidate.id] = candidate
        if cleared:
            self._focus_projection = FocusProjection(False, None)
            self._emit("focus_stopped")
        self._emit("changed")
        return len(candidates)

    def archive_task(self, task_id: str) -> None:
        """Compatibility entry for the single-task storage operation.

        The user-facing rule (V12-05) is whole-subtree archiving, so this
        simply delegates to archive_subtree; the parent rule applies at
        every level, and completion states never change either way.
        """
        self.archive_subtree(task_id)

    def restore_archived(self, task_id: str) -> int:
        """Un-archive in one transaction; restoring never grabs focus.

        One consistent rule covers every entry point: the target, every
        archived ANCESTOR on its path to the root (a single item can
        always be made visible again) and every archived DESCENDANT (a
        restored root brings the whole archived tree back with its
        original completion states and relations) are unarchived together.
        The return value is the number of tasks restored (0 = nothing was
        archived).  Partially archived trees stay a defined, visible state.
        """
        task = self._require(task_id)
        if not task.archived:
            return 0
        restore_ids = {task_id}
        cursor = task.parent_id
        while cursor is not None:
            ancestor = self._tasks.get(cursor)
            if ancestor is None or not ancestor.archived:
                break
            restore_ids.add(cursor)
            cursor = ancestor.parent_id
        restore_ids |= subtree_ids(self._tasks, task_id)
        candidates = []
        for tid in sorted(restore_ids):
            archived = self._tasks[tid]
            if not archived.archived:
                continue
            candidate = replace(archived)
            candidate.unarchive()
            candidates.append(candidate)
        if not candidates:
            return 0
        self._repo.apply_batch(upserts=candidates)
        for candidate in candidates:
            self._tasks[candidate.id] = candidate
        self._emit("changed")
        return len(candidates)

    # -- backups ------------------------------------------------------------------

    def create_manual_backup(self) -> BackupRecord:
        """Explicit user-facing backup from the live, consistent connection."""
        self._ensure_loaded()
        return backup_module.create_backup(
            self._repo.path, "manual", source_connection=self._repo.db)

    def list_backups(self) -> list[BackupSummary]:
        """Backup listing reads only the backups directory.

        It deliberately does not require the store itself (CR-U04): a
        degraded, write-forbidden module must still be able to browse
        exactly the backups it can repair itself with.
        """
        return backup_module.list_backups(self._store_path)

    def restore_from_backup(self, backup_directory) -> RestoreResult:
        """Restore a verified backup and reopen the store from it.

        The current store is backed up (``pre-restore``) before anything is
        overwritten; the backup being restored is protected from retention
        during the whole operation.  After ANY outcome the previous
        in-memory state is discarded and rebuilt from a fresh open of the
        on-disk store, so a failure after the file replacement can never
        leave the service serving (and re-writing) stale tasks.

        The result distinguishes the two successful-restore outcomes
        (review CR-A05): ``store_available=True`` means the reopened store
        is fully usable; ``store_available=False`` (with
        ``unavailable_reason``) means the on-disk data WAS restored but
        the fresh open failed, so the module is in its stable write-
        forbidden state - the restore itself must not be reported as an
        ordinary success.  A degraded module stays a valid restore caller:
        the remembered store path lets a verified backup replace the
        broken store, making restore the documented self-repair entry.
        Restoring a v1 backup yields a v1 store which this build then
        upgrades through the normal pre-migration backup + migrate path.
        """
        path = self._store_path
        if self._repo is not None:
            self._repo.close()
        protected = frozenset({Path(os.path.abspath(os.fspath(backup_directory)))})
        try:
            result = backup_module.restore_backup(path, backup_directory)
        except Exception as exc:  # noqa: BLE001 - state rebuild is mandatory
            self._reacquire_after_restore(path, exc, protected)
            raise
        self._reacquire_after_restore(path, None, protected)
        if self._unavailable_reason is not None:
            # Data restored on disk, store unusable in memory: a partial
            # success the caller (V12-05 UI) must show as such.
            return replace(result, store_available=False,
                           unavailable_reason=self._unavailable_reason)
        return result

    def _reacquire_after_restore(self, path, failure,
                                 protected: frozenset[Path]) -> None:
        """Rebuild every cached view from the disk store (CR-A03).

        After a restore attempt the old repository object and task cache
        are untrusted: the on-disk database is opened fresh and the task
        cache, focus projection and UI event are rebuilt from it.  The
        restore input stays protected from the retention prune of a
        reopening-induced v1 migration.  When even that open fails, the
        reason is kept and every future read serves empty and every future
        write raises ``TaskStoreUnavailable`` until the module is
        reconstructed by a restart.
        """
        self._tasks = {}
        self._loaded = False
        self._focus_projection = None
        self._store_generation += 1
        try:
            self._repo = TaskRepository(path,
                                        protected_backups=protected)
            self._unavailable_reason = None
        except Exception as exc:  # noqa: BLE001 - fail closed, no guessing
            self._unavailable_reason = f"restore_reopen_failed:" \
                                       f"{type(exc).__name__}"
            self._repo = None
            logger.error("todo store reopen after restore failed; "
                         "module enters write-forbidden state")
        self._emit("changed")

    def complete(self, task_id: str, *, scope: str = "self") -> int:
        """Complete a task; the scope is always explicit (V13-03).

        ``scope='self'`` is the frozen v1 behaviour: only the target task
        changes and completion never cascades (default for compatibility
        callers).  ``scope='subtree'`` completes the root and every
        unfinished descendant (archived descendants included) in ONE
        transaction, keeping hierarchy and order; if the focused task is
        inside the subtree the focus clears in the same transaction.

        Returns the number of tasks newly completed (0 = nothing to do).
        """
        _check_scope(scope)
        self._ensure_loaded()
        task = self._require(task_id)
        if scope == "self" and task.status is Status.DONE:
            return 0
        if scope == "self":
            candidates = [replace(task)]
            candidates[0].complete()
            ids = frozenset((task_id,))
        else:
            ids = frozenset(subtree_ids(self._tasks, task_id))
            candidates = []
            for tid in sorted(ids):
                entry = self._tasks[tid]
                if entry.status is Status.DONE:
                    continue
                candidate = replace(entry)
                candidate.complete()
                candidates.append(candidate)
            if not candidates:
                return 0
        cleared = self._repo.apply_batch(
            upserts=candidates, clear_focus_for=ids)
        for candidate in candidates:
            self._tasks[candidate.id] = candidate
        if cleared:
            self._focus_projection = FocusProjection(False, None)
            self._emit("focus_stopped")
        self._emit("changed")
        return len(candidates)

    def restore(self, task_id: str, *, scope: str = "self") -> int:
        """Reopen a completed task; the scope is always explicit (V13-03).

        ``scope='self'`` (default) reopens only the target.  ``scope='subtree'``
        reopens the target and every DONE descendant in ONE transaction.
        Restore never guesses provenance: reopening does not by itself
        reopen anything outside the requested scope, and archived flags are
        never touched here (``restore_archived`` owns that axis).

        Returns the number of tasks reopened (0 = nothing to do).
        """
        _check_scope(scope)
        task = self._require(task_id)
        if scope == "self":
            if task.status is Status.OPEN:
                return 0
            candidate = replace(task)
            candidate.restore()
            self._repo.upsert(candidate)
            self._tasks[task_id] = candidate
            self._emit("changed")
            return 1
        self._ensure_loaded()
        ids = frozenset(subtree_ids(self._tasks, task_id))
        candidates = []
        for tid in sorted(ids):
            entry = self._tasks[tid]
            if entry.status is not Status.DONE:
                continue
            candidate = replace(entry)
            candidate.restore()
            candidates.append(candidate)
        if not candidates:
            return 0
        self._repo.apply_batch(upserts=candidates)
        for candidate in candidates:
            self._tasks[candidate.id] = candidate
        self._emit("changed")
        return len(candidates)

    def subtree_summary(self, task_id: str) -> SubtreeSummary:
        """Counting facts for delete/complete confirmation dialogs.

        Read-only; describes the subtree rooted at ``task_id`` (inclusive)
        without exposing titles or notes.  ``focus_included`` reflects the
        live focus row at the moment of the call.
        """
        self._ensure_loaded()
        self._require(task_id)
        ids = frozenset(subtree_ids(self._tasks, task_id))
        open_count = sum(1 for tid in ids
                         if self._tasks[tid].status is Status.OPEN)
        archived_count = sum(1 for tid in ids
                             if self._tasks[tid].archived)
        focus_id = self.focus_task_id()
        return SubtreeSummary(
            total=len(ids),
            open=open_count,
            done=len(ids) - open_count,
            archived=archived_count,
            focus_included=focus_id in ids,
            ids=ids,
        )

    def add_subtask(self, parent_id: str, title: str) -> Task:
        return self.add_task(title, self._require(parent_id).horizon,
                             parent_id=parent_id)

    def move_within_siblings(self, task_id: str, delta: int) -> None:
        task = self._require(task_id)
        siblings = self.children_of(task.parent_id)
        index = next(i for i, t in enumerate(siblings) if t.id == task_id)
        target = index + delta
        if not 0 <= target < len(siblings):
            return
        if target == index:
            return
        item = siblings.pop(index)  # MOVE (remove + insert), not a swap
        siblings.insert(target, item)
        candidates = []
        for sort_key, entry in enumerate(siblings):
            candidate = replace(entry)
            if candidate.sort_key != sort_key:
                candidate.sort_key = sort_key
                candidate.touch()
            candidates.append(candidate)
        self._repo.apply_batch(upserts=candidates)
        for candidate in candidates:
            self._tasks[candidate.id] = candidate
        self._emit("changed")

    def delete_subtree(self, task_id: str) -> int:
        """Delete a task and its whole subtree in ONE transaction."""
        self._ensure_loaded()
        self._require(task_id)
        ids = subtree_ids(self._tasks, task_id)
        # children BEFORE parents: the tasks table self-references, so a
        # parent-first delete would violate the FK inside the transaction
        depth = {tid: 0 for tid in ids}
        for tid in ids:
            cursor = self._tasks[tid].parent_id
            while cursor in depth:
                depth[tid] += 1
                cursor = self._tasks[cursor].parent_id
        ordered = sorted(ids, key=lambda tid: -depth[tid])
        cleared = self._repo.apply_batch(
            delete_ids=ordered, clear_focus_for=ids)
        for gone in ids:
            self._tasks.pop(gone, None)
        if cleared:
            self._focus_projection = FocusProjection(False, None)
            self._emit("focus_stopped")
        self._emit("changed")
        return len(ids)

    # -- focus (at most ONE active; drives the pet context) --------------------------

    def focus_task_id(self) -> str | None:
        # safe read: the unavailable state answers None (UI may poll this)
        if self._unavailable_reason is not None:
            return None
        focus = self._repo.get_focus()
        return focus[0] if focus else None

    def focus_task(self) -> Task | None:
        # safe read: the unavailable state answers None like every other
        # read (review CR-A05 read-contract alignment)
        if self._unavailable_reason is not None:
            return None
        self._ensure_loaded()
        focus_id = self.focus_task_id()
        return self._tasks.get(focus_id) if focus_id else None

    def start_focus(self, task_id: str) -> bool:
        task = self._require(task_id)
        if task.status is Status.DONE:
            raise TodoError("cannot focus a completed task")
        if self.focus_task_id() == task_id:
            return False
        self._repo.set_focus(task_id)
        self._focus_projection = FocusProjection(True, task.horizon)
        self._emit("focus_started")
        self._emit("changed")
        return True

    def stop_focus(self, *, reason: str = "user") -> None:
        self._ensure_loaded()
        if not self._repo.clear_focus():
            return
        self._focus_projection = FocusProjection(False, None)
        self._emit("focus_stopped")
        self._emit("changed")

    # -- helpers ----------------------------------------------------------------------

    def _require(self, task_id: str) -> Task:
        self._ensure_loaded()
        task = self._tasks.get(task_id)
        if task is None:
            raise TodoError("task not found")
        return task

    def _emit(self, event: str) -> None:
        if event == "changed":
            self._data_generation += 1
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:  # noqa: BLE001 - listeners never break the store
                logger.error("todo event listener failed")

    def close(self) -> None:
        if self._repo is not None:
            self._repo.close()
