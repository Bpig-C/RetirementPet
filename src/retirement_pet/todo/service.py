"""TodoModule service: use cases, transactions, events (M6.5).

The service owns the frozen rules; the UI never writes SQL.  Changes are
announced through an event callback (event-driven UI refresh, no polling).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date
from typing import Callable

from retirement_pet.todo.domain import (
    FocusProjection,
    Horizon,
    Status,
    Task,
    TodoError,
    check_parenting,
    inherited_horizon,
    subtree_ids,
)
from retirement_pet.todo.repository import TaskRepository

logger = logging.getLogger(__name__)

#: coarse event kinds surfaced to the UI and the context bridge
EVENTS = ("changed", "focus_started", "focus_stopped")


class TodoService:
    def __init__(self, repository: TaskRepository,
                 on_event: Callable[[str], None] | None = None):
        self._repo = repository
        self._on_event = on_event
        self._tasks: dict[str, Task] = {}
        self._loaded = False
        self._focus_projection: FocusProjection | None = None

    # -- loading ------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._tasks = self._repo.load_all()
            self._loaded = True

    @property
    def degraded(self) -> bool:
        # Alpha is fail-closed: an unavailable store never produces a service,
        # so every constructed service is backed by a healthy exact-v1 store.
        return False

    @property
    def degraded_backup(self):
        # Automatic backup/recovery was removed from the Alpha contract.
        return None

    # -- queries (tree read on demand; UI-only) ----------------------------------

    def all_tasks(self) -> list[Task]:
        self._ensure_loaded()
        return sorted(self._tasks.values(),
                      key=lambda t: (t.sort_key, t.created_at))

    def children_of(self, parent_id: str | None) -> list[Task]:
        self._ensure_loaded()
        return sorted((t for t in self._tasks.values()
                       if t.parent_id == parent_id),
                      key=lambda t: (t.sort_key, t.created_at))

    def by_horizon(self, horizon: Horizon, *,
                   include_ancestors: bool = False) -> list[Task]:
        """Tasks of one horizon; ancestor context optional (UI filter)."""
        self._ensure_loaded()
        matched = [t for t in self._tasks.values() if t.horizon is horizon]
        if not include_ancestors:
            return sorted(matched, key=lambda t: (t.sort_key, t.created_at))
        include: set[str] = set()
        for task in matched:
            cursor = task
            while cursor is not None and cursor.id not in include:
                include.add(cursor.id)
                cursor = self._tasks.get(cursor.parent_id) \
                    if cursor.parent_id else None
        return sorted((self._tasks[i] for i in include),
                      key=lambda t: (t.sort_key, t.created_at))

    def get(self, task_id: str) -> Task | None:
        self._ensure_loaded()
        return self._tasks.get(task_id)

    def load_focus_only(self) -> Task | None:
        """Compatibility read of one focused Task; never loads the tree."""
        if self._loaded:
            focus_id = self.focus_task_id()
            return self._tasks.get(focus_id) if focus_id else None
        return self._repo.load_focus_task()

    def load_focus_projection(self) -> FocusProjection:
        """Return the cached privacy-safe O(1) focus projection."""
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

    def complete(self, task_id: str) -> None:
        """Completion never cascades to children (frozen rule)."""
        task = self._require(task_id)
        if task.status is Status.DONE:
            return
        candidate = replace(task)
        candidate.complete()
        cleared = self._repo.apply_batch(
            upserts=(candidate,), clear_focus_for=(task_id,))
        self._tasks[task_id] = candidate
        if cleared:
            self._focus_projection = FocusProjection(False, None)
            self._emit("focus_stopped")
        self._emit("changed")

    def restore(self, task_id: str) -> None:
        task = self._require(task_id)
        if task.status is Status.OPEN:
            return
        candidate = replace(task)
        candidate.restore()
        self._repo.upsert(candidate)
        self._tasks[task_id] = candidate
        self._emit("changed")

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
        focus = self._repo.get_focus()
        return focus[0] if focus else None

    def focus_task(self) -> Task | None:
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
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:  # noqa: BLE001 - listeners never break the store
                logger.error("todo event listener failed")

    def close(self) -> None:
        self._repo.close()
