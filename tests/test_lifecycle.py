"""M4 lifecycle: immutable install, idempotency, digest conflict, recovery.

Crash points are exercised by interrupting the process mid-install and
running recovery in a NEW PackLibrary instance (CONFORMANCE 6: real
restart, not an in-process function call).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from retirement_pet.lifecycle import LCY_E007_DIGEST_CONFLICT, LifecycleError, PackLibrary
from retirement_pet.petpack.identity import PackKey, RevisionKey
from retirement_pet.petpack.validator import validate_petpack

REF_PACK = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / \
    "petpack" / "minimal-static.petpack"
ROOT = Path(__file__).resolve().parent.parent
OFFICIAL_PACK = ROOT / "assets" / "petpack" / \
    "retirement-cat-official.petpack"
REALISTIC_PACK = ROOT / "assets" / "petpack" / "examples" / \
    "realistic-retirement-cat-0.1.1.petpack"


@pytest.fixture()
def lib(tmp_path):
    library = PackLibrary(tmp_path / "library")
    yield library
    library.close()


def _make_revision_key(lib: PackLibrary) -> RevisionKey:
    record = lib.list_revisions()[0]
    return record.revision_key


def test_install_registers_ready_and_never_activates(lib):
    record = lib.install(REF_PACK)
    assert record.revision_key.pack.package_id == "minimal-static"
    assert record.trust_channel == "LOCAL_IMPORTED"
    assert lib.list_revisions() == [record]
    # no journal intent remains
    assert not list(lib.journal_dir.glob("intent-*.json"))
    # INSTALL_COMMITTED event appended
    event = json.loads((lib.journal_dir / "events.jsonl").read_text(
        encoding="utf-8").splitlines()[-1])
    assert event["event"] == "INSTALL_COMMITTED"
    receipts = list(lib.receipts_dir.glob("*.json"))
    assert len(receipts) == 1
    receipt_bytes = receipts[0].read_bytes()
    receipt = json.loads(receipt_bytes.decode("utf-8"))
    assert receipt["content_digest"] == record.revision_key.content_digest
    assert receipt["warnings_acknowledged"] == []
    assert event["receipt_id"] == receipt["install_id"]
    assert event["receipt_sha256"] == hashlib.sha256(receipt_bytes).hexdigest()


def test_reinstall_same_digest_is_idempotent(lib):
    first = lib.install(REF_PACK)
    again = lib.install(REF_PACK)
    assert first.revision_key == again.revision_key
    assert len(lib.list_revisions()) == 1
    assert len(list(lib.receipts_dir.glob("*.json"))) == 1


def test_reencoded_zip_with_same_revision_is_idempotent(lib):
    original = REF_PACK.read_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original), "r") as source:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as dest:
            for name in reversed(source.namelist()):
                dest.writestr(name, source.read(name))
    reencoded = output.getvalue()
    assert reencoded != original
    assert validate_petpack(reencoded).revision_key == \
        validate_petpack(original).revision_key

    first = lib.install_bytes(original)
    again = lib.install_bytes(reencoded)

    assert again == first
    assert len(lib.list_revisions()) == 1
    assert len(list(lib.receipts_dir.glob("*.json"))) == 1


def test_install_writes_receipt_before_publish_intent(lib, monkeypatch):
    original = lib._write_intent
    observed = []

    def assert_receipt_first(intent):
        observed.append(len(list(lib.receipts_dir.glob("*.json"))))
        return original(intent)

    monkeypatch.setattr(lib, "_write_intent", assert_receipt_first)
    lib.install(REF_PACK)

    assert observed == [1]


def test_prevalidated_report_cannot_be_mixed_with_another_archive(lib):
    data_a = REF_PACK.read_bytes()
    data_b = REALISTIC_PACK.read_bytes()
    report_a = validate_petpack(data_a, trust_channel="LOCAL_IMPORTED")
    assert report_a.accepted

    with pytest.raises(LifecycleError) as exc:
        lib.install_bytes(
            data_b,
            trust_channel="LOCAL_IMPORTED",
            validation_report=report_a,
            expected_revision=report_a.revision_key,
            expected_archive_sha256=hashlib.sha256(data_b).hexdigest(),
        )

    assert exc.value.code == "PPK-LCY-E001"
    assert lib.list_revisions() == []
    assert not list(lib.receipts_dir.glob("*.json"))
    assert not any(lib.staging_dir.iterdir())


def test_prevalidated_report_cannot_change_trust_channel(lib):
    data = OFFICIAL_PACK.read_bytes()
    report = validate_petpack(data, trust_channel="BUILTIN_OFFICIAL")
    assert report.accepted

    with pytest.raises(LifecycleError) as exc:
        lib.install_bytes(
            data,
            trust_channel="LOCAL_IMPORTED",
            validation_report=report,
            expected_revision=report.revision_key,
            expected_archive_sha256=hashlib.sha256(data).hexdigest(),
        )

    assert exc.value.code == "PPK-LCY-E001"
    assert lib.list_revisions() == []


def test_failed_intent_publish_cleans_staging_without_ready(
        lib, monkeypatch):
    def fail_intent(_intent):
        raise LifecycleError("PPK-LCY-E001", "injected intent failure")

    monkeypatch.setattr(lib, "_write_intent", fail_intent)
    with pytest.raises(LifecycleError):
        lib.install(REF_PACK)

    assert lib.list_revisions() == []
    assert not any(lib.staging_dir.iterdir())
    # A receipt is an immutable validation fact, not a READY claim.
    assert len(list(lib.receipts_dir.glob("*.json"))) == 1


def test_same_version_different_digest_rejected(lib, tmp_path):
    lib.install(REF_PACK)
    # same pack key + version but DIFFERENT content: rebuild with a
    # different (valid) thumbnail so validation passes and the digest moves
    sys.path.insert(0, str(ROOT / "tests"))
    from test_petpack import build_pack, make_manifest, png_bytes

    manifest = make_manifest()
    files = {
        "assets/thumb.png": png_bytes(4, 4, (1, 2, 3, 255)),  # different
        "assets/idle_0.png": png_bytes(8, 8),
    }
    manifest["package"]["publisher_id"] = "community.retirementpet"
    manifest["package"]["id"] = "minimal-static"
    manifest["package"]["version"] = "1.0.0"
    # keep the character action namespace consistent with the new FQID
    manifest["actions"][1]["id"] = (
        "community.retirementpet.minimal-static.sample-series.demo.wave")
    manifest["actions"][1]["semantic"] = manifest["actions"][1]["id"]
    tampered = tmp_path / "tampered.petpack"
    tampered.write_bytes(build_pack(manifest, files))
    with pytest.raises(LifecycleError) as exc:
        lib.install(tampered)
    assert exc.value.code == LCY_E007_DIGEST_CONFLICT
    # the original revision is untouched
    assert len(lib.list_revisions()) == 1


def test_corrupt_pack_is_refused_without_side_effects(lib, tmp_path):
    corrupt = tmp_path / "corrupt.petpack"
    corrupt.write_bytes(b"not a zip")
    with pytest.raises(LifecycleError):
        lib.install(corrupt)
    assert lib.list_revisions() == []
    assert not list(lib.staging_dir.iterdir()) or all(
        p.is_dir() and not any(p.iterdir())
        for p in lib.staging_dir.iterdir())


def test_uninstall_non_active_revision_trashes_it(lib):
    record = lib.install(REF_PACK)
    assert lib.uninstall_revision(record.revision_key) is True
    assert lib.list_revisions() == []
    trashed = list(lib.trash_dir.iterdir())
    assert trashed, "revision must land in trash before physical delete"
    events = (lib.journal_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "UNINSTALL_TRASHED" in events


def test_uninstall_unknown_revision_returns_false(lib):
    rk = RevisionKey(pack=PackKey("a", "b"), package_version="1.0.0",
                     content_digest="0" * 64)
    assert lib.uninstall_revision(rk) is False


def test_receipt_written_once_and_immutable_shape(lib, tmp_path):
    receipt = lib.write_receipt(REF_PACK, trust_channel="LOCAL_IMPORTED")
    assert receipt["publisher_verification_status"] == "UNVERIFIED"
    assert receipt["trust_channel"] == "LOCAL_IMPORTED"
    receipt2 = lib.write_receipt(REF_PACK, trust_channel="LOCAL_IMPORTED")
    assert receipt2["install_id"] != receipt["install_id"]


# -- complementary child self-exit at the rename/commit boundary -------------


def test_child_self_exit_between_rename_and_commit_recovers_consistently(
        tmp_path):
    """Child-side os._exit between PUBLISH and catalog commit recovers.

    This remains useful complementary coverage, but is not parent-driven
    TerminateProcess evidence; that contract lives in test_lifecycle_matrix.
    """
    library_root = tmp_path / "library"
    work = tmp_path / "work"
    work.mkdir(parents=True)

    child = f"""
import sys
sys.path.insert(0, r"{ROOT / 'src'}")
from pathlib import Path
from retirement_pet.lifecycle import PackLibrary

library = PackLibrary(Path(r"{library_root}"))

# Monkeypatch the catalog commit so the child self-exits right after the
# same-volume rename but BEFORE the SQLite INSERT commits.
import os
original = library._commit_install
def crashing_commit(intent, rk):
    target = Path(intent["target"])
    deadline = 0
    while not target.is_file() and deadline < 100:
        import time; time.sleep(0.05); deadline += 1
    sys.stdout.write("RENAME-DONE"); sys.stdout.flush()
    os._exit(9)  # abrupt child loss; nothing after rename survives
library._commit_install = crashing_commit
library.install(Path(r"{REF_PACK}"))
"""
    proc = subprocess.run([sys.executable, "-c", child],
                          capture_output=True, text=True, timeout=60)
    assert "RENAME-DONE" in proc.stdout, "stdout=" + proc.stdout + " stderr=" + proc.stderr

    # a fresh library recovers on construction: the durable intent finishes
    # the install idempotently (rename survived, catalog commit did not)
    crashed_view = PackLibrary(library_root)
    records = crashed_view.list_revisions()
    assert len(records) == 1, "intent replay must complete the install"
    assert records[0].pack_path.is_file()
    assert not list(crashed_view.journal_dir.glob("intent-*.json"))
    crashed_view.close()


def test_newer_schema_is_refused_for_old_app(tmp_path):
    import sqlite3

    db_dir = tmp_path / "library"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(db_dir / "state.db")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO meta VALUES ('schema_version', '99')")
    conn.commit()
    conn.close()

    with pytest.raises(LifecycleError):
        PackLibrary(db_dir)


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_builtin_release_reconciles_legacy_writable_copy(lib, damage):
    """The builtin is executed from release media, never a mutable copy."""
    legacy = lib.register_builtin_release(OFFICIAL_PACK)
    writable_copy = lib._path_of(legacy.revision_key)
    writable_copy.parent.mkdir(parents=True, exist_ok=True)
    writable_copy.write_bytes(OFFICIAL_PACK.read_bytes())
    assert writable_copy != OFFICIAL_PACK.resolve()
    if damage == "missing":
        writable_copy.unlink()
    else:
        writable_copy.write_bytes(b"tampered-writable-cache")
    with lib._db:
        lib._db.execute(
            "UPDATE revisions SET pack_path=?, trust_channel=?, builtin=0"
            " WHERE publisher_id=? AND package_id=? AND package_version=?"
            " AND content_digest=?",
            (str(writable_copy), "LOCAL_IMPORTED",
             *lib._row_of(legacy.revision_key)),
        )

    repaired = lib.register_builtin_release(
        OFFICIAL_PACK, trust_channel="BUILTIN_OFFICIAL")

    assert repaired.pack_path == OFFICIAL_PACK.resolve()
    assert repaired.pack_path.is_file()
    assert repaired.builtin is True
    assert repaired.trust_channel == "BUILTIN_OFFICIAL"
    if damage == "missing":
        assert not writable_copy.exists()
    else:
        assert writable_copy.read_bytes() == b"tampered-writable-cache"
    with pytest.raises(LifecycleError):
        lib.uninstall_revision(repaired.revision_key)


def test_checked_in_spec_is_location_independent():
    spec = (ROOT / "RetirementPet.spec").read_text(encoding="utf-8")
    normalized = spec.replace("\\\\", "/")
    assert not re.search(
        r"(?<![A-Za-z0-9_])[A-Za-z]:/", normalized,
        flags=re.IGNORECASE,
    )
    assert "SPECPATH" in spec


def test_malformed_existing_schema_is_not_mutated_and_handle_is_closed(
        tmp_path):
    import sqlite3

    root = tmp_path / "library"
    root.mkdir()
    database = root / "state.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    connection.execute("CREATE TABLE revisions (publisher_id TEXT)")
    connection.commit()
    connection.close()
    frozen = database.read_bytes()

    with pytest.raises(LifecycleError):
        PackLibrary(root)

    assert database.read_bytes() == frozen
    moved = root / "state.moved"
    database.rename(moved)
    moved.rename(database)


def test_malformed_recovery_intent_is_preflighted_without_database_write(
        tmp_path):
    root = tmp_path / "library"
    prepared = PackLibrary(root)
    prepared.close()
    database = root / "state.db"
    frozen = database.read_bytes()
    (root / "journal" / "intent-malformed.json").write_text(
        '{"transaction_id":"malformed"}', encoding="utf-8")

    with pytest.raises(LifecycleError):
        PackLibrary(root)

    assert database.read_bytes() == frozen


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_recovery_requires_exact_receipt_before_catalog_commit(
        tmp_path, monkeypatch, damage):
    import sqlite3

    root = tmp_path / "library"
    prepared = PackLibrary(root)

    def stop_after_publish(_intent, _rk):
        raise RuntimeError("injected stop after media publish")

    monkeypatch.setattr(prepared, "_commit_install", stop_after_publish)
    with pytest.raises(RuntimeError):
        prepared.install(REF_PACK)
    intent_path = next((root / "journal").glob("intent-*.json"))
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    receipt_path = root / "receipts" / f"{intent['receipt_id']}.json"
    target = Path(intent["target"])
    assert target.is_file()
    prepared.close()

    database = root / "state.db"
    frozen = database.read_bytes()
    if damage == "missing":
        receipt_path.unlink()
    else:
        receipt_path.write_bytes(b"{}")

    with pytest.raises(LifecycleError):
        PackLibrary(root)

    assert intent_path.is_file()
    assert target.is_file()
    assert database.read_bytes() == frozen
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM revisions").fetchone() \
            == (0,)
    finally:
        connection.close()


def _forged_intent(record, transaction_id, staging, target):
    library_root = next(
        parent for parent in record.pack_path.parents
        if (parent / "receipts").is_dir()
    )
    receipt_path = next(
        path for path in (library_root / "receipts").glob("*.json")
        if json.loads(path.read_text(encoding="utf-8")).get("content_digest")
        == record.revision_key.content_digest
    )
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes.decode("utf-8"))
    return {
        "transaction_id": transaction_id,
        "staging": str(staging),
        "target": str(target),
        "publisher_id": record.revision_key.pack.publisher_id,
        "package_id": record.revision_key.pack.package_id,
        "package_version": record.revision_key.package_version,
        "content_digest": record.revision_key.content_digest,
        "character_count": record.character_count,
        "trust_channel": record.trust_channel,
        "archive_sha256": receipt["archive_sha256"],
        "receipt_id": receipt["install_id"],
        "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "builtin": False,
        "expected_mutation": "INSERT_REVISION",
    }


@pytest.mark.parametrize("forgery", ["staging", "target", "transaction_id"])
def test_recovery_rejects_forged_paths_without_touching_outside_root(
        tmp_path, forgery):
    root = tmp_path / "library"
    prepared = PackLibrary(root)
    record = prepared.install(REF_PACK)
    prepared.close()
    transaction_id = "a" * 32
    expected_staging = root / "packs" / "staging" / transaction_id / \
        "pack.petpack"
    outside = tmp_path / "outside.petpack"
    outside.write_bytes(b"outside-sentinel")
    target = record.pack_path
    staging = expected_staging
    if forgery == "staging":
        staging = outside
    elif forgery == "target":
        target = outside
    else:
        transaction_id = "not-hex"
    intent = _forged_intent(
        record, transaction_id, staging, target)
    intent_path = root / "journal" / f"intent-{transaction_id}.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    frozen = (root / "state.db").read_bytes()

    with pytest.raises(LifecycleError):
        PackLibrary(root)

    assert outside.read_bytes() == b"outside-sentinel"
    assert intent_path.is_file()
    assert (root / "state.db").read_bytes() == frozen


def test_recovery_rejects_valid_target_with_wrong_revision_digest(tmp_path):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_petpack import build_pack, make_manifest, png_bytes

    root = tmp_path / "library"
    prepared = PackLibrary(root)
    record = prepared.install(REF_PACK)
    prepared.close()
    manifest = make_manifest()
    manifest["package"]["publisher_id"] = record.revision_key.pack.publisher_id
    manifest["package"]["id"] = record.revision_key.pack.package_id
    manifest["package"]["version"] = record.revision_key.package_version
    manifest["series"]["id"] = "reference"
    manifest["rights_declarations"][0]["claimant_ref"] = (
        record.revision_key.pack.publisher_id)
    manifest["sources"][0]["creator"] = record.revision_key.pack.publisher_id
    manifest["actions"][1]["id"] = (
        "community.retirementpet.minimal-static.reference.demo.wave")
    manifest["actions"][1]["semantic"] = manifest["actions"][1]["id"]
    wrong_bytes = build_pack(
        manifest,
        {
            "assets/thumb.png": png_bytes(4, 4, (1, 2, 3, 255)),
            "assets/idle_0.png": png_bytes(8, 8, (4, 5, 6, 255)),
        },
    )
    record.pack_path.write_bytes(wrong_bytes)
    transaction_id = "b" * 32
    intent = _forged_intent(
        record, transaction_id,
        root / "packs" / "staging" / transaction_id / "pack.petpack",
        record.pack_path,
    )
    intent_path = root / "journal" / f"intent-{transaction_id}.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    frozen = (root / "state.db").read_bytes()

    with pytest.raises(LifecycleError):
        PackLibrary(root)

    assert record.pack_path.read_bytes() == wrong_bytes
    assert intent_path.is_file()
    assert (root / "state.db").read_bytes() == frozen


@pytest.mark.parametrize(
    ("trust_channel", "builtin"),
    [("BUILTIN_OFFICIAL", False), ("LOCAL_IMPORTED", True)],
)
def test_recovery_intent_cannot_self_assign_builtin_trust(
        tmp_path, trust_channel, builtin):
    root = tmp_path / "library"
    prepared = PackLibrary(root)
    record = prepared.install(REF_PACK)
    media = record.pack_path.read_bytes()
    prepared.close()

    transaction_id = "c" * 32
    staging = root / "packs" / "staging" / transaction_id / "pack.petpack"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(media)
    intent = _forged_intent(
        record, transaction_id, staging, record.pack_path)
    intent["trust_channel"] = trust_channel
    intent["builtin"] = builtin
    intent_path = root / "journal" / f"intent-{transaction_id}.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    frozen = (root / "state.db").read_bytes()

    with pytest.raises(LifecycleError):
        PackLibrary(root)

    assert intent_path.is_file()
    assert staging.read_bytes() == media
    assert (root / "state.db").read_bytes() == frozen


@pytest.mark.parametrize("wrong_primary_key", ["meta", "revisions", "active"])
def test_full_column_schema_with_wrong_primary_key_is_frozen(
        tmp_path, wrong_primary_key):
    import sqlite3

    root = tmp_path / "library"
    root.mkdir()
    database = root / "state.db"
    connection = sqlite3.connect(database)
    meta_key = "" if wrong_primary_key == "meta" else " PRIMARY KEY"
    connection.execute(
        f"CREATE TABLE meta (key TEXT{meta_key}, value TEXT NOT NULL)")
    connection.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    revisions_pk = "" if wrong_primary_key == "revisions" else (
        ", PRIMARY KEY (publisher_id, package_id, package_version,"
        " content_digest)")
    connection.execute(
        "CREATE TABLE revisions ("
        "publisher_id TEXT NOT NULL, package_id TEXT NOT NULL,"
        "package_version TEXT NOT NULL, content_digest TEXT NOT NULL,"
        "pack_path TEXT NOT NULL, installed_at TEXT NOT NULL,"
        "character_count INTEGER NOT NULL, trust_channel TEXT NOT NULL,"
        "builtin INTEGER NOT NULL DEFAULT 0" + revisions_pk + ")")
    active_key = "" if wrong_primary_key == "active" else " PRIMARY KEY"
    connection.execute(
        "CREATE TABLE active_selection ("
        f"slot TEXT{active_key}, publisher_id TEXT NOT NULL,"
        "package_id TEXT NOT NULL, package_version TEXT NOT NULL,"
        "content_digest TEXT NOT NULL, character_fqid TEXT NOT NULL,"
        "variant_id TEXT, config_revision_id TEXT,"
        "generation INTEGER NOT NULL, commit_sequence INTEGER NOT NULL)")
    connection.commit()
    connection.close()
    frozen = database.read_bytes()

    with pytest.raises(LifecycleError):
        PackLibrary(root)

    assert database.read_bytes() == frozen


def test_recovery_restores_staging_when_catalog_committed_target_missing(
        tmp_path):
    root = tmp_path / "library"
    prepared = PackLibrary(root)
    record = prepared.install(REF_PACK)
    media = record.pack_path.read_bytes()
    transaction_id = "d" * 32
    staging = root / "packs" / "staging" / transaction_id / "pack.petpack"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(media)
    intent = _forged_intent(
        record, transaction_id, staging, record.pack_path)
    intent_path = root / "journal" / f"intent-{transaction_id}.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    record.pack_path.unlink()
    prepared.close()

    recovered = PackLibrary(root)
    try:
        ready = recovered.get_revision(record.revision_key)
        assert ready is not None
        assert ready.pack_path == recovered._path_of(record.revision_key)
        assert ready.pack_path.read_bytes() == media
        assert not staging.exists()
        assert not intent_path.exists()
    finally:
        recovered.close()


def test_library_rejects_reparse_root_before_any_storage_action(
        tmp_path, monkeypatch):
    import retirement_pet.lifecycle as lifecycle_module

    root = tmp_path / "library"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside-anchor-sentinel")
    frozen = sentinel.read_bytes()

    monkeypatch.setattr(
        lifecycle_module,
        "_path_is_reparse",
        lambda path: Path(path).absolute() == root.absolute(),
        raising=False,
    )
    with pytest.raises(LifecycleError):
        PackLibrary(root)

    assert sentinel.read_bytes() == frozen
    assert not (root / "state.db").exists()
    assert list(root.iterdir()) == []


def _make_windows_junction(link: Path, target: Path) -> None:
    """Create a real directory reparse point or skip on a restricted host."""
    if sys.platform != "win32":
        pytest.skip("Windows junction conformance test")
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("this Windows host cannot create a junction")


def test_real_windows_root_junction_is_rejected_without_following_target(
        tmp_path):
    outside = tmp_path / "outside-root"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside-root-sentinel")
    root = tmp_path / "library-junction"
    _make_windows_junction(root, outside)
    opened = None
    try:
        with pytest.raises(LifecycleError):
            opened = PackLibrary(root)
    finally:
        if opened is not None:
            opened.close()
        # rmdir removes only the junction itself; it never traverses target.
        if root.exists():
            root.rmdir()

    assert sentinel.read_bytes() == b"outside-root-sentinel"
    assert not (outside / "state.db").exists()


def test_nonexistent_root_below_mocked_reparse_ancestor_is_not_created(
        tmp_path, monkeypatch):
    import retirement_pet.lifecycle as lifecycle_module

    base = tmp_path / "base"
    base.mkdir()
    junction = base / "junction"
    requested = junction / "library"
    outside = tmp_path / "outside-mocked-ancestor"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside-mocked-ancestor")
    original = lifecycle_module._path_is_reparse

    monkeypatch.setattr(
        lifecycle_module,
        "_path_is_reparse",
        lambda path: Path(path).absolute() == junction.absolute()
        or original(path),
    )
    with pytest.raises(LifecycleError):
        PackLibrary(requested)

    assert not junction.exists()
    assert sentinel.read_bytes() == b"outside-mocked-ancestor"


def test_nonexistent_root_below_real_windows_junction_is_not_created(
        tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside-ancestor"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside-ancestor")
    junction = base / "junction"
    requested = junction / "library"
    _make_windows_junction(junction, outside)
    opened = None
    try:
        with pytest.raises(LifecycleError):
            opened = PackLibrary(requested)
        assert not (outside / "library").exists()
        assert sentinel.read_bytes() == b"outside-ancestor"
    finally:
        if opened is not None:
            opened.close()
        if junction.exists():
            junction.rmdir()


def test_real_windows_managed_directory_junction_is_preflighted(tmp_path):
    root = tmp_path / "library"
    prepared = PackLibrary(root)
    prepared.close()
    database = root / "state.db"
    frozen_database = database.read_bytes()
    journal = root / "journal"
    journal.rmdir()
    outside = tmp_path / "outside-journal"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside-journal-sentinel")
    _make_windows_junction(journal, outside)
    opened = None
    try:
        with pytest.raises(LifecycleError):
            opened = PackLibrary(root)
    finally:
        if opened is not None:
            opened.close()
        if journal.exists():
            journal.rmdir()

    assert database.read_bytes() == frozen_database
    assert sentinel.read_bytes() == b"outside-journal-sentinel"


@pytest.mark.parametrize(
    "relative",
    [
        "state.db", "state.db-journal", "state.db-wal", "state.db-shm",
        "journal",
        "packs/staging", "packs/revisions", "packs/trash", "receipts",
    ],
)
def test_library_preflights_every_existing_managed_reparse(
        tmp_path, monkeypatch, relative):
    import retirement_pet.lifecycle as lifecycle_module

    root = tmp_path / "library"
    prepared = PackLibrary(root)
    prepared.close()
    target = root / relative
    if "." in target.name:
        target.touch(exist_ok=True)
    else:
        target.mkdir(parents=True, exist_ok=True)
    database = root / "state.db"
    frozen_database = database.read_bytes()
    outside = tmp_path / "outside-managed-sentinel"
    outside.write_bytes(b"outside-managed-sentinel")

    monkeypatch.setattr(
        lifecycle_module,
        "_path_is_reparse",
        lambda path: Path(path).absolute() == target.absolute(),
        raising=False,
    )
    with pytest.raises(LifecycleError):
        PackLibrary(root)

    assert database.read_bytes() == frozen_database
    assert outside.read_bytes() == b"outside-managed-sentinel"


def test_hardlinked_external_valid_state_database_is_rejected_unchanged(
        tmp_path):
    root = tmp_path / "library"
    prepared = PackLibrary(root)
    prepared.close()
    database = root / "state.db"
    outside = tmp_path / "outside-valid-state.db"
    database.replace(outside)
    os.link(outside, database)
    frozen = outside.read_bytes()
    frozen_hash = hashlib.sha256(frozen).hexdigest()
    opened = None
    try:
        with pytest.raises(LifecycleError):
            opened = PackLibrary(root)
    finally:
        if opened is not None:
            opened.close()

    assert outside.read_bytes() == frozen
    assert hashlib.sha256(outside.read_bytes()).hexdigest() == frozen_hash
    bootstrap = PackLibrary.bootstrap(root)
    try:
        assert bootstrap.degraded is True
    finally:
        bootstrap.close()


@pytest.mark.parametrize("failure_stage", ["before_write", "after_fsync"])
def test_install_event_gap_recovers_once_before_intent_deletion(
        tmp_path, monkeypatch, failure_stage):
    root = tmp_path / "library"
    library = PackLibrary(root)
    original_append = library._append_event

    def fail_after_catalog_commit(event):
        if failure_stage == "after_fsync":
            original_append(event)
        raise OSError("injected event append failure")

    monkeypatch.setattr(library, "_append_event", fail_after_catalog_commit)
    with pytest.raises(OSError):
        library.install(REF_PACK)
    ready = library.list_revisions()
    assert len(ready) == 1
    intents = list((root / "journal").glob("intent-*.json"))
    assert len(intents) == 1
    intent = json.loads(intents[0].read_text(encoding="utf-8"))
    transaction_id = intent["transaction_id"]
    revision = str(ready[0].revision_key)
    library.close()

    recovered = PackLibrary(root)
    recovered.close()
    assert not list((root / "journal").glob("intent-*.json"))
    events = [json.loads(line) for line in
              (root / "journal" / "events.jsonl").read_text(
                  encoding="utf-8").splitlines()]
    matching = [event for event in events
                if event.get("event") == "INSTALL_COMMITTED"
                and event.get("transaction_id") == transaction_id
                and event.get("revision") == revision]
    assert len(matching) == 1

    reopened = PackLibrary(root)
    reopened.close()
    events_again = [json.loads(line) for line in
                    (root / "journal" / "events.jsonl").read_text(
                        encoding="utf-8").splitlines()]
    assert sum(event.get("transaction_id") == transaction_id
               for event in events_again) == 1


def test_recovery_rechecks_paths_after_intent_validation_before_move(
        tmp_path, monkeypatch):
    """A path swapped after validation is rejected at the action boundary."""
    import retirement_pet.lifecycle as lifecycle_module

    root = tmp_path / "library"
    library = PackLibrary(root)
    record = library.install(REF_PACK)
    media = record.pack_path.read_bytes()
    transaction_id = "e" * 32
    staging = root / "packs" / "staging" / transaction_id / "pack.petpack"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(media)
    target = record.pack_path
    intent = _forged_intent(record, transaction_id, staging, target)
    intent_path = root / "journal" / f"intent-{transaction_id}.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    target.unlink()
    sentinel = tmp_path / "outside-sentinel.bin"
    sentinel.write_bytes(b"outside-sentinel")

    original_validate = library._validated_intent
    original_is_reparse = lifecycle_module._path_is_reparse
    armed = False

    def validate_then_swap(path):
        nonlocal armed
        trusted = original_validate(path)
        armed = True
        return trusted

    def changed_path(path):
        return (armed and Path(path).absolute() == target.parent.absolute()) \
            or original_is_reparse(path)

    monkeypatch.setattr(library, "_validated_intent", validate_then_swap)
    monkeypatch.setattr(lifecycle_module, "_path_is_reparse", changed_path)
    with pytest.raises(LifecycleError):
        library.recover_pending_intents()

    assert staging.read_bytes() == media
    assert not target.exists()
    assert intent_path.is_file()
    assert sentinel.read_bytes() == b"outside-sentinel"
    library.close()


def test_recovery_rejects_action_time_hardlink_replacement(tmp_path,
                                                           monkeypatch):
    root = tmp_path / "library"
    library = PackLibrary(root)
    record = library.install(REF_PACK)
    media = record.pack_path.read_bytes()
    transaction_id = "1" * 32
    staging = root / "packs" / "staging" / transaction_id / "pack.petpack"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(media)
    target = record.pack_path
    intent = _forged_intent(record, transaction_id, staging, target)
    intent_path = root / "journal" / f"intent-{transaction_id}.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    target.unlink()
    outside = tmp_path / "outside-valid-pack.petpack"
    outside.write_bytes(media)
    frozen = outside.read_bytes()
    frozen_hash = hashlib.sha256(frozen).hexdigest()

    original_validate = library._validated_intent
    swapped = False

    def validate_then_hardlink(path):
        nonlocal swapped
        trusted = original_validate(path)
        if not swapped:
            staging.unlink()
            os.link(outside, staging)
            swapped = True
        return trusted

    monkeypatch.setattr(library, "_validated_intent", validate_then_hardlink)
    with pytest.raises(LifecycleError):
        library.recover_pending_intents()

    assert staging.is_file()
    assert not target.exists()
    assert intent_path.is_file()
    assert outside.read_bytes() == frozen
    assert hashlib.sha256(outside.read_bytes()).hexdigest() == frozen_hash
    library.close()


def test_legacy_install_event_prevents_duplicate_until_uninstall(tmp_path):
    root = tmp_path / "library"
    library = PackLibrary(root)
    record = library.install(REF_PACK)
    transaction_id = "2" * 32
    staging = root / "packs" / "staging" / transaction_id / "pack.petpack"
    intent = _forged_intent(
        record, transaction_id, staging, record.pack_path)
    intent_path = root / "journal" / f"intent-{transaction_id}.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    events_path = root / "journal" / "events.jsonl"
    legacy = {
        "event": "INSTALL_COMMITTED",
        "revision": str(record.revision_key),
        "at_utc": record.installed_at,
        "builtin": False,
    }
    events_path.write_text(
        json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
    library.close()

    recovered = PackLibrary(root)
    recovered.close()
    assert not intent_path.exists()
    events = [json.loads(line) for line in
              events_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["INSTALL_COMMITTED"]
    assert "transaction_id" not in events[0]

    reinstaller = PackLibrary(root)
    assert reinstaller.uninstall_revision(record.revision_key) is True
    reinstaller.install(REF_PACK)
    reinstaller.close()
    events = [json.loads(line) for line in
              events_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == [
        "INSTALL_COMMITTED", "UNINSTALL_TRASHED", "INSTALL_COMMITTED"]
    assert isinstance(events[-1].get("transaction_id"), str)


def test_partial_event_tail_is_preserved_and_recovery_is_idempotent(tmp_path):
    root = tmp_path / "library"
    library = PackLibrary(root)
    first = library.install(REF_PACK)
    assert library.uninstall_revision(first.revision_key) is True
    current = library.install(REF_PACK)
    events_path = root / "journal" / "events.jsonl"
    complete_lines = events_path.read_bytes().splitlines(keepends=True)
    latest_event = json.loads(complete_lines[-1])
    transaction_id = latest_event["transaction_id"]
    staging = root / "packs" / "staging" / transaction_id / "pack.petpack"
    intent = _forged_intent(
        current, transaction_id, staging, current.pack_path)
    intent_path = root / "journal" / f"intent-{transaction_id}.json"
    library.close()
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    torn = b"".join(complete_lines[:-1]) + b'{"event":'
    events_path.write_bytes(torn)

    recovered = PackLibrary(root)
    recovered.close()
    assert not intent_path.exists()
    repaired = events_path.read_bytes()
    assert repaired.startswith(torn)
    assert repaired[len(torn):].startswith(b"\n")

    valid_events = []
    for line in repaired.splitlines():
        try:
            valid_events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    assert sum(
        event.get("event") == "INSTALL_COMMITTED"
        and event.get("transaction_id") == transaction_id
        for event in valid_events
    ) == 1

    reopened = PackLibrary(root)
    reopened.close()
    assert events_path.read_bytes() == repaired
