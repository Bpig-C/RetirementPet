"""Configuration ownership and effective-value resolution (M6; DESIGN_V2 15).

Every config key declares owner/scope/allowed_sources/merge_policy; there
is NO generic deep-merge of arbitrary JSON.  Effective values resolve per
key through the frozen 8-level priority, skipping sources the key does not
allow:

    1 session preview
    2 per-character user override
    3 global user override
    4 selected profile
    5 user-applied recommendation snapshot
    6 character-specific default
    7 module default
    8 engine default

Semantics (ADR-V2-009): missing, null and empty are DIFFERENT; arrays
replace as a whole; a removed action's settings become orphans instead of
silently applying elsewhere; every resolved value can explain its source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class Source(str, Enum):
    SESSION_PREVIEW = "session_preview"
    CHARACTER_OVERRIDE = "character_override"
    GLOBAL_OVERRIDE = "global_override"
    PROFILE = "profile"
    RECOMMENDATION = "recommendation_snapshot"
    CHARACTER_DEFAULT = "character_default"
    MODULE_DEFAULT = "module_default"
    ENGINE_DEFAULT = "engine_default"


#: frozen resolution order (DESIGN_V2 15)
PRIORITY: tuple[Source, ...] = (
    Source.SESSION_PREVIEW,
    Source.CHARACTER_OVERRIDE,
    Source.GLOBAL_OVERRIDE,
    Source.PROFILE,
    Source.RECOMMENDATION,
    Source.CHARACTER_DEFAULT,
    Source.MODULE_DEFAULT,
    Source.ENGINE_DEFAULT,
)


class MergePolicy(str, Enum):
    REPLACE = "replace"        # scalars and arrays: whole-value replace
    ORPHAN_ON_REMOVE = "orphan_on_remove"  # action-scoped entries


@dataclass(frozen=True)
class ConfigKey:
    key: str
    owner: str                       # "user" | "module" | "engine" | "pack"
    scope: str                       # "global" | "character"
    allowed_sources: frozenset[Source]
    merge_policy: MergePolicy = MergePolicy.REPLACE
    validator: Any = None            # callable(value) -> value or raises


class ConfigResolutionError(ValueError):
    pass


def _validate(key: ConfigKey, value: Any) -> Any:
    if key.validator is None or value is None:
        return value
    return key.validator(value)


@dataclass
class EffectiveConfigResolver:
    """Layered per-key resolution with explainable sources."""

    schema: Mapping[str, ConfigKey]
    layers: dict[Source, dict[str, Any]] = field(default_factory=dict)

    def set_layer(self, source: Source, values: Mapping[str, Any]) -> None:
        self.layers[source] = dict(values)

    def update_layer(self, source: Source, key: str, value: Any) -> None:
        self.layers.setdefault(source, {})[key] = value

    def clear_layer(self, source: Source) -> None:
        self.layers.pop(source, None)

    def resolve(self, key: str, *, character_fqid: str | None = None) -> Any:
        source, value = self.resolve_with_source(key, character_fqid=character_fqid)
        return value

    def resolve_with_source(self, key: str, *,
                            character_fqid: str | None = None
                            ) -> tuple[Source, Any]:
        config = self.schema.get(key)
        if config is None:
            raise ConfigResolutionError(f"unknown config key: {key}")

        for source in PRIORITY:
            if source not in config.allowed_sources:
                continue
            layer = self.layers.get(source)
            if not layer or key not in layer:
                continue
            value = layer[key]
            if source is Source.CHARACTER_OVERRIDE:
                # per-character values only apply to that character
                scoped = value.get(character_fqid, _MISSING) \
                    if isinstance(value, dict) else _MISSING
                if scoped is _MISSING:
                    continue
                value = scoped
            if source is Source.CHARACTER_DEFAULT:
                scoped = value.get(character_fqid, _MISSING) \
                    if isinstance(value, dict) else _MISSING
                if scoped is _MISSING:
                    continue
                value = scoped
            return source, _validate(config, value)

        # an engine default is mandatory for every declared key
        engine = self.layers.get(Source.ENGINE_DEFAULT, {})
        raise ConfigResolutionError(f"no engine default for key: {key}")

    def resolved(self, *, character_fqid: str | None = None) -> dict[str, Any]:
        return {key: self.resolve(key, character_fqid=character_fqid)
                for key in self.schema}

    def provenance(self, *, character_fqid: str | None = None) -> dict[str, Source]:
        return {key: self.resolve_with_source(key, character_fqid=character_fqid)[0]
                for key in self.schema}

    # -- reset operations (DESIGN_V2 15: resets name their exact scope) -----

    def reset_field(self, key: str, source: Source) -> bool:
        layer = self.layers.get(source)
        if layer and key in layer:
            del layer[key]
            return True
        return False

    def reset_page(self, keys: list[str], source: Source) -> int:
        layer = self.layers.get(source, {})
        removed = 0
        for key in keys:
            if key in layer:
                del layer[key]
                removed += 1
        return removed

    # -- orphans ---------------------------------------------------------------

    @staticmethod
    def orphans(action_keys: list[str], character_overrides: Mapping[str, Any]
                ) -> dict[str, Any]:
        """Action settings whose action no longer exists move to the orphan
        shelf; they are retained (never deleted, never reapplied)."""
        return {k: v for k, v in character_overrides.items()
                if k not in action_keys}


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover
        return "<missing>"


_MISSING = _Missing()
