"""Application wiring: composition root for every component.

The window stays dumb; this class owns construction, the unified animation
clock, menus, tray integration and the shutdown sequence (design 12).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu

from retirement_pet import APP_NAME, __version__
from retirement_pet.action_controller import ActionController
from retirement_pet.action_registry import (
    ActionRegistry,
    DND_SAFE_SEMANTICS,
    MEETING_SAFE_SEMANTICS,
)
from retirement_pet.activity_monitor import ActivityMonitor, IdleProvider
from retirement_pet.assets import AssetBundle
from retirement_pet.audio import AudioManager
from retirement_pet.clock import Clock, SystemClock
from retirement_pet.timeline import IdleResetReason, IdleTimeline
from retirement_pet.clocks import ServiceClock, VisualClock
from retirement_pet.config_system import UiConfigService
from retirement_pet.config_system import Source as UiConfigSource
from retirement_pet.context_adapter import (
    CAT_CLICKED,
    CAT_RANDOM_ACTIONS,
    ContextServicePort,
    PerformanceBridge,
    cat_capabilities,
)
from retirement_pet.countdown_module import RetirementCountdownModule
from retirement_pet.countdown import parse_target
from retirement_pet.embedded_pack import (
    builtin_official_revision,
    ensure_builtin_official,
    is_exact_legacy_builtin_selection,
)
from retirement_pet.global_hotkey import TodoHotkeyFilter
from retirement_pet.lifecycle import PackLibrary
from retirement_pet.lru_cache import LruByteCache
from retirement_pet.switcher import (
    ActiveSelectionStore,
    CharacterCatalog,
    RuntimeSwitcher,
    first_character_id,
)
from retirement_pet.layout_policy import resolve_layout
from retirement_pet.logging_setup import setup_logging
from retirement_pet.models import ActionId, LifeStage, OverlaySnapshot, RenderSnapshot
from retirement_pet.overlay import OverlayController
from retirement_pet.paths import (
    ensure_user_data_dir,
    log_dir,
    settings_file,
    state_file,
)
from retirement_pet.perf_markers import (
    FIRST_PET_PAINT,
    QT_READY,
    TRAY_READY,
    PerfMarkers,
)
from retirement_pet.random_actions import RandomActionScheduler
from retirement_pet.resource_path import asset_path
from retirement_pet.rhythm import RhythmController
from retirement_pet.runtime_state import (
    ContextId,
    ContextStore,
    EmoteController,
    PerformanceResolver,
)
from retirement_pet.schedule_manager import ScheduleManager
from retirement_pet.session_watch import SessionWatchFilter
from retirement_pet.settings import SettingsStore
from retirement_pet.single_instance import (
    QUIET_COMMAND,
    SHOW_COMMAND,
    SingleInstanceGuard,
)
from retirement_pet.startup import StartupManager, WinRegBackend
from retirement_pet.state_store import StateStore
from retirement_pet.text_profile import TextSafetyError, render as render_text
from retirement_pet.ui.countdown_panel import CountdownPanel
from retirement_pet.ui.pet_window import PetWindow
from retirement_pet.ui.renderer import CatRenderer
from retirement_pet.ui.settings_dialog import SettingsDialog
from retirement_pet.ui.tray import TrayController, make_tray_icon
from retirement_pet.todo.context_bridge import TodoContextBridge
from retirement_pet.todo.domain import FocusProjection
from retirement_pet.todo.errors import (
    SchemaTooNew,
    TaskStoreAvailability,
    TaskStoreUnavailable,
)

logger = logging.getLogger(__name__)

#: marks a settings key absent from the store (distinct from a stored None)
_ACTION_SETTING_ABSENT = object()

# A restored selection becomes last-known-good only after it has survived a
# real, shown-window paint and a quiet observation interval.
ACTIVE_HEALTH_OBSERVATION_MS = 30_000

# One independent UX campaign.  Do not tie this to ``__version__``: a patch
# release must not repeatedly ask users who already made an explicit choice.
CHARACTER_ONBOARDING_CAMPAIGN = "character-choice-1"
CHARACTER_ONBOARDING_SETTING = "character_onboarding_campaign"

# The one-click preview is LOCAL_IMPORTED content even though its bytes ship
# with the application.  Pin every identity layer so a replaced file cannot
# borrow the trusted wording/button of the bundled preview.
BUNDLED_PREVIEW_FILE = "realistic-retirement-cat-0.1.1.petpack"
BUNDLED_PREVIEW_ARCHIVE_SHA256 = (
    "ce80e7dd73726cdbb252f21d65538014fe10790dce7a44816ebb80bc26c2a964"
)
BUNDLED_PREVIEW_CONTENT_DIGEST = (
    "6c4b368ad79124bf5bc48e6d8190917c7031f923b2cf00f728c4c17b9c21260c"
)
BUNDLED_PREVIEW_PUBLISHER_ID = "community.retirementpet"
BUNDLED_PREVIEW_PACKAGE_ID = "realistic-retirement-cat"
BUNDLED_PREVIEW_PACKAGE_VERSION = "0.1.1"
BUNDLED_PREVIEW_CHARACTER_ID = "realistic-cat"
# CR-P01: the historical preview misses spec-6.1 publisher_ref and carries
# the versioned legacy-exemption warning (PPK-MAN-W002) on every load.
BUNDLED_PREVIEW_WARNING_CODES: tuple[str, ...] = ("PPK-MAN-W002",)

#: effects that persist while a given action is active
ACTION_EFFECTS = {
    ActionId.REST: "zzz",
    ActionId.MUSIC: "notes",
    ActionId.EXERCISE: "sweat",
}


class _UnavailableTodoService:
    """Stable, privacy-safe facade used after one failed initialization.

    Reads remain safe so the panel and tray can still be opened.  Writes fail
    explicitly instead of pretending to succeed or repeatedly probing a store
    whose ownership/safety preflight already failed.
    """

    __slots__ = ("_reason", "_projection")

    # The facade never rebuilt a store, so its generation never changes;
    # UI consumers treat it as a stable value (CR-U04).
    store_generation = 0
    # No real service exists behind the facade; agents comparing against
    # this always mismatch and must re-read (V13-01).
    data_generation = 0

    def __init__(self, reason: str):
        self._reason = str(reason)
        self._projection = FocusProjection(False, None)

    @property
    def degraded(self) -> bool:
        return True

    @property
    def degraded_backup(self):
        return None

    def all_tasks(self) -> list:
        return []

    def children_of(self, _parent_id) -> list:
        return []

    def by_horizon(self, _horizon, *, include_ancestors: bool = False) -> list:
        del include_ancestors
        return []

    def get(self, _task_id):
        return None

    def load_focus_only(self):
        return None

    def load_focus_projection(self) -> FocusProjection:
        return self._projection

    def focus_task_id(self):
        return None

    def focus_task(self):
        return None

    def _reject_mutation(self, *_args, **_kwargs):
        raise TaskStoreUnavailable(
            self._reason, stage="application")

    def subtree_summary(self, _task_id):
        # No store was ever opened, so no task can exist in one; the
        # confirmation preview fails closed like every other task read.
        from retirement_pet.todo import TodoError

        raise TodoError("task not found")

    add_task = _reject_mutation
    rename = _reject_mutation
    set_horizon = _reject_mutation
    set_due_date = _reject_mutation
    set_importance = _reject_mutation
    set_urgency = _reject_mutation
    set_note = _reject_mutation
    complete = _reject_mutation
    restore = _reject_mutation
    add_subtask = _reject_mutation
    move_within_siblings = _reject_mutation
    delete_subtree = _reject_mutation
    archive_subtree = _reject_mutation
    restore_archived = _reject_mutation
    start_focus = _reject_mutation
    stop_focus = _reject_mutation
    create_manual_backup = _reject_mutation
    restore_from_backup = _reject_mutation

    def list_backups(self) -> list:
        # no store was opened, so no backup of it can exist
        return []

    def note_for(self, _task_id) -> str:
        raise TaskStoreUnavailable(self._reason, stage="application")

    def close(self) -> None:
        return None


class PetApplication:
    def __init__(
        self,
        argv: list[str] | None = None,
        *,
        data_dir: Path | None = None,
        clock: Clock | None = None,
        idle_provider: IdleProvider | None = None,
        headless: bool = False,
        instance_name: str | None = None,
        todo_hotkey_backend=None,
    ):
        argv = list(sys.argv if argv is None else argv)
        self.debug = "--debug" in argv
        self.startup_mode = "--startup" in argv

        self._data_dir = ensure_user_data_dir(data_dir)
        setup_logging(log_dir(self._data_dir), debug=self.debug)
        logger.info("%s v%s starting (startup=%s)", APP_NAME, __version__, self.startup_mode)

        existing_app = QApplication.instance()
        self.qt_app = existing_app if existing_app is not None else QApplication(
            argv[:1] if argv else []
        )
        self._owns_qt_app = existing_app is None
        self.qt_app.setApplicationName(APP_NAME)
        self.qt_app.setApplicationVersion(__version__)
        self.qt_app.setQuitOnLastWindowClosed(False)
        self.qt_app.setWindowIcon(make_tray_icon())

        self._guard = SingleInstanceGuard(
            instance_name, allow_quit_command="--test-ipc-quit" in argv
        )
        self._guard.on_show_requested = self._show_from_second_instance
        self._guard.on_quit_requested = self._quit_from_ipc
        self._guard.on_hide_requested = self._hide_window
        self._guard.on_panel_requested = self._open_control_panel
        self._guard.on_panel_close_requested = self._close_control_panel
        # V13-01: the same local server multiplexes the versioned agent
        # JSON protocol; handlers run on this (application) thread and
        # call the real services below.
        from retirement_pet.agent_protocol import AgentProtocolServer

        self.agent_server = AgentProtocolServer(self)
        self._guard.on_agent_line = self.agent_server.handle_line
        notify_command = QUIET_COMMAND if self.startup_mode else SHOW_COMMAND
        if not self._guard.acquire(command=notify_command):
            logger.info("primary instance already running; exiting")
            self._second_instance = True
            return
        self._second_instance = False
        self._shutdown_complete = False

        self.clock = clock or SystemClock()
        self._rng = random.Random()
        self.perf = PerfMarkers(
            log_dir(self._data_dir) / "perf_markers.jsonl", clock=self.clock
        )
        self.perf.mark(QT_READY)

        self.settings = SettingsStore(settings_file(self._data_dir))
        self.settings.load()
        # V12-06: user-facing UI configuration (layout/visibility, text
        # templates, action modes) resolved on the frozen resolver priority
        # and persisted through the same settings file.
        self.ui_config = UiConfigService(self.settings)
        self._character_onboarding_handled_in_session = False
        # Verification state is reported only through the explicit
        # ``--report-window`` diagnostic hook.  A first report is emitted
        # when the pet appears; the second, atomic report proves that the
        # deferred one-time choice has either been offered or intentionally
        # skipped before an external harness inspects focus.
        self._character_onboarding_report_ready = False
        self._character_onboarding_panel_shown = False
        self._character_onboarding_report_state = "pending"
        self.state = StateStore(state_file(self._data_dir))
        self.state.load()

        # -- v2 runtime semantics (M2) ------------------------------------
        # Facts and performance resolution are separate from the executor.
        # Services speak facts through ContextServicePort; the bridge
        # re-resolves the single primary performance on every fact change.
        self.contexts = ContextStore()
        self.capabilities = cat_capabilities()
        self.resolver = PerformanceResolver(self.contexts)
        self._service_port = ContextServicePort(self.contexts)
        self.countdown_module = RetirementCountdownModule(
            self.settings, self.clock.now
        )

        self.controller = ActionController(ActionRegistry(), self.clock, self._rng)
        # CR-C06: one veto at the executor covers every request path
        # (context resolve, random scheduler, audio, panel, user) - the
        # per-action "disabled" mode must mean the pet never performs it.
        self.controller.set_request_gate(self._mode_request_gate)
        self._character_listeners: list = []
        # Idle has a first-class timeline: the controller only tracks
        # explicit actions, and pack idles must keep animating while none
        # is running (V12-01).
        self._idle_timeline = IdleTimeline(self.clock)
        self.controller.on_change(self._on_action_changed)
        self.overlay = OverlayController(self.clock, self._rng)
        self.emotes = EmoteController()
        self.emotes.bind_contexts(self.contexts)

        self.bridge = PerformanceBridge(
            self.contexts, self.resolver, self.controller, self.capabilities
        )
        self.emotes._on_expired = lambda: self.bridge.apply("emote_ended")

        # Todo has a permanently storage-free runtime boundary.  Merely
        # reading todo_bridge never creates tasks.db; a real service is opened
        # once on demand, or eagerly below only when a SQLite family member is
        # already present and therefore needs fail-closed inspection.
        self.todo_events: list[callable] = []
        self._todo_service = None
        self._todo_bridge_instance = TodoContextBridge(self.contexts)
        self._todo_init_attempted = False
        self.todo_availability = TaskStoreAvailability.ready()
        self.todo_error: Exception | None = None
        # Bounded, application-level store for unsaved todo note drafts
        # (CR-U03): kept drafts outlive the todo page being disposed with
        # the control panel and are re-offered when the page is rebuilt.
        self.todo_note_drafts: dict[str, str] = {}

        # Construct the programmatic Bootstrap renderer and pet surface before
        # touching any user-writable character storage.  A corrupt/newer
        # catalog, selection store or recovery journal must never prevent the
        # transparent window and its recovery menu from existing.
        self.bundle = AssetBundle()
        self.renderer = CatRenderer(self.bundle)
        self.panel = CountdownPanel()

        self.window = PetWindow(
            renderer=self.renderer,
            panel=self.panel,
            menu_builder=self._build_menu,
            always_on_top=bool(self.settings.get("always_on_top", True)),
            click_through=bool(self.settings.get("click_through", False)),
            panel_expanded=bool(self.settings.get("countdown_panel_expanded", True)),
        )
        self._restore_window_position()

        # -- character system (M5; P0 remediation) ---------------------------
        self._asset_cache = LruByteCache(48 * 1024 * 1024)  # GLOBAL budget
        self._init_character_subsystem()

        self.audio = AudioManager(volume=float(self.settings.get("volume", 0.35)))
        self.audio.set_sound_enabled(bool(self.settings.get("sound_enabled", True)))
        self.audio.error_message.connect(self._on_audio_error)
        self.audio.playback_changed.connect(self._on_playback_changed)
        # local playback wins the core.music fact: the bridge un-projects
        # while our own player is running and re-asserts when it stops
        self.audio.playback_changed.connect(
            lambda playing, _track: (
                self.media_bridge.set_local_playback_active(bool(playing))))
        self._sync_music_tracks()

        self.activity = ActivityMonitor(idle_provider)
        # V13-05: system media bridge (other players via the OS media
        # session layer).  Independent from AudioManager; default OFF and
        # fully released while off.  The persisted opt-in is applied only
        # AFTER construction (V13-05 acceptance F-#1: an early call used
        # to crash every restart with the setting on).
        from retirement_pet.media_bridge import SystemMediaBridge, default_provider

        self.media_bridge = SystemMediaBridge(
            default_provider(), self.settings, self.contexts)
        if bool(self.settings.get("media_bridge_enabled", False)):
            self.media_bridge.set_enabled(True)
        self.rhythm = RhythmController(
            self._service_port, self.clock, self.activity,
            self.settings.as_dict(), self.overlay,
            settings_provider=self.settings.as_dict,
        )
        self.schedule = ScheduleManager(
            self._service_port, self.clock, self.settings.as_dict,
            self.state, self.overlay,
        )
        self.random_scheduler = RandomActionScheduler(
            self.controller, self.clock, self._rng,
            enabled=lambda: bool(self.settings.get("random_actions_enabled", True))
            and not self.schedule.is_meeting(),
            min_interval_s=lambda: float(self.settings.get("random_action_min_interval_s", 45)),
            max_interval_s=lambda: float(self.settings.get("random_action_max_interval_s", 120)),
            mode_allowed=lambda semantic: self._action_mode(semantic) == "auto",
        )

        self.startup_manager = StartupManager(WinRegBackend())
        self._check_startup_health()

        # Two clocks, one job each (DESIGN_V2 20): frames only while visible,
        # services always - never services riding the animation frame rate.
        self.visual_clock = VisualClock(
            fps=int(self.settings.get("animation_fps", 16)), clock=self.clock
        )
        self.service_clock = ServiceClock(clock=self.clock)
        self._register_clock_tasks()

        self.tray = TrayController(self._populate_tray_menu)
        self.tray.activated_toggle.connect(self._toggle_window)

        self._health_observation_expected = None
        self._health_observation_painted = False
        self._session_suspended = False
        self._health_observation_timer = QTimer(self.window)
        self._health_observation_timer.setSingleShot(True)
        self._health_observation_timer.setInterval(
            ACTIVE_HEALTH_OBSERVATION_MS)
        self._health_observation_timer.timeout.connect(
            self._complete_active_health_observation)

        # Window interactions -> action requests (never direct action changes).
        self.window.clicked.connect(self._on_pet_clicked)
        self.window.double_clicked.connect(self._toggle_panel)
        self.window.wheel_steps.connect(self._on_wheel)
        self.window.position_saved.connect(self._on_position_saved)
        self.window.first_paint.connect(self._on_first_pet_paint)
        self.qt_app.aboutToQuit.connect(self.shutdown)
        self._qt_lifecycle_connected = True

        # V12-06: apply the persisted layout × visibility combination to the
        # window, overlay bubble gate included; invalid persisted values
        # fall back to the engine default combination.
        self._countdown_cache = None
        self._apply_layout_config()

        # Lock screen / sleep / session disconnect suspend the visual clock.
        self._session_watch = SessionWatchFilter(self._on_session_suspended)
        self._native_filter_installed = False
        try:
            self.qt_app.installNativeEventFilter(self._session_watch)
            self._native_filter_installed = True
        except Exception:  # noqa: BLE001 - native watch is optional
            logger.exception("failed to install native session filter")
        # Ctrl+Alt+T is a best-effort OS registration, not a timer.  Keep its
        # filter and lifecycle independent from the session watcher so either
        # optional native feature may fail without disabling the other.
        self._todo_hotkey = TodoHotkeyFilter(
            lambda: self._open_control_panel("todo"),
            backend=todo_hotkey_backend,
        )
        self._todo_hotkey_filter_installed = False
        try:
            self.qt_app.installNativeEventFilter(self._todo_hotkey)
            self._todo_hotkey_filter_installed = True
        except Exception:  # noqa: BLE001 - menus remain fully functional
            logger.exception("failed to install global Todo hotkey filter")
        self._restore_active_character()

        # Existing Todo state is restored before the first frame.  A wholly
        # absent four-member SQLite family remains lazy and creates no file.
        if self._todo_file_family_present():
            self._initialize_todo()

        self._countdown_cache = None
        self._startup_timer = QTimer(self.window)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.timeout.connect(self._show_window)
        self._headless = headless

        # First valid frame needs countdown data before the first service
        # tick fires (PERFORMANCE_BUDGET 3.3: no placeholder frames).
        self._refresh_countdown()

        # Verification hook (CONFORMANCE 7.x evidence harnesses): after the
        # pet window is shown, write its native handle and first-run panel
        # state to the given path so an external harness can inspect the REAL
        # native windows even where the owning pid differs from process pid.
        self._report_window_path: Path | None = None
        if "--report-window" in argv:
            idx = argv.index("--report-window")
            if idx + 1 < len(argv):
                self._report_window_path = Path(argv[idx + 1])

    # -- lifecycle -----------------------------------------------------------

    def _on_first_pet_paint(self) -> None:
        """Start observation only for an exact cross-start ACTIVE restore."""
        self.perf.mark(FIRST_PET_PAINT)
        self._health_observation_painted = True
        self._resume_active_health_observation()

    def _pause_active_health_observation(self) -> None:
        """Pause without credit; resume restarts the full safe interval."""
        self._health_observation_timer.stop()

    def _resume_active_health_observation(self) -> None:
        """Observe a fresh full interval while visible and unsuspended."""
        if (self._health_observation_expected is not None
                and self._health_observation_painted
                and self.window.isVisible()
                and not self._session_suspended
                and not self._shutdown_complete):
            # Conservative policy: hidden/suspended elapsed time earns no
            # credit; every resume restarts the complete observation window.
            self._health_observation_timer.start(
                ACTIVE_HEALTH_OBSERVATION_MS)

    def _on_session_suspended(self, suspended: bool) -> None:
        self._session_suspended = bool(suspended)
        self.visual_clock.set_suspended(self._session_suspended)
        self._update_idle_timeline_running()
        if self._session_suspended:
            self._pause_active_health_observation()
        else:
            self._resume_active_health_observation()

    def _update_idle_timeline_running(self) -> None:
        """Idle time advances only while BOTH gate reasons allow it (CR-A04).

        Visibility and session state each pause the timeline independently;
        clearing one reason must never erase the other's pause, so the
        running state is always recomputed from the combined condition
        instead of the individual handlers calling pause/resume directly.
        """
        self.media_bridge.set_app_active(
            self.window.isVisible() and not self._session_suspended)
        if self.window.isVisible() and not self._session_suspended:
            self._idle_timeline.resume()  # continue, never replay hidden time
        else:
            self._idle_timeline.pause()

    def _complete_active_health_observation(self) -> None:
        """Promote only the exact restored tuple that began observation."""
        self._health_observation_timer.stop()
        expected = self._health_observation_expected
        if expected is None or self._shutdown_complete:
            return
        if not self.window.isVisible() or self._session_suspended:
            # A stale queued timeout cannot earn hidden/suspended credit.
            return
        self._health_observation_expected = None
        self._health_observation_painted = False
        if self.switcher.checkpoint_active_health(expected):
            logger.info("ACTIVE health observation promoted %s to LKG",
                        expected.character_fqid)
        else:
            logger.info("ACTIVE changed during health observation; no LKG"
                        " promotion")

    def _write_window_report(self) -> None:
        if self._report_window_path is None:
            return
        # R08-03: an incrementing sequence makes every observation
        # uniquely identifiable, so a harness can tell a fresh sample
        # from a stale file left behind by an earlier run
        self._report_window_sequence = (
            getattr(self, "_report_window_sequence", 0) + 1)
        if getattr(self, "_report_window_timer", None) is None:
            from PySide6.QtCore import QTimer

            timer = QTimer(self.window)
            timer.setInterval(400)
            timer.timeout.connect(self._write_window_report)
            self._report_window_timer = timer
        if not self._report_window_timer.isActive()                 and not self._shutdown_complete:
            # diagnostics-only: keep the observation file live so
            # harnesses can sample the real layout anchor over time
            timer = self._report_window_timer
            timer.start()
        temporary_path = self._report_window_path.with_name(
            f".{self._report_window_path.name}.{os.getpid()}.tmp")
        try:
            panel = getattr(self, "_panel", None)
            panel_visible = bool(
                self._character_onboarding_panel_shown
                and panel is not None
                and panel.isVisible()
            )
            panel_hwnd = int(panel.winId()) if panel_visible else 0
            payload = json.dumps({
                "schema": 2,
                "pid": os.getpid(),
                # Preserve the v1 top-level field for existing harnesses.
                "pid": os.getpid(),
                "hwnd": int(self.window.winId()),
                "sequence": self._report_window_sequence,
                "generated_at": datetime.now(timezone.utc).isoformat(
                    timespec="milliseconds"),
                # R08-03: the REAL layout foot anchor, not the window
                # bottom edge - local DIP for context, SCREEN pixels as
                # the comparison basis
                "foot_point": self.window.current_foot_point(),
                "foot_point_screen": self.window.current_foot_point_global(),
                "window_dpi": self.window.current_window_dpi(),
                "window_frame": [self.window.x(), self.window.y(),
                                 self.window.width(), self.window.height()],
                "onboarding": {
                    "campaign": CHARACTER_ONBOARDING_CAMPAIGN,
                    "ready": self._character_onboarding_report_ready,
                    "state": self._character_onboarding_report_state,
                    "offered": self._character_onboarding_panel_shown,
                    "panel_visible": panel_visible,
                    "panel_hwnd": panel_hwnd,
                },
            }, ensure_ascii=False, sort_keys=True)
            self._report_window_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(payload, encoding="utf-8")
            # Windows may briefly deny replacement while an external harness
            # has the previous JSON open.  Bound the diagnostic-only retry;
            # never turn that narrow sharing race into a missing ready report.
            for attempt in range(10):
                try:
                    os.replace(temporary_path, self._report_window_path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.01 * (attempt + 1))
        except Exception:  # noqa: BLE001 - diagnostics must never crash the app
            logger.exception("failed to write window report")
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _detach_qt_lifecycle(self) -> None:
        """Best-effort teardown of callbacks owned by this app instance."""
        try:
            self._todo_hotkey.detach()
        except Exception:  # noqa: BLE001 - process shutdown continues
            logger.exception("failed to detach global Todo hotkey")
        if getattr(self, "_qt_lifecycle_connected", False):
            for signal, slot in (
                (self.qt_app.aboutToQuit, self.shutdown),
                (self._health_observation_timer.timeout,
                 self._complete_active_health_observation),
                (self._startup_timer.timeout, self._show_window),
                (self.window.first_paint, self._on_first_pet_paint),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            self._qt_lifecycle_connected = False
        if getattr(self, "_native_filter_installed", False):
            try:
                self.qt_app.removeNativeEventFilter(self._session_watch)
            except Exception:  # noqa: BLE001 - process shutdown continues
                logger.exception("failed to remove native session filter")
            finally:
                self._native_filter_installed = False
        if getattr(self, "_todo_hotkey_filter_installed", False):
            try:
                self.qt_app.removeNativeEventFilter(self._todo_hotkey)
            except Exception:  # noqa: BLE001 - process shutdown continues
                logger.exception("failed to remove global Todo hotkey filter")
            finally:
                self._todo_hotkey_filter_installed = False

    def run(self) -> int:
        if (getattr(self, "_second_instance", True)
                or getattr(self, "_shutdown_complete", True)):
            return 0
        self.tray.show()
        self.perf.mark(TRAY_READY)
        self._session_watch.attach(self.window)
        # Headless verification must not reserve a real user shortcut.  The
        # installed application registers only after its native window exists.
        if not self._headless and self._todo_hotkey_filter_installed:
            self._todo_hotkey.attach(self.window)
        self.service_clock.start()
        if self.startup_mode:
            # Freeze an explicit readiness boundary for external native gates:
            # autostart suppresses onboarding by policy rather than merely
            # racing ahead of a deferred panel timer.
            self._character_onboarding_report_state = \
                "suppressed_autostart"
            self._character_onboarding_report_ready = True
            delay_ms = int(float(self.settings.get("startup_delay_s", 8)) * 1000)
            logger.info("startup mode: showing window in %d ms", delay_ms)
            self._startup_timer.start(delay_ms)
        else:
            self._show_window()
            if not self._headless:
                # Enter the event loop first; constructor/headless/native
                # verification paths must never create or activate this UI.
                QTimer.singleShot(0, self._maybe_open_character_onboarding)
        if self._headless:
            return 0
        return self.qt_app.exec()

    # -- TodoModule (M6.5) ----------------------------------------------------

    @property
    def todo(self):
        """Return the real service or the memoized unavailable facade."""
        if self._todo_service is None:
            self._initialize_todo()
        return self._todo_service

    @property
    def todo_bridge(self):
        """Return the storage-free bridge without initializing Todo."""
        return self._todo_bridge_instance

    def _todo_file_family_present(self) -> bool:
        path = self._data_dir / "tasks.db"
        return any(os.path.lexists(str(member)) for member in (
            path, Path(f"{path}-wal"), Path(f"{path}-shm"),
            Path(f"{path}-journal")))

    def _initialize_todo(self) -> None:
        """Attempt Todo initialization at most once for this application."""
        if self._todo_service is not None or self._todo_init_attempted:
            return
        self._todo_init_attempted = True
        repository = None
        service = None
        try:
            from retirement_pet.todo import TaskRepository, TodoService

            repository = TaskRepository(self._data_dir / "tasks.db")
            service = TodoService(
                repository, on_event=self._dispatch_todo_event)
            projection = service.load_focus_projection()
            self._todo_bridge_instance.sync_focus(projection)
        except Exception as exc:  # all failures isolate this optional module
            if service is not None:
                try:
                    service.close()
                except Exception:  # noqa: BLE001 - best-effort ownership close
                    logger.error("todo initialization cleanup failed")
            elif repository is not None:
                try:
                    repository.close()
                except Exception:  # noqa: BLE001 - best-effort ownership close
                    logger.error("todo initialization cleanup failed")

            if isinstance(exc, SchemaTooNew):
                reason = "schema_too_new"
                log_code = "todo initialization schema unsupported"
            elif isinstance(exc, TaskStoreUnavailable):
                reason = "store_unavailable"
                log_code = "todo initialization unavailable"
            else:
                reason = "initialization_failed"
                log_code = "todo initialization failed"
            self.todo_error = exc
            self.todo_availability = TaskStoreAvailability.unavailable(reason)
            self._todo_service = _UnavailableTodoService(reason)
            logger.error(log_code)
            return

        self._todo_service = service
        self.todo_error = None
        self.todo_availability = TaskStoreAvailability.ready()

    def _dispatch_todo_event(self, event: str) -> None:
        # The DB/cache transaction is already committed.  Publish the safe
        # projection before UI listeners so every observer sees final state.
        try:
            projection = self._todo_service.load_focus_projection()
            self._todo_bridge_instance.sync_focus(projection)
        except Exception:  # noqa: BLE001 - the store stays authoritative
            logger.error("todo context synchronization failed")
        for listener in list(self.todo_events):
            try:
                listener(event)
            except Exception:  # noqa: BLE001
                logger.error("todo UI listener failed")

    def subscribe_todo_events(self, listener):
        """Subscribe to committed Todo events and return an idempotent remover."""
        self.todo_events.append(listener)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed
            if not subscribed:
                return
            subscribed = False
            try:
                self.todo_events.remove(listener)
            except ValueError:
                pass

        return unsubscribe

    def shutdown(self) -> None:
        if getattr(self, "_shutdown_complete", False):
            return
        self._shutdown_complete = True
        logger.info("shutting down")
        steps = (
            ("shutdown report timer failed",
             lambda: (getattr(self, "_report_window_timer", None)
                      and self._report_window_timer.stop())),
            ("shutdown health timer failed",
             lambda: self._health_observation_timer.stop()),
            ("shutdown startup timer failed",
             lambda: self._startup_timer.stop()),
            ("shutdown lifecycle detach failed", self._detach_qt_lifecycle),
            ("shutdown visual clock failed",
             lambda: self.visual_clock.set_visible(False)),
            ("shutdown service clock failed", self.service_clock.stop),
            ("shutdown window position failed", self._save_window_position),
            ("shutdown state save failed", self.state.save),
            ("shutdown settings save failed", self.settings.save),
            ("shutdown audio failed", self.audio.stop),
            ("shutdown tray failed", self.tray.hide),
            ("shutdown control panel failed", self._close_control_panel),
            ("shutdown media bridge failed", self.media_bridge.shutdown),
            ("shutdown todo failed",
             lambda: self._todo_service.close()
             if self._todo_service is not None else None),
            ("shutdown switcher failed", self.switcher.shutdown),
            ("shutdown selection store failed", self._selection_store.close),
            ("shutdown library failed", self.library.close),
            ("shutdown instance guard failed", self._guard.release),
        )
        self._health_observation_expected = None
        self._health_observation_painted = False
        for log_code, close_resource in steps:
            try:
                close_resource()
            except Exception:  # noqa: BLE001 - every later step must run
                logger.error(log_code)

    def quit(self) -> None:
        self.qt_app.quit()

    def _quit_from_ipc(self) -> None:
        """Normal-exit request from the verification harness.

        Only reachable when the primary was started with ``--test-ipc-quit``;
        routes through the same quit() as the tray 退出 action, so the
        harness evidence covers the real shutdown path (clocks stop, state
        and settings saved, tray hidden, process exits by itself).
        """
        logger.info("quit requested via test IPC")
        self.quit()

    # -- window management ------------------------------------------------------

    def _show_window(self, activate: bool = False) -> None:
        """Show the pet.  ``activate`` steals keyboard focus and is reserved
        for explicit user summoning (tray click, second-instance ``show``);
        normal startup and autostart stay quiet (design 1: 安静陪伴)."""
        if self._shutdown_complete:
            return
        self.window.show()
        self.window.raise_()
        self._write_window_report()
        if activate:
            self.window.activateWindow()
        self.visual_clock.set_fps(int(self.settings.get("animation_fps", 16)))
        self.visual_clock.set_visible(True)
        self._update_idle_timeline_running()
        self._resume_active_health_observation()

    def _hide_window(self) -> None:
        self._pause_active_health_observation()
        self.window.hide()
        # visibility already off: the combiner now pauses the timeline
        self._update_idle_timeline_running()
        self.visual_clock.set_visible(False)  # hidden = zero visual ticks

    def _toggle_window(self) -> None:
        if self.window.isVisible():
            self._hide_window()
        else:
            self._show_window(activate=True)  # user explicitly asked for it

    def _show_from_second_instance(self) -> None:
        self._show_window(activate=True)  # user launched the app again
        self._maybe_open_character_onboarding(user_initiated=True)

    def _restore_to_primary_screen(self) -> None:
        moved = self.window.restore_to_primary_screen()
        if moved:
            self._save_window_position()
            self.state.save()

    def _open_control_panel(
            self, page: str = "overview", *, activate: bool = True) -> None:
        """Open the multi-page control panel (single instance, lazy pages).

        The panel is a standard window, separate from the transparent pet
        surface; only one instance exists and switching pages never builds
        all of them (DESIGN_V2 11.3; M6).
        """
        panel = getattr(self, "_panel", None)
        if panel is not None:
            if not panel.open_page(page):
                panel.open_page("overview")
            panel.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,
                               not activate)
            panel.show()
            panel.raise_()
            if activate:
                panel.activateWindow()
            return
        from retirement_pet.ui.panel import ControlPanel
        from retirement_pet.ui.panel import pages as panel_pages
        from retirement_pet.ui.panel.todo_page import build_todo_page as _build_todo_page

        panel = ControlPanel(self)
        panel.register_page("overview", lambda: panel_pages.build_overview_page(self))
        panel.register_page("characters", lambda: panel_pages.build_characters_page(self))
        panel.register_page("actions", lambda: panel_pages.build_actions_page(self))
        panel.register_page("text", lambda: panel_pages.build_text_page(self))
        panel.register_page("display", lambda: panel_pages.build_display_page(self))
        panel.register_page("sound_schedule", lambda: panel_pages.build_sound_schedule_page(self))
        panel.register_page("todo", lambda: _build_todo_page(self))
        panel.register_page("about", lambda: panel_pages.build_about_page(self))
        panel.finish_registration()
        self._panel = panel
        if not panel.open_page(page):
            panel.open_page("overview")
        panel.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,
                           not activate)
        panel.show()
        panel.raise_()
        if activate:
            panel.activateWindow()

    def _on_control_panel_disposed(self, panel) -> None:
        if getattr(self, "_panel", None) is panel:
            self._panel = None

    def _close_control_panel(self) -> None:
        panel = getattr(self, "_panel", None)
        if panel is not None:
            panel.close()

    def _restore_window_position(self) -> None:
        pos = self.state.get("window_pos")
        if isinstance(pos, list) and len(pos) == 2:
            from PySide6.QtCore import QPoint

            self.window.move(QPoint(int(pos[0]), int(pos[1])))
            self.window.ensure_visible_on_screen()
        else:
            self.window.place_default()

    def _save_window_position(self) -> None:
        self.state.set("window_pos", [self.window.x(), self.window.y()])

    def _on_position_saved(self, _point) -> None:
        self._save_window_position()
        self.state.save()

    # -- clocks ---------------------------------------------------------------

    def _register_clock_tasks(self) -> None:
        # VisualClock: frame composition only (controller + overlay + repaint).
        self.visual_clock.tick.connect(self._frame_tick)
        # ServiceClock: facts, independent of the visual frame rate.  They
        # keep running while the pet is hidden (ADR-V2-017).
        self.service_clock.register(1_000, self._slow_tick)
        self.service_clock.register(5_000, self._screen_check)

    def _frame_tick(self, now_ms: int) -> None:
        self.controller.tick(now_ms)
        self.overlay.tick(now_ms)
        snapshot = self._compose_snapshot(now_ms)
        self.window.update_snapshot(snapshot, self._countdown_cache)

    def _slow_tick(self, now_ms: int) -> None:
        try:
            self.rhythm.tick()
        except Exception:  # noqa: BLE001
            logger.exception("rhythm tick failed")
        try:
            self.schedule.tick()
        except Exception:  # noqa: BLE001
            logger.exception("schedule tick failed")
        try:
            self.random_scheduler.tick(now_ms)
        except Exception:  # noqa: BLE001
            logger.exception("random scheduler tick failed")
        try:
            self._refresh_countdown()
        except Exception:  # noqa: BLE001
            logger.exception("countdown refresh failed")

    def _screen_check(self, _now_ms: int) -> None:
        if self.window.isVisible():
            self.window.ensure_visible_on_screen()

    def _refresh_countdown(self) -> None:
        self._countdown_cache = self.countdown_module.refresh()
        # The heading renders {days}/{stage_name} live, so it must follow
        # the 1 Hz countdown snapshot, not only config changes.
        self._sync_countdown_heading()

    # -- snapshot composition -----------------------------------------------------

    def _compose_snapshot(self, now_ms: int) -> RenderSnapshot:
        # Countdown data arrives from the 1 Hz service tick, not per frame
        # (PERFORMANCE_BUDGET 7: the countdown is recomputed at most once
        # per second, never per animation frame).
        runtime = self.controller.current
        action = runtime.spec.action_id if runtime else ActionId.IDLE
        overlay_snap = self.overlay.snapshot(now_ms)
        effects = set(overlay_snap.effects)
        derived = ACTION_EFFECTS.get(action)
        if derived and (action is not ActionId.MUSIC or self.audio.is_playing()):
            effects.add(derived)
        overlay_final = OverlaySnapshot(
            blink=overlay_snap.blink,
            gaze_x=overlay_snap.gaze_x,
            gaze_y=overlay_snap.gaze_y,
            effects=tuple(sorted(effects)),
            bubble_text=overlay_snap.bubble_text,
        )
        stage = (
            self._countdown_cache.stage
            if self._countdown_cache
            else LifeStage.YOUNG
        )
        return RenderSnapshot(
            action=action,
            stage=stage,
            # Explicit actions keep their ActionRuntime clock; idle runs on
            # its own monotonic timeline so pack idles keep animating
            # (V12-01) instead of freezing on the first frame.
            elapsed_ms=(runtime.elapsed_ms(now_ms) if runtime
                        else self._idle_timeline.elapsed_ms(now_ms)),
            frame=runtime.frame(now_ms) if runtime else 0,
            time_ms=now_ms,
            overlay=overlay_final,
            assets_available=self.bundle.available
            and self.bundle.action_visual(action.value) is not None,
        )

    # -- character switching (M5; P0 transaction protocol) ----------------------

    def _init_character_subsystem(self) -> None:
        """Build the disk-backed character subsystem as one failure boundary.

        Nothing is published on ``self`` until catalog construction, embedded
        media validation and all store/switcher constructors have succeeded.
        Any failure closes the partial disk-backed graph and replaces it with
        an in-memory graph that can only display the already-created
        programmatic Bootstrap renderer.
        """
        library = None
        selection_store = None
        switcher = None
        try:
            library = PackLibrary(self._data_dir / "library")
            ensure_builtin_official(library)
            selection_store = ActiveSelectionStore(library)
            switcher = RuntimeSwitcher(
                library,
                selection_store,
                asset_cache=self._asset_cache,
                safe_renderer=self.renderer,
            )
            catalog = CharacterCatalog(library)
        except Exception:  # noqa: BLE001 - recovery UI must always survive
            logger.exception(
                "character subsystem unavailable; entering Bootstrap mode")
            if switcher is not None:
                try:
                    switcher.shutdown()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    logger.exception("failed to close partial runtime switcher")
            if selection_store is not None:
                try:
                    selection_store.close()
                except Exception:  # noqa: BLE001
                    logger.exception("failed to close partial selection store")
            if library is not None:
                try:
                    library.close()
                except Exception:  # noqa: BLE001
                    logger.exception("failed to close partial pack library")

            library = PackLibrary.bootstrap(self._data_dir / "library")
            selection_store = ActiveSelectionStore(library)
            switcher = RuntimeSwitcher(
                library,
                selection_store,
                asset_cache=self._asset_cache,
                safe_renderer=self.renderer,
            )
            catalog = CharacterCatalog(library)

        self.library = library
        self._selection_store = selection_store
        self.switcher = switcher
        self.catalog = catalog
        self._character_storage_degraded = bool(library.degraded)
        if not library.degraded:
            # L07-01: resume scheduled deletions only now - the selection
            # facts are readable, so a revision that became active or the
            # restore basis is protected, not deleted.
            try:
                library.recover_pending_deletes(
                    self._protected_selection_guard())
            except Exception:  # noqa: BLE001 - keep startup alive
                logger.exception("pending delete recovery failed")

    def _restore_active_character(self) -> None:
        """Restore exact ACTIVE without rewriting it, then try LKG.

        LKG is a health checkpoint, not an alias for the most recent switch.
        It is never promoted during startup.  If it is the only renderable
        selection, ACTIVE alone is repaired with a new high-water tuple.
        """
        self._health_observation_expected = None
        self._health_observation_painted = False
        if self._character_storage_degraded:
            self.switcher.enter_safe_mode(self.window)
            self._sync_capabilities()
            return

        try:
            active = self._selection_store.get("active")
            last_known_good = self._selection_store.get("last_known_good")
        except Exception:  # noqa: BLE001 - never guess after failed readback
            logger.exception("selection readback failed; entering safe mode")
            self.switcher.enter_safe_mode(self.window)
            self._sync_capabilities()
            return

        if (active is not None and self._selection_is_ready(active)
                and self.switcher.restore_selection(active, self.window)):
            self._health_observation_expected = active
            logger.info("restored exact ACTIVE character %s",
                        active.character_fqid)
            self._migrate_legacy_builtin_after_restore(active)
            self._sync_capabilities()
            return

        if (last_known_good is not None
                and self._selection_is_ready(last_known_good)
                and self.switcher.restore_selection(
                    last_known_good, self.window)):
            if self.switcher.repair_active_from_current(self.window):
                logger.info("repaired ACTIVE from last-known-good character %s",
                            last_known_good.character_fqid)
                repaired = self._selection_store.get("active")
                self._health_observation_expected = repaired
                self._migrate_legacy_builtin_after_restore(repaired)
            else:
                logger.error("LKG rendered but ACTIVE repair was indeterminate")
                self.switcher.enter_safe_mode(self.window)
            self._sync_capabilities()
            return

        self._fallback_to_builtin()

    def _migrate_legacy_builtin_after_restore(self, selection) -> bool:
        """Atomically upgrade only the frozen official v1.0.0 selection.

        Old ACTIVE is rendered first.  The quality upgrade then uses the same
        REQUEST→PREPARE→SWAP→COMMIT protocol as an explicit user switch.  A
        normal prepare/swap failure therefore leaves the old renderer,
        ACTIVE and LKG untouched, while an external character is never
        considered eligible.  A v1.0.1 ACTIVE makes later starts idempotent.
        """
        if not is_exact_legacy_builtin_selection(self.library, selection):
            return False
        try:
            current = builtin_official_revision(self.library)
            if current is None:
                return False
            character = first_character_id(self.library, current)
            request = self.switcher.request(
                current.revision_key,
                character,
                variant_id=selection.variant_id,
                config_revision_id=selection.config_revision_id,
            )
            candidate = self.switcher.prepare(request)
            if candidate is None or not self.switcher.swap_and_commit(
                    request, self.window):
                logger.warning(
                    "official v1.0.1 migration failed; exact v1.0.0 remains")
                return False
            migrated = self._selection_store.get("active")
            if migrated is None \
                    or migrated.revision_key() != current.revision_key:
                logger.error(
                    "official migration commit had no matching ACTIVE readback")
                return False
            self._health_observation_expected = migrated
            logger.info("migrated official character to %s",
                        current.revision_key)
            return True
        except Exception:  # noqa: BLE001 - startup migration is best effort
            logger.exception(
                "official v1.0.1 migration failed; restored v1.0.0 retained")
            return False

    def _selection_is_ready(self, selection) -> bool:
        try:
            record = self.library.get_revision(selection.revision_key())
            return record is not None and record.pack_path.is_file()
        except Exception:  # noqa: BLE001 - storage is outside the trust boundary
            logger.exception("READY lookup failed")
            return False

    def _bundled_character_preview_path(self) -> Path:
        """Return the installed path of the pinned local preview pack."""
        return asset_path("petpack", "examples", BUNDLED_PREVIEW_FILE)

    def _active_character_is_local(self) -> bool:
        """Whether the current authoritative selection is user/local content."""
        if self._character_storage_degraded:
            return False
        try:
            active = self._selection_store.get("active")
            if active is None:
                return False
            record = self.library.get_revision(active.revision_key())
            return record is not None and not record.builtin
        except Exception:  # noqa: BLE001 - optional onboarding stays fail-safe
            logger.exception("failed to inspect active character for onboarding")
            return False

    def _character_onboarding_pending(self) -> bool:
        """Return whether the one-time character choice is still meaningful."""
        if self._character_onboarding_handled_in_session:
            return False
        if self.settings.get(CHARACTER_ONBOARDING_SETTING, "") == \
                CHARACTER_ONBOARDING_CAMPAIGN:
            return False
        if self._character_storage_degraded or self._active_character_is_local():
            return False
        return True

    def _complete_character_onboarding(self) -> bool:
        """Remember one explicit choice without duplicating ACTIVE character."""
        self._character_onboarding_handled_in_session = True
        previous = self.settings.get(CHARACTER_ONBOARDING_SETTING, "")
        self.settings.set(
            CHARACTER_ONBOARDING_SETTING, CHARACTER_ONBOARDING_CAMPAIGN)
        if self.settings.save():
            return True
        # Keep this process quiet; a later process retries persistence and may
        # repair the marker instead of falsely claiming durable success now.
        # Roll back in memory as shutdown performs another settings save.
        self.settings.set(CHARACTER_ONBOARDING_SETTING, previous)
        logger.error("character onboarding choice could not be persisted")
        return False

    def _maybe_open_character_onboarding(
            self, *, user_initiated: bool = False) -> None:
        """Offer the choice only after a real, intentional desktop launch.

        Autostart never opens or activates a standard window.  A later manual
        second launch is represented by ``user_initiated=True`` and may offer
        the choice even when the primary process originally began at login.
        """
        if self._shutdown_complete or self._headless:
            return
        try:
            if self.startup_mode and not user_initiated:
                self._character_onboarding_report_state = \
                    "suppressed_autostart"
                return
            if self.settings.get(CHARACTER_ONBOARDING_SETTING, "") == \
                    CHARACTER_ONBOARDING_CAMPAIGN:
                self._character_onboarding_report_state = "completed"
                return
            if self._active_character_is_local():
                self._complete_character_onboarding()
                self._character_onboarding_report_state = "existing_local"
                return
            if self._character_onboarding_pending():
                self._open_control_panel(
                    "characters", activate=bool(user_initiated))
                self._character_onboarding_panel_shown = True
                self._character_onboarding_report_state = "offered"
            else:
                self._character_onboarding_report_state = "not_applicable"
        finally:
            # The normal-startup native gate waits for this second report so
            # it observes both top-level windows, not merely the pet's earlier
            # show().  Autostart never schedules this method.
            self._character_onboarding_report_ready = True
            self._write_window_report()

    @staticmethod
    def _is_expected_bundled_preview(preview) -> bool:
        """Fail closed unless an isolated preview matches every frozen fact."""
        try:
            key = preview.revision_key
            return (
                preview.archive_sha256 == BUNDLED_PREVIEW_ARCHIVE_SHA256
                and key.pack.publisher_id == BUNDLED_PREVIEW_PUBLISHER_ID
                and key.pack.package_id == BUNDLED_PREVIEW_PACKAGE_ID
                and key.package_version == BUNDLED_PREVIEW_PACKAGE_VERSION
                and key.content_digest == BUNDLED_PREVIEW_CONTENT_DIGEST
                and tuple(preview.character_ids)
                == (BUNDLED_PREVIEW_CHARACTER_ID,)
                and tuple(preview.warning_codes)
                == BUNDLED_PREVIEW_WARNING_CODES
                and bool(preview.first_frames_verified)
            )
        except (AttributeError, TypeError):
            return False

    def _activate_bundled_character_preview(
            self, preview) -> tuple[bool, str]:
        """Install and switch the exact bundled LOCAL_IMPORTED preview.

        This is the transaction behind the onboarding button.  Failures before
        ACTIVATE leave ACTIVE unchanged.  If ACTIVATE cannot confirm its own
        candidate, RuntimeSwitcher reconciles to persisted authority or enters
        Bootstrap safe mode; this campaign remains pending in either case.
        """
        if not self._is_expected_bundled_preview(preview):
            logger.error("bundled character preview identity mismatch")
            return False, "半写实预览包身份校验失败，已保留当前角色。"

        ok, message = self._import_preflighted_character_pack(
            preview,
            warnings_acknowledged=BUNDLED_PREVIEW_WARNING_CODES,
        )
        if not ok:
            return False, message
        try:
            entry = next(
                item for item in self.catalog.entries()
                if item.revision_key == preview.revision_key
                and item.character_id == BUNDLED_PREVIEW_CHARACTER_ID
            )
        except Exception:  # noqa: BLE001 - optional UI path must retain ACTIVE
            logger.exception("bundled preview READY entry was not readable")
            return False, "预览包已导入，但角色条目不可用；当前角色未切换。"
        if not self._switch_character(entry):
            return False, (
                "预览包已导入，但目标角色未启用；已按可确认的持久化状态"
                "恢复显示，可稍后重试。")

        persisted = self._complete_character_onboarding()
        if not persisted:
            return True, "半写实退休猫已启用；程序会在后续启动继续尝试补记选择。"
        return True, "已启用半写实退休猫；以后可在角色页继续更换。"

    def _fallback_to_builtin(self) -> None:
        """Land on the REAL builtin official cat (never a fake selection)."""
        try:
            record = builtin_official_revision(self.library)
            if record is None:
                raise LookupError("builtin official revision is unavailable")
            character = first_character_id(self.library, record)
            request = self.switcher.request(record.revision_key, character)
            candidate = self.switcher.prepare(request)
            if candidate is None or not self.switcher.swap_and_commit(
                    request, self.window):
                raise RuntimeError("builtin activation did not commit")
        except Exception:  # noqa: BLE001 - bootstrap renderer is authoritative
            logger.error("builtin official pack unavailable; staying on the"
                         " Bootstrap Renderer (programmatic cat)",
                         exc_info=True)
            self.switcher.enter_safe_mode(self.window)
            self._sync_capabilities()
            return
        logger.info("fell back to builtin official character %s",
                    candidate.character_fqid())
        self._sync_capabilities()

    def _import_character_pack(self, pack_path: Path) -> tuple[bool, str]:
        """Preflight and install one unprivileged local PetPack.

        Import never activates the pack.  The user reviews the new catalog
        entry and performs the existing two-phase switch explicitly, so an
        installation error cannot replace the visible character or ACTIVE/LKG.
        """
        if self._character_storage_degraded:
            return False, "角色库存储当前不可用，未导入任何内容。"
        from retirement_pet.petpack.local_import import (
            LocalImportError,
            preflight_local_pack,
        )

        try:
            preview = preflight_local_pack(Path(pack_path))
        except LocalImportError as exc:
            logger.warning("local character pack import rejected: %s", exc)
            return False, f"导入失败：{exc}"
        except Exception:  # noqa: BLE001 - import is an optional UI path
            logger.exception("local character pack preflight failed")
            return False, "导入失败：无法安全读取角色包。"
        return self._import_preflighted_character_pack(preview)

    def _import_preflighted_character_pack(
            self, preview, *,
            warnings_acknowledged: tuple[str, ...] = ()) -> tuple[bool, str]:
        """Install a snapshot whose first frame was verified in a child."""
        if self._character_storage_degraded:
            return False, "角色库存储当前不可用，未导入任何内容。"
        from retirement_pet.petpack.local_import import (
            LocalImportError,
            LocalImportPreview,
        )

        try:
            if not isinstance(preview, LocalImportPreview) \
                    or not preview.first_frames_verified:
                raise LocalImportError("角色包缺少隔离首帧检查结果")
            if hashlib.sha256(preview.payload).hexdigest() != \
                    preview.archive_sha256:
                raise LocalImportError("角色包预检快照完整性检查失败")
            already_present = self.library.get_revision(
                preview.revision_key) is not None
            record = self.library.install_bytes(
                preview.payload,
                warnings_acknowledged=warnings_acknowledged,
                expected_revision=preview.revision_key,
                expected_archive_sha256=preview.archive_sha256,
                validation_report=preview.validation_report,
            )
            if record is None:
                raise RuntimeError("installed revision was not readable")
            if record.revision_key != preview.revision_key:
                raise RuntimeError("installed revision did not match preflight")
        except LocalImportError as exc:
            logger.warning("local character pack import rejected: %s", exc)
            return False, f"导入失败：{exc}"
        except (OSError, RuntimeError) as exc:
            # Preserve useful diagnostics in the local log without echoing a
            # user-selected absolute path (or another system detail) into the
            # control panel and desktop bubble.
            logger.warning("local character pack storage failure: %s", exc)
            return False, "导入结果未确认；当前角色未切换，请重启后检查角色列表。"
        except Exception as exc:  # noqa: BLE001 - import is an optional UI path
            logger.exception("local character pack import failed")
            return False, "导入结果未确认；当前角色未切换，请查看本地日志。"

        if already_present:
            message = f"角色包已存在：{preview.package_name}"
        else:
            count = len(preview.character_ids)
            message = f"导入成功：{preview.package_name}（{count} 个角色）"
        logger.info("local character pack READY: %s", record.revision_key)
        self.overlay.show_bubble(message, 3000)
        return True, message

    def _switch_character(self, entry) -> bool:
        """User-initiated switch through the REQUEST→PREPARE→SWAP→COMMIT
        transaction; context, global settings and the retirement target
        survive by construction (they never live in a character runtime)."""
        request = self.switcher.request(entry.revision_key,
                                        entry.character_id)
        candidate = self.switcher.prepare(request)
        if candidate is None:
            self.overlay.show_bubble("角色无法加载，已保持当前角色", 3500,
                                   importance="error")
            return False
        previous_runtime = self.switcher.current_runtime
        previous_safe_mode = self.switcher.in_safe_mode
        if not self.switcher.swap_and_commit(request, self.window):
            # A CAS loser may render the authoritative third runtime even
            # though this request returns False; an indeterminate write may
            # enter Bootstrap mode.  Reconcile only when the switcher's real
            # authority changed, not merely because it was already safe.
            if (self.switcher.current_runtime is not previous_runtime
                    or self.switcher.in_safe_mode != previous_safe_mode):
                self._sync_capabilities()
            self.overlay.show_bubble(
                "目标角色未启用；已恢复安全可确认的显示状态", 3500)
            return False
        self._sync_capabilities()
        if entry.revision_key in set(self.library.pending_delete_keys()):
            # L07-01 state closure: explicitly selecting a pending-delete
            # revision withdraws the scheduled deletion - an activation
            # must never silently await its own removal.
            self.library.cancel_pending_delete(
                entry.revision_key, reason="reselected_by_user")
        self.overlay.show_bubble(f"已切换到 {entry.display_name}", 2500)
        # Per-character text overrides and the {character_name} variable
        # follow the ACTIVE character; the layout combo itself is global
        # and intentionally survives the switch.
        self._apply_layout_config()
        return True

    def on_character_changed(self, callback):
        """Subscribe to ACTIVE-character changes; returns an unsubscribe."""
        self._character_listeners.append(callback)

        def _unsubscribe() -> None:
            try:
                self._character_listeners.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def _notify_character_changed(self) -> None:
        for callback in list(self._character_listeners):
            try:
                callback()
            except Exception:  # noqa: BLE001 - listeners must not break swaps
                logger.exception("character change listener failed")

    def _protected_selection_guard(self):
        """Guard over the FRESH active and last-known-good revisions."""
        def guard(rk) -> bool:
            protected = set()
            for slot in ("active", "last_known_good"):
                selection = self._selection_store.get(slot)
                if selection is not None:
                    protected.add(selection.revision_key())
            return rk in protected
        return guard

    def _sync_capabilities(self) -> None:
        """Publish ACTIVE capabilities and immediately resolve current facts."""
        # The visible body changed (switch commit, safe mode or fallback):
        # the new character's idle loop starts fresh (V12-01).
        self._idle_timeline.reset(IdleResetReason.CHARACTER_SWITCH)
        runtime = self.switcher.current_runtime
        if runtime is not None and hasattr(runtime, "capabilities"):
            capabilities = runtime.capabilities()
        else:
            capabilities = cat_capabilities()
        self.capabilities = capabilities
        self.bridge.set_capabilities(
            capabilities, reason="character_capabilities_changed")
        self._notify_character_changed()

    # -- interactions ------------------------------------------------------------

    def request_manual_action(self, semantic: str) -> tuple[bool, str]:
        """Public formal trigger used by the characters/actions UI and the
        local agent protocol (V13-02): every engine gate in order, and the
        message explains acceptance or the real rejection reason."""
        return self._request_manual_action(semantic)

    def standby_now(self) -> None:
        """Public entry for the agent protocol's action.stop (V13-02):
        same semantics as the UI 待机 button."""
        self._user_standby()

    def activity_status(self) -> dict:
        """Explainable activity facts (V13-06) for the UI and agents.

        ``state`` is the aggregate input fact (active/idle/unknown) from
        system idle time only - never key content, window titles or app
        usage history.  ``focused`` reflects the single focus task
        WITHOUT its title anywhere in this payload.
        """
        focusing = bool(self.todo_bridge.projection.focusing)
        state = self.rhythm.activity_state()
        return {
            "state": state,
            "link_enabled": self.rhythm.link_enabled(),
            "focused": focusing,
            "working_on_focused_task": focusing and state == "active",
        }

    def action_mode(self, semantic: str) -> str:
        """Public read of the per-action mode (auto|manual|disabled)."""
        return self._action_mode(semantic)

    def switch_character_entry(self, entry) -> bool:
        """Public formal switch (REQUEST→PREPARE→SWAP→COMMIT) shared by the
        characters page and the local agent protocol (V13-02)."""
        return self._switch_character(entry)

    def _user_standby(self) -> None:
        """User picks 待机: end the performance and clear USER-owned facts.

        Facts owned by services (rhythm/schedule/audio) remain true - the
        resolver immediately re-derives the performance from them (DESIGN_V2
        6.1: user expressions never fake or delete real facts).
        """
        self.contexts.clear_owned_by("user")
        self.resolver.clear_user_request()
        self.controller.end_current("user_idle_choice")
        self.bridge.apply("user_standby")

    def _on_pet_clicked(self) -> None:
        # Click responses are short emotes (DESIGN_V2 6.4): they expire and
        # then re-resolve from the CURRENT facts - never from a saved action.
        from time import monotonic_ns

        now_ms = monotonic_ns() // 1_000_000
        if self.emotes.request(CAT_CLICKED, now_ms, 2500):
            self.controller.request(ActionId.INTERACT, "user", force=True)
        if self.ui_config.source_of(
                "greeting_text", self._active_character_fqid()) \
                is not UiConfigSource.ENGINE_DEFAULT:
            greeting = self._render_text_template("greeting_text")
            if greeting is not None:
                self.overlay.show_bubble(greeting, 2500)
                return
        messages = ("喵？", "在呢～", "摸摸头", "还有很长的陪伴呢")
        if self._rng.random() < 0.4:
            self.overlay.show_bubble(self._rng.choice(messages), 2500)

    def _on_wheel(self, steps: int) -> None:
        if not bool(self.settings.get("wheel_controls_volume", True)):
            return
        volume = self.audio.adjust_volume(0.05 * steps)
        self.overlay.show_bubble(f"音量 {int(volume * 100)}%", 1200)

    def _toggle_panel(self) -> None:
        self.window.toggle_panel()
        self.settings.set("countdown_panel_expanded", self.window.panel_expanded)
        self.settings.save()

    def _on_audio_error(self, message: str) -> None:
        self.overlay.show_bubble(message, 3500)

    def _on_playback_changed(self, playing: bool, track: str) -> None:
        # Music is a FACT (core.context.music): playback state drives the
        # fact; the bridge resolves the performance (DESIGN_V2 5.2).
        if playing:
            self._service_port.request(ActionId.MUSIC, "audio")
        else:
            self._service_port.end_if_action(ActionId.MUSIC, "playback_stopped")
        if track and playing:
            self.overlay.show_bubble(f"♪ {track}", 2500)

    def _sync_music_tracks(self) -> None:
        paths = self.settings.get("music_paths", [])
        if isinstance(paths, list):
            kept = self.audio.set_tracks([str(p) for p in paths])
            if kept != len(paths):
                # Persist only the surviving entries (existence validated).
                self.settings.set("music_paths", self.audio.tracks)
                self.settings.save()

    # -- action events ------------------------------------------------------------

    def _on_action_changed(self, event) -> None:
        logger.debug(
            "action change: %s -> %s (%s)",
            event.previous.spec.action_id.value if event.previous else "none",
            event.current.spec.action_id.value if event.current else "idle",
            event.reason,
        )
        if event.current is None:
            # Entering idle (action ended naturally or was invalidated):
            # the idle loop restarts from its first frame.
            self._idle_timeline.reset(IdleResetReason.ACTION_END)

    # -- startup health -------------------------------------------------------------

    def _check_startup_health(self) -> None:
        try:
            if self.startup_manager.needs_repair():
                QTimer.singleShot(
                    4000,
                    lambda: self.overlay.show_bubble(
                        "检测到开机自启指向旧位置，右键菜单可修复", 6000
                    ),
                )
        except Exception:  # noqa: BLE001
            logger.exception("startup health check failed")

    # -- menus ------------------------------------------------------------------------

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        self._populate_menu(menu)
        return menu

    def _populate_tray_menu(self, menu: QMenu) -> None:
        """Tray context menu = fixed recovery commands + the full menu.

        The five fixed commands (显示 / 关闭穿透 / 恢复主屏 / 控制面板 / 退出,
        DESIGN_V2 11.2 & ADR-V2-012) must survive click-through, hidden
        windows, lost screens and broken characters - the tray is the only
        input path that cannot be taken away from the user.
        """
        show_action = QAction("显示桌宠", menu)
        show_action.triggered.connect(lambda: self._show_window(activate=True))
        menu.addAction(show_action)

        disable_ct = QAction("关闭鼠标穿透", menu)
        disable_ct.triggered.connect(lambda: self._set_click_through(False))
        menu.addAction(disable_ct)

        restore_screen = QAction("恢复到主屏幕", menu)
        restore_screen.triggered.connect(lambda: self._restore_to_primary_screen())
        menu.addAction(restore_screen)

        panel_action = QAction("打开控制面板", menu)
        panel_action.triggered.connect(lambda: self._open_control_panel())
        menu.addAction(panel_action)

        menu.addSeparator()
        self._populate_menu(menu)

    def _populate_menu(self, menu: QMenu) -> None:
        toggle = QAction("显示 / 隐藏宠物", menu)
        toggle.triggered.connect(self._toggle_window)
        menu.addAction(toggle)

        todo_action = QAction("打开待办", menu)
        todo_action.triggered.connect(
            lambda checked=False: self._open_control_panel("todo"))
        menu.addAction(todo_action)

        menu.addSeparator()

        act_menu = menu.addMenu("动作")
        for action_id, label in (
            (ActionId.IDLE, "待机"),
            (ActionId.WORK, "工作"),
            (ActionId.REST, "休息"),
            (ActionId.EAT, "干饭"),
            (ActionId.EXERCISE, "健身"),
        ):
            act = QAction(label, act_menu)
            if action_id is ActionId.IDLE:
                # 待机 = end the current performance and clear the user's own
                # facts; the resolver then re-derives from remaining facts
                # (user intent never fakes or deletes other services' facts).
                act.triggered.connect(
                    lambda: self._user_standby()
                )
            else:
                act.triggered.connect(
                    lambda checked=False, a=action_id: self._service_port.request(
                        a, "user"
                    )
                )
            act_menu.addAction(act)

        meeting = QAction("会议模式", act_menu)
        meeting.setCheckable(True)
        meeting.setChecked(self.schedule.manual_meeting)
        meeting.triggered.connect(lambda checked: self.schedule.set_manual_meeting(checked))
        act_menu.addAction(meeting)
        menu.addMenu(act_menu)

        meal_menu = menu.addMenu("干饭提醒")
        for label, handler in (
            ("开始吃饭", lambda: self.schedule.mark_started("meal")),
            ("稍后提醒", lambda: self.schedule.snooze("meal")),
            ("今天跳过", lambda: self.schedule.skip_today("meal")),
        ):
            act = QAction(label, meal_menu)
            act.triggered.connect(lambda checked=False, h=handler: h())
            meal_menu.addAction(act)

        exercise_menu = menu.addMenu("健身提醒")
        for label, handler in (
            ("开始健身", lambda: self.schedule.mark_started("exercise")),
            ("稍后提醒", lambda: self.schedule.snooze("exercise")),
            ("今天跳过", lambda: self.schedule.skip_today("exercise")),
        ):
            act = QAction(label, exercise_menu)
            act.triggered.connect(lambda checked=False, h=handler: h())
            exercise_menu.addAction(act)

        music_menu = menu.addMenu("音乐")
        for label, handler in (
            ("播放 / 暂停", self.audio.toggle),
            ("上一首", self.audio.previous),
            ("下一首", self.audio.next),
        ):
            act = QAction(label, music_menu)
            act.triggered.connect(lambda checked=False, h=handler: h())
            music_menu.addAction(act)
        music_menu.addSeparator()
        add_music = QAction("添加音乐文件...", music_menu)
        add_music.triggered.connect(self._add_music_files)
        music_menu.addAction(add_music)
        menu.addMenu(music_menu)

        menu.addSeparator()

        try:
            characters = self.catalog.entries()
        except Exception:  # noqa: BLE001 - broken catalog keeps the safe cat
            characters = []
        if characters:
            character_menu = menu.addMenu("切换角色")
            for entry in characters:
                label = f"{entry.display_name}（{entry.series_id}）"
                act = QAction(label, character_menu)
                act.triggered.connect(
                    lambda checked=False, e=entry: self._switch_character(e))
                character_menu.addAction(act)

        panel = QAction("显示倒计时面板", menu)
        panel.setCheckable(True)
        panel.setChecked(self.window.panel_expanded)
        panel.triggered.connect(lambda checked: self._set_panel_expanded(checked))
        menu.addAction(panel)

        click_through = QAction("鼠标穿透", menu)
        click_through.setCheckable(True)
        click_through.setChecked(bool(self.settings.get("click_through", False)))
        click_through.triggered.connect(lambda checked: self._set_click_through(checked))
        menu.addAction(click_through)

        on_top = QAction("始终置顶", menu)
        on_top.setCheckable(True)
        on_top.setChecked(bool(self.settings.get("always_on_top", True)))
        on_top.triggered.connect(lambda checked: self._set_always_on_top(checked))
        menu.addAction(on_top)

        autostart = QAction("开机自动启动", menu)
        autostart.setCheckable(True)
        autostart.setChecked(self.startup_manager.is_enabled())
        autostart.triggered.connect(lambda checked: self._set_autostart(checked))
        menu.addAction(autostart)

        if self.startup_manager.needs_repair():
            repair = QAction("修复开机自启", menu)
            repair.triggered.connect(lambda: self._repair_autostart())
            menu.addAction(repair)

        settings_action = QAction("设置...", menu)
        settings_action.triggered.connect(lambda: self._open_settings())
        menu.addAction(settings_action)

        menu.addSeparator()
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

    # -- settings application -----------------------------------------------------------

    def _set_panel_expanded(self, expanded: bool) -> None:
        self.window.set_panel_expanded(expanded)
        self.settings.set("countdown_panel_expanded", expanded)
        self.settings.save()

    def _set_click_through(self, enabled: bool) -> None:
        self.window.set_click_through(enabled)
        self._refresh_todo_hotkey_after_window_flags()
        self.settings.set("click_through", enabled)
        self.settings.save()
        if enabled:
            self.overlay.show_bubble("已开启鼠标穿透，从托盘菜单恢复", 4000)

    def _set_always_on_top(self, enabled: bool) -> None:
        self.window.set_always_on_top(enabled)
        self._refresh_todo_hotkey_after_window_flags()
        self.settings.set("always_on_top", enabled)
        self.settings.save()

    def _refresh_todo_hotkey_after_window_flags(self) -> None:
        """Rebind if Qt recreated the native HWND while changing flags."""
        if getattr(self, "_headless", True) \
                or not getattr(self, "_todo_hotkey_filter_installed", False):
            return
        self._todo_hotkey.attach(self.window)

    def _set_autostart(self, enabled: bool) -> None:
        if self.startup_manager.set_enabled(enabled):
            self.settings.set("startup_enabled", enabled)
            self.settings.save()
        else:
            self.overlay.show_bubble("无法修改开机自启（注册表不可用）", 4000,
                                   importance="error")

    def _repair_autostart(self) -> None:
        if self.startup_manager.repair():
            self.overlay.show_bubble("开机自启已修复", 3000)
        else:
            self.overlay.show_bubble("修复失败，请检查权限", 3000,
                                   importance="error")

    def _add_music_files(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        paths, _ = QFileDialog.getOpenFileNames(
            self.window,
            "选择音乐文件",
            "",
            "音频文件 (*.mp3 *.wav *.flac *.ogg *.m4a *.aac);;所有文件 (*.*)",
        )
        if not paths:
            return
        added = 0
        for path in paths:
            if self.audio.add_track(path):  # validates existence
                added += 1
        if added:
            self.settings.set("music_paths", self.audio.tracks)
            self.settings.save()
            self.overlay.show_bubble(f"已添加 {added} 首歌", 2500)
        else:
            self.overlay.show_bubble("没有可用的音乐文件", 3000,
                                   importance="error")

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings.as_dict(), self._apply_settings, self.window)
        dialog.exec()

    def _apply_settings(self, values: dict) -> bool:
        before = self.settings.as_dict()
        music_paths_before = self.settings.get("music_paths", [])
        self.settings.update(values)
        if not self.settings.save():
            # Persistence is the commit point.  Keep runtime and in-memory
            # state aligned with the last durable settings on failure; a
            # key that did not exist before must not linger in memory
            # (CR-C04 discipline applies to every apply payload key).
            rollback = {key: before[key] for key in values
                        if key in before}
            self.settings.update(rollback)
            for key in values:
                if key not in before:
                    self.settings.discard(key)
            logger.warning("settings update was not persisted")
            return False
        # Apply immediately-visible effects.
        self.window.set_always_on_top(
            bool(self.settings.get("always_on_top", True)))
        self.audio.set_volume(float(self.settings.get("volume", 0.35)))
        self.audio.set_sound_enabled(
            bool(self.settings.get("sound_enabled", True)))
        self._sync_music_tracks()
        if self.settings.get("music_paths", []) != music_paths_before:
            self.overlay.show_bubble("音乐列表已更新", 2500)
        if "media_bridge_enabled" in values:
            # V13-05: the explicit user opt-in starts/stops the whole
            # bridge (subscriptions released when off)
            wanted = bool(values["media_bridge_enabled"])
            if wanted != self.media_bridge.is_enabled():
                if self.media_bridge.set_enabled(wanted):
                    if wanted:
                        self.overlay.show_bubble("已开启系统媒体联动", 2500)
                else:
                    self.overlay.show_bubble(
                        "系统媒体联动不可用：未检测到系统媒体会话服务", 3500)
        logger.info("settings updated: %s", sorted(values.keys()))
        return True

    # -- V12-06: layout/visibility, text templates, manual actions ----------

    def _active_character_fqid(self) -> str | None:
        active = self._selection_store.get("active")
        return active.character_fqid if active is not None else None

    def _apply_layout_config(self) -> None:
        """Resolve the persisted layout × policy and apply it to the pet.

        Persisted values were combination-validated at save time; a value
        written by an older version or by hand still falls back to the
        engine default instead of crashing here.
        """
        try:
            view_model = resolve_layout(
                str(self.ui_config.effective("layout_id")),
                str(self.ui_config.effective("visibility_policy")),
            )
        except ValueError:
            logger.warning("invalid persisted layout combination; "
                           "using standard+normal")
            view_model = resolve_layout("standard", "normal")
        self.overlay.set_bubble_policy(view_model["bubbles"])
        self.window.apply_layout_view_model(view_model)
        self._sync_countdown_heading()

    def _text_template_variables(self) -> dict[str, str]:
        snap = self._countdown_cache
        fqid = self._active_character_fqid()
        character_name = "退休猫"
        series_name = "退休猫"
        if fqid is not None:
            # display names come from the catalog entry (manifest), never
            # from the fqid suffix
            active = self._selection_store.get("active")
            character_id = fqid.rsplit(".", 1)[-1]
            for entry in self.catalog.entries():
                if entry.character_id == character_id and (
                        active is None
                        or entry.revision_key == active.revision_key()):
                    character_name = entry.display_name
                    series_name = entry.series_id
                    break
        return {
            "character_name": character_name,
            "series_name": series_name,
            "stage_name": snap.stage_text if snap is not None else "",
            "days": str(snap.days) if snap is not None else "",
            "hours": str(snap.hours) if snap is not None else "",
            "minutes": str(snap.minutes) if snap is not None else "",
        }

    def _render_text_template(self, key: str) -> str | None:
        """Render a user template key for the active character; None means
        the built-in default text should be used.

        effective() itself validates what it resolves (CR-C01), so the
        whole chain stays inside the guard: no persisted value may ever
        escape as an exception from a Qt callback."""
        try:
            template = str(self.ui_config.effective(
                key, self._active_character_fqid()))
            return render_text(
                template, self._text_template_variables())
        except TextSafetyError:
            logger.warning("text template for %s failed safety checks", key)
            return None

    def _sync_countdown_heading(self) -> None:
        self.window.set_countdown_heading(
            self._render_text_template("countdown_text"))

    def _mode_request_gate(self, action_id: ActionId, source: str,
                           force: bool) -> bool:
        """Reject requests for semantics the user set to disabled.

        Safety discipline is untouched: this can only ever REMOVE an
        action from consideration, never admit one that discipline or
        cooldowns would have blocked.
        """
        if self._action_mode(action_id.value) != "disabled":
            return True
        logger.info(
            "action request for %s from %s vetoed: semantic disabled",
            action_id.value, source)
        return False

    def _action_mode(self, semantic: str) -> str:
        modes = self.settings.get("ui_action_modes", {})
        value = modes.get(semantic, "auto") if isinstance(modes, dict) \
            else "auto"
        return value if value in ("auto", "manual", "disabled") else "auto"

    def _set_action_setting(self, settings_key: str, semantic: str,
                            value) -> bool:
        """Persist one per-action override with exact-presence rollback
        (CR-C04): a settings key that did not exist before a failed save
        must not linger in memory, where it would take effect immediately
        and reach disk through the next unrelated successful save."""
        raw = self.settings.get(settings_key, None)
        existed = self.settings.get(settings_key, _ACTION_SETTING_ABSENT) \
            is not _ACTION_SETTING_ABSENT
        mapping = dict(raw) if isinstance(raw, dict) else {}
        mapping[semantic] = value
        self.settings.set(settings_key, mapping)
        if self.settings.save():
            return True
        if existed and isinstance(raw, dict):
            self.settings.set(settings_key, raw)
        else:
            self.settings.discard(settings_key)
        return False

    def _set_action_mode(self, semantic: str, mode: str) -> bool:
        if mode not in ("auto", "manual", "disabled"):
            return False
        if not self._set_action_setting("ui_action_modes", semantic, mode):
            return False
        if mode == "disabled":
            # an unbounded runtime started before the switch would
            # otherwise keep playing (loop actions have no duration end)
            self.controller.end_if_action(
                ActionId(semantic), "mode:disabled")
        return True

    def _action_single_pass(self, semantic: str) -> tuple[int, int] | None:
        """Frame count and one-pass duration of the ACTIVE character's
        material, or None when it has no sequence pass.

        C06-R2: control availability and playback timing come from the
        current PetPack's declared per-frame durations - never from the
        built-in bundle's frame count over an assumed fps.
        """
        runtime = self.switcher.current_runtime
        if runtime is not None and hasattr(runtime, "semantic_sequence_ms"):
            durations = runtime.semantic_sequence_ms(f"core.{semantic}")
            if durations:
                return len(durations), sum(durations)
            return None
        # built-in cat: a sequence visual (if any) is timed by spec fps,
        # the one declared timing authority for that material
        try:
            spec = self.controller.spec_of(ActionId(semantic))
        except ValueError:
            return None
        visual = self.bundle.action_visual(semantic)
        frames = getattr(visual, "frames", None)
        if frames and spec.animation_fps > 0:
            return len(frames), max(1, len(frames) * 1000
                                    // spec.animation_fps)
        return None

    def _action_loop(self, semantic: str) -> bool:
        loops = self.settings.get("ui_action_loops", {})
        value = loops.get(semantic, True) if isinstance(loops, dict) else True
        return bool(value)

    def _set_action_loop(self, semantic: str, loop: bool) -> bool:
        return self._set_action_setting("ui_action_loops", semantic,
                                        bool(loop))

    def _request_manual_action(self, semantic: str) -> tuple[bool, str]:
        """User-triggered action through every engine gate, in order.

        Capability -> per-action mode -> meeting discipline -> dnd
        discipline -> cooldown.  The force request bypasses priority, so
        the discipline gates MUST run before it.
        """
        semantic = str(semantic).strip()
        try:
            action_id = ActionId(semantic)
        except ValueError:
            return False, "未知动作"
        character_actions = {a.rsplit(".", 1)[-1]
                             for a in self.capabilities.character_actions}
        core_key = "core.idle" if semantic == "idle" \
            else f"core.{semantic}"
        supported = (
            self.capabilities.supports(semantic)
            or self.capabilities.supports(core_key)
            or semantic in character_actions
            or self.controller.has_action(action_id)
        )
        if not supported:
            return False, "当前角色缺少该动作素材，已回退待机"
        mode = self._action_mode(semantic)
        if mode == "disabled":
            return False, "该动作已禁用，先在动作页改回自动或仅手动"
        active_contexts = {c for c in self.contexts.active()}
        if ContextId.MEETING in active_contexts \
                and semantic not in MEETING_SAFE_SEMANTICS:
            return False, "会议中已禁止该动作"
        if ContextId.MEETING in active_contexts \
                and semantic == "music" and self.audio.sound_enabled:
            return False, "会议中仅静音音乐可用，请先静音"
        if ContextId.DO_NOT_DISTURB in active_contexts \
                and semantic not in DND_SAFE_SEMANTICS:
            return False, "勿扰中仅休息或音乐可用"
        current = self.controller.current
        if current is not None and current.spec.action_id == action_id:
            return False, "该动作正在表演中"
        remaining = self.controller.cooldown_remaining_ms(action_id)
        if remaining > 0:
            return False, f"冷却中，还剩约 {remaining // 1000 + 1} 秒"
        payload: dict | None = None
        spec = self.controller.spec_of(action_id)
        if spec.max_duration_ms is None:
            payload = {"loop": self._action_loop(semantic)}
            if not payload["loop"]:
                # single pass = the ACTIVE material's full declared cycle
                # once; without sequence material this stays the honest
                # minimum-duration fallback (and the switch is disabled)
                material = self._action_single_pass(semantic)
                if material is not None:
                    payload["single_pass_ms"] = material[1]
        accepted = self.controller.request(
            action_id, "panel", force=True, payload=payload)
        if accepted:
            if payload and payload.get("single_pass_ms"):
                return True, (
                    f"已触发：{semantic}（单次播放素材一遍，"
                    f"{payload['single_pass_ms'] / 1000:.1f} 秒）")
            return True, f"已触发：{semantic}"
        return False, "当前表演优先级更高，稍后再试"
