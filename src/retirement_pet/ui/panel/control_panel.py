"""Control panel shell: fixed nav, lazy pages, one page live at a time."""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

#: page id -> (nav title, factory).  Factories are called ONCE, on first
#: visit (lazy); the panel never instantiates all pages up front.
PAGE_IDS: dict[str, str] = {
    "overview": "概览",
    "characters": "角色",
    "actions": "动作与决策",
    "text": "文案",
    "display": "显示",
    "sound_schedule": "声音与日程",
    "todo": "待办事项",
    "about": "关于与诊断",
}


class ControlPanel(QDialog):
    """Predefined-layout multi-page panel; standard window flags."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._app = app
        self.setWindowTitle("退休宠物 · 控制面板")
        self.setModal(False)
        self.setMinimumSize(720, 480)
        self.setSizeGripEnabled(True)

        self._factories: dict[str, Callable[[], QWidget]] = {}
        self._built: dict[str, QWidget] = {}
        self._active_page: QWidget | None = None
        self._disposed = False

        root = QVBoxLayout(self)
        body = QHBoxLayout()
        root.addLayout(body, 1)

        self._nav = QListWidget()
        self._nav.setMaximumWidth(150)
        self._nav.currentItemChanged.connect(self._on_nav_changed)
        body.addWidget(self._nav)

        self._stack = QStackedWidget()
        body.addWidget(self._stack, 1)

        footer = QHBoxLayout()
        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignLeft)
        footer.addWidget(self._status, 1)
        close_btn = QPushButton("关闭")
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        root.addLayout(footer)

    # -- registration ------------------------------------------------------

    def register_page(self, page_id: str, factory: Callable[[], QWidget]):
        if page_id not in PAGE_IDS:
            raise ValueError(f"unknown page id: {page_id}")
        self._factories[page_id] = factory
        QListWidgetItem(PAGE_IDS[page_id], self._nav)

    def finish_registration(self) -> None:
        if self._nav.count() == 0:
            raise RuntimeError("control panel has no pages registered")

    # -- navigation ----------------------------------------------------------

    def open_page(self, page_id: str) -> bool:
        """Switch to a page, building it on first visit."""
        if self._disposed:
            return False
        if page_id not in PAGE_IDS or page_id not in self._factories:
            return False
        for row in range(self._nav.count()):
            item = self._nav.item(row)
            if PAGE_IDS[page_id] == item.text():
                self._nav.setCurrentItem(item)
                return True
        return False

    def _on_nav_changed(self, current, _previous) -> None:
        if current is None or self._disposed:
            return
        title = current.text()
        page_id = next(pid for pid, name in PAGE_IDS.items() if name == title)
        widget = self._built.get(page_id)
        if widget is None:
            factory = self._factories[page_id]
            widget = factory()
            self._built[page_id] = widget
            self._stack.addWidget(widget)
            logger.debug("control panel page built: %s", page_id)
        if self._active_page is not widget:
            if self._active_page is not None and hasattr(
                    self._active_page, "deactivate"):
                self._active_page.deactivate()
            self._active_page = widget
        self._stack.setCurrentWidget(widget)
        if hasattr(widget, "activate"):
            widget.activate()

    # -- lifecycle ------------------------------------------------------------

    def built_page_ids(self) -> list[str]:
        return list(self._built)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def _dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._active_page is not None and hasattr(
                self._active_page, "deactivate"):
            self._active_page.deactivate()
        self._active_page = None
        for widget in list(self._built.values()):
            if hasattr(widget, "dispose"):
                widget.dispose()
            self._stack.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._built.clear()
        self._app._on_control_panel_disposed(self)

    def done(self, result: int) -> None:
        self._dispose()
        super().done(result)
        self.deleteLater()

    def accept(self) -> None:
        self.done(QDialog.Accepted)

    def reject(self) -> None:
        self.done(QDialog.Rejected)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._dispose()
        super().closeEvent(event)
        self.deleteLater()
