"""Lightweight, event-driven Todo panel over :class:`TodoService`."""

from __future__ import annotations

import logging
from datetime import date

from PySide6.QtCore import QDate, QEvent, QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from retirement_pet.todo import Horizon, Status, TodoError
from retirement_pet.todo.errors import TaskStoreUnavailable

logger = logging.getLogger(__name__)

_FILTERS = [
    ("全部", None),
    ("短期", Horizon.SHORT),
    ("中期", Horizon.MEDIUM),
    ("长期", Horizon.LONG),
]
_HORIZON_LABELS = {
    Horizon.SHORT: "短期",
    Horizon.MEDIUM: "中期",
    Horizon.LONG: "长期",
}
_UNAVAILABLE_TEXT = "待办暂不可用，任务文件未作改动"
_DOMAIN_ERROR_TEXT = "操作未完成，请检查任务状态"
_UNKNOWN_ERROR_TEXT = "操作失败，请稍后重试"
_CANCELLED = object()


def build_todo_page(app) -> QWidget:
    return TodoPage(app)


class TodoPage(QWidget):
    """A small task tree that is subscribed only while it is visible."""

    def __init__(self, app):
        super().__init__()
        self._app = app
        self._todo = app.todo
        self._available = bool(app.todo_availability.available)
        self._active = False
        self._disposed = False
        self._unsubscribe = None
        self._refreshing = False
        self._write_depth = 0
        self._pending_refresh = False
        self._refresh_scheduled = False
        self._first_content_refresh = True
        self._task_by_id = {}
        self._items_by_id: dict[str, QTreeWidgetItem] = {}
        self._focus_id: str | None = None
        self._todo_service_events = 0
        self.buttons: dict[str, QPushButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        if not self._available:
            note = QLabel(_UNAVAILABLE_TEXT)
            note.setWordWrap(True)
            root.addWidget(note)

        add_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("快速新增任务，回车添加")
        self.horizon_box = QComboBox()
        for horizon in Horizon:
            self.horizon_box.addItem(_HORIZON_LABELS[horizon], horizon)
        self._add_button = QPushButton("添加")
        self._add_button.setAutoDefault(False)
        self._add_button.clicked.connect(self._on_add)
        self.input.returnPressed.connect(self._on_add)
        add_row.addWidget(self.input, 1)
        add_row.addWidget(self.horizon_box)
        add_row.addWidget(self._add_button)
        root.addLayout(add_row)

        filter_row = QHBoxLayout()
        self.filter_box = QComboBox()
        for label, horizon in _FILTERS:
            self.filter_box.addItem(label, horizon)
        self.filter_box.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(QLabel("筛选"))
        filter_row.addWidget(self.filter_box)
        filter_row.addStretch(1)
        self.focus_label = QLabel("")
        filter_row.addWidget(self.focus_label)
        root.addLayout(filter_row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["任务", "周期", "状态", "截止"])
        self.tree.setColumnWidth(0, 260)
        if self._available:
            self.tree.setEditTriggers(
                QAbstractItemView.EditKeyPressed
                | QAbstractItemView.SelectedClicked)
        else:
            self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.installEventFilter(self)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(
            lambda _current, _previous: self._update_action_states())
        root.addWidget(self.tree, 1)

        action_grid = QGridLayout()
        action_grid.setContentsMargins(0, 0, 0, 0)
        action_grid.setHorizontalSpacing(5)
        action_grid.setVerticalSpacing(4)
        action_specs = (
            ("complete", "完成", self._on_complete),
            ("restore", "恢复", self._on_restore),
            ("rename", "重命名", self._on_rename),
            ("add_subtask", "加子任务", self._on_add_subtask),
            ("move_up", "上移", lambda: self._on_move(-1)),
            ("move_down", "下移", lambda: self._on_move(1)),
            ("horizon", "改周期", self._on_move_horizon),
            ("due", "设截止", self._on_set_due),
            ("focus_start", "开始处理", self._on_focus_start),
            ("focus_stop", "停止处理", self._on_focus_stop),
            ("delete", "删除（含子任务）", self._on_delete),
        )
        for index, (name, label, handler) in enumerate(action_specs):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            button.clicked.connect(handler)
            row, column = divmod(index, 6)
            action_grid.addWidget(button, row, column)
            self.buttons[name] = button
        for column in range(6):
            action_grid.setColumnStretch(column, 1)
        root.addLayout(action_grid)

        self.status = QLabel("")
        root.addWidget(self.status)
        self._update_action_states()

    # -- lifecycle and events -------------------------------------------------

    def activate(self) -> None:
        if self._disposed or self._active:
            return
        self._active = True
        self._unsubscribe = self._app.subscribe_todo_events(
            self._on_service_event)
        self._safe_refresh()
        self._update_action_states()

    def deactivate(self) -> None:
        if not self._active:
            return
        self._active = False
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            unsubscribe()
        self._update_action_states()

    def dispose(self) -> None:
        if self._disposed:
            return
        self.deactivate()
        self._disposed = True

    def _on_service_event(self, _event: str) -> None:
        if not self._active or self._disposed:
            return
        self._todo_service_events += 1
        if self._write_depth:
            self._pending_refresh = True
            return
        self._safe_refresh()

    def _safe_refresh(self) -> bool:
        try:
            self.refresh()
        except TaskStoreUnavailable:
            self.status.setText(_UNAVAILABLE_TEXT)
            return False
        except TodoError:
            self.status.setText(_DOMAIN_ERROR_TEXT)
            return False
        except Exception:  # noqa: BLE001 - never escape a Qt signal boundary
            logger.error("todo UI refresh failed")
            self.status.setText(_UNKNOWN_ERROR_TEXT)
            return False
        return True

    def _schedule_refresh(self) -> None:
        if self._refresh_scheduled:
            return
        self._refresh_scheduled = True

        def perform() -> None:
            self._refresh_scheduled = False
            if self._active and not self._disposed:
                self._safe_refresh()

        QTimer.singleShot(0, perform)

    # -- tree -----------------------------------------------------------------

    def _selected_id(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item is not None else None

    def _filter_horizon(self) -> Horizon | None:
        raw = self.filter_box.currentData()
        return None if raw is None else Horizon(raw)

    def _on_filter_changed(self, _index: int) -> None:
        if self._active:
            self._safe_refresh()
        else:
            self._update_action_states()

    def refresh(self) -> None:
        selected_id = self._selected_id()
        expanded_ids = {
            task_id for task_id, item in self._items_by_id.items()
            if item.isExpanded()
        }
        scroll_value = self.tree.verticalScrollBar().value()

        horizon = self._filter_horizon()
        source = self._todo.all_tasks() if horizon is None else \
            self._todo.by_horizon(horizon, include_ancestors=True)
        tasks = list(source)
        focus_id = self._todo.focus_task_id()
        projection = self._app.todo_bridge.projection

        self._refreshing = True
        previous_blocked = self.tree.blockSignals(True)
        restore_expansion = not self._first_content_refresh
        try:
            self.tree.clear()
            task_by_id = {task.id: task for task in tasks}
            items: dict[str, QTreeWidgetItem] = {}

            # Pass one creates every item.  Pass two links it, so input order
            # (including children before parents) cannot trigger rescans.
            for task in tasks:
                item = QTreeWidgetItem([
                    task.title,
                    _HORIZON_LABELS[task.horizon],
                    "已完成" if task.status is Status.DONE else "进行中",
                    task.due_date.isoformat() if task.due_date else "",
                ])
                item.setData(0, Qt.UserRole, task.id)
                if self._available:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                if task.id == focus_id:
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                    item.setToolTip(0, "正在处理")
                items[task.id] = item

            for task in tasks:
                item = items[task.id]
                parent = items.get(task.parent_id) if task.parent_id else None
                if parent is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)

            self._task_by_id = task_by_id
            self._items_by_id = items
            self._focus_id = focus_id

            if selected_id in items:
                self.tree.setCurrentItem(items[selected_id])
            if self._first_content_refresh:
                self.tree.expandAll()
                if tasks:
                    self._first_content_refresh = False
            else:
                for task_id, item in items.items():
                    item.setExpanded(task_id in expanded_ids)
            self.tree.doItemsLayout()
            if restore_expansion:
                # Qt may reveal/expand the current item's ancestors while it
                # lays out the new model.  Reapply the captured user state.
                for task_id, item in items.items():
                    item.setExpanded(task_id in expanded_ids)
            scrollbar = self.tree.verticalScrollBar()
            scrollbar.setValue(min(scroll_value, scrollbar.maximum()))
        finally:
            self.tree.blockSignals(previous_blocked)
            self._refreshing = False

        if projection.focusing and projection.horizon is not None:
            self.focus_label.setText(
                f"正在专注（{_HORIZON_LABELS[projection.horizon]}）")
        else:
            self.focus_label.setText("未在专注")
        self._update_action_states()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._refreshing or column != 0 or not self._ensure_writable():
            return
        task_id = item.data(0, Qt.UserRole)
        if not task_id:
            return
        self._run_write(
            lambda: self._todo.rename(task_id, item.text(0)),
            refresh_on_failure=True,
        )

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.tree and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Delete:
                self._on_delete()
                return True
            if event.key() == Qt.Key_F2:
                self._on_rename()
                return True
        return super().eventFilter(watched, event)

    # -- safe write boundary --------------------------------------------------

    def _ensure_writable(self) -> bool:
        if not self._available:
            self.status.setText(_UNAVAILABLE_TEXT)
            return False
        return self._active and not self._disposed

    def _run_write(self, operation, *, refresh_on_failure: bool = False):
        if not self._ensure_writable():
            return False, None
        result = None
        error_text = None
        self._write_depth += 1
        try:
            result = operation()
        except TaskStoreUnavailable:
            error_text = _UNAVAILABLE_TEXT
        except TodoError:
            error_text = _DOMAIN_ERROR_TEXT
        except Exception:  # noqa: BLE001 - privacy-safe Qt slot boundary
            logger.error("todo UI operation failed")
            error_text = _UNKNOWN_ERROR_TEXT
        finally:
            self._write_depth -= 1

        refresh_needed = self._write_depth == 0 and self._pending_refresh
        if refresh_needed:
            self._pending_refresh = False
            # Rebuilding a QTreeWidget synchronously from itemChanged destroys
            # the editor while Qt is still committing it.  Coalesce service
            # events at the end of this event turn instead.
            self._schedule_refresh()
        if error_text is not None and refresh_on_failure:
            self._safe_refresh()
        if error_text is not None:
            self.status.setText(error_text)
            return False, None
        return True, result

    # -- handlers -------------------------------------------------------------

    def _on_add(self) -> None:
        if not self._ensure_writable():
            return
        title = self.input.text()
        if not title.strip():
            return
        horizon = Horizon(self.horizon_box.currentData())
        success, _ = self._run_write(
            lambda: self._todo.add_task(title, horizon))
        if success:
            self.input.clear()

    def _on_complete(self) -> None:
        task_id = self._selected_id()
        if task_id:
            self._run_write(lambda: self._todo.complete(task_id))

    def _on_restore(self) -> None:
        task_id = self._selected_id()
        if task_id:
            self._run_write(lambda: self._todo.restore(task_id))

    def _on_rename(self) -> None:
        if not self._ensure_writable():
            return
        item = self.tree.currentItem()
        if item is not None:
            self.tree.editItem(item, 0)

    def _on_add_subtask(self) -> None:
        task_id = self._selected_id()
        if not task_id or not self._ensure_writable():
            return
        title = self.input.text().strip()
        if not title:
            self.status.setText("先在输入框写下子任务标题")
            return
        success, _ = self._run_write(
            lambda: self._todo.add_subtask(task_id, title))
        if success:
            self.input.clear()

    def _on_move(self, delta: int) -> None:
        if not self._ensure_writable() or self._filter_horizon() is not None:
            return
        task_id = self._selected_id()
        if task_id:
            self._run_write(
                lambda: self._todo.move_within_siblings(task_id, delta))

    def _on_move_horizon(self) -> None:
        task_id = self._selected_id()
        if not task_id:
            return

        def change_horizon():
            task = self._todo.get(task_id)
            if task is None:
                raise TodoError("task not found")
            order = [Horizon.SHORT, Horizon.MEDIUM, Horizon.LONG]
            next_horizon = order[(order.index(task.horizon) + 1) % len(order)]
            self._todo.set_horizon(task_id, next_horizon)

        self._run_write(change_horizon)

    def _ask_due_date(self, task) -> tuple[str, date | None]:
        dialog = QDialog(self)
        dialog.setWindowTitle("设置截止日期")
        layout = QVBoxLayout(dialog)
        picker = QDateEdit()
        picker.setCalendarPopup(True)
        if task.due_date:
            picker.setDate(QDate(
                task.due_date.year, task.due_date.month, task.due_date.day))
        else:
            picker.setDate(QDate.currentDate())
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        clear_button = buttons.addButton("清除", QDialogButtonBox.ResetRole)
        clear_button.setObjectName("todo_due_clear")
        outcome: list[tuple[str, date | None]] = [("cancel", None)]

        def accept_date() -> None:
            picked = picker.date()
            outcome[0] = (
                "set", date(picked.year(), picked.month(), picked.day()))
            dialog.accept()

        def clear_date() -> None:
            outcome[0] = ("clear", None)
            dialog.accept()

        buttons.accepted.connect(accept_date)
        buttons.rejected.connect(dialog.reject)
        clear_button.clicked.connect(clear_date)
        layout.addWidget(picker)
        layout.addWidget(buttons)
        dialog.exec()
        return outcome[0]

    def _on_set_due(self) -> None:
        task_id = self._selected_id()
        if not task_id:
            return

        def set_due():
            task = self._todo.get(task_id)
            if task is None:
                raise TodoError("task not found")
            action, due_date = self._ask_due_date(task)
            if action == "cancel":
                return _CANCELLED
            self._todo.set_due_date(
                task_id, due_date if action == "set" else None)
            return action

        self._run_write(set_due)

    def _on_focus_start(self) -> None:
        task_id = self._selected_id()
        if task_id:
            self._run_write(lambda: self._todo.start_focus(task_id))

    def _on_focus_stop(self) -> None:
        self._run_write(self._todo.stop_focus)

    def _on_delete(self) -> None:
        task_id = self._selected_id()
        if not task_id:
            return

        def delete_if_confirmed():
            confirm = QMessageBox.question(
                self,
                "删除任务",
                "删除该任务及全部子任务？此操作不可撤销。",
            )
            if confirm != QMessageBox.Yes:
                return _CANCELLED
            return self._todo.delete_subtree(task_id)

        success, removed = self._run_write(delete_if_confirmed)
        if success and removed is not _CANCELLED:
            self.status.setText(f"已删除 {removed} 个任务")

    # -- enabled state --------------------------------------------------------

    def _update_action_states(self) -> None:
        writable = self._available and self._active and not self._disposed
        self.input.setEnabled(writable)
        self.horizon_box.setEnabled(writable)
        self._add_button.setEnabled(writable)

        item = self.tree.currentItem()
        task_id = item.data(0, Qt.UserRole) if item is not None else None
        task = self._task_by_id.get(task_id)
        selected = writable and task is not None
        focusing = self._focus_id is not None

        for button in self.buttons.values():
            button.setEnabled(False)
        if not writable:
            return

        self.buttons["focus_stop"].setEnabled(focusing)
        if not selected:
            return
        is_done = task.status is Status.DONE
        self.buttons["complete"].setEnabled(not is_done)
        self.buttons["restore"].setEnabled(is_done)
        for name in ("rename", "add_subtask", "horizon", "due", "delete"):
            self.buttons[name].setEnabled(True)
        self.buttons["focus_start"].setEnabled(
            not is_done and task_id != self._focus_id)

        if self._filter_horizon() is None:
            parent = item.parent()
            if parent is None:
                index = self.tree.indexOfTopLevelItem(item)
                count = self.tree.topLevelItemCount()
            else:
                index = parent.indexOfChild(item)
                count = parent.childCount()
            self.buttons["move_up"].setEnabled(index > 0)
            self.buttons["move_down"].setEnabled(index + 1 < count)
