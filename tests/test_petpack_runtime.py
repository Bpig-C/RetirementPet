"""Pack runtime: prepare, render, offscreen first-frame (PETPACK_SPEC 18.10)."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

import pytest

from PySide6.QtGui import QColor, QImage

import retirement_pet.petpack.runtime as runtime_module
from retirement_pet.lru_cache import LruByteCache
from retirement_pet.petpack.runtime import (
    PackCharacterRuntime,
    render_first_frame_offscreen,
)
from retirement_pet.petpack.validator import validate_petpack

REF_PACK = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / \
    "petpack" / "minimal-static.petpack"


@pytest.fixture()
def validated(qt_application):
    from retirement_pet.petpack.runtime import load_pack

    data = REF_PACK.read_bytes()
    assert validate_petpack(data).accepted
    return load_pack(data)


def _snapshot(action="idle", elapsed_ms=0):
    from retirement_pet.models import ActionId, LifeStage, RenderSnapshot

    return RenderSnapshot(action=ActionId(action), stage=LifeStage.YOUNG,
                          elapsed_ms=elapsed_ms, frame=0, time_ms=0)


def _render_to_image(runtime, action="idle", elapsed_ms=0) -> QImage:
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter

    image = QImage(128, 128, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    try:
        runtime.render(painter, QRectF(0, 0, 128, 128),
                       _snapshot(action, elapsed_ms))
    finally:
        painter.end()
    return image


def _has_opaque_pixels(image: QImage) -> bool:
    for y in range(0, image.height(), 4):
        for x in range(0, image.width(), 4):
            if image.pixelColor(x, y).alpha() > 0:
                return True
    return False


def test_offscreen_first_frame_is_verifiable(validated):
    """Validator-runtime step 10: idle first frame decodes and paints."""
    archive, manifest = validated
    image = render_first_frame_offscreen(archive, manifest, "demo")
    assert image is not None
    assert _has_opaque_pixels(image), "core.idle first frame must paint content"


def test_character_without_idle_is_not_activatable(validated):
    archive, manifest = validated
    manifest = json.loads(json.dumps(manifest))
    # break the idle binding (simulated damaged revision)
    for character in manifest["characters"]:
        character["actions"] = {}
    image = render_first_frame_offscreen(archive, manifest, "demo")
    assert image is None


def test_missing_semantic_renders_own_idle(validated):
    """The pack has no core.work; requesting work still paints THIS idle."""
    archive, manifest = validated
    runtime = PackCharacterRuntime(archive, manifest, "demo")
    assert runtime.prepare()
    assert "core.work" in runtime.missing_semantics

    fallback = _render_to_image(runtime, action="work")
    idle = _render_to_image(runtime, action="idle")
    assert _has_opaque_pixels(fallback)
    # both frames are the same character body (not another role's)
    assert fallback.constBits() == idle.constBits()


def test_capabilities_expose_supported_semantics(validated):
    archive, manifest = validated
    runtime = PackCharacterRuntime(archive, manifest, "demo")
    runtime.prepare()
    caps = runtime.capabilities()
    assert caps.supports("core.idle")
    assert not caps.supports("core.work")
    assert caps.character_fqid == \
        "community.retirementpet.minimal-static.reference.demo"


# -- Phase 2C: shared-cache identity and declared working sets -----------------


class _CountingArchive:
    def __init__(self, files: dict[str, bytes]):
        self.files = dict(files)
        self.reads: Counter[str] = Counter()

    def read(self, path: str) -> bytes | None:
        self.reads[path] += 1
        return self.files.get(path)


class _FakePixmap:
    decode_calls: Counter[bytes] = Counter()

    def __init__(self):
        self.payload = b""
        self._width = 0
        self._height = 0

    def loadFromData(self, data: bytes) -> bool:  # noqa: N802
        raw = bytes(data)
        type(self).decode_calls[raw] += 1
        parts = raw.split(b"|", 3)
        if len(parts) != 4 or parts[0] != b"OK":
            return False
        self._width = int(parts[1])
        self._height = int(parts[2])
        self.payload = parts[3]
        return True

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height


def _fake_blob(payload: str, width: int = 4, height: int = 4) -> bytes:
    return f"OK|{width}|{height}|{payload}".encode("ascii")


def _cache_manifest(
        idle_paths: tuple[str, ...],
        actions: dict[str, tuple[str, ...]] | None = None,
        dimensions: dict[str, tuple[int, int]] | None = None) -> dict:
    bindings = {"core.idle": "action.idle"}
    action_specs = []
    all_paths = list(idle_paths)

    def renderer(paths: tuple[str, ...]) -> dict:
        if len(paths) == 1:
            return {"type": "static", "asset": f"asset.{paths[0]}"}
        return {
            "type": "sequence",
            "frames": [
                {"asset": f"asset.{path}", "duration_ms": 50}
                for path in paths
            ],
        }

    action_specs.append({
        "id": "action.idle",
        "semantic": "core.idle",
        "lifecycle": {"loop": {"renderer": renderer(idle_paths)}},
    })
    for semantic, paths in (actions or {}).items():
        action_id = f"action.{semantic.rsplit('.', 1)[-1]}"
        bindings[semantic] = action_id
        all_paths.extend(paths)
        action_specs.append({
            "id": action_id,
            "semantic": semantic,
            "lifecycle": {"loop": {"renderer": renderer(paths)}},
        })
    unique_paths = list(dict.fromkeys(all_paths))
    return {
        "schema_version": "1.0",
        "package": {"publisher_id": "cache.test", "id": "pack"},
        "series": {"id": "series"},
        "assets": [
            {
                "id": f"asset.{path}",
                "path": path,
                "properties": {
                    "width": (dimensions or {}).get(path, (4, 4))[0],
                    "height": (dimensions or {}).get(path, (4, 4))[1],
                },
            }
            for path in unique_paths
        ],
        "actions": action_specs,
        "characters": [{"id": "demo", "actions": bindings}],
    }


@pytest.fixture()
def fake_pixmap(monkeypatch):
    _FakePixmap.decode_calls.clear()
    monkeypatch.setattr(runtime_module, "QPixmap", _FakePixmap)
    return _FakePixmap


def test_shared_cache_uses_full_digest_under_revision_pressure(fake_pixmap):
    cache = LruByteCache(max_bytes=3 * 4 * 4 * 4)
    prefix = "abcdef012345"
    for index in range(32):
        payload = f"revision-{index}"
        archive = _CountingArchive({"same.png": _fake_blob(payload)})
        digest = prefix + f"{index:052x}"
        runtime = PackCharacterRuntime(
            archive, _cache_manifest(("same.png",)), "demo",
            asset_cache=cache, content_digest=digest)
        pixmap = runtime._decode("asset.same.png")
        assert pixmap is not None
        assert pixmap.payload == payload.encode("ascii")
        assert cache.byte_size <= cache.max_bytes
        assert cache.key_count <= cache.max_keys


def test_shared_cache_without_digest_gets_runtime_unique_namespace(fake_pixmap):
    cache = LruByteCache(max_bytes=1024)
    manifest = _cache_manifest(("same.png",))
    first = PackCharacterRuntime(
        _CountingArchive({"same.png": _fake_blob("first")}),
        manifest, "demo", asset_cache=cache)
    second = PackCharacterRuntime(
        _CountingArchive({"same.png": _fake_blob("second")}),
        manifest, "demo", asset_cache=cache)

    assert first._decode("asset.same.png").payload == b"first"
    assert second._decode("asset.same.png").payload == b"second"


def test_failed_decode_is_negative_cached_and_read_once(fake_pixmap):
    bad = b"NOT-AN-IMAGE"
    archive = _CountingArchive({"bad.png": bad})
    cache = LruByteCache(max_bytes=1024)
    runtime = PackCharacterRuntime(
        archive, _cache_manifest(("bad.png",)), "demo",
        asset_cache=cache, content_digest="1" * 64)

    assert runtime._decode("asset.bad.png") is None
    assert runtime._decode("asset.bad.png") is None
    assert archive.reads["bad.png"] == 1
    assert fake_pixmap.decode_calls[bad] == 1
    assert cache.miss_count == 1


def test_single_frame_over_budget_prepare_fails_once(fake_pixmap):
    huge = _fake_blob("huge", width=10, height=10)  # 400 decoded bytes
    archive = _CountingArchive({"huge.png": huge})
    cache = LruByteCache(max_bytes=399)
    runtime = PackCharacterRuntime(
        archive, _cache_manifest(
            ("huge.png",), dimensions={"huge.png": (10, 10)}), "demo",
        asset_cache=cache, content_digest="2" * 64)

    assert runtime.prepare() is False
    assert runtime.prepare() is False
    assert archive.reads["huge.png"] == 1
    assert fake_pixmap.decode_calls[huge] == 0
    assert cache.byte_size == 0
    assert cache.miss_count == 0


def test_idle_sequence_over_budget_is_never_activated_or_redecoded(
        fake_pixmap):
    files = {
        "idle-a.png": _fake_blob("idle-a", width=8, height=8),
        "idle-b.png": _fake_blob("idle-b", width=8, height=8),
    }  # each 256 B, declared idle working set 512 B
    archive = _CountingArchive(files)
    runtime = PackCharacterRuntime(
        archive, _cache_manifest(
            tuple(files), dimensions={path: (8, 8) for path in files}), "demo",
        asset_cache=LruByteCache(max_bytes=400),
        content_digest="3" * 64)

    assert runtime.prepare() is False
    reads_after_first = archive.reads.copy()
    assert runtime.prepare() is False
    assert archive.reads == reads_after_first


def test_idle_plus_action_over_budget_degrades_action_without_decode_loop(
        fake_pixmap, monkeypatch):
    files = {
        "idle.png": _fake_blob("idle", width=6, height=6),       # 144 B
        "work-a.png": _fake_blob("work-a", width=6, height=6), # 144 B
        "work-b.png": _fake_blob("work-b", width=6, height=6), # 144 B
    }
    archive = _CountingArchive(files)
    runtime = PackCharacterRuntime(
        archive,
        _cache_manifest(
            ("idle.png",),
            {"core.work": ("work-a.png", "work-b.png")},
            dimensions={path: (6, 6) for path in files}),
        "demo", asset_cache=LruByteCache(max_bytes=400),
        content_digest="4" * 64)

    assert runtime.prepare() is True
    assert "core.work" in runtime.missing_semantics
    assert not runtime.capabilities().supports("core.work")
    reads_after_prepare = archive.reads.copy()
    monkeypatch.setattr(runtime, "_draw_fitted", lambda *_args: None)
    for elapsed in range(0, 1000, 25):
        runtime.render(None, None, _snapshot("work", elapsed))
    assert archive.reads == reads_after_prepare


def test_idle_plus_action_at_budget_remains_available_and_loads_on_demand(
        fake_pixmap, monkeypatch):
    files = {
        "idle.png": _fake_blob("idle", width=6, height=6),
        "work-a.png": _fake_blob("work-a", width=6, height=6),
        "work-b.png": _fake_blob("work-b", width=6, height=6),
    }  # 3 * 144 B == the cache budget
    archive = _CountingArchive(files)
    runtime = PackCharacterRuntime(
        archive,
        _cache_manifest(
            ("idle.png",),
            {"core.work": ("work-a.png", "work-b.png")},
            dimensions={path: (6, 6) for path in files}),
        "demo", asset_cache=LruByteCache(max_bytes=432),
        content_digest="5" * 64)

    assert runtime.prepare() is True
    assert "core.work" not in runtime.missing_semantics
    assert runtime.capabilities().supports("core.work")
    assert archive.reads == Counter(
        {"idle.png": 1, "work-a.png": 1, "work-b.png": 1})
    assert fake_pixmap.decode_calls == Counter({files["idle.png"]: 1})
    monkeypatch.setattr(runtime, "_draw_fitted", lambda *_args: None)
    for elapsed in range(0, 1000, 25):
        runtime.render(None, None, _snapshot("work", elapsed))
    assert archive.reads == Counter(
        {"idle.png": 1, "work-a.png": 1, "work-b.png": 1})
    assert fake_pixmap.decode_calls == Counter({
        files["idle.png"]: 1,
        files["work-a.png"]: 1,
        files["work-b.png"]: 1,
    })


def test_prepare_does_not_decode_all_optional_action_frames(fake_pixmap):
    files = {"idle.png": _fake_blob("idle")}
    actions = {}
    dimensions = {"idle.png": (4, 4)}
    for action_index, semantic in enumerate((
            "core.work", "core.rest", "core.eat", "core.exercise",
            "core.meeting", "core.music")):
        paths = tuple(
            f"action-{action_index}-{frame}.png" for frame in range(300))
        actions[semantic] = paths
        for path in paths:
            files[path] = _fake_blob(path)
            dimensions[path] = (4, 4)
    archive = _CountingArchive(files)
    runtime = PackCharacterRuntime(
        archive,
        _cache_manifest(
            ("idle.png",), actions, dimensions=dimensions),
        "demo", asset_cache=LruByteCache(max_bytes=48 * 1024 * 1024),
        content_digest="6" * 64,
    )

    assert runtime.prepare() is True
    assert fake_pixmap.decode_calls == Counter({files["idle.png"]: 1})
    assert sum(archive.reads.values()) == len(files)


def test_optional_decode_failure_is_cached_and_draws_warm_idle(
        fake_pixmap, monkeypatch):
    idle = _fake_blob("idle")
    broken = b"BROKEN-OPTIONAL-FRAME"
    files = {"idle.png": idle, "work.png": broken}
    archive = _CountingArchive(files)
    runtime = PackCharacterRuntime(
        archive,
        _cache_manifest(
            ("idle.png",), {"core.work": ("work.png",)}),
        "demo", asset_cache=LruByteCache(max_bytes=1024),
        content_digest="7" * 64,
    )
    drawn = []
    monkeypatch.setattr(
        runtime, "_draw_fitted",
        lambda _painter, _rect, pixmap: drawn.append(pixmap.payload),
    )

    assert runtime.prepare() is True
    runtime.render(None, None, _snapshot("work", 0))
    runtime.render(None, None, _snapshot("work", 50))

    assert drawn == [b"idle", b"idle"]
    assert fake_pixmap.decode_calls[broken] == 1


def test_fitted_draw_maps_original_pixmap_once_with_smooth_hint():
    """No intermediate QPixmap.scaled call is allowed in the DPI path."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter

    class Source:
        def width(self):
            return 512

        def height(self):
            return 512

        def scaled(self, *_args):
            raise AssertionError("intermediate pixmap scaling is forbidden")

    class Painter:
        def __init__(self):
            self.hints = []
            self.draws = []
            self.saved = 0

        def save(self):
            self.saved += 1

        def restore(self):
            self.saved -= 1

        def setRenderHint(self, hint, enabled):  # noqa: N802
            self.hints.append((hint, enabled))

        def drawPixmap(self, *args):  # noqa: N802
            self.draws.append(args)

    painter = Painter()
    source = Source()
    PackCharacterRuntime._draw_fitted(
        painter, QRectF(0, 0, 232, 236), source)

    assert painter.saved == 0
    assert painter.hints == [
        (QPainter.RenderHint.SmoothPixmapTransform, True)]
    assert len(painter.draws) == 1
    target, actual_source, source_rect = painter.draws[0]
    assert actual_source is source
    assert target == QRectF(0, 2, 232, 232)
    assert source_rect == QRectF(0, 0, 512, 512)
