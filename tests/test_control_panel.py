"""M6 control panel: lazy pages, single instance, IPC, no pet-surface drift."""

from __future__ import annotations

import json
import time

import pytest


def _never_returning_import_process(pack_path, send_connection) -> None:
    """Spawn-pickleable canary for bounded cancellation/timeout tests."""
    from pathlib import Path

    marker = Path(f"{pack_path}.entered")
    marker.write_text("entered", encoding="ascii")
    try:
        while True:
            time.sleep(1.0)
    finally:
        send_connection.close()


def _snapshot_then_replace_import_process(pack_path, send_connection) -> None:
    """Prove the exact child snapshot survives a source-path replacement."""
    import os
    from dataclasses import replace
    from pathlib import Path

    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from retirement_pet.petpack import local_import

    qt_app = QApplication.instance() or QApplication([
        "RetirementPet-snapshot-test", "-platform", "offscreen",
    ])
    preview = local_import.inspect_local_pack(Path(pack_path))
    Path(pack_path).write_bytes(b"replacement after immutable snapshot")
    local_import.validate_local_pack_first_frames(preview)
    preview = replace(preview, first_frames_verified=True)
    send_connection.send_bytes(local_import._success_frame(preview))
    for offset in range(
            0, len(preview.payload), local_import.ISOLATED_IMPORT_CHUNK_BYTES):
        send_connection.send_bytes(preview.payload[
            offset:offset + local_import.ISOLATED_IMPORT_CHUNK_BYTES])
    send_connection.close()


def _flush_deferred_deletes(qt_application) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_application.processEvents()


def _wait_until(qt_application, predicate, timeout_s=8.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qt_application.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    qt_application.processEvents()
    assert predicate(), "timed out waiting for Qt background operation"


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
        instance_name=f"pytest-panel-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


def test_panel_opens_with_lazy_pages(app, qt_application):
    app._open_control_panel("overview")
    panel = app._panel
    assert panel.isVisible()
    # only the visited page was built
    assert panel.built_page_ids() == ["overview"]


def test_visiting_pages_builds_them_on_demand(app, qt_application):
    app._open_control_panel("characters")
    panel = app._panel
    assert panel.built_page_ids() == ["characters"]
    assert panel.open_page("about")
    assert set(panel.built_page_ids()) == {"characters", "about"}
    # pages never visited were never constructed
    assert "todo" not in panel.built_page_ids()


def test_single_instance_reuses_panel(app, qt_application):
    app._open_control_panel("overview")
    first = app._panel
    app._open_control_panel("about")
    assert app._panel is first  # reused, not recreated
    assert "about" in first.built_page_ids()


def test_unknown_page_falls_back_gracefully(app, qt_application):
    app._open_control_panel("no_such_page")
    panel = app._panel
    assert panel is not None
    assert panel.built_page_ids() == ["overview"]
    assert panel._stack.currentWidget() is panel._built["overview"]
    assert panel._nav.currentItem().text() == "概览"


def test_ipc_command_routes_to_panel_page(app, qt_application):
    app._guard.on_panel_requested("todo")
    panel = app._panel
    assert panel is not None and panel.isVisible()
    assert "todo" in panel.built_page_ids()


def test_display_page_only_allows_matrix_combinations(app, qt_application):
    """standard + dnd is forbidden by the engine matrix; the UI must not
    present it as selectable (ADR-V2-022)."""
    from retirement_pet.layout_policy import ALLOWED, LayoutId

    spec = ALLOWED[LayoutId.STANDARD]
    assert "dnd" not in {p.value for p in spec.allowed_policies}


def test_alpha_display_previews_are_explicitly_disabled(app, qt_application):
    from PySide6.QtWidgets import QComboBox, QLabel

    app._open_control_panel("display")
    page = app._panel._built["display"]
    layout = page.findChild(QComboBox, "layout_preview")
    visibility = page.findChild(QComboBox, "visibility_preview")
    note = page.findChild(QLabel, "layout_preview_note")
    assert layout is not None and not layout.isEnabled()
    assert visibility is not None and not visibility.isEnabled()
    assert "不会保存或应用" in note.text()


def test_random_action_checkbox_is_persistent_and_live(app, qt_application):
    import json
    from PySide6.QtWidgets import QCheckBox

    app._open_control_panel("actions")
    page = app._panel._built["actions"]
    checkbox = page.findChild(QCheckBox, "random_actions_enabled")
    assert checkbox.isChecked() is True
    assert app.random_scheduler._enabled() is True

    checkbox.setChecked(False)
    qt_application.processEvents()

    assert app.settings.get("random_actions_enabled") is False
    assert app.random_scheduler._enabled() is False
    persisted = json.loads(app.settings.path.read_text(encoding="utf-8"))
    assert persisted["random_actions_enabled"] is False


def test_partial_schedule_apply_preserves_not_on_top(app, qt_application):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton

    app._set_always_on_top(False)
    assert app.window._always_on_top is False
    app._open_control_panel("sound_schedule")
    page = app._panel._built["sound_schedule"]
    apply_button = page.findChild(QPushButton, "schedule_apply")
    QTest.mouseClick(apply_button, Qt.LeftButton)

    assert app.settings.get("always_on_top") is False
    assert app.window._always_on_top is False


def test_schedule_rejects_invalid_time_without_overwriting(
        app, qt_application):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton

    before = app.settings.get("meal_times")
    app._open_control_panel("sound_schedule")
    page = app._panel._built["sound_schedule"]
    page.findChild(QLineEdit, "schedule_meals").setText("99:99")
    QTest.mouseClick(
        page.findChild(QPushButton, "schedule_apply"), Qt.LeftButton)

    assert app.settings.get("meal_times") == before
    assert "格式无效" in page.findChild(QLabel, "schedule_status").text()


def test_schedule_reports_save_failure_and_rolls_back(
        app, qt_application, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton

    before = app.settings.as_dict()
    app._open_control_panel("sound_schedule")
    page = app._panel._built["sound_schedule"]
    page.findChild(QLineEdit, "schedule_meals").setText("07:30")
    monkeypatch.setattr(app.settings, "save", lambda: False)
    QTest.mouseClick(
        page.findChild(QPushButton, "schedule_apply"), Qt.LeftButton)

    assert app.settings.as_dict() == before
    assert "保存失败" in page.findChild(QLabel, "schedule_status").text()


def test_pet_surface_contract_unchanged_by_panel(app, qt_application):
    """Opening/closing the panel never touches the pet window contract."""
    from PySide6.QtCore import Qt

    app._show_window()
    flags_before = app.window.windowFlags()
    transparent_before = bool(
        app.window.testAttribute(Qt.WA_TranslucentBackground))

    app._open_control_panel("overview")
    panel = app._panel
    assert not bool(panel.windowFlags() & Qt.FramelessWindowHint)  # standard
    panel.accept()
    app._open_control_panel("display")
    app._panel.accept()

    assert app.window.windowFlags() == flags_before
    assert bool(app.window.testAttribute(Qt.WA_TranslucentBackground)) is \
        transparent_before
    app._hide_window()


def test_characters_page_switches_active_character(app, qt_application):
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    app.library.install(root / "tests/fixtures/petpack/minimal-static.petpack")
    app._open_control_panel("characters")

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QListWidget, QPushButton

    page = app._panel._built["characters"]
    character_list = page.findChild(QListWidget, "character_list")
    button = page.findChild(QPushButton, "character_switch")
    demo_row = next(
        row for row in range(character_list.count())
        if "← 当前" not in character_list.item(row).text())
    character_list.setCurrentRow(demo_row)
    assert button.isEnabled()
    QTest.mouseClick(button, Qt.LeftButton)

    active = app._selection_store.get("active")
    assert active.character_fqid.endswith("demo")
    assert sum("← 当前" in character_list.item(row).text()
               for row in range(character_list.count())) == 1
    assert "切换成功" in page.findChild(QLabel, "character_status").text()
    assert not button.isEnabled()


def test_characters_single_builtin_is_selected_and_honestly_disabled(
        app, qt_application):
    from PySide6.QtWidgets import QLabel, QListWidget, QPushButton

    app._open_control_panel("characters")
    page = app._panel._built["characters"]
    character_list = page.findChild(QListWidget, "character_list")
    button = page.findChild(QPushButton, "character_switch")
    status = page.findChild(QLabel, "character_status")
    assert character_list.count() == 1
    assert "← 当前" in character_list.currentItem().text()
    assert not button.isEnabled()
    assert "当前只有一个角色" in status.text()


def test_pending_character_onboarding_group_has_unique_named_controls(
        app, qt_application):
    from PySide6.QtWidgets import QGroupBox, QPushButton

    assert app._character_onboarding_pending()
    app._open_control_panel("characters")
    page = app._panel._built["characters"]

    groups = page.findChildren(QGroupBox, "character_onboarding")
    official_buttons = page.findChildren(
        QPushButton, "character_onboarding_keep_official")
    preview_buttons = page.findChildren(
        QPushButton, "character_onboarding_enable_preview")

    assert len(groups) == 1
    assert len(official_buttons) == 1
    assert len(preview_buttons) == 1
    assert groups[0].isVisible()


def test_closing_character_onboarding_does_not_record_a_choice(
        app, qt_application):
    from retirement_pet.app import CHARACTER_ONBOARDING_SETTING

    marker_before = app.settings.get(CHARACTER_ONBOARDING_SETTING, "")
    settings_bytes_before = (
        app.settings.path.read_bytes() if app.settings.path.is_file() else None)
    assert marker_before == ""

    app._open_control_panel("characters")
    panel = app._panel
    assert panel is not None
    panel.accept()
    _flush_deferred_deletes(qt_application)

    assert app._panel is None
    assert app.settings.get(CHARACTER_ONBOARDING_SETTING, "") == marker_before
    settings_bytes_after = (
        app.settings.path.read_bytes() if app.settings.path.is_file() else None)
    assert settings_bytes_after == settings_bytes_before
    assert app._character_onboarding_pending()


def test_character_onboarding_official_choice_persists_and_hides_group(
        app, qt_application):
    import json

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QGroupBox, QPushButton
    from retirement_pet.app import (
        CHARACTER_ONBOARDING_CAMPAIGN,
        CHARACTER_ONBOARDING_SETTING,
    )

    app._open_control_panel("characters")
    page = app._panel._built["characters"]
    group = page.findChild(QGroupBox, "character_onboarding")
    button = page.findChild(
        QPushButton, "character_onboarding_keep_official")

    QTest.mouseClick(button, Qt.LeftButton)
    qt_application.processEvents()

    assert app.settings.get(CHARACTER_ONBOARDING_SETTING) == \
        CHARACTER_ONBOARDING_CAMPAIGN
    persisted = json.loads(app.settings.path.read_text(encoding="utf-8"))
    assert persisted[CHARACTER_ONBOARDING_SETTING] == \
        CHARACTER_ONBOARDING_CAMPAIGN
    assert group.isHidden()
    assert not app._character_onboarding_pending()


def test_character_onboarding_preview_uses_worker_and_one_click_activation(
        app, qt_application, monkeypatch):
    from PySide6.QtCore import QObject, Signal, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QGroupBox, QPushButton
    from retirement_pet.ui.panel import pages

    preview = object()
    worker_paths = []
    activation_calls = []

    class SuccessfulWorker(QObject):
        succeeded = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, path, parent=None):
            super().__init__(parent)
            worker_paths.append(path)

        def start(self):
            self.succeeded.emit(preview)
            self.finished.emit()

    def activate(candidate):
        activation_calls.append(candidate)
        assert app._complete_character_onboarding()
        return True, "已启用半写实退休猫"

    expected_path = app._bundled_character_preview_path()
    assert expected_path.is_file()
    monkeypatch.setattr(pages, "_LocalImportWorker", SuccessfulWorker)
    monkeypatch.setattr(
        pages, "_confirm_local_pack",
        lambda *_args: pytest.fail("onboarding must not enter manual confirm flow"))
    monkeypatch.setattr(app, "_activate_bundled_character_preview", activate)
    app._open_control_panel("characters")
    page = app._panel._built["characters"]
    group = page.findChild(QGroupBox, "character_onboarding")
    button = page.findChild(
        QPushButton, "character_onboarding_enable_preview")

    QTest.mouseClick(button, Qt.LeftButton)
    qt_application.processEvents()

    assert worker_paths == [expected_path]
    assert activation_calls == [preview]
    assert page._import_worker_state["worker"] is None
    assert group.isHidden()
    assert not app._character_onboarding_pending()


def test_character_onboarding_preview_failure_remains_pending_and_retryable(
        app, qt_application, monkeypatch):
    from PySide6.QtCore import QObject, Signal, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton
    from retirement_pet.ui.panel import pages

    worker_paths = []
    activation_calls = []

    class FailingWorker(QObject):
        succeeded = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, path, parent=None):
            super().__init__(parent)
            worker_paths.append(path)

        def start(self):
            self.failed.emit("测试预检失败")
            self.finished.emit()

    monkeypatch.setattr(pages, "_LocalImportWorker", FailingWorker)
    monkeypatch.setattr(
        app, "_activate_bundled_character_preview",
        lambda preview: activation_calls.append(preview))
    app._open_control_panel("characters")
    page = app._panel._built["characters"]
    group = page.findChild(QGroupBox, "character_onboarding")
    status = page.findChild(QLabel, "character_onboarding_status")
    button = page.findChild(
        QPushButton, "character_onboarding_enable_preview")

    QTest.mouseClick(button, Qt.LeftButton)
    qt_application.processEvents()

    assert len(worker_paths) == 1
    assert activation_calls == []
    assert button.isEnabled()
    assert group.isVisible()
    assert "可以重试" in status.text()
    assert app._character_onboarding_pending()

    QTest.mouseClick(button, Qt.LeftButton)
    qt_application.processEvents()
    assert len(worker_paths) == 2
    assert activation_calls == []
    assert button.isEnabled()
    assert group.isVisible()
    assert app._character_onboarding_pending()


def test_character_page_imports_then_explicitly_switches_realistic_pack(
        app, qt_application, monkeypatch):
    from pathlib import Path

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QListWidget, QPushButton
    from retirement_pet.ui.panel import pages

    pack = (Path(__file__).resolve().parent.parent / "assets" / "petpack" /
            "examples" / "realistic-retirement-cat-0.1.1.petpack")
    monkeypatch.setattr(
        pages.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(pack), "PetPack")),
    )
    monkeypatch.setattr(pages, "_confirm_local_pack", lambda *_args: True)
    app._open_control_panel("characters")
    page = app._panel._built["characters"]
    character_list = page.findChild(QListWidget, "character_list")
    import_button = page.findChild(QPushButton, "character_import")
    switch_button = page.findChild(QPushButton, "character_switch")
    status = page.findChild(QLabel, "character_status")

    QTest.mouseClick(import_button, Qt.LeftButton)

    _wait_until(qt_application, lambda: character_list.count() == 2)

    assert character_list.count() == 2
    assert "导入成功" in status.text()
    assert app._selection_store.get("active").character_fqid.endswith(".cat")

    realistic_row = next(
        row for row in range(character_list.count())
        if "半写实退休猫" in character_list.item(row).text())
    assert "本地内容 · 发布者/权利未验证" in \
        character_list.item(realistic_row).text()
    character_list.setCurrentRow(realistic_row)
    assert switch_button.isEnabled()
    QTest.mouseClick(switch_button, Qt.LeftButton)

    active = app._selection_store.get("active")
    assert active.character_fqid.endswith(".realistic-cat")
    assert "切换成功" in status.text()


def test_character_page_isolated_import_rejects_malformed_pack_without_mutation(
        app, qt_application, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QPushButton
    from retirement_pet.ui.panel import pages

    selected = tmp_path / "broken.petpack"
    selected.write_bytes(b"not a zip")
    revisions_before = app.library.list_revisions()
    receipts_before = set(app.library.receipts_dir.glob("*.json"))
    monkeypatch.setattr(
        pages.QFileDialog, "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(selected), "PetPack")))
    app._open_control_panel("characters")
    page = app._panel._built["characters"]
    status = page.findChild(QLabel, "character_status")

    QTest.mouseClick(
        page.findChild(QPushButton, "character_import"), Qt.LeftButton)
    _wait_until(qt_application, lambda: "导入失败" in status.text())

    assert app.library.list_revisions() == revisions_before
    assert set(app.library.receipts_dir.glob("*.json")) == receipts_before


def test_failed_character_import_keeps_active_and_catalog(
        app, qt_application, tmp_path):
    active_before = app._selection_store.get("active")
    entries_before = app.catalog.entries()
    broken = tmp_path / "broken.petpack"
    broken.write_bytes(b"not a zip")

    ok, message = app._import_character_pack(broken)

    assert not ok
    assert "导入失败" in message
    assert app._selection_store.get("active") == active_before
    assert app.catalog.entries() == entries_before


def test_character_import_uses_the_exact_preflight_snapshot_when_source_changes(
        app, qt_application, tmp_path):
    from pathlib import Path

    from retirement_pet.petpack import local_import

    canonical = (Path(__file__).resolve().parent.parent / "assets" /
                 "petpack" / "examples" /
                 "realistic-retirement-cat-0.1.1.petpack")
    selected = tmp_path / "selected.petpack"
    original = canonical.read_bytes()
    selected.write_bytes(original)
    expected_preview = local_import.inspect_local_pack(canonical)
    receipts_before = set(app.library.receipts_dir.glob("*.json"))

    preview = local_import.preflight_local_pack(
        selected, _target=_snapshot_then_replace_import_process)
    assert selected.read_bytes() == b"replacement after immutable snapshot"
    ok, message = app._import_preflighted_character_pack(preview)

    assert ok, message
    record = app.library.get_revision(expected_preview.revision_key)
    assert record is not None
    assert record.pack_path.read_bytes() == original
    receipts_after = set(app.library.receipts_dir.glob("*.json"))
    new_receipts = receipts_after - receipts_before
    assert len(new_receipts) == 1
    receipt = json.loads(new_receipts.pop().read_text(encoding="utf-8"))
    assert receipt["archive_sha256"] == expected_preview.archive_sha256
    assert receipt["trust_channel"] == "LOCAL_IMPORTED"
    assert receipt["publisher_verification_status"] == "UNVERIFIED"


def test_closing_panel_during_nonreturning_import_is_bounded_and_kills_child(
        app, qt_application, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton
    from retirement_pet.ui.panel import pages

    selected = tmp_path / "slow.petpack"
    selected.write_bytes(b"placeholder")
    entered = tmp_path / "slow.petpack.entered"
    real_worker = pages._LocalImportWorker
    monkeypatch.setattr(
        pages,
        "_LocalImportWorker",
        lambda path, parent: real_worker(
            path, parent,
            _target=_never_returning_import_process,
            _timeout_seconds=30.0,
        ),
    )
    monkeypatch.setattr(
        pages.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(selected), "PetPack")),
    )
    app._open_control_panel("characters")
    panel = app._panel
    page = panel._built["characters"]
    QTest.mouseClick(
        page.findChild(QPushButton, "character_import"), Qt.LeftButton)
    _wait_until(qt_application, entered.is_file)
    worker = page._import_worker_state["worker"]

    started = time.monotonic()
    panel.accept()
    elapsed = time.monotonic() - started

    assert worker.isFinished()
    assert not worker.has_live_process()
    assert elapsed < 1.5
    assert app._panel is None


def test_nonreturning_import_times_out_and_releases_child(
        app, qt_application, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QPushButton
    from retirement_pet.ui.panel import pages

    selected = tmp_path / "timeout.petpack"
    selected.write_bytes(b"placeholder")
    entered = tmp_path / "timeout.petpack.entered"
    real_worker = pages._LocalImportWorker
    monkeypatch.setattr(
        pages,
        "_LocalImportWorker",
        lambda path, parent: real_worker(
            path, parent,
            _target=_never_returning_import_process,
            _timeout_seconds=0.5,
        ),
    )
    monkeypatch.setattr(
        pages.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(selected), "PetPack")),
    )
    app._open_control_panel("characters")
    page = app._panel._built["characters"]
    QTest.mouseClick(
        page.findChild(QPushButton, "character_import"), Qt.LeftButton)
    worker = page._import_worker_state["worker"]

    _wait_until(qt_application, entered.is_file)
    status = page.findChild(QLabel, "character_status")
    _wait_until(qt_application, lambda: "检查超时" in status.text())

    assert worker.isFinished()
    assert not worker.has_live_process()
    assert page._import_worker_state["worker"] is None


def test_quit_inside_file_dialog_cannot_start_import_worker_or_install(
        app, qt_application, monkeypatch):
    from pathlib import Path

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton
    from retirement_pet.ui.panel import pages

    pack = (Path(__file__).resolve().parent.parent / "assets" / "petpack" /
            "examples" / "realistic-retirement-cat-0.1.1.petpack")
    receipts_before = set(app.library.receipts_dir.glob("*.json"))
    workers_constructed = []

    def quit_then_return_selection(*_args, **_kwargs):
        app.shutdown()
        return str(pack), "PetPack"

    def forbidden_worker(*_args, **_kwargs):
        workers_constructed.append(1)
        raise AssertionError("worker created after application shutdown")

    monkeypatch.setattr(
        pages.QFileDialog, "getOpenFileName",
        staticmethod(quit_then_return_selection))
    monkeypatch.setattr(pages, "_LocalImportWorker", forbidden_worker)
    app._open_control_panel("characters")
    page = app._panel._built["characters"]

    QTest.mouseClick(
        page.findChild(QPushButton, "character_import"), Qt.LeftButton)

    assert app._shutdown_complete
    assert workers_constructed == []
    assert set(app.library.receipts_dir.glob("*.json")) == receipts_before


def test_dispose_inside_confirmation_cannot_install_validated_snapshot(
        app, qt_application, monkeypatch):
    from pathlib import Path

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton
    from retirement_pet.ui.panel import pages

    pack = (Path(__file__).resolve().parent.parent / "assets" / "petpack" /
            "examples" / "realistic-retirement-cat-0.1.1.petpack")
    install_calls = []
    receipts_before = set(app.library.receipts_dir.glob("*.json"))

    def dispose_then_confirm(*_args):
        app._panel.accept()
        return True

    monkeypatch.setattr(
        pages.QFileDialog, "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(pack), "PetPack")))
    monkeypatch.setattr(pages, "_confirm_local_pack", dispose_then_confirm)
    monkeypatch.setattr(
        app, "_import_preflighted_character_pack",
        lambda *_args, **_kwargs: install_calls.append(1))
    app._open_control_panel("characters")
    page = app._panel._built["characters"]

    QTest.mouseClick(
        page.findChild(QPushButton, "character_import"), Qt.LeftButton)
    worker = page._import_worker_state["worker"]
    _wait_until(qt_application, lambda: app._panel is None)
    _wait_until(qt_application, worker.isFinished)

    assert app._panel is None
    assert page._import_worker_state["disposed"]
    assert not worker.has_live_process()
    assert install_calls == []
    assert set(app.library.receipts_dir.glob("*.json")) == receipts_before


@pytest.mark.parametrize(
    ("close_path", "switch_away"),
    (
        ("accept", False),
        ("reject", False),
        ("close", False),
        ("done", False),
        ("escape", False),
        ("accept", True),
        ("reject", True),
    ),
)
def test_all_panel_close_paths_dispose_pages_and_clear_app_reference(
        app, qt_application, monkeypatch, close_path, switch_away):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog
    from retirement_pet.todo import Horizon
    import shiboken6

    app._open_control_panel("todo")
    panel = app._panel
    page = panel._built["todo"]
    assert len(app.todo_events) == 1
    refresh_calls = []
    real_refresh = page.refresh

    def counted_refresh():
        refresh_calls.append(1)
        return real_refresh()

    monkeypatch.setattr(page, "refresh", counted_refresh)
    if switch_away:
        assert panel.open_page("overview")
        assert set(panel.built_page_ids()) == {"todo", "overview"}
        assert len(app.todo_events) == 0
    before_close_refreshes = len(refresh_calls)

    if close_path == "done":
        panel.done(QDialog.Rejected)
    elif close_path == "escape":
        panel.setFocus()
        QTest.keyClick(panel, Qt.Key_Escape)
    else:
        getattr(panel, close_path)()

    # accept/reject are QDialog.done paths and do not emit closeEvent in Qt
    # 6.8.  Disposal therefore has to be unified above closeEvent itself.
    assert panel._built == {}
    assert panel._stack.count() == 0
    assert len(app.todo_events) == 0
    _flush_deferred_deletes(qt_application)
    assert not shiboken6.isValid(panel)
    assert not shiboken6.isValid(page)
    assert app._panel is None

    app.todo.add_task(f"after-{close_path}", Horizon.SHORT)
    assert len(refresh_calls) == before_close_refreshes
