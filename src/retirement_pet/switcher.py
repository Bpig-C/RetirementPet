"""Character catalog, selection store and the PetSurface switch transaction.

P0 remediation (B/C/D/A) — the frozen protocol:

REQUEST     a switch request allocates a monotonic ``request_generation``
            token; older requests become stale the moment a newer one exists.

PREPARE     validate the Revision (READY + trust channel), build the
            candidate Runtime, verify its offscreen core.idle FIRST FRAME
            using the candidate itself.  No window write, no selection
            write; a failed prepare leaves the visible character untouched.

SWAP        at a safe frame boundary the surface swaps its renderer to the
            candidate, then the NEW renderer must complete a synchronous
            first-frame paint through the surface.  Only then:

COMMIT      ActiveSelection is persisted with CAS semantics — a commit whose
            generation is not newer than the stored one is REJECTED (real
            latest-wins: A may finish after B, but can never overwrite B).

Any failure (stale token, swap error, first frame error, CAS loss, DB
error) restores the OLD renderer, keeps the OLD selection and releases the
failed candidate, logging a structured error.  Because a UI frame swap and
a SQLite commit cannot form one cross-system atomic transaction, the crash
recovery protocol is: crash before commit → the persisted (old) selection
wins and the restarted surface renders it again; crash after commit → the
new selection wins.  Both boundaries are covered by real-kill tests.

Old runtimes are dropped at commit (single GUI thread = no in-flight paint).
The pixmap budget is GLOBAL, while every runtime has a distinct owner
namespace so confirmed old and rejected candidates can be released without
touching the authoritative renderer.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from retirement_pet.lifecycle import LifecycleError, PackLibrary
from retirement_pet.petpack.archive import PetpackArchive
from retirement_pet.petpack.identity import PackKey, RevisionKey
from retirement_pet.petpack.manifest import parse_manifest
from retirement_pet.petpack.runtime import (
    PackCharacterRuntime,
    render_first_frame_offscreen,
)

logger = logging.getLogger(__name__)


# -- catalog -----------------------------------------------------------------------

@dataclass(frozen=True)
class CatalogEntry:
    publisher_id: str
    package_id: str
    package_version: str
    content_digest: str
    series_id: str
    character_id: str
    display_name: str
    trust_channel: str
    builtin: bool
    revision_key: object


class CharacterCatalog:
    """series -> characters view over installed revisions."""

    def __init__(self, library: PackLibrary):
        self._library = library

    def entries(self) -> list[CatalogEntry]:
        # The immutable v1.0.0 row remains a catalog fact and an exact restore
        # target.  Once its validated v1.0.1 successor is READY, presenting
        # both built-in cats to users would be a duplicate rather than useful
        # history, so only the precise retired built-in entry is hidden.
        from retirement_pet.embedded_pack import (
            LEGACY_CONTENT_DIGEST,
            LEGACY_PACKAGE_VERSION,
            builtin_official_revision,
        )

        current_builtin = builtin_official_revision(self._library)
        out: list[CatalogEntry] = []
        for record in self._library.list_revisions():
            if (
                current_builtin is not None
                and record.builtin
                and record.trust_channel == "BUILTIN_OFFICIAL"
                and record.revision_key.pack.publisher_id == "official"
                and record.revision_key.pack.package_id
                == "retirement-cat-official"
                and record.revision_key.package_version
                == LEGACY_PACKAGE_VERSION
                and record.revision_key.content_digest
                == LEGACY_CONTENT_DIGEST
            ):
                continue
            path = record.pack_path
            if not path.is_file():
                continue  # trash pending / removed externally: not READY
            try:
                _archive, manifest = load_verified(
                    path, record.revision_key,
                    trust_channel=record.trust_channel)
            except (OSError, LifecycleError):
                continue
            series_id = str(manifest["series"]["id"])
            for character in manifest.get("characters", []):
                display = character.get("display_name", {})
                name = next(iter(display.values())) if display else \
                    str(character.get("id", ""))
                out.append(CatalogEntry(
                    publisher_id=record.revision_key.pack.publisher_id,
                    package_id=record.revision_key.pack.package_id,
                    package_version=record.revision_key.package_version,
                    content_digest=record.revision_key.content_digest,
                    series_id=series_id,
                    character_id=str(character.get("id", "")),
                    display_name=name,
                    trust_channel=record.trust_channel,
                    builtin=record.builtin,
                    revision_key=record.revision_key,
                ))
        return out

    def grouped(self) -> dict[tuple[str, str], list[CatalogEntry]]:
        """(publisher, series) -> entries, the UI browsing hierarchy."""
        groups: dict[tuple[str, str], list[CatalogEntry]] = {}
        for entry in self.entries():
            groups.setdefault((entry.publisher_id, entry.series_id),
                              []).append(entry)
        return groups


# -- active selection ----------------------------------------------------------------

@dataclass(frozen=True)
class ActiveSelection:
    publisher_id: str
    package_id: str
    package_version: str
    content_digest: str
    character_fqid: str
    variant_id: str | None
    config_revision_id: str | None
    generation: int
    commit_sequence: int

    def revision_key(self) -> RevisionKey:
        return RevisionKey(
            pack=PackKey(self.publisher_id, self.package_id),
            package_version=self.package_version,
            content_digest=self.content_digest)


class ActiveSelectionStore:
    """One atomic ACTIVE row + one LAST-KNOWN-GOOD row in SQLite.

    ``commit_if_newer`` implements latest-wins via CAS on generation: a
    stale racing writer can never overwrite a newer committed selection.
    """

    def __init__(self, library: PackLibrary):
        self._library = library
        self._db: sqlite3.Connection | None = None
        try:
            if not library.degraded:
                library._preflight_existing_database()
            self._connect()
            self._reconcile_activation_journal()
        except Exception:
            self.close()
            raise

    def _connect(self) -> None:
        target: str | Path = (":memory:" if self._library.degraded else
                              self._library.root / "state.db")
        self._db = sqlite3.connect(target)
        if not self._library.degraded:
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS active_selection ("
            " slot TEXT PRIMARY KEY,"
            " publisher_id TEXT NOT NULL, package_id TEXT NOT NULL,"
            " package_version TEXT NOT NULL, content_digest TEXT NOT NULL,"
            " character_fqid TEXT NOT NULL,"
            " variant_id TEXT, config_revision_id TEXT,"
            " generation INTEGER NOT NULL, commit_sequence INTEGER NOT NULL)")
        # Durable pending activation facts (CR-P03): written in the SAME
        # transaction as every confirmed ACTIVE write and deleted only after
        # the events.jsonl line is proven, so an audit-write failure can be
        # replayed from facts instead of being inferred from the one ACTIVE
        # slot that survived.
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS activation_audit_due ("
            " publisher_id TEXT NOT NULL, package_id TEXT NOT NULL,"
            " package_version TEXT NOT NULL, content_digest TEXT NOT NULL,"
            " character_fqid TEXT NOT NULL,"
            " generation INTEGER NOT NULL, commit_sequence INTEGER NOT NULL,"
            " created_at TEXT NOT NULL,"
            " UNIQUE (publisher_id, package_id, package_version,"
            "         content_digest, generation, commit_sequence))")
        self._db.commit()

    def reconnect(self) -> None:
        """Reconnect after an indeterminate write and restore durability PRAGMAs."""
        try:
            self.close()
        finally:
            if not self._library.degraded:
                self._library._preflight_existing_database()
            self._connect()

    def _reconcile_activation_journal(self) -> None:
        """Backfill the append-only activation audit at startup (review P-3).

        Two idempotent, audit-only sources; neither can block startup.
        First the durable pending ledger (CR-P03): every confirmed ACTIVE
        write left a due row until its events.jsonl line was proven, so a
        history whose audit write failed mid-process replays from facts
        even after the ACTIVE slot was overwritten by a newer switch.
        Then the legacy backfill: an ACTIVE row from an older build with
        no due row still gets its line exactly once (``recovered`` set).
        """
        if self._library.degraded or self._db is None:
            return
        try:
            self.drain_activation_audit()
            active = self.get("active")
            if active is None:
                return
            self._library.record_activation_committed(
                publisher_id=active.publisher_id,
                package_id=active.package_id,
                package_version=active.package_version,
                content_digest=active.content_digest,
                character_fqid=active.character_fqid,
                generation=active.generation,
                commit_sequence=active.commit_sequence,
                recovered=True,
            )
        except Exception:  # noqa: BLE001 - audit must never block startup
            logger.exception("activation journal reconciliation failed")

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def get(self, slot: str = "active") -> ActiveSelection | None:
        if slot not in {"active", "last_known_good"}:
            raise ValueError(f"unsupported selection slot: {slot}")
        row = self._db.execute(
            "SELECT publisher_id, package_id, package_version, content_digest,"
            " character_fqid, variant_id, config_revision_id, generation,"
            " commit_sequence FROM active_selection WHERE slot=?",
            (slot,)).fetchone()
        if row is None:
            return None
        PackLibrary._validate_selection_row((slot, *row))
        return ActiveSelection(*row)

    def commit(self, selection: ActiveSelection,
               slot: str = "active") -> bool:
        """Unconditional persist (bootstrap/repair paths only)."""
        self._validate_write(selection, slot)
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO active_selection VALUES"
                " (?,?,?,?,?,?,?,?,?,?)",
                (slot, selection.publisher_id, selection.package_id,
                 selection.package_version, selection.content_digest,
                 selection.character_fqid, selection.variant_id,
                 selection.config_revision_id, selection.generation,
                 selection.commit_sequence))
            if slot == "active":
                self._insert_activation_due(selection)
        return True

    def commit_if_newer(self, selection: ActiveSelection,
                        slot: str = "active") -> bool:
        """CAS persist: rejected when a selection with generation >= ours
        is already stored (a newer request won the race)."""
        self._validate_write(selection, slot)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT generation FROM active_selection WHERE slot=?",
                    (slot,)).fetchone()
                if row is not None and int(row[0]) >= selection.generation:
                    self._db.execute("ROLLBACK")
                    return False
                self._db.execute(
                    "INSERT OR REPLACE INTO active_selection VALUES"
                    " (?,?,?,?,?,?,?,?,?,?)",
                    (slot, selection.publisher_id, selection.package_id,
                     selection.package_version, selection.content_digest,
                     selection.character_fqid, selection.variant_id,
                     selection.config_revision_id, selection.generation,
                     selection.commit_sequence))
                if slot == "active":
                    # The audit fact is durable exactly when the confirmed
                    # selection is (CR-P03): one transaction, no window in
                    # which a commit exists without a recoverable audit due.
                    self._insert_activation_due(selection)
                self._db.execute("COMMIT")
                return True
            except sqlite3.Error:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def _insert_activation_due(self, selection: ActiveSelection) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO activation_audit_due VALUES"
            " (?,?,?,?,?,?,?,?)",
            (selection.publisher_id, selection.package_id,
             selection.package_version, selection.content_digest,
             selection.character_fqid, selection.generation,
             selection.commit_sequence,
             datetime.now(timezone.utc).isoformat(timespec="seconds")))

    def drain_activation_audit(self, *,
                               skip: ActiveSelection | None = None) -> int:
        """Retry every durable pending activation fact (CR-P03).

        Facts are appended to events.jsonl in commit order; a fact whose
        journal line is proven (appended now, or already present) is
        removed from the ledger.  The first fact the journal still cannot
        take stops the drain and stays durable for the next confirmed
        commit or the next startup.  ``skip`` excludes one selection that
        the caller journals inline.  Returns the number of facts retired.
        """
        if self._library.degraded or self._db is None:
            return 0
        rows = self._db.execute(
            "SELECT rowid, publisher_id, package_id, package_version,"
            " content_digest, character_fqid, generation, commit_sequence"
            " FROM activation_audit_due ORDER BY rowid").fetchall()
        drained = 0
        for row in rows:
            if skip is not None and tuple(row[1:]) == (
                    skip.publisher_id, skip.package_id,
                    skip.package_version, skip.content_digest,
                    skip.character_fqid, skip.generation,
                    skip.commit_sequence):
                continue
            proven = self._library.record_activation_committed(
                publisher_id=row[1], package_id=row[2],
                package_version=row[3], content_digest=row[4],
                character_fqid=row[5], generation=int(row[6]),
                commit_sequence=int(row[7]), recovered=True)
            if not proven:
                break
            self._db.execute(
                "DELETE FROM activation_audit_due WHERE rowid=?", (row[0],))
            self._db.commit()
            drained += 1
        return drained

    def clear_activation_audit_due(self, selection: ActiveSelection) -> None:
        """Retire the durable fact whose journal line was proven inline."""
        self._db.execute(
            "DELETE FROM activation_audit_due WHERE publisher_id=? AND"
            " package_id=? AND package_version=? AND content_digest=? AND"
            " generation=? AND commit_sequence=?",
            (selection.publisher_id, selection.package_id,
             selection.package_version, selection.content_digest,
             selection.generation, selection.commit_sequence))
        self._db.commit()

    def pending_activation_audit_count(self) -> int:
        if self._db is None:
            return 0
        return int(self._db.execute(
            "SELECT COUNT(*) FROM activation_audit_due").fetchone()[0])

    @staticmethod
    def _validate_write(selection: ActiveSelection, slot: str) -> None:
        PackLibrary._validate_selection_row((
            slot, selection.publisher_id, selection.package_id,
            selection.package_version, selection.content_digest,
            selection.character_fqid, selection.variant_id,
            selection.config_revision_id, selection.generation,
            selection.commit_sequence,
        ))


# -- switch surface --------------------------------------------------------------------

class SwitchSurface(Protocol):
    """What the switcher needs from the pet window (PetWindow implements it)."""

    @property
    def renderer(self): ...

    def swap_renderer(self, renderer) -> None: ...

    def paint_first_frame(self, renderer) -> bool: ...


class FakeSurface:
    """Test surface honoring the same protocol."""

    def __init__(self, fail_first_frame=False):
        self.renderer = None
        self.fail_first_frame = fail_first_frame
        self.swaps = 0

    def swap_renderer(self, renderer) -> None:
        self.renderer = renderer
        self.swaps += 1

    def paint_first_frame(self, renderer) -> bool:
        return not self.fail_first_frame


# -- runtime switcher ------------------------------------------------------------------

@dataclass
class SwitchRequest:
    request_id: int
    revision_key: RevisionKey
    character_fqid: str
    variant_id: str | None = None
    config_revision_id: str | None = None
    candidate: PackCharacterRuntime | None = None


@dataclass(frozen=True)
class SwitchError:
    code: str
    phase: str
    detail: str = ""


class _NullSafeRenderer:
    """Last-resort transparent renderer for non-UI switcher consumers."""

    def render(self, *_args, **_kwargs) -> None:
        return None


class RuntimeSwitcher:
    """REQUEST → PREPARE → SWAP → COMMIT transaction (latest-wins)."""

    def __init__(self, library: PackLibrary, selection_store: ActiveSelectionStore,
                 asset_cache=None, safe_renderer=None):
        self._library = library
        self._store = selection_store
        self._cache = asset_cache  # GLOBAL budget shared by all runtimes
        slots = [selection for selection in (
            self._store.get("active"), self._store.get("last_known_good"))
                 if selection is not None]
        self.active_generation = max(
            (selection.generation for selection in slots), default=0)
        self.commit_sequence = max(
            (selection.commit_sequence for selection in slots), default=0)
        self.request_generation = self.active_generation
        self.current_runtime: PackCharacterRuntime | None = None
        self.last_error: SwitchError | None = None
        self.retired_count = 0
        self._safe_renderer = (
            safe_renderer if safe_renderer is not None else _NullSafeRenderer())
        self.in_safe_mode = False
        self._retained_renderers: list[object] = []
        self._prepared_candidates: dict[int, PackCharacterRuntime] = {}

    @property
    def retained_renderers(self) -> tuple[object, ...]:
        return tuple(self._retained_renderers)

    # -- REQUEST ------------------------------------------------------------

    def request(self, revision_key: RevisionKey, character_fqid: str,
                *, variant_id: str | None = None,
                config_revision_id: str | None = None) -> SwitchRequest:
        # Every prior prepared request becomes stale at this instant.  Drop
        # its private cache owner even if its caller never returns to commit.
        for runtime in self._prepared_candidates.values():
            if runtime is not self.current_runtime \
                    and all(runtime is not held
                            for held in self._retained_renderers):
                self._release_runtime_cache(runtime)
        self._prepared_candidates.clear()
        self.request_generation += 1
        return SwitchRequest(
            request_id=self.request_generation, revision_key=revision_key,
            character_fqid=character_fqid, variant_id=variant_id,
            config_revision_id=config_revision_id)

    def _is_latest(self, request: SwitchRequest) -> bool:
        return request.request_id == self.request_generation

    # -- PREPARE --------------------------------------------------------------

    def prepare(
            self, request: SwitchRequest, *, authority_recovery: bool = False
            ) -> PackCharacterRuntime | None:
        """Validate + build + offscreen-first-frame the candidate runtime."""
        from PySide6.QtWidgets import QApplication

        if request.candidate is not None:
            self._release_candidate(request)
        if self.in_safe_mode and not authority_recovery:
            self._reject(request, "PPK-LCY-E003", "prepare",
                         "safe mode requires a fresh authority recovery")
            return None
        if not self._is_latest(request):
            self._reject(request, "PPK-LCY-W002", "prepare",
                         f"stale request {request.request_id} < "
                         f"{self.request_generation}")
            return None
        QApplication.instance() or QApplication([])  # pixmap decode needs it
        record = self._library.get_revision(request.revision_key)
        if record is None or not record.pack_path.is_file():
            self._reject(request, "PPK-LCY-E004", "prepare",
                         "revision not READY")
            return None
        character_id = request.character_fqid.rsplit(".", 1)[-1]
        try:
            archive, manifest = load_verified(
                record.pack_path, request.revision_key,
                trust_channel=record.trust_channel)
            runtime = PackCharacterRuntime(
                archive, manifest, character_id,
                content_digest=request.revision_key.content_digest,
                asset_cache=self._cache)
        except (KeyError, StopIteration, ValueError, OSError, LifecycleError):
            self._reject(request, "PPK-LCY-E004", "prepare",
                         "candidate build failed", log_exc=True)
            return None
        # A persisted selection is an exact tuple.  Bare ids remain accepted
        # for interactive catalog requests, but a supplied FQID must match the
        # runtime-derived identity byte-for-byte.
        if "." in request.character_fqid \
                and request.character_fqid != runtime.character_fqid():
            runtime.release_cache()
            self._reject(request, "PPK-LCY-E004", "prepare",
                         "character FQID does not match revision manifest")
            return None
        # Variant overlays are reserved by PetPack 1.0 but are not rendered
        # by this runtime yet.  Persisting one would claim false exactness.
        if request.variant_id is not None:
            runtime.release_cache()
            self._reject(request, "PPK-LCY-E004", "prepare",
                         "variant overlays are not implemented")
            return None
        # No config-revision registry exists in v1; accepting a non-null id
        # would silently restore a different configuration than was selected.
        if request.config_revision_id is not None:
            runtime.release_cache()
            self._reject(request, "PPK-LCY-E004", "prepare",
                         "config revision is not available")
            return None
        try:
            candidate_first_frame = runtime.first_frame_image()
        except Exception:  # noqa: BLE001 - decoder/renderer boundary
            candidate_first_frame = None
        if not candidate_first_frame:
            runtime.release_cache()
            self._reject(request, "PPK-LCY-E004", "prepare",
                         "candidate first frame not verifiable")
            return None
        runtime.variant_id = request.variant_id
        runtime.config_revision_id = request.config_revision_id
        request.candidate = runtime
        self._prepared_candidates[request.request_id] = runtime
        return runtime

    # -- SWAP + COMMIT ----------------------------------------------------------

    def swap_and_commit(self, request: SwitchRequest,
                        surface: SwitchSurface) -> bool:
        """Frame-boundary swap, synchronous first frame, then CAS commit.

        Returns True only when surface.renderer, ActiveSelection and the
        library agree on the new character.  Every failure path restores
        the previous renderer and selection exactly.
        """
        candidate = request.candidate
        if candidate is None:
            self._reject(request, "PPK-LCY-E004", "commit",
                         "commit without a prepared candidate")
            return False
        if not self._is_latest(request):
            # stale: a newer request exists; release the candidate, keep
            # whatever the newer flow decides
            self._reject(request, "PPK-LCY-W002", "commit",
                         f"stale request {request.request_id} < "
                         f"{self.request_generation}")
            self._release_candidate(request)
            return False

        old_renderer = surface.renderer
        try:
            previous_selection = self._store.get("active")
        except (sqlite3.Error, LifecycleError) as exc:
            # No selection write has started, so this candidate cannot be an
            # unknown authority and must not become a retained lease.
            self._enter_safe_mode(surface, old_renderer, None)
            self._release_candidate(request)
            self._reject(request, "PPK-LCY-E003", "commit",
                         f"pre-commit read failed: {type(exc).__name__}")
            return False
        try:
            surface.swap_renderer(candidate)          # frame boundary
            if not surface.paint_first_frame(candidate):
                raise RuntimeError("surface first frame failed")
        except Exception as exc:  # noqa: BLE001 - renderer is untrusted media
            logger.exception("surface swap failed; rolling back renderer")
            self._reject(request, "PPK-LCY-E006", "swap", str(exc)[:120])
            surface.swap_renderer(old_renderer)
            self._release_candidate(request)
            return False

        selection = _selection_of(candidate, self.active_generation + 1,
                                  self.commit_sequence + 1,
                                  getattr(candidate, "variant_id", None))
        try:
            won = self._store.commit_if_newer(selection)
        except sqlite3.Error:
            logger.exception("selection commit INDETERMINATE; reading back")
            try:
                self._store.reconnect()
                authoritative = self._store.get("active")
            except (sqlite3.Error, LifecycleError):
                logger.exception("selection readback failed after reconnect")
                self._enter_safe_mode(surface, old_renderer, candidate)
                self._forget_candidate(request)
                self._reject(request, "PPK-LCY-E003", "commit",
                             "INDETERMINATE readback unavailable")
                return False
            if authoritative == selection:
                self._adopt_candidate(
                    candidate, old_renderer, selection, request)
                return True
            if authoritative is None:
                if previous_selection is None:
                    self._restore_empty_selection(
                        surface, old_renderer, candidate)
                else:
                    self._enter_safe_mode(surface, old_renderer, candidate)
            elif not self._render_authoritative(
                    surface, authoritative, old_renderer, candidate):
                self._enter_safe_mode(surface, old_renderer, candidate)
            self._forget_candidate(request)
            self._reject(request, "PPK-LCY-E003", "commit",
                         "INDETERMINATE resolved as not-candidate")
            return False
        if not won:
            try:
                authoritative = self._store.get("active")
            except (sqlite3.Error, LifecycleError):
                logger.exception("CAS loser authoritative read failed")
                self._enter_safe_mode(surface, old_renderer, candidate)
                self._forget_candidate(request)
                self._reject(request, "PPK-LCY-E003", "commit",
                             "CAS loser readback unavailable")
                return False
            if authoritative is None or not self._render_authoritative(
                    surface, authoritative, old_renderer, candidate):
                self._enter_safe_mode(surface, old_renderer, candidate)
            self._forget_candidate(request)
            self._reject(request, "PPK-LCY-W002", "commit",
                         "CAS rejected: a newer selection is committed")
            return False

        self._adopt_candidate(candidate, old_renderer, selection, request)
        return True

    def _adopt_candidate(
            self, candidate: PackCharacterRuntime, old_renderer,
            selection: ActiveSelection, request: SwitchRequest) -> None:
        """Adopt a fully confirmed ACTIVE write; LKG is not promoted here."""
        self.active_generation = selection.generation
        self.commit_sequence = selection.commit_sequence
        self._release_runtime_caches_except(candidate, old_renderer)
        self.current_runtime = candidate
        if old_renderer is not None and old_renderer is not candidate \
                and isinstance(old_renderer, PackCharacterRuntime):
            self.retired_count += 1
        self._forget_candidate(request)
        self.in_safe_mode = False
        self.last_error = None
        self._record_activation(selection)

    def _record_activation(self, selection: ActiveSelection) -> None:
        """Journal the audit line AFTER a confirmed ACTIVE write (P-3).

        The durable due-row for this selection was written in the same
        transaction as the ACTIVE row (CR-P03), so a failed audit write
        can never lose the fact: older pending facts retry first to keep
        the journal in commit order, then this selection's line is
        attempted inline and its due row retired only on proof.  Any
        failure keeps the facts pending for the next confirmed commit or
        the next startup - auditing can neither fail a switch nor fake
        one, and never reverses a confirmed selection.
        """
        if self._library.degraded:
            return
        try:
            self._store.drain_activation_audit(skip=selection)
        except Exception:  # noqa: BLE001 - audit must never fail a switch
            logger.exception("pending activation audit retry failed")
        try:
            proven = self._library.record_activation_committed(
                publisher_id=selection.publisher_id,
                package_id=selection.package_id,
                package_version=selection.package_version,
                content_digest=selection.content_digest,
                character_fqid=selection.character_fqid,
                generation=selection.generation,
                commit_sequence=selection.commit_sequence,
            )
        except Exception:  # noqa: BLE001 - audit must never fail a switch
            logger.exception("ACTIVATE_COMMITTED audit hook failed")
            return
        if proven:
            try:
                self._store.clear_activation_audit_due(selection)
            except Exception:  # noqa: BLE001 - cleanup stays best-effort
                logger.exception("activation audit due-row cleanup failed")

    # -- helpers -------------------------------------------------------------

    def _reject(self, request: SwitchRequest, code: str, phase: str,
                detail: str, *, log_exc: bool = False) -> None:
        self.last_error = SwitchError(code, phase, detail)
        logger.warning("switch rejected [%s/%s] request=%s: %s",
                       code, phase, request.request_id, detail,
                       exc_info=log_exc)

    @staticmethod
    def _release_runtime_cache(renderer) -> None:
        if isinstance(renderer, PackCharacterRuntime):
            renderer.release_cache()

    def _release_candidate(self, request: SwitchRequest) -> None:
        tracked = self._prepared_candidates.pop(request.request_id, None)
        self._release_runtime_cache(request.candidate or tracked)
        request.candidate = None

    def _forget_candidate(self, request: SwitchRequest) -> None:
        self._prepared_candidates.pop(request.request_id, None)
        request.candidate = None

    def _forget_runtime_candidate(self, runtime) -> None:
        for request_id, tracked in list(self._prepared_candidates.items()):
            if tracked is runtime:
                self._prepared_candidates.pop(request_id, None)

    def _release_runtime_caches_except(self, keep, *renderers) -> None:
        """Release every proven non-authoritative runtime owner exactly once."""
        candidates = (
            *renderers, self.current_runtime, *self._retained_renderers,
            *self._prepared_candidates.values())
        seen: set[int] = set()
        for renderer in candidates:
            if renderer is keep or id(renderer) in seen:
                continue
            seen.add(id(renderer))
            self._release_runtime_cache(renderer)
        self._retained_renderers.clear()
        self._prepared_candidates.clear()

    def _render_authoritative(
            self, surface: SwitchSurface,
            authoritative: ActiveSelection,
            old_renderer, candidate) -> bool:
        """Render a proven persisted selection, including a third-party winner."""
        target = next(
            (runtime for runtime in (candidate, old_renderer,
                                     self.current_runtime)
             if self._runtime_matches_selection(runtime, authoritative)),
            None,
        )
        if target is None:
            recovery = SwitchRequest(
                request_id=self.request_generation,
                revision_key=authoritative.revision_key(),
                character_fqid=authoritative.character_fqid,
                variant_id=authoritative.variant_id,
                config_revision_id=authoritative.config_revision_id,
            )
            target = self.prepare(recovery, authority_recovery=True)
            self._forget_candidate(recovery)
        if target is None:
            logger.error("authoritative selection cannot be rendered: %s",
                         authoritative.character_fqid)
            return False
        try:
            surface.swap_renderer(target)
            if not surface.paint_first_frame(target):
                raise RuntimeError("authoritative first-frame paint failed")
        except Exception:  # noqa: BLE001 - surface/renderer are trust boundaries
            logger.exception("authoritative surface render failed: %s",
                             authoritative.character_fqid)
            self._retain_renderers(target)
            return False
        self._release_runtime_caches_except(
            target, old_renderer, candidate)
        self.current_runtime = target
        self.active_generation = authoritative.generation
        self.commit_sequence = authoritative.commit_sequence
        self.in_safe_mode = False
        self._retained_renderers.clear()
        return True

    def _restore_empty_selection(self, surface: SwitchSurface,
                                 old_renderer, candidate) -> None:
        """A successful read proved that neither old nor candidate is ACTIVE."""
        surface.swap_renderer(old_renderer)
        self.current_runtime = (
            old_renderer if isinstance(old_renderer, PackCharacterRuntime)
            else None)
        self.in_safe_mode = False
        self._release_runtime_caches_except(old_renderer, candidate)

    def enter_safe_mode(self, surface: SwitchSurface) -> None:
        """Fail closed to the application-supplied Bootstrap renderer."""
        self._enter_safe_mode(
            surface, surface.renderer, self.current_runtime)

    def _enter_safe_mode(self, surface: SwitchSurface,
                         old_renderer, candidate) -> None:
        """Show the supplied Bootstrap renderer when authority is unknowable.

        Both pack runtimes remain strongly referenced until shutdown or a
        later proven recovery; the engine never guesses which one is ACTIVE.
        """
        self._retain_renderers(old_renderer, candidate)
        try:
            surface.swap_renderer(self._safe_renderer)
        except Exception:  # noqa: BLE001 - safe-mode transition must not escape
            logger.exception("Bootstrap renderer swap failed")
        self.current_runtime = None
        self.in_safe_mode = True

    def _retain_renderers(self, *renderers) -> None:
        """Hold every unconfirmed pack lease until recovery or shutdown."""
        for renderer in renderers:
            if renderer is not None and renderer is not self._safe_renderer \
                    and all(renderer is not held
                            for held in self._retained_renderers):
                self._retained_renderers.append(renderer)

    @staticmethod
    def _runtime_matches_selection(runtime,
                                   selection: ActiveSelection) -> bool:
        if not isinstance(runtime, PackCharacterRuntime):
            return False
        try:
            projected = _selection_of(
                runtime, selection.generation, selection.commit_sequence,
                getattr(runtime, "variant_id", None))
        except (AttributeError, KeyError, TypeError):
            return False
        return projected == selection

    def shutdown(self) -> None:
        """Explicit teardown (P0-D): drop runtimes and clear the shared cache."""
        self._release_runtime_caches_except(None)
        self.current_runtime = None
        self.in_safe_mode = False
        if self._cache is not None:
            self._cache.clear()

    def restore_selection(self, selection: ActiveSelection,
                          surface: SwitchSurface) -> bool:
        """Render an exact persisted tuple without writing ACTIVE or LKG."""
        request = self.request(
            selection.revision_key(), selection.character_fqid,
            variant_id=selection.variant_id,
            config_revision_id=selection.config_revision_id)
        candidate = self.prepare(request, authority_recovery=True)
        if candidate is None or not self._runtime_matches_selection(
                candidate, selection):
            self._release_candidate(request)
            return False
        old_renderer = surface.renderer
        try:
            surface.swap_renderer(candidate)
            if not surface.paint_first_frame(candidate):
                raise RuntimeError("surface first frame failed")
        except Exception as exc:  # noqa: BLE001
            logger.exception("persisted selection restore failed")
            surface.swap_renderer(old_renderer)
            self._release_candidate(request)
            self._reject(request, "PPK-LCY-E006", "restore", str(exc)[:120])
            return False
        self._release_runtime_caches_except(candidate, old_renderer)
        self.current_runtime = candidate
        self._forget_candidate(request)
        self.in_safe_mode = False
        self.last_error = None
        return True

    def repair_active_from_current(self, surface: SwitchSurface) -> bool:
        """Persist the currently restored LKG as a new high-water ACTIVE."""
        candidate = self.current_runtime
        if candidate is None or self.in_safe_mode:
            return False
        try:
            previous = self._store.get("active")
        except (sqlite3.Error, LifecycleError):
            self._enter_safe_mode(surface, surface.renderer, candidate)
            return False
        self.request_generation += 1
        request = SwitchRequest(
            request_id=self.request_generation,
            revision_key=_revision_key_of(candidate),
            character_fqid=candidate.character_fqid(),
            variant_id=getattr(candidate, "variant_id", None),
            config_revision_id=getattr(candidate, "config_revision_id", None),
            candidate=candidate,
        )
        proposed = _selection_of(
            candidate, self.active_generation + 1,
            self.commit_sequence + 1,
            getattr(candidate, "variant_id", None))
        try:
            won = self._store.commit_if_newer(proposed)
        except sqlite3.Error:
            self._enter_safe_mode(surface, surface.renderer, candidate)
            return False
        if not won:
            try:
                authoritative = self._store.get("active")
            except (sqlite3.Error, LifecycleError):
                self._enter_safe_mode(surface, surface.renderer, candidate)
                return False
            if authoritative is None or not self._render_authoritative(
                    surface, authoritative, surface.renderer, candidate):
                self._enter_safe_mode(surface, surface.renderer, candidate)
            return False
        self._adopt_candidate(candidate, surface.renderer, proposed, request)
        return True

    def checkpoint_active_health(
            self, expected_selection: ActiveSelection | None = None) -> bool:
        """Promote exact ACTIVE after observing an optional frozen tuple."""
        runtime = self.current_runtime
        if runtime is None or self.in_safe_mode:
            return False
        try:
            active = self._store.get("active")
            if (expected_selection is not None
                    and active != expected_selection):
                return False
            if active is None or not self._runtime_matches_selection(
                    runtime, active):
                return False
            record = self._library.get_revision(active.revision_key())
            if record is None or not record.pack_path.is_file():
                return False
            load_verified(
                record.pack_path, active.revision_key(),
                trust_channel=record.trust_channel)
            self._store.commit(active, slot="last_known_good")
        except (OSError, sqlite3.Error, LifecycleError):
            logger.exception("ACTIVE health checkpoint failed")
            return False
        return True

    # -- compatibility shims (used by app + older tests) ----------------------

    def prepare_legacy(self, revision_key: RevisionKey, character_id: str,
                       *, variant_id: str | None = None):
        request = self.request(revision_key, character_id)
        return self.prepare(request)

    def commit_candidate(self, candidate) -> bool:
        """Legacy path without a surface: commit only (used where no window
        exists, e.g. library-level tests).  Kept for API stability."""
        if candidate is None:
            return False
        selection = _selection_of(candidate, self.active_generation + 1,
                                  self.commit_sequence + 1,
                                  getattr(candidate, "variant_id", None))
        if not self._store.commit_if_newer(selection):
            self._forget_runtime_candidate(candidate)
            self._release_runtime_cache(candidate)
            return False
        self.active_generation = selection.generation
        self.commit_sequence = selection.commit_sequence
        self._record_activation(selection)
        self._release_runtime_caches_except(candidate)
        self.current_runtime = candidate
        self.in_safe_mode = False
        return True


def _revision_key_of(runtime: PackCharacterRuntime) -> RevisionKey:
    manifest = runtime._manifest
    package = manifest["package"]
    return RevisionKey(
        pack=PackKey(package["publisher_id"], package["id"]),
        package_version=package["version"],
        content_digest=runtime.content_digest())


def _selection_of(runtime: PackCharacterRuntime, generation: int,
                  commit_sequence: int, variant_id) -> ActiveSelection:
    rk = _revision_key_of(runtime)
    return ActiveSelection(
        publisher_id=rk.pack.publisher_id, package_id=rk.pack.package_id,
        package_version=rk.package_version, content_digest=rk.content_digest,
        character_fqid=runtime.character_fqid(), variant_id=variant_id,
        config_revision_id=getattr(runtime, "config_revision_id", None),
        generation=generation,
        commit_sequence=commit_sequence)


# -- helpers ------------------------------------------------------------------------

def load_verified(pack_path: Path, expected_revision_key: RevisionKey,
                  trust_channel: str = "LOCAL_IMPORTED"
                  ) -> tuple[PetpackArchive, dict]:
    from retirement_pet.petpack.validator import validate_petpack

    data = pack_path.read_bytes()
    report = validate_petpack(data, trust_channel=trust_channel)
    if not report.accepted or report.revision_key != expected_revision_key:
        raise LifecycleError("PPK-LCY-E001", f"revision not READY: {pack_path}")
    archive = PetpackArchive(data)
    manifest = parse_manifest(archive.manifest_bytes)
    return archive, manifest


def first_character_id(library: PackLibrary, record) -> str:
    archive, manifest = load_verified(record.pack_path, record.revision_key,
                                      trust_channel=record.trust_channel)
    return str(manifest["characters"][0]["id"])


def uninstall_with_safe_switch(library: PackLibrary,
                               switcher: RuntimeSwitcher,
                               revision_key: RevisionKey,
                               surface: SwitchSurface | None = None) -> bool:
    """UNINSTALL_REVISION for any revision, including the ACTIVE one.

    The active selection is switched away FIRST through the SAME
    request→prepare→swap→commit transaction as a user switch; the physical
    uninstall starts only after that commit is durable.  Safe targets in
    order: another installed revision, then the builtin official pack.
    There is no fake "engine-safe-cat" selection anymore (P0-A).
    """
    active = switcher._store.get("active")
    active_key = active.revision_key() if active else None

    if active_key is not None and active_key == revision_key:
        alternative = next(
            (r for r in library.list_revisions()
             if r.revision_key != revision_key), None)
        if alternative is not None:
            target, character = (alternative.revision_key,
                                 first_character_id(library, alternative))
        else:
            from retirement_pet.embedded_pack import builtin_official_revision

            builtin = builtin_official_revision(library)
            if builtin is None:
                raise LifecycleError(
                    "PPK-LCY-E006",
                    "no safe switch target: builtin official pack missing")
            target, character = builtin.revision_key, \
                first_character_id(library, builtin)
        if surface is not None:
            request = switcher.request(target, character)
            candidate = switcher.prepare(request)
            if candidate is None or not switcher.swap_and_commit(request, surface):
                raise LifecycleError("PPK-LCY-E006",
                                     "active character safe-switch failed")
        else:
            request = switcher.request(target, character)
            candidate = switcher.prepare(request)
            if candidate is None or not switcher.commit_candidate(candidate):
                raise LifecycleError("PPK-LCY-E006",
                                     "active character safe-switch failed")

    return library.uninstall_revision(
        revision_key, active_guard=lambda rk: (
            switcher._store.get("active") is not None
            and switcher._store.get("active").revision_key() == rk))
