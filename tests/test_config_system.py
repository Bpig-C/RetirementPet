"""M6 config semantics: priority, missing/null/empty, arrays, orphans."""

from __future__ import annotations

import pytest

from retirement_pet.config_system import (
    ConfigKey,
    ConfigResolutionError,
    EffectiveConfigResolver,
    MergePolicy,
    Source,
)


def make_resolver() -> EffectiveConfigResolver:
    schema = {
        "volume": ConfigKey(
            key="volume", owner="user", scope="global",
            allowed_sources=frozenset({
                Source.SESSION_PREVIEW, Source.CHARACTER_OVERRIDE,
                Source.GLOBAL_OVERRIDE, Source.PROFILE,
                Source.RECOMMENDATION, Source.CHARACTER_DEFAULT,
                Source.MODULE_DEFAULT, Source.ENGINE_DEFAULT}),
            validator=lambda v: max(0.0, min(1.0, float(v)))),
        "action.core.work": ConfigKey(
            key="action.core.work", owner="user", scope="character",
            allowed_sources=frozenset({
                Source.SESSION_PREVIEW, Source.CHARACTER_OVERRIDE,
                Source.GLOBAL_OVERRIDE, Source.RECOMMENDATION,
                Source.CHARACTER_DEFAULT, Source.MODULE_DEFAULT,
                Source.ENGINE_DEFAULT}),
            merge_policy=MergePolicy.ORPHAN_ON_REMOVE),
        "autostart": ConfigKey(
            key="autostart", owner="user", scope="global",
            # system keys: profile and pack recommendation can never own these
            allowed_sources=frozenset({
                Source.GLOBAL_OVERRIDE, Source.ENGINE_DEFAULT})),
        "meal_times": ConfigKey(
            key="meal_times", owner="user", scope="global",
            allowed_sources=frozenset({
                Source.GLOBAL_OVERRIDE, Source.MODULE_DEFAULT,
                Source.ENGINE_DEFAULT})),
    }
    resolver = EffectiveConfigResolver(schema)
    resolver.set_layer(Source.ENGINE_DEFAULT, {
        "volume": 0.35, "action.core.work": "auto",
        "autostart": False, "meal_times": [],
    })
    resolver.set_layer(Source.MODULE_DEFAULT, {"meal_times": ["08:00", "12:00"]})
    return resolver


def test_engine_default_is_last_resort():
    assert make_resolver().resolve("volume") == 0.35


def test_module_default_beats_engine_default():
    resolver = make_resolver()
    assert resolver.resolve("meal_times") == ["08:00", "12:00"]


def test_full_priority_chain_volume():
    resolver = make_resolver()
    resolver.set_layer(Source.PROFILE, {"volume": 0.5})
    resolver.set_layer(Source.GLOBAL_OVERRIDE, {"volume": 0.6})
    resolver.set_layer(Source.CHARACTER_OVERRIDE,
                       {"volume": {"cat": 0.7}})
    resolver.set_layer(Source.SESSION_PREVIEW, {"volume": 0.9})

    assert resolver.resolve("volume", character_fqid="cat") == 0.9
    resolver.clear_layer(Source.SESSION_PREVIEW)
    assert resolver.resolve("volume", character_fqid="cat") == 0.7
    # another character does not inherit the per-character override
    assert resolver.resolve("volume", character_fqid="other") == 0.6
    resolver.clear_layer(Source.GLOBAL_OVERRIDE)
    assert resolver.resolve("volume") == 0.5


def test_provenance_is_explainable():
    resolver = make_resolver()
    resolver.set_layer(Source.GLOBAL_OVERRIDE, {"volume": 0.6})
    source, _ = resolver.resolve_with_source("volume")
    assert source is Source.GLOBAL_OVERRIDE
    assert resolver.provenance()["meal_times"] is Source.MODULE_DEFAULT


def test_disallowed_source_is_skipped():
    resolver = make_resolver()
    resolver.set_layer(Source.RECOMMENDATION, {"autostart": True})
    assert resolver.resolve("autostart") is False  # pack cannot own this


def test_missing_null_and_empty_are_different():
    resolver = make_resolver()
    resolver.set_layer(Source.GLOBAL_OVERRIDE, {
        "meal_times": None})  # null = user cleared the value
    assert resolver.resolve("meal_times") is None
    resolver.update_layer(Source.GLOBAL_OVERRIDE, "meal_times", [])
    assert resolver.resolve("meal_times") == []  # empty = explicitly none
    del resolver.layers[Source.GLOBAL_OVERRIDE]["meal_times"]
    assert resolver.resolve("meal_times") == ["08:00", "12:00"]  # missing = fall through


def test_arrays_replace_whole():
    resolver = make_resolver()
    resolver.update_layer(Source.GLOBAL_OVERRIDE, "meal_times", ["09:00"])
    assert resolver.resolve("meal_times") == ["09:00"]  # not merged with module


def test_validator_clamps_invalid_value():
    resolver = make_resolver()
    resolver.set_layer(Source.GLOBAL_OVERRIDE, {"volume": 7})
    assert resolver.resolve("volume") == 1.0


def test_unknown_key_raises():
    with pytest.raises(ConfigResolutionError):
        make_resolver().resolve("nope")


def test_orphans_retained_not_applied():
    orphans = EffectiveConfigResolver.orphans(
        action_keys=["action.core.work"],
        character_overrides={
            "action.core.work": "manual",
            "action.retired.nap": "disabled",  # action no longer exists
        })
    assert orphans == {"action.retired.nap": "disabled"}


def test_reset_operations_name_scope():
    resolver = make_resolver()
    resolver.set_layer(Source.GLOBAL_OVERRIDE, {"volume": 0.6, "autostart": True})
    assert resolver.reset_field("volume", Source.GLOBAL_OVERRIDE) is True
    assert resolver.resolve("volume") == 0.35
    assert resolver.reset_page(["volume", "autostart"],
                               Source.GLOBAL_OVERRIDE) == 1  # volume already gone
    assert resolver.resolve("autostart") is False


def test_character_default_is_scoped():
    resolver = make_resolver()
    resolver.set_layer(Source.CHARACTER_DEFAULT,
                       {"volume": {"cat": 0.25}})
    assert resolver.resolve("volume", character_fqid="cat") == 0.25
    assert resolver.resolve("volume", character_fqid="dog") == 0.35
