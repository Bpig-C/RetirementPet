"""Registry of action definitions (design section 7/8).

Priorities follow design 7.1: meeting 90 > meal/exercise 75 > interaction 65
> work/rest 50 > random 20 > idle 0.  User force requests bypass priority.
"""

from __future__ import annotations

from retirement_pet.models import ActionId, ActionSpec

DEFAULT_SPECS: dict[ActionId, ActionSpec] = {
    ActionId.IDLE: ActionSpec(
        action_id=ActionId.IDLE,
        priority=0,
        min_duration_ms=0,
        max_duration_ms=None,
        cooldown_ms=0,
        interruptible=True,
        loop=True,
        animation_fps=16,
    ),
    ActionId.WORK: ActionSpec(
        action_id=ActionId.WORK,
        priority=50,
        min_duration_ms=10_000,
        max_duration_ms=None,
        cooldown_ms=0,
        interruptible=True,
        loop=True,
        animation_fps=16,
    ),
    ActionId.REST: ActionSpec(
        action_id=ActionId.REST,
        priority=50,
        min_duration_ms=10_000,
        max_duration_ms=None,
        cooldown_ms=0,
        interruptible=True,
        loop=True,
        animation_fps=8,
    ),
    ActionId.EAT: ActionSpec(
        action_id=ActionId.EAT,
        priority=75,
        min_duration_ms=4_000,
        max_duration_ms=9_000,
        cooldown_ms=0,
        interruptible=False,
        loop=False,
        animation_fps=8,
    ),
    ActionId.EXERCISE: ActionSpec(
        action_id=ActionId.EXERCISE,
        priority=75,
        min_duration_ms=30_000,
        max_duration_ms=120_000,
        cooldown_ms=0,
        interruptible=True,
        loop=False,
        animation_fps=12,
    ),
    ActionId.MEETING: ActionSpec(
        action_id=ActionId.MEETING,
        priority=90,
        min_duration_ms=10_000,
        max_duration_ms=None,
        cooldown_ms=0,
        interruptible=False,
        loop=True,
        animation_fps=8,
    ),
    ActionId.MUSIC: ActionSpec(
        action_id=ActionId.MUSIC,
        priority=65,
        min_duration_ms=5_000,
        max_duration_ms=None,
        cooldown_ms=0,
        interruptible=True,
        loop=True,
        animation_fps=8,
    ),
    ActionId.INTERACT: ActionSpec(
        action_id=ActionId.INTERACT,
        priority=65,
        min_duration_ms=600,
        max_duration_ms=1_500,
        cooldown_ms=2_000,
        interruptible=True,
        loop=False,
        animation_fps=16,
    ),
    ActionId.STRETCH: ActionSpec(
        action_id=ActionId.STRETCH,
        priority=20,
        min_duration_ms=1_500,
        max_duration_ms=2_600,
        cooldown_ms=120_000,
        interruptible=True,
        loop=False,
        animation_fps=8,
        weight=1.0,
    ),
    ActionId.LICK_PAW: ActionSpec(
        action_id=ActionId.LICK_PAW,
        priority=20,
        min_duration_ms=2_000,
        max_duration_ms=3_200,
        cooldown_ms=180_000,
        interruptible=True,
        loop=False,
        animation_fps=8,
        weight=1.0,
    ),
    ActionId.LOOK_AROUND: ActionSpec(
        action_id=ActionId.LOOK_AROUND,
        priority=20,
        min_duration_ms=1_400,
        max_duration_ms=2_400,
        cooldown_ms=150_000,
        interruptible=True,
        loop=False,
        animation_fps=8,
        weight=0.8,
    ),
    ActionId.YAWN: ActionSpec(
        action_id=ActionId.YAWN,
        priority=20,
        min_duration_ms=1_800,
        max_duration_ms=2_600,
        cooldown_ms=240_000,
        interruptible=True,
        loop=False,
        animation_fps=8,
        weight=0.6,
    ),
}

#: Engine-frozen discipline for manually requested actions (DESIGN_V2 6.2;
#: V12-06 actions page).  During a meeting only muted meeting-safe requests
#: pass; under do-not-disturb only dnd-safe actions pass.  The UI must gate
#: on these BEFORE calling the controller - force requests bypass priority
#: and would otherwise bypass discipline too.
MEETING_SAFE_SEMANTICS = frozenset({"music"})
DND_SAFE_SEMANTICS = frozenset({"music", "rest"})


class ActionRegistry:
    def __init__(self, specs: dict[ActionId, ActionSpec] | None = None):
        self._specs = dict(specs or DEFAULT_SPECS)

    def get(self, action_id: ActionId) -> ActionSpec:
        return self._specs[action_id]

    def has(self, action_id: ActionId) -> bool:
        return action_id in self._specs

    def all_ids(self) -> tuple[ActionId, ...]:
        return tuple(self._specs)

    def replace(self, spec: ActionSpec) -> None:
        self._specs[spec.action_id] = spec
