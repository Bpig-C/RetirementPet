"""M6 control panel: lazy pages, single instance, IPC, no pet-surface drift."""

from __future__ import annotations

import json
import time
from pathlib import Path

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


def test_display_page_applies_matrix_combinations(app, qt_application):
    """V12-06: the layout/policy combos are live - enabled, restricted by
    the engine allow-matrix, and applied to the real window on save."""
    import json as _json
    from PySide6.QtWidgets import QComboBox, QPushButton

    app._open_control_panel("display")
    page = app._panel._built["display"]
    layout = page.findChild(QComboBox, "display_layout_combo")
    visibility = page.findChild(QComboBox, "display_policy_combo")
    apply_btn = page.findChild(QPushButton, "display_apply")
    assert layout is not None and layout.isEnabled()
    assert visibility is not None

    # standard + dnd is forbidden: present but not selectable
    layout.setCurrentIndex(layout.findData("standard"))
    qt_application.processEvents()
    dnd_index = visibility.findData("dnd")
    assert dnd_index >= 0
    assert visibility.model().item(dnd_index).isEnabled() is False

    # an allowed combination saves, persists and drives the window
    layout.setCurrentIndex(layout.findData("pet_only"))
    visibility.setCurrentIndex(visibility.findData("dnd"))
    qt_application.processEvents()
    apply_btn.click()
    qt_application.processEvents()
    assert app.ui_config.effective("layout_id") == "pet_only"
    assert app.ui_config.effective("visibility_policy") == "dnd"
    assert app.window._countdown_mode == "hidden"
    persisted = _json.loads(app.settings.path.read_text(encoding="utf-8"))
    assert persisted["ui_layout_id"] == "pet_only"


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
    # CR-P01: the historical preview carries the publisher_ref exemption
    # warning; the install step receives exactly the acknowledgment the
    # panel passes after the human confirmation dialog (pages.py).
    ok, message = app._import_preflighted_character_pack(
        preview, warnings_acknowledged=preview.warning_codes)

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


# -- V12-05: views, quadrants, reversible archive and the notes side page ------


def _todo_page(app):
    app._open_control_panel("todo")
    panel = app._panel
    return panel._built["todo"]


def _select_task(page, task_id):
    page.tree.setCurrentItem(page._items_by_id[task_id])


def test_todo_main_list_hides_completed_with_done_entry_and_counts(
        app, qt_application):
    from retirement_pet.todo import Horizon

    parent = app.todo.add_task("父任务", Horizon.SHORT)
    app.todo.add_subtask(parent.id, "未完成子任务")
    app.todo.complete(parent.id)          # completing never hides children
    page = _todo_page(app)
    page.refresh()

    # default main list ("进行中") hides the completed parent from the
    # matched set but keeps it as a dimmed structure path above the
    # unfinished child
    assert "进行中" in page.view_box.itemText(0)
    assert parent.id in page._items_by_id
    assert "（结构路径）" in page._items_by_id[parent.id].text(0)
    child_visible = [task for task in page._task_by_id.values()
                     if task.title == "未完成子任务"]
    assert len(child_visible) == 1

    # the completed entry shows it again; nothing was deleted
    done_index = next(i for i in range(page.view_box.count())
                      if page.view_box.itemText(i).startswith("已完成"))
    page.view_box.setCurrentIndex(done_index)
    page.refresh()
    assert parent.id in page._items_by_id
    assert page._items_by_id[parent.id].text(2) == "已完成"

    # pending count excludes the completed parent, includes the child
    assert "待完成 1" in page.count_label.text()


def test_todo_quadrant_views_and_quick_axis_toggles(app, qt_application):
    from retirement_pet.todo import Horizon, Level

    task = app.todo.add_task("象限任务", Horizon.MEDIUM)
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()

    page.buttons["importance"].click()    # None -> high
    page.buttons["urgency"].click()       # None -> high
    page.refresh()
    quadrant = page._todo.get(task.id)
    assert quadrant.importance is Level.HIGH
    assert quadrant.urgency is Level.HIGH

    quad_index = next(i for i in range(page.view_box.count())
                      if "重要且紧急" in page.view_box.itemText(i))
    page.view_box.setCurrentIndex(quad_index)
    page.refresh()
    assert task.id in page._items_by_id

    # cycling further: high -> low -> unclassified
    _select_task(page, task.id)
    page.buttons["importance"].click()    # high -> low
    page.refresh()
    assert page._todo.get(task.id).importance is Level.LOW
    assert page._todo.get(task.id).urgency is Level.HIGH
    unclassified_index = next(i for i in range(page.view_box.count())
                              if page.view_box.itemText(i).startswith("未分类"))
    page.view_box.setCurrentIndex(unclassified_index)
    page.refresh()
    assert task.id not in page._items_by_id   # still classified on one axis


def test_todo_archive_subtree_and_restore_keeps_tree_and_focus_rules(
        app, qt_application):
    from retirement_pet.todo import Horizon

    parent = app.todo.add_task("归档父任务", Horizon.SHORT)
    child = app.todo.add_subtask(parent.id, "归档子任务")
    grandchild = app.todo.add_subtask(child.id, "归档孙任务")
    app.todo.complete(grandchild.id)      # mixed completion inside subtree
    other = app.todo.add_task("保持专注的任务", Horizon.LONG)
    app.todo.start_focus(child.id)
    page = _todo_page(app)
    _select_task(page, parent.id)

    page.buttons["archive"].click()
    qt_application.processEvents()
    # one operation archived the whole tree, completion states untouched
    assert app.todo.get(parent.id).archived is True
    assert app.todo.get(child.id).archived is True
    assert app.todo.get(grandchild.id).archived is True
    assert app.todo.get(grandchild.id).status.value == "done"
    # the focused task was inside the subtree: focus cleared
    assert app.todo.load_focus_projection().focusing is False

    archived_index = next(i for i in range(page.view_box.count())
                          if page.view_box.itemText(i).startswith("已归档"))
    page.view_box.setCurrentIndex(archived_index)
    page.refresh()
    _select_task(page, parent.id)
    page.buttons["restore"].click()
    qt_application.processEvents()
    # restoring the root brings the whole tree back with original states
    assert app.todo.get(parent.id).archived is False
    assert app.todo.get(child.id).archived is False
    assert app.todo.get(grandchild.id).archived is False
    assert app.todo.get(grandchild.id).status.value == "done"
    # restoring never grabs focus
    assert app.todo.load_focus_projection().focusing is False

    # focusing the restored tree works again afterwards
    app.todo.start_focus(other.id)
    assert app.todo.focus_task_id() == other.id


def test_todo_notes_side_page_autosaves_and_survives_switch_and_restart(
        app, qt_application, tmp_path, monkeypatch):
    from retirement_pet.todo import Horizon

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    task = app.todo.add_task("备注任务", Horizon.SHORT)
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()

    # clicking a task opened the side page on demand
    assert page._note_panel.isVisible()
    content = "第一段笔记。\n\n第二段，带 Emoji 🎈 和换行。"
    page.note_editor.setPlainText(content)
    page._on_note_save_clicked()          # explicit save
    assert "备注已保存" in page.status.text()
    assert app.todo.note_for(task.id) == content
    assert not page._note_dirty

    # autosave on task switch: edit, then select another task - the draft
    # must be flushed, not dropped
    other = app.todo.add_task("另一任务", Horizon.SHORT)
    page.refresh()
    edited = content + "\n补充一段。"
    page.note_editor.setPlainText(edited)
    _select_task(page, other.id)
    qt_application.processEvents()
    assert app.todo.note_for(task.id) == edited

    # switching panel pages flushes too, and the note survives a restart
    _select_task(page, task.id)
    qt_application.processEvents()
    page.note_editor.setPlainText(edited + "\n重启前最后一段。")
    app._panel.open_page("overview")
    qt_application.processEvents()
    app.shutdown()
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    restarted = PetApplication(
        argv=["retirement-pet"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-panel-restart-{tmp_path.name}",
    )
    try:
        assert restarted.todo.note_for(task.id) == edited + "\n重启前最后一段。"
    finally:
        restarted.shutdown()


def test_todo_note_over_limit_keeps_draft_and_reports_instead_of_truncating(
        app, qt_application):
    from retirement_pet.todo import Horizon

    task = app.todo.add_task("限额任务", Horizon.SHORT)
    page = _todo_page(app)
    _select_task(page, task.id)
    qt_application.processEvents()

    over_limit = "字" * 10_001
    page.note_editor.setPlainText(over_limit)
    page._on_note_save_clicked()
    # visible failure, no silent truncation, draft kept for retry
    assert "备注未保存" in page.status.text()
    assert page.note_editor.toPlainText() == over_limit
    assert page._note_dirty
    assert app.todo.note_for(task.id) == ""

    # fixing the content retries successfully
    page.note_editor.setPlainText("合规内容")
    page._on_note_save_clicked()
    assert "备注已保存" in page.status.text()
    assert app.todo.note_for(task.id) == "合规内容"


# -- V12-07: character library versions, rollback, safe uninstall -------------


def _minimal_static_version(tmp_path, version):
    """A validator-passing minimal-static variant at a given version."""
    import sys

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "tests"))
    from test_petpack import build_pack, make_manifest, png_bytes

    manifest = make_manifest()
    manifest["package"]["publisher_id"] = "community.retirementpet"
    manifest["package"]["id"] = "minimal-static"
    manifest["package"]["version"] = version
    manifest["actions"][1]["id"] = (
        "community.retirementpet.minimal-static.sample-series.demo.wave")
    manifest["actions"][1]["semantic"] = manifest["actions"][1]["id"]
    pack = tmp_path / f"minimal-static-{version}.petpack"
    pack.write_bytes(build_pack(manifest, {
        "assets/thumb.png": png_bytes(4, 4, (9, 8, 7, 255)),
        "assets/idle_0.png": png_bytes(8, 8),
    }))
    return pack


def content_minimum_width(scroll):
    widget = scroll.widget()
    return max(widget.minimumSizeHint().width(), widget.minimumWidth())


def _open_characters(app):
    app._open_control_panel("characters")
    return app._panel._built["characters"]


def _find_row(listing, fragment):
    for row in range(listing.count()):
        if fragment in listing.item(row).text():
            return row
    raise AssertionError(f"no row containing {fragment!r}")


def test_characters_page_series_filter_details_and_version_labels(
        app, qt_application, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (QComboBox, QGroupBox, QLabel,
                                   QListWidget, QPushButton)

    root = Path(__file__).resolve().parent.parent
    app.library.install(root / "tests/fixtures/petpack/minimal-static.petpack")
    app.library.install(_minimal_static_version(tmp_path, "1.0.1"))
    page = _open_characters(app)
    listing = page.findChild(QListWidget, "character_list")
    combo = page.findChild(QComboBox, "character_series_filter")

    # same-named/related entries are distinguishable by version + digest
    assert any("1.0.0" in listing.item(r).text()
               for r in range(listing.count()))
    assert any("1.0.1" in listing.item(r).text()
               for r in range(listing.count()))

    # series filter narrows the list to that series only
    reference_index = next(
        i for i in range(combo.count())
        if combo.itemData(i) is not None
        and combo.itemData(i)[1] == "reference")
    combo.setCurrentIndex(reference_index)
    assert sum("minimal-static" in listing.item(r).text()
               for r in range(listing.count())) == 2
    combo.setCurrentIndex(0)

    # the details view shows identity, source, and budget facts
    listing.setCurrentRow(_find_row(listing, "1.0.1"))
    QTest.mouseClick(page.findChild(QPushButton, "character_details"),
                     Qt.LeftButton)
    box = page.findChild(QGroupBox, "character_details_box")
    assert not box.isHidden()
    text = page.findChild(QLabel, "character_details_text").text()
    assert "minimal-static" in text
    assert "1.0.1" in text
    assert "内容摘要：" in text
    assert "本地导入" in text
    assert "归档预算" in text
    assert "解码缓存 48 MiB" in text
    # a bounded thumbnail decoded from the pack's declared thumbnail asset
    thumb = page.findChild(QLabel, "character_details_thumb")
    assert thumb.pixmap() is not None
    assert len(page._character_thumbnail_cache["map"]) <= \
        page._character_thumbnail_cache["max"]


def test_characters_page_rollback_to_last_known_good(app, qt_application):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QListWidget, QPushButton

    root = Path(__file__).resolve().parent.parent
    app.library.install(root / "tests/fixtures/petpack/minimal-static.petpack")
    page = _open_characters(app)
    listing = page.findChild(QListWidget, "character_list")
    rollback_btn = page.findChild(QPushButton, "character_rollback")
    status = page.findChild(QLabel, "character_status")

    # without a differing last-known-good there is nothing to roll back to
    QTest.mouseClick(rollback_btn, Qt.LeftButton)
    assert "没有可回退" in status.text()

    # official (healthy) -> demo -> health checkpoint -> back to official
    demo_entry = next(e for e in app.catalog.entries()
                      if e.character_id == "demo")
    assert app._switch_character(demo_entry) is True
    assert app.switcher.checkpoint_active_health() is True
    official = next(e for e in app.catalog.entries() if e.builtin)
    assert app._switch_character(official) is True
    page.activate()

    listing.setCurrentRow(_find_row(listing, "minimal-static"))
    assert rollback_btn.isEnabled()
    QTest.mouseClick(rollback_btn, Qt.LeftButton)
    assert "已回退到上一健康版本" in status.text()
    assert app._selection_store.get("active").character_fqid.endswith("demo")


def test_characters_page_uninstall_states(app, qt_application, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QListWidget, QPushButton

    root = Path(__file__).resolve().parent.parent
    record = app.library.install(
        root / "tests/fixtures/petpack/minimal-static.petpack")
    app.library.install(_minimal_static_version(tmp_path, "1.0.1"))
    page = _open_characters(app)
    listing = page.findChild(QListWidget, "character_list")
    uninstall_btn = page.findChild(QPushButton, "character_uninstall")
    status = page.findChild(QLabel, "character_status")

    # the active builtin and the restore basis are protected
    listing.setCurrentRow(_find_row(listing, "官方"))
    assert not uninstall_btn.isEnabled()
    assert "内置安全包" in uninstall_btn.toolTip()
    listing.setCurrentRow(_find_row(listing, "1.0.0"))
    assert uninstall_btn.isEnabled()

    # file-locked media becomes a visible pending delete, not a fake success
    handle = open(record.pack_path, "rb")
    try:
        QTest.mouseClick(uninstall_btn, Qt.LeftButton)
        assert "待删除" in status.text()
        assert any("待删除" in listing.item(r).text()
                   for r in range(listing.count()))
        assert record.revision_key in app.library.pending_delete_keys()
    finally:
        handle.close()

    # the deletion resumes on the next library open (the same recovery
    # the constructor runs), and the page no longer lists the revision
    handle_was = app.library.recover_pending_deletes(
        lambda rk: False)
    assert handle_was == 1
    assert app.library.pending_delete_keys() == []
    assert app.library.get_revision(record.revision_key) is None
    app._panel.close()
    page = _open_characters(app)
    listing = page.findChild(QListWidget, "character_list")
    assert not any("1.0.0" in listing.item(r).text()
                   for r in range(listing.count()))

    # an unlocked unused revision deletes immediately via the button
    listing.setCurrentRow(_find_row(listing, "1.0.1"))
    uninstall_btn = page.findChild(QPushButton, "character_uninstall")
    status = page.findChild(QLabel, "character_status")
    assert uninstall_btn.isEnabled()
    QTest.mouseClick(uninstall_btn, Qt.LeftButton)
    assert "已删除本地版本" in status.text()
    assert app.library.pending_delete_keys() == []


def test_characters_page_min_size_details_and_onboarding_reachable(
        app, qt_application, tmp_path):
    """L07-02 at 720x480: the details text is fully readable (last line
    inside the viewport, coordinate hit lands on it) and the onboarding
    buttons keep real, hittable heights - in both onboarding states."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (QApplication, QGroupBox, QLabel,
                                   QListWidget, QPushButton, QScrollArea)

    root = Path(__file__).resolve().parent.parent
    app.library.install(root / "tests/fixtures/petpack/minimal-static.petpack")

    # -- state 1: onboarding pending; the first-choice buttons survive --
    page = _open_characters(app)
    panel = app._panel
    panel.resize(720, 480)
    qt_application.processEvents()
    group = page.findChild(QGroupBox, "character_onboarding")
    assert group.isVisible()
    for name in ("character_onboarding_keep_official",
                 "character_onboarding_enable_preview"):
        button = page.findChild(QPushButton, name)
        assert button.height() >= button.minimumSizeHint().height() > 8
        local = QPoint(button.width() // 2, button.height() // 2)
        hit = QApplication.widgetAt(button.mapToGlobal(local))
        assert (hit is button
                or (hit is not None and button.isAncestorOf(hit))), name

    # -- state 2: onboarding done; details fully reachable at min size --
    app._complete_character_onboarding()
    page.activate()
    qt_application.processEvents()
    listing = page.findChild(QListWidget, "character_list")
    listing.setCurrentRow(_find_row(listing, "minimal-static"))
    QTest.mouseClick(page.findChild(QPushButton, "character_details"),
                     Qt.LeftButton)
    qt_application.processEvents()
    scroll = page.findChild(QScrollArea, "characters_scroll")
    details_text = page.findChild(QLabel, "character_details_text")
    details_box = page.findChild(QGroupBox, "character_details_box")
    assert not details_box.isHidden()
    assert details_text.height() >= details_text.heightForWidth(
        details_text.width()) - 1

    vbar = scroll.verticalScrollBar()
    vbar.setValue(vbar.maximum())
    qt_application.processEvents()

    # the last line is inside the viewport, and a coordinate probe at the
    # bottom of the text really hits the details label
    viewport = scroll.viewport()
    bottom_global = details_text.mapToGlobal(
        QPoint(details_text.width() // 2, details_text.height() - 2))
    assert bottom_global.y() <= viewport.mapToGlobal(
        QPoint(0, viewport.height())).y() + 1
    hit = QApplication.widgetAt(bottom_global)
    assert hit is details_text, repr(hit)
    # the budget line - the actual last row - is among the visible lines
    visible_region = details_text.visibleRegion().boundingRect()
    assert visible_region.height() >= details_text.height() - 2

    # -- horizontal accessibility (L07-02 residual): the content must
    # FIT the viewport - the h-scrollbar is deliberately disabled, so
    # any minimum-width overflow would clip the right side silently
    assert content_minimum_width(scroll) <= viewport.width()
    assert scroll.widget().width() <= viewport.width() + 1
    # the digest line is chunked, so the long hex token cannot widen
    # the content: the text's right edge stays inside the viewport
    text_right = details_text.mapToGlobal(
        QPoint(details_text.width(), details_text.height() // 2))
    assert text_right.x() <= viewport.mapToGlobal(
        QPoint(viewport.width(), 0)).x()
    hit_text_edge = QApplication.widgetAt(text_right)
    assert (hit_text_edge is details_text
            or details_box.isAncestorOf(hit_text_edge))
    # the last action button is fully visible and hittable at its edge
    import_btn = page.findChild(QPushButton, "character_import")
    edge = import_btn.mapToGlobal(
        QPoint(import_btn.width() - 2, import_btn.height() // 2))
    assert edge.x() <= viewport.mapToGlobal(
        QPoint(viewport.width(), 0)).x()
    hit_btn = QApplication.widgetAt(edge)
    assert hit_btn is import_btn or import_btn.isAncestorOf(hit_btn)


def _two_character_pack(tmp_path, huge_thumbnail=False):
    """One revision, two characters with DIFFERENT declared thumbnails."""
    import copy
    import sys

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "tests"))
    from test_petpack import build_pack, make_manifest, png_bytes

    manifest = make_manifest()
    manifest["package"]["publisher_id"] = "community.retirementpet"
    manifest["package"]["id"] = "minimal-static"
    manifest["package"]["version"] = "2.0.1"
    manifest["actions"][1]["id"] = (
        "community.retirementpet.minimal-static.sample-series.demo.wave")
    manifest["actions"][1]["semantic"] = manifest["actions"][1]["id"]

    second = copy.deepcopy(manifest["characters"][0])
    second["id"] = "demo2"
    second["thumbnail_asset"] = "thumb2"
    # an action bound by two characters requires a rig contract; give
    # demo2 its own idle action instead
    idle2 = copy.deepcopy(manifest["actions"][0])
    idle2["id"] = "action.idle.demo2"
    manifest["actions"].append(idle2)
    second["actions"] = {"core.idle": "action.idle.demo2"}
    manifest["characters"].append(second)

    assets = {a["id"]: a for a in manifest["assets"]}
    assets["thumb"]["properties"] = {}
    if huge_thumbnail:
        # flat color: compresses to a few KB but decodes 4.2 MPixels,
        # above the thumbnail decode budget and legal for the validator
        pixel_data = png_bytes(2048, 2048, (3, 3, 3, 255))
    else:
        pixel_data = png_bytes(32, 32, (10, 200, 10, 255))
    thumb2 = copy.deepcopy(assets["thumb"])
    thumb2["id"] = "thumb2"
    thumb2["path"] = "assets/thumb2.png"
    assets["thumb2"] = thumb2
    manifest["assets"] = list(assets.values())
    files = {
        "assets/thumb.png": pixel_data,
        "assets/idle_0.png": png_bytes(8, 8),
        "assets/thumb2.png": png_bytes(32, 32, (200, 10, 10, 255)),
    }
    pack = tmp_path / "minimal-static-2.0.1.petpack"
    pack.write_bytes(build_pack(manifest, files))
    return pack


def test_thumbnail_pixel_budget_refuses_huge_decode(
        app, qt_application, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QListWidget, QPushButton

    app.library.install(_two_character_pack(tmp_path, huge_thumbnail=True))
    page = _open_characters(app)
    listing = page.findChild(QListWidget, "character_list")
    listing.setCurrentRow(_find_row(listing, "2.0.1"))
    QTest.mouseClick(page.findChild(QPushButton, "character_details"),
                     Qt.LeftButton)
    qt_application.processEvents()
    thumb_label = page.findChild(QLabel, "character_details_thumb")
    # the compressed file is tiny, but decoding 4.2 MPixels on the UI
    # thread is refused before any pixel buffer is allocated
    # this PySide6 returns a null QPixmap (not None) for an unset label
    assert thumb_label.pixmap().isNull()
    assert "未解码" in thumb_label.text()
    cache = page._character_thumbnail_cache["map"]
    assert not any(key.endswith(":demo") for key in cache)


def test_same_revision_two_characters_have_distinct_thumbnails(
        app, qt_application, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QListWidget, QPushButton

    app.library.install(_two_character_pack(tmp_path))
    page = _open_characters(app)
    listing = page.findChild(QListWidget, "character_list")
    details_btn = page.findChild(QPushButton, "character_details")
    thumb_label = page.findChild(QLabel, "character_details_thumb")
    cache = page._character_thumbnail_cache["map"]

    listing.setCurrentRow(_find_row(listing, "2.0.1"))
    # rows for demo and demo2 of the SAME revision: find them in order
    demo_rows = [r for r in range(listing.count())
                 if "2.0.1" in listing.item(r).text()]
    assert len(demo_rows) == 2
    listing.setCurrentRow(demo_rows[0])
    QTest.mouseClick(details_btn, Qt.LeftButton)
    qt_application.processEvents()
    first_pixmap = thumb_label.pixmap()
    assert first_pixmap is not None

    listing.setCurrentRow(demo_rows[1])
    QTest.mouseClick(details_btn, Qt.LeftButton)
    qt_application.processEvents()
    second_pixmap = thumb_label.pixmap()
    assert second_pixmap is not None

    # same revision, different characters: distinct images and cache keys
    assert first_pixmap.toImage() != second_pixmap.toImage()
    assert len(cache) == 2
    assert len({key.rsplit(":", 1)[0] for key in cache}) == 1
