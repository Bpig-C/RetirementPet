"""Bounded LRU pixmap cache: byte billing, eviction, hard ceiling."""

from __future__ import annotations

from retirement_pet.lru_cache import LruByteCache, estimate_pixmap_bytes
from retirement_pet.petpack.archive import MAX_FILE_COUNT


class FakePix:
    def __init__(self, w: int, h: int):
        self._wh = (w, h)

    def width(self) -> int:
        return self._wh[0]

    def height(self) -> int:
        return self._wh[1]


def test_billing_uses_decoded_size():
    assert estimate_pixmap_bytes(FakePix(100, 50)) == 100 * 50 * 4


def test_eviction_is_lru_and_respects_ceiling():
    cache: LruByteCache[FakePix] = LruByteCache(max_bytes=1000)
    cache.put("a", FakePix(10, 10))  # 400 B
    cache.put("b", FakePix(10, 10))  # 400 B -> 800 B total
    cache.get("a")  # touch a so b becomes the LRU entry
    cache.put("c", FakePix(10, 10))  # 400 B -> must evict b, not a
    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None
    assert cache.byte_size <= cache.max_bytes


def test_oversized_entry_is_refused_not_cached():
    cache: LruByteCache[FakePix] = LruByteCache(max_bytes=100)
    cache.put("huge", FakePix(100, 100))  # 40000 B > ceiling
    assert cache.get("huge") is None
    assert cache.byte_size == 0


def test_misses_are_cached_without_cost():
    cache: LruByteCache[FakePix] = LruByteCache(max_bytes=1000)
    cache.put("gone", None)
    assert cache.get("gone") is None
    assert cache.is_known_miss("gone")
    assert cache.byte_size == 0
    cache.put("x", FakePix(5, 5))
    assert cache.byte_size == 5 * 5 * 4


def test_replace_updates_billing():
    cache: LruByteCache[FakePix] = LruByteCache(max_bytes=4000)
    cache.put("k", FakePix(10, 10))
    cache.put("k", FakePix(20, 20))  # replace: 1600 B, not 400+1600
    assert cache.byte_size == 20 * 20 * 4


def test_clear_resets_everything():
    cache: LruByteCache[FakePix] = LruByteCache(max_bytes=4000)
    cache.put("k", FakePix(10, 10))
    cache.put("miss", None)
    cache.clear()
    assert cache.byte_size == 0
    assert cache.entry_count == 0
    assert not cache.is_known_miss("miss")


def test_positive_and_negative_replacements_are_mutually_exclusive():
    cache: LruByteCache[FakePix] = LruByteCache(max_bytes=4000)

    assert cache.put("k", None) is True
    assert cache.is_known_miss("k")
    assert cache.put("k", FakePix(10, 10)) is True
    assert not cache.is_known_miss("k")
    assert cache.get("k") is not None
    assert cache.entry_count == 1
    assert cache.miss_count == 0
    assert cache.byte_size == 400

    assert cache.put("k", None) is True
    assert cache.get("k") is None
    assert cache.is_known_miss("k")
    assert cache.entry_count == 0
    assert cache.miss_count == 1
    assert cache.byte_size == 0


def test_total_key_lru_bounds_zero_cost_misses():
    cache: LruByteCache[FakePix] = LruByteCache(
        max_bytes=4000, max_keys=3)
    for index in range(10):
        assert cache.put(f"miss-{index}", None) is True

    assert cache.max_keys == 3
    assert cache.key_count == 3
    assert cache.miss_count == 3
    assert not cache.is_known_miss("miss-0")
    assert cache.is_known_miss("miss-9")


def test_positive_hits_and_negative_hits_share_one_recency_order():
    cache: LruByteCache[FakePix] = LruByteCache(
        max_bytes=4000, max_keys=2)
    cache.put("positive", FakePix(5, 5))
    cache.put("old-miss", None)
    assert cache.get("positive") is not None  # protect the positive value

    cache.put("new-miss", None)

    assert cache.get("positive") is not None
    assert not cache.is_known_miss("old-miss")
    assert cache.is_known_miss("new-miss")
    assert cache.key_count == 2


def test_default_total_key_limit_covers_one_maximal_pack_but_is_finite():
    cache: LruByteCache[FakePix] = LruByteCache(max_bytes=4000)
    assert 2 * MAX_FILE_COUNT <= cache.max_keys < 100_000


def test_discard_prefix_releases_only_one_runtime_owner():
    cache: LruByteCache[FakePix] = LruByteCache(max_bytes=4000)
    cache.put("old:a", FakePix(5, 5))
    cache.put("old:miss", None)
    cache.put("new:a", FakePix(5, 5))

    assert cache.discard_prefix("old:") == 2
    assert cache.key_count == 1
    assert cache.entry_count == 1
    assert cache.miss_count == 0
    assert cache.get("new:a") is not None


def test_oversized_replacement_drops_the_old_positive_value():
    cache: LruByteCache[FakePix] = LruByteCache(max_bytes=500)
    assert cache.put("k", FakePix(10, 10)) is True

    assert cache.put("k", FakePix(100, 100)) is False
    assert cache.get("k") is None
    assert cache.is_known_miss("k")
    assert cache.entry_count == 0
    assert cache.byte_size == 0
