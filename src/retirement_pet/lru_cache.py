"""Bounded LRU pixmap cache (DESIGN_V2 20; PETPACK_SPEC 17 budget mechanics).

Every cached pixmap is billed at its estimated DECODED size
(width * height * 4 bytes for ARGB32), not file size - that is what RAM
actually costs.  Eviction is least-recently-used; a hard byte ceiling is
enforced on every insert.  Misses (None results) are cached as failures
with zero cost so broken paths are not re-probed every frame.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Callable, Generic, TypeVar, cast

from retirement_pet.petpack.archive import MAX_FILE_COUNT

logger = logging.getLogger(__name__)

V = TypeVar("V")
_MISS = object()

# Two maximal runtime owners fit without making zero-byte negative entries
# unbounded.  Bind this to the archive budget so the limits cannot drift.
DEFAULT_MAX_KEYS = 2 * MAX_FILE_COUNT + 96

#: bytes per pixel for Qt ARGB32 premultiplied in-memory format
BYTES_PER_PIXEL = 4


def estimate_pixmap_bytes(pixmap) -> int:
    """Estimated decoded footprint of a QPixmap/QImage in bytes."""
    try:
        return max(0, int(pixmap.width())) * max(0, int(pixmap.height())) * BYTES_PER_PIXEL
    except Exception:  # noqa: BLE001 - already-destroyed pixmaps bill zero
        return 0


class LruByteCache(Generic[V]):
    """One bounded LRU for both decoded values and negative results.

    Positive entries are bounded by decoded bytes and every key (including a
    zero-byte known miss) participates in the same recency order and key
    ceiling.  ``put`` returns whether the supplied *positive value* was
    retained; storing a ``None`` negative result returns whether that miss was
    retained.  Positive and negative states for one key are always exclusive.
    """

    def __init__(self, max_bytes: int,
                 sizer: Callable[[V], int] = estimate_pixmap_bytes,
                 *, max_keys: int = DEFAULT_MAX_KEYS):
        self._max_bytes = max(0, int(max_bytes))
        self._max_keys = max(0, int(max_keys))
        self._sizer = sizer
        self._entries: "OrderedDict[str, tuple[object, int]]" = OrderedDict()
        self._bytes = 0
        self._miss_count = 0

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def byte_size(self) -> int:
        return self._bytes

    @property
    def entry_count(self) -> int:
        """Number of retained positive values (legacy-compatible meaning)."""
        return len(self._entries) - self._miss_count

    @property
    def max_keys(self) -> int:
        return self._max_keys

    @property
    def key_count(self) -> int:
        return len(self._entries)

    @property
    def miss_count(self) -> int:
        return self._miss_count

    def get(self, key: str) -> V | None:
        if key not in self._entries:
            return None
        self._entries.move_to_end(key)
        value, _size = self._entries[key]
        if value is _MISS:
            return None
        return cast(V, value)

    def is_known_miss(self, key: str) -> bool:
        entry = self._entries.get(key)
        if entry is None or entry[0] is not _MISS:
            return False
        self._entries.move_to_end(key)
        return True

    def put(self, key: str, value: V | None) -> bool:
        self._remove(key)
        if value is None:
            self._entries[key] = (_MISS, 0)
            self._miss_count += 1
            self._evict()
            return key in self._entries
        size = max(0, int(self._sizer(value)))
        if size > self._max_bytes:
            # A single oversized entry would evict everything and still
            # break the ceiling.  Remember the deterministic refusal so a
            # render loop cannot decode the same image every frame.
            logger.debug("cache entry exceeds decoded-byte ceiling")
            self._entries[key] = (_MISS, 0)
            self._miss_count += 1
            self._evict()
            return False
        self._entries[key] = (value, size)
        self._bytes += size
        self._evict()
        entry = self._entries.get(key)
        return entry is not None and entry[0] is not _MISS

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0
        self._miss_count = 0

    def discard_prefix(self, prefix: str) -> int:
        """Release every entry owned by one runtime namespace."""
        keys = [key for key in self._entries if key.startswith(prefix)]
        for key in keys:
            self._remove(key)
        return len(keys)

    def _remove(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        value, size = entry
        self._bytes -= size
        if value is _MISS:
            self._miss_count -= 1

    def _evict(self) -> None:
        while ((self._bytes > self._max_bytes
                or len(self._entries) > self._max_keys)
               and self._entries):
            _, (value, size) = self._entries.popitem(last=False)
            self._bytes -= size
            if value is _MISS:
                self._miss_count -= 1
            logger.debug("LRU evicted entry; cache now %d B", self._bytes)
