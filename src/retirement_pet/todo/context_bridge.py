"""Privacy boundary between Todo focus state and the pet runtime.

Only :class:`FocusProjection` may cross this module.  The bridge never owns a
TodoService, Task, task id, title, due date, or repository connection.
"""

from __future__ import annotations

from typing import Callable

from retirement_pet.todo.domain import FocusProjection


class TodoContextBridge:
    """Cache and apply the safe focus projection to ``ContextStore``."""

    def __init__(self, context_store, *, owner: str = "todo",
                 on_contexts_changed: Callable[[], None] | None = None):
        self._store = context_store
        self._owner = owner
        self._on_contexts_changed = on_contexts_changed
        self._projection = FocusProjection(False, None)

    def sync_focus(self, projection: FocusProjection) -> None:
        """Apply a typed projection; reject every richer object."""
        if type(projection) is not FocusProjection:
            raise TypeError("focus projection required")
        self._projection = projection
        if projection.focusing:
            self._apply_working()
        else:
            self._clear_working()

    def _apply_working(self) -> None:
        from retirement_pet.runtime_state import ContextId

        changed = self._store.set(ContextId.WORKING, self._owner)
        if changed and self._on_contexts_changed:
            self._on_contexts_changed()

    def _clear_working(self) -> None:
        from retirement_pet.runtime_state import ContextId

        changed = self._store.clear(ContextId.WORKING, owner=self._owner)
        if changed and self._on_contexts_changed:
            self._on_contexts_changed()

    @property
    def projection(self) -> FocusProjection:
        """Return the cached safe DTO without consulting Todo storage."""
        return self._projection


__all__ = ["TodoContextBridge"]
