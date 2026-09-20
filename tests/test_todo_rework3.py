"""第三轮主控返工（CR-U01..U06）的 UI 链路测试。

隔离约定：全部使用 pytest 临时目录与合成任务，不触碰日用数据库；
测试内容不含任何个人任务或备注。
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
        instance_name=f"pytest-rework3-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


def _todo_page(app):
    app._open_control_panel("todo")
    return app._panel._built["todo"]


def _select_task(page, task_id):
    page.tree.setCurrentItem(page._items_by_id[task_id])


def _flush_deferred_deletes(qt_application) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_application.processEvents()


# -- CR-U02: a failed note read must never bind the previous task's text -----


def test_note_read_failure_never_binds_previous_task_content(
        app, qt_application, monkeypatch):
    from retirement_pet.todo import Horizon, TodoError
    from retirement_pet.todo.errors import TaskStoreUnavailable

    task_a = app.todo.add_task("甲任务", Horizon.SHORT)
    task_b = app.todo.add_task("乙任务", Horizon.SHORT)
    app.todo.set_note(task_a.id, "A original")
    app.todo.set_note(task_b.id, "B original")
    page = _todo_page(app)
    _select_task(page, task_a.id)
    qt_application.processEvents()
    assert page._note_loaded
    assert page.note_editor.toPlainText() == "A original"

    real_note_for = app.todo.note_for
    for failure in (TodoError("READ-GATE-DOMAIN"),
                    TaskStoreUnavailable("READ-GATE-STORE"),
                    RuntimeError("READ-GATE-UNKNOWN")):
        def broken_read(task_id, _failure=failure, _real=real_note_for):
            if task_id == task_b.id:
                raise _failure
            return _real(task_id)

        monkeypatch.setattr(app.todo, "note_for", broken_read)
        _select_task(page, task_b.id)
        qt_application.processEvents()

        # explicit failure state: nothing of A is bound to B
        assert page._note_task_id == task_b.id
        assert not page._note_loaded
        assert page.note_editor.toPlainText() == ""
        assert page.note_editor.isReadOnly()
        assert not page._note_save_button.isEnabled()

        # even a programmatic edit + flush attempt must not reach B
        page.note_editor.setPlainText("MUST-NOT-PERSIST")
        page._flush_note()
        qt_application.processEvents()
        assert real_note_for(task_b.id) == "B original"
        assert task_b.id not in app.todo_note_drafts

        # the fault heals without reselection, and A stayed intact
        monkeypatch.setattr(app.todo, "note_for", real_note_for)
        page.refresh()
        assert page._note_loaded
        assert page.note_editor.toPlainText() == "B original"
        assert real_note_for(task_a.id) == "A original"

        _select_task(page, task_a.id)
        qt_application.processEvents()


# -- CR-U03: kept drafts survive task switches AND a full panel close --------


def test_failed_draft_survives_task_switch_panel_close_and_reopen(
        app, qt_application, monkeypatch):
    from retirement_pet.todo import Horizon

    task_a = app.todo.add_task("草稿甲", Horizon.SHORT)
    task_b = app.todo.add_task("草稿乙", Horizon.SHORT)
    app.todo.set_note(task_a.id, "A persisted")
    page = _todo_page(app)
    _select_task(page, task_a.id)
    qt_application.processEvents()

    real_set_note = app.todo.set_note
    state = {"broken": True}

    def broken_set_note(task_id, note):
        if state["broken"] and task_id == task_a.id:
            raise RuntimeError("injected note write failure")
        return real_set_note(task_id, note)

    monkeypatch.setattr(app.todo, "set_note", broken_set_note)
    page.note_editor.setPlainText("A unsaved draft")
    qt_application.processEvents()
    _select_task(page, task_b.id)          # the autosave flush fails here
    qt_application.processEvents()
    assert app.todo.note_for(task_a.id) == "A persisted"   # write failed
    assert app.todo_note_drafts[task_a.id] == "A unsaved draft"

    # fault heals BEFORE the panel closes: close-flush persists the draft
    state["broken"] = False
    app._panel.close()
    _flush_deferred_deletes(qt_application)
    assert app.todo_note_drafts.get(task_a.id) is None
    assert app.todo_events == []          # page unsubscribed: no polling left

    app._open_control_panel("todo")
    reopened = app._panel._built["todo"]
    reopened.refresh()
    _select_task(reopened, task_a.id)
    qt_application.processEvents()
    assert reopened.note_editor.toPlainText() == "A unsaved draft"
    reopened._on_note_save_clicked()
    assert app.todo.note_for(task_a.id) == "A unsaved draft"


def test_draft_survives_close_while_fault_persists_and_offers_retry(
        app, qt_application, monkeypatch):
    """CR-U03 fault-still-active branch: the kept draft outlives the
    disposed page in the application-level store and is retried later."""
    from retirement_pet.todo import Horizon

    task = app.todo.add_task("故障草稿", Horizon.SHORT)
    app.todo.set_note(task.id, "old content")
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()

    real_set_note = app.todo.set_note

    def broken_set_note(task_id, _note):
        raise RuntimeError("injected note write failure")

    monkeypatch.setattr(app.todo, "set_note", broken_set_note)
    page.note_editor.setPlainText("still unsaved")
    qt_application.processEvents()
    app._panel.close()
    _flush_deferred_deletes(qt_application)
    # the close-flush failed too, but the draft is kept at app level
    assert app.todo_note_drafts[task.id] == "still unsaved"
    assert app.todo.note_for(task.id) == "old content"

    # reopen healed: the draft is re-offered and saves successfully
    app._open_control_panel("todo")
    reopened = app._panel._built["todo"]
    monkeypatch.setattr(app.todo, "set_note", real_set_note)
    reopened.refresh()
    _select_task(reopened, task.id)
    qt_application.processEvents()
    assert reopened.note_editor.toPlainText() == "still unsaved"
    assert reopened._note_dirty
    reopened._on_note_save_clicked()
    assert app.todo.note_for(task.id) == "still unsaved"
    assert app.todo_note_drafts == {}


# -- CR-U05: half-classified tasks stay reachable in 未分类 --------------------


def test_single_axis_tasks_stay_in_unclassified_and_quadrant_needs_both(
        app, qt_application):
    from retirement_pet.todo import Horizon, Level

    task = app.todo.add_task("单轴可见任务", Horizon.MEDIUM)
    page = _todo_page(app)
    open_index = 0
    unclassified_index = next(
        i for i in range(page.view_box.count())
        if page.view_box.itemText(i).startswith("未分类"))
    quad_index_by = {}
    for i in range(page.view_box.count()):
        text = page.view_box.itemText(i)
        for label, key in (("重要且紧急", (1, 1)), ("重要不紧急", (1, 2)),
                           ("紧急不重要", (2, 1)), ("不紧急不重要", (2, 2))):
            if text.startswith(label):
                quad_index_by[key] = i
    assert len(quad_index_by) == 4
    expected_level = {0: None, 1: Level.HIGH, 2: Level.LOW}

    for importance_clicks in range(3):
        for urgency_clicks in range(3):
            app.todo.set_importance(task.id, None)
            app.todo.set_urgency(task.id, None)
            page.view_box.setCurrentIndex(open_index)
            page.refresh()
            _select_task(page, task.id)
            qt_application.processEvents()
            for _ in range(importance_clicks):
                page.buttons["importance"].click()
                qt_application.processEvents()
            for _ in range(urgency_clicks):
                page.buttons["urgency"].click()
                qt_application.processEvents()

            current = app.todo.get(task.id)
            assert current.importance is expected_level[importance_clicks]
            assert current.urgency is expected_level[urgency_clicks]

            classified = importance_clicks > 0 and urgency_clicks > 0
            page.view_box.setCurrentIndex(unclassified_index)
            page.refresh()
            in_unclassified = task.id in page._items_by_id
            assert in_unclassified is not classified, (
                importance_clicks, urgency_clicks)
            # a fully classified task sits in exactly ITS quadrant; a
            # half- or un-classified one sits in NO quadrant at all
            expected_quad = quad_index_by.get(
                (importance_clicks, urgency_clicks))
            for key, quad_index in quad_index_by.items():
                page.view_box.setCurrentIndex(quad_index)
                page.refresh()
                present = task.id in page._items_by_id
                if classified and key == (importance_clicks,
                                          urgency_clicks):
                    assert present
                else:
                    assert not present, (importance_clicks, urgency_clicks,
                                         key)
            assert expected_quad is not None or not classified


def test_cycling_an_axis_back_to_none_reports_unclassified(
        app, qt_application):
    """CR-U05: the cleared feedback must say 未分类, never keep the
    previous 高/低 success wording."""
    from retirement_pet.todo import Horizon

    task = app.todo.add_task("清除反馈任务", Horizon.SHORT)
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()
    page.buttons["importance"].click()    # None -> high
    qt_application.processEvents()
    assert page.status.text() == "重要：高"
    page.buttons["importance"].click()    # high -> low
    qt_application.processEvents()
    assert page.status.text() == "重要：低"
    page.buttons["importance"].click()    # low -> None
    qt_application.processEvents()
    assert page.status.text() == "重要已清除（未分类）"


# -- CR-U04: the backup/restore entry and post-restore UI consistency ---------


def test_backup_dialog_creates_lists_and_restores_with_full_resync(
        app, qt_application, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QPushButton
    from retirement_pet.todo import Horizon

    task = app.todo.add_task("备份恢复任务", Horizon.SHORT)
    app.todo.set_note(task.id, "snapshot old note")
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()

    page._open_backup_dialog()
    dialog = page._backup_dialog
    assert dialog is not None
    create_button = dialog.findChild(QPushButton, "todo_backup_create")
    restore_button = dialog.findChild(QPushButton, "todo_backup_restore")
    listing = dialog.findChild(type(page._backup_list), "todo_backup_list")
    assert create_button is not None and restore_button.isEnabled()
    create_button.click()
    qt_application.processEvents()
    assert listing.count() == 1
    assert "已创建备份" in page._backup_status.text()

    # pre-restore state: a SAVED newer note plus an UNSAVED stale draft.
    # Deselect first so reselecting really reopens (and reloads) the note.
    app.todo.set_note(task.id, "newer note")
    page.refresh()
    page.tree.setCurrentItem(None)
    qt_application.processEvents()
    _select_task(page, task.id)
    qt_application.processEvents()
    assert page.note_editor.toPlainText() == "newer note"
    page.note_editor.setPlainText("unsaved stale draft")
    qt_application.processEvents()
    assert app.todo_note_drafts[task.id] == "unsaved stale draft"

    prompts = []

    def fake_question(_parent, _title, text):
        from PySide6.QtWidgets import QMessageBox
        prompts.append(text)
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", fake_question)
    listing.setCurrentRow(0)
    restore_button.click()
    qt_application.processEvents()

    # the restore replaced the whole store from the backup
    assert prompts and "未保存的备注草稿" in prompts[-1]
    assert "pre-restore" in prompts[-1]
    assert app.todo.note_for(task.id) == "snapshot old note"
    assert app.todo_note_drafts == {}               # drafts dropped first
    assert "恢复完成" in page._backup_status.text()
    # the editor resynced from the NEW store instead of keeping stale text
    assert page.note_editor.toPlainText() == "snapshot old note"
    assert page._note_loaded and not page._note_dirty
    # tree and selection resynced: the surviving task is present again
    page.refresh()
    assert task.id in page._items_by_id


def test_restore_reports_partial_success_and_self_heals_on_second_restore(
        app, qt_application, monkeypatch):
    """The disk-restored-but-reopen-failed outcome is reported as such, the
    degraded page gates every write entry, and restoring again repairs."""
    from PySide6.QtWidgets import QMessageBox
    from retirement_pet.todo import Horizon
    from retirement_pet.todo import service as todo_service_module

    task = app.todo.add_task("自修复任务", Horizon.SHORT)
    app.todo.set_note(task.id, "restored content")
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()

    page._open_backup_dialog()
    dialog = page._backup_dialog
    from PySide6.QtWidgets import QPushButton
    create_button = dialog.findChild(QPushButton, "todo_backup_create")
    restore_button = dialog.findChild(QPushButton, "todo_backup_restore")
    listing = dialog.findChild(type(page._backup_list), "todo_backup_list")
    create_button.click()
    qt_application.processEvents()
    assert listing.count() == 1

    real_repo_cls = todo_service_module.TaskRepository

    class FlakyRepo(real_repo_cls):
        def __init__(self, path, *args, **kwargs):
            if inject["fail"]:
                inject["fail"] = False
                raise RuntimeError("injected reopen failure")
            super().__init__(path, *args, **kwargs)

    inject = {"fail": True}
    monkeypatch.setattr(todo_service_module, "TaskRepository", FlakyRepo)
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *_a, **_k: QMessageBox.Yes))
    listing.setCurrentRow(0)
    restore_button.click()
    qt_application.processEvents()

    # the data WAS restored on disk, but the fresh open failed
    assert "已写回磁盘" in page._backup_status.text()
    assert app.todo.degraded
    assert not page._writable_now()
    assert not page.input.isEnabled()
    assert page.note_editor.isReadOnly()
    assert not page._note_save_button.isEnabled()
    for button in page.buttons.values():
        assert not button.isEnabled()
    # the self-repair entry itself stays reachable
    assert page._backup_button.isEnabled()

    # a second restore through the same dialog repairs the module
    inject["fail"] = False
    page._reload_backup_list()
    listing.setCurrentRow(0)
    restore_button.click()
    qt_application.processEvents()
    assert "恢复完成" in page._backup_status.text()
    assert not app.todo.degraded
    assert page._writable_now()
    assert page.input.isEnabled()
    assert app.todo.note_for(task.id) == "restored content"


def test_restore_when_current_task_vanishes_keeps_page_consistent(
        app, qt_application, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from retirement_pet.todo import Horizon

    keeper = app.todo.add_task("备份里存在", Horizon.SHORT)
    app.todo.set_note(keeper.id, "keeper note")
    page = _todo_page(app)

    page._open_backup_dialog()
    from PySide6.QtWidgets import QPushButton
    dialog = page._backup_dialog
    create_button = dialog.findChild(QPushButton, "todo_backup_create")
    restore_button = dialog.findChild(QPushButton, "todo_backup_restore")
    listing = dialog.findChild(type(page._backup_list), "todo_backup_list")
    create_button.click()
    qt_application.processEvents()

    born_after_backup = app.todo.add_task("备份后新增", Horizon.SHORT)
    page.refresh()
    _select_task(page, born_after_backup.id)
    qt_application.processEvents()
    page.note_editor.setPlainText("draft about a task that will vanish")
    qt_application.processEvents()

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *_a, **_k: QMessageBox.Yes))
    listing.setCurrentRow(0)
    restore_button.click()
    qt_application.processEvents()

    # the vanished task is gone from store and tree, nothing crashed
    assert app.todo.get(born_after_backup.id) is None
    page.refresh()
    assert born_after_backup.id not in page._items_by_id
    assert keeper.id in page._items_by_id
    # the editor never turns the vanished draft into a phantom write
    assert app.todo_note_drafts == {}
    assert not page._note_loaded
    assert page.note_editor.toPlainText() == ""

    # selecting a surviving task works normally again
    _select_task(page, keeper.id)
    qt_application.processEvents()
    assert page.note_editor.toPlainText() == "keeper note"
    page.note_editor.setPlainText("keeper note + new line")
    qt_application.processEvents()
    page._on_note_save_clicked()
    assert app.todo.note_for(keeper.id) == "keeper note + new line"


# -- CR-U07: a full draft store never evicts unpersisted content --------------


def _fill_drafts(page, qt_application, tasks, count):
    for i, task in enumerate(tasks[:count]):
        _select_task(page, task.id)
        qt_application.processEvents()
        page.note_editor.setPlainText(f"草稿{i:02d}")
        qt_application.processEvents()


def test_full_draft_store_keeps_existing_and_refuses_new_with_feedback(
        app, qt_application, monkeypatch):
    """CR-U07: the 17th draft is refused with visible feedback; the 16
    unpersisted drafts all survive intact."""
    from retirement_pet.todo import Horizon

    tasks = [app.todo.add_task(f"任务{i:02d}", Horizon.SHORT)
             for i in range(17)]
    page = _todo_page(app)

    def broken_set_note(_task_id, _note):
        raise RuntimeError("injected note write failure")

    monkeypatch.setattr(app.todo, "set_note", broken_set_note)
    _fill_drafts(page, qt_application, tasks, 16)
    assert len(app.todo_note_drafts) == 16
    assert app.todo_note_drafts[tasks[15].id] == "草稿15"

    _select_task(page, tasks[16].id)
    qt_application.processEvents()
    page.note_editor.setPlainText("草稿16")
    qt_application.processEvents()

    # refused, not admitted by evicting the oldest draft
    assert len(app.todo_note_drafts) == 16
    assert app.todo_note_drafts[tasks[0].id] == "草稿00"
    assert tasks[16].id not in app.todo_note_drafts
    assert "已满" in page.status.text()

    # switching away reports honestly that the refused live content is
    # NOT kept, and the first draft is re-offered unchanged
    _select_task(page, tasks[0].id)
    qt_application.processEvents()
    assert "未被自动保留" in page.status.text()
    assert page.note_editor.toPlainText() == "草稿00"


def test_draft_limit_heals_close_flush_recovers_live_and_recycles(
        app, qt_application, monkeypatch):
    """CR-U07: once the fault heals, closing the panel persists all 16
    kept drafts AND the refused live editor, recycling the store."""
    from retirement_pet.todo import Horizon

    tasks = [app.todo.add_task(f"任务{i:02d}", Horizon.SHORT)
             for i in range(17)]
    page = _todo_page(app)
    real_set_note = app.todo.set_note

    def broken_set_note(_task_id, _note):
        raise RuntimeError("injected note write failure")

    monkeypatch.setattr(app.todo, "set_note", broken_set_note)
    _fill_drafts(page, qt_application, tasks, 16)
    _select_task(page, tasks[16].id)
    qt_application.processEvents()
    page.note_editor.setPlainText("草稿16-live")
    qt_application.processEvents()
    assert len(app.todo_note_drafts) == 16
    assert tasks[16].id not in app.todo_note_drafts

    monkeypatch.setattr(app.todo, "set_note", real_set_note)
    app._panel.close()
    _flush_deferred_deletes(qt_application)

    assert app.todo_note_drafts == {}
    for i, task in enumerate(tasks[:16]):
        assert app.todo.note_for(task.id) == f"草稿{i:02d}"
    assert app.todo.note_for(tasks[16].id) == "草稿16-live"


# -- CR-U08: an unknown restore publish result is never "unchanged" -----------


def test_restore_publish_unknown_reports_replaced_store_and_self_heals(
        app, qt_application, monkeypatch):
    """CR-U08 through the real restore button: a post-replace durability
    failure must not claim the current store is unchanged; the listing
    reflects the reopened store and restoring the pre-restore backup
    repairs the original state."""
    from pathlib import Path

    from PySide6.QtWidgets import QMessageBox, QPushButton
    from retirement_pet.todo import Horizon
    from retirement_pet.todo import backup as backup_module

    first = app.todo.add_task("备份里的任务", Horizon.SHORT)
    page = _todo_page(app)
    page._open_backup_dialog()
    dialog = page._backup_dialog
    create_button = dialog.findChild(QPushButton, "todo_backup_create")
    restore_button = dialog.findChild(QPushButton, "todo_backup_restore")
    listing = dialog.findChild(type(page._backup_list), "todo_backup_list")
    create_button.click()
    qt_application.processEvents()

    # the live store now holds one task MORE than the backup
    second = app.todo.add_task("备份之后新增", Horizon.SHORT)
    qt_application.processEvents()

    real_fsync_file = backup_module._fsync_file
    live_store = Path(app.todo._store_path).resolve()
    flaky = {"on": True}

    def flaky_fsync_file(target):
        if flaky["on"] and Path(target).resolve() == live_store:
            raise OSError("injected post-replace fsync failure")
        return real_fsync_file(target)

    def always_yes(_parent, _title, _text):
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", always_yes)
    monkeypatch.setattr(backup_module, "_fsync_file", flaky_fsync_file)
    listing.setCurrentRow(0)
    restore_button.click()
    qt_application.processEvents()
    # the durability fault heals for everything after the first restore
    flaky["on"] = False

    # the unknown publish result is reported as such: no false claim
    status = page._backup_status.text()
    assert "已替换磁盘数据" in status
    assert "未改动" not in status
    assert "pre-restore" in status
    # the page reflects the reopened store: the extra task is gone
    page.refresh()
    assert first.id in page._items_by_id
    assert second.id not in page._items_by_id
    assert app.todo.get(second.id) is None
    # both the manual and the pre-restore backup are listed again
    assert listing.count() >= 2

    # the documented self-repair path: restore the pre-restore backup
    pre_row = next(listing.item(i)
                   for i in range(listing.count())
                   if "pre-restore" in listing.item(i).text())
    listing.setCurrentItem(pre_row)
    restore_button.click()
    qt_application.processEvents()
    assert "恢复完成" in page._backup_status.text()
    page.refresh()
    assert first.id in page._items_by_id
    assert second.id in page._items_by_id


def test_restore_backup_unusable_still_reports_unchanged_store(
        app, qt_application, monkeypatch):
    """CR-U08 keeps the honest branch too: a pre-replace failure has
    really left the current store untouched."""
    from PySide6.QtWidgets import QMessageBox, QPushButton
    from retirement_pet.todo import Horizon
    from retirement_pet.todo import backup as backup_module

    task = app.todo.add_task("当前库任务", Horizon.SHORT)
    page = _todo_page(app)
    page._open_backup_dialog()
    dialog = page._backup_dialog
    create_button = dialog.findChild(QPushButton, "todo_backup_create")
    restore_button = dialog.findChild(QPushButton, "todo_backup_restore")
    listing = dialog.findChild(type(page._backup_list), "todo_backup_list")
    create_button.click()
    qt_application.processEvents()
    assert listing.count() >= 1

    def broken_verify(_candidate):
        raise backup_module.BackupError("backup_content_mismatch")

    def always_yes(_parent, _title, _text):
        return QMessageBox.Yes

    monkeypatch.setattr(backup_module, "_verify_snapshot", broken_verify)
    monkeypatch.setattr(QMessageBox, "question", always_yes)
    listing.setCurrentRow(0)
    restore_button.click()
    qt_application.processEvents()

    assert "未改动" in page._backup_status.text()
    assert task.id in page._items_by_id
    assert app.todo.get(task.id) is not None
