"""M6 layout matrix: allowed combinations, no invisible click blockers."""

from __future__ import annotations

import pytest

from retirement_pet.layout_policy import (
    ALLOWED,
    LayoutId,
    VisibilityPolicy,
    combination_allowed,
    is_invisible_click_blocker,
    recommend_layout,
    resolve_layout,
)


ALL_LAYOUTS = [l.value for l in LayoutId]
ALL_POLICIES = [p.value for p in VisibilityPolicy]


def test_every_declared_combination_resolves():
    count = 0
    for layout in ALL_LAYOUTS:
        for policy in ALL_POLICIES:
            if combination_allowed(layout, policy):
                view = resolve_layout(layout, policy)
                assert view["character_visible"] is True  # character always visible
                count += 1
    assert count >= 12  # the frozen matrix declares at least these


def test_forbidden_combinations_raise():
    # standard countdown layout has no DND preset (frozen matrix)
    with pytest.raises(ValueError):
        resolve_layout("standard", "dnd")


def test_unknown_layout_or_policy_never_allowed():
    assert combination_allowed("free_drag", "normal") is False
    assert combination_allowed("compact", "max_stealth") is False
    with pytest.raises(ValueError):
        resolve_layout("free_drag", "normal")


def test_no_combination_hides_character_while_clickable():
    for layout in ALL_LAYOUTS:
        for policy in ALL_POLICIES:
            assert not is_invisible_click_blocker(layout, policy)


def test_pack_recommendations_only_accept_known_ids():
    assert recommend_layout("compact") == ("compact", None)
    accepted, warning = recommend_layout("my_custom_floating_layout")
    assert accepted is None
    assert warning == "UX-LYT-W001"


def test_dnd_policy_hides_decorations_and_limits_bubbles():
    view = resolve_layout("pet_only", "dnd")
    assert view["decorations"] == "hidden"
    assert view["bubbles"] == "safe_errors_only"
    assert view["context_markers"] == "dnd_only"
