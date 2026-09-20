"""Lightweight, event-driven Todo panel over :class:`TodoService`."""

from __future__ import annotations

import logging
from datetime import date

from PySide6.QtCore import QDate, QEvent, QRegularExpression, QTimer, Qt
from PySide6.QtGui import QBrush, QColor, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from retirement_pet.todo import Horizon, Level, Status, TodoError
from retirement_pet.todo.backup import BackupError
from retirement_pet.todo.domain import MAX_NOTE_CHARS
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
# Views are pure predicates over the same task snapshot; nothing is
# deleted by hiding (V12-05).  "进行中" is the default main list.
_VIEWS = [
    ("open", "进行中"),
    ("done", "已完成"),
    ("archived", "已归档"),
    ("quadrant", "重要且紧急", Level.HIGH, Level.HIGH),
    ("quadrant", "重要不紧急", Level.HIGH, Level.LOW),
    ("quadrant", "紧急不重要", Level.LOW, Level.HIGH),
    ("quadrant", "不紧急不重要", Level.LOW, Level.LOW),
    ("uncategorized", "未分类"),
]
# Quick-toggle cycle: unclassified -> high -> low -> unclassified.
_LEVEL_CYCLE = {None: Level.HIGH, Level.HIGH: Level.LOW,
                Level.LOW: None}
_DIM_TEXT = QBrush(QColor(0xA0, 0xA0, 0xA0))
#: compact priority tag look (V13-04): text tags, color only emphasizes
_IMPORTANT_TEXT = QBrush(QColor(0xB3, 0x3A, 0x3A))
_UNAVAILABLE_TEXT = "待办暂不可用，任务文件未作改动"
_DOMAIN_ERROR_TEXT = "操作未完成，请检查任务状态"
_UNKNOWN_ERROR_TEXT = "操作失败，请稍后重试"
_NOTE_PLACEHOLDER = ("点击任务后在此记录备注（纯文本，"
                     f"最多 {MAX_NOTE_CHARS} 字）")
_NOTE_SAVE_DELAY_MS = 800
# CR-U02: until a read succeeds NOTHING is bound to the editor - an empty
# editor is a failure state, never writable stale content.
_NOTE_LOAD_FAILED_TEXT = "备注读取失败，内容未加载；可重试或重新选择任务"
# CR-U03: kept drafts live in the application-level bounded store; the
# bound is generous for daily use but never unbounded.
_MAX_KEPT_NOTE_DRAFTS = 16
_CANCELLED = object()


def build_todo_page(app) -> QWidget:
    return TodoPage(app)


class SecurePreviewDocument(QTextDocument):
    """Root-layer resource boundary (CR13-01).

    Qt loads markdown images at the DOCUMENT layer, below the browser
    widget, so overriding only the widget's ``loadResource`` leaves
    ``file:``/``data:``/reference-style loads reachable.  This document
    subclass is the actual resource entry point for the preview and
    fails closed for EVERY resource request, whatever the syntax that
    produced it.
    """

    def loadResource(self, _type, _url):  # noqa: N802 - Qt signature
        # no resource is ever fetched: images are stripped before parse
        # AND this entry point refuses everything that slips through
        return None


class _MarkdownPreview(QTextBrowser):
    """Read-only Markdown preview: no network, no file access (V13-04).

    Three independent layers, because no single one is sufficient:

    - the document is a :class:`SecurePreviewDocument`, so the document-
      level resource loader refuses every request (inline, reference-
      style, folded, HTML ``<img>``, any scheme);
    - ``render_markdown`` additionally strips every known markdown image
      form before parsing, keeping the alt text;
    - links never navigate on click; the page installs a handler that
      opens http/https links only after an explicit user confirmation.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDocument(SecurePreviewDocument())
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self._open_handler = None
        self.anchorClicked.connect(self._on_anchor)

    def set_open_handler(self, handler) -> None:
        self._open_handler = handler

    def loadResource(self, _type, _url):  # noqa: N802 - Qt signature
        # widget-level defence in depth (the document level is the root)
        return None

    def render_markdown(self, text: str) -> None:
        sanitized = sanitize_preview_markdown(text)
        self.setMarkdown(sanitized)

    def _on_anchor(self, url) -> None:
        if self._open_handler is not None:
            self._open_handler(url)


# markdown inline-image and reference forms; the alt text is kept
# (?<!\\) keeps escaped literals (\![\[...\]] text) untouched
_IMAGE_INLINE_RE = QRegularExpression(r"(?<!\\)!\[([^\]]*)\]\(([^)]*)\)")
_IMAGE_REFERENCE_RE = QRegularExpression(
    r"(?<!\\)!\[([^\]]*)\]\[([^\]]*)\]")
_IMAGE_SHORT_RE = QRegularExpression(r"(?<!\\)!\[([^\]\[]+)\](?!\()")
# reference definition lines: [label]: url "title" (URL dropped entirely)
_REFERENCE_DEF_RE = QRegularExpression(
    r"^\s{0,3}\[[^\]\n]+\]:\s*\S+[^\n]*$",
    QRegularExpression.PatternOption.MultilineOption)


def _strip_matches(text: str, pattern) -> tuple[str, bool]:
    result = pattern.globalMatch(text)
    if not result.hasNext():
        return text, False
    out = []
    cursor = 0
    while result.hasNext():
        match = result.next()
        start = match.capturedStart()
        out.append(text[cursor:start])
        if pattern is not _REFERENCE_DEF_RE:
            out.append(match.captured(1))  # keep alt text
        cursor = start + match.capturedLength()
    out.append(text[cursor:])
    return "".join(out), True


def sanitize_preview_markdown(text: str) -> str:
    """Strip EVERY markdown image form before parsing (CR13-01).

    Inline, reference-style, folded/shortcut references and the reference
    definition lines themselves are removed; only alt text survives.  No
    URL shape is trusted - the document-level loader (see
    SecurePreviewDocument) is the fail-closed root boundary.
    """
    changed = True
    while changed:
        changed = False
        text, c = _strip_matches(text, _IMAGE_INLINE_RE)
        changed = changed or c
        text, c = _strip_matches(text, _IMAGE_REFERENCE_RE)
        changed = changed or c
        text, c = _strip_matches(text, _IMAGE_SHORT_RE)
        changed = changed or c
        text, c = _strip_matches(text, _REFERENCE_DEF_RE)
        changed = changed or c
    return text


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
        # notes side page state (V12-05): one loaded task, a dirty flag and
        # per-task drafts that survive a failed write for retry.
        # CR-U02: _note_task_id (which task the panel is open for),
        # _note_loaded (whether content actually bound to the editor) and
        # _note_dirty always switch together.
        self._note_task_id: str | None = None
        self._note_loaded = False
        self._note_dirty = False
        # CR-U07: one visible warning per task binding while retention
        # is paused by a full store of unpersisted drafts
        self._draft_limit_warned = False
        self._note_generation = self._todo.store_generation
        self._backup_dialog: QDialog | None = None
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
        self.view_box = QComboBox()
        for view in _VIEWS:
            self.view_box.addItem(view[1])
        self.view_box.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(QLabel("视图"))
        filter_row.addWidget(self.view_box)
        self.filter_box = QComboBox()
        for label, horizon in _FILTERS:
            self.filter_box.addItem(label, horizon)
        self.filter_box.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(QLabel("周期"))
        filter_row.addWidget(self.filter_box)
        self.count_label = QLabel("")
        filter_row.addWidget(self.count_label)
        filter_row.addStretch(1)
        self.focus_label = QLabel("")
        filter_row.addWidget(self.focus_label)
        root.addLayout(filter_row)

        body_splitter = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["任务", "周期", "状态", "截止"])
        # V13-04: compact fixed widths so ALL columns (priority tags in
        # the status column included) fit a 720px window with the notes
        # pane open; the title column absorbs the remaining width.  The
        # tree's minimum width pins the column total, so the splitter can
        # never push the status column out of the viewport (E-F2).
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setMinimumSectionSize(40)
        self.tree.header().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 46)
        self.tree.setColumnWidth(2, 86)
        self.tree.setColumnWidth(3, 78)
        self.tree.setMinimumWidth(40 + 46 + 86 + 78 + 2)
        if self._available:
            self.tree.setEditTriggers(
                QAbstractItemView.EditKeyPressed
                | QAbstractItemView.SelectedClicked)
        else:
            self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.installEventFilter(self)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        body_splitter.addWidget(self.tree)

        # notes side page: created up front, shown on demand when a task is
        # selected; its content always comes from note_for, never the tree
        note_panel = QWidget()
        note_layout = QVBoxLayout(note_panel)
        note_layout.setContentsMargins(4, 0, 0, 0)
        note_layout.setSpacing(2)
        note_header = QHBoxLayout()
        note_header.addWidget(QLabel("备注"))
        # V13-04: 编辑/预览切换；预览用 Qt 原生 Markdown，只读、不联网
        self._note_mode = "edit"
        self._note_mode_edit = QPushButton("编辑")
        self._note_mode_edit.setObjectName("todo_note_mode_edit")
        self._note_mode_edit.setCheckable(True)
        self._note_mode_edit.setChecked(True)
        self._note_mode_edit.setAutoDefault(False)
        self._note_mode_edit.clicked.connect(
            lambda: self._set_note_mode("edit"))
        self._note_mode_preview = QPushButton("预览")
        self._note_mode_preview.setObjectName("todo_note_mode_preview")
        self._note_mode_preview.setCheckable(True)
        self._note_mode_preview.setAutoDefault(False)
        self._note_mode_preview.clicked.connect(
            lambda: self._set_note_mode("preview"))
        note_header.addWidget(self._note_mode_edit)
        note_header.addWidget(self._note_mode_preview)
        note_header.addStretch(1)
        self.note_counter = QLabel(f"0/{MAX_NOTE_CHARS}")
        note_header.addWidget(self.note_counter)
        self._note_save_button = QPushButton("保存备注")
        self._note_save_button.setAutoDefault(False)
        self._note_save_button.clicked.connect(self._on_note_save_clicked)
        note_header.addWidget(self._note_save_button)
        note_layout.addLayout(note_header)
        self.note_editor = QPlainTextEdit()
        self.note_editor.setPlaceholderText(_NOTE_PLACEHOLDER)
        self.note_editor.textChanged.connect(self._on_note_changed)
        note_layout.addWidget(self.note_editor, 1)
        self.note_preview = _MarkdownPreview()
        self.note_preview.set_open_handler(self._open_preview_link)
        self.note_preview.setVisible(False)
        note_layout.addWidget(self.note_preview, 1)
        body_splitter.addWidget(note_panel)
        # V13-04 (acceptance E-F2): the tree pane must never shrink below
        # its own column widths, or the status column (priority tags)
        # falls out of the viewport at 720x480.  The note pane gets the
        # remainder and stays usable by scrolling.
        body_splitter.setChildrenCollapsible(False)
        body_splitter.setStretchFactor(0, 1)
        body_splitter.setStretchFactor(1, 0)
        self._body_splitter = body_splitter
        note_panel.setVisible(False)
        self._note_panel = note_panel
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(_NOTE_SAVE_DELAY_MS)
        self._note_timer.timeout.connect(self._flush_note)
        root.addWidget(body_splitter, 1)

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
            ("importance", "切换重要", self._on_toggle_importance),
            ("urgency", "切换紧急", self._on_toggle_urgency),
            ("archive", "归档子树", self._on_archive_subtree),
            ("delete", "删除", self._on_delete_subtree),
        )
        for index, (name, label, handler) in enumerate(action_specs):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            button.clicked.connect(handler)
            row, column = divmod(index, 7)
            action_grid.addWidget(button, row, column)
            self.buttons[name] = button
        for column in range(7):
            action_grid.setColumnStretch(column, 1)
        root.addLayout(action_grid)

        # backup/restore entry (CR-U04 / A-3): the entry itself stays
        # reachable whenever a store exists, because restoring a verified
        # backup is the documented self-repair path for a degraded store.
        backup_row = QHBoxLayout()
        self._backup_button = QPushButton("备份 / 恢复…")
        self._backup_button.setObjectName("todo_backup_open")
        self._backup_button.setAutoDefault(False)
        self._backup_button.clicked.connect(self._open_backup_dialog)
        backup_row.addWidget(self._backup_button)
        backup_row.addStretch(1)
        root.addLayout(backup_row)

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
        self._flush_all_drafts()
        self._active = False
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            unsubscribe()
        self._update_action_states()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._flush_all_drafts()
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

    def _view(self) -> tuple:
        """The active view spec from _VIEWS."""
        index = max(0, self.view_box.currentIndex())
        return _VIEWS[index]

    @staticmethod
    def _task_view_keys(task) -> tuple[str, ...]:
        """Which views a task belongs to (mutually exclusive classes).

        A task counts toward exactly one state key (open/done/archived)
        and exactly one classification key (one quadrant or
        uncategorized), so one sweep over the store labels every task.
        CR-U05: a task stays in 未分类 until BOTH axes are set, so a
        half-classified task can always be found and finished from the
        classification entries.
        """
        if task.archived:
            return ("archived",)
        state = "done" if task.status is Status.DONE else "open"
        if task.importance is None or task.urgency is None:
            return state, "uncategorized"
        return state, f"quad_{task.importance}_{task.urgency}"

    @staticmethod
    def _view_key(view) -> str:
        return (f"quad_{view[2]}_{view[3]}"
                if view[0] == "quadrant" else view[0])

    @staticmethod
    def _status_texts(task) -> tuple[str, str, str]:
        """Status column text plus compact priority tags (V13-04).

        The main tree shows 重要/紧急 without opening details: HIGH axes
        get a text tag in the status column (要/急); the tooltip carries
        the full classification including LOW and unset axes.
        """
        if task.archived:
            base = "已归档"
        elif task.status is Status.DONE:
            base = "已完成"
        else:
            base = "进行中"
        tags = ""
        if task.importance is Level.HIGH:
            tags += "·要"
        if task.urgency is Level.HIGH:
            tags += "·急"

        def axis(value) -> str:
            return {Level.HIGH: "高", Level.LOW: "低", None: "未设"}[value]

        detail = (f"重要：{axis(task.importance)}；"
                  f"紧急：{axis(task.urgency)}")
        return base, tags, detail

    def refresh(self) -> None:
        selected_id = self._selected_id()
        expanded_ids = {
            task_id for task_id, item in self._items_by_id.items()
            if item.isExpanded()
        }
        scroll_value = self.tree.verticalScrollBar().value()

        horizon = self._filter_horizon()
        view = self._view()
        view_key = self._view_key(view)
        all_tasks = self._todo.all_tasks()
        counts: dict[str, int] = {}
        matched = []
        for task in all_tasks:
            keys = self._task_view_keys(task)
            for key in keys:
                counts[key] = counts.get(key, 0) + 1
            if view_key in keys and (horizon is None
                                     or task.horizon == horizon):
                matched.append(task)
        # dimmed ancestor paths keep the tree structure readable without
        # pulling hidden tasks into counts (V12-05)
        matched_ids = {task.id for task in matched}
        context_ids: set[str] = set()
        for task in matched:
            cursor = task.parent_id
            while cursor is not None and cursor not in matched_ids \
                    and cursor not in context_ids:
                context_ids.add(cursor)
                parent = self._todo.get(cursor)
                cursor = parent.parent_id if parent else None
        tasks = matched + [self._todo.get(task_id)
                           for task_id in sorted(context_ids)]
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
                status_text, priority_tags, priority_detail = \
                    self._status_texts(task)
                item = QTreeWidgetItem([
                    task.title,
                    _HORIZON_LABELS[task.horizon],
                    status_text + priority_tags,
                    task.due_date.isoformat() if task.due_date else "",
                ])
                if priority_tags or task.importance is not None \
                        or task.urgency is not None:
                    # text tag first, colour only emphasises (V13-04);
                    # low/unset axes stay readable via the tooltip
                    item.setForeground(2, _IMPORTANT_TEXT)
                item.setToolTip(2, priority_detail)
                item.setData(0, Qt.UserRole, task.id)
                if task.id not in matched_ids:
                    for column in range(4):
                        item.setForeground(column, _DIM_TEXT)
                    item.setText(0, task.title + "（结构路径）")
                if self._writable_now() and task.id in matched_ids:
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

        self._update_view_counts(counts)
        if projection.focusing and projection.horizon is not None:
            self.focus_label.setText(
                f"正在专注（{_HORIZON_LABELS[projection.horizon]}）")
        else:
            self.focus_label.setText("未在专注")
        self._sync_note_with_store()
        self._update_action_states()

    def _sync_note_with_store(self) -> None:
        """Keep the notes side page consistent with the store (CR-U04).

        A store generation change (restore from backup) invalidates every
        kept draft and the loaded editor: pre-restore content must never
        be written back over the restored data.  Independently, an editor
        whose content is not loaded yet (fresh selection or a previously
        failed read) retries its read here, so a transient store fault
        heals without any extra user action.
        """
        generation = self._todo.store_generation
        if generation != self._note_generation:
            self._note_generation = generation
            self._note_timer.stop()
            self._drafts.clear()
            self._note_dirty = False
            self._note_loaded = False
            self._reset_note_editor("")
        if self._note_task_id is not None and not self._note_loaded:
            self._load_note()

    def _update_view_counts(self, counts: dict[str, int]) -> None:
        pending = counts.get("open", 0)
        for index, view in enumerate(_VIEWS):
            self.view_box.setItemText(
                index, f"{view[1]}（{counts.get(self._view_key(view), 0)}）")
        self.count_label.setText(f"待完成 {pending}")

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
                # archiving is reversible; no confirm dialog (V12-05)
                self._on_archive_subtree()
                return True
            if event.key() == Qt.Key_F2:
                self._on_rename()
                return True
        return super().eventFilter(watched, event)

    # -- safe write boundary --------------------------------------------------

    def _ensure_writable(self) -> bool:
        if not self._writable_now():
            self.status.setText(_UNAVAILABLE_TEXT)
            return False
        return True

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
        if not task_id:
            return

        def complete_with_scope():
            task = self._todo.get(task_id)
            if task is None:
                raise TodoError("task not found")
            if task.status is Status.DONE:
                return _CANCELLED
            summary = self._todo.subtree_summary(task_id)
            open_descendants = summary.open - 1  # self is open here
            if open_descendants <= 0:
                return ("self", 1, self._todo.complete(
                    task_id, scope="self"))
            scope = self._ask_complete_scope(summary.open)
            if scope == "cancel":
                return _CANCELLED
            return (scope, self._todo.complete(task_id, scope=scope),
                    open_descendants)

        success, outcome = self._run_write(complete_with_scope)
        if (success and isinstance(outcome, tuple)
                and outcome[0] in ("self", "subtree")):
            scope, count = outcome[0], outcome[1]
            if scope == "subtree":
                self.status.setText(f"已完成整个任务树（{count} 项）")

    def _ask_complete_scope(self, open_count: int) -> str:
        """One concise choice: whole subtree (primary) or only this task."""
        return self._ask_scope_choice(
            "todo_complete_scope_dialog", "完成范围",
            f"该任务下还有未完成的子任务（连同自身共 {open_count} 项未完成）。",
            ("todo_complete_subtree", f"完成整个任务树（{open_count} 项）",
             "subtree"),
            ("todo_complete_self", "仅完成此项", "self"),
            primary="subtree")

    def _ask_restore_scope(self, done_count: int) -> str:
        """Default restores only the selection; subtree is the opt-in."""
        return self._ask_scope_choice(
            "todo_restore_scope_dialog", "恢复范围",
            f"该任务下还有已完成的子任务（连同自身共 {done_count} 项已完成）。",
            ("todo_restore_self", "仅恢复此项", "self"),
            ("todo_restore_subtree", f"恢复整个子树（{done_count} 项）",
             "subtree"),
            primary="self")

    def _ask_scope_choice(self, dialog_name: str, title: str, text: str,
                          first, second, *, primary: str) -> str:
        dialog = QDialog(self)
        dialog.setObjectName(dialog_name)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        buttons = QHBoxLayout()
        outcome = ["cancel"]

        def choose(value: str) -> None:
            outcome[0] = value
            dialog.accept()

        specs = (first, second)
        for object_name, label_text, value in specs:
            button = QPushButton(label_text)
            button.setObjectName(object_name)
            button.setAutoDefault(False)
            button.setDefault(value == primary)
            button.clicked.connect(lambda _checked=False, v=value:
                                   choose(v))
            buttons.addWidget(button)
        cancel_button = QPushButton("取消")
        cancel_button.setObjectName(f"{dialog_name}_cancel")
        cancel_button.setAutoDefault(False)
        cancel_button.clicked.connect(dialog.reject)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)
        dialog.exec()
        return outcome[0]

    def _on_restore(self) -> None:
        task_id = self._selected_id()
        if not task_id:
            return

        def restore_by_state():
            task = self._todo.get(task_id)
            if task is None:
                raise TodoError("task not found")
            if task.archived:
                return ("archived", self._todo.restore_archived(task_id))
            if task.status is Status.OPEN:
                return _CANCELLED
            summary = self._todo.subtree_summary(task_id)
            done_descendants = summary.done - 1  # self is done here
            if done_descendants <= 0:
                return (("done", "self"),
                        self._todo.restore(task_id, scope="self"))
            scope = self._ask_restore_scope(summary.done)
            if scope == "cancel":
                return _CANCELLED
            return (("done", scope),
                    self._todo.restore(task_id, scope=scope))

        success, outcome = self._run_write(restore_by_state)
        if success and isinstance(outcome, tuple):
            kind, count = outcome[0], outcome[1]
            if kind == "archived":
                self.status.setText(f"已恢复 {count} 个任务")
            elif outcome[0][1] == "subtree":
                self.status.setText(f"已恢复整个子树（{count} 项）")

    def _on_archive_subtree(self) -> None:
        task_id = self._selected_id()
        if not task_id:
            return

        def archive_if_not_cancelled():
            task = self._todo.get(task_id)
            if task is None:
                raise TodoError("task not found")
            if task.parent_id is not None:
                parent = self._todo.get(task.parent_id)
                if parent is not None and parent.archived:
                    # already inside an archived tree; nothing to do
                    return 0
            return self._todo.archive_subtree(task_id)

        success, count = self._run_write(archive_if_not_cancelled)
        if success and count:
            self.status.setText(
                f"已归档 {count} 个任务，可在“已归档”视图恢复")

    # -- physical delete (V13-03) ---------------------------------------------

    def _on_delete_subtree(self) -> None:
        task_id = self._selected_id()
        if not task_id:
            return

        def delete_after_confirm():
            summary = self._todo.subtree_summary(task_id)
            focus_note = ("，包含当前正在处理的任务"
                          if summary.focus_included else "")
            archived_note = (f"，其中已归档 {summary.archived} 个"
                             if summary.archived else "")
            text = (f"将永久删除该任务及整个子树，共 {summary.total} 个任务"
                    f"（未完成 {summary.open} 个{archived_note}{focus_note}）。\n"
                    "删除不可撤销；如只想暂时收起，请改用“归档子树”。")
            if not self._confirm("删除任务树", text):
                return _CANCELLED
            return self._todo.delete_subtree(task_id), summary.ids

        success, outcome = self._run_write(delete_after_confirm)
        if success and isinstance(outcome, tuple):
            count, deleted_ids = outcome
            self._forget_deleted_subtree(deleted_ids)
            self.status.setText(f"已删除 {count} 个任务")

    def _confirm(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(
            self, title, text, QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No)
        return answer == QMessageBox.Yes

    def _forget_deleted_subtree(self, deleted_ids) -> None:
        """Drop per-task UI state owned by the deleted subtree (V13-03).

        Kept note drafts for deleted tasks are removed (they could never
        save again), and a note editor still bound to a deleted task is
        unbound without attempting one last (failing) flush.
        """
        for task_id in deleted_ids:
            self._drafts.pop(task_id, None)
        if self._note_task_id in deleted_ids:
            self._note_timer.stop()
            self._draft_limit_warned = False
            self._note_task_id = None
            self._note_loaded = False
            self._note_dirty = False
            self._note_panel.setVisible(False)
            self._reset_note_editor("")

    def _toggle_axis(self, axis: str) -> None:
        task_id = self._selected_id()
        if not task_id:
            return

        def cycle_level():
            task = self._todo.get(task_id)
            if task is None:
                raise TodoError("task not found")
            current = getattr(task, axis)
            nxt = _LEVEL_CYCLE[current]
            getattr(self._todo, f"set_{axis}")(task_id, nxt)
            return nxt

        success, new_level = self._run_write(cycle_level)
        if success:
            label = "重要" if axis == "importance" else "紧急"
            if new_level is None:
                # CR-U05: the cleared state is exactly "unclassified" and
                # must not keep the previous 高/低 success wording
                self.status.setText(f"{label}已清除（未分类）")
            else:
                shown = {Level.HIGH: "高", Level.LOW: "低"}[new_level]
                self.status.setText(f"{label}：{shown}")

    def _on_toggle_importance(self) -> None:
        self._toggle_axis("importance")

    def _on_toggle_urgency(self) -> None:
        self._toggle_axis("urgency")

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

    # -- notes side page (V12-05, reworked per CR-U02/U03) ----------------------
    #
    # Policy: autosave with a short debounce, plus a flush whenever the
    # selection leaves the task or the page closes.  The editor is bound
    # to a task ONLY after its content was actually loaded (or a kept
    # draft for that same task was found); a failed read shows an explicit
    # failure state and cannot be saved, so one task's content can never
    # be written under another task's id.  A failed write keeps its draft
    # in the application-level bounded store, so it survives task
    # switches, page disposal and panel close and is re-offered on the
    # next selection.

    @property
    def _drafts(self) -> dict[str, str]:
        """Bounded application-level kept-draft store (CR-U03)."""
        return self._app.todo_note_drafts

    def _on_current_changed(self, current, _previous) -> None:
        self._update_action_states()
        if self._refreshing:
            return
        task_id = current.data(0, Qt.UserRole) \
            if current is not None else None
        self._open_note_for(task_id)

    def _open_note_for(self, task_id: str | None) -> None:
        if task_id == self._note_task_id:
            return
        # flush while the outgoing content is still bound to ITS task
        self._flush_note()
        self._draft_limit_warned = False
        # identity, load state and buffer switch together (CR-U02)
        self._note_task_id = task_id
        self._note_loaded = False
        self._note_dirty = False
        if task_id is None:
            self._note_panel.setVisible(False)
            self._reset_note_editor("")
            self._update_action_states()
            return
        self._note_panel.setVisible(True)
        self._reset_note_editor("")
        self._apply_splitter_sizes()
        self._load_note()

    def _reset_note_editor(self, text: str, *,
                           placeholder: str = _NOTE_PLACEHOLDER) -> None:
        blocked = self.note_editor.blockSignals(True)
        try:
            self.note_editor.setPlainText(text)
        finally:
            self.note_editor.blockSignals(blocked)
        self.note_editor.setPlaceholderText(placeholder)
        self._update_note_counter()
        if self._note_mode == "preview":
            self.note_preview.render_markdown(text or placeholder)

    def _apply_splitter_sizes(self) -> None:
        """Give the note pane the space left over by the tree's minimum.

        The tree's minimumWidth already pins its column total; this only
        balances the remainder so the note editor stays usable.
        """
        splitter = self._body_splitter
        total = max(1, splitter.width())
        tree_minimum = self.tree.minimumWidth()
        note_width = max(60, min(total - tree_minimum, int(total * 0.45)))
        splitter.setSizes([total - note_width, note_width])

    def _set_note_mode(self, mode: str) -> None:
        if mode == self._note_mode:
            self._sync_note_mode_buttons()
            return
        self._note_mode = mode
        self._sync_note_mode_buttons()
        if mode == "preview":
            # preview always mirrors the CURRENT editor content (an
            # unsaved draft included); it never reads or writes the store
            self.note_preview.render_markdown(self.note_editor.toPlainText())
            self.note_editor.setVisible(False)
            self.note_preview.setVisible(True)
        else:
            self.note_preview.setVisible(False)
            self.note_editor.setVisible(True)
        self._update_action_states()

    def _sync_note_mode_buttons(self) -> None:
        self._note_mode_edit.setChecked(self._note_mode == "edit")
        self._note_mode_preview.setChecked(self._note_mode == "preview")

    def _open_preview_link(self, url) -> None:
        """Controlled open: http/https only, behind one confirmation."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        parsed = QUrl(url)
        if parsed.scheme() not in ("http", "https") or not parsed.host():
            self.status.setText("预览只允许打开 http/https 链接")
            return
        answer = QMessageBox.question(
            self, "打开链接",
            f"要在默认浏览器打开 {parsed.host()} 的链接吗？")
        if answer == QMessageBox.Yes:
            QDesktopServices.openUrl(parsed)

    def _show_note_unloaded(self, placeholder: str) -> None:
        """Explicit failure state: nothing writable until a read succeeds."""
        self._note_dirty = False
        self._note_loaded = False
        self._reset_note_editor("", placeholder=placeholder)
        self._update_action_states()

    def _load_note(self) -> None:
        task_id = self._note_task_id
        if task_id is None or self._note_loaded:
            return
        draft = self._drafts.get(task_id)
        if draft is not None:
            # a kept draft is newer than the store for this task; show it
            # so a previously failed save can simply be retried
            self._note_dirty = True
            self._note_loaded = True
            self._reset_note_editor(draft)
        else:
            try:
                text = self._todo.note_for(task_id)
            except TaskStoreUnavailable:
                self.status.setText(_UNAVAILABLE_TEXT)
                self._show_note_unloaded(_NOTE_LOAD_FAILED_TEXT)
                return
            except TodoError:
                self.status.setText(_DOMAIN_ERROR_TEXT)
                self._show_note_unloaded(_NOTE_LOAD_FAILED_TEXT)
                return
            except Exception:  # noqa: BLE001 - privacy-safe Qt slot boundary
                logger.error("todo note read failed")
                self.status.setText(_UNKNOWN_ERROR_TEXT)
                self._show_note_unloaded(_NOTE_LOAD_FAILED_TEXT)
                return
            self._note_dirty = False
            self._note_loaded = True
            self._reset_note_editor(text)
        self._update_action_states()

    def _on_note_changed(self) -> None:
        if self._note_task_id is None or not self._note_loaded:
            return
        self._note_dirty = True
        if not self._keep_draft(self._note_task_id,
                                self.note_editor.toPlainText()):
            self._warn_draft_limit()
        self._update_note_counter()
        self._note_timer.start()

    def _keep_draft(self, task_id: str, text: str) -> bool:
        """Retain a failed-write draft; True once it is kept (CR-U07).

        A full store of still-unpersisted drafts never evicts content:
        the incoming draft is refused and the caller surfaces the limit.
        Successful saves pop their entry, so the space recycles itself.
        """
        drafts = self._drafts
        if task_id in drafts or len(drafts) < _MAX_KEPT_NOTE_DRAFTS:
            drafts[task_id] = text
            return True
        return False

    def _warn_draft_limit(self) -> None:
        if self._draft_limit_warned:
            return
        self._draft_limit_warned = True
        self.status.setText(
            f"备注保留队列已满（{_MAX_KEPT_NOTE_DRAFTS} 条未保存），"
            "当前内容暂不纳入自动保留，请先保存成功腾出空间")

    def _update_note_counter(self) -> None:
        length = len(self.note_editor.toPlainText())
        self.note_counter.setText(f"{length}/{MAX_NOTE_CHARS}")

    def _flush_note(self) -> None:
        """Persist the dirty draft, if any; failures keep it for retry."""
        task_id = self._note_task_id
        # an editor without loaded content must never be written (CR-U02)
        if task_id is None or not self._note_loaded or not self._note_dirty:
            return
        if not self._writable_now():
            return
        text = self.note_editor.toPlainText()
        if self._write_note_text(task_id, text):
            self._note_dirty = False
            self._drafts.pop(task_id, None)
            self.status.setText("备注已保存")
        elif task_id not in self._drafts:
            # CR-U07: the write failed AND retention refused this draft,
            # so "content is kept" would be a false promise here
            self.status.setText(
                "备注未保存：保留队列已满，当前内容未被自动保留")

    def _write_note_text(self, task_id: str, text: str) -> bool:
        try:
            self._todo.set_note(task_id, text)
        except TaskStoreUnavailable:
            self.status.setText(_UNAVAILABLE_TEXT)
            return False
        except TodoError as exc:
            # includes the over-limit rejection: visible, not truncated
            self.status.setText(f"备注未保存：{exc}")
            return False
        except Exception:  # noqa: BLE001 - privacy-safe Qt slot boundary
            logger.error("todo note write failed")
            self.status.setText("备注未保存，内容已保留，稍后会重试")
            return False
        return True

    def _flush_kept_draft(self, task_id: str) -> bool:
        """Retry one kept draft directly; True only once it is persisted."""
        text = self._drafts.get(task_id)
        if text is None or not self._writable_now():
            return False
        if self._write_note_text(task_id, text):
            self._drafts.pop(task_id, None)
            return True
        return False

    def _on_note_save_clicked(self) -> None:
        self._note_timer.stop()
        self._flush_note()

    def _flush_all_drafts(self) -> None:
        """Close-time flush of the live editor and EVERY kept draft.

        Kept drafts live in the application-level store, so a write that
        still fails here survives the page being disposed with the panel
        (CR-U03) and is offered again when the task is next selected.
        """
        self._note_timer.stop()
        live_task_id = self._note_task_id
        if self._note_loaded:
            self._flush_note()
        for task_id in list(self._drafts):
            if task_id == live_task_id and self._note_loaded:
                continue  # the live editor flush above covered this task
            self._flush_kept_draft(task_id)

    # -- backup / restore (CR-U04, A-3) ----------------------------------------
    #
    # The dialog is the explicit user entry for create_manual_backup,
    # list_backups and restore_from_backup.  It states the overwrite
    # scope before restoring, drops unsaved drafts first, and reports the
    # three outcomes separately: full success, "data restored on disk but
    # the store could not be reopened", and ordinary failure.

    def _open_backup_dialog(self) -> None:
        if not self._available or self._disposed:
            return
        dialog = QDialog(self)
        dialog.setObjectName("todo_backup_dialog")
        dialog.setWindowTitle("备份与恢复")
        layout = QVBoxLayout(dialog)
        hint = QLabel(
            "备份是任务库的完整只读快照。恢复会用所选备份替换当前全部任务，"
            "当前数据会先自动另存为 pre-restore 备份；未保存的备注草稿会被丢弃。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        listing = QListWidget()
        listing.setObjectName("todo_backup_list")
        layout.addWidget(listing, 1)
        self._backup_list = listing
        buttons = QHBoxLayout()
        create_button = QPushButton("创建备份")
        create_button.setObjectName("todo_backup_create")
        create_button.setAutoDefault(False)
        create_button.clicked.connect(self._on_backup_create)
        restore_button = QPushButton("恢复所选…")
        restore_button.setObjectName("todo_backup_restore")
        restore_button.setAutoDefault(False)
        restore_button.clicked.connect(self._on_backup_restore)
        refresh_button = QPushButton("刷新")
        refresh_button.setObjectName("todo_backup_refresh")
        refresh_button.setAutoDefault(False)
        refresh_button.clicked.connect(self._reload_backup_list)
        close_button = QPushButton("关闭")
        close_button.setObjectName("todo_backup_close")
        close_button.setAutoDefault(False)
        close_button.clicked.connect(dialog.close)
        for button in (create_button, restore_button, refresh_button,
                       close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._backup_status = QLabel("")
        self._backup_status.setWordWrap(True)
        layout.addWidget(self._backup_status)
        create_button.setEnabled(self._writable_now())
        self._backup_dialog = dialog
        dialog.finished.connect(self._forget_backup_dialog)
        self._reload_backup_list()
        dialog.setModal(True)
        dialog.show()
        dialog.raise_()

    def _forget_backup_dialog(self, *_args) -> None:
        self._backup_dialog = None

    def _reload_backup_list(self) -> None:
        if self._backup_dialog is None:
            return
        listing = self._backup_list
        listing.clear()
        try:
            summaries = self._todo.list_backups()
        except TaskStoreUnavailable:
            self._backup_status.setText(_UNAVAILABLE_TEXT)
            return
        except Exception:  # noqa: BLE001 - privacy-safe Qt slot boundary
            logger.error("todo backup listing failed")
            self._backup_status.setText(_UNKNOWN_ERROR_TEXT)
            return
        for summary in summaries:
            # show second precision whether the manifest stored seconds
            # (older backups) or microseconds
            stamp = (summary.created_at or "时间未知")[:19]
            kind = summary.kind or "来源未知"
            verified = "校验通过" if summary.valid else "无法校验"
            item = QListWidgetItem(
                f"{stamp} · {kind} · {summary.task_count or 0} 个任务"
                f" · {verified}")
            item.setData(Qt.UserRole, str(summary.directory))
            listing.addItem(item)
        if not summaries:
            self._backup_status.setText("还没有备份；创建备份后可在此恢复。")

    def _on_backup_create(self) -> None:
        if not self._writable_now():
            self._backup_status.setText(_UNAVAILABLE_TEXT)
            return
        try:
            record = self._todo.create_manual_backup()
        except TaskStoreUnavailable:
            self._backup_status.setText(_UNAVAILABLE_TEXT)
            return
        except TodoError:
            self._backup_status.setText(_DOMAIN_ERROR_TEXT)
            return
        except Exception:  # noqa: BLE001 - privacy-safe Qt slot boundary
            logger.error("todo manual backup failed")
            self._backup_status.setText(_UNKNOWN_ERROR_TEXT)
            return
        self._backup_status.setText(
            f"已创建备份（{record.task_count} 个任务）")
        self._reload_backup_list()
        self._safe_refresh()

    def _on_backup_restore(self) -> None:
        item = self._backup_list.currentItem()
        if item is None:
            self._backup_status.setText("先在列表中选择一个要恢复的备份")
            return
        backup_directory = item.data(Qt.UserRole)
        pending = len(self._drafts)
        scope = ("恢复会用所选备份替换当前全部任务数据；"
                 f"还有 {pending} 条未保存的备注草稿，会被丢弃"
                 if pending else
                 "恢复会用所选备份替换当前全部任务数据")
        answer = QMessageBox.question(
            self, "确认恢复",
            f"{scope}。当前数据会先自动另存为 pre-restore 备份。继续？")
        if answer != QMessageBox.Yes:
            return
        self._prepare_for_restore()
        message: str
        try:
            result = self._todo.restore_from_backup(backup_directory)
        except TaskStoreUnavailable:
            message = "恢复未完成：任务存储暂不可用，现有数据未改动"
        except BackupError as exc:
            if exc.reason == "restore_publish_unknown":
                # os.replace already completed: the on-disk store very
                # likely IS the backup content, so "unchanged" would be
                # false; the listing below reflects the reopened store
                message = ("恢复已替换磁盘数据，但写入完成状态未知；"
                           "任务列表已按重新打开的存储刷新。如需回到恢复前，"
                           "请选择最新的 pre-restore 备份再次恢复")
            else:
                message = "恢复未完成：所选备份无法使用，现有数据未改动"
        except TodoError:
            message = _DOMAIN_ERROR_TEXT
        except Exception:  # noqa: BLE001 - privacy-safe Qt slot boundary
            logger.error("todo restore from backup failed")
            message = _UNKNOWN_ERROR_TEXT
        else:
            if result.store_available:
                message = f"恢复完成（{result.task_count} 个任务）"
            else:
                # the on-disk data WAS restored; the module is now in its
                # write-forbidden state until a later restore repairs it
                message = ("备份内容已写回磁盘，但任务存储暂不可用；"
                           "重启应用后可再次在此恢复以修复")
        # refresh the listing first so its own status text cannot clobber
        # the restore outcome the user must see
        self._reload_backup_list()
        self._backup_status.setText(message)
        self._after_restore()

    def _prepare_for_restore(self) -> None:
        """Drop every pre-restore draft and unbind the editor (CR-U04)."""
        self._note_timer.stop()
        self._drafts.clear()
        self._note_dirty = False
        self._note_loaded = False
        self._reset_note_editor("")
        self._update_action_states()

    def _after_restore(self) -> None:
        """Rebuild the page from the new store (CR-U04)."""
        self._safe_refresh()

    # -- enabled state --------------------------------------------------------

    def _writable_now(self) -> bool:
        """Live store health, re-read on every use (CR-U04).

        A store that turned degraded (e.g. after a restore whose fresh
        reopen failed) must disable every write entry immediately, not
        just at page construction.
        """
        return (self._available and not self._todo.degraded
                and self._active and not self._disposed)

    def _update_action_states(self) -> None:
        writable = self._writable_now()
        self.input.setEnabled(writable)
        self.horizon_box.setEnabled(writable)
        self._add_button.setEnabled(writable)
        self._backup_button.setEnabled(
            self._available and self._active and not self._disposed)
        # the notes side page follows the same write gate as the tree,
        # and additionally requires actually-loaded content (CR-U02)
        note_writable = writable and self._note_loaded
        self.note_editor.setReadOnly(not note_writable)
        self._note_save_button.setEnabled(note_writable)
        # mode toggling only makes sense with bound content (V13-04); in
        # the unavailable store nothing is ever bound, so every button
        # on the page stays disabled there
        note_mode_enabled = self._note_loaded
        self._note_mode_edit.setEnabled(note_mode_enabled)
        self._note_mode_preview.setEnabled(note_mode_enabled)

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
        is_archived = task.archived
        # physical delete works on every state (V13-03); archiving stays
        # the everyday action, delete is the explicit destructive one
        self.buttons["delete"].setEnabled(True)
        self.buttons["complete"].setEnabled(not is_done and not is_archived)
        # CR-U01: every archived task can be unarchived (keeping its
        # completion state); a completed unarchived task can be reopened.
        self.buttons["restore"].setEnabled(is_done or is_archived)
        for name in ("rename", "add_subtask", "horizon", "due"):
            self.buttons[name].setEnabled(not is_archived)
        self.buttons["importance"].setEnabled(not is_archived)
        self.buttons["urgency"].setEnabled(not is_archived)
        self.buttons["archive"].setEnabled(not is_archived)
        self.buttons["focus_start"].setEnabled(
            not is_done and not is_archived and task_id != self._focus_id)

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
