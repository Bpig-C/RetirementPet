"""Role-agnostic runtime state (DESIGN_V2 6; M2).

- **ContextStore**: long-lived facts (working, meeting, music...) that may
  coexist.  Facts are only set by trusted services or explicit user
  commands; random actions can never clear a fact.
- **PerformanceResolver**: derives the ONE primary performance from the
  current facts, an optional user request, an optional emote and the
  character's capability table.  Re-resolving from facts replaces the old
  action-return-stack (ADR-V2-004): when an emote or temporary performance
  ends, the next performance is computed fresh from the current context.
- Discipline matrix (DESIGN_V2 6.2): meeting / do-not-disturb forbid random
  performances, restrict user requests to safe ones and gate emotes.
- **Namespaces** (PETPACK_SPEC 4.2): ``core.*`` is Engine-owned; a
  character-specific action id MUST be prefixed with its character FQID and
  can never override a core semantic.

Pure Python, injectable clock - no Qt, fully deterministic under test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Mapping


# -- core semantics (Engine-owned; PETPACK_SPEC 7.1) ---------------------------

CORE_IDLE = "core.idle"
CORE_CLICKED = "core.clicked"
CORE_ENTER = "core.enter"
CORE_EXIT = "core.exit"
CORE_REST = "core.rest"
CORE_WORK = "core.work"
CORE_EAT = "core.eat"
CORE_EXERCISE = "core.exercise"
CORE_MEETING = "core.meeting"
CORE_MUSIC = "core.music"

#: performance sources a random scheduler may propose (v1 cat expresses
#: these through character-specific actions, NOT core semantics)
RANDOM_EXPRESS_CANDIDATES = ("stretch", "lick_paw", "look_around", "yawn")

CORE_CONTEXT_PREFIX = "core.context."


class ContextId(str, Enum):
    WORKING = "core.context.working"
    RESTING = "core.context.resting"
    EATING = "core.context.eating"
    EXERCISING = "core.context.exercising"
    MEETING = "core.context.meeting"
    MUSIC = "core.context.music"
    DO_NOT_DISTURB = "core.context.do_not_disturb"

    @property
    def semantic(self) -> str:
        """The core performance semantic this fact maps to."""
        return {
            ContextId.WORKING: CORE_WORK,
            ContextId.RESTING: CORE_REST,
            ContextId.EATING: CORE_EAT,
            ContextId.EXERCISING: CORE_EXERCISE,
            ContextId.MEETING: CORE_MEETING,
            ContextId.MUSIC: CORE_MUSIC,
            ContextId.DO_NOT_DISTURB: CORE_REST,  # dnd renders as quiet rest
        }[self]


#: facts that carry discipline rules (DESIGN_V2 6.2)
DISCIPLINE_CONTEXTS = frozenset({ContextId.MEETING, ContextId.DO_NOT_DISTURB})

#: resolution order for coexisting facts (later wins if several are active)
CONTEXT_PRECEDENCE: tuple[ContextId, ...] = (
    ContextId.MEETING,
    ContextId.DO_NOT_DISTURB,
    ContextId.WORKING,
    ContextId.EATING,
    ContextId.EXERCISING,
    ContextId.MUSIC,
    ContextId.RESTING,
)


def is_valid_character_action_id(character_fqid: str, action_id: str) -> bool:
    """A character-specific action MUST live in its CharacterFQID namespace."""
    return action_id.startswith(f"{character_fqid}.")


# -- capability table -----------------------------------------------------------


@dataclass(frozen=True)
class CharacterCapabilities:
    """What a character can express (resolved from its pack, M2: built-in cat).

    ``semantics`` maps core semantic -> character-specific render action key.
    ``character_actions`` are namespaced extra actions (e.g. random idle
    flourishes) this character can perform on request.
    """

    character_fqid: str
    semantics: Mapping[str, str] = field(default_factory=dict)
    character_actions: frozenset[str] = frozenset()

    def supports(self, semantic: str) -> bool:
        return semantic in self.semantics

    def render_action(self, semantic: str) -> str | None:
        return self.semantics.get(semantic)


# -- primary performance ----------------------------------------------------------


@dataclass(frozen=True)
class PrimaryPerformance:
    """The single main performance for this moment (DESIGN_V2 6.2)."""

    semantic: str                      # core semantic OR character action id
    render_action: str | None          # character-specific key, None if missing
    source: str                        # user | emote | context | random | idle
    missing_semantics: tuple[str, ...] = ()  # facts with no capability (engine overlay)


# -- context store ---------------------------------------------------------------

ContextChangeCallback = Callable[[], None]


class ContextStore:
    """Long-lived facts; multiple may coexist (ADR-V2-004)."""

    def __init__(self):
        self._facts: dict[ContextId, str] = {}  # id -> owner token
        self._listeners: list[ContextChangeCallback] = []

    def on_change(self, callback: ContextChangeCallback) -> Callable[[], None]:
        self._listeners.append(callback)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def _emit(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # noqa: BLE001 - listeners must not break facts
                pass

    def set(self, context_id: ContextId, owner: str) -> bool:
        """Activate a fact.  Returns True when the set changed."""
        if self._facts.get(context_id) == owner:
            return False
        self._facts[context_id] = owner
        self._emit()
        return True

    def clear(self, context_id: ContextId, owner: str | None = None) -> bool:
        """Deactivate a fact (optionally only when owned by ``owner``)."""
        if context_id not in self._facts:
            return False
        if owner is not None and self._facts[context_id] != owner:
            return False
        del self._facts[context_id]
        self._emit()
        return True

    def clear_owned_by(self, owner: str) -> None:
        for context_id in list(self._facts):
            if self._facts[context_id] == owner:
                del self._facts[context_id]
        self._emit()

    def is_active(self, context_id: ContextId) -> bool:
        return context_id in self._facts

    def active(self) -> frozenset[ContextId]:
        return frozenset(self._facts)

    def owner_of(self, context_id: ContextId) -> str | None:
        return self._facts.get(context_id)


# -- performance resolver ----------------------------------------------------------

class PerformanceResolver:
    """Solves the primary performance from facts + requests + capabilities."""

    def __init__(self, context_store: ContextStore):
        self._contexts = context_store
        self._user_request: PrimaryPerformance | None = None
        self._random_candidate: str | None = None

    # -- requests ---------------------------------------------------------

    def set_user_request(self, semantic: str, capabilities: CharacterCapabilities,
                         *, character_action: bool = False) -> bool:
        """Explicit user choice; checked against the discipline matrix.

        Character actions must be namespaced (they can never shadow core.*).
        """
        if not character_action:
            if semantic == CORE_IDLE:
                self._user_request = None
                return True
            if not semantic.startswith("core."):
                return False  # non-core without namespace is invalid
        else:
            if not is_valid_character_action_id(capabilities.character_fqid, semantic):
                return False
            if semantic in capabilities.character_actions:
                self._user_request = PrimaryPerformance(
                    semantic=semantic, render_action=semantic,
                    source="user",
                )
                return True
            return False

        # discipline matrix for core-semantic user requests
        if not self._user_request_allowed(semantic):
            return False
        self._user_request = PrimaryPerformance(
            semantic=semantic,
            render_action=capabilities.render_action(semantic),
            source="user",
            missing_semantics=() if capabilities.supports(semantic) else (semantic,),
        )
        return True

    def clear_user_request(self) -> None:
        self._user_request = None

    def set_random_candidate(self, candidate: str | None) -> None:
        """Random scheduler proposal; the resolver applies the matrix."""
        self._random_candidate = candidate

    def _user_request_allowed(self, semantic: str) -> bool:
        active = self._contexts.active()
        if ContextId.MEETING in active:
            # meeting: only silent, meeting-safe semantics
            return semantic in (CORE_MEETING, CORE_REST)
        if ContextId.DO_NOT_DISTURB in active:
            return semantic == CORE_REST
        return True

    # -- resolution ---------------------------------------------------------

    def resolve(self, capabilities: CharacterCapabilities) -> PrimaryPerformance:
        facts = self._contexts.active()

        meeting = ContextId.MEETING in facts
        dnd = ContextId.DO_NOT_DISTURB in facts

        # 1. user request (already filtered at request time)
        if self._user_request is not None:
            return self._with_idle_fallback(self._user_request, capabilities)

        # 2. discipline facts win over everything except explicit user intent
        if meeting:
            return self._context_performance(ContextId.MEETING, capabilities)
        if dnd:
            return self._context_performance(ContextId.DO_NOT_DISTURB, capabilities)

        # 3. highest-precedence non-discipline fact
        for context_id in CONTEXT_PRECEDENCE:
            if context_id in facts:
                return self._context_performance(context_id, capabilities)

        # 4. random candidate (forbidden under discipline facts, already out)
        if self._random_candidate is not None:
            candidate = self._random_candidate
            if (candidate in capabilities.character_actions
                    and is_valid_character_action_id(
                        capabilities.character_fqid, candidate)):
                return PrimaryPerformance(
                    semantic=candidate, render_action=candidate, source="random"
                )
            # unknown candidate: fall through to idle (never guess semantics)

        # 5. core.idle
        return PrimaryPerformance(
            semantic=CORE_IDLE,
            render_action=capabilities.render_action(CORE_IDLE),
            source="idle",
        )

    def _context_performance(self, context_id: ContextId,
                             capabilities: CharacterCapabilities) -> PrimaryPerformance:
        semantic = context_id.semantic
        if capabilities.supports(semantic):
            return PrimaryPerformance(
                semantic=semantic,
                render_action=capabilities.render_action(semantic),
                source="context",
            )
        # missing capability: keep the fact, fall back to THIS character's idle
        return PrimaryPerformance(
            semantic=CORE_IDLE,
            render_action=capabilities.render_action(CORE_IDLE),
            source="context_fallback",
            missing_semantics=(semantic,),
        )

    def _with_idle_fallback(self, perf: PrimaryPerformance,
                            capabilities: CharacterCapabilities) -> PrimaryPerformance:
        # ``_user_request`` stores semantic intent.  Its render_action was
        # resolved against the character that accepted the request and must
        # never be borrowed after a character switch.
        if perf.semantic.startswith("core."):
            if capabilities.supports(perf.semantic):
                return PrimaryPerformance(
                    semantic=perf.semantic,
                    render_action=capabilities.render_action(perf.semantic),
                    source=perf.source,
                )
        elif (perf.semantic in capabilities.character_actions
              and is_valid_character_action_id(
                  capabilities.character_fqid, perf.semantic)):
            return PrimaryPerformance(
                semantic=perf.semantic,
                render_action=perf.semantic,
                source=perf.source,
            )
        return PrimaryPerformance(
            semantic=CORE_IDLE,
            render_action=capabilities.render_action(CORE_IDLE),
            source=f"{perf.source}_fallback",
            missing_semantics=(perf.semantic,),
        )


# -- emotes -----------------------------------------------------------------------

@dataclass
class Emote:
    """A short, expirable expression request (DESIGN_V2 6.4)."""

    semantic: str                    # character action id or core.clicked
    started_ms: int
    duration_ms: int
    silent: bool = False


class EmoteController:
    """Tracks the single active emote; expiry triggers a re-resolve."""

    def __init__(self, on_expired: Callable[[], None] | None = None):
        self._current: Emote | None = None
        self._on_expired = on_expired

    def request(self, semantic: str, now_ms: int, duration_ms: int, *,
                silent: bool = False, force: bool = False) -> bool:
        # discipline gate: meeting/dnd only allow silent, short emotes
        facts = self._facts
        if (ContextId.MEETING in facts or ContextId.DO_NOT_DISTURB in facts):
            if not silent or duration_ms > 2000:
                return False
        if self._current is not None and not force:
            return False
        self._current = Emote(semantic=semantic, started_ms=now_ms,
                              duration_ms=duration_ms, silent=silent)
        return True

    #: facts provider injected by the app (avoids a store dependency cycle)
    _facts: frozenset[ContextId] = frozenset()

    def bind_contexts(self, store: ContextStore) -> None:
        self._facts = store.active()
        store.on_change(lambda: setattr(self, "_facts", store.active()))

    def current(self, now_ms: int) -> Emote | None:
        emote = self._current
        if emote is None:
            return None
        if now_ms - emote.started_ms >= emote.duration_ms:
            self._current = None
            if self._on_expired is not None:
                try:
                    self._on_expired()
                except Exception:  # noqa: BLE001
                    pass
            return None
        return emote

    def clear(self) -> None:
        self._current = None
