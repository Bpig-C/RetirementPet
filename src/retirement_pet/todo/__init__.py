"""TodoModule: built-in lightweight todo list (M6.5, product decision).

An optional module integrated into the same EXE with strict boundaries:
domain (entities + frozen tree rules), repository (tasks.db + migrations),
service (use cases + transactions), context_bridge (coarse pet projection).
The store is USER GLOBAL data - never part of any PetPack or character
switch.
"""

from retirement_pet.todo.domain import (
    FocusProjection,
    Horizon,
    Status,
    Task,
    TodoError,
)
from retirement_pet.todo.repository import TaskRepository
from retirement_pet.todo.service import TodoService

__all__ = [
    "FocusProjection",
    "Horizon",
    "Status",
    "Task",
    "TodoError",
    "TaskRepository",
    "TodoService",
]
