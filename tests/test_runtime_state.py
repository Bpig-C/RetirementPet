"""M2 runtime state: facts, resolver discipline, emotes, namespaces (M2 gate)."""

from __future__ import annotations

import pytest

from retirement_pet.context_adapter import (
    CAT_CLICKED,
    CAT_RANDOM_ACTIONS,
    cat_capabilities,
)
from retirement_pet.runtime_state import (
    CORE_EAT,
    CORE_IDLE,
    CORE_MEETING,
    CORE_REST,
    CORE_WORK,
    CharacterCapabilities,
    ContextId,
    ContextStore,
    EmoteController,
    PerformanceResolver,
    is_valid_character_action_id,
)


@pytest.fixture()
def store():
    return ContextStore()


@pytest.fixture()
def resolver(store):
    return PerformanceResolver(store)


@pytest.fixture()
def cat():
    return cat_capabilities()


@pytest.fixture()
def idle_only():
    """A minimal character: idle + its own click flourish, no core actions."""
    return CharacterCapabilities(
        character_fqid="community.example.demo-pack.demo-series.demo",
        semantics={CORE_IDLE: "demo.idle"},
        character_actions=frozenset({
            "community.example.demo-pack.demo-series.demo.wave"}),
    )


# -- ContextStore: facts are long-lived and concurrent --------------------------


def test_multiple_contexts_coexist(store):
    store.set(ContextId.WORKING, "rhythm")
    store.set(ContextId.MUSIC, "audio")
    assert store.active() == {ContextId.WORKING, ContextId.MUSIC}


def test_clear_owned_by_only_removes_own_facts(store):
    store.set(ContextId.WORKING, "rhythm")
    store.set(ContextId.MUSIC, "audio")
    store.clear_owned_by("rhythm")
    assert store.active() == {ContextId.MUSIC}


def test_change_notification_fires(store):
    events = []
    store.on_change(lambda: events.append(1))
    store.set(ContextId.WORKING, "rhythm")
    store.clear(ContextId.WORKING)
    store.clear(ContextId.WORKING)  # no change -> no event
    assert len(events) == 2


# -- resolver: discipline matrix (DESIGN_V2 6.2) ---------------------------------


def test_meeting_forbids_random_and_resolves_meeting(store, resolver, cat):
    store.set(ContextId.MEETING, "schedule")
    perf = resolver.resolve(cat)
    assert perf.semantic == CORE_MEETING

    resolver.set_random_candidate(CAT_RANDOM_ACTIONS["yawn"])
    assert resolver.resolve(cat).semantic == CORE_MEETING


def test_meeting_blocks_unsafe_user_requests(store, resolver, cat):
    store.set(ContextId.MEETING, "schedule")
    assert resolver.set_user_request(CORE_WORK, cat) is False
    assert resolver.set_user_request(CORE_REST, cat) is True  # meeting-safe


def test_dnd_allows_only_rest(store, resolver, cat):
    store.set(ContextId.DO_NOT_DISTURB, "user")
    assert resolver.set_user_request(CORE_WORK, cat) is False
    assert resolver.set_user_request(CORE_REST, cat) is True
    perf = resolver.resolve(cat)
    assert perf.semantic == CORE_REST


def test_working_context_allows_random(store, resolver, cat):
    store.set(ContextId.WORKING, "rhythm")
    assert resolver.resolve(cat).semantic == CORE_WORK
    # random proposals under a non-discipline fact resolve to the fact
    resolver.set_random_candidate(CAT_RANDOM_ACTIONS["stretch"])
    assert resolver.resolve(cat).semantic == CORE_WORK


# -- resolver: precedence and fallback --------------------------------------------


def test_meeting_beats_working(store, resolver, cat):
    store.set(ContextId.WORKING, "rhythm")
    store.set(ContextId.MEETING, "schedule")
    assert resolver.resolve(cat).semantic == CORE_MEETING
    store.clear(ContextId.MEETING)
    assert resolver.resolve(cat).semantic == CORE_WORK


def test_user_request_beats_context(store, resolver, cat):
    store.set(ContextId.WORKING, "rhythm")
    resolver.set_user_request(CORE_REST, cat)
    assert resolver.resolve(cat).semantic == CORE_REST
    resolver.clear_user_request()
    assert resolver.resolve(cat).semantic == CORE_WORK


def test_idle_when_nothing_active(store, resolver, cat):
    perf = resolver.resolve(cat)
    assert perf.semantic == CORE_IDLE
    assert perf.source == "idle"


# -- missing capabilities: keep the fact, fall back to THIS character's idle -----


def test_missing_semantic_falls_back_to_own_idle(store, resolver, idle_only):
    store.set(ContextId.WORKING, "rhythm")
    perf = resolver.resolve(idle_only)
    assert perf.render_action == "demo.idle"  # NOT another character's body
    assert perf.source == "context_fallback"
    assert perf.missing_semantics == ("core.work",)


def test_missing_semantic_user_request_falls_back(resolver, idle_only):
    assert resolver.set_user_request(CORE_EAT, idle_only) is True
    perf = resolver.resolve(idle_only)
    assert perf.render_action == "demo.idle"
    assert perf.missing_semantics == (CORE_EAT,)


def test_core_user_request_rebinds_to_current_character_capabilities(
        resolver, cat, idle_only):
    """A request stores intent, never another character's render mapping."""
    assert resolver.set_user_request(CORE_WORK, cat)
    assert resolver.resolve(cat).render_action == "work"

    demo = resolver.resolve(idle_only)
    assert demo.semantic == CORE_IDLE
    assert demo.render_action == "demo.idle"
    assert demo.source == "user_fallback"
    assert demo.missing_semantics == (CORE_WORK,)

    restored = resolver.resolve(cat)
    assert restored.semantic == CORE_WORK
    assert restored.render_action == "work"
    assert restored.missing_semantics == ()


def test_character_user_request_never_crosses_character_namespace(
        resolver, idle_only):
    from retirement_pet.runtime_state import CharacterCapabilities

    old_fqid = "community.example.old-pack.series.old"
    old_action = f"{old_fqid}.wave"
    old = CharacterCapabilities(
        character_fqid=old_fqid,
        semantics={CORE_IDLE: "old.idle"},
        character_actions=frozenset({old_action}),
    )
    assert resolver.set_user_request(
        old_action, old, character_action=True)
    assert resolver.resolve(old).render_action == old_action

    # Membership alone is not authority: a malformed capability table must
    # not smuggle an old namespace into the current character's allow-list.
    spoofed = CharacterCapabilities(
        character_fqid=idle_only.character_fqid,
        semantics=idle_only.semantics,
        character_actions=frozenset({old_action}),
    )
    current = resolver.resolve(spoofed)
    assert current.semantic == CORE_IDLE
    assert current.render_action == "demo.idle"
    assert current.source == "user_fallback"
    assert current.missing_semantics == (old_action,)


# -- namespaces: character actions can never override core.* ----------------------


def test_character_action_requires_namespace(idle_only):
    assert is_valid_character_action_id(
        "community.example.demo-pack.demo-series.demo",
        "community.example.demo-pack.demo-series.demo.wave",
    )
    assert not is_valid_character_action_id(
        "community.example.demo-pack.demo-series.demo", "wave")
    # core.* ids are never valid character action ids
    assert not is_valid_character_action_id(
        "community.example.demo-pack.demo-series.demo", CORE_WORK)


def test_namespaced_action_resolves_from_user_request(resolver, idle_only):
    fq = "community.example.demo-pack.demo-series.demo.wave"
    assert resolver.set_user_request(
        fq, idle_only, character_action=True) is True
    assert resolver.resolve(idle_only).render_action == fq


def test_unknown_character_action_is_rejected(resolver, idle_only):
    fq = "community.example.demo-pack.demo-series.demo.dance"
    assert resolver.set_user_request(
        fq, idle_only, character_action=True) is False


def test_random_candidate_without_capability_falls_to_idle(resolver, idle_only):
    resolver.set_random_candidate(CAT_RANDOM_ACTIONS["yawn"])  # other cat's action
    perf = resolver.resolve(idle_only)
    assert perf.semantic == CORE_IDLE
    assert perf.render_action == "demo.idle"


def test_random_candidate_membership_cannot_spoof_another_character_namespace(
        resolver, idle_only):
    old_action = "community.example.old-pack.series.old.wave"
    spoofed = CharacterCapabilities(
        character_fqid=idle_only.character_fqid,
        semantics=idle_only.semantics,
        character_actions=frozenset({old_action}),
    )
    resolver.set_random_candidate(old_action)
    perf = resolver.resolve(spoofed)
    assert perf.semantic == CORE_IDLE
    assert perf.render_action == "demo.idle"


# -- emotes: short, expirable, gated, re-resolve on end (no action stack) ---------


def test_emote_expires_and_triggers_callback(store):
    fired = []
    emotes = EmoteController(on_expired=lambda: fired.append(1))
    emotes.bind_contexts(store)
    assert emotes.request("wave", now_ms=1000, duration_ms=500) is True
    assert emotes.current(1200) is not None
    assert emotes.current(1500) is None  # expired
    assert fired == [1]
    # re-resolve happens from facts: no old action is "restored"


def test_emote_blocked_in_meeting_unless_short_and_silent(store):
    store.set(ContextId.MEETING, "schedule")
    emotes = EmoteController()
    emotes.bind_contexts(store)
    assert emotes.request("wave", now_ms=0, duration_ms=3000) is False
    assert emotes.request("wave", now_ms=0, duration_ms=800, silent=True) is True


def test_single_emote_at_a_time(store):
    emotes = EmoteController()
    emotes.bind_contexts(store)
    assert emotes.request("wave", now_ms=0, duration_ms=1000) is True
    assert emotes.request("nod", now_ms=200, duration_ms=1000) is False


# -- RetirementCountdownModule: user-owned target ----------------------------------


def test_countdown_target_survives_settings_rebuild():
    from datetime import datetime

    from retirement_pet.countdown_module import RetirementCountdownModule

    settings = {"target_datetime": "2065-01-01T00:00:00"}
    now = datetime(2026, 8, 28, 12, 0, 0)
    module = RetirementCountdownModule(settings, lambda: now)
    first = module.refresh()
    # rebuilding the module (runtime reconstruction) must not move the target
    rebuilt = RetirementCountdownModule(settings, lambda: now)
    second = rebuilt.refresh()
    assert first.target == second.target


def test_countdown_invalid_user_value_uses_default():
    from datetime import datetime

    from retirement_pet.countdown_module import (
        DEFAULT_TARGET_TEXT,
        RetirementCountdownModule,
    )

    module = RetirementCountdownModule(
        {"target_datetime": "not-a-date"}, lambda: datetime(2026, 8, 28))
    assert module.refresh() is not None
    assert module.target.isoformat().startswith(DEFAULT_TARGET_TEXT[:10])
