"""M6 TextProfile: allowlisted variables, plain text, hostile input blocked."""

from __future__ import annotations

import pytest

from retirement_pet.text_profile import (
    SAFE_VARIABLES,
    TextProfile,
    TextSafetyError,
    render,
)


def test_safe_variable_substitution():
    out = render("还有 {days} 天，{character_name} 陪着你", {
        "days": 12345, "character_name": "退休猫"})
    assert out == "还有 12345 天，退休猫 陪着你"


def test_unknown_token_stays_literal():
    assert render("hi {user_profile_path}", {}) == "hi {user_profile_path}"


def test_format_specifier_is_not_interpreted():
    # the format mini-language must never run: braces stay literal
    out = render("v={days!r}", {"days": 5})
    assert out == "v={days!r}"


@pytest.mark.parametrize("evil", (
    "{__class__}",
    "{days.__real__}",
    "{days[0]}",
    "{days.foo}",
    "{os.environ}",
))
def test_attribute_index_and_env_access_impossible(evil):
    # every non-allowlisted token renders literally - nothing is evaluated
    assert render(evil, {"days": 1}) == evil


def test_control_and_bidi_characters_rejected():
    with pytest.raises(TextSafetyError):
        render("ok\x00gone", {})
    with pytest.raises(TextSafetyError):
        render("override\u202edirection", {})


def test_markup_characters_rejected():
    with pytest.raises(TextSafetyError):
        render("<b>bold</b>", {})


def test_variable_values_are_checked_too():
    with pytest.raises(TextSafetyError):
        render("hi {character_name}", {"character_name": "bad\x07bell"})


def test_oversized_template_rejected():
    with pytest.raises(TextSafetyError):
        render("x" * 501, {})


def test_profile_semantic_lookup_and_pool():
    profile = TextProfile({
        "event.clicked.reply": ["在呢", "今天也陪着你"],
        "action.core.work.caption": "一起专心一会儿",
    })
    assert profile.render("action.core.work.caption", {}) == "一起专心一会儿"
    assert profile.render("event.clicked.reply", {}) == "在呢"  # deterministic
    assert profile.render("system.unknown.key", {}, default="fallback") == "fallback"


def test_allowlist_is_exact():
    assert SAFE_VARIABLES == frozenset({
        "character_name", "days", "hours", "minutes", "series_name",
        "stage_name"})
