"""Core data models shared across the app.

Definitions (specs) are immutable; runtime state is kept separately and is
only mutated by :class:`retirement_pet.action_controller.ActionController`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class ActionId(str, Enum):
    IDLE = "idle"
    BLINK = "blink"  # overlay-only; never a main action
    STRETCH = "stretch"
    LICK_PAW = "lick_paw"
    LOOK_AROUND = "look_around"
    YAWN = "yawn"
    INTERACT = "interact"
    WORK = "work"
    REST = "rest"
    EAT = "eat"
    EXERCISE = "exercise"
    MEETING = "meeting"
    MUSIC = "music"

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.value


#: Actions that may be scheduled randomly (design 8.2).
RANDOM_ACTION_IDS: tuple[ActionId, ...] = (
    ActionId.STRETCH,
    ActionId.LICK_PAW,
    ActionId.LOOK_AROUND,
    ActionId.YAWN,
)

#: Actions rendered as the main pose; blink is an overlay, idle is implicit.
OVERLAY_ONLY_ACTIONS: frozenset[ActionId] = frozenset({ActionId.BLINK})


class LifeStage(str, Enum):
    YOUNG = "young"
    MIDDLE = "middle"
    OLD = "old"
    RETIRED = "retired"


@dataclass(frozen=True)
class ActionSpec:
    action_id: ActionId
    priority: int
    min_duration_ms: int
    max_duration_ms: int | None
    cooldown_ms: int
    interruptible: bool
    loop: bool
    animation_fps: int
    weight: float = 0.0

    @property
    def finite(self) -> bool:
        return self.max_duration_ms is not None


@dataclass(frozen=True)
class ActionRequest:
    action_id: ActionId
    source: str
    requested_at: datetime
    force: bool = False
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ActionRuntime:
    spec: ActionSpec
    started_at_ms: int
    duration_ms: int | None
    payload: dict[str, Any] = field(default_factory=dict)
    frame_index: int = 0

    def elapsed_ms(self, now_ms: int) -> int:
        return max(0, now_ms - self.started_at_ms)

    def frame(self, now_ms: int) -> int:
        """Frame index derived from elapsed time and spec fps."""
        if self.spec.animation_fps <= 0:
            return 0
        return int(self.elapsed_ms(now_ms) * self.spec.animation_fps / 1000.0)


@dataclass(frozen=True)
class OverlaySnapshot:
    """Visual overlay state sampled by the renderer each frame."""

    blink: bool = False
    gaze_x: float = 0.0  # normalized -1..1
    gaze_y: float = 0.0
    effects: tuple[str, ...] = ()
    bubble_text: str | None = None


@dataclass(frozen=True)
class RenderSnapshot:
    """Everything the renderer needs to draw one frame."""

    action: ActionId
    stage: LifeStage
    elapsed_ms: int
    frame: int
    time_ms: int  # animation-clock time, drives breath/tail phases
    overlay: OverlaySnapshot = field(default_factory=OverlaySnapshot)
    assets_available: bool = False  # True when PNG layers were used
