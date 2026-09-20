"""P0 switch transaction: prepare/swap/commit, latest-wins, CAS, release."""

from __future__ import annotations

import gc
import sqlite3
import sys
import weakref
from dataclasses import replace
from pathlib import Path

import pytest

from retirement_pet.lifecycle import LifecycleError, PackLibrary
from retirement_pet.lru_cache import LruByteCache
from retirement_pet.switcher import (
    ActiveSelection,
    ActiveSelectionStore,
    CharacterCatalog,
    FakeSurface,
    RuntimeSwitcher,
    first_character_id,
    uninstall_with_safe_switch,
)

ROOT = Path(__file__).resolve().parent.parent
REF_PACK = ROOT / "tests" / "fixtures" / "petpack" / "minimal-static.petpack"
OFFICIAL_CAT = ROOT / "assets" / "petpack" / "retirement-cat-official.petpack"
OFFICIAL_CAT_CURRENT = ROOT / "assets" / "petpack" / \
    "retirement-cat-official-1.0.2.petpack"


@pytest.fixture()
def library(tmp_path):
    library = PackLibrary(tmp_path / "library")
    library.install(REF_PACK)
    library.register_builtin_release(OFFICIAL_CAT)
    yield library
    library.close()


@pytest.fixture()
def switcher(library, qt_application):
    return RuntimeSwitcher(library, ActiveSelectionStore(library),
                           asset_cache=LruByteCache(48 * 1024 * 1024))


def _revision(library, package_id):
    return next(r.revision_key for r in library.list_revisions()
                if r.revision_key.pack.package_id == package_id)


def _commit(library, switcher, surface, package_id, character):
    request = switcher.request(_revision(library, package_id), character)
    candidate = switcher.prepare(request)
    assert candidate is not None
    return switcher.swap_and_commit(request, surface)


def _owned_cache_keys(switcher, runtime) -> list[str]:
    prefix = runtime.cache_namespace
    return [key for key in switcher._cache._entries
            if key.startswith(prefix)]


def assert_trio_consistent(library, switcher, surface):
    """P0-B: the window renderer, ActiveSelection and the library agree."""
    active = switcher._store.get("active")
    assert active is not None, "a committed selection must exist"
    record = library.get_revision(active.revision_key())
    assert record is not None and record.pack_path.is_file()
    assert surface.renderer is switcher.current_runtime
    assert switcher.current_runtime.character_fqid() == active.character_fqid


# -- catalog -------------------------------------------------------------------


def test_catalog_projects_series_and_characters(library):
    catalog = CharacterCatalog(library)
    ids = {(e.series_id, e.character_id) for e in catalog.entries()}
    assert ("reference", "demo") in ids
    assert ("retirement-cat", "cat") in ids
    builtin_entries = [e for e in catalog.entries() if e.builtin]
    assert any(e.character_id == "cat" for e in builtin_entries)


def test_catalog_excludes_unready_paths(library):
    for record in library.list_revisions():
        if not record.builtin:
            record.pack_path.unlink()  # pending trash / external loss
    catalog = CharacterCatalog(library)
    assert all(e.builtin for e in catalog.entries())


def test_catalog_hides_only_exact_legacy_builtin_when_current_is_ready(library):
    from retirement_pet.embedded_pack import LEGACY_CONTENT_DIGEST

    current = library.register_builtin_release(OFFICIAL_CAT_CURRENT)
    records = library.list_revisions()
    assert any(
        record.revision_key.content_digest == LEGACY_CONTENT_DIGEST
        for record in records
    ), "the old Revision remains an immutable catalog fact"
    official_entries = [
        entry for entry in CharacterCatalog(library).entries()
        if entry.package_id == "retirement-cat-official"
    ]
    assert len(official_entries) == 1
    assert official_entries[0].package_version == "1.0.2"
    assert official_entries[0].revision_key == current.revision_key


# -- transaction: prepare/swap/commit ---------------------------------------------


def test_commit_requires_prepare_candidate(library, switcher):
    surface = FakeSurface()
    request = switcher.request(_revision(library, "minimal-static"), "demo")
    assert switcher.swap_and_commit(request, surface) is False
    assert switcher.last_error is not None


def test_full_transaction_commits_and_trio_consistent(library, switcher):
    surface = FakeSurface()
    assert _commit(library, switcher, surface, "minimal-static", "demo")
    assert_trio_consistent(library, switcher, surface)
    active = switcher._store.get("active")
    assert active.character_fqid.endswith("demo")
    assert active.generation == 1
    assert switcher._store.get("last_known_good") is None


def test_explicit_health_checkpoint_promotes_exact_active(library, switcher):
    surface = FakeSurface()
    assert _commit(library, switcher, surface, "minimal-static", "demo")
    active = switcher._store.get("active")
    assert switcher._store.get("last_known_good") is None

    assert switcher.checkpoint_active_health() is True

    assert switcher._store.get("last_known_good") == active


def test_restore_exact_active_does_not_write_active_or_lkg(library, switcher):
    surface = FakeSurface()
    assert _commit(library, switcher, surface, "minimal-static", "demo")
    assert switcher.checkpoint_active_health()
    active_before = switcher._store.get("active")
    lkg_before = switcher._store.get("last_known_good")

    resumed = RuntimeSwitcher(library, switcher._store)
    restored_surface = FakeSurface()
    assert resumed.restore_selection(active_before, restored_surface) is True

    assert resumed._store.get("active") == active_before
    assert resumed._store.get("last_known_good") == lkg_before
    assert restored_surface.renderer is resumed.current_runtime


def test_prepare_failure_keeps_visible_character(library, switcher):
    surface = FakeSurface()
    _commit(library, switcher, surface, "minimal-static", "demo")
    before = surface.renderer

    request = switcher.request(
        _revision(library, "retirement-cat-official"), "no-such-character")
    assert switcher.prepare(request) is None
    assert switcher.swap_and_commit(request, surface) is False
    assert surface.renderer is before
    assert switcher._store.get("active").character_fqid.endswith("demo")


def test_surface_first_frame_failure_rolls_back_renderer(library, switcher):
    surface = FakeSurface(fail_first_frame=True)
    request = switcher.request(_revision(library, "minimal-static"), "demo")
    candidate = switcher.prepare(request)
    assert candidate is not None
    assert _owned_cache_keys(switcher, candidate)
    assert switcher.swap_and_commit(request, surface) is False
    assert switcher._store.get("active") is None
    assert request.candidate is None  # released
    assert _owned_cache_keys(switcher, candidate) == []
    assert switcher.last_error.code == "PPK-LCY-E006"


def test_new_switcher_resumes_from_persisted_state(library, switcher):
    surface = FakeSurface()
    _commit(library, switcher, surface, "minimal-static", "demo")
    persisted = switcher._store.get("active")

    resumed = RuntimeSwitcher(library, switcher._store)
    assert resumed.active_generation == persisted.generation
    assert resumed.commit_sequence == persisted.commit_sequence
    assert resumed.request_generation >= persisted.generation


def test_new_switcher_uses_high_water_mark_across_both_slots(library, switcher):
    rk = _revision(library, "minimal-static")
    base = ActiveSelection(
        publisher_id=rk.pack.publisher_id, package_id=rk.pack.package_id,
        package_version=rk.package_version, content_digest=rk.content_digest,
        character_fqid="community.retirementpet.minimal-static.reference.demo",
        variant_id=None, config_revision_id=None,
        generation=3, commit_sequence=4)
    switcher._store.commit(base, "active")
    switcher._store.commit(
        replace(base, generation=11, commit_sequence=17),
        "last_known_good")

    resumed = RuntimeSwitcher(library, switcher._store)

    assert resumed.active_generation == 11
    assert resumed.request_generation == 11
    assert resumed.commit_sequence == 17


def test_prepare_rejects_mismatched_full_selection_tuple(library, switcher):
    rk = _revision(library, "minimal-static")

    forged_fqid = switcher.request(rk, "forged.publisher.series.demo")
    assert switcher.prepare(forged_fqid) is None

    missing_variant = switcher.request(rk, "demo", variant_id="missing")
    assert switcher.prepare(missing_variant) is None

    unknown_config = switcher.request(
        rk, "demo", config_revision_id="config-does-not-exist")
    assert switcher.prepare(unknown_config) is None


def test_prepare_rejects_even_declared_non_null_variant(
        library, switcher, tmp_path):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_petpack import build_pack, make_manifest, png_bytes

    manifest = make_manifest()
    manifest["characters"][0]["variants"] = [{"id": "blue"}]
    files = {
        "assets/thumb.png": png_bytes(4, 4),
        "assets/idle_0.png": png_bytes(8, 8),
    }
    path = tmp_path / "declared-variant.petpack"
    path.write_bytes(build_pack(manifest, files))
    record = library.install(path)

    request = switcher.request(
        record.revision_key, "demo", variant_id="blue")
    assert switcher.prepare(request) is None


def test_prepare_rejects_valid_pack_replacing_expected_revision(
        library, switcher):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_petpack import build_pack, make_manifest, png_bytes

    rk = _revision(library, "minimal-static")
    record = library.get_revision(rk)
    manifest = make_manifest()
    manifest["package"]["publisher_id"] = rk.pack.publisher_id
    manifest["package"]["id"] = rk.pack.package_id
    manifest["package"]["version"] = rk.package_version
    manifest["series"]["id"] = "reference"
    manifest["rights_declarations"][0]["claimant_ref"] = rk.pack.publisher_id
    manifest["sources"][0]["creator"] = rk.pack.publisher_id
    manifest["actions"][1]["id"] = (
        "community.retirementpet.minimal-static.reference.demo.wave")
    manifest["actions"][1]["semantic"] = manifest["actions"][1]["id"]
    files = {
        "assets/thumb.png": png_bytes(4, 4, (1, 2, 3, 255)),
        "assets/idle_0.png": png_bytes(8, 8, (4, 5, 6, 255)),
    }
    record.pack_path.write_bytes(build_pack(manifest, files))

    request = switcher.request(rk, "demo")
    assert switcher.prepare(request) is None


# -- P0-C: REAL latest-wins (out-of-order completion) -------------------------------


def test_out_of_order_completion_newer_request_wins(library, switcher):
    """A starts first, B starts later; A finishes LAST but must NOT win."""
    surface = FakeSurface()
    request_a = switcher.request(
        _revision(library, "minimal-static"), "demo")      # generation 1
    candidate_a = switcher.prepare(request_a)
    assert candidate_a is not None
    request_b = switcher.request(
        _revision(library, "retirement-cat-official"), "cat")  # gen 2: latest

    candidate_b = switcher.prepare(request_b)
    assert candidate_a is not None and candidate_b is not None

    assert switcher.swap_and_commit(request_b, surface) is True
    assert surface.renderer is candidate_b
    assert _owned_cache_keys(switcher, candidate_a) == []
    assert _owned_cache_keys(switcher, candidate_b)

    # A finishes afterwards -> stale: rejected, released, B untouched
    assert switcher.swap_and_commit(request_a, surface) is False
    assert switcher.last_error.code == "PPK-LCY-W002"
    assert request_a.candidate is None
    assert surface.renderer is candidate_b
    assert_trio_consistent(library, switcher, surface)
    assert switcher._store.get("active").character_fqid.endswith("cat")


def test_prepare_rejects_request_that_was_stale_before_build(
        library, switcher):
    request_a = switcher.request(
        _revision(library, "minimal-static"), "demo")
    switcher.request(
        _revision(library, "retirement-cat-official"), "cat")

    assert switcher.prepare(request_a) is None
    assert request_a.candidate is None
    assert switcher._prepared_candidates == {}


def test_new_request_releases_abandoned_prepared_candidate(library, switcher):
    request_a = switcher.request(
        _revision(library, "minimal-static"), "demo")
    candidate_a = switcher.prepare(request_a)
    assert candidate_a is not None
    assert _owned_cache_keys(switcher, candidate_a)

    switcher.request(
        _revision(library, "retirement-cat-official"), "cat")

    assert _owned_cache_keys(switcher, candidate_a) == []


def test_legacy_commit_rejection_releases_candidate_archive_owner(
        library, switcher, monkeypatch):
    request = switcher.request(
        _revision(library, "minimal-static"), "demo")
    candidate = switcher.prepare(request)
    assert candidate is not None
    assert candidate in switcher._prepared_candidates.values()
    monkeypatch.setattr(
        switcher._store, "commit_if_newer", lambda _selection: False)

    assert switcher.commit_candidate(candidate) is False

    assert candidate not in switcher._prepared_candidates.values()
    assert _owned_cache_keys(switcher, candidate) == []


def test_store_cas_rejects_stale_generation(library, switcher):
    surface = FakeSurface()
    _commit(library, switcher, surface, "minimal-static", "demo")
    committed = switcher._store.get("active")

    stale = ActiveSelection(
        publisher_id="official", package_id="retirement-cat-official",
        package_version="1.0.0", content_digest="f" * 64,
        character_fqid="official.retirement-cat.retirement-cat.cat",
        variant_id=None, config_revision_id=None,
        generation=committed.generation,  # NOT newer
        commit_sequence=committed.commit_sequence + 1)
    assert switcher._store.commit_if_newer(stale) is False
    assert switcher._store.get("active").character_fqid.endswith("demo")


def test_concurrent_writers_higher_generation_wins(library, switcher):
    surface = FakeSurface()
    _commit(library, switcher, surface, "minimal-static", "demo")

    newer = ActiveSelection(
        publisher_id="official", package_id="retirement-cat-official",
        package_version="1.0.0",
        content_digest=_revision(
            library, "retirement-cat-official").content_digest,
        character_fqid="official.retirement-cat.retirement-cat.cat",
        variant_id=None, config_revision_id=None,
        generation=switcher._store.get("active").generation + 5,
        commit_sequence=99)
    assert switcher._store.commit_if_newer(newer) is True
    older = ActiveSelection(
        publisher_id="x", package_id="y", package_version="1.0.0",
        content_digest="0" * 64, character_fqid="x.y.z", variant_id=None,
        config_revision_id=None,
        generation=newer.generation - 1, commit_sequence=100)
    assert switcher._store.commit_if_newer(older) is False


def test_db_disconnect_commit_resolves_by_readback(library, switcher):
    surface = FakeSurface()
    _commit(library, switcher, surface, "minimal-static", "demo")
    seq = switcher._store.get("active").commit_sequence

    switcher._store._db.close()
    switcher._store._db = sqlite3.connect(library.root / "state.db")
    reloaded = switcher._store.get("active")
    assert reloaded.commit_sequence == seq
    assert reloaded.character_fqid.endswith("demo")


def test_indeterminate_requires_full_selection_tuple_match(
        library, switcher, monkeypatch):
    surface = FakeSurface()
    assert _commit(library, switcher, surface, "minimal-static", "demo")
    old_renderer = surface.renderer
    old_selection = switcher._store.get("active")

    request = switcher.request(
        _revision(library, "retirement-cat-official"), "cat")
    assert switcher.prepare(request) is not None

    def ambiguous_commit(proposed, slot="active"):
        authoritative = replace(
            old_selection, generation=proposed.generation,
            commit_sequence=proposed.commit_sequence)
        switcher._store.commit(authoritative, slot)
        raise sqlite3.OperationalError("connection outcome unknown")

    monkeypatch.setattr(switcher._store, "commit_if_newer", ambiguous_commit)

    assert switcher.swap_and_commit(request, surface) is False
    assert surface.renderer is old_renderer
    assert switcher.current_runtime is old_renderer
    assert switcher._store.get("active").character_fqid == \
        old_selection.character_fqid


def test_indeterminate_exact_candidate_readback_keeps_candidate(
        library, switcher, monkeypatch):
    surface = FakeSurface()
    request = switcher.request(_revision(library, "minimal-static"), "demo")
    candidate = switcher.prepare(request)
    original = switcher._store.commit_if_newer

    def committed_but_ack_lost(proposed, slot="active"):
        assert original(proposed, slot) is True
        raise sqlite3.OperationalError("ack lost")

    monkeypatch.setattr(
        switcher._store, "commit_if_newer", committed_but_ack_lost)
    assert switcher.swap_and_commit(request, surface) is True
    assert surface.renderer is candidate
    assert switcher.current_runtime is candidate


def test_precommit_read_failure_releases_candidate_and_bounds_safe_mode(
        library, switcher, monkeypatch):
    surface = FakeSurface()
    assert _commit(library, switcher, surface, "minimal-static", "demo")
    old_renderer = surface.renderer
    request = switcher.request(
        _revision(library, "retirement-cat-official"), "cat")
    candidate = switcher.prepare(request)
    assert candidate is not None
    monkeypatch.setattr(
        switcher._store, "get",
        lambda _slot="active": (_ for _ in ()).throw(
            sqlite3.OperationalError("pre-commit read unavailable")),
    )

    assert switcher.swap_and_commit(request, surface) is False
    assert switcher.in_safe_mode is True
    assert switcher.retained_renderers == (old_renderer,)
    assert _owned_cache_keys(switcher, candidate) == []
    retained_before = switcher.retained_renderers
    key_count_before = switcher._cache.key_count

    retry = switcher.request(
        _revision(library, "retirement-cat-official"), "cat")
    assert switcher.prepare(retry) is None
    assert switcher.retained_renderers == retained_before
    assert switcher._cache.key_count == key_count_before


def test_confirmed_restore_can_exit_safe_mode_and_release_old_leases(
        library, switcher):
    surface = FakeSurface()
    assert _commit(library, switcher, surface, "minimal-static", "demo")
    active = switcher._store.get("active")
    old_renderer = switcher.current_runtime
    old_namespace = old_renderer.cache_namespace
    switcher.enter_safe_mode(surface)

    assert switcher.restore_selection(active, surface) is True

    assert switcher.in_safe_mode is False
    assert switcher.retained_renderers == ()
    assert switcher.current_runtime is not old_renderer
    assert not any(key.startswith(old_namespace)
                   for key in switcher._cache._entries)
    assert _owned_cache_keys(switcher, switcher.current_runtime)


def test_indeterminate_no_row_enters_safe_mode_and_retains_runtimes(
        library, switcher, monkeypatch):
    surface = FakeSurface()
    assert _commit(library, switcher, surface, "minimal-static", "demo")
    old_renderer = surface.renderer
    request = switcher.request(
        _revision(library, "retirement-cat-official"), "cat")
    candidate = switcher.prepare(request)
    safe_renderer = object()
    switcher._safe_renderer = safe_renderer

    def row_disappeared(_proposed, slot="active"):
        with switcher._store._db:
            switcher._store._db.execute(
                "DELETE FROM active_selection WHERE slot=?", (slot,))
        raise sqlite3.OperationalError("outcome unknown")

    monkeypatch.setattr(switcher._store, "commit_if_newer", row_disappeared)
    assert switcher.swap_and_commit(request, surface) is False
    assert switcher.in_safe_mode is True
    assert surface.renderer is safe_renderer
    assert old_renderer in switcher.retained_renderers
    assert candidate in switcher.retained_renderers
    assert _owned_cache_keys(switcher, old_renderer)
    assert _owned_cache_keys(switcher, candidate)


def test_indeterminate_readback_error_enters_safe_mode(
        library, switcher, monkeypatch):
    surface = FakeSurface()
    assert _commit(library, switcher, surface, "minimal-static", "demo")
    old_renderer = surface.renderer
    request = switcher.request(
        _revision(library, "retirement-cat-official"), "cat")
    candidate = switcher.prepare(request)
    safe_renderer = object()
    switcher._safe_renderer = safe_renderer
    monkeypatch.setattr(
        switcher._store, "commit_if_newer",
        lambda _selection: (_ for _ in ()).throw(
            sqlite3.OperationalError("write uncertain")))
    monkeypatch.setattr(
        switcher._store, "reconnect",
        lambda: (_ for _ in ()).throw(
            sqlite3.OperationalError("readback unavailable")))

    assert switcher.swap_and_commit(request, surface) is False
    assert switcher.in_safe_mode is True
    assert surface.renderer is safe_renderer
    assert old_renderer in switcher.retained_renderers
    assert candidate in switcher.retained_renderers


def test_cas_loser_renders_authoritative_persisted_selection(
        library, switcher, monkeypatch):
    surface = FakeSurface()
    assert _commit(library, switcher, surface, "minimal-static", "demo")
    request = switcher.request(
        _revision(library, "retirement-cat-official"), "cat")
    candidate = switcher.prepare(request)
    assert candidate is not None

    def competing_commit(proposed, slot="active"):
        winner = replace(
            proposed, generation=proposed.generation + 5,
            commit_sequence=proposed.commit_sequence + 5)
        switcher._store.commit(winner, slot)
        return False

    monkeypatch.setattr(switcher._store, "commit_if_newer", competing_commit)

    assert switcher.swap_and_commit(request, surface) is False
    authoritative = switcher._store.get("active")
    assert surface.renderer is candidate
    assert switcher.current_runtime is candidate
    assert surface.renderer.character_fqid() == authoritative.character_fqid


def test_cas_loser_get_error_enters_safe_mode(
        library, switcher, monkeypatch):
    surface = FakeSurface()
    assert _commit(library, switcher, surface, "minimal-static", "demo")
    old_renderer = surface.renderer
    request = switcher.request(
        _revision(library, "retirement-cat-official"), "cat")
    candidate = switcher.prepare(request)
    safe_renderer = object()
    switcher._safe_renderer = safe_renderer
    monkeypatch.setattr(switcher._store, "commit_if_newer",
                        lambda _selection: False)
    original_get = switcher._store.get
    get_calls = 0

    def fail_authoritative_read(_slot="active"):
        nonlocal get_calls
        get_calls += 1
        if get_calls == 1:
            return original_get(_slot)
        raise sqlite3.OperationalError("authoritative read failed")

    monkeypatch.setattr(
        switcher._store, "get", fail_authoritative_read)

    assert switcher.swap_and_commit(request, surface) is False
    assert switcher.in_safe_mode is True
    assert surface.renderer is safe_renderer
    assert old_renderer in switcher.retained_renderers
    assert candidate in switcher.retained_renderers


def test_cas_loser_loads_different_authoritative_runtime(
        library, switcher, monkeypatch):
    surface = FakeSurface()
    painted = []
    surface.paint_first_frame = lambda renderer: (
        painted.append(renderer) or True)
    cat_rk = _revision(library, "retirement-cat-official")
    cat_record = library.get_revision(cat_rk)
    winner_id = first_character_id(library, cat_record)
    probe = switcher.request(cat_rk, winner_id)
    winner_probe = switcher.prepare(probe)
    winner_character = winner_probe.character_fqid()
    request = switcher.request(_revision(library, "minimal-static"), "demo")
    candidate = switcher.prepare(request)
    assert cat_rk != request.revision_key
    assert winner_character != candidate.character_fqid()

    def competing_commit(proposed, slot="active"):
        winner = ActiveSelection(
            publisher_id=cat_rk.pack.publisher_id,
            package_id=cat_rk.pack.package_id,
            package_version=cat_rk.package_version,
            content_digest=cat_rk.content_digest,
            character_fqid=winner_character,
            variant_id=None, config_revision_id=None,
            generation=proposed.generation + 5,
            commit_sequence=proposed.commit_sequence + 5)
        switcher._store.commit(winner, slot)
        return False

    monkeypatch.setattr(switcher._store, "commit_if_newer", competing_commit)
    assert switcher.swap_and_commit(request, surface) is False
    authoritative = switcher._store.get("active")
    assert surface.renderer is not candidate
    assert surface.renderer is switcher.current_runtime
    assert surface.renderer.character_fqid() == authoritative.character_fqid
    assert painted[-1] is switcher.current_runtime
    assert _owned_cache_keys(switcher, candidate) == []
    assert _owned_cache_keys(switcher, switcher.current_runtime)


def _install_third_runtime_pack(library, tmp_path):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_petpack import base_files, build_pack, make_manifest

    manifest = make_manifest()
    manifest["package"]["id"] = "third-static"
    manifest["series"]["id"] = "third-series"
    action_fqid = (
        "community.example.third-static.third-series.demo.wave")
    manifest["actions"][1]["id"] = action_fqid
    manifest["actions"][1]["semantic"] = action_fqid
    path = tmp_path / "third-static.petpack"
    path.write_bytes(build_pack(manifest, base_files()))
    return library.install(path)


@pytest.mark.parametrize("fault", ["swap", "paint"])
def test_authoritative_surface_exception_retains_all_runtime_leases(
        library, switcher, monkeypatch, tmp_path, fault):
    """A third valid CAS winner must survive until safe-mode shutdown."""
    surface = FakeSurface()
    assert _commit(library, switcher, surface, "minimal-static", "demo")
    old_renderer = surface.renderer

    third = _install_third_runtime_pack(library, tmp_path)
    winner_id = first_character_id(library, third)
    probe = switcher.request(third.revision_key, winner_id)
    winner_probe = switcher.prepare(probe)
    assert winner_probe is not None
    winner_fqid = winner_probe.character_fqid()

    request = switcher.request(
        _revision(library, "retirement-cat-official"), "cat")
    candidate = switcher.prepare(request)
    assert candidate is not None
    assert len({old_renderer.character_fqid(), candidate.character_fqid(),
                winner_fqid}) == 3

    def competing_commit(proposed, slot="active"):
        winner = ActiveSelection(
            publisher_id=third.revision_key.pack.publisher_id,
            package_id=third.revision_key.pack.package_id,
            package_version=third.revision_key.package_version,
            content_digest=third.revision_key.content_digest,
            character_fqid=winner_fqid,
            variant_id=None,
            config_revision_id=None,
            generation=proposed.generation + 5,
            commit_sequence=proposed.commit_sequence + 5,
        )
        switcher._store.commit(winner, slot)
        return False

    original_swap = surface.swap_renderer
    original_paint = surface.paint_first_frame

    def guarded_swap(renderer):
        if fault == "swap" and getattr(
                renderer, "character_fqid", lambda: None)() == winner_fqid:
            raise RuntimeError("authoritative swap exploded")
        return original_swap(renderer)

    def guarded_paint(renderer):
        if fault == "paint" and getattr(
                renderer, "character_fqid", lambda: None)() == winner_fqid:
            raise RuntimeError("authoritative paint exploded")
        return original_paint(renderer)

    safe_renderer = object()
    switcher._safe_renderer = safe_renderer
    monkeypatch.setattr(switcher._store, "commit_if_newer", competing_commit)
    monkeypatch.setattr(surface, "swap_renderer", guarded_swap)
    monkeypatch.setattr(surface, "paint_first_frame", guarded_paint)

    assert switcher.swap_and_commit(request, surface) is False
    assert surface.renderer is safe_renderer
    assert switcher.current_runtime is None
    assert switcher.in_safe_mode is True
    leases = switcher.retained_renderers
    assert old_renderer in leases
    assert candidate in leases
    third_leases = [runtime for runtime in leases if getattr(
        runtime, "character_fqid", lambda: None)() == winner_fqid]
    assert len(third_leases) == 1


def test_cas_loser_unrenderable_authoritative_enters_safe_mode(
        library, switcher, monkeypatch):
    surface = FakeSurface()
    assert _commit(library, switcher, surface, "minimal-static", "demo")
    old_renderer = surface.renderer
    request = switcher.request(
        _revision(library, "retirement-cat-official"), "cat")
    candidate = switcher.prepare(request)
    safe_renderer = object()
    switcher._safe_renderer = safe_renderer

    def invalid_winner(proposed, slot="active"):
        winner = replace(
            proposed,
            content_digest="0" * 64,
            generation=proposed.generation + 5,
            commit_sequence=proposed.commit_sequence + 5)
        switcher._store.commit(winner, slot)
        return False

    monkeypatch.setattr(switcher._store, "commit_if_newer", invalid_winner)
    assert switcher.swap_and_commit(request, surface) is False
    assert switcher.in_safe_mode is True
    assert surface.renderer is safe_renderer
    assert old_renderer in switcher.retained_renderers
    assert candidate in switcher.retained_renderers


# -- P0-D: runtime and cache release ------------------------------------------------


def test_old_runtime_released_after_commit(library, switcher):
    surface = FakeSurface()
    _commit(library, switcher, surface, "minimal-static", "demo")
    first = switcher.current_runtime
    first_namespace = first.cache_namespace
    assert _owned_cache_keys(switcher, first)
    ref = weakref.ref(first)

    _commit(library, switcher, surface, "retirement-cat-official", "cat")
    assert switcher.current_runtime is not first
    assert switcher.retired_count == 1
    assert not any(key.startswith(first_namespace)
                   for key in switcher._cache._entries)
    assert _owned_cache_keys(switcher, switcher.current_runtime)
    del first
    gc.collect()
    assert ref() is None, "retired runtime must not be pinned by the switcher"


def test_shared_cache_bounded_across_100_switches(library, switcher):
    surface = FakeSurface()
    for i in range(100):
        package = ("minimal-static" if i % 2 == 0
                   else "retirement-cat-official")
        character = "demo" if package == "minimal-static" else "cat"
        assert _commit(library, switcher, surface, package, character)
    cache = switcher._cache
    assert cache.byte_size <= cache.max_bytes
    assert cache.entry_count <= 20  # two packs, not 100 private caches
    assert_trio_consistent(library, switcher, surface)


def test_switcher_shutdown_releases_cache(library, switcher):
    surface = FakeSurface()
    _commit(library, switcher, surface, "minimal-static", "demo")
    assert switcher._cache.byte_size > 0
    switcher.shutdown()
    assert switcher.current_runtime is None
    assert switcher._cache.byte_size == 0


# -- P0-A: safe targets ---------------------------------------------------------------


def test_selection_to_missing_revision_is_not_accepted(library, switcher):
    surface = FakeSurface()
    _commit(library, switcher, surface, "minimal-static", "demo")
    rk = _revision(library, "minimal-static")
    library.uninstall_revision(rk, active_guard=lambda _rk: False)
    assert library.get_revision(rk) is None

    selection = switcher._store.get("active")
    assert selection.revision_key() == rk
    fresh = RuntimeSwitcher(library, switcher._store)
    request = fresh.request(selection.revision_key(),
                            selection.character_fqid)
    assert fresh.prepare(request) is None  # revision not READY -> refused


def test_uninstall_sole_user_pack_lands_on_real_builtin(library, switcher):
    """Safe fallback is the REAL builtin revision - never a fake
    all-zero-digest selection (P0-A)."""
    surface = FakeSurface()
    demo_rk = _revision(library, "minimal-static")
    _commit(library, switcher, surface, "minimal-static", "demo")

    assert uninstall_with_safe_switch(library, switcher, demo_rk,
                                      surface=surface)
    active = switcher._store.get("active")
    record = library.get_revision(active.revision_key())
    assert record is not None and record.builtin  # REAL READY revision
    assert active.content_digest != "0" * 64
    assert_trio_consistent(library, switcher, surface)


def test_builtin_official_pack_cannot_be_uninstalled(library, switcher):
    cat_rk = _revision(library, "retirement-cat-official")
    with pytest.raises(LifecycleError):
        library.uninstall_revision(cat_rk)
    assert library.get_revision(cat_rk) is not None
