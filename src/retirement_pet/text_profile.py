"""TextProfile rendering: semantic keys, plain text, safe variables (M6).

Template variables come ONLY from the engine allowlist and are substituted
by plain string replacement - no eval, no attribute access, no indexing,
no format mini-language, no HTML.  Control and bidirectional control
characters are rejected or stripped; text is always rendered as plain
text (DESIGN_V2 13; PETPACK_SPEC 14; CONTENT_POLICY 8).
"""

from __future__ import annotations

import re
from typing import Mapping

#: exact allowlist of template tokens (DESIGN_V2 13.1)
SAFE_VARIABLES = frozenset({
    "character_name", "days", "hours", "minutes", "series_name",
    "stage_name",
})

_TOKEN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

#: C0/C1 controls except tab/newline are rejected outright; bidi controls
#: are always rejected (they must never reach the renderer)
_FORBIDDEN_CHARS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]"
)


class TextSafetyError(ValueError):
    pass


def check_plain_text(text: str, *, max_length: int = 500) -> str:
    if not isinstance(text, str):
        raise TextSafetyError("text must be a string")
    if len(text) > max_length:
        raise TextSafetyError("text too long")
    hit = _FORBIDDEN_CHARS.search(text)
    if hit:
        raise TextSafetyError(
            f"control/bidi character U+{ord(hit.group(0)):04X} forbidden")
    # angle brackets are not interpreted but rejected early: text profiles
    # are plain text and must not LOOK like markup after future changes
    if "<" in text or ">" in text:
        raise TextSafetyError("markup characters are not allowed")
    return text


def render(template: str, variables: Mapping[str, str]) -> str:
    """Substitute {token} for allowlisted tokens only; unknown tokens stay
    literal (they are visible to the user, never an injection path)."""
    check_plain_text(template)

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in SAFE_VARIABLES:
            return match.group(0)  # unknown token: literal, harmless
        value = variables.get(name, "")
        check_plain_text(str(value), max_length=200)
        return str(value)

    return _TOKEN.sub(substitute, template)


class TextProfile:
    """Semantic-keyed plain-text entries (single locale per profile)."""

    def __init__(self, entries: Mapping[str, object]):
        self._entries = dict(entries)

    def get(self, semantic_key: str, default: str | None = None) -> str | None:
        value = self._entries.get(semantic_key)
        if value is None:
            return default
        if isinstance(value, list):
            # candidate pools: deterministic pick by caller context
            value = value[0] if value else default
        if not isinstance(value, str):
            raise TextSafetyError("text entry must be a string or list")
        return value

    def render(self, semantic_key: str, variables: Mapping[str, str],
               default: str | None = None) -> str | None:
        template = self.get(semantic_key, default)
        if template is None:
            return None
        return render(template, variables)
