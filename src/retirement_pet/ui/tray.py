"""System tray icon and its menu (design section 10, DESIGN_V2 11.2).

The tray is the recovery path when the window is hidden or click-through,
so the context menu MUST be attached via ``setContextMenu`` - right-clicks
on the tray icon then open it even when the pet itself cannot receive any
input.  Menu content is re-populated on every ``aboutToShow`` so checkable
state (穿透 / 置顶 / 自启…) never goes stale; the app supplies the content
through a populate callback so the tray stays dumb.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def make_tray_icon() -> QIcon:
    """Use assets/icons artwork when present, else draw a cat face."""
    from retirement_pet.resource_path import asset_path

    for name in ("retirement_pet.ico", "retirement_pet.png"):
        icon_file = asset_path("icons", name)
        if icon_file.is_file():
            icon = QIcon(str(icon_file))
            if not icon.isNull():
                return icon

    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(Qt.NoPen))
        fur = QBrush(QColor(233, 223, 203))
        painter.setBrush(fur)
        painter.drawPolygon(
            QPolygonF([QPointF(10, 20), QPointF(16, 3), QPointF(29, 15)])
        )
        painter.drawPolygon(
            QPolygonF([QPointF(35, 15), QPointF(48, 3), QPointF(54, 20)])
        )
        painter.drawEllipse(8, 12, 48, 46)
        painter.setBrush(QBrush(QColor(58, 58, 64)))
        painter.drawEllipse(22, 30, 7, 9)
        painter.drawEllipse(35, 30, 7, 9)
        painter.setBrush(QBrush(QColor(185, 138, 133)))
        painter.drawEllipse(29, 45, 6, 4)
    finally:
        painter.end()
    return QIcon(pix)


class TrayController(QObject):
    #: Tray icon left-clicked (primary activation).
    activated_toggle = Signal()

    def __init__(
        self,
        menu_populate: Callable[[QMenu], None],
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._menu_populate = menu_populate
        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._rebuild_menu)
        self._icon = QSystemTrayIcon(make_tray_icon(), self)
        # The recovery contract (ADR-V2-012): right-clicking the tray icon
        # must always open the menu, including while the pet window is
        # click-through, hidden or off-screen.  setContextMenu is what makes
        # the platform show it without any window involvement.
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)

    def _rebuild_menu(self) -> None:
        self._menu.clear()
        self._menu_populate(self._menu)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.activated_toggle.emit()

    @property
    def icon(self) -> QSystemTrayIcon:
        return self._icon

    @property
    def menu(self) -> QMenu:
        return self._menu

    def rebuild_menu(self) -> None:
        """Populate the menu right now (programmatic access / tests)."""
        self._rebuild_menu()

    def show(self) -> None:
        self._icon.show()

    def hide(self) -> None:
        self._icon.hide()

    def popup_menu(self, global_pos) -> None:
        self._rebuild_menu()
        self._menu.popup(global_pos)

    def show_message(self, title: str, text: str) -> None:
        self._icon.showMessage(title, text, QSystemTrayIcon.Information, 4000)
