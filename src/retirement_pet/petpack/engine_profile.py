"""What THIS engine actually implements (V12-02 honesty registry).

The validator must never accept a pack on the grounds that it merely parses:
every capability a pack may require, every core semantic the engine can
trigger, and every renderer kind the runtime can draw is declared here and
checked against the manifest.  Anything absent from this registry is
unsupported, no matter how benign it looks.

Engine API version vs application version (PETPACK_SPEC 5 "Engine API 兼容
范围"): ``engine_min``/``engine_max_exclusive`` bind the PetPack ENGINE API
line, not the application release.  Every shipped pack (both frozen official
revisions and the local 0.1.x preview) declares ``[2.0.0, 3.0.0)``, so the
engine API implemented by this runtime is versioned 2.0.0.  This constant is
independent of ``retirement_pet.__version__`` and must move only with a real
runtime API change.
"""

from __future__ import annotations

#: Engine API compatibility version (see module docstring).
ENGINE_VERSION = "2.0.0"

#: Capabilities the engine can satisfy when a pack lists them in
#: ``compatibility.required_capabilities``.  A required capability outside
#: this set is PPK-MAN-E006 (the pack could never render as authored).
#:
#: Review P-1 evidence rule: membership requires ACTUAL in-pack runtime
#: consumption, not merely format validation.  ``asset.wav.v1``,
#: ``asset.ogg.v1`` and ``text.plaintext.v1`` are format-checked by the
#: validator (PPK-RES-E005 / PPK-TXT-*) but nothing in the runtime loads
#: pack audio or renders pack text profiles today, so they are NOT
#: admission-satisfiable and stay out of this set until a real consumer
#: ships.
SUPPORTED_CAPABILITIES = frozenset({
    "renderer.static.v1",
    "renderer.sequence.v1",
    "asset.png.v1",
    "semantic.core.idle.v1",
    "semantic.core.work.v1",
    "semantic.core.rest.v1",
    "semantic.core.eat.v1",
    "semantic.core.exercise.v1",
    "semantic.core.meeting.v1",
    "semantic.core.music.v1",
})

#: Core semantics the engine can trigger (context_adapter.CAT_SEMANTICS /
#: petpack.runtime._ACTION_TO_SEMANTIC).  A ``core.*`` semantic outside this
#: set is unknown: it is disabled with PPK-ACT-W001 only when its exact
#: ``semantic.<name>.v1`` capability is declared optional, otherwise the pack
#: is rejected with PPK-ACT-E002 (PETPACK_SPEC 11).
KNOWN_CORE_SEMANTICS = frozenset({
    "core.idle", "core.work", "core.rest", "core.eat",
    "core.exercise", "core.meeting", "core.music",
})

#: Renderer kinds the runtime profile path can actually draw
#: (petpack.runtime.PackRuntime._profile_for).  ``layered`` is accepted by
#: the validator only as a parameterized rig TEMPLATE (PETPACK_SPEC 11.4)
#: and ``builtin_effect`` is not a body-frame renderer; both are disabled at
#: runtime with PPK-ACT-W001, and a core.idle bound to them can never render,
#: which is PPK-ACT-E004.
SUPPORTED_RENDERER_KINDS = frozenset({"static", "sequence"})


def semantic_capability(semantic: str) -> str:
    """Exact capability name covering one core semantic (PETPACK_SPEC 11:
    ``core.foo.bar`` maps to ``semantic.core.foo.bar.v1``)."""
    return f"semantic.{semantic}.v1"
