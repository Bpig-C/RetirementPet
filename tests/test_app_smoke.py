"""PetApplication integration smoke: wire-up, frames, menus, clean shutdown."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

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
        instance_name=f"pytest-app-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


def pump(app, frames=5, step_ms=100, services=True):
    app.visual_clock.set_visible(True)  # compose frames like a shown window
    for _ in range(frames):
        app.clock.advance_ms(step_ms)
        app.visual_clock.pump()
        if services:
            app.service_clock.pump()


def test_application_builds_and_runs_headless(app, qt_application):
    assert not app._second_instance
    assert app.visual_clock is not None
    assert app.service_clock is not None
    assert app.window is not None


def test_frame_tick_updates_countdown(app, qt_application):
    pump(app, frames=3)
    assert app._countdown_cache is not None
    assert app._countdown_cache.days > 10000


def test_menu_builds(app, qt_application):
    menu = app._build_menu()
    labels = [action.text() for action in menu.actions()]
    assert "打开待办" in labels
    assert any("退出" in label for label in labels)
    assert any("动作" in label for label in labels)
    assert any("音乐" in label for label in labels)
    assert any("开机自动启动" in label for label in labels)
    menu.deleteLater()


def test_pet_menu_todo_entry_opens_existing_todo_page(
        app, qt_application, monkeypatch):
    opened = []
    monkeypatch.setattr(app, "_open_control_panel", opened.append)
    menu = app._build_menu()
    action = next(item for item in menu.actions() if item.text() == "打开待办")

    action.trigger()

    assert opened == ["todo"]
    menu.deleteLater()


def test_persisted_position_restored(tmp_path, qt_application, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication

    (tmp_path / "state.json").write_text(
        json.dumps({"window_pos": [50, 70]}), encoding="utf-8"
    )
    pet = PetApplication(
        argv=["retirement-pet"],
        data_dir=tmp_path,
        headless=True,
        instance_name=f"pytest-restore-{tmp_path.name}",
    )
    try:
        assert pet.window.x() == 50 and pet.window.y() == 70
    finally:
        pet.shutdown()


def test_second_app_instance_reports_not_primary(tmp_path, qt_application, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication

    name = f"pytest-second-{tmp_path.name}"
    first = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path / "a",
        headless=True, instance_name=name,
    )
    try:
        second = PetApplication(
            argv=["retirement-pet"], data_dir=tmp_path / "b",
            headless=True, instance_name=name,
        )
        assert second._second_instance is True
        assert second.run() == 0
    finally:
        first.shutdown()


def test_meeting_mode_flow(app, qt_application):
    app.schedule.set_manual_meeting(True)
    assert app.controller.current_action() is not None
    from retirement_pet.models import ActionId

    assert app.controller.current_action() is ActionId.MEETING
    app.schedule.set_manual_meeting(False)
    assert app.controller.current_action() is None


def test_settings_apply_updates_window(app, qt_application):
    app._apply_settings({"always_on_top": False, "volume": 0.1})
    from PySide6.QtCore import Qt

    assert not bool(app.window.windowFlags() & Qt.WindowStaysOnTopHint)
    assert app.audio.volume == pytest.approx(0.1)


def test_shutdown_saves_state(app, qt_application, tmp_path):
    app.window.move(120, 130)
    app.shutdown()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data.get("window_pos") == [120, 130]


def test_settings_file_written(app, qt_application, tmp_path):
    app.settings.set("volume", 0.5)
    app.settings.save()
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data["volume"] == 0.5
    # Unknown-field preservation still applies through the app's store.
    data["future_key"] = 123
    (tmp_path / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    app.settings.load()
    assert app.settings.get("future_key") == 123


# -- M1: hidden window = zero visual ticks; services keep running -------------


def test_hidden_window_produces_zero_visual_ticks(app, qt_application):
    app.run()  # headless: shows the window, then returns
    assert app.visual_clock.running

    app._hide_window()
    assert not app.visual_clock.running

    visual_ticks = []
    app.visual_clock.tick.connect(visual_ticks.append)
    for _ in range(50):  # 5 simulated seconds
        app.clock.advance_ms(100)
        app.visual_clock.pump()
        app.service_clock.pump()
    assert visual_ticks == [], "hidden pet must compose zero frames"


def test_hidden_window_keeps_countdown_fresh(app, qt_application):
    """Facts update on the service clock while the pet is hidden."""
    app.run()
    app._hide_window()
    before = app._countdown_cache.total_seconds
    for _ in range(20):  # 2 simulated seconds
        app.clock.advance_ms(100)
        app.service_clock.pump()
    assert app._countdown_cache.total_seconds < before


def test_show_resumes_visual_clock(app, qt_application):
    app.run()
    app._hide_window()
    assert not app.visual_clock.running
    app._show_window()
    assert app.visual_clock.running
    app._hide_window()


def test_perf_markers_written(app, qt_application, tmp_path):
    app.run()  # tray_ready is marked in run()
    events = {e["marker"] for e in app.perf.events}
    assert "process_start" in events
    assert "qt_ready" in events
    assert "tray_ready" in events
    assert (tmp_path / "logs" / "perf_markers.jsonl").is_file()


# -- M2: services speak facts; the executor follows the resolver ---------------


def test_music_playback_sets_music_context(app, qt_application):
    from retirement_pet.models import ActionId
    from retirement_pet.runtime_state import ContextId

    app._on_playback_changed(True, "test-track")
    assert app.contexts.is_active(ContextId.MUSIC)
    assert app.controller.current_action() is ActionId.MUSIC

    app._on_playback_changed(False, "")
    assert not app.contexts.is_active(ContextId.MUSIC)
    assert app.controller.current_action() is None


def test_user_standby_clears_user_facts_only(app, qt_application):
    from retirement_pet.models import ActionId

    app._service_port.request(ActionId.WORK, "user")
    assert app.controller.current_action() is ActionId.WORK
    app._user_standby()
    assert app.controller.current_action() is None

    # a service fact survives user standby and re-derives its performance
    app._service_port.request(ActionId.WORK, "rhythm")
    app._user_standby()
    assert app.controller.current_action() is ActionId.WORK


# -- M5: PetSurface shows pack characters; switching preserves user data -------


@pytest.fixture()
def pack_app(app, qt_application, tmp_path):
    """An app whose library has the reference packs installed."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    app.library.install(root / "tests/fixtures/petpack/minimal-static.petpack")
    app.library.register_builtin_release(
        root / "assets/petpack/retirement-cat-official.petpack")
    return app


def test_switch_character_swaps_body_and_keeps_target(pack_app, qt_application):
    entry = next(e for e in pack_app.catalog.entries()
                 if e.character_id == "demo")
    before_target = pack_app.countdown_module.target

    assert pack_app._switch_character(entry) is True
    assert pack_app.window._renderer is pack_app.switcher.current_runtime
    assert pack_app.switcher.current_runtime.character_fqid().endswith(".demo")

    # the retirement target is USER global data: switching must not move it
    assert pack_app.countdown_module.target == before_target
    # context facts and settings survive by construction
    assert pack_app.settings.get("target_datetime") is not None


def test_switch_failure_keeps_current_body(pack_app, qt_application):
    from retirement_pet.petpack.identity import PackKey, RevisionKey

    original_renderer = pack_app.window._renderer
    broken_entry = type("E", (), {})()
    broken_entry.revision_key = RevisionKey(
        pack=PackKey("community.example", "missing"), package_version="1.0.0",
        content_digest="0" * 64)
    broken_entry.character_id = "demo"
    broken_entry.display_name = "不存在的角色"

    assert pack_app._switch_character(broken_entry) is False
    assert pack_app.window._renderer is original_renderer


def test_active_character_restored_on_restart(pack_app, qt_application, tmp_path):
    assert pack_app.switcher.checkpoint_active_health()
    entry = next(e for e in pack_app.catalog.entries()
                 if e.character_id == "demo")
    pack_app._switch_character(entry)
    active_before = pack_app._selection_store.get("active")
    lkg_before = pack_app._selection_store.get("last_known_good")
    data_dir = pack_app._data_dir
    pack_app.shutdown()

    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    reborn = PetApplication(
        argv=["retirement-pet"], data_dir=data_dir, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-restart-{tmp_path.name}",
    )
    try:
        renderer = reborn.window._renderer
        assert renderer is not reborn.renderer  # not the engine cat anymore
        assert renderer.character_fqid().endswith(".demo")
        assert reborn._selection_store.get("active") == active_before
        assert reborn._selection_store.get("last_known_good") == lkg_before
    finally:
        reborn.shutdown()


# -- P0 remediation: resolver follows the ACTIVE character's capabilities -------


def test_switch_updates_resolver_capabilities(pack_app, qt_application):
    """After switching, facts resolve through the ACTIVE character.

    The demo character (minimal-static) supports ONLY core.idle: a work
    fact must keep its meaning but render as THIS character's idle with a
    missing-semantics marker - never silently borrow the cat's work body.
    """
    entry = next(e for e in pack_app.catalog.entries()
                 if e.character_id == "demo")
    assert pack_app._switch_character(entry) is True

    from retirement_pet.runtime_state import CORE_IDLE, ContextId

    caps = pack_app.bridge._capabilities
    assert caps.character_fqid.endswith(".demo")
    assert caps.supports(CORE_IDLE)
    assert not caps.supports("core.work")

    pack_app.contexts.set(ContextId.WORKING, "rhythm")
    perf = pack_app.resolver.resolve(pack_app.capabilities)
    assert perf.semantic == CORE_IDLE
    assert perf.source == "context_fallback"
    assert "core.work" in perf.missing_semantics
    pack_app.contexts.clear(ContextId.WORKING, "rhythm")


@pytest.mark.parametrize(
    ("action_id", "context_name", "owner", "old_effect"),
    [
        pytest.param("work", "WORKING", "rhythm", None, id="work"),
        pytest.param("rest", "RESTING", "rhythm", "zzz", id="rest"),
        pytest.param("music", "MUSIC", "audio", "notes", id="music"),
        pytest.param("meeting", "MEETING", "schedule", None, id="meeting"),
    ],
)
def test_active_fact_reconciles_immediately_when_character_changes(
        pack_app, qt_application, monkeypatch,
        action_id, context_name, owner, old_effect):
    """A successful swap must resolve already-true facts exactly now.

    No later timer, context mutation or user input may be required to stop an
    action that the newly-active character cannot render.
    """
    from retirement_pet.models import ActionId
    from retirement_pet.runtime_state import CORE_IDLE, ContextId

    action = ActionId(action_id)
    context = ContextId[context_name]
    if action is ActionId.MUSIC:
        monkeypatch.setattr(pack_app.audio, "is_playing", lambda: True)
    assert pack_app._service_port.request(action, owner)
    assert pack_app.contexts.owner_of(context) == owner
    assert pack_app.controller.current_action() is action
    if old_effect is not None:
        assert old_effect in pack_app._compose_snapshot(0).overlay.effects

    demo = next(e for e in pack_app.catalog.entries()
                if e.character_id == "demo")
    official = next(e for e in pack_app.catalog.entries()
                    if e.character_id == "cat" and e.builtin)
    assert pack_app._switch_character(demo)

    # The fact is global and remains true, but the idle-only demo must not
    # keep executing or decorating the old character's action.
    assert pack_app.contexts.owner_of(context) == owner
    assert pack_app.controller.current_action() is None
    perf = pack_app.bridge.last_performance
    assert perf.semantic == CORE_IDLE
    assert perf.source == "context_fallback"
    assert perf.missing_semantics == (context.semantic,)
    snapshot = pack_app._compose_snapshot(0)
    assert snapshot.action is ActionId.IDLE
    if old_effect is not None:
        assert old_effect not in snapshot.overlay.effects

    # Switching back reuses the same still-true fact immediately.
    assert pack_app._switch_character(official)
    assert pack_app.contexts.owner_of(context) == owner
    assert pack_app.controller.current_action() is action
    restored = pack_app.bridge.last_performance
    assert restored.semantic == context.semantic
    assert restored.source == "context"
    assert restored.missing_semantics == ()


def test_successful_character_switch_reconciles_exactly_once(
        pack_app, qt_application, monkeypatch):
    demo = next(e for e in pack_app.catalog.entries()
                if e.character_id == "demo")
    calls = []
    real_apply = pack_app.bridge.apply

    def counted_apply(reason):
        calls.append(reason)
        return real_apply(reason)

    monkeypatch.setattr(pack_app.bridge, "apply", counted_apply)
    assert pack_app._switch_character(demo)
    assert calls == ["character_capabilities_changed"]


def test_failed_character_switch_does_not_reconcile_or_change_performance(
        pack_app, qt_application, monkeypatch):
    from retirement_pet.models import ActionId
    from retirement_pet.petpack.identity import PackKey, RevisionKey
    from retirement_pet.runtime_state import ContextId

    assert pack_app._service_port.request(ActionId.WORK, "rhythm")
    before = (
        pack_app.capabilities,
        pack_app.bridge._capabilities,
        pack_app.bridge.last_performance,
        pack_app.controller.current_action(),
        pack_app.window._renderer,
    )
    calls = []
    real_apply = pack_app.bridge.apply

    def counted_apply(reason):
        calls.append(reason)
        return real_apply(reason)

    monkeypatch.setattr(pack_app.bridge, "apply", counted_apply)
    missing = type("MissingEntry", (), {})()
    missing.revision_key = RevisionKey(
        pack=PackKey("community.example", "missing"),
        package_version="1.0.0",
        content_digest="0" * 64,
    )
    missing.character_id = "demo"
    missing.display_name = "不存在的角色"

    assert pack_app._switch_character(missing) is False
    assert calls == []
    assert (
        pack_app.capabilities,
        pack_app.bridge._capabilities,
        pack_app.bridge.last_performance,
        pack_app.controller.current_action(),
        pack_app.window._renderer,
    ) == before
    assert pack_app.contexts.owner_of(ContextId.WORKING) == "rhythm"


def test_indeterminate_switch_failure_reconciles_bootstrap_capabilities(
        pack_app, qt_application, monkeypatch):
    """Safe mode is a renderer change and needs the same semantic publish."""
    from retirement_pet.models import ActionId
    from retirement_pet.runtime_state import ContextId

    demo = next(e for e in pack_app.catalog.entries()
                if e.character_id == "demo")
    official = next(e for e in pack_app.catalog.entries()
                    if e.character_id == "cat" and e.builtin)
    assert pack_app._switch_character(demo)
    assert pack_app._service_port.request(ActionId.WORK, "rhythm")
    assert pack_app.controller.current_action() is None

    calls = []
    real_apply = pack_app.bridge.apply

    def counted_apply(reason):
        calls.append(reason)
        return real_apply(reason)

    def fail_closed(_request, surface):
        pack_app.switcher.enter_safe_mode(surface)
        return False

    monkeypatch.setattr(pack_app.bridge, "apply", counted_apply)
    monkeypatch.setattr(pack_app.switcher, "swap_and_commit", fail_closed)
    assert pack_app._switch_character(official) is False

    assert pack_app.switcher.in_safe_mode
    assert pack_app.window._renderer is pack_app.renderer
    assert pack_app.capabilities.character_fqid.endswith(".cat")
    assert pack_app.contexts.owner_of(ContextId.WORKING) == "rhythm"
    assert pack_app.controller.current_action() is ActionId.WORK
    assert pack_app.bridge.last_performance.semantic == "core.work"
    assert calls == ["character_capabilities_changed"]


def test_cas_loser_reconciles_authoritative_runtime_even_when_request_is_false(
        pack_app, qt_application, monkeypatch):
    """False means this request lost, not necessarily that the body stayed."""
    from retirement_pet.models import ActionId
    from retirement_pet.runtime_state import CORE_IDLE, ContextId

    assert pack_app._service_port.request(ActionId.WORK, "rhythm")
    demo = next(e for e in pack_app.catalog.entries()
                if e.character_id == "demo")
    calls = []
    real_apply = pack_app.bridge.apply

    def counted_apply(reason):
        calls.append(reason)
        return real_apply(reason)

    def render_authoritative_but_lose(request, surface):
        authoritative = request.candidate
        surface.swap_renderer(authoritative)
        pack_app.switcher.current_runtime = authoritative
        pack_app.switcher.in_safe_mode = False
        request.candidate = None
        return False

    monkeypatch.setattr(pack_app.bridge, "apply", counted_apply)
    monkeypatch.setattr(
        pack_app.switcher, "swap_and_commit", render_authoritative_but_lose)
    assert pack_app._switch_character(demo) is False

    assert pack_app.window._renderer is pack_app.switcher.current_runtime
    assert pack_app.capabilities.character_fqid.endswith(".demo")
    assert pack_app.contexts.owner_of(ContextId.WORKING) == "rhythm"
    assert pack_app.controller.current_action() is None
    assert pack_app.bridge.last_performance.semantic == CORE_IDLE
    assert calls == ["character_capabilities_changed"]


def test_failed_request_while_already_safe_does_not_reconcile_unchanged_state(
        pack_app, qt_application, monkeypatch):
    demo = next(e for e in pack_app.catalog.entries()
                if e.character_id == "demo")
    pack_app.switcher.enter_safe_mode(pack_app.window)
    pack_app._sync_capabilities()
    before = (
        pack_app.switcher.current_runtime,
        pack_app.switcher.in_safe_mode,
        pack_app.capabilities,
        pack_app.bridge.last_performance,
    )
    calls = []
    real_apply = pack_app.bridge.apply

    def counted_apply(reason):
        calls.append(reason)
        return real_apply(reason)

    monkeypatch.setattr(pack_app.bridge, "apply", counted_apply)
    monkeypatch.setattr(
        pack_app.switcher, "swap_and_commit",
        lambda _request, _surface: False)
    assert pack_app._switch_character(demo) is False
    assert calls == []
    assert (
        pack_app.switcher.current_runtime,
        pack_app.switcher.in_safe_mode,
        pack_app.capabilities,
        pack_app.bridge.last_performance,
    ) == before


def test_builtin_official_cat_registered_on_startup(app, qt_application):
    """Empty library startup: the embedded official pack is READY (P0-A)."""
    from retirement_pet.lifecycle import PackLibrary  # noqa: F401

    builtins = app.library.builtin_revisions()
    assert builtins, "embedded official pack must be READY after startup"
    assert {record.revision_key.package_version for record in builtins} == {
        "1.0.0", "1.0.1",
    }
    assert all(
        record.revision_key.pack.package_id == "retirement-cat-official"
        and record.pack_path.is_file()
        for record in builtins
    )
    active = app._selection_store.get("active")
    assert active is not None and active.package_version == "1.0.1"


def _activate_exact_legacy_builtin(pet):
    from retirement_pet.embedded_pack import LEGACY_CONTENT_DIGEST

    record = next(
        record for record in pet.library.list_revisions()
        if record.revision_key.content_digest == LEGACY_CONTENT_DIGEST
    )
    request = pet.switcher.request(record.revision_key, "cat")
    assert pet.switcher.prepare(request) is not None
    assert pet.switcher.swap_and_commit(request, pet.window)
    assert pet.switcher.checkpoint_active_health()
    active = pet._selection_store.get("active")
    lkg = pet._selection_store.get("last_known_good")
    assert active == lkg
    return active, lkg


def test_exact_legacy_builtin_migrates_atomically_and_restart_is_idempotent(
        app, qt_application, tmp_path):
    old_active, old_lkg = _activate_exact_legacy_builtin(app)
    data_dir = app._data_dir
    app.shutdown()

    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    migrated = PetApplication(
        argv=["retirement-pet"], data_dir=data_dir, clock=FakeClock(),
        headless=True, instance_name=f"pytest-v101-migrate-{tmp_path.name}",
    )
    try:
        active = migrated._selection_store.get("active")
        assert active.package_version == "1.0.1"
        assert active.content_digest != old_active.content_digest
        assert active.generation > old_active.generation
        assert active.commit_sequence > old_active.commit_sequence
        assert migrated._selection_store.get("last_known_good") == old_lkg
        assert migrated.window.renderer is migrated.switcher.current_runtime
        assert migrated.switcher.current_runtime.content_digest() == \
            active.content_digest
        visible_official = [
            entry for entry in migrated.catalog.entries()
            if entry.package_id == "retirement-cat-official"
        ]
        assert [entry.package_version for entry in visible_official] == [
            "1.0.1"]
    finally:
        migrated.shutdown()

    restarted = PetApplication(
        argv=["retirement-pet"], data_dir=data_dir, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-v101-idempotent-{tmp_path.name}",
    )
    try:
        assert restarted._selection_store.get("active") == active
        assert restarted._selection_store.get("last_known_good") == old_lkg
    finally:
        restarted.shutdown()


def test_legacy_migration_prepare_failure_keeps_old_renderer_active_and_lkg(
        app, qt_application, tmp_path, monkeypatch):
    old_active, old_lkg = _activate_exact_legacy_builtin(app)
    data_dir = app._data_dir
    app.shutdown()

    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock
    from retirement_pet.switcher import RuntimeSwitcher

    real_prepare = RuntimeSwitcher.prepare

    def fail_only_v101(self, request, *, authority_recovery=False):
        if request.revision_key.package_version == "1.0.1":
            return None
        return real_prepare(
            self, request, authority_recovery=authority_recovery)

    monkeypatch.setattr(RuntimeSwitcher, "prepare", fail_only_v101)
    reborn = PetApplication(
        argv=["retirement-pet"], data_dir=data_dir, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-v101-failure-{tmp_path.name}",
    )
    try:
        assert reborn._selection_store.get("active") == old_active
        assert reborn._selection_store.get("last_known_good") == old_lkg
        assert reborn.window.renderer is reborn.switcher.current_runtime
        assert reborn.switcher.current_runtime.content_digest() == \
            old_active.content_digest
    finally:
        reborn.shutdown()


def test_external_active_character_is_never_taken_by_builtin_migration(
        pack_app, qt_application, tmp_path):
    external = next(
        entry for entry in pack_app.catalog.entries()
        if entry.package_id == "minimal-static"
    )
    assert pack_app._switch_character(external)
    before = pack_app._selection_store.get("active")
    data_dir = pack_app._data_dir
    pack_app.shutdown()

    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    reborn = PetApplication(
        argv=["retirement-pet"], data_dir=data_dir, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-v101-external-{tmp_path.name}",
    )
    try:
        assert reborn._selection_store.get("active") == before
        assert reborn.switcher.current_runtime.character_fqid().endswith(
            ".demo")
    finally:
        reborn.shutdown()


@pytest.mark.parametrize(
    "catalog_kind",
    ["corrupt", "too_new", "malformed_selection", "malformed_value",
     "malformed_intent", "wrong_constraints"],
)
def test_catalog_failure_still_starts_bootstrap_without_overwriting_database(
        tmp_path, qt_application, monkeypatch, catalog_kind):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    library_dir = tmp_path / "library"
    library_dir.mkdir(parents=True)
    database = library_dir / "state.db"
    if catalog_kind == "corrupt":
        database.write_bytes(b"not-a-sqlite-database\x00frozen")
    elif catalog_kind == "too_new":
        conn = sqlite3.connect(database)
        conn.execute(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta VALUES ('schema_version', '99')")
        conn.commit()
        conn.close()
    else:
        from retirement_pet.lifecycle import PackLibrary
        from retirement_pet.switcher import ActiveSelectionStore

        prepared = PackLibrary(library_dir)
        if catalog_kind == "malformed_selection":
            with prepared._db:
                prepared._db.execute(
                    "CREATE TABLE active_selection ("
                    "slot TEXT PRIMARY KEY, publisher_id TEXT)")
        elif catalog_kind == "malformed_value":
            store = ActiveSelectionStore(prepared)
            with store._db:
                store._db.execute(
                    "INSERT INTO active_selection VALUES "
                    "(?,?,?,?,?,?,?,?,?,?)",
                    ("active", "publisher", "package", "1.0.0", "a" * 64,
                     "publisher.package.series.character", None, None,
                     "not-an-integer", 1),
                )
            store.close()
        elif catalog_kind == "wrong_constraints":
            with prepared._db:
                prepared._db.execute("DROP TABLE revisions")
                prepared._db.execute(
                    "CREATE TABLE revisions ("
                    "publisher_id TEXT NOT NULL, package_id TEXT NOT NULL,"
                    "package_version TEXT NOT NULL,"
                    "content_digest TEXT NOT NULL, pack_path TEXT NOT NULL,"
                    "installed_at TEXT NOT NULL,"
                    "character_count INTEGER NOT NULL,"
                    "trust_channel TEXT NOT NULL,"
                    "builtin INTEGER NOT NULL DEFAULT 0)")
        else:
            (prepared.journal_dir / "intent-malformed.json").write_text(
                '{"transaction_id":"malformed"}', encoding="utf-8")
        prepared.close()
    frozen_bytes = database.read_bytes()

    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-bootstrap-{catalog_kind}-{tmp_path.name}",
    )
    try:
        assert pet.library.degraded is True
        assert pet.window is not None
        assert pet.tray is not None
        assert pet.window._renderer is pet.renderer
        assert pet._selection_store.get("active") is None
        assert pet._selection_store.get("last_known_good") is None
        menu = pet._build_menu()
        assert menu.actions()
        menu.deleteLater()
    finally:
        pet.shutdown()
        pet.shutdown()
    assert database.read_bytes() == frozen_bytes


def test_embedded_validator_failure_keeps_bootstrap_window_and_menu(
        tmp_path, qt_application, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    import retirement_pet.app as app_module
    from retirement_pet.clock import FakeClock
    from retirement_pet.lifecycle import LifecycleError

    def fail_validation(_library):
        raise LifecycleError("PPK-LCY-E001", "forced embedded failure")

    monkeypatch.setattr(
        app_module, "ensure_builtin_official", fail_validation)
    pet = app_module.PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-validator-{tmp_path.name}",
    )
    try:
        assert pet.library.degraded is True
        assert pet.window._renderer is pet.renderer
        assert pet._build_menu().actions()
    finally:
        pet.shutdown()


def test_library_reparse_trust_anchor_uses_bootstrap_without_touching_outside(
        tmp_path, qt_application, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    import retirement_pet.lifecycle as lifecycle_module
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    library_root = (tmp_path / "library").absolute()
    outside = tmp_path / "outside-sentinel.bin"
    outside.write_bytes(b"outside-bootstrap-sentinel")
    monkeypatch.setattr(
        lifecycle_module,
        "_path_is_reparse",
        lambda path: Path(path).absolute() == library_root,
        raising=False,
    )

    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-reparse-bootstrap-{tmp_path.name}",
    )
    try:
        assert pet.library.degraded is True
        assert pet.window._renderer is pet.renderer
        assert pet._build_menu().actions()
        assert outside.read_bytes() == b"outside-bootstrap-sentinel"
    finally:
        pet.shutdown()


def test_startup_lkg_repairs_active_with_complete_ready_selection(
        app, qt_application, tmp_path):
    assert app.switcher.checkpoint_active_health()
    valid = app._selection_store.get("last_known_good")
    assert valid is not None
    invalid = replace(
        valid,
        character_fqid="forged.publisher.series.cat",
        generation=valid.generation + 20,
        commit_sequence=valid.commit_sequence + 20,
    )
    app._selection_store.commit(invalid, "active")
    app.shutdown()

    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    reborn = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-lkg-repair-{tmp_path.name}",
    )
    try:
        active = reborn._selection_store.get("active")
        lkg = reborn._selection_store.get("last_known_good")
        assert active is not None and lkg is not None
        assert active.character_fqid == valid.character_fqid
        assert lkg == valid
        assert active.generation > invalid.generation
        assert active.commit_sequence > invalid.commit_sequence
        assert reborn.library.get_revision(active.revision_key()) is not None
        assert reborn.library.get_revision(lkg.revision_key()) is not None
        assert reborn.window._renderer.character_fqid() == active.character_fqid
    finally:
        reborn.shutdown()


def test_restart_active_health_checkpoint_waits_for_first_paint_observation(
        pack_app, qt_application, tmp_path):
    entry = next(e for e in pack_app.catalog.entries()
                 if e.character_id == "demo")
    assert pack_app._switch_character(entry)
    expected_active = pack_app._selection_store.get("active")
    lkg_before = pack_app._selection_store.get("last_known_good")
    data_dir = pack_app._data_dir
    pack_app.shutdown()

    from retirement_pet.app import (
        ACTIVE_HEALTH_OBSERVATION_MS,
        PetApplication,
    )
    from retirement_pet.clock import FakeClock

    reborn = PetApplication(
        argv=["retirement-pet"], data_dir=data_dir, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-health-observe-{tmp_path.name}",
    )
    try:
        assert reborn._health_observation_expected == expected_active
        assert reborn._selection_store.get("last_known_good") == lkg_before
        assert reborn._health_observation_timer.isSingleShot()
        assert reborn._health_observation_timer.interval() == \
            ACTIVE_HEALTH_OBSERVATION_MS
        assert reborn._health_observation_timer.parent() is reborn.window
        assert not reborn._health_observation_timer.isActive()

        reborn.run()
        # Exercise the production signal connection, not a test-only direct
        # checkpoint call.  The actual 30-second wait is completed explicitly.
        reborn.window.first_paint.emit()
        assert reborn._health_observation_timer.isActive()
        assert reborn._selection_store.get("last_known_good") == lkg_before

        reborn._complete_active_health_observation()
        assert not reborn._health_observation_timer.isActive()
        assert reborn._selection_store.get("last_known_good") == expected_active
    finally:
        reborn.shutdown()


def test_switch_before_health_timeout_does_not_promote_replacement(
        pack_app, qt_application, tmp_path):
    demo = next(e for e in pack_app.catalog.entries()
                if e.character_id == "demo")
    assert pack_app._switch_character(demo)
    restored_active = pack_app._selection_store.get("active")
    lkg_before = pack_app._selection_store.get("last_known_good")
    data_dir = pack_app._data_dir
    pack_app.shutdown()

    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    reborn = PetApplication(
        argv=["retirement-pet"], data_dir=data_dir, clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-health-switch-{tmp_path.name}",
    )
    try:
        assert reborn._health_observation_expected == restored_active
        reborn.run()
        reborn.window.first_paint.emit()
        assert reborn._health_observation_timer.isActive()

        replacement = next(e for e in reborn.catalog.entries()
                           if e.character_id != "demo")
        assert reborn._switch_character(replacement)
        replacement_active = reborn._selection_store.get("active")
        assert replacement_active != restored_active

        reborn._complete_active_health_observation()
        assert reborn._selection_store.get("last_known_good") == lkg_before
        assert reborn._selection_store.get("active") == replacement_active
    finally:
        reborn.shutdown()


@pytest.mark.parametrize("pause_kind", ["hidden", "suspended"])
def test_health_observation_counts_only_visible_unsuspended_time(
        pack_app, qt_application, tmp_path, pause_kind):
    demo = next(e for e in pack_app.catalog.entries()
                if e.character_id == "demo")
    assert pack_app._switch_character(demo)
    expected_active = pack_app._selection_store.get("active")
    lkg_before = pack_app._selection_store.get("last_known_good")
    data_dir = pack_app._data_dir
    pack_app.shutdown()

    from retirement_pet.app import (
        ACTIVE_HEALTH_OBSERVATION_MS,
        PetApplication,
    )
    from retirement_pet.clock import FakeClock

    reborn = PetApplication(
        argv=["retirement-pet"], data_dir=data_dir, clock=FakeClock(),
        headless=True,
        instance_name=(
            f"pytest-health-pause-{pause_kind}-{tmp_path.name}"),
    )
    try:
        reborn.run()
        reborn.window.first_paint.emit()
        assert reborn._health_observation_timer.isActive()

        if pause_kind == "hidden":
            reborn._hide_window()
        else:
            reborn._on_session_suspended(True)
        assert not reborn._health_observation_timer.isActive()

        # Even a stale queued timeout cannot promote while observation is
        # paused.  The frozen tuple remains eligible for a fresh full interval.
        reborn._health_observation_timer.timeout.emit()
        assert reborn._selection_store.get("last_known_good") == lkg_before
        assert reborn._health_observation_expected == expected_active

        if pause_kind == "hidden":
            reborn._show_window()
        else:
            reborn._on_session_suspended(False)
        assert reborn._health_observation_timer.isActive()
        assert reborn._health_observation_timer.remainingTime() <= \
            ACTIVE_HEALTH_OBSERVATION_MS
        assert reborn._health_observation_timer.remainingTime() > \
            ACTIVE_HEALTH_OBSERVATION_MS - 1_000

        reborn._complete_active_health_observation()
        assert reborn._selection_store.get("last_known_good") == expected_active
    finally:
        reborn.shutdown()


def test_shutdown_detaches_window_owned_timers_and_delayed_startup(
        tmp_path, qt_application, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    pet = PetApplication(
        argv=["retirement-pet", "--startup"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-qt-shutdown-{tmp_path.name}",
    )
    assert pet._health_observation_timer.parent() is pet.window
    assert pet._startup_timer.parent() is pet.window
    pet.run()
    assert pet._startup_timer.isActive()
    pet._health_observation_expected = pet._selection_store.get("active")

    pet.shutdown()

    assert not pet._health_observation_timer.isActive()
    assert not pet._startup_timer.isActive()
    assert pet._health_observation_expected is None
    assert pet._native_filter_installed is False
    assert pet._todo_hotkey_filter_installed is False
    assert pet._qt_lifecycle_connected is False
    pet.window.hide()
    pet._startup_timer.timeout.emit()
    assert not pet.window.isVisible()
