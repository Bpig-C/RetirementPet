"""Asset bundle: manifest-driven layered sprites with graceful fallback.

Missing PNGs never crash anything: sequences drop missing frames, part-based
actions fall back to the programmatic cat when a required part is absent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtGui import QPixmap

from retirement_pet.lru_cache import LruByteCache
from retirement_pet.resource_path import asset_path

logger = logging.getLogger(__name__)

#: PETPACK_SPEC_1_0 17 budget: current-character RAM target.  v1 has one
#: bundle; the mechanism (byte-billed LRU with hard ceiling) is the same one
#: v2 per-Revision caches will use.
ACTIVE_RUNTIME_CACHE_BYTES = 48 * 1024 * 1024


@dataclass(frozen=True)
class SequenceVisual:
    frames: tuple[QPixmap, ...]
    loop: bool


@dataclass(frozen=True)
class PartsVisual:
    parts: dict[str, QPixmap]
    attachments: tuple[str, ...]
    face_override: str | None


ActionVisual = SequenceVisual | PartsVisual

#: runtime effect ids (renderer/app) -> manifest ``effects`` key.  The
#: manifest ships "note" while the runtime overlay id is "notes"; both are
#: accepted so either spelling of an asset pack resolves.
_EFFECT_KEY_ALIASES = {"notes": "note"}


class AssetBundle:
    def __init__(self, root: Path | None = None,
                 cache_bytes: int = ACTIVE_RUNTIME_CACHE_BYTES):
        self._root = Path(root) if root else asset_path()
        self._manifest: dict = {}
        self._pixmap_cache: LruByteCache[QPixmap] = LruByteCache(cache_bytes)
        self._load_manifest()

    def _load_manifest(self) -> None:
        path = self._root / "manifest.json"
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._manifest = data
        except Exception as exc:  # noqa: BLE001
            logger.warning("manifest unreadable (%s); using programmatic cat", exc)
            self._manifest = {}

    @property
    def available(self) -> bool:
        return bool(self._manifest.get("actions"))

    def canvas_size(self) -> tuple[int, int]:
        canvas = self._manifest.get("canvas", {})
        return int(canvas.get("width", 256)), int(canvas.get("height", 256))

    def pixmap(self, rel_path: str) -> QPixmap | None:
        """Cached pixmap load; missing or unloadable files return None."""
        cached = self._pixmap_cache.get(rel_path)
        if cached is not None:
            return cached
        if self._pixmap_cache.is_known_miss(rel_path):
            return None
        pix: QPixmap | None = None
        full = self._root / rel_path
        if full.is_file():
            candidate = QPixmap(str(full))
            if not candidate.isNull():
                pix = candidate
        if pix is None:
            logger.debug("missing sprite: %s", rel_path)
        self._pixmap_cache.put(rel_path, pix)
        return pix

    # -- action visuals -----------------------------------------------------

    def action_visual(self, action_key: str) -> ActionVisual | None:
        actions = self._manifest.get("actions", {})
        spec = actions.get(action_key)
        if not isinstance(spec, dict):
            return None
        mode = spec.get("mode", "parts")
        if mode == "sequence":
            return self._sequence_visual(spec)
        return self._parts_visual(spec)

    def action_frame_count(self, action_key: str) -> int:
        """Declared sequence frame count from the manifest, without loading
        any pixmap.  0 when the action is parts-based or undeclared.  Used
        for control enablement; actual playback reads loaded frames."""
        spec = self._manifest.get("actions", {}).get(action_key)
        if not isinstance(spec, dict):
            return 0
        if spec.get("mode", "parts") != "sequence":
            return 0
        return len(spec.get("frames", []))

    def _sequence_visual(self, spec: dict) -> SequenceVisual | None:
        frames = []
        for rel in spec.get("frames", []):
            pix = self.pixmap(str(rel))
            if pix is not None:
                frames.append(pix)
        if not frames:
            return None
        # NOTE: per-action ``fps`` in the manifest is deliberately NOT read
        # here.  v1 timing has exactly one authority - ActionSpec.animation_fps
        # (action_registry) drives frame advance; a second fps number in the
        # visual would silently disagree with it (DESIGN_V2 10.3).
        return SequenceVisual(
            frames=tuple(frames),
            loop=bool(spec.get("loop", True)),
        )

    def _parts_visual(self, spec: dict) -> PartsVisual | None:
        parts_spec = self._manifest.get("parts", {})
        required = self._manifest.get("required_parts", ["body", "head", "face_open"])
        parts: dict[str, QPixmap] = {}
        for name in parts_spec:
            pix = self.pixmap(str(parts_spec[name].get("path", "")))
            if pix is not None:
                parts[name] = pix
        for name in required:
            if name not in parts:
                return None  # cannot build the cat -> programmatic fallback
        attachments = tuple(
            a for a in spec.get("attachments", []) if a in parts
        )
        face_override = spec.get("face") if spec.get("face") in parts else None
        return PartsVisual(parts=parts, attachments=attachments, face_override=face_override)

    def effect_pixmap(self, name: str) -> QPixmap | None:
        effects = self._manifest.get("effects", {})
        rel = effects.get(name)
        if not rel:
            rel = effects.get(_EFFECT_KEY_ALIASES.get(name, ""))
        if not rel:
            return None
        return self.pixmap(str(rel))
