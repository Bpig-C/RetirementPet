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

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from retirement_pet.layout_policy import combination_allowed
from retirement_pet.text_profile import check_plain_text

logger = logging.getLogger(__name__)


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


# -- V12-06: engine schema for user-facing UI configuration -------------------

def _valid_layout(value: Any) -> str:
    from retirement_pet.layout_policy import LayoutId

    return LayoutId(str(value)).value


def _valid_policy(value: Any) -> str:
    from retirement_pet.layout_policy import VisibilityPolicy

    return VisibilityPolicy(str(value)).value


def _valid_template(value: Any) -> str:
    return check_plain_text(str(value))


#: text consumers the user may template (semantic key -> engine default).
#: ``countdown_text`` renders as the panel heading; the default reproduces
#: the built-in ``stage_text`` exactly, so unset means no visual change.
TEXT_TEMPLATE_DEFAULTS: dict[str, str] = {
    "countdown_text": "{stage_name}",
    "greeting_text": "{character_name}陪着你",
}

UI_CONFIG_SCHEMA: dict[str, ConfigKey] = {
    "layout_id": ConfigKey(
        "layout_id", "user", "global",
        frozenset({Source.GLOBAL_OVERRIDE, Source.ENGINE_DEFAULT}),
        validator=_valid_layout),
    "visibility_policy": ConfigKey(
        "visibility_policy", "user", "global",
        frozenset({Source.GLOBAL_OVERRIDE, Source.ENGINE_DEFAULT}),
        validator=_valid_policy),
    # text templates: RECOMMENDATION is deliberately NOT allowed - pack
    # suggestions must never resolve until the user applies them.
    "countdown_text": ConfigKey(
        "countdown_text", "user", "character",
        frozenset({Source.CHARACTER_OVERRIDE, Source.GLOBAL_OVERRIDE,
                   Source.ENGINE_DEFAULT}),
        validator=_valid_template),
    "greeting_text": ConfigKey(
        "greeting_text", "user", "character",
        frozenset({Source.CHARACTER_OVERRIDE, Source.GLOBAL_OVERRIDE,
                   Source.ENGINE_DEFAULT}),
        validator=_valid_template),
}

_ENGINE_DEFAULTS = {
    "layout_id": "standard",
    "visibility_policy": "normal",
    **TEXT_TEMPLATE_DEFAULTS,
}

#: SettingsStore keys backing the persisted layers
_SETTINGS_LAYOUT = "ui_layout_id"
_SETTINGS_POLICY = "ui_visibility_policy"
_SETTINGS_TEMPLATES = "ui_text_templates"
_SETTINGS_CHAR_TEMPLATES = "ui_character_text_templates"


class UiConfigError(ValueError):
    pass


class UiConfigService:
    """User-facing UI configuration on the frozen resolver priority.

    Persistence goes through SettingsStore; resolution goes through
    EffectiveConfigResolver (global user override -> per-character override
    -> engine default).  Pack recommendations are parked OUTSIDE every
    resolving layer and only move to the global override when the user
    explicitly applies them.  A failed save rolls the resolver back to the
    last durable values and reports False.
    """

    def __init__(self, settings, schema: Mapping[str, ConfigKey] | None = None):
        self._settings = settings
        self._schema: dict[str, ConfigKey] = dict(
            UI_CONFIG_SCHEMA if schema is None else schema)
        self.resolver = EffectiveConfigResolver(self._schema)
        self.resolver.set_layer(Source.ENGINE_DEFAULT, dict(_ENGINE_DEFAULTS))
        self._pending_recommendation: dict[str, Any] = {}
        self.reload()

    # -- resolution ----------------------------------------------------------

    def effective(self, key: str, character_fqid: str | None = None) -> Any:
        return self.resolver.resolve(key, character_fqid=character_fqid)

    def source_of(self, key: str,
                  character_fqid: str | None = None) -> Source:
        return self.resolver.resolve_with_source(
            key, character_fqid=character_fqid)[0]

    @property
    def schema_keys(self) -> tuple[str, ...]:
        return tuple(self._schema)

    # -- persistence ---------------------------------------------------------

    def _sanitize(self, key: str, value: Any) -> Any:
        """Return the persisted value only if it fully validates (CR-C01).

        On-disk settings are untrusted input: a value that fails its schema
        validator (unknown layout, over-long or forbidden-character text)
        is dropped with a logged warning instead of poisoning a layer, so
        resolution always yields a usable value and startup never raises.
        """
        config = self._schema.get(key)
        if config is None or not isinstance(value, str):
            return None
        try:
            return _validate(config, value)
        except Exception as exc:  # noqa: BLE001 - drop and keep launching
            logger.warning("ignoring invalid persisted ui config %s (%s)",
                           key, exc)
            return None

    def reload(self) -> None:
        """Rebuild resolver layers from the durable settings file.

        Every persisted value passes the schema validator here (CR-C01);
        a layout/policy pair that is individually valid but outside the
        allowed combination matrix is dropped as a whole, so the resolver
        falls back to the engine defaults for that launch.
        """
        data = self._settings.as_dict()
        global_layer: dict[str, Any] = {}
        layout = self._sanitize("layout_id", data.get(_SETTINGS_LAYOUT))
        policy = self._sanitize("visibility_policy",
                                data.get(_SETTINGS_POLICY))
        if layout is not None and policy is not None \
                and not combination_allowed(layout, policy):
            logger.warning("persisted layout/policy combination %s+%s is "
                           "not allowed; falling back to engine defaults",
                           layout, policy)
            layout = policy = None
        if layout is not None:
            global_layer["layout_id"] = layout
        if policy is not None:
            global_layer["visibility_policy"] = policy
        templates = data.get(_SETTINGS_TEMPLATES)
        if isinstance(templates, dict):
            for key, value in templates.items():
                sanitized = self._sanitize(key, value)
                if sanitized is not None:
                    global_layer[key] = sanitized
        self.resolver.set_layer(Source.GLOBAL_OVERRIDE, global_layer)

        per_key: dict[str, dict[str, str]] = {
            key: {} for key in TEXT_TEMPLATE_DEFAULTS}
        char_templates = data.get(_SETTINGS_CHAR_TEMPLATES)
        if isinstance(char_templates, dict):
            for fqid, entries in char_templates.items():
                if not isinstance(entries, dict):
                    continue
                for key, value in entries.items():
                    if key not in per_key:
                        continue
                    sanitized = self._sanitize(key, value)
                    if sanitized is not None:
                        per_key[key][str(fqid)] = sanitized
        # the resolver expects one {fqid: value} mapping per config key
        self.resolver.set_layer(Source.CHARACTER_OVERRIDE, per_key)

    def _persist(self) -> bool:
        """Write resolver layers through to SettingsStore; on failure roll
        every touched settings key back to its exact prior presence."""
        global_layer = self.resolver.layers.get(Source.GLOBAL_OVERRIDE, {})
        char_layer = self.resolver.layers.get(Source.CHARACTER_OVERRIDE, {})
        char_templates: dict[str, dict[str, str]] = {}
        for key in TEXT_TEMPLATE_DEFAULTS:
            for fqid, value in char_layer.get(key, {}).items():
                char_templates.setdefault(str(fqid), {})[key] = value
        resolved = {
            _SETTINGS_TEMPLATES: {
                key: value for key, value in global_layer.items()
                if key in TEXT_TEMPLATE_DEFAULTS},
            _SETTINGS_CHAR_TEMPLATES: char_templates,
            _SETTINGS_LAYOUT: global_layer.get("layout_id"),
            _SETTINGS_POLICY: global_layer.get("visibility_policy"),
        }
        # an unset key is removed rather than written as null residue
        values = {key: value for key, value in resolved.items()
                  if value is not None}
        discards = [key for key, value in resolved.items() if value is None]
        before = {key: self._settings.get(key) for key in resolved}
        for key in discards:
            self._settings.discard(key)
        self._settings.update(values)
        if self._settings.save():
            return True
        for key, value in before.items():
            if value is None:
                self._settings.discard(key)
            else:
                self._settings.update({key: value})
        logger.warning("ui config was not persisted; keeping last values")
        return False

    # -- user overrides --------------------------------------------------------

    def set_user(self, key: str, value: Any,
                 character_fqid: str | None = None) -> bool:
        """Persist a user override; False means the save failed and the
        resolver still holds the last durable value."""
        config = self._schema.get(key)
        if config is None:
            raise UiConfigError(f"unknown config key: {key}")
        try:
            value = _validate(config, value)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            raise UiConfigError(str(exc)) from exc

        if key in ("layout_id", "visibility_policy") \
                and character_fqid is None:
            other = "visibility_policy" if key == "layout_id" else "layout_id"
            candidate = value if key == "layout_id" \
                else self.effective(other)
            other_value = value if key == "visibility_policy" \
                else self.effective(other)
            if not combination_allowed(str(candidate), str(other_value)):
                raise UiConfigError(
                    f"layout/policy combination not allowed: "
                    f"{candidate}+{other_value}")

        if character_fqid is None:
            self.resolver.update_layer(Source.GLOBAL_OVERRIDE, key, value)
        else:
            scoped = dict(self.resolver.layers.get(
                Source.CHARACTER_OVERRIDE, {}).get(key, {}))
            scoped[character_fqid] = value
            self.resolver.update_layer(
                Source.CHARACTER_OVERRIDE, key, scoped)
        if not self._persist():
            self.reload()
            return False
        return True

    def reset(self, key: str, character_fqid: str | None = None) -> bool:
        """Remove a user override; resolution falls back to the next source."""
        removed = False
        if character_fqid is None:
            removed = self.resolver.reset_field(key, Source.GLOBAL_OVERRIDE)
        else:
            scoped = dict(self.resolver.layers.get(
                Source.CHARACTER_OVERRIDE, {}).get(key, {}))
            if character_fqid in scoped:
                del scoped[character_fqid]
                self.resolver.update_layer(
                    Source.CHARACTER_OVERRIDE, key, scoped)
                removed = True
        if removed and not self._persist():
            self.reload()
            return False
        return removed

    # -- grouped display transaction (CR-C03) ---------------------------------

    def set_display(self, layout: str, policy: str) -> bool:
        """Validate the CANDIDATE pair and publish both values in ONE save.

        The matrix is checked against the candidate pair itself, never
        against the still-current other half, so a legal target combination
        can always be applied in one step.  A failed save rolls the resolver
        back to the complete previous pair via reload().
        """
        try:
            layout = _validate(self._schema["layout_id"], layout)
            policy = _validate(self._schema["visibility_policy"], policy)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            raise UiConfigError(str(exc)) from exc
        if not combination_allowed(layout, policy):
            raise UiConfigError(
                f"layout/policy combination not allowed: {layout}+{policy}")

        global_layer = self.resolver.layers.get(Source.GLOBAL_OVERRIDE, {})
        before_layout = global_layer.get("layout_id")
        before_policy = global_layer.get("visibility_policy")
        self.resolver.update_layer(Source.GLOBAL_OVERRIDE,
                                   "layout_id", layout)
        self.resolver.update_layer(Source.GLOBAL_OVERRIDE,
                                   "visibility_policy", policy)
        if not self._persist():
            if before_layout is None:
                self.resolver.reset_field("layout_id", Source.GLOBAL_OVERRIDE)
            else:
                self.resolver.update_layer(Source.GLOBAL_OVERRIDE,
                                           "layout_id", before_layout)
            if before_policy is None:
                self.resolver.reset_field("visibility_policy",
                                          Source.GLOBAL_OVERRIDE)
            else:
                self.resolver.update_layer(Source.GLOBAL_OVERRIDE,
                                           "visibility_policy", before_policy)
            self.reload()
            return False
        return True

    def reset_display(self) -> bool | None:
        """Reset BOTH display keys as one operation (CR-C03).

        True on success, False when the save failed (values restored),
        None when neither key had an override to remove.
        """
        global_layer = self.resolver.layers.get(Source.GLOBAL_OVERRIDE, {})
        had_layout = "layout_id" in global_layer
        had_policy = "visibility_policy" in global_layer
        if not (had_layout or had_policy):
            return None
        if had_layout:
            self.resolver.reset_field("layout_id", Source.GLOBAL_OVERRIDE)
        if had_policy:
            self.resolver.reset_field("visibility_policy",
                                      Source.GLOBAL_OVERRIDE)
        if not self._persist():
            self.reload()
            return False
        return True

    # -- pack recommendations (parked until explicitly applied) -------------

    def offer_recommendation(self, values: Mapping[str, Any]) -> dict[str, str]:
        """Validate a pack recommendation without applying it.  Returns
        per-key error messages; valid keys wait in ``pending_recommendation``."""
        errors: dict[str, str] = {}
        staged: dict[str, Any] = {}
        for key, value in values.items():
            config = self._schema.get(key)
            if config is None:
                errors[key] = f"unknown config key: {key}"
                continue
            try:
                staged[key] = _validate(config, value)
            except Exception as exc:  # noqa: BLE001
                errors[key] = str(exc)
        if not errors:
            layout = str(staged.get("layout_id", self.effective("layout_id")))
            policy = str(staged.get(
                "visibility_policy", self.effective("visibility_policy")))
            if not combination_allowed(layout, policy):
                errors["visibility_policy"] = (
                    f"layout/policy combination not allowed: "
                    f"{layout}+{policy}")
        if not errors:
            self._pending_recommendation = staged
        return errors

    @property
    def pending_recommendation(self) -> dict[str, Any]:
        return dict(self._pending_recommendation)

    def apply_recommendation(self) -> bool:
        """Move the parked recommendation into the global user override."""
        if not self._pending_recommendation:
            return False
        staged = dict(self._pending_recommendation)
        before = {key: self.resolver.layers.get(Source.GLOBAL_OVERRIDE, {}).get(key)
                  for key in staged}
        for key, value in staged.items():
            self.resolver.update_layer(Source.GLOBAL_OVERRIDE, key, value)
        if not self._persist():
            layer = self.resolver.layers.setdefault(Source.GLOBAL_OVERRIDE, {})
            for key, value in before.items():
                if value is None:
                    layer.pop(key, None)
                else:
                    layer[key] = value
            return False
        self._pending_recommendation.clear()
        return True

    def discard_recommendation(self) -> None:
        self._pending_recommendation.clear()
