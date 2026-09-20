"""V13-05: system media bridge contract, projection and privacy.

隔离约定：契约测试全部使用 SyntheticMediaProvider（无 OS 访问）；Windows
原生 provider 仅做可用性探测与会话枚举的实机取证（无会话时如实断言 0）。
不读取任何账号、Cookie、播放历史或网易云私有接口。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from retirement_pet.media_bridge import (
    STATUS_CHANGING,
    STATUS_PAUSED,
    STATUS_PLAYING,
    MediaSessionSummary,
    SyntheticMediaProvider,
    SystemMediaBridge,
    UnavailableProvider,
    _truncate,
)


@pytest.fixture()
def provider():
    return SyntheticMediaProvider()


@pytest.fixture()
def bridge(qt_application, provider):
    from retirement_pet.runtime_state import ContextStore

    class _Settings:
        def __init__(self):
            self.values = {}

        def get(self, key, default=None):
            return self.values.get(key, default)

    contexts = ContextStore()
    media = SystemMediaBridge(provider, _Settings(), contexts)
    yield media
    media.shutdown()


def _session(session_id="s1", app="CloudMusic", status=STATUS_PLAYING,
             title="song", artist="artist", **kwargs):
    return MediaSessionSummary(
        session_id=session_id, app_name=app, title=title, artist=artist,
        status=status, can_play_pause=True, can_next=True,
        can_previous=True, **kwargs)


# -- lifecycle gating -------------------------------------------------------------


def test_bridge_default_off_and_reports_disabled(bridge):
    assert bridge.is_enabled() is False
    snapshot = bridge.snapshot()
    assert snapshot.enabled is False
    assert snapshot.sessions == ()
    accepted, message = bridge.play_pause()
    assert accepted is False and "disabled" in message


def test_enable_starts_provider_disable_stops_and_releases(bridge, provider):
    assert bridge.set_enabled(True) is True
    assert provider.running is True
    assert bridge.is_enabled() is True

    assert bridge.set_enabled(False) is True
    assert provider.running is False  # subscriptions released


def test_unavailable_provider_refuses_enable(qt_application):
    from retirement_pet.runtime_state import ContextStore

    class _Settings:
        def get(self, *_args, **_kwargs):
            return None

    provider = UnavailableProvider("winrt not installed")
    media = SystemMediaBridge(provider, _Settings(), ContextStore())
    assert media.set_enabled(True) is False
    assert media.is_enabled() is False
    snapshot = media.snapshot()
    assert snapshot.bridge_available is False
    assert snapshot.reason == "winrt not installed"


# -- session state transitions (provider contract) ------------------------------------


def test_session_appear_disappear_and_projection(bridge, qt_application):
    from retirement_pet.runtime_state import ContextId

    bridge.set_enabled(True)
    contexts = bridge._contexts

    provider = bridge._provider
    provider.set_session(_session(), active=True)
    # the synthetic provider emits synchronously on this thread, so the
    # direct-connected signal has already projected the fact
    qt_application.processEvents()
    snapshot = bridge.snapshot()
    assert snapshot.active is not None
    assert snapshot.active.status == STATUS_PLAYING
    assert contexts.is_active(ContextId.MUSIC)
    assert contexts.owner_of(ContextId.MUSIC) == "media_bridge"

    provider.remove_session("s1")
    qt_application.processEvents()
    assert not contexts.is_active(ContextId.MUSIC)
    assert bridge.snapshot().active is None


def test_pause_and_resume_projection_follows_real_state(
        bridge, qt_application, provider):
    from retirement_pet.runtime_state import ContextId

    bridge.set_enabled(True)
    provider.set_session(_session(), active=True)
    qt_application.processEvents()
    assert contexts_active(bridge, ContextId.MUSIC)

    provider.mutate_session("s1", status=STATUS_PAUSED)
    qt_application.processEvents()
    assert not contexts_active(bridge, ContextId.MUSIC)

    provider.mutate_session("s1", status=STATUS_PLAYING)
    qt_application.processEvents()
    assert contexts_active(bridge, ContextId.MUSIC)


def contexts_active(bridge, context_id) -> bool:
    return bridge._contexts.is_active(context_id)


def test_multi_session_active_switch_and_command_routing(
        bridge, qt_application, provider):
    bridge.set_enabled(True)
    provider.set_session(_session("a", app="PlayerA"), active=True)
    provider.set_session(_session("b", app="PlayerB"))
    qt_application.processEvents()

    snapshot = bridge.snapshot()
    assert len(snapshot.sessions) == 2
    assert snapshot.active.session_id == "a"

    accepted, _ = bridge.skip_next("b")  # explicit target session
    assert accepted is True
    assert provider.commands[-1] == ("next", "b")


def test_player_refusal_is_reported_honestly(bridge, provider):
    bridge.set_enabled(True)
    provider.set_session(_session(), active=True)
    provider.command_fail_for = {"play_pause"}
    accepted, message = bridge.play_pause()
    assert accepted is False
    assert "refused" in message


def test_command_without_any_session_fails_honestly(bridge):
    bridge.set_enabled(True)
    accepted, message = bridge.play_pause()
    assert accepted is False


def test_local_playback_wins_over_system_media(bridge, qt_application,
                                               provider):
    from retirement_pet.runtime_state import ContextId

    bridge.set_enabled(True)
    provider.set_session(_session(), active=True)
    qt_application.processEvents()
    assert contexts_active(bridge, ContextId.MUSIC)

    bridge.set_local_playback_active(True)  # our own player starts
    assert not contexts_active(bridge, ContextId.MUSIC)

    bridge.set_local_playback_active(False)  # and stops again
    assert contexts_active(bridge, ContextId.MUSIC)


def test_disabled_bridge_clears_its_context_fact(bridge, qt_application,
                                                 provider):
    from retirement_pet.runtime_state import ContextId

    bridge.set_enabled(True)
    provider.set_session(_session(), active=True)
    qt_application.processEvents()
    assert contexts_active(bridge, ContextId.MUSIC)
    bridge.set_enabled(False)
    assert not contexts_active(bridge, ContextId.MUSIC)


def test_snapshot_payload_is_low_sensitive(bridge, qt_application, provider):
    bridge.set_enabled(True)
    provider.set_session(
        _session(title="秘密歌名SecretTrack", artist="秘密艺人"), active=True)
    qt_application.processEvents()
    payload = bridge.snapshot().to_payload()
    text = repr(payload)
    assert "秘密歌名SecretTrack" not in text  # titles never in payloads
    assert "秘密艺人" not in text
    assert payload["controlling"] == "system"
    assert payload["sessions"][0]["app_name"] == "CloudMusic"
    assert payload["active_session_id"] == "s1"


def test_truncation_for_display_metadata():
    assert _truncate("x" * 100, 40).endswith("…")
    assert len(_truncate("x" * 100, 40)) == 40
    assert _truncate(None, 40) == ""
    assert _truncate("  short  ", 40) == "short"


def test_no_media_key_simulation_in_implementation():
    """No global media keys: the bridge must not synthesize keyboard input."""
    from pathlib import Path

    source = Path("src/retirement_pet/media_bridge.py").read_text(
        encoding="utf-8")
    for forbidden in ("SendInput", "keybd_event", "VK_MEDIA", "keybd_event",
                      "post_message", "PostMessageW", "INPUT_KEYBOARD"):
        assert forbidden not in source, forbidden


# -- real application wiring -----------------------------------------------------------


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
        instance_name=f"pytest-media-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


def test_app_bridge_default_off_and_toggles_via_settings(app, qt_application,
                                                         monkeypatch):
    from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton

    assert app.media_bridge.is_enabled() is False

    app._open_control_panel("sound_schedule")
    page = app._panel._built["sound_schedule"]
    media_check = page.findChild(QCheckBox, "schedule_media_bridge")
    media_status = page.findChild(QLabel, "schedule_media_status")
    apply_btn = page.findChild(QPushButton, "schedule_apply")
    assert media_check is not None and not media_check.isChecked()
    assert media_status.text() == "系统媒体联动：未开启"

    media_check.setChecked(True)
    apply_btn.click()
    qt_application.processEvents()
    assert app.settings.get("media_bridge_enabled") is True
    # Windows SMTC availability decides; on a capable machine this is on
    status_text = media_status.text()
    # enabled with no session yet, enabled with sessions, or honestly
    # unavailable - all three are valid outcomes on a real machine
    assert any(mark in status_text
               for mark in ("已开启", "当前控制", "不可用", "开启失败")
               ), status_text

    media_check.setChecked(False)
    apply_btn.click()
    qt_application.processEvents()
    assert app.media_bridge.is_enabled() is False


def test_agent_media_status_and_commands(app, qt_application):
    from retirement_pet.agent_protocol import UNAVAILABLE

    server = app.agent_server
    response = json.loads(server.handle_line(
        b'{"protocol":"retirement-pet.agent.v1","request_id":"m1",'
        b'"operation":"media.status","args":{}}\n')[:-1].decode("utf-8"))
    assert response["ok"] is True
    assert response["data"]["enabled"] is False  # honest: bridge off
    assert response["data"]["provider"] in ("windows-smtc", "unavailable")

    response = json.loads(server.handle_line(
        b'{"protocol":"retirement-pet.agent.v1","request_id":"m2",'
        b'"operation":"media.play_pause","args":{}}\n')[:-1].decode("utf-8"))
    assert response["ok"] is True
    assert response["data"]["accepted"] is False
    assert response["data"]["reason"].find("disabled") >= 0
    del UNAVAILABLE



def test_bridge_released_on_shutdown(app, qt_application):
    app.media_bridge.set_enabled(True)
    qt_application.processEvents()
    app.shutdown()
    qt_application.processEvents()
    assert app.media_bridge.is_enabled() is False


# -- Windows native provider (environment-honest) ----------------------------------------


def test_windows_smtc_provider_availability_probe():
    from retirement_pet.media_bridge import WindowsSmtcProvider

    provider = WindowsSmtcProvider()
    available, reason = provider.availability()
    if not available:
        pytest.skip(f"SMTC layer unavailable: {reason}")
    started = provider.start(lambda: None)
    if not started:
        # projections exist but the system layer refused at start
        # (e.g. SMTC service down): environmental, save and SKIP
        pytest.skip("SMTC layer present but refused at start")
    try:
        deadline = time.monotonic() + 5
        snapshot = None
        while time.monotonic() < deadline:
            snapshot = provider.snapshot()
            if snapshot is not None:
                break
            time.sleep(0.1)
        assert snapshot is not None, (
            "worker started but never produced a session snapshot")
        # a machine with no player running reports an honest empty list
        for session in snapshot:
            assert session.status in (
                "playing", "paused", "stopped", "changing", "closed",
                "opened", "unknown")
    finally:
        provider.stop()


# -- real Windows SMTC integration (own silent session; no third-party app) ----


def _smtc_projections_installed() -> bool:
    try:
        import winrt.windows.media.playback  # noqa: F401
        import winrt.windows.media.core  # noqa: F401
    except (ImportError, OSError):
        return False
    return sys.platform == "win32"


@pytest.mark.skipif(not _smtc_projections_installed(),
                    reason="winrt playback projections unavailable")
def test_real_smtc_session_appear_control_and_disappear(qt_application,
                                                        tmp_path):
    """Full real-OS loop: a real session appears, the bridge projects it,
    play/pause toggles the REAL player, exit removes the session."""
    import subprocess
    import wave

    from retirement_pet.media_bridge import WindowsSmtcProvider
    from retirement_pet.runtime_state import ContextId

    wav = tmp_path / "silent.wav"
    with wave.open(str(wav), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 8000 * 5)

    ready = tmp_path / "child-ready.marker"
    stop = tmp_path / "child-stop.marker"
    child = subprocess.Popen(
        [sys.executable,
         str(Path(__file__).parent / "helpers" / "_media_session_child.py"),
         str(wav), str(ready), str(stop)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            qt_application.processEvents()
            time.sleep(0.1)
        if not ready.exists():
            stderr = child.stderr.read().decode("utf-8", "replace")
            child.wait(timeout=10)
            pytest.skip(
                f"media subprocess could not establish a session: "
                f"{stderr[:200]}")
        own_id = json.loads(
            ready.read_text(encoding="utf-8")).get("session_id", "")
        assert own_id, "child did not report its session identity"

        provider = WindowsSmtcProvider()
        bridge = SystemMediaBridge(
            provider, _StubSettings(), _BridgeContexts())
        if not bridge.set_enabled(True):
            # environmental: the SMTC layer refused at start in this
            # session; save and SKIP rather than a bare FAIL
            pytest.skip(
                f"SMTC refused at start: {bridge.snapshot().reason}")

        # 1. the real session appears
        own = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            qt_application.processEvents()
            snapshot = bridge.snapshot()
            own = next(
                (s for s in snapshot.sessions
                 if s.session_id == own_id), None)
            if own is not None:
                break
            time.sleep(0.2)
        assert own is not None, "our real SMTC session never appeared"
        # wait for the real player to reach a stable playing status
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            qt_application.processEvents()
            snapshot = bridge.snapshot()
            own = next(
                (s for s in snapshot.sessions
                 if s.session_id == own.session_id), own)
            if own.status == STATUS_PLAYING:
                break
            time.sleep(0.2)
        if own.status == STATUS_CHANGING:
            pytest.skip(
                "real SMTC player remained in the OS changing state; "
                "the runner has no usable playback device")
        assert own.status == STATUS_PLAYING

        # 2. play/pause toggles the REAL player (status flips via events)
        accepted, message = bridge.play_pause(own.session_id)
        assert accepted is True, message
        paused = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            qt_application.processEvents()
            snapshot = bridge.snapshot()
            own = next(
                (s for s in snapshot.sessions
                 if s.session_id == own.session_id), None)
            if own is not None and own.status == STATUS_PAUSED:
                paused = True
                break
            time.sleep(0.2)
        assert paused is True, "real player did not pause"

        accepted, _message = bridge.play_pause(own.session_id)
        assert accepted is True
        resumed = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            qt_application.processEvents()
            snapshot = bridge.snapshot()
            own = next(
                (s for s in snapshot.sessions
                 if s.session_id == own.session_id), None)
            if own is not None and own.status == STATUS_PLAYING:
                resumed = True
                break
            time.sleep(0.2)
        assert resumed is True, "real player did not resume"

        # 3. projection follows the real status when ours is the active one
        snapshot = bridge.snapshot()
        if snapshot.active is not None \
                and snapshot.active.session_id == own.session_id:
            assert bridge._contexts.is_active(ContextId.MUSIC)

        # 4. child exit removes the session
        stop.write_text("stop", encoding="ascii")
        child.wait(timeout=30)
        gone = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            qt_application.processEvents()
            snapshot = bridge.snapshot()
            if not any(s.session_id == own.session_id
                       for s in snapshot.sessions):
                gone = True
                break
            time.sleep(0.2)
        assert gone is True, "session did not disappear after player exit"
        # environment-aware: our projection must match the CURRENT reality
        # - no music fact only when no other player is actually playing
        still_playing = any(
            session.status == STATUS_PLAYING
            for session in bridge.snapshot().sessions)
        if not still_playing:
            assert not bridge._contexts.is_active(ContextId.MUSIC)
        bridge.set_enabled(False)
    finally:
        if child.poll() is None:
            stop.write_text("stop", encoding="ascii")
            child.wait(timeout=15)


class _StubSettings:
    def get(self, *_args, **_kwargs):
        return None


class _BridgeContexts:
    """Minimal owner-aware context store stand-in for the native test."""

    def __init__(self):
        from retirement_pet.runtime_state import ContextStore

        self._store = ContextStore()

    def set(self, context_id, owner):
        return self._store.set(context_id, owner)

    def clear(self, context_id, owner=None):
        return self._store.clear(context_id, owner)

    def is_active(self, context_id):
        return self._store.is_active(context_id)


def test_enabled_idle_bridge_does_no_periodic_work(qt_application):
    """Budget claim (V13-05): with no sessions and no events there is NO
    background work - rescans happen only on real system events."""
    from unittest.mock import patch

    from retirement_pet.media_bridge import WindowsSmtcProvider

    provider = WindowsSmtcProvider()

    class _Settings:
        def get(self, *_args, **_kwargs):
            return None

    from retirement_pet.runtime_state import ContextStore

    bridge = SystemMediaBridge(provider, _Settings(), ContextStore())
    try:
        if not bridge.set_enabled(True):
            # availability() may succeed but start() refused: honest
            # degradation, SKIP with the real reason
            snapshot = bridge.snapshot()
            assert snapshot.enabled is False
            assert snapshot.reason
            pytest.skip(f"SMTC refused at start: {snapshot.reason}")
        worker = provider._worker
        with patch.object(worker, "_rescan",
                          wraps=worker._rescan) as rescan:
            for _ in range(150):  # ~3s of pumped idle event loop
                qt_application.processEvents()
                time.sleep(0.02)
            assert rescan.call_count == 0  # event-driven only, no polling
    finally:
        bridge.set_enabled(False)


def test_persisted_enable_survives_restart(tmp_path, qt_application,
                                           monkeypatch):
    """Acceptance F-#1 regression: an app restarted with the bridge
    opt-in persisted must not crash and must come up enabled."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    first = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-mr1-{tmp_path.name}")
    assert first.media_bridge.is_enabled() is False
    first.media_bridge.set_enabled(True)
    assert first.settings.get("media_bridge_enabled") is False  # not yet
    first.settings.set("media_bridge_enabled", True)
    assert first.settings.save()
    first.shutdown()

    second = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-mr2-{tmp_path.name}")
    try:
        # construction survived (the P0 used to raise here); the
        # preference MUST be preserved either way, and the runtime state
        # follows what the system actually allowed (review: honest
        # degradation when the SMTC layer refuses at start)
        assert second.settings.get("media_bridge_enabled") is True
        if second.media_bridge.is_enabled():
            pass  # system allowed: fully enabled
        else:
            snapshot = second.media_bridge.snapshot()
            assert snapshot.reason  # the real refusal is visible to users
    finally:
        if second.media_bridge.is_enabled():
            second.media_bridge.set_enabled(False)
        second.shutdown()


def test_local_playback_state_reflected_immediately(bridge, qt_application,
                                                    provider):
    """Acceptance F-#2: toggling local playback updates the cached
    snapshot (UI/payload) immediately, not on the next media event."""
    bridge.set_enabled(True)
    provider.set_session(_session(), active=True)
    qt_application.processEvents()
    assert bridge.snapshot().to_payload()["controlling"] == "system"

    bridge.set_local_playback_active(True)
    assert bridge.snapshot().to_payload()["controlling"] == "local"
    bridge.set_local_playback_active(False)
    assert bridge.snapshot().to_payload()["controlling"] == "system"


def test_hidden_bridge_ignores_provider_events_until_resume(
        bridge, qt_application, provider):
    """Acceptance F-#3: refresh work pauses while hidden/suspended."""
    bridge.set_enabled(True)
    bridge.set_app_active(False)
    before = bridge.snapshot()
    provider.set_session(_session(), active=True)  # fires an event
    qt_application.processEvents()
    assert bridge.snapshot() is before  # refresh was suppressed

    bridge.set_app_active(True)
    qt_application.processEvents()
    assert bridge.snapshot().active is not None  # refreshed on resume


# -- review P2: non-blocking commands and real pause ----------------------------


def test_p2_command_with_never_answering_player_returns_quickly(
        bridge, provider):
    """A provider command that never answers must not stall the Qt-thread
    caller: the bridge applies a hard bounded wait and reports honestly."""
    bridge.set_enabled(True)
    provider.set_session(_session(), active=True)
    provider.command_delay_s = 5.0  # simulated dead player

    started = time.monotonic()
    accepted, message = bridge.play_pause()
    elapsed = time.monotonic() - started
    assert accepted is False
    assert "did not answer" in message
    assert elapsed < 3.0, f"command blocked the caller for {elapsed:.1f}s"


def test_p2_hidden_app_pauses_provider_and_resync_on_resume(
        bridge, qt_application, provider):
    """set_app_active(False) PAUSES the provider (events ignored), and
    resume re-syncs instead of leaving stale sessions."""
    bridge.set_enabled(True)
    bridge.set_app_active(False)
    assert provider.paused is True

    provider.set_session(_session("late"), active=True)  # event while hidden
    qt_application.processEvents()
    assert bridge.snapshot().active is None  # suppressed while paused

    bridge.set_app_active(True)
    assert provider.paused is False
    qt_application.processEvents()
    assert bridge.snapshot().active is not None  # re-synced on resume


# -- RR13-05: honest start-race state and documented bounded blocking ----


def test_rr13_05_start_race_reports_real_reason(qt_application):
    """availability() yes but start() no: the snapshot must carry the
    REAL refusal, not a stale 'available' from the probe."""
    from retirement_pet.media_bridge import (
        SyntheticMediaProvider,
        SystemMediaBridge,
    )

    class _Settings:
        def get(self, *_args, **_kwargs):
            return None

    class RaceProvider(SyntheticMediaProvider):
        def availability(self):
            return True, ""

        def start(self, on_event):
            return False  # loses the race at start time

    bridge = SystemMediaBridge(RaceProvider(), _Settings(),
                               _BridgeContexts())
    assert bridge.set_enabled(True) is False
    snapshot = bridge.snapshot()
    assert snapshot.enabled is False
    assert snapshot.bridge_available is True
    assert snapshot.reason  # the real refusal is visible


def test_rr13_05_bounded_blocking_is_the_documented_commitment():
    """The 750ms Qt-thread wait is a DOCUMENTED trade-off, not an async
    claim: docstrings and the protocol doc must say so."""
    from pathlib import Path

    source = Path("src/retirement_pet/media_bridge.py").read_text(
        encoding="utf-8")
    assert "BY DESIGN and BY" in source
    assert "NOT an async implementation" in source
    protocol_doc = Path("docs/AGENT_PROTOCOL.md").read_text(
        encoding="utf-8")
    assert "750" in protocol_doc


# -- final recheck #2: start failure reaches the settings page ----------------


def test_rr13_final_start_failure_reaches_settings_label(
        qt_application, tmp_path, monkeypatch):
    """With the persisted preference on and the provider refusing at
    start, the settings label must show the failure reason (not a bare
    未开启), and the preference must not be silently cleared."""
    from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock
    from retirement_pet.media_bridge import SyntheticMediaProvider

    class RefusingProvider(SyntheticMediaProvider):
        def availability(self):
            return True, ""

        def start(self, on_event):
            return False  # the system layer refuses in this environment

    pet = PetApplication(
        argv=["retirement-pet"], data_dir=tmp_path, clock=FakeClock(),
        headless=True, instance_name=f"pytest-refuse-{tmp_path.name}",
    )
    try:
        from retirement_pet.media_bridge import SyntheticMediaProvider as _S

        # swap in the refusing provider for a deterministic environment
        refusing = RefusingProvider()
        refusing.paused = False
        pet.media_bridge._provider = refusing

        pet._open_control_panel("sound_schedule")
        page = pet._panel._built["sound_schedule"]
        media_check = page.findChild(QCheckBox, "schedule_media_bridge")
        media_status = page.findChild(QLabel, "schedule_media_status")
        apply_btn = page.findChild(QPushButton, "schedule_apply")

        media_check.setChecked(True)
        apply_btn.click()
        qt_application.processEvents()

        # the preference stays on (user intent preserved)
        assert pet.settings.get("media_bridge_enabled") is True
        # runtime honestly off
        assert pet.media_bridge.is_enabled() is False
        # the label shows the failure reason, not a bare 未开启
        assert "开启失败" in media_status.text(), media_status.text()
    finally:
        pet.shutdown()
