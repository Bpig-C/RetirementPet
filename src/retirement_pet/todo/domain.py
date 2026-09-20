"""TodoModule domain: task entities and frozen tree constraints (M6.5).

FROZEN RULES (product decision):
- no self-parent, no cycles, max depth 6;
- a child inherits its parent's horizon BY DEFAULT but the user may change
  it per task; horizons never migrate automatically by date;
- completion is explicit-scope (V13-03): the default ``scope='self'``
  completes only the target and never cascades; an explicit
  ``scope='subtree'`` completes the root AND every unfinished descendant in
  ONE transaction, keeping hierarchy and order; restore is scoped the same
  way and never guesses which descendants were cascade-completed;
- deleting/archiving a parent removes the whole subtree in ONE transaction;
- at most ONE active focus task at any time;
- the task store is USER GLOBAL data: it belongs to the user, never to a
  character pack, and switching characters never touches it.

v2 additions (1.2.0):
- importance and urgency are INDEPENDENT user-set axes; nothing derives
  them from title, horizon or due date, and migration leaves them unset;
- archiving is explicit and reversible, stored per task (archived flag +
  archived_at); completion states are never changed by archiving;
- ``note`` is plain user text (newlines/emoji allowed, bounded length) and
  never crosses into the pet runtime (context bridge stays title-free).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Iterable


class Horizon(str, Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class Status(str, Enum):
    OPEN = "open"
    DONE = "done"


class Level(str, Enum):
    """Independent quadrant axis value (v2).

    ``importance`` and ``urgency`` are two independent axes and are
    deliberately NOT derived from horizon, due date or title.  ``None``
    (the v1 migration default) means unclassified; the quadrant view keeps
    an explicit unclassified entry.
    """

    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class SubtreeSummary:
    """Scope facts for delete/complete confirmation dialogs (V13-03).

    Counts describe the whole subtree rooted at the task (inclusive);
    ``open``/``archived``/``done`` count tasks in that state.  ``ids`` is
    the exact id set the summary was computed over, so a UI can drop
    per-task state (kept note drafts) after the confirmed operation.
    Contains no titles or notes.
    """

    total: int
    open: int
    done: int
    archived: int
    focus_included: bool
    ids: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.ids, frozenset):
            raise TypeError("ids must be a frozenset")
        if self.total != len(self.ids):
            raise ValueError("total must match ids size")
        if self.open + self.done != self.total:
            raise ValueError("open + done must equal total")


@dataclass(frozen=True, slots=True)
class FocusProjection:
    """Privacy-safe focus state allowed to cross into the pet runtime.

    The compatibility subscript exposes only the two frozen fields used by
    the existing Todo panel.  It deliberately returns the horizon's string
    value so UI code never needs access to a task or repository row.
    """

    focusing: bool
    horizon: Horizon | None

    def __post_init__(self) -> None:
        if type(self.focusing) is not bool:  # bool only; integers are rejected
            raise TypeError("focusing must be a bool")
        if self.horizon is not None and not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be a Horizon or None")
        if self.focusing != (self.horizon is not None):
            raise ValueError("focus projection fields are inconsistent")

    def __getitem__(self, key: str):
        if key == "focusing":
            return self.focusing
        if key == "horizon":
            return self.horizon.value if self.horizon is not None else None
        raise KeyError(key)


MAX_DEPTH = 6
_ALL_HORIZONS = {h.value for h in Horizon}
_ALL_LEVELS = {level.value for level in Level}
#: plain-text note bound; UI must surface the limit, never silently truncate
MAX_NOTE_CHARS = 10_000


class TodoError(ValueError):
    """Domain rule violation (stable message, no user data in str)."""


def new_task_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Task:
    id: str
    parent_id: str | None
    title: str
    horizon: Horizon
    status: Status = Status.OPEN
    sort_key: int = 0
    due_date: date | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    # -- v2 fields (defaults are exactly what the v1 migration writes) -----
    importance: Level | None = None
    urgency: Level | None = None
    archived: bool = False
    archived_at: datetime | None = None
    note: str = ""

    # -- factory -----------------------------------------------------------------

    @classmethod
    def create(cls, title: str, horizon: Horizon,
               parent_id: str | None = None, sort_key: int = 0,
               due_date: date | None = None) -> "Task":
        title = _validate_title(title)
        return cls(id=new_task_id(), parent_id=parent_id, title=title,
                   horizon=horizon, sort_key=sort_key, due_date=due_date)

    # -- mutations (all bump updated_at) --------------------------------------------

    def rename(self, title: str) -> None:
        self.title = _validate_title(title)
        self.touch()

    def set_horizon(self, horizon: Horizon) -> None:
        self.horizon = Horizon(horizon)
        self.touch()

    def set_due_date(self, due_date: date | None) -> None:
        self.due_date = due_date
        self.touch()

    def set_classification(self, *,
                           importance: Level | None = ...,
                           urgency: Level | None = ...) -> None:
        """Set the independent quadrant axes (explicit user action only).

        Each axis is set only when a value is passed; ``None`` means
        unclassified.  Untouched axes keep their current value.
        """
        if importance is not ...:
            self.importance = _validate_level(importance)
        if urgency is not ...:
            self.urgency = _validate_level(urgency)
        self.touch()

    def set_note(self, note: str) -> None:
        if not isinstance(note, str):
            raise TodoError("note must be a string")
        if len(note) > MAX_NOTE_CHARS:
            raise TodoError(f"note longer than {MAX_NOTE_CHARS} chars")
        self.note = note
        self.touch()

    def archive(self) -> None:
        """Mark explicitly archived; status and completion never change."""
        if self.archived:
            return
        self.archived = True
        self.archived_at = datetime.now()
        self.touch()

    def unarchive(self) -> None:
        if not self.archived:
            return
        self.archived = False
        self.archived_at = None
        self.touch()

    def complete(self) -> None:
        if self.status is Status.DONE:
            return
        self.status = Status.DONE
        self.completed_at = datetime.now()
        self.touch()

    def restore(self) -> None:
        if self.status is Status.OPEN:
            return
        self.status = Status.OPEN
        self.completed_at = None
        self.touch()

    def touch(self) -> None:
        self.updated_at = datetime.now()


def _validate_title(title: str) -> str:
    if not isinstance(title, str):
        raise TodoError("title must be a string")
    stripped = title.strip()
    if not stripped:
        raise TodoError("title must not be empty")
    if len(stripped) > 200:
        raise TodoError("title too long")
    return stripped


def _validate_level(value: Level | None) -> Level | None:
    if value is None:
        return None
    if not isinstance(value, Level):
        raise TodoError("quadrant level must be a Level or None")
    return value


# -- tree constraints -----------------------------------------------------------------


def check_parenting(tasks_by_id: dict[str, "Task"], task_id: str,
                    parent_id: str | None) -> None:
    """Validate a (potentially new) parent link against the frozen rules.

    ``tasks_by_id`` is the current store WITHOUT the change applied; the
    function proves the post-change tree is still a forest of depth <= 6.
    """
    if parent_id is None:
        return
    if parent_id == task_id:
        raise TodoError("self-parenting is not allowed")
    if parent_id not in tasks_by_id:
        raise TodoError("parent task does not exist")

    # cycle check: walk up from the candidate parent; if we reach the task
    # itself, the link would close a loop
    seen: set[str] = set()
    cursor = parent_id
    while cursor is not None:
        if cursor == task_id or cursor in seen:
            raise TodoError("task parenting would form a cycle")
        seen.add(cursor)
        cursor = tasks_by_id[cursor].parent_id

    # depth check: depth(child) = depth(parent) + 1 <= MAX_DEPTH
    depth = 0
    cursor = parent_id
    while cursor is not None:
        depth += 1
        if depth >= MAX_DEPTH:
            raise TodoError(f"max task depth is {MAX_DEPTH}")
        cursor = tasks_by_id[cursor].parent_id
    if depth + 1 > MAX_DEPTH:
        raise TodoError(f"max task depth is {MAX_DEPTH}")


def subtree_ids(tasks_by_id: dict[str, "Task"], root_id: str) -> set[str]:
    """All ids in the subtree rooted at ``root_id`` (inclusive)."""
    children: dict[str | None, list[str]] = {}
    for task_id, task in tasks_by_id.items():
        children.setdefault(task.parent_id, []).append(task_id)
    collected: set[str] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current in collected:
            continue
        collected.add(current)
        stack.extend(children.get(current, ()))
    return collected


def inherited_horizon(tasks_by_id: dict[str, "Task"],
                      parent_id: str | None,
                      default: Horizon) -> Horizon:
    """A new child defaults to its parent's horizon (user may override)."""
    if parent_id is None:
        return default
    parent = tasks_by_id.get(parent_id)
    return parent.horizon if parent else default


def validate_horizon(value: str) -> Horizon:
    if value not in _ALL_HORIZONS:
        raise TodoError(f"unknown horizon: {value}")
    return Horizon(value)
