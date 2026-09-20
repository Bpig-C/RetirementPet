"""V12-06 config pages: real UI + App chain tests.

All tests drive real PetApplication/panel widgets with an isolated data
dir (never the user's daily database).  The acceptance stages actually
covered differ per item and are stated per test:

- Full loop (修改 → 应用 → 实际显示改变 → 重建 PetApplication 验证重启
  保持 → 恢复默认): the display-page tests only; they are the ones that
  rebuild PetApplication from the persisted settings file.
- Apply → visible surface change: text-template tests (window title with
  variables substituted, per-character overrides), display-page apply in
  the running app, and the actions page where the acceptance surface is
  the widget (including one real hit-test click at the 720x480 minimum
  size) or the visible status/label text.
- Apply → internal state: mode/loop setting writes and rollback paths,
  where the contract is the settings file plus controller state, not a
  drawn pixel; these do NOT rebuild the application.
"""

import json
import logging
from pathlib import Path

import pytest

from retirement_pet.models import ActionId

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
)


@pytest.fixture
def app(qt_application, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    pet = PetApplication(
        argv=["retirement-pet"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-cfg-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


def _page(app, page_id):
    app._open_control_panel(page_id)
    return app._panel._built[page_id]


def _find(page, name, widget_type):
    found = page.findChild(widget_type, name)
    assert found is not None, name
    return found


def _status_text(page, name):
    label = page.findChild(QLabel, name)
    assert label is not None, name
    return label.text()


# -- service level: resolver priority, persistence, recommendations --------


def test_ui_config_round_trip_priority_and_save_failure(app, monkeypatch):
    """Global override -> per-character override -> engine default; a failed
    save keeps the last durable values resolvable."""
    from retirement_pet.config_system import Source, UiConfigError

    cfg = app.ui_config
    assert cfg.effective("countdown_text") == "{stage_name}"
    assert cfg.source_of("countdown_text") is Source.ENGINE_DEFAULT

    assert cfg.set_user("countdown_text", "全局 {days}")
    assert cfg.set_user("countdown_text", "角色 {days}", "cat.other")
    assert cfg.effective("countdown_text") == "全局 {days}"
    assert cfg.effective("countdown_text", "cat.other") == "角色 {days}"
    assert cfg.source_of(
        "countdown_text", "cat.other") is Source.CHARACTER_OVERRIDE

    # forbidden combo rejected; the last valid value survives
    assert cfg.set_user("layout_id", "standard")
    with pytest.raises(UiConfigError):
        cfg.set_user("visibility_policy", "dnd")
    assert cfg.effective("visibility_policy") == "normal"

    # persistence failure rolls back; resolver still holds durable values
    monkeypatch.setattr(app.settings, "save", lambda: False)
    assert cfg.set_user("countdown_text", "不应生效") is False
    assert cfg.effective("countdown_text") == "全局 {days}"
    monkeypatch.undo()

    assert cfg.reset("countdown_text", "cat.other")
    assert cfg.effective("countdown_text", "cat.other") == "全局 {days}"


def test_pack_recommendation_takes_effect_only_after_explicit_apply(app):
    from retirement_pet.config_system import Source

    cfg = app.ui_config
    assert cfg.offer_recommendation(
        {"layout_id": "focus", "visibility_policy": "quiet"}) == {}
    # parked, NOT resolving
    assert cfg.effective("layout_id") == "standard"
    assert cfg.source_of("layout_id") is Source.ENGINE_DEFAULT
    assert cfg.pending_recommendation["layout_id"] == "focus"

    assert cfg.apply_recommendation()
    assert cfg.effective("layout_id") == "focus"
    assert cfg.effective("visibility_policy") == "quiet"
    assert cfg.pending_recommendation == {}

    # invalid or forbidden recommendations stage nothing
    assert "layout_id" in cfg.offer_recommendation({"layout_id": "nope"})
    assert cfg.pending_recommendation == {}
    assert "visibility_policy" in cfg.offer_recommendation(
        {"layout_id": "standard", "visibility_policy": "dnd"})
    assert cfg.effective("layout_id") == "focus"


# -- display page: apply -> real window -> restart -> reset -----------------


def test_display_apply_changes_window_and_survives_restart(
        qt_application, monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock
    from retirement_pet.ui.pet_window import CAT_AREA_HEIGHT

    app = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-cfg-{tmp_path.name}")
    try:
        page = _page(app, "display")
        layout = _find(page, "display_layout_combo", QComboBox)
        policy = _find(page, "display_policy_combo", QComboBox)
        apply_btn = _find(page, "display_apply", QPushButton)
        layout.setCurrentIndex(layout.findData("pet_only"))
        policy.setCurrentIndex(policy.findData("normal"))
        qt_application.processEvents()
        apply_btn.click()
        qt_application.processEvents()
        # real, visible effect: the panel area no longer exists
        assert app.window._countdown_mode == "hidden"
        assert not app.window._panel_visible()
        assert app.window.height() == CAT_AREA_HEIGHT
        data_dir = tmp_path
    finally:
        app.shutdown()

    restarted = PetApplication(
        argv=["retirement-pet"], data_dir=data_dir, clock=FakeClock(),
        headless=True, instance_name=f"pytest-cfg2-{tmp_path.name}")
    try:
        # the hidden panel survives the restart, not just the stored value
        assert restarted.window._countdown_mode == "hidden"
        assert restarted.ui_config.effective("layout_id") == "pet_only"

        page = _page(restarted, "display")
        reset_btn = _find(page, "display_reset", QPushButton)
        reset_btn.click()
        qt_application.processEvents()
        assert restarted.ui_config.effective("layout_id") == "standard"
        assert restarted.window._countdown_mode == "standard"
        persisted = json.loads(
            restarted.settings.path.read_text(encoding="utf-8"))
        assert "ui_layout_id" not in persisted
    finally:
        restarted.shutdown()


# -- text page: edit -> preview -> apply -> display -> override -> reset ----


def test_text_page_template_applies_to_window_heading(app, qt_application):
    page = _page(app, "text")
    key_combo = _find(page, "text_key_combo", QComboBox)
    template_edit = _find(page, "text_template_edit", QLineEdit)
    apply_btn = _find(page, "text_apply", QPushButton)

    assert key_combo.currentData() == "countdown_text"
    # the current template loads from the resolver (engine default)
    assert template_edit.text() == "{stage_name}"

    template_edit.setText("距离退休还有 {days} 天")
    qt_application.processEvents()
    apply_btn.click()
    qt_application.processEvents()
    assert "已应用" in _status_text(page, "text_status")
    assert app.window._heading_text is not None
    assert "距离退休还有" in app.window._heading_text
    assert "{days}" not in app.window._heading_text


def test_text_page_character_override_wins_and_restart_keeps(
        app, qt_application):
    page = _page(app, "text")
    template_edit = _find(page, "text_template_edit", QLineEdit)
    apply_btn = _find(page, "text_apply", QPushButton)
    scope_character = _find(page, "text_scope_character", QCheckBox)
    reset_btn = _find(page, "text_reset", QPushButton)

    fqid = app._active_character_fqid()
    assert fqid is not None
    assert app.ui_config.set_user("countdown_text", "全局模板")
    # switching scope first loads that scope's current value into the editor
    scope_character.setChecked(True)
    qt_application.processEvents()
    template_edit.setText("角色专属模板")
    qt_application.processEvents()
    apply_btn.click()
    qt_application.processEvents()
    assert app.ui_config.effective("countdown_text", fqid) == "角色专属模板"
    assert app.ui_config.effective("countdown_text") == "全局模板"
    # other characters keep resolving the global template
    assert app.ui_config.effective("countdown_text", "other.char") == "全局模板"

    # a long Chinese template is valid (within the 500-char safety limit)
    long_template = "陪" + "你" * 480 + " {days}"
    template_edit.setText(long_template)
    qt_application.processEvents()
    apply_btn.click()
    qt_application.processEvents()
    assert "已应用" in _status_text(page, "text_status")

    # over the limit is rejected with visible feedback; last value kept
    template_edit.setText("超" * 501)
    qt_application.processEvents()
    apply_btn.click()
    qt_application.processEvents()
    assert "未保存" in _status_text(page, "text_status")
    assert app.ui_config.effective("countdown_text", fqid) == long_template

    # reset the character scope: resolution falls back to the global template
    scope_character.setChecked(True)
    qt_application.processEvents()
    reset_btn.click()
    qt_application.processEvents()
    assert app.ui_config.effective("countdown_text", fqid) == "全局模板"


def test_text_page_unknown_variables_show_hint_and_render_literally(
        app, qt_application):
    page = _page(app, "text")
    template_edit = _find(page, "text_template_edit", QLineEdit)
    template_edit.setText("{days} 天后 {nonsense} 仍显示")
    qt_application.processEvents()
    texts = [label.text() for label in page.findChildren(QLabel)]
    assert any("未知变量" in text and "nonsense" in text for text in texts)


# -- actions page: real buttons, discipline, modes, loop --------------------


def test_actions_page_trigger_button_performs_and_reports(
        app, qt_application):
    page = _page(app, "actions")
    trigger = _find(page, "action_trigger_stretch", QPushButton)
    trigger.click()
    qt_application.processEvents()
    assert "已触发" in _status_text(page, "actions_status")
    current = app.controller.current
    assert current is not None and current.spec.action_id.value == "stretch"

    # a second immediate click is honest about the state
    trigger.click()
    qt_application.processEvents()
    assert "已触发" not in _status_text(page, "actions_status")


def test_actions_page_mode_combo_gates_trigger_and_random(
        app, qt_application):
    page = _page(app, "actions")
    mode_combo = _find(page, "action_mode_yawn", QComboBox)
    trigger = _find(page, "action_trigger_yawn", QPushButton)

    mode_combo.setCurrentIndex(mode_combo.findData("disabled"))
    qt_application.processEvents()
    assert app._action_mode("yawn") == "disabled"
    trigger.click()
    qt_application.processEvents()
    assert "禁用" in _status_text(page, "actions_status")
    current = app.controller.current
    assert current is None or current.spec.action_id.value != "yawn"

    # the random scheduler cannot fire a disabled action either
    assert app.random_scheduler._mode_allowed("yawn") is False
    assert app.random_scheduler._mode_allowed("stretch") is True

    mode_combo.setCurrentIndex(mode_combo.findData("manual"))
    qt_application.processEvents()
    assert app.random_scheduler._mode_allowed("yawn") is False
    trigger.click()
    qt_application.processEvents()
    assert "已触发" in _status_text(page, "actions_status")


def test_actions_page_loop_switch_needs_real_sequence_material(
        app, qt_application, tmp_path):
    """CR-C06: no fake single-pass control.  Since V13-07 the built-in
    cat ships a real work sequence, so the honest-disable semantics is
    exercised by switching to a parts-only pack: there the loop switch
    is disabled with an honest tooltip instead of pretending a 10s
    duration is one material loop."""
    page = _page(app, "actions")
    loop_check = _find(page, "action_loop_work", QCheckBox)
    # built-in cat 1.0.2: work is a real 4-frame sequence
    assert loop_check.isEnabled() is True
    assert "帧" in loop_check.toolTip()

    parts = _parts_only_petpack(tmp_path)
    app.library.install(parts)
    entry = next(e for e in app.catalog.entries()
                 if e.character_id == "partsdemo")
    assert app._switch_character(entry) is True
    qt_application.processEvents()
    page = _page(app, "actions")
    loop_check = _find(page, "action_loop_work", QCheckBox)
    assert loop_check.isEnabled() is False
    assert "序列素材" in loop_check.toolTip()
    # the stored preference is untouched by the disabled control
    assert app._action_loop("work") is True


def test_single_pass_uses_measured_material_duration(
        app, qt_application, monkeypatch):
    """App→engine contract: the app measures the ACTIVE material and the
    engine receives its full-pass duration.  The real PetPack path is
    proven separately against an installed non-uniform pack."""
    monkeypatch.setattr(
        app, "_action_single_pass",
        lambda semantic: (12, 750) if semantic == "work" else None)

    page = _page(app, "actions")
    loop_check = _find(page, "action_loop_work", QCheckBox)
    assert loop_check.isEnabled() is True
    assert "12 帧" in loop_check.toolTip()
    loop_check.setChecked(False)
    qt_application.processEvents()
    assert app._action_loop("work") is False

    _find(page, "action_trigger_work", QPushButton).click()
    qt_application.processEvents()
    current = app.controller.current
    assert current is not None and current.spec.action_id.value == "work"
    assert current.duration_ms == 750
    assert "单次播放素材一遍" in _status_text(page, "actions_status")


def test_disabled_core_action_blocks_context_performance(
        app, qt_application):
    """CR-C06: master's exact scenario - rest disabled + RESTING context
    must not make rest the main performance."""
    from retirement_pet.runtime_state import ContextId

    assert app._set_action_mode("rest", "disabled")
    app.contexts.set(ContextId.RESTING, "test-owner")
    qt_application.processEvents()
    try:
        current = app.controller.current
        assert current is None or current.spec.action_id.value != "rest"
        # the context intent is still rest; the open page says honestly
        # that the disabled intent is NOT performed
        assert app.bridge.last_performance is not None
        assert "rest" in app.bridge.last_performance.semantic
        page = _page(app, "actions")
        label = page.findChild(QLabel, "actions_current")
        assert "已禁用" in label.text()
        assert "rest" in label.text()
    finally:
        app.contexts.clear(ContextId.RESTING, "test-owner")


def test_disabling_active_core_action_stops_it(app, qt_application):
    accepted, _message = app._request_manual_action("work")
    assert accepted
    assert app.controller.current is not None
    # an unbounded runtime started before the switch must not keep playing
    assert app._set_action_mode("work", "disabled")
    assert app.controller.current is None


def test_disabled_mode_covers_non_panel_request_paths(
        app, qt_application):
    """The veto lives at the executor, so audio/scheduler-style requests
    are covered exactly like panel requests."""
    from retirement_pet.models import ActionId as _ActionId

    assert app.controller.request(_ActionId.MUSIC, "audio") is True
    app.controller.end_current("test-cleanup")
    assert app._set_action_mode("music", "disabled")
    assert app.controller.request(_ActionId.MUSIC, "audio") is False
    assert app.controller.request(
        _ActionId.MUSIC, "resolve:context", force=True) is False
    assert app.controller.current is None


def test_actions_page_meeting_discipline_blocks_without_bypass(
        app, qt_application):
    from retirement_pet.runtime_state import ContextId

    app.contexts.set(ContextId.MEETING, "test-meeting")
    try:
        page = _page(app, "actions")
        stretch = _find(page, "action_trigger_stretch", QPushButton)
        stretch.click()
        qt_application.processEvents()
        assert "会议" in _status_text(page, "actions_status")
        current = app.controller.current
        assert current is None \
            or current.spec.action_id.value != "stretch"
    finally:
        app.contexts.clear(ContextId.MEETING, "test-meeting")


def test_actions_page_missing_material_reports_fallback(
        app, qt_application, monkeypatch):
    """A character that neither declares the semantic nor registers the
    action gets an honest fallback report, and nothing performs."""
    page = _page(app, "actions")
    trigger = _find(page, "action_trigger_work", QPushButton)

    class NoSemantics:
        semantics: dict = {}
        character_actions = frozenset()

        def supports(self, key):
            return False

    original = app.capabilities
    app.capabilities = NoSemantics()
    monkeypatch.setattr(
        app.controller, "has_action",
        lambda action_id: action_id.value != "work")
    try:
        trigger.click()
        qt_application.processEvents()
    finally:
        app.capabilities = original
    assert "缺少" in _status_text(page, "actions_status")
    current = app.controller.current
    assert current is None or current.spec.action_id.value != "work"


# -- CR-C01: corrupt persisted config degrades at load, never blocks startup --


def _launch_with_settings(tmp_path, subdir, extra):
    """Construct a real PetApplication over a data dir whose settings.json
    carries the extra (possibly corrupt) keys."""
    import json

    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock
    from retirement_pet.paths import settings_file

    data_dir = tmp_path / subdir
    data_dir.mkdir(exist_ok=True)
    payload = {"schema_version": 1}
    payload.update(extra)
    settings_file(data_dir).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return PetApplication(
        argv=["retirement-pet"], data_dir=data_dir,
        clock=FakeClock(), headless=True,
        instance_name=f"pytest-corrupt-{subdir}")


def test_invalid_persisted_templates_degrade_at_load(
        qt_application, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.config_system import Source

    fqid = "engine.retirement-cat.retirement-cat.cat"
    pet = _launch_with_settings(tmp_path, "corrupt-text", {
        "ui_text_templates": {
            "countdown_text": "超" * 501,        # over the 500-char limit
            "greeting_text": "bad \u202e text",  # bidi control character
        },
        "ui_character_text_templates": {
            fqid: {"countdown_text": "<markup>"},  # markup forbidden
        },
    })
    try:
        cfg = pet.ui_config
        # every poisoned key resolves to the engine default; nothing raises
        assert cfg.effective("countdown_text") == "{stage_name}"
        assert cfg.source_of("countdown_text") is Source.ENGINE_DEFAULT
        assert cfg.effective("greeting_text") == "{character_name}陪着你"
        assert cfg.effective("countdown_text", fqid) == "{stage_name}"
        # startup reached a live window whose heading was computed
        assert pet.window._heading_text is not None
        # the text page still opens and operates on the clean state
        page = _page(pet, "text")
        reset_btn = _find(page, "text_reset", QPushButton)
        template_edit = _find(page, "text_template_edit", QLineEdit)
        template_edit.setText("重启后仍可编辑 {days}")
        qt_application.processEvents()
        reset_btn.click()
        qt_application.processEvents()
        assert cfg.effective("countdown_text") == "{stage_name}"
    finally:
        pet.shutdown()


def test_invalid_layout_config_degrades_at_load(
        qt_application, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    # individually invalid values fall back to the engine defaults
    pet = _launch_with_settings(tmp_path, "corrupt-layout", {
        "ui_layout_id": "nope",
        "ui_visibility_policy": "bogus",
    })
    try:
        assert pet.ui_config.effective("layout_id") == "standard"
        assert pet.ui_config.effective("visibility_policy") == "normal"
        assert pet.window._countdown_mode == "standard"
    finally:
        pet.shutdown()

    # individually valid but outside the allow matrix: BOTH drop together
    pet2 = _launch_with_settings(tmp_path, "corrupt-combo", {
        "ui_layout_id": "standard",
        "ui_visibility_policy": "dnd",
    })
    try:
        assert pet2.ui_config.effective("layout_id") == "standard"
        assert pet2.ui_config.effective("visibility_policy") == "normal"
        assert pet2.window._countdown_mode == "standard"
    finally:
        pet2.shutdown()


# -- CR-C02: action controls keep effective geometry at the 720x480 minimum ---


def test_actions_page_controls_visible_and_clickable_at_min_size(
        app, qt_application):
    """CR-C02: at the minimum panel size every trigger button keeps a
    usable height, and a REAL coordinate click resolves to the trigger
    itself and performs the action."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

    app._open_control_panel("actions")
    panel = app._panel
    panel.resize(720, 480)
    panel.show()
    qt_application.processEvents()
    page = panel._built["actions"]

    triggers = [b for b in page.findChildren(QPushButton)
                if b.objectName().startswith("action_trigger_")]
    assert len(triggers) >= 6
    for btn in triggers:
        assert btn.isVisible(), btn.objectName()
        assert btn.height() >= btn.minimumSizeHint().height() > 0, \
            btn.objectName()

    scroll = page.findChild(QScrollArea, "actions_scroll")
    assert scroll is not None
    viewport = scroll.viewport()
    clickable = [
        btn for btn in triggers
        if viewport.rect().contains(
            viewport.mapFromGlobal(btn.mapToGlobal(btn.rect().center())))
        and btn.isVisible()]
    assert clickable, "at least one trigger must sit inside the viewport"
    target = clickable[0]

    status_before = _status_text(page, "actions_status")
    # what a user would REALLY hit at the button's screen position
    global_center = target.mapToGlobal(target.rect().center())
    hit = QApplication.widgetAt(global_center)
    assert hit is not None and (hit is target or target.isAncestorOf(hit)), \
        "the screen position must resolve to the trigger itself"
    QTest.mouseClick(hit, Qt.MouseButton.LeftButton,
                     pos=hit.mapFromGlobal(global_center))
    qt_application.processEvents()

    status = _status_text(page, "actions_status")
    assert status != status_before
    assert "已触发" in status or "冷却" in status or "表演" in status


# -- CR-C03: layout+policy form ONE validated, all-or-nothing transaction -----


def test_display_group_switch_covers_every_allowed_pair(app):
    from retirement_pet.config_system import UiConfigError
    from retirement_pet.layout_policy import ALLOWED, POLICIES

    cfg = app.ui_config
    pairs = [(layout.value, policy.value)
             for layout, spec in ALLOWED.items()
             for policy in spec.allowed_policies]
    assert len(pairs) >= 8
    for layout, policy in pairs:
        # the candidate pair is validated as a whole: no stale-half veto
        assert cfg.set_display(layout, policy) is True, (layout, policy)
        assert cfg.effective("layout_id") == layout
        assert cfg.effective("visibility_policy") == policy

    last_layout, last_policy = pairs[-1]
    for layout, spec in ALLOWED.items():
        for policy in POLICIES:
            if policy.value in spec.allowed_policies:
                continue
            with pytest.raises(UiConfigError):
                cfg.set_display(layout.value, policy.value)
            # the previous valid pair survives untouched
            assert cfg.effective("layout_id") == last_layout
            assert cfg.effective("visibility_policy") == last_policy


def test_display_group_save_failure_restores_whole_pair(app, monkeypatch):
    cfg = app.ui_config
    assert cfg.set_display("standard", "normal")

    monkeypatch.setattr(app.settings, "save", lambda: False)
    assert cfg.set_display("pet_only", "quiet") is False
    monkeypatch.undo()

    # resolver AND disk both still hold the complete previous pair
    assert cfg.effective("layout_id") == "standard"
    assert cfg.effective("visibility_policy") == "normal"
    cfg.reload()
    assert cfg.effective("layout_id") == "standard"
    assert cfg.effective("visibility_policy") == "normal"


def test_display_reset_distinguishes_failure_and_unset(
        app, qt_application, monkeypatch):
    page = _page(app, "display")
    layout = _find(page, "display_layout_combo", QComboBox)
    policy = _find(page, "display_policy_combo", QComboBox)
    apply_btn = _find(page, "display_apply", QPushButton)
    reset_btn = _find(page, "display_reset", QPushButton)

    # nothing configured: reset must say so instead of claiming success
    reset_btn.click()
    qt_application.processEvents()
    assert "无需恢复" in _status_text(page, "display_status")

    layout.setCurrentIndex(layout.findData("pet_only"))
    policy.setCurrentIndex(policy.findData("quiet"))
    qt_application.processEvents()
    apply_btn.click()
    qt_application.processEvents()
    assert "已应用并保存" in _status_text(page, "display_status")

    # a failed save during reset is reported as a failure, values kept
    monkeypatch.setattr(app.settings, "save", lambda: False)
    reset_btn.click()
    qt_application.processEvents()
    monkeypatch.undo()
    assert "恢复失败" in _status_text(page, "display_status")
    assert app.ui_config.effective("layout_id") == "pet_only"
    assert app.ui_config.effective("visibility_policy") == "quiet"

    # and it can then succeed for real
    reset_btn.click()
    qt_application.processEvents()
    assert "已恢复默认" in _status_text(page, "display_status")
    assert app.ui_config.effective("layout_id") == "standard"
    assert app.ui_config.effective("visibility_policy") == "normal"


# -- CR-C04: failed action-setting saves leave NO memory or disk residue ------


def test_action_mode_failure_rolls_back_absent_key_exactly(
        app, qt_application, monkeypatch):
    cfg_key = "ui_action_modes"
    assert app.settings.get(cfg_key) is None  # key does not exist yet

    monkeypatch.setattr(app.settings, "save", lambda: False)
    assert app._set_action_mode("yawn", "disabled") is False
    monkeypatch.undo()

    # the failed change never took effect in memory and left no key behind
    assert app._action_mode("yawn") == "auto"
    assert app.settings.get(cfg_key) is None

    # a later UNRELATED successful save must not smuggle the key to disk
    app._apply_settings({"volume": 0.5})
    persisted = json.loads(app.settings.path.read_text(encoding="utf-8"))
    assert cfg_key not in persisted

    # the honest path still works once the fault is gone
    assert app._set_action_mode("yawn", "manual") is True
    assert app._action_mode("yawn") == "manual"


def test_action_mode_failure_restores_previous_mapping(app, monkeypatch):
    assert app._set_action_mode("work", "manual") is True
    monkeypatch.setattr(app.settings, "save", lambda: False)
    assert app._set_action_mode("work", "disabled") is False
    monkeypatch.undo()
    assert app._action_mode("work") == "manual"
    assert app._action_mode("yawn") == "auto"


def test_action_loop_failure_rolls_back_absent_key_exactly(app, monkeypatch):
    cfg_key = "ui_action_loops"
    assert app.settings.get(cfg_key) is None

    monkeypatch.setattr(app.settings, "save", lambda: False)
    assert app._set_action_loop("work", False) is False
    monkeypatch.undo()

    assert app._action_loop("work") is True
    assert app.settings.get(cfg_key) is None
    app._apply_settings({"volume": 0.6})
    persisted = json.loads(app.settings.path.read_text(encoding="utf-8"))
    assert cfg_key not in persisted


def test_actions_page_control_restores_effective_mode_on_failure(
        app, qt_application, monkeypatch):
    page = _page(app, "actions")
    combo = _find(page, "action_mode_yawn", QComboBox)
    effective_before = app._action_mode("yawn")

    monkeypatch.setattr(app.settings, "save", lambda: False)
    combo.setCurrentIndex(combo.findData("disabled"))
    qt_application.processEvents()
    monkeypatch.undo()

    # the control snapped back to the effective mode WITHOUT re-firing
    assert combo.currentData() == effective_before
    assert "保存失败" in _status_text(page, "actions_status")
    assert app._action_mode("yawn") == effective_before


# -- CR-C05: the actions page follows the REAL performance stream -------------


def test_actions_page_follows_real_performance_and_unsubscribes(
        app, qt_application, caplog):
    from retirement_pet.runtime_state import ContextId

    page = _page(app, "actions")
    label = page.findChild(QLabel, "actions_current")
    qt_application.processEvents()

    # manual start: the label shows the actual performance immediately
    _find(page, "action_trigger_stretch", QPushButton).click()
    qt_application.processEvents()
    assert "stretch" in label.text()
    assert "面板触发" in label.text()

    # a context switch drives the controller; the active page follows
    app.contexts.set(ContextId.WORKING, "test-owner")
    qt_application.processEvents()
    try:
        assert "work" in label.text()
        current = app.controller.current
        assert current is not None
        assert current.spec.action_id.value == "work"
        assert "上下文驱动" in label.text()

        # the end of a performance goes through the same subscription
        app.controller.end_current("test-finished")
        qt_application.processEvents()
        # no runtime left: the label falls back to the context intent
        assert "core.work" in label.text()
    finally:
        app.contexts.clear(ContextId.WORKING, "test-owner")
    qt_application.processEvents()
    assert not label.text().startswith("work")

    # a capability republish (the character-switch path) also refreshes
    app.bridge.set_capabilities(app.capabilities, reason="test-republish")
    qt_application.processEvents()

    # closing the page removes the subscription: later action changes
    # must NOT reach the disposed page.  The page's widgets are already
    # destroyed, so a leaked subscription would crash inside the listener
    # and the controller logs it as a failed listener.
    app._panel.close()
    qt_application.processEvents()
    assert app._panel is None
    with caplog.at_level(logging.ERROR,
                         logger="retirement_pet.action_controller"):
        accepted, _message = app._request_manual_action("stretch")
        qt_application.processEvents()
    assert accepted
    assert "action change listener failed" not in caplog.text

    # reopening builds a fresh page that shows the live state again
    reopened = _page(app, "actions")
    reopened_label = reopened.findChild(QLabel, "actions_current")
    qt_application.processEvents()
    assert "stretch" in reopened_label.text()


def test_actions_page_mode_labels_follow_effective_state(
        app, qt_application):
    page = _page(app, "actions")
    yawn_label = next(label for label in page.findChildren(QLabel)
                      if label.text().startswith("yawn（"))
    assert "自动" in yawn_label.text()

    assert app._set_action_mode("yawn", "manual")
    _find(page, "action_mode_yawn", QComboBox).setCurrentIndex(
        _find(page, "action_mode_yawn", QComboBox).findData("manual"))
    qt_application.processEvents()
    assert "仅手动" in yawn_label.text()

    # re-entering the page also resyncs every row label
    app._set_action_mode("yawn", "disabled")
    page.activate()
    assert "禁用" in yawn_label.text()


# -- C06-R1: a vetoed context request must not leave a stale performance ----


def test_vetoed_meeting_request_ends_stale_work_performance(
        app, qt_application):
    from retirement_pet.runtime_state import ContextId

    app.contexts.set(ContextId.WORKING, "test-owner")
    qt_application.processEvents()
    assert app.controller.current is not None
    assert app.controller.current.spec.action_id.value == "work"

    assert app._set_action_mode("meeting", "disabled")
    app.contexts.set(ContextId.MEETING, "test-owner")
    qt_application.processEvents()
    try:
        # the resolver's choice (meeting) is vetoed; the work performance
        # from the earlier resolve must not outlive it, and both REAL
        # facts stay true (nothing is cleared to mask the veto)
        assert app.controller.current is None
        assert app.bridge.last_performance.semantic == "core.meeting"
        active = {c for c in app.contexts.active()}
        assert ContextId.MEETING in active
        assert ContextId.WORKING in active
    finally:
        app.contexts.clear(ContextId.MEETING, "test-owner")
        app.contexts.clear(ContextId.WORKING, "test-owner")


def test_unvetoed_context_switch_still_replaces_performance(
        app, qt_application):
    """Control: without a disable, entering meeting replaces work as before."""
    from retirement_pet.runtime_state import ContextId

    app.contexts.set(ContextId.WORKING, "test-owner")
    qt_application.processEvents()
    app.contexts.set(ContextId.MEETING, "test-owner")
    qt_application.processEvents()
    try:
        current = app.controller.current
        assert current is not None
        assert current.spec.action_id.value == "meeting"
    finally:
        app.contexts.clear(ContextId.MEETING, "test-owner")
        app.contexts.clear(ContextId.WORKING, "test-owner")


def test_vetoed_dnd_request_ends_stale_work_performance(
        app, qt_application):
    from retirement_pet.runtime_state import ContextId

    app.contexts.set(ContextId.WORKING, "test-owner")
    qt_application.processEvents()
    assert app.controller.current is not None

    assert app._set_action_mode("rest", "disabled")
    app.contexts.set(ContextId.DO_NOT_DISTURB, "test-owner")
    qt_application.processEvents()
    try:
        # dnd resolves to rest; rest is disabled -> the stale work
        # performance ends instead of violating the dnd discipline
        assert app.controller.current is None
        assert app.bridge.last_performance.semantic == "core.rest"
        active = {c for c in app.contexts.active()}
        assert ContextId.DO_NOT_DISTURB in active
        assert ContextId.WORKING in active
    finally:
        app.contexts.clear(ContextId.DO_NOT_DISTURB, "test-owner")
        app.contexts.clear(ContextId.WORKING, "test-owner")


# -- C05-R1: the open page follows vetoed context changes too ----------------


def test_disabled_context_refreshes_open_page_without_action_event(
        app, qt_application):
    from retirement_pet.runtime_state import ContextId

    page = _page(app, "actions")
    label = page.findChild(QLabel, "actions_current")
    qt_application.processEvents()
    assert "core.idle" in label.text()

    assert app._set_action_mode("rest", "disabled")
    app.contexts.set(ContextId.RESTING, "test-owner")
    qt_application.processEvents()
    try:
        # the vetoed resolve fires no controller event; the page follows
        # through its context subscription and labels the intent honestly
        assert app.controller.current is None
        assert "core.rest" in label.text()
        assert "已禁用" in label.text()
    finally:
        app.contexts.clear(ContextId.RESTING, "test-owner")


def test_actions_page_dispose_removes_every_subscription(
        app, qt_application):
    baseline_ctx = len(app.contexts._listeners)
    baseline_ctrl = len(app.controller._listeners)
    _page(app, "actions")
    assert len(app.contexts._listeners) == baseline_ctx + 1
    assert len(app.controller._listeners) == baseline_ctrl + 1
    app._panel.close()
    assert len(app.contexts._listeners) == baseline_ctx
    assert len(app.controller._listeners) == baseline_ctrl


# -- C06-R2: loop control and timing read the ACTIVE PetPack -----------------


def _parts_only_petpack(tmp_path):
    """Build a validator-passing pack whose core.work is a STATIC asset
    (no sequence material anywhere) - the honest-disable test fixture."""
    import hashlib
    import json
    import zipfile

    from PySide6.QtGui import QImage, QColor

    fixtures = Path(__file__).resolve().parent / "fixtures/petpack/minimal-static"
    root = tmp_path / "partsdemo-pack"
    (root / "assets").mkdir(parents=True)
    (root / "legal").mkdir()
    (root / "legal" / "license.txt").write_bytes(
        (fixtures / "legal/license.txt").read_bytes())

    for name, rgb in (("idle.png", (60, 60, 60)),
                      ("work.png", (30, 160, 30))):
        img = QImage(64, 64, QImage.Format.Format_RGB32)
        img.fill(QColor(*rgb))
        assert img.save(str(root / "assets" / name), "PNG")
    (root / "assets" / "thumbnail.png").write_bytes(
        (fixtures / "assets/thumbnail.png").read_bytes())

    manifest = json.loads((fixtures / "petpack.json").read_text("utf-8"))
    manifest["package"]["id"] = "parts-demo"
    manifest["package"]["display_name"] = {"en": "Parts Demo",
                                           "zh-CN": "部件演示包"}

    def asset_entry(asset_id, filename):
        data = (root / "assets" / filename).read_bytes()
        return {
            "id": asset_id, "media_type": "image/png",
            "path": f"assets/{filename}",
            "byte_size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "properties": {"width": 64, "height": 64},
            "rights_ref": "rights.original",
            "source_ref": "source.original",
        }

    manifest["assets"] = [
        asset_entry("asset.thumb", "thumbnail.png"),
        asset_entry("asset.idle", "idle.png"),
        asset_entry("asset.work", "work.png"),
    ]
    actions = [a for a in manifest["actions"]
               if a["semantic"] in ("core.idle", "core.work")]
    for action in actions:
        action["lifecycle"]["loop"]["renderer"] = {
            "type": "static",
            "asset": "asset.idle" if action["semantic"] == "core.idle"
            else "asset.work"}
        action["id"] = f"action.parts.{action['semantic']}"
    manifest["actions"] = actions
    character = manifest["characters"][0]
    character["id"] = "partsdemo"
    character["actions"] = {
        a["semantic"]: a["id"] for a in actions}

    out = tmp_path / "parts-demo.petpack"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("petpack.json",
                         json.dumps(manifest, ensure_ascii=False))
        for filename in ("thumbnail.png", "idle.png", "work.png",
                         "legal/license.txt"):
            archive.write(root / "assets" / filename
                          if not filename.startswith("legal")
                          else root / filename,
                          f"assets/{filename}"
                          if not filename.startswith("legal")
                          else filename)
    return out


def _sequence_petpack(tmp_path):
    """Build a validator-passing pack whose core.work is a two-frame
    sequence with NON-uniform frame durations (500/700ms -> 1200ms pass)."""
    import hashlib
    import json
    import zipfile

    from PySide6.QtGui import QImage, QColor

    fixtures = Path(__file__).resolve().parent / "fixtures/petpack/minimal-static"
    root = tmp_path / "seqdemo-pack"
    (root / "assets").mkdir(parents=True)
    (root / "legal").mkdir()
    (root / "legal" / "license.txt").write_bytes(
        (fixtures / "legal/license.txt").read_bytes())

    for name, rgb in (("idle.png", (60, 60, 60)),
                      ("f0.png", (220, 30, 30)),
                      ("f1.png", (30, 30, 220))):
        img = QImage(64, 64, QImage.Format.Format_RGB32)
        img.fill(QColor(*rgb))
        assert img.save(str(root / "assets" / name), "PNG")
    (root / "assets" / "thumbnail.png").write_bytes(
        (fixtures / "assets/thumbnail.png").read_bytes())

    manifest = json.loads((fixtures / "petpack.json").read_text("utf-8"))
    manifest["package"]["id"] = "sequence-demo"
    manifest["package"]["display_name"] = {"en": "Sequence Demo",
                                           "zh-CN": "序列演示包"}

    def asset_entry(asset_id, filename):
        data = (root / "assets" / filename).read_bytes()
        return {
            "id": asset_id, "media_type": "image/png",
            "path": f"assets/{filename}",
            "byte_size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "properties": {"width": 64, "height": 64},
            "rights_ref": "rights.original",
            "source_ref": "source.original",
        }

    for entry in manifest["assets"]:
        if entry["id"] == "asset.idle":
            entry.update(asset_entry("asset.idle", "idle.png"))
    manifest["assets"].append(asset_entry("asset.f0", "f0.png"))
    manifest["assets"].append(asset_entry("asset.f1", "f1.png"))

    manifest["actions"].append({
        "id": "action.work",
        "semantic": "core.work",
        "audio": None,
        "loop_modes": ["repeat"],
        "policy_tags": ["silent"],
        "user_modes": ["auto", "manual", "disabled"],
        "interrupt": {"max_exit_ms": 0, "policy": "immediate"},
        "lifecycle": {"loop": {"renderer": {"type": "sequence", "frames": [
            {"asset": "asset.f0", "duration_ms": 500},
            {"asset": "asset.f1", "duration_ms": 700},
        ]}}},
    })
    manifest["characters"][0]["id"] = "seqdemo"
    manifest["characters"][0]["actions"]["core.work"] = "action.work"

    (root / "petpack.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    pack_path = tmp_path / "sequence-demo.petpack"
    with zipfile.ZipFile(pack_path, "w") as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    return pack_path


def _switch_to_sequence_character(app):
    app.library.install(_pack_path_holder[0])
    entry = next(e for e in app.catalog.entries()
                 if e.character_id == "seqdemo")
    assert app._switch_character(entry) is True


_pack_path_holder = [None]


def test_loop_switch_and_single_pass_follow_installed_pack(
        app, qt_application, tmp_path):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from retirement_pet.models import ActionId, LifeStage, RenderSnapshot

    _pack_path_holder[0] = _sequence_petpack(tmp_path)
    _switch_to_sequence_character(app)
    assert app.switcher.current_runtime is not None

    # the ACTIVE pack's declared schedule drives availability and timing
    assert app._action_single_pass("work") == (2, 1200)

    page = _page(app, "actions")
    check = _find(page, "action_loop_work", QCheckBox)
    assert check.isEnabled() is True
    assert "手动触发" in check.toolTip()
    check.setChecked(False)
    qt_application.processEvents()

    _find(page, "action_trigger_work", QPushButton).click()
    qt_application.processEvents()
    current = app.controller.current
    assert current is not None and current.spec.action_id.value == "work"
    # one pass = 500 + 700 = 1200ms of DECLARED material time, not a
    # frame-count-over-fps guess and not the fixed 10s stand-in
    assert current.duration_ms == 1200
    assert "1.2 秒" in _status_text(page, "actions_status")

    # material timing drives the visible frame schedule through the real
    # pack render path: f0 for 0-499ms, f1 from 500ms (the cycle boundary)
    runtime = app.switcher.current_runtime

    def body_at(elapsed_ms):
        image = QImage(64, 64, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.black)
        painter = QPainter(image)
        snapshot = RenderSnapshot(
            action=ActionId.WORK, stage=LifeStage.RETIRED,
            elapsed_ms=elapsed_ms, frame=0, time_ms=elapsed_ms)
        runtime.render_body(painter, QRectF(0, 0, 64, 64), snapshot)
        painter.end()
        return image

    assert body_at(100) == body_at(400)      # both inside f0's 500ms
    assert body_at(100) != body_at(600)      # f1 after the 500ms boundary
    assert body_at(600) == body_at(1199)     # last frame until the pass ends

    # the single pass really ENDS at the material boundary
    assert app.controller.current is not None
    app.clock.advance_ms(1199)
    app.controller.tick()
    assert app.controller.current is not None
    app.clock.advance_ms(1)
    app.controller.tick()
    assert app.controller.current is None


def test_auto_context_path_is_out_of_loop_switch_scope(
        app, qt_application, tmp_path):
    """The loop switch governs manual triggers only; context-driven
    performances loop until their fact changes.  The tooltip labels this."""
    from retirement_pet.runtime_state import ContextId

    _pack_path_holder[0] = _sequence_petpack(tmp_path)
    _switch_to_sequence_character(app)
    app._set_action_loop("work", False)

    app.contexts.set(ContextId.WORKING, "test-owner")
    qt_application.processEvents()
    try:
        current = app.controller.current
        assert current is not None
        assert current.spec.action_id.value == "work"
        assert current.duration_ms is None
    finally:
        app.contexts.clear(ContextId.WORKING, "test-owner")


def test_loop_switch_capability_follows_character_switches(
        app, qt_application, tmp_path):
    _pack_path_holder[0] = _parts_only_petpack(tmp_path)

    page = _page(app, "actions")
    check = _find(page, "action_loop_work", QCheckBox)
    assert check.isEnabled() is True         # built-in cat 1.0.2: sequence

    app.library.install(_pack_path_holder[0])
    entry = next(e for e in app.catalog.entries()
                 if e.character_id == "partsdemo")
    assert app._switch_character(entry) is True
    qt_application.processEvents()
    # the page stayed open: the character-change listener refreshed it
    assert check.isEnabled() is False        # parts pack: no sequence

    official = next(e for e in app.catalog.entries() if e.builtin)
    assert app._switch_character(official) is True
    qt_application.processEvents()
    assert check.isEnabled() is True
