"""SystemMediaBridge (V13-05): observe and control OTHER players.

The bridge talks to the operating system's public media session layer
(Windows Global System Media Transport Controls).  It is deliberately a
separate component from :class:`AudioManager`, which keeps playing the
user's own local files; the UI and the agent protocol always say which of
"本地播放" or "系统媒体" they are controlling.

Frozen privacy boundary (V1.3 work order §9):

- no accounts, cookies, playback history, playlist files, lyric caches or
  any player-private API is read - only the public system session layer;
- media titles/artists are truncated summaries for display; they never
  enter ordinary logs or acceptance evidence;
- no global media keys are sent.  Commands go to the explicitly selected
  system media session through its public controls; there is no key-based
  broadcast fallback in this version at all;
- default OFF.  When disabled, every subscription and system object is
  released; while hidden/suspended the bridge stops refresh work instead
  of polling.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

#: display truncation for low-sensitive media metadata summaries
TITLE_MAX_CHARS = 40
APP_NAME_MAX_CHARS = 24
#: event coalescing window: bursts of property/playback events trigger at
#: most one rescan per window (no free-running polling loop exists)
RESCAN_DEBOUNCE_S = 0.3

STATUS_PLAYING = "playing"
STATUS_PAUSED = "paused"
STATUS_STOPPED = "stopped"
STATUS_CHANGING = "changing"
STATUS_CLOSED = "closed"
STATUS_OPENED = "opened"
STATUS_UNKNOWN = "unknown"


def _truncate(value: str | None, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


@dataclass(frozen=True)
class MediaSessionSummary:
    """Low-sensitive summary of one system media session."""

    session_id: str
    app_name: str
    title: str
    artist: str
    status: str
    can_play_pause: bool
    can_next: bool
    can_previous: bool


@dataclass(frozen=True)
class MediaSnapshot:
    bridge_available: bool
    reason: str
    sessions: tuple[MediaSessionSummary, ...]
    active: MediaSessionSummary | None
    local_playback_active: bool
    enabled: bool

    def to_payload(self) -> dict:
        """Agent/status payload: app name, status and controls only."""
        sessions = [
            {
                "session_id": session.session_id,
                "app_name": session.app_name,
                "status": session.status,
                "can_play_pause": session.can_play_pause,
                "can_next": session.can_next,
                "can_previous": session.can_previous,
            }
            for session in self.sessions
        ]
        return {
            "bridge_available": self.bridge_available,
            "reason": self.reason,
            "enabled": self.enabled,
            "local_playback_active": self.local_playback_active,
            "controlling": ("local" if self.local_playback_active
                            else "system" if self.active is not None
                            else "none"),
            "sessions": sessions,
            "active_session_id": (self.active.session_id
                                  if self.active else None),
        }


class MediaBridgeProvider:
    """Abstract session source.  Implementations run their own thread."""

    name = "abstract"

    def availability(self) -> tuple[bool, str]:
        raise NotImplementedError

    def start(self, on_event: Callable[[], None]) -> bool:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> tuple[MediaSessionSummary, ...] | None:
        raise NotImplementedError

    def active_session_id(self) -> str | None:
        raise NotImplementedError

    def play_pause(self, session_id: str | None) -> tuple[bool, str]:
        raise NotImplementedError

    def skip_next(self, session_id: str | None) -> tuple[bool, str]:
        raise NotImplementedError

    def skip_previous(self, session_id: str | None) -> tuple[bool, str]:
        raise NotImplementedError


class UnavailableProvider(MediaBridgeProvider):
    """Honest placeholder when the OS layer cannot be used at all."""

    name = "unavailable"

    def __init__(self, reason: str):
        self._reason = reason

    def availability(self) -> tuple[bool, str]:
        return False, self._reason

    def start(self, on_event: Callable[[], None]) -> bool:
        return False

    def stop(self) -> None:
        return None

    def snapshot(self):
        return None

    def active_session_id(self):
        return None

    def play_pause(self, session_id):
        return False, "media bridge unavailable"

    skip_next = play_pause
    skip_previous = play_pause


class SyntheticMediaProvider(MediaBridgeProvider):
    """Scripted provider for tests: no OS access, synchronous control."""

    name = "synthetic"

    def __init__(self):
        self._sessions: dict[str, MediaSessionSummary] = {}
        self._active_id: str | None = None
        self._on_event: Callable[[], None] | None = None
        self._running = False
        self.commands: list[tuple[str, str | None]] = []
        self.command_result: tuple[bool, str] = (True, "ok")
        self.command_fail_for: set[str] = set()
        self.command_delay_s: float = 0.0
        self.rescans = 0
        self.paused = False

    def availability(self) -> tuple[bool, str]:
        return True, ""

    def start(self, on_event: Callable[[], None]) -> bool:
        self._running = True
        self._on_event = on_event
        return True

    def stop(self) -> None:
        self._running = False
        self._on_event = None

    @property
    def running(self) -> bool:
        return self._running

    def snapshot(self):
        if not self._running:
            return None
        return tuple(self._sessions.values())

    def active_session_id(self):
        return self._active_id

    def set_paused(self, paused: bool) -> None:
        self.paused = bool(paused)
        if not self.paused and self._on_event is not None:
            self._on_event()  # resume re-sync

    def _command(self, verb: str,
                 session_id: str | None) -> tuple[bool, str]:
        self.commands.append((verb, session_id))
        if self.command_delay_s > 0:
            time.sleep(self.command_delay_s)
        if not self._sessions:
            return False, "no active system media session"
        if verb in self.command_fail_for:
            return False, "player refused"
        return self.command_result

    def play_pause(self, session_id):
        return self._command("play_pause", session_id)

    def skip_next(self, session_id):
        return self._command("next", session_id)

    def skip_previous(self, session_id):
        return self._command("previous", session_id)

    # -- test scripting ------------------------------------------------------

    def set_session(self, summary: MediaSessionSummary,
                    *, active: bool | None = None) -> None:
        self._sessions[summary.session_id] = summary
        if active is True or (active is None and not self._sessions):
            self._active_id = summary.session_id
        if self._on_event is not None:
            self._on_event()

    def remove_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        if self._active_id == session_id:
            self._active_id = None
        if self._on_event is not None:
            self._on_event()

    def emit_rescan(self) -> None:
        """Test hook: simulate the worker scheduling one refresh pass."""
        self.rescans += 1
        if self._on_event is not None and not self.paused:
            self._on_event()

    def mutate_session(self, session_id: str, **changes) -> None:
        current = self._sessions.get(session_id)
        if current is None:
            return
        self._sessions[session_id] = replace(current, **changes)
        if self._on_event is not None:
            self._on_event()


class _SmtcWorker:
    """Background asyncio worker owning the WinRT session manager.

    Runs on its own daemon thread; every interaction with WinRT happens
    inside its event loop.  Events are coalesced into rescans; there is no
    free-running polling timer.
    """

    def __init__(self, on_event: Callable[[], None]):
        import asyncio

        self._asyncio = asyncio
        self._on_event = on_event
        self._loop: "asyncio.AbstractEventLoop | None" = None
        self._thread: threading.Thread | None = None
        self._manager = None
        self._manager_token = None
        self._session_tokens: dict[str, tuple] = {}
        self._summaries: dict[str, MediaSessionSummary] = {}
        self._active_id: str | None = None
        self._lock = threading.Lock()
        self._rescan_scheduled = False
        self._stopped = threading.Event()
        self._paused = threading.Event()
        self._ready = threading.Event()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> bool:
        try:
            self._thread = threading.Thread(
                target=self._run, name="rp-media-bridge", daemon=True)
            self._thread.start()
            # fail fast when the platform layer rejects us outright
            if not self._ready.wait(timeout=10):
                self.stop()
                return False
            with self._lock:
                return self._manager is not None
        except Exception:  # noqa: BLE001 - provider must never crash the app
            logger.exception("media bridge worker failed to start")
            self.stop()
            return False

    def rebind(self, on_event: Callable[[], None]) -> None:
        """Re-point the event callback (e.g. after a probe start)."""
        self._on_event = on_event

    def set_paused(self, paused: bool) -> None:
        """Suspend/resume worker refresh work (hidden/suspended gate)."""
        if paused:
            self._paused.set()
        else:
            self._paused.clear()
            # re-sync once on resume so missed events are not lost
            self._schedule_rescan()

    def _run(self) -> None:
        asyncio = self._asyncio
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            ok = loop.run_until_complete(self._initialize())
            self._ready.set()
            if ok:
                loop.run_forever()
        except Exception:  # noqa: BLE001 - logged, bridge reports degraded
            logger.exception("media bridge worker loop failed")
        finally:
            self._release_sync()
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

    async def _initialize(self) -> bool:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Mgr,
        )

        try:
            self._manager = await Mgr.request_async()
        except Exception as exc:  # noqa: BLE001 - honest unavailability
            logger.info("media bridge unavailable: %s",
                        type(exc).__name__)
            self._manager = None
            return False
        self._manager_token = self._manager.add_sessions_changed(
            self._on_sessions_changed)
        await self._rescan()
        return True

    def stop(self) -> None:
        self._stopped.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self._thread = None

    def _release_sync(self) -> None:
        manager = self._manager
        self._manager = None
        if manager is not None and self._manager_token is not None:
            try:
                manager.remove_sessions_changed(self._manager_token)
            except Exception:  # noqa: BLE001
                pass
        self._manager_token = None
        for tokens in self._session_tokens.values():
            for remove, token in tokens:
                try:
                    remove(token)
                except Exception:  # noqa: BLE001
                    pass
        self._session_tokens.clear()
        with self._lock:
            self._summaries.clear()
            self._active_id = None

    # -- events ----------------------------------------------------------------

    def _on_sessions_changed(self, *_args) -> None:
        self._schedule_rescan()

    def _on_session_changed(self, _session, session_id: str,
                            *_args) -> None:
        del _session, session_id
        self._schedule_rescan()

    def _schedule_rescan(self) -> None:
        loop = self._loop
        if loop is None or self._stopped.is_set() or self._rescan_scheduled:
            return
        if self._paused.is_set():
            return  # hidden/suspended: refresh work stays paused (P2)
        self._rescan_scheduled = True

        def arm() -> None:
            loop.call_later(RESCAN_DEBOUNCE_S, self._run_rescan)

        try:
            loop.call_soon_threadsafe(arm)
        except RuntimeError:
            self._rescan_scheduled = False

    def _run_rescan(self) -> None:
        self._rescan_scheduled = False
        if self._stopped.is_set() or self._loop is None:
            return
        self._asyncio.ensure_future(self._rescan())

    # -- rescans -----------------------------------------------------------------

    async def _rescan(self) -> None:
        manager = self._manager
        if manager is None:
            return
        try:
            sessions = manager.get_sessions()
            found: dict[str, object] = {}
            for session in sessions:
                session_id = self._stable_id(
                    session.source_app_user_model_id)
                found[session_id] = session
                if session_id not in self._session_tokens:
                    self._subscribe(session, session_id)
            current = manager.get_current_session()
            active_id = self._stable_id(
                current.source_app_user_model_id) if current else None
            summaries: dict[str, MediaSessionSummary] = {}
            for session_id, session in found.items():
                summaries[session_id] = await self._summarize(session)
            for gone in set(self._session_tokens) - set(found):
                self._unsubscribe(gone)
            with self._lock:
                self._summaries = summaries
                self._active_id = active_id if active_id in summaries \
                    else None
        except Exception:  # noqa: BLE001 - degrade, keep subscriptions
            logger.exception("media bridge rescan failed")
            return
        self._emit()

    def _subscribe(self, session, session_id: str) -> None:
        tokens = []
        for add_name, remove_name in (
                ("add_media_properties_changed",
                 "remove_media_properties_changed"),
                ("add_playback_info_changed",
                 "remove_playback_info_changed")):
            add = getattr(session, add_name)
            token = add(
                lambda s, *a, _sid=session_id:
                self._on_session_changed(s, _sid, *a))
            tokens.append((getattr(session, remove_name), token))
        self._session_tokens[session_id] = tuple(tokens)

    def _unsubscribe(self, session_id: str) -> None:
        for remove, token in self._session_tokens.pop(session_id, ()):
            try:
                remove(token)
            except Exception:  # noqa: BLE001
                pass

    async def _summarize(self, session) -> MediaSessionSummary:
        aumid = session.source_app_user_model_id or ""
        session_id = self._stable_id(aumid)
        app_name = _truncate(self._app_name(aumid), APP_NAME_MAX_CHARS)
        title = artist = ""
        status = STATUS_UNKNOWN
        can_pause = can_next = can_previous = False
        try:
            props = await session.try_get_media_properties_async()
            title = _truncate(props.title, TITLE_MAX_CHARS)
            artist = _truncate(props.artist, TITLE_MAX_CHARS)
        except Exception:  # noqa: BLE001 - metadata is optional
            pass
        try:
            info = session.get_playback_info()
            status = self._status_name(info)
            controls = info.controls
            can_pause = bool(getattr(controls, "is_play_enabled", False))                 or bool(getattr(controls, "is_pause_enabled", False))
            can_next = bool(controls.is_next_enabled)
            can_previous = bool(controls.is_previous_enabled)
        except Exception:  # noqa: BLE001
            pass
        return MediaSessionSummary(
            session_id=session_id, app_name=app_name, title=title,
            artist=artist, status=status, can_play_pause=can_pause,
            can_next=can_next, can_previous=can_previous)

    @staticmethod
    def _status_name(info) -> str:
        try:
            value = int(info.playback_status)
        except (TypeError, ValueError):
            return STATUS_UNKNOWN
        # GlobalSystemMediaTransportControlsSessionPlaybackStatus values:
        # Closed=0, Opened=1, Changing=2, Stopped=3, Playing=4, Paused=5
        return {
            0: STATUS_CLOSED,
            1: STATUS_OPENED,
            2: STATUS_CHANGING,
            3: STATUS_STOPPED,
            4: STATUS_PLAYING,
            5: STATUS_PAUSED,
        }.get(value, STATUS_UNKNOWN)

    @staticmethod
    def _stable_id(aumid: str) -> str:
        return hashlib.sha256((aumid or "").encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _app_name(aumid: str) -> str:
        # AUMID like "App.!App" or "Company.Package!App" -> "App"
        tail = aumid.rsplit("!", 1)[-1] if aumid else ""
        tail = tail.rsplit("\\", 1)[-1] if tail else aumid.split("!")[0] \
            if aumid else ""
        return tail or "media"

    def _emit(self) -> None:
        callback = self._on_event
        if callback is None:
            return
        try:
            callback()
        except Exception:  # noqa: BLE001 - never break the worker
            logger.exception("media bridge event callback failed")

    # -- reads/commands from the app thread -----------------------------------

    def snapshot(self):
        with self._lock:
            if not self._summaries and self._active_id is None:
                return () if self._manager is not None else None
            return tuple(self._summaries.values())

    def active_session_id(self):
        with self._lock:
            return self._active_id

    def _command(self, verb: str,
                 session_id: str | None) -> tuple[bool, str]:
        loop = self._loop
        if loop is None or not loop.is_running():
            return False, "media bridge not running"
        import asyncio

        future = asyncio.run_coroutine_threadsafe(
            self._command_coro(verb, session_id), loop)
        try:
            # a short bounded wait only: this runs on the Qt thread, so a
            # player that never answers must not stall the UI or an agent
            # request for seconds (review P2)
            return future.result(timeout=0.75)
        except asyncio.TimeoutError:
            return False, "player did not answer in time"
        except Exception as exc:  # noqa: BLE001 - honest failure
            return False, f"command failed: {type(exc).__name__}"

    async def _command_coro(self, verb: str,
                            session_id: str | None) -> tuple[bool, str]:
        manager = self._manager
        if manager is None:
            return False, "media bridge unavailable"
        session = None
        if session_id is None:
            session = manager.get_current_session()
        else:
            for candidate in manager.get_sessions():
                if self._stable_id(
                        candidate.source_app_user_model_id) == session_id:
                    session = candidate
                    break
        if session is None:
            return False, "no active system media session"
        if verb == "play_pause":
            # SMTC exposes separate play/pause buttons: a toggle targets
            # whichever one opposes the CURRENT status.  A "changing"
            # transition is treated as playing so the toggle always tends
            # toward pausing an active player.
            status = self._status_name(session.get_playback_info())
            playing = status in (STATUS_PLAYING, STATUS_CHANGING)
            method = (session.try_pause_async if playing
                      else session.try_play_async)
        else:
            method = {
                "next": session.try_skip_next_async,
                "previous": session.try_skip_previous_async,
            }[verb]
        try:
            accepted = await method()
        except Exception:  # noqa: BLE001 - players may refuse anything
            return False, "player refused the command"
        return bool(accepted), "ok" if accepted else "player did not accept"

    def play_pause(self, session_id):
        return self._command("play_pause", session_id)

    def skip_next(self, session_id):
        return self._command("next", session_id)

    def skip_previous(self, session_id):
        return self._command("previous", session_id)


class WindowsSmtcProvider(MediaBridgeProvider):
    """Windows SMTC provider (public system media session layer)."""

    name = "windows-smtc"

    def __init__(self):
        self._worker: _SmtcWorker | None = None

    def availability(self) -> tuple[bool, str]:
        if self._worker is not None:
            return True, ""
        try:
            import winrt.windows.media.control  # noqa: F401
        except ImportError:
            return False, "winrt projections not installed"
        except OSError as exc:
            return False, f"system layer unavailable: {type(exc).__name__}"
        return True, ""

    def start(self, on_event: Callable[[], None]) -> bool:
        if self._worker is not None:
            # already running: rebind the event callback (the previous
            # owner may have been a probe with a null sink)
            self._worker.rebind(on_event)
            return True
        worker = _SmtcWorker(on_event)
        if not worker.start():
            return False
        self._worker = worker
        return True

    def stop(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.stop()

    def snapshot(self):
        worker = self._worker
        return worker.snapshot() if worker is not None else None

    def active_session_id(self):
        worker = self._worker
        return worker.active_session_id() if worker is not None else None

    def play_pause(self, session_id):
        worker = self._worker
        return (False, "media bridge not running") if worker is None \
            else worker.play_pause(session_id)

    def skip_next(self, session_id):
        worker = self._worker
        return (False, "media bridge not running") if worker is None \
            else worker.skip_next(session_id)

    def skip_previous(self, session_id):
        worker = self._worker
        return (False, "media bridge not running") if worker is None \
            else worker.skip_previous(session_id)



class SystemMediaBridge(QObject):
    """Application-facing bridge: settings gate, projection and commands.

    All provider callbacks are marshalled onto the Qt thread via the
    private ``_provider_event`` signal; the bridge then refreshes its
    cached snapshot and projects the ``core.music`` context with the
    guarded owner ``media_bridge`` so it can never fight the local audio
    manager's own fact.
    """

    _provider_event = Signal()

    changed = Signal()

    def __init__(self, provider: MediaBridgeProvider, settings,
                 contexts, parent: QObject | None = None):
        super().__init__(parent)
        self._provider = provider
        self._settings = settings
        self._contexts = contexts
        self._enabled = False
        self._local_playback = False
        self._app_active = True
        self._snapshot: MediaSnapshot | None = None
        self._provider_event.connect(self._on_provider_event)

    # -- lifecycle -------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def set_enabled(self, enabled: bool) -> bool:
        """Start or fully stop the bridge per the user's explicit choice."""
        if enabled == self._enabled:
            return True
        if enabled:
            available, reason = self._provider.availability()
            if not available:
                self._enabled = False
                self._start_failure = ""
                self._refresh(reason=reason)
                self.changed.emit()
                return False
            if not self._provider.start(self._emit_provider_event):
                self._enabled = False
                # a race can make availability() and start() disagree;
                # the snapshot must carry the REAL outcome, not a stale
                # "available" from the earlier probe.  Emitting changed
                # here lets the settings page show the true reason
                # (final recheck #2).
                self._start_failure = (
                    "system media session layer refused at start")
                self._refresh(reason=self._start_failure)
                self.changed.emit()
                return False
            self._start_failure = ""
            self._enabled = True
            logger.info("media bridge enabled (provider=%s)",
                        self._provider.name)
        else:
            self._provider.stop()
            self._enabled = False
            # releases subscriptions, system objects AND the context fact
            self._contexts.clear(
                _music_context_id(), owner="media_bridge")
            logger.info("media bridge disabled; subscriptions released")
        self._refresh()
        self.changed.emit()
        return True

    def is_enabled(self) -> bool:
        return self._enabled

    def shutdown(self) -> None:
        if self._enabled:
            self.set_enabled(False)

    # -- runtime gates -----------------------------------------------------------

    def set_local_playback_active(self, active: bool) -> None:
        if active == self._local_playback:
            return
        self._local_playback = bool(active)
        # the cached snapshot carries local_playback_active: refresh it
        # NOW or the UI/payload keep the stale "controlling" answer until
        # the next unrelated system media event (acceptance F-#2)
        self._refresh()
        self._project()
        self.changed.emit()

    def set_app_active(self, active: bool) -> None:
        """Hidden/suspended: actually PAUSE provider refresh work and
        resume with a re-sync, not merely gate the projection (P2)."""
        self._app_active = bool(active)
        pause = getattr(self._provider, "set_paused", None)
        if pause is not None:
            pause(not self._app_active)
        if self._app_active:
            self._refresh()
            self._project()
            self.changed.emit()

    def _emit_provider_event(self) -> None:
        # called from the provider worker thread; queued to the Qt thread
        self._provider_event.emit()

    # -- snapshot ------------------------------------------------------------------

    def snapshot(self) -> MediaSnapshot:
        if self._snapshot is None:
            self._refresh()
        snapshot = self._snapshot
        assert snapshot is not None
        return snapshot

    def _refresh(self, *, reason: str = "") -> None:
        available, why = self._provider.availability()
        if not self._enabled:
            self._snapshot = MediaSnapshot(
                bridge_available=available,
                reason=reason or why,
                sessions=(), active=None,
                local_playback_active=self._local_playback,
                enabled=False)
            return
        sessions = self._provider.snapshot()
        if sessions is None:
            self._snapshot = MediaSnapshot(
                bridge_available=False, reason=why or "unavailable",
                sessions=(), active=None,
                local_playback_active=self._local_playback, enabled=True)
            return
        active_id = self._provider.active_session_id()
        active = next((s for s in sessions if s.session_id == active_id),
                      None)
        self._snapshot = MediaSnapshot(
            bridge_available=True, reason="", sessions=tuple(sessions),
            active=active, local_playback_active=self._local_playback,
            enabled=True)

    def _on_provider_event(self) -> None:
        if not self._enabled:
            return
        if not self._app_active:
            # hidden/suspended: refresh work pauses (work order §9);
            # set_app_active(True) refreshes on resume
            return
        self._refresh()
        self._project()
        self.changed.emit()

    def _project(self) -> None:
        """Project real system playback into the core.music context."""
        snapshot = self.snapshot()
        # live gate (not the cached copy): local playback always wins
        playing = (self._enabled
                   and snapshot.active is not None
                   and snapshot.active.status == STATUS_PLAYING
                   and not self._local_playback)
        if playing:
            self._contexts.set(_music_context_id(), "media_bridge")
        else:
            self._contexts.clear(_music_context_id(), owner="media_bridge")

    # -- commands ---------------------------------------------------------------------

    def play_pause(self, session_id: str | None = None):
        return self._command(self._provider.play_pause, session_id)

    def skip_next(self, session_id: str | None = None):
        return self._command(self._provider.skip_next, session_id)

    def skip_previous(self, session_id: str | None = None):
        return self._command(self._provider.skip_previous, session_id)

    #: bounded media-command wait, in the Qt thread, BY DESIGN and BY
    #: DOCUMENTED COMMITMENT (review RR13-05): each command may freeze
    #: the caller for at most this long.  This is a conscious trade-off,
    #: NOT an async implementation; docs/AGENT_PROTOCOL.md records the
    #: same figure.  Each command runs on its OWN daemon thread, so a
    #: never-answering player cannot occupy a shared worker and poison
    #: later commands.
    _COMMAND_TIMEOUT_S = 0.75

    def _command(self, method, session_id):
        if not self._enabled:
            return False, "media bridge is disabled"
        result: dict = {}

        def run() -> None:
            try:
                result["answer"] = method(session_id)
            except Exception as exc:  # noqa: BLE001 - honest failure
                result["answer"] = (False,
                                    f"command failed: {type(exc).__name__}")

        # one daemon thread per command: a never-answering player cannot
        # occupy a shared worker, cannot block shutdown, and the caller
        # waits at most _COMMAND_TIMEOUT_S (documented commitment)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(self._COMMAND_TIMEOUT_S)
        if "answer" not in result:
            return False, "player did not answer in time"
        accepted, message = result["answer"]
        # report the player's real answer; never claim success for a
        # request that was merely delivered
        return bool(accepted), message


def _music_context_id():
    from retirement_pet.runtime_state import ContextId

    return ContextId.MUSIC


def default_provider() -> MediaBridgeProvider:
    """Pick the platform provider; honest fallback elsewhere."""
    import sys

    if sys.platform == "win32":
        return WindowsSmtcProvider()
    return UnavailableProvider(f"unsupported platform: {sys.platform}")
