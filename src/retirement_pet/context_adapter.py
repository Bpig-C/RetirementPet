"""Adapter layer: v1 domain services speak Context, not actions (M2).

DESIGN_V2 5.2: modules turn product features into Context/Event/Overlay
requests; they never mutate the main action directly.  v1's
RhythmController / ScheduleManager / RandomActionScheduler were built
against the ActionController surface (request / end_if_action /
end_current / is_busy_with_higher_priority).  :class:`ContextServicePort`
implements that same surface by translating calls into ContextStore facts,
so the services run unmodified while the semantics become fact-based.

:class:`PerformanceBridge` closes the loop on the other side: on every
context change it re-resolves the primary performance and drives the
ActionController with the resolved render action.  Resolving from facts on
every change means there IS no action stack to restore (ADR-V2-004).
"""

from __future__ import annotations

import logging
from datetime import datetime

from retirement_pet.models import ActionId
from retirement_pet.runtime_state import (
    CORE_IDLE,
    CharacterCapabilities,
    ContextId,
    ContextStore,
    Emote,
    PerformanceResolver,
    PrimaryPerformance,
)

logger = logging.getLogger(__name__)

#: character actions of the built-in retirement cat (its own namespace; the
#: FQID is "engine.retirement-cat" because the official pack ships embedded)
CAT_FQID = "engine.retirement-cat.retirement-cat.cat"
CAT_CHARACTER_ACTIONS = frozenset({
    "engine.retirement-cat.retirement-cat.cat.stretch",
    "engine.retirement-cat.retirement-cat.cat.lick_paw",
    "engine.retirement-cat.retirement-cat.cat.look_around",
    "engine.retirement-cat.retirement-cat.cat.yawn",
    "engine.retirement-cat.retirement-cat.cat.interact",
    "engine.retirement-cat.retirement-cat.cat.work_variants",  # reserved
})

#: core semantic -> cat ActionId (the cat supports every core semantic)
CAT_SEMANTICS: dict[str, str] = {
    CORE_IDLE: ActionId.IDLE.value,
    "core.work": ActionId.WORK.value,
    "core.rest": ActionId.REST.value,
    "core.eat": ActionId.EAT.value,
    "core.exercise": ActionId.EXERCISE.value,
    "core.meeting": ActionId.MEETING.value,
    "core.music": ActionId.MUSIC.value,
}

#: random-express candidate -> cat character action id
CAT_RANDOM_ACTIONS: dict[str, str] = {
    "stretch": "engine.retirement-cat.retirement-cat.cat.stretch",
    "lick_paw": "engine.retirement-cat.retirement-cat.cat.lick_paw",
    "look_around": "engine.retirement-cat.retirement-cat.cat.look_around",
    "yawn": "engine.retirement-cat.retirement-cat.cat.yawn",
}

CAT_CLICKED = "engine.retirement-cat.retirement-cat.cat.interact"


def cat_capabilities() -> CharacterCapabilities:
    return CharacterCapabilities(
        character_fqid=CAT_FQID,
        semantics=dict(CAT_SEMANTICS),
        character_actions=frozenset(
            CAT_RANDOM_ACTIONS.values()) | {CAT_CLICKED},
    )


class ContextServicePort:
    """The ActionController-shaped surface v1 services use, backed by facts.

    ``request(WORK)`` sets the working fact owned by ``source``;
    ``end_if_action(WORK)`` clears it.  High-priority probe stays an
    approximation over active facts until M2 services are fact-native.
    """

    def __init__(self, store: ContextStore, rng=None):
        self._store = store
        self._fact_for_action: dict[ActionId, ContextId] = {
            ActionId.WORK: ContextId.WORKING,
            ActionId.REST: ContextId.RESTING,
            ActionId.EAT: ContextId.EATING,
            ActionId.EXERCISE: ContextId.EXERCISING,
            ActionId.MEETING: ContextId.MEETING,
            ActionId.MUSIC: ContextId.MUSIC,
        }

    def request(self, action_id: ActionId, source: str, force: bool = False,
                payload: dict | None = None, requested_at: datetime | None = None
                ) -> bool:
        context_id = self._fact_for_action.get(action_id)
        if context_id is None:
            logger.debug("service request for non-fact action %s ignored", action_id)
            return False
        return self._store.set(context_id, source)

    def end_if_action(self, action_id: ActionId, reason: str = "invalidated") -> bool:
        context_id = self._fact_for_action.get(action_id)
        if context_id is None:
            return False
        return self._store.clear(context_id)

    def end_current(self, reason: str = "invalidated") -> bool:
        """'My discipline state no longer holds.'

        v1 services call this only when a MEETING window closes
        (manual off / window over), so the port ends discipline facts.
        The bridge then re-resolves - restoring any other true fact
        (e.g. work) instead of a blank idle.
        """
        changed = False
        for context_id in (ContextId.MEETING, ContextId.DO_NOT_DISTURB):
            changed |= self._store.clear(context_id)
        return changed

    def is_busy_with_higher_priority(self, priority: int) -> bool:
        # v1 rhythm uses this to avoid interrupting discipline states.
        return bool(self._store.active() & {ContextId.MEETING,
                                            ContextId.DO_NOT_DISTURB})

    # -- v1 read surface: derive "current action" from facts -----------------

    def current_action(self) -> ActionId | None:
        """The action the CURRENT FACTS imply (services' read view)."""
        fact_for_action = {
            ActionId.WORK: ContextId.WORKING,
            ActionId.REST: ContextId.RESTING,
            ActionId.EAT: ContextId.EATING,
            ActionId.EXERCISE: ContextId.EXERCISING,
            ActionId.MEETING: ContextId.MEETING,
            ActionId.MUSIC: ContextId.MUSIC,
        }
        for action_id, context_id in fact_for_action.items():
            if self._store.is_active(context_id):
                return action_id
        return None

    @property
    def current(self):
        """Mimic the executor's ``current`` read for simple probes."""

        class _View:
            __slots__ = ("spec",)

            def __init__(self, action_id: ActionId | None):
                self.spec = None if action_id is None else _SpecView(action_id)

        action_id = self.current_action()
        return None if action_id is None else _View(action_id)


class _SpecView:
    __slots__ = ("action_id",)

    def __init__(self, action_id: ActionId):
        self.action_id = action_id


class PerformanceBridge:
    """Re-resolves on every context change and drives the ActionController."""

    def __init__(self, store: ContextStore, resolver: PerformanceResolver,
                 controller, capabilities: CharacterCapabilities):
        self._store = store
        self._resolver = resolver
        self._controller = controller
        self._capabilities = capabilities
        self.last_performance: PrimaryPerformance | None = None
        self._missing_listener = None
        store.on_change(self._on_contexts_changed)

    def set_missing_semantics_listener(self, listener) -> None:
        self._missing_listener = listener

    def set_capabilities(
            self, capabilities: CharacterCapabilities, *,
            reason: str = "character_capabilities_changed",
    ) -> PrimaryPerformance:
        """Publish a character capability table and reconcile current facts.

        A character swap changes the resolver's input even when no Context
        changed.  Updating the pointer without applying it would leave the
        previous character's action active until some unrelated later event.
        """
        self._capabilities = capabilities
        return self.apply(reason)

    def _action_id(self, perf: PrimaryPerformance) -> ActionId | None:
        render = perf.render_action
        if render is None:
            return None
        try:
            return ActionId(render)
        except ValueError:
            return None

    def _on_contexts_changed(self) -> None:
        self.apply("context_changed")

    def apply(self, reason: str) -> PrimaryPerformance:
        perf = self._resolver.resolve(self._capabilities)
        self.last_performance = perf
        action_id = self._action_id(perf)
        if action_id is None or action_id is ActionId.IDLE:
            self._controller.end_current(f"resolve:{reason}")
        else:
            # The resolver already owns priority decisions; force the executor.
            accepted = self._controller.request(
                action_id, f"resolve:{reason}", force=True)
            if not accepted:
                # App-level veto (e.g. semantic disabled): the resolver's
                # choice is unplayable, so a performance left over from an
                # earlier resolve must not outlive the facts that produced
                # it (C06-R1) - discipline contexts stay true regardless.
                self._controller.end_current(f"resolve:{reason}:vetoed")
        if perf.missing_semantics and self._missing_listener is not None:
            try:
                self._missing_listener(perf.missing_semantics)
            except Exception:  # noqa: BLE001
                logger.exception("missing-semantics listener failed")
        return perf

    def emote_to_action(self, emote: Emote) -> ActionId | None:
        try:
            return ActionId(emote.semantic.rsplit(".", 1)[-1])
        except ValueError:
            return None
