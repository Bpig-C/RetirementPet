"""Predefined layouts and visibility policies (M6; DESIGN_V2 12; ADR-V2-011/022).

Five named layouts and three named visibility presets.  They combine only
through the ENGINE allow-matrix - never a free boolean cartesian product -
and no combination may end up with an invisible character that still
intercepts input.  Packs may recommend known layout IDs; unknown
recommendations degrade with a warning and are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LayoutId(str, Enum):
    PET_ONLY = "pet_only"
    COMPACT = "compact"
    STANDARD = "standard"
    HOVER_EXPAND = "hover_expand"
    FOCUS = "focus"


class VisibilityPolicy(str, Enum):
    NORMAL = "normal"
    QUIET = "quiet"
    DND = "dnd"


@dataclass(frozen=True)
class LayoutSpec:
    layout_id: LayoutId
    character_visible: bool
    countdown: str                    # hidden | badge | standard | hover | summary
    allowed_policies: frozenset[VisibilityPolicy]


@dataclass(frozen=True)
class PolicySpec:
    policy_id: VisibilityPolicy
    context_markers: str              # full | reduced | dnd_only
    bubbles: str                      # all | important | safe_errors_only
    decorations: str                  # full | reduced | hidden


#: ENGINE allow-matrix (frozen; extend by fixture, not by UI freedom)
ALLOWED: dict[LayoutId, LayoutSpec] = {
    LayoutId.PET_ONLY: LayoutSpec(
        LayoutId.PET_ONLY, True, "hidden",
        frozenset({VisibilityPolicy.NORMAL, VisibilityPolicy.QUIET,
                   VisibilityPolicy.DND})),
    LayoutId.COMPACT: LayoutSpec(
        LayoutId.COMPACT, True, "badge",
        frozenset({VisibilityPolicy.NORMAL, VisibilityPolicy.QUIET,
                   VisibilityPolicy.DND})),
    LayoutId.STANDARD: LayoutSpec(
        LayoutId.STANDARD, True, "standard",
        frozenset({VisibilityPolicy.NORMAL, VisibilityPolicy.QUIET})),
    LayoutId.HOVER_EXPAND: LayoutSpec(
        LayoutId.HOVER_EXPAND, True, "hover",
        frozenset({VisibilityPolicy.NORMAL, VisibilityPolicy.QUIET,
                   VisibilityPolicy.DND})),
    LayoutId.FOCUS: LayoutSpec(
        LayoutId.FOCUS, True, "summary",
        frozenset({VisibilityPolicy.NORMAL, VisibilityPolicy.QUIET,
                   VisibilityPolicy.DND})),
}

POLICIES: dict[VisibilityPolicy, PolicySpec] = {
    VisibilityPolicy.NORMAL: PolicySpec(
        VisibilityPolicy.NORMAL, "full", "all", "full"),
    VisibilityPolicy.QUIET: PolicySpec(
        VisibilityPolicy.QUIET, "reduced", "important", "reduced"),
    VisibilityPolicy.DND: PolicySpec(
        VisibilityPolicy.DND, "dnd_only", "safe_errors_only", "hidden"),
}

#: packs may only recommend these IDs; anything else warns and is ignored
RECOMMENDABLE_LAYOUT_IDS = frozenset(l.value for l in LayoutId)


def combination_allowed(layout: str, policy: str) -> bool:
    try:
        spec = ALLOWED[LayoutId(layout)]
        return VisibilityPolicy(policy) in spec.allowed_policies
    except (ValueError, KeyError):
        return False


def is_invisible_click_blocker(layout: str, policy: str) -> bool:
    """True for combinations that would hide the character while still
    intercepting desktop clicks - the matrix makes these impossible."""
    spec = ALLOWED.get(LayoutId(layout)) if layout in RECOMMENDABLE_LAYOUT_IDS \
        else None
    if spec is None:
        return False
    return (not spec.character_visible) and policy != VisibilityPolicy.DND.value


def resolve_layout(layout: str, policy: str) -> dict:
    """Validated view model for the renderer; raises on forbidden combos."""
    if layout not in RECOMMENDABLE_LAYOUT_IDS:
        raise ValueError(f"unknown layout: {layout}")
    if not combination_allowed(layout, policy):
        raise ValueError(f"layout/policy combination not allowed: "
                         f"{layout}+{policy}")
    spec = ALLOWED[LayoutId(layout)]
    pol = POLICIES[VisibilityPolicy(policy)]
    return {
        "layout": spec.layout_id.value,
        "character_visible": spec.character_visible,
        "countdown": spec.countdown,
        "context_markers": pol.context_markers,
        "bubbles": pol.bubbles,
        "decorations": pol.decorations,
    }


def recommend_layout(layout: str) -> tuple[str | None, str | None]:
    """Pack recommendation gate: returns (accepted_layout, warning_code)."""
    if layout in RECOMMENDABLE_LAYOUT_IDS:
        return layout, None
    return None, "UX-LYT-W001"  # unknown recommendation: ignored with warning
