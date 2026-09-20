"""ACTIVATE_COMMITTED append-only audit (V12-02 review P-3).

The ACTIVE row stays the only selection authority.  These tests pin the
audit layer on top of it: a journalled line exists exactly for confirmed
commits, CAS losers and unknown outcomes never journal, restart backfill
is idempotent, and an audit failure can never flip a switch outcome.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from retirement_pet.lifecycle import PackLibrary
from retirement_pet.lru_cache import LruByteCache
from retirement_pet.switcher import (
    ActiveSelectionStore,
    FakeSurface,
    RuntimeSwitcher,
)

ROOT = Path(__file__).resolve().parent.parent
REF_PACK = ROOT / "tests" / "fixtures" / "petpack" / "minimal-static.petpack"
PACKAGE_ID = "minimal-static"
CHARACTER = "demo"


@pytest.fixture()
def library(tmp_path):
    library = PackLibrary(tmp_path / "library")
    library.install(REF_PACK)
    yield library
    library.close()


@pytest.fixture()
def switcher(library):
    return RuntimeSwitcher(library, ActiveSelectionStore(library),
                           asset_cache=LruByteCache(48 * 1024 * 1024))


def _activation_events(library) -> list[dict]:
    path = library.journal_dir / "events.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line.decode("utf-8"))
        for line in path.read_bytes().splitlines()
        if line.strip() and json.loads(line.decode("utf-8")).get("event")
        == "ACTIVATE_COMMITTED"
    ]


def _swap_and_commit(switcher) -> bool:
    revision = next(r.revision_key for r in
                    switcher._library.list_revisions()
                    if r.revision_key.pack.package_id == PACKAGE_ID)
    request = switcher.request(revision, CHARACTER)
    candidate = switcher.prepare(request)
    assert candidate is not None
    return switcher.swap_and_commit(request, FakeSurface())


def test_successful_switch_appends_exactly_one_activation_event(
        library, switcher):
    assert _activation_events(library) == []
    assert _swap_and_commit(switcher)

    events = _activation_events(library)
    assert len(events) == 1
    event = events[0]
    assert event["package_id"] == PACKAGE_ID
    assert event["publisher_id"] == "community.retirementpet"
    assert event["package_version"] == "1.0.0"
    assert event["character_fqid"].endswith(".demo")
    assert event["generation"] == 1 and event["commit_sequence"] == 1
    assert event["recovered"] is False
    assert len(event["content_digest"]) == 64
    # install events and activation events share one append-only journal
    all_events = [
        json.loads(line.decode("utf-8"))
        for line in (library.journal_dir / "events.jsonl")
        .read_bytes().splitlines() if line.strip()
    ]
    assert [e["event"] for e in all_events] == [
        "INSTALL_COMMITTED", "ACTIVATE_COMMITTED"]


def test_cas_loser_never_journals(library, switcher):
    # a third-party writer holds a newer generation; our commit must lose
    winner = switcher._store.get("active")
    assert winner is None or winner.generation >= 1
    from retirement_pet.switcher import ActiveSelection
    record = next(r for r in library.list_revisions()
                  if r.revision_key.pack.package_id == PACKAGE_ID)
    rk = record.revision_key
    competitor = ActiveSelection(
        publisher_id=rk.pack.publisher_id, package_id=rk.pack.package_id,
        package_version=rk.package_version, content_digest=rk.content_digest,
        character_fqid=f"{rk.pack.publisher_id}.{rk.pack.package_id}."
                       f"{rk.package_version}.{CHARACTER}",
        variant_id=None, config_revision_id=None,
        generation=10_000, commit_sequence=10_000)
    assert switcher._store.commit(competitor, slot="active")

    assert not _swap_and_commit(switcher)
    assert _activation_events(library) == []


def test_indeterminate_readback_confirmed_commit_journals_once(
        library, switcher, monkeypatch):
    real_commit = switcher._store.commit_if_newer
    did_raise = []

    def flaky_commit(selection, slot="active"):
        if not did_raise:
            did_raise.append(True)
            assert real_commit(selection, slot)  # the write DID land
            raise sqlite3.OperationalError("simulated crash after write")
        return real_commit(selection, slot)

    monkeypatch.setattr(switcher._store, "commit_if_newer", flaky_commit)
    assert _swap_and_commit(switcher)
    assert len(_activation_events(library)) == 1

    # idempotency: replaying the identical confirmed activation appends nothing
    active = switcher._store.get("active")
    assert library.record_activation_committed(
        publisher_id=active.publisher_id, package_id=active.package_id,
        package_version=active.package_version,
        content_digest=active.content_digest,
        character_fqid=active.character_fqid,
        generation=active.generation,
        commit_sequence=active.commit_sequence)
    assert len(_activation_events(library)) == 1


def test_restart_reconciliation_backfills_missing_event_once(
        library, switcher):
    # committed through the raw store: crash window before the journal line
    assert _swap_and_commit(switcher)
    events_path = library.journal_dir / "events.jsonl"
    payload = [line for line in events_path.read_bytes().splitlines()
               if line.strip()
               and json.loads(line.decode("utf-8")).get("event")
               != "ACTIVATE_COMMITTED"]
    events_path.write_bytes(b"\n".join(payload) + b"\n")
    assert _activation_events(library) == []

    fresh = ActiveSelectionStore(library)
    try:
        backfilled = _activation_events(library)
        assert len(backfilled) == 1
        assert backfilled[0]["recovered"] is True
        assert backfilled[0]["generation"] == 1
        # the ACTIVE row itself is untouched by the audit backfill
        assert fresh.get("active").generation == 1
    finally:
        fresh.close()

    again = ActiveSelectionStore(library)
    try:
        assert len(_activation_events(library)) == 1  # still exactly one
    finally:
        again.close()


def test_audit_failure_never_fails_a_confirmed_switch(
        library, switcher, monkeypatch):
    monkeypatch.setattr(library, "record_activation_committed",
                        lambda **kwargs: False)
    assert _swap_and_commit(switcher)
    assert switcher._store.get("active") is not None

    def explode(**kwargs):
        raise RuntimeError("journal on fire")
    monkeypatch.setattr(library, "record_activation_committed", explode)
    assert _swap_and_commit(switcher)
    assert switcher._store.get("active").generation == 2


def test_audit_failure_then_next_switch_restores_full_history(
        library, switcher, monkeypatch):
    # generation 1 is confirmed but its audit write fails; the fact must
    # stay durable beyond the ACTIVE slot (review recheck CR-P03)
    monkeypatch.setattr(library, "record_activation_committed",
                        lambda **kwargs: False)
    assert _swap_and_commit(switcher)
    assert switcher._store.get("active").generation == 1
    assert _activation_events(library) == []
    assert switcher._store.pending_activation_audit_count() == 1

    monkeypatch.undo()
    assert _swap_and_commit(switcher)
    events = _activation_events(library)
    assert [event["generation"] for event in events] == [1, 2]
    assert events[0]["recovered"] is True
    assert events[1]["recovered"] is False
    assert switcher._store.pending_activation_audit_count() == 0

    # a restart replays nothing and drops nothing: history is complete
    fresh = ActiveSelectionStore(library)
    try:
        assert [event["generation"]
                for event in _activation_events(library)] == [1, 2]
        assert fresh.pending_activation_audit_count() == 0
        assert fresh.get("active").generation == 2
    finally:
        fresh.close()


def test_consecutive_audit_failures_recover_on_restart(
        library, switcher, monkeypatch):
    monkeypatch.setattr(library, "record_activation_committed",
                        lambda **kwargs: False)
    for expected_generation in (1, 2, 3):
        assert _swap_and_commit(switcher)
        assert switcher._store.get("active").generation == expected_generation
    assert _activation_events(library) == []
    assert switcher._store.pending_activation_audit_count() == 3
    switcher._store.close()

    # restart with a healthy journal replays every fact in commit order
    monkeypatch.undo()
    fresh = ActiveSelectionStore(library)
    try:
        events = _activation_events(library)
        assert [event["generation"] for event in events] == [1, 2, 3]
        assert all(event["recovered"] for event in events)
        assert fresh.pending_activation_audit_count() == 0
        assert fresh.get("active").generation == 3
    finally:
        fresh.close()

    # the restart drain is idempotent: no duplicate lines, empty ledger
    again = ActiveSelectionStore(library)
    try:
        assert [event["generation"]
                for event in _activation_events(library)] == [1, 2, 3]
        assert again.pending_activation_audit_count() == 0
    finally:
        again.close()


def test_lkg_checkpoint_and_degraded_library_journal_nothing(
        tmp_path):
    library = PackLibrary(tmp_path / "library")
    library.install(REF_PACK)
    try:
        switcher = RuntimeSwitcher(library, ActiveSelectionStore(library))
        assert _swap_and_commit(switcher)
        assert len(_activation_events(library)) == 1
        assert switcher.checkpoint_active_health()
        assert len(_activation_events(library)) == 1  # LKG is not an activation

        from retirement_pet.lifecycle import PackLibrary as _PL
        bootstrap = _PL.bootstrap(tmp_path / "library")
        try:
            assert bootstrap.degraded
            assert not bootstrap.record_activation_committed(
                publisher_id="p", package_id="c", package_version="1.0.0",
                content_digest="0" * 64, character_fqid="p.c.1.0.0.x",
                generation=1, commit_sequence=1)
        finally:
            bootstrap.close()
    finally:
        library.close()


def test_legacy_commit_candidate_journals_too(library):
    switcher = RuntimeSwitcher(library, ActiveSelectionStore(library))
    revision = next(r.revision_key for r in library.list_revisions()
                    if r.revision_key.pack.package_id == PACKAGE_ID)
    request = switcher.request(revision, CHARACTER)
    candidate = switcher.prepare(request)
    assert candidate is not None
    assert switcher.commit_candidate(candidate)
    events = _activation_events(library)
    assert len(events) == 1
    assert events[0]["character_fqid"].endswith(".demo")
    assert events[0]["recovered"] is False
