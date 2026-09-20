"""PetPack character runtime: render validated packs (M3; DESIGN_V2 10).

Renders a validated, immutable pack revision through the allowed profiles
(``static`` and ``sequence``).  Activation warms the mandatory idle working
set; optional actions are admitted by verified PNG dimensions and decoded
on demand from the already-materialized immutable archive into the shared
LRU budget.

``render_first_frame_offscreen`` implements the validator-runtime step
(PETPACK_SPEC 18 step 10): isolated, silent, no state writes, one
core.idle first frame per character - the Qt-tier complement to the
pure-logic validator.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from typing import Mapping
from uuid import uuid4

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

from retirement_pet.assets import SequenceVisual
from retirement_pet.character_layout import CharacterGeometry
from retirement_pet.lru_cache import LruByteCache
from retirement_pet.models import ActionId
from retirement_pet.petpack.archive import PetpackArchive
from retirement_pet.petpack.manifest import SUPPORTED_SCHEMA

#: action id (v1 bridge) -> pack semantic, mirrored from context_adapter
_ACTION_TO_SEMANTIC: dict[str, str] = {
    ActionId.IDLE.value: "core.idle",
    ActionId.WORK.value: "core.work",
    ActionId.REST.value: "core.rest",
    ActionId.EAT.value: "core.eat",
    ActionId.EXERCISE.value: "core.exercise",
    ActionId.MEETING.value: "core.meeting",
    ActionId.MUSIC.value: "core.music",
}

MIN_FRAME_DURATION_MS = 33  # duplicated budget: visible refresh <= 30 FPS

#: author-preview columns per action before bounded sampling kicks in.
#: Sized so a 16-frame MVP sequence (Blender first batch) plus its seam
#: fits WITHOUT truncation; longer sequences keep first frames + LAST
#: frame + seam and report what was omitted (CR-T04).
MAX_PREVIEW_COLUMNS = 18


@dataclass(frozen=True)
class _Profile:
    kind: str                     # static | sequence
    static_asset: str | None      # decoded asset id for static
    frames: tuple[tuple[str, int], ...]  # (asset id, duration_ms)


@dataclass(frozen=True)
class PreviewPoint:
    """One wall-clock sample the author preview paints through render_body."""

    elapsed_ms: int
    label: str                    # column caption: "static" | "f0 500ms" | ...
    frame_index: int | None       # None for the loop-seam / fallback sample


@dataclass(frozen=True)
class PreviewRow:
    """Per-semantic preview plan: one row of the author contact sheet."""

    semantic: str
    action_value: str             # ActionId value that drives render_body
    kind: str                     # static | sequence | fallback
    frame_count: int
    total_ms: int
    points: tuple[PreviewPoint, ...]
    omitted_frames: int = 0       # frames not sampled by a truncated row


class PackCharacterRuntime:
    """One character of one validated revision, ready to paint."""

    def __init__(self, archive: PetpackArchive, manifest: dict,
                 character_id: str,
                 asset_cache: LruByteCache[QPixmap] | None = None,
                 content_digest: str | None = None):
        if manifest.get("schema_version") != SUPPORTED_SCHEMA:
            raise ValueError("unsupported schema")
        self._archive = archive
        self._manifest = manifest
        self.character_id = character_id
        self.content_digest_value = content_digest
        # GLOBAL budget shared across all runtimes (P0-D): the cache is
        # either injected by the app or owned privately for isolated use.
        # Keys use the COMPLETE revision digest.  A 12-hex display prefix is
        # not an identity boundary: attacker-controlled packs can search for
        # such collisions and make one revision display another's pixels.
        # Runtimes without a digest receive a process-unique namespace so an
        # explicitly shared cache is still safe.
        self._cache = asset_cache or LruByteCache(48 * 1024 * 1024)
        revision_namespace = content_digest or "no-digest"
        # Ownership is per Runtime, not merely per revision.  Two characters
        # from one Revision may coexist during a transaction and must be
        # releasable independently once authority is known.
        self._cache_prefix = (
            f"{revision_namespace}:{character_id}:{uuid4().hex}:")

        matches = [c for c in manifest.get("characters", [])
                   if c.get("id") == character_id]
        if not matches:
            raise KeyError(f"character {character_id!r} not in pack")
        character = matches[0]
        self._character = character
        self._assets_by_id = {a.get("id"): a for a in manifest.get("assets", [])}
        self._asset_data_by_id: dict[str, bytes | None] = {}
        self._actions_by_id = {a.get("id"): a for a in manifest.get("actions", [])}
        self._bound = character.get("actions", {})
        self._profiles: dict[str, _Profile | None] = {}
        self._prepare_result: bool | None = None
        self._decoded_size_by_asset: dict[str, int | None] = {}
        # missing semantics discovered at prepare: engine shows its own hint
        self.missing_semantics: tuple[str, ...] = ()

        for semantic in ("core.idle", "core.work", "core.rest", "core.eat",
                         "core.exercise", "core.meeting", "core.music"):
            self._profiles[semantic] = self._resolve_profile(semantic)

    # -- prepare ---------------------------------------------------------------

    def _cache_key(self, asset_id: str) -> str | None:
        asset = self._assets_by_id.get(asset_id)
        if not asset:
            return None
        path = str(asset.get("path", ""))
        return f"{self._cache_prefix}{path}" if path else None

    def _decode(self, asset_id: str) -> QPixmap | None:
        asset = self._assets_by_id.get(asset_id)
        if not asset:
            return None
        path = str(asset.get("path", ""))
        key = f"{self._cache_prefix}{path}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if self._cache.is_known_miss(key):
            return None
        data = self._asset_data(asset_id)
        if not data:
            self._cache.put(key, None)
            return None
        pixmap = QPixmap()
        try:
            decoded = pixmap.loadFromData(data)
        except Exception:  # noqa: BLE001 - Qt decoder boundary
            decoded = False
        if not decoded:
            self._cache.put(key, None)
            return None
        if not self._cache.put(key, pixmap):
            return None
        return pixmap

    def _asset_data(self, asset_id: str) -> bytes | None:
        if asset_id in self._asset_data_by_id:
            return self._asset_data_by_id[asset_id]
        asset = self._assets_by_id.get(asset_id)
        path = str(asset.get("path", "")) if asset else ""
        try:
            data = self._archive.read(path) if path else None
        except Exception:  # noqa: BLE001 - immutable media read boundary
            data = None
        self._asset_data_by_id[asset_id] = data
        return data

    @property
    def cache_namespace(self) -> str:
        return self._cache_prefix

    def release_cache(self) -> int:
        """Release decoded pixels and known misses owned by this runtime."""
        return self._cache.discard_prefix(self._cache_prefix)

    @staticmethod
    def _profile_assets(profile: _Profile) -> tuple[str, ...]:
        if profile.kind == "static":
            return (profile.static_asset,) if profile.static_asset else ()
        return tuple(asset_id for asset_id, _duration in profile.frames)

    def _estimated_decoded_bytes(self, asset_id: str) -> int | None:
        """Read trusted image dimensions without performing a Qt decode."""
        if asset_id in self._decoded_size_by_asset:
            return self._decoded_size_by_asset[asset_id]
        asset = self._assets_by_id.get(asset_id)
        result = None
        if asset:
            path = str(asset.get("path", ""))
            media_type = str(asset.get("media_type", ""))
            data = self._asset_data(asset_id)
            if media_type == "image/png":
                if (data is not None and len(data) >= 24
                        and data[:8] == b"\x89PNG\r\n\x1a\n"
                        and data[12:16] == b"IHDR"):
                    width, height = struct.unpack_from(">II", data, 16)
                    if width > 0 and height > 0:
                        result = width * height * 4
            else:
                # Test adapters and trusted in-process callers may omit MIME;
                # production PetPacks always take the verified PNG-header path.
                properties = asset.get("properties")
                if isinstance(properties, dict):
                    width = properties.get("width")
                    height = properties.get("height")
                    if type(width) is int and type(height) is int \
                            and width > 0 and height > 0:
                        result = width * height * 4
        self._decoded_size_by_asset[asset_id] = result
        return result

    def _working_set_assets(
            self, *profiles: _Profile) -> list[tuple[str, str]] | None:
        """Return unique assets/keys for one idle-plus-action working set."""
        assets: list[tuple[str, str]] = []
        seen_keys: set[str] = set()
        for profile in profiles:
            for asset_id in self._profile_assets(profile):
                key = self._cache_key(asset_id)
                if key is None:
                    return None
                if key not in seen_keys:
                    seen_keys.add(key)
                    assets.append((asset_id, key))
        return assets

    def _working_set_fits(self, *profiles: _Profile) -> bool:
        """Pure arithmetic check for one declared idle/action working set.

        The test is by unique cache key, not frame occurrence: a sequence may
        reference one sprite many times without multiplying RAM.  No QPixmap
        is decoded here: large packs are loaded on demand as PetPack 1.0
        requires, and character activation stays bounded on the GUI thread.
        """
        assets = self._working_set_assets(*profiles)
        if assets is None:
            return False
        if not assets or len(assets) > self._cache.max_keys:
            return False

        decoded_bytes = 0
        for asset_id, _key in assets:
            size = self._estimated_decoded_bytes(asset_id)
            if size is None:
                return False
            decoded_bytes += size
            if decoded_bytes > self._cache.max_bytes:
                return False
        return True

    def _warm_profile(self, profile: _Profile) -> bool:
        """Decode and retain only the activation-critical idle profile."""
        assets = self._working_set_assets(profile)
        if not assets:
            return False
        for asset_id, _key in assets:
            if self._decode(asset_id) is None:
                return False
        return all(self._cache.get(key) is not None
                   for _asset_id, key in assets)

    def _resolve_profile(self, semantic: str) -> _Profile | None:
        """Resolve a core semantic to a drawable profile, or None (missing)."""
        action_id = self._bound.get(semantic)
        if not action_id:
            return None
        action = self._actions_by_id.get(action_id)
        if not action:
            return None
        lifecycle = action.get("lifecycle", {})
        section = lifecycle.get("loop") or lifecycle.get("enter") or {}
        renderer = section.get("renderer") if isinstance(section, dict) else None
        if not isinstance(renderer, dict):
            return None
        kind = renderer.get("type")
        if kind == "static":
            asset_id = renderer.get("asset")
            return _Profile("static", asset_id, ()) if asset_id else None
        if kind == "sequence":
            frames = []
            for frame in renderer.get("frames", []):
                duration = int(frame.get("duration_ms", MIN_FRAME_DURATION_MS))
                frames.append((str(frame.get("asset")), max(1, duration)))
            return _Profile("sequence", None, tuple(frames))
        return None

    def prepare(self) -> bool:
        """Decode core.idle; returns False if the character cannot render.

        Records missing semantics so the engine can add its own overlay
        hint while the character still falls back to its own idle.
        """
        if self._prepare_result is not None:
            return self._prepare_result

        idle = self._profiles.get("core.idle")
        if idle is None or not self._working_set_fits(idle) \
                or not self._warm_profile(idle):
            # Preparation is deterministic for an immutable Revision.  Cache
            # the result so repeated activation/first-frame attempts cannot
            # become a decode loop even when several individually valid idle
            # frames exceed the combined budget.
            self._prepare_result = False
            return False

        missing: list[str] = []
        for semantic, profile in list(self._profiles.items()):
            if semantic == "core.idle":
                continue
            if profile is None:
                missing.append(semantic)
                continue
            if not self._working_set_fits(idle, profile):
                # The idle fallback plus this action cannot coexist inside the
                # runtime budget.  Disable it once, instead of letting its
                # sequence evict/redecode idle forever.
                self._profiles[semantic] = None
                missing.append(semantic)

        self.missing_semantics = tuple(missing)
        self._prepare_result = True
        return True

    def first_frame_image(self) -> QImage | None:
        """Isolated, silent core.idle first frame rendered by THIS runtime
        (P0-B: the candidate verifies itself; decodes land in its shared
        cache so a committed runtime is already warm)."""
        from retirement_pet.models import ActionId, LifeStage, RenderSnapshot

        if not self.prepare():
            return None
        image = QImage(128, 128, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        try:
            self.render(
                painter, QRectF(0, 0, 128, 128),
                RenderSnapshot(action=ActionId.IDLE, stage=LifeStage.YOUNG,
                               elapsed_ms=0, frame=0, time_ms=0),
            )
        finally:
            painter.end()
        return image

    def preview_schedule(self) -> tuple[PreviewRow, ...]:
        """Author-preview sampling plan (V12-03 CR-T02/T03, CR-T04).

        One row per bound semantic plus one fallback row per missing core
        semantic.  Sequence rows sample every frame start AND the loop seam
        (total wraps back to frame 0), so uneven frame lengths and the
        first/last transition are painted exactly as the desktop draws
        them.  A 16-frame MVP sequence plus seam fits whole; longer rows
        are bounded to MAX_PREVIEW_COLUMNS by keeping the FIRST frames,
        the LAST frame and the seam, with omitted_frames telling how many
        were skipped - the leading frames are never silently presented as
        the whole action.  The caller paints these points through
        ``render_body``; nothing here decodes assets or touches global
        state.
        """
        rows: list[PreviewRow] = []
        for semantic, profile in self._profiles.items():
            action_value = semantic.removeprefix("core.")
            if profile is None:
                rows.append(PreviewRow(
                    semantic, action_value, "fallback", 0, 0,
                    (PreviewPoint(0, "->idle", None),)))
                continue
            if profile.kind == "static":
                rows.append(PreviewRow(
                    semantic, action_value, "static", 1, 0,
                    (PreviewPoint(0, "static", 0),)))
                continue
            starts = []
            elapsed = 0
            for _asset_id, duration in profile.frames:
                starts.append(elapsed)
                elapsed += duration
            total = elapsed
            points = [PreviewPoint(starts[i], f"f{i} {duration}ms", i)
                      for i, (_asset_id, duration) in enumerate(profile.frames)]
            if total > 0:
                points.append(PreviewPoint(total, f"seam {total}ms", None))
            if not points:
                # An empty sequence paints nothing; one blank sample keeps
                # the contact-sheet row honest about that.
                points = [PreviewPoint(0, "empty", None)]
            omitted = 0
            if len(points) > MAX_PREVIEW_COLUMNS:
                # Bounded sampling that never loses the ending: the last
                # FRAME and the seam always survive (CR-T04).
                frame_points = points[:-1]
                seam_point = points[-1]
                head = MAX_PREVIEW_COLUMNS - 2
                omitted = len(frame_points) - head - 1
                points = frame_points[:head] + [frame_points[-1], seam_point]
            rows.append(PreviewRow(
                semantic, action_value, "sequence",
                len(profile.frames), total, tuple(points),
                omitted_frames=omitted))
        return tuple(rows)

    # -- painting -----------------------------------------------------------------

    def is_animated_for(self, action) -> bool:
        """True when the visual for this action actually advances
        (sequence material); a static profile draws identical pixels
        every tick, so repaints can be skipped (V12-08 downshift)."""
        semantic = _ACTION_TO_SEMANTIC.get(
            getattr(action, "value", action), "core.idle")
        profile = self._profiles.get(semantic)
        return profile is not None and profile.kind == "sequence"

    def semantic_sequence_ms(self, semantic: str) -> tuple[int, ...] | None:
        """Per-frame durations of one semantic's sequence material.

        This is the material's OWN timing (C06-R2): a single full pass
        lasts the sum of these durations.  None when the semantic is
        missing or draws from a static asset - the loop control must not
        offer a material pass that does not exist.
        """
        profile = self._profiles.get(semantic)
        if profile is None or profile.kind != "sequence":
            return None
        return tuple(duration for _, duration in profile.frames)

    def capabilities(self):
        """Adapter to the v2 resolver capability table.

        semantics maps core semantic -> the EXECUTOR action key the v1
        bridge understands (the same values CAT_SEMANTICS uses), so the
        resolver can drive the ActionController for pack characters too.
        """
        from retirement_pet.context_adapter import CAT_SEMANTICS
        from retirement_pet.runtime_state import CharacterCapabilities

        semantics = {
            semantic: CAT_SEMANTICS.get(semantic, semantic)
            for semantic, profile in self._profiles.items()
            if profile is not None
        }
        return CharacterCapabilities(
            character_fqid=self.character_fqid(),
            semantics=semantics,
            character_actions=frozenset())

    def character_fqid(self) -> str:
        package = self._manifest["package"]
        return (f"{package['publisher_id']}.{package['id']}"
                f".{self._manifest['series']['id']}.{self.character_id}")

    def content_digest(self) -> str | None:
        return self.content_digest_value

    def render(self, painter: QPainter, rect: QRectF, snapshot) -> None:
        """Compatibility entry point; PetWindow uses ``render_body``."""
        self.render_body(painter, rect, snapshot)

    def character_geometry(self):
        """Manifest geometry for the shared layout transform, or None.

        Parsed once per immutable revision; a malformed block keeps the
        window on the legacy whole-PNG fit instead of guessing anchors.
        """
        if not hasattr(self, "_geometry_parsed"):
            self._geometry_parsed = CharacterGeometry.from_character(
                self._character)
        return self._geometry_parsed

    def render_body(self, painter: QPainter, rect: QRectF, snapshot,
                    layout=None) -> None:
        """Paint pack-owned body pixels without engine overlays.

        ``layout`` is the window-computed :class:`BodyLayout` shared with
        bubbles and hit testing; without it the body falls back to the
        legacy whole-PNG fit centered in ``rect``.
        """
        if not self.prepare():
            return
        semantic = _ACTION_TO_SEMANTIC.get(
            snapshot.action.value, "core.idle")
        profile = self._profiles.get(semantic) or self._profiles["core.idle"]
        if profile is None:
            return
        asset_id = self._asset_for_elapsed(profile, snapshot.elapsed_ms)
        pixmap = self._decode(asset_id) if asset_id is not None else None
        if pixmap is None and semantic != "core.idle":
            # A decoder-specific optional-frame failure is negatively cached;
            # keep the pet visible with this character's already-warm idle.
            idle = self._profiles.get("core.idle")
            idle_asset = (self._asset_for_elapsed(idle, snapshot.elapsed_ms)
                          if idle is not None else None)
            pixmap = self._decode(idle_asset) \
                if idle_asset is not None else None
        if pixmap is None:
            return
        if layout is not None and not layout.body_rect.isEmpty():
            target = QRectF(layout.body_rect)
        else:
            target = None
        if target is not None:
            source = QRectF(0.0, 0.0,
                            float(pixmap.width()), float(pixmap.height()))
            painter.save()
            try:
                painter.setRenderHint(
                    QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawPixmap(target, pixmap, source)
            finally:
                painter.restore()
        else:
            self._draw_fitted(painter, rect, pixmap)

    @staticmethod
    def _asset_for_elapsed(
            profile: _Profile, elapsed_ms: int) -> str | None:
        if profile.kind == "static":
            return profile.static_asset
        total = sum(duration for _, duration in profile.frames)
        if total <= 0:
            return None
        elapsed = elapsed_ms % total
        for asset_id, duration in profile.frames:
            if elapsed < duration:
                return asset_id
            elapsed -= duration
        return None

    @staticmethod
    def _draw_fitted(painter: QPainter, rect: QRectF, pixmap: QPixmap) -> None:
        """Map source pixels to the device once through QPainter.

        Creating an intermediate 232px QPixmap made a 150% screen perform a
        512/256→232→348 two-step resample.  A floating-point target QRectF
        lets Qt map the original source directly to the backing-store device
        pixels while preserving aspect ratio.
        """
        width = float(pixmap.width())
        height = float(pixmap.height())
        if width <= 0 or height <= 0 or rect.isEmpty():
            return
        scale = min(rect.width() / width, rect.height() / height)
        target_width = width * scale
        target_height = height * scale
        target = QRectF(
            rect.x() + (rect.width() - target_width) / 2.0,
            rect.y() + (rect.height() - target_height) / 2.0,
            target_width,
            target_height,
        )
        source = QRectF(0.0, 0.0, width, height)
        painter.save()
        try:
            painter.setRenderHint(
                QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(target, pixmap, source)
        finally:
            painter.restore()


def render_first_frame_offscreen(archive: PetpackArchive, manifest: dict,
                                 character_id: str) -> QImage | None:
    """Validator-runtime step 18.10: isolated silent core.idle first frame.

    Returns the rendered frame, or None when the character has no
    verifiable core.idle (the pack must not be activatable in that case).
    """
    runtime = PackCharacterRuntime(archive, manifest, character_id)
    return runtime.first_frame_image()

    image = QImage(128, 128, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    try:
        runtime.render(
            painter, QRectF(0, 0, 128, 128),
            RenderSnapshot(action=ActionId.IDLE, stage=LifeStage.YOUNG,
                           elapsed_ms=0, frame=0, time_ms=0),
        )
    finally:
        painter.end()
    return image


def load_pack(data: bytes) -> tuple[PetpackArchive, dict]:
    """Convenience: validated archive + parsed manifest for trusted callers.

    Callers MUST have run validate_petpack() first; this helper does not
    re-run hostile-input checks - it is the post-validation load path.
    """
    archive = PetpackArchive(data)
    from retirement_pet.petpack.manifest import parse_manifest

    manifest = parse_manifest(archive.manifest_bytes)
    return archive, manifest
