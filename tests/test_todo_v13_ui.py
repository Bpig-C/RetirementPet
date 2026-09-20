"""V13-03 UI wiring: delete entry, complete/restore scope dialogs.

隔离约定：全部使用独立临时数据目录与合成任务，不触碰日用数据库；
对话框通过 monkeypatch 驱动，不做真实模态交互。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def app(qt_application, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    pet = PetApplication(
        argv=["retirement-pet"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-v13ui-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


def _todo_page(app):
    app._open_control_panel("todo")
    return app._panel._built["todo"]


def _select_task(page, task_id):
    page.tree.setCurrentItem(page._items_by_id[task_id])


def _build_tree(app):
    """root -> (child -> grandchild); plus an independent survivor root."""
    from retirement_pet.todo import Horizon

    root = app.todo.add_task("父任务", Horizon.MEDIUM)
    child = app.todo.add_subtask(root.id, "子任务")
    grandchild = app.todo.add_subtask(child.id, "孙任务")
    survivor = app.todo.add_task("独立任务", Horizon.SHORT)
    return root, child, grandchild, survivor


def _confirm_yes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    prompts = []

    def fake_question(_parent, _title, text, *args, **kwargs):
        prompts.append(text)
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", fake_question)
    return prompts


def _confirm_no(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *_a, **_k: QMessageBox.No))


# -- delete entry -------------------------------------------------------------


def test_delete_button_removes_subtree_after_single_confirm(
        app, qt_application, monkeypatch):
    from retirement_pet.todo import Status

    root, child, grandchild, survivor = _build_tree(app)
    page = _todo_page(app)
    _select_task(page, root.id)
    qt_application.processEvents()
    assert page.buttons["delete"].isEnabled()

    prompts = _confirm_yes(monkeypatch)
    page.buttons["delete"].click()
    qt_application.processEvents()

    assert len(prompts) == 1  # exactly one confirmation
    assert "共 3 个任务" in prompts[0]
    assert "未完成 3 个" in prompts[0]
    assert app.todo.get(root.id) is None
    assert app.todo.get(child.id) is None
    assert app.todo.get(grandchild.id) is None
    survivor_row = app.todo.get(survivor.id)
    assert survivor_row is not None
    assert survivor_row.status is Status.OPEN
    assert "已删除 3 个任务" in page.status.text()
    page.refresh()
    assert survivor.id in page._items_by_id
    assert root.id not in page._items_by_id


def test_delete_cancelled_keeps_whole_tree(app, qt_application, monkeypatch):
    root, child, _grandchild, survivor = _build_tree(app)
    page = _todo_page(app)
    _select_task(page, root.id)
    qt_application.processEvents()

    _confirm_no(monkeypatch)
    page.buttons["delete"].click()
    qt_application.processEvents()

    for task in (root, child, survivor):
        assert app.todo.get(task.id) is not None
    assert "已删除" not in page.status.text()


def test_delete_confirm_shows_focus_and_archived_counts(
        app, qt_application, monkeypatch):
    root, child, _grandchild, _survivor = _build_tree(app)
    app.todo.archive_subtree(child.id)
    app.todo.start_focus(root.id)
    page = _todo_page(app)
    _select_task(page, root.id)
    qt_application.processEvents()

    prompts = _confirm_yes(monkeypatch)
    page.buttons["delete"].click()
    qt_application.processEvents()

    assert "已归档 2 个" in prompts[0]
    assert "包含当前正在处理的任务" in prompts[0]
    assert app.todo.focus_task_id() is None  # cleared with the subtree


def test_delete_clears_kept_drafts_and_note_binding(
        app, qt_application, monkeypatch):
    root, child, grandchild, _survivor = _build_tree(app)
    app.todo.set_note(child.id, "已保存备注")
    page = _todo_page(app)
    _select_task(page, child.id)
    qt_application.processEvents()
    assert page._note_task_id == child.id
    # simulate a kept (failed-save) draft for the grandchild
    app.todo_note_drafts[grandchild.id] = "未保存草稿"

    _select_task(page, root.id)
    qt_application.processEvents()
    _confirm_yes(monkeypatch)
    page.buttons["delete"].click()
    qt_application.processEvents()

    assert app.todo_note_drafts == {}  # drafts for deleted tasks dropped
    assert page._note_task_id is None
    assert not page._note_panel.isVisible()


def test_delete_failure_keeps_tree_and_reports_error(
        app, qt_application, monkeypatch):
    root, child, _grandchild, survivor = _build_tree(app)
    page = _todo_page(app)
    _select_task(page, root.id)
    qt_application.processEvents()
    _confirm_yes(monkeypatch)

    def failing_delete(_task_id):
        raise RuntimeError("disk error")

    monkeypatch.setattr(app.todo, "delete_subtree", failing_delete)
    page.buttons["delete"].click()
    qt_application.processEvents()

    assert "操作失败" in page.status.text()
    for task in (root, child, survivor):
        assert app.todo.get(task.id) is not None


def test_delete_enabled_for_archived_tasks(app, qt_application):
    root, child, _grandchild, _survivor = _build_tree(app)
    app.todo.archive_subtree(child.id)
    page = _todo_page(app)
    page.view_box.setCurrentIndex(2)  # 已归档 view
    page.refresh()
    _select_task(page, child.id)
    qt_application.processEvents()
    assert page.buttons["delete"].isEnabled()


# -- complete scope dialog ------------------------------------------------------


def _scope_reply(monkeypatch, scope):
    calls = []

    def fake_ask(self, count):
        calls.append(count)
        return scope

    from retirement_pet.ui.panel import todo_page

    monkeypatch.setattr(todo_page.TodoPage, "_ask_complete_scope", fake_ask)
    return calls


def test_complete_leaf_does_not_ask_scope(app, qt_application, monkeypatch):
    root, _child, grandchild, _survivor = _build_tree(app)
    page = _todo_page(app)
    _select_task(page, grandchild.id)
    qt_application.processEvents()

    calls = _scope_reply(monkeypatch, "self")
    page.buttons["complete"].click()
    qt_application.processEvents()
    assert calls == []  # leaf: no scope dialog
    assert app.todo.get(grandchild.id).status.value == "done"


def test_complete_parent_subtree_scope_finishes_all(
        app, qt_application, monkeypatch):
    root, child, grandchild, _survivor = _build_tree(app)
    page = _todo_page(app)
    _select_task(page, root.id)
    qt_application.processEvents()

    calls = _scope_reply(monkeypatch, "subtree")
    page.buttons["complete"].click()
    qt_application.processEvents()

    assert calls == [3]  # three open tasks including the root
    assert app.todo.get(root.id).status.value == "done"
    assert app.todo.get(child.id).status.value == "done"
    assert app.todo.get(grandchild.id).status.value == "done"
    assert "已完成整个任务树（3 项）" in page.status.text()


def test_complete_parent_self_scope_keeps_children_open(
        app, qt_application, monkeypatch):
    root, child, grandchild, _survivor = _build_tree(app)
    page = _todo_page(app)
    _select_task(page, root.id)
    qt_application.processEvents()

    _scope_reply(monkeypatch, "self")
    page.buttons["complete"].click()
    qt_application.processEvents()

    assert app.todo.get(root.id).status.value == "done"
    assert app.todo.get(child.id).status.value == "open"
    assert app.todo.get(grandchild.id).status.value == "open"


def test_complete_cancelled_changes_nothing(app, qt_application, monkeypatch):
    root, child, _grandchild, _survivor = _build_tree(app)
    page = _todo_page(app)
    _select_task(page, root.id)
    qt_application.processEvents()

    _scope_reply(monkeypatch, "cancel")
    page.buttons["complete"].click()
    qt_application.processEvents()

    assert app.todo.get(root.id).status.value == "open"
    assert app.todo.get(child.id).status.value == "open"


def test_complete_scope_dialog_has_primary_subtree_button(
        app, qt_application, monkeypatch):
    from PySide6.QtWidgets import QDialog, QPushButton

    from retirement_pet.ui.panel import todo_page

    page = _todo_page(app)
    clicked = {}

    def fake_exec(self):
        assert self.objectName() == "todo_complete_scope_dialog"
        for name, key in (("todo_complete_subtree", "subtree"),
                          ("todo_complete_self", "self"),
                          ("todo_complete_scope_dialog_cancel", "cancel")):
            button = self.findChild(QPushButton, name)
            assert button is not None, name
        primary = self.findChild(QPushButton, "todo_complete_subtree")
        assert primary.isDefault()  # subtree is the main action
        clicked["text"] = primary.text()
        primary.click()
        return QDialog.Accepted

    monkeypatch.setattr(todo_page.QDialog, "exec", fake_exec)
    result = page._ask_complete_scope(5)
    assert result == "subtree"
    assert clicked["text"] == "完成整个任务树（5 项）"


# -- restore scope dialog -------------------------------------------------------


def _restore_reply(monkeypatch, scope):
    from retirement_pet.ui.panel import todo_page

    monkeypatch.setattr(
        todo_page.TodoPage, "_ask_restore_scope",
        lambda self, count: scope)


def test_restore_done_leaf_does_not_ask_scope(app, qt_application,
                                              monkeypatch):
    root, _child, grandchild, _survivor = _build_tree(app)
    app.todo.complete(grandchild.id)
    page = _todo_page(app)
    page.view_box.setCurrentIndex(1)  # 已完成
    page.refresh()
    _select_task(page, grandchild.id)
    qt_application.processEvents()

    calls = []
    from retirement_pet.ui.panel import todo_page

    monkeypatch.setattr(
        todo_page.TodoPage, "_ask_restore_scope",
        lambda self, count: calls.append(count) or "self")
    page.buttons["restore"].click()
    qt_application.processEvents()
    assert calls == []
    assert app.todo.get(grandchild.id).status.value == "open"


def test_restore_parent_subtree_reopens_all_done_descendants(
        app, qt_application, monkeypatch):
    root, child, grandchild, _survivor = _build_tree(app)
    app.todo.complete(root.id, scope="subtree")
    page = _todo_page(app)
    page.view_box.setCurrentIndex(1)  # 已完成
    page.refresh()
    _select_task(page, root.id)
    qt_application.processEvents()

    _restore_reply(monkeypatch, "subtree")
    page.buttons["restore"].click()
    qt_application.processEvents()

    for task in (root, child, grandchild):
        assert app.todo.get(task.id).status.value == "open"
    assert "已恢复整个子树（3 项）" in page.status.text()


def test_restore_parent_self_keeps_descendants_done(
        app, qt_application, monkeypatch):
    root, child, grandchild, _survivor = _build_tree(app)
    app.todo.complete(root.id, scope="subtree")
    page = _todo_page(app)
    page.view_box.setCurrentIndex(1)
    page.refresh()
    _select_task(page, root.id)
    qt_application.processEvents()

    _restore_reply(monkeypatch, "self")
    page.buttons["restore"].click()
    qt_application.processEvents()

    assert app.todo.get(root.id).status.value == "open"
    assert app.todo.get(child.id).status.value == "done"
    assert app.todo.get(grandchild.id).status.value == "done"


def test_restore_scope_dialog_defaults_to_self(app, qt_application,
                                               monkeypatch):
    from PySide6.QtWidgets import QDialog, QPushButton

    from retirement_pet.ui.panel import todo_page

    page = _todo_page(app)

    def fake_exec(self):
        assert self.objectName() == "todo_restore_scope_dialog"
        primary = self.findChild(QPushButton, "todo_restore_self")
        subtree = self.findChild(QPushButton, "todo_restore_subtree")
        assert primary is not None and subtree is not None
        assert primary.isDefault()  # default restores only the selection
        clicked["text"] = subtree.text()
        subtree.click()
        return QDialog.Accepted

    clicked = {}
    monkeypatch.setattr(todo_page.QDialog, "exec", fake_exec)
    result = page._ask_restore_scope(4)
    assert result == "subtree"
    assert clicked["text"] == "恢复整个子树（4 项）"


# -- keyboard + regression --------------------------------------------------------


def test_delete_key_still_archives_without_dialog(app, qt_application,
                                                  monkeypatch):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QMessageBox

    root, _child, _grandchild, _survivor = _build_tree(app)
    page = _todo_page(app)
    _select_task(page, root.id)
    qt_application.processEvents()

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *_a, **_k: asked.append(1) or QMessageBox.No))

    key = QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier)
    page.eventFilter(page.tree, key)
    qt_application.processEvents()

    assert asked == []  # archive path: no confirmation dialog
    archived = app.todo.get(root.id)
    assert archived.archived
    assert archived.status.value == "open"
