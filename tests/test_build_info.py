"""Artifact build identity and release inventory contracts."""

from __future__ import annotations

import json
import hashlib
from types import SimpleNamespace
from pathlib import Path

import pytest

from retirement_pet.build_info import (
    BuildInfoError,
    build_attestation,
    compute_build_id,
    load_build_info,
    source_build_info,
    validate_build_info,
    validate_build_attestation,
    write_build_info,
)
from scripts import dist_manifest, release_identity


def test_release_version_is_consistent_across_runtime_and_packaging():
    import tomllib

    from retirement_pet import __version__

    root = Path(__file__).resolve().parent.parent
    project = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8"))
    version_resource = (root / "scripts/version_info.txt").read_text(
        encoding="utf-8")
    assert __version__ == "1.1.1"
    assert project["project"]["version"] == __version__
    assert 'StringStruct("FileVersion", "1.1.1.0")' in version_resource
    assert 'StringStruct("ProductVersion", "1.1.1.0")' in version_resource


def test_character_author_sources_are_release_inputs_with_byte_stable_license():
    root = Path(__file__).resolve().parent.parent
    attributes = (root / ".gitattributes").read_text(encoding="utf-8")

    assert "character-work" in release_identity._BUILD_INPUT_PATHS
    assert ("/character-work/realistic-retirement-cat/legal/license.txt binary"
            in attributes)


def _valid_build_info() -> dict:
    value = {
        "schema": 1,
        "artifact": "RetirementPet",
        "artifact_kind": "onedir",
        "version": "1.1.1",
        "commit": "a" * 40,
        "git_tree": "b" * 40,
        "source_clean": True,
        "built_at_utc": "2026-09-01T00:00:00+00:00",
        "inputs": {
            "inventory_sha256": hashlib.sha256(b"").hexdigest(),
            "files": {},
        },
        "toolchain": {
            "public_lock_sha256": "9" * 64,
            "attestation_sha256": "c" * 64,
            "python_version": "3.12.7",
            "python_implementation": "CPython",
            "python_architecture": "64bit",
            "qt_version": "6.8.3",
            "packages": {"pyside6": "6.8.3"},
        },
        "packaging": {"mode": "onedir", "upx": False,
                      "spec": "RetirementPet.spec",
                      "source": "git-archive"},
    }
    value["build_id"] = compute_build_id(value)
    return value


def test_build_id_is_deterministic_and_excludes_timestamp():
    first = _valid_build_info()
    second = dict(first, built_at_utc="2026-09-02T00:00:00+00:00")
    second["build_id"] = compute_build_id(second)
    assert second["build_id"] == first["build_id"]
    assert validate_build_info(second)["build_id"] == first["build_id"]


@pytest.mark.parametrize("field,value", (
    ("commit", "not-a-commit"),
    ("git_tree", "1" * 39),
    ("source_clean", False),
    ("version", "9.9.9"),
))
def test_build_info_rejects_invalid_or_untrusted_identity(field, value):
    info = _valid_build_info()
    info[field] = value
    info["build_id"] = compute_build_id(info)
    with pytest.raises(BuildInfoError):
        validate_build_info(info)


def test_build_info_rejects_tampering_without_recomputed_id():
    info = _valid_build_info()
    info["commit"] = "d" * 40
    with pytest.raises(BuildInfoError, match="does not match"):
        validate_build_info(info)


def test_build_info_rejects_internally_inconsistent_input_inventory():
    info = _valid_build_info()
    info["inputs"]["files"] = {"src/example.py": "d" * 64}
    info["build_id"] = compute_build_id(info)
    with pytest.raises(BuildInfoError, match="inventory digest"):
        validate_build_info(info)


def test_source_run_never_claims_release_identity(tmp_path, monkeypatch):
    missing = tmp_path / "missing-build-info.json"
    monkeypatch.setattr(
        "retirement_pet.build_info.resource_path", lambda *_parts: missing)
    assert load_build_info(require_embedded=False) == source_build_info()
    with pytest.raises(BuildInfoError, match="compiled"):
        load_build_info(require_embedded=True)


def test_frozen_identity_requires_compiled_and_external_exact_match(
        tmp_path, monkeypatch):
    import retirement_pet.build_info as build_info_module

    info = _valid_build_info()
    external = tmp_path / "build-info.json"
    external.write_text(json.dumps(info), encoding="utf-8")
    monkeypatch.setattr(build_info_module, "resource_path",
                        lambda *_parts: external)
    monkeypatch.setattr(
        build_info_module.importlib, "import_module",
        lambda _name: SimpleNamespace(BUILD_INFO=info),
    )
    assert load_build_info(require_embedded=True) == info

    changed = dict(info, built_at_utc="2026-09-03T00:00:00+00:00")
    external.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(BuildInfoError, match="differ"):
        load_build_info(require_embedded=True)


def test_build_info_diagnostic_write_is_atomic_and_valid(tmp_path):
    output = tmp_path / "nested" / "build-info.json"
    info = _valid_build_info()
    write_build_info(output, info)
    assert json.loads(output.read_text(encoding="utf-8")) == info
    assert not output.with_name(output.name + ".tmp").exists()


def test_source_main_build_info_output_is_honest(tmp_path, monkeypatch):
    from retirement_pet.main import main

    output = tmp_path / "source-build-info.json"
    monkeypatch.setattr(
        "sys.argv", ["retirement-pet", "--build-info-out", str(output)])
    assert main() == 0
    assert json.loads(output.read_text(encoding="utf-8")) == \
        source_build_info()


def test_attestation_v2_wraps_compiled_identity(monkeypatch):
    info = _valid_build_info()
    monkeypatch.setattr(
        "retirement_pet.build_info.load_build_info", lambda **_kwargs: info)
    attestation = build_attestation()
    assert validate_build_attestation(attestation) == {
        "protocol": 2,
        "compiled_identity": info,
        "external_match": True,
    }
    with pytest.raises(BuildInfoError):
        validate_build_attestation({
            "protocol": 1,
            "compiled_identity": info,
            "external_match": True,
        })


def test_generated_python_module_contains_same_validated_identity(tmp_path):
    output = tmp_path / "retirement_pet_build_info.py"
    info = _valid_build_info()
    release_identity.write_python_module_atomic(output, info)
    namespace: dict = {}
    exec(output.read_text(encoding="utf-8"), namespace)  # noqa: S102
    assert namespace["BUILD_INFO"] == info


def test_toolchain_verifier_reports_exact_package_drift(monkeypatch):
    lock = {
        "schema": 2,
        "python": {
            "version": "3.12.7",
            "implementation": "CPython",
            "architecture": "64bit",
        },
        "packages": {"one-package": "1.0"},
        "packaging": {"mode": "onedir", "upx": False},
        "qt_version": "6.8.3",
    }
    monkeypatch.setattr(release_identity.platform, "python_version",
                        lambda: "3.12.7")
    monkeypatch.setattr(release_identity.platform, "python_implementation",
                        lambda: "CPython")
    monkeypatch.setattr(release_identity.platform, "architecture",
                        lambda: ("64bit", "WindowsPE"))
    monkeypatch.setattr(release_identity, "installed_packages",
                        lambda: {"one-package": "2.0", "extra": "1"})
    failures = release_identity.verify_toolchain(lock)
    assert any("versions differ" in failure for failure in failures)
    assert any("unlocked packages" in failure for failure in failures)


def test_tracked_build_lock_contains_only_machine_neutral_fields():
    root = Path(__file__).resolve().parent.parent
    lock = release_identity.load_lock(root / "scripts" / "build_lock.json")
    encoded = json.dumps(lock, sort_keys=True).casefold()

    assert lock["schema"] == 2
    assert set(lock["python"]) == {
        "version", "implementation", "architecture"}
    for forbidden in (
            "distribution", "compiler", "machine", "fingerprint",
            "site_packages", "base_runtime", "git", "sha256"):
        assert forbidden not in encoded


def _exact_toolchain_fixture() -> dict:
    tree = {"sha256": "1" * 64, "file_count": 1, "total_bytes": 1}
    return {
        "python": {
            "version": "3.12.7",
            "implementation": "CPython",
            "architecture": "64bit",
            "compiler": "private compiler",
            "distribution": "private distribution",
            "machine": "private architecture",
        },
        "qt_version": "6.8.3",
        "packages": {"one-package": "1.0"},
        "fingerprints": {
            "python_executable_sha256": "2" * 64,
            "python_runtime_dll_sha256": "3" * 64,
            "site_packages": tree,
            "base_stdlib": tree,
            "base_runtime": {"DLLs": tree},
            "git": {"version": "private Git", "exe_sha256": "4" * 64},
        },
    }


def test_private_attestation_is_lock_bound_and_exact(
        tmp_path, monkeypatch):
    lock = {
        "schema": 2,
        "python": {
            "version": "3.12.7", "implementation": "CPython",
            "architecture": "64bit",
        },
        "packages": {"one-package": "1.0"},
        "packaging": {"mode": "onedir", "upx": False},
        "qt_version": "6.8.3",
    }
    lock_path = tmp_path / "build_lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    exact = _exact_toolchain_fixture()
    monkeypatch.setattr(
        release_identity, "verify_toolchain", lambda _lock=None: [])
    monkeypatch.setattr(
        release_identity, "exact_toolchain_snapshot", lambda: exact)
    attestation = release_identity.create_toolchain_attestation(lock)
    path = tmp_path / "toolchain-attestation.json"
    path.write_text(json.dumps(attestation), encoding="utf-8")

    failures, digest = release_identity.verify_release_toolchain(
        lock_path, path)
    assert failures == []
    assert digest == release_identity.toolchain_attestation_digest(attestation)

    changed = json.loads(json.dumps(attestation))
    changed["toolchain"]["python"]["compiler"] = "different compiler"
    path.write_text(json.dumps(changed), encoding="utf-8")
    failures, changed_digest = release_identity.verify_release_toolchain(
        lock_path, path, digest)
    assert changed_digest != digest
    assert any("exact toolchain differs" in item for item in failures)
    assert any("expected digest" in item for item in failures)


def test_attestation_digest_binds_complete_proof_and_public_lock():
    lock_digest = "5" * 64
    attestation = {
        "schema": 1,
        "generated_at_utc": "2026-09-01T00:00:00+00:00",
        "public_lock_sha256": lock_digest,
        "toolchain": _exact_toolchain_fixture(),
    }
    original = release_identity.toolchain_attestation_digest(attestation)
    changed = json.loads(json.dumps(attestation))
    changed["public_lock_sha256"] = "6" * 64
    assert release_identity.toolchain_attestation_digest(changed) != original


def test_input_inventory_is_path_sorted_and_content_bound(tmp_path):
    first = tmp_path / "z.txt"
    second = tmp_path / "a.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    a = release_identity.input_inventory([first, second], tmp_path)
    b = release_identity.input_inventory([second, first], tmp_path)
    assert a == b
    first.write_text("changed", encoding="utf-8")
    assert release_identity.input_inventory([first, second], tmp_path) != a


def test_source_snapshot_is_bound_to_committed_blob_bytes(
        tmp_path, monkeypatch):
    source = tmp_path / "source"
    tracked = source / "src" / "module.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(b"line one\n")
    oid = release_identity._git_blob_oid(tracked)
    listing = f"100644 blob {oid}\tsrc/module.py\0".encode("utf-8")
    monkeypatch.setattr(
        release_identity.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=listing),
    )
    assert release_identity.verify_source_snapshot(
        tmp_path, source, "a" * 40) == []

    tracked.write_bytes(b"line one\r\n")
    failures = release_identity.verify_source_snapshot(
        tmp_path, source, "a" * 40)
    assert failures == [
        "snapshot inputs differ from Git blobs: src/module.py"]


def test_git_identity_rejects_hidden_index_flags(monkeypatch):
    def fake_git(_root, *args):
        if args[:2] == ("status", "--porcelain"):
            return ""
        if args == ("ls-files", "-v"):
            return "H src/ordinary.py\nh src/assume-unchanged.py\n"
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args == ("rev-parse", "HEAD^{tree}"):
            return "b" * 40
        raise AssertionError(args)

    monkeypatch.setattr(release_identity, "_run_git", fake_git)
    identity = release_identity.git_identity(Path("."))
    assert identity["source_clean"] is False
    assert identity["special_index_paths"] == ["src/assume-unchanged.py"]


def test_toolchain_tree_rejects_loadable_sourceless_bytecode(tmp_path):
    (tmp_path / "sentinel.pyc").write_bytes(b"loadable legacy bytecode")
    with pytest.raises(
            release_identity.ReleaseIdentityError,
            match="legacy sourceless bytecode is forbidden"):
        release_identity._tree_fingerprint(tmp_path)


def test_toolchain_tree_ignores_only_normal_pycache(tmp_path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "module.cpython-312.pyc").write_bytes(b"cache")
    fingerprint = release_identity._tree_fingerprint(tmp_path)
    assert fingerprint["file_count"] == 0


def _fake_dist(root: Path) -> Path:
    dist = root / "RetirementPet"
    for rel in dist_manifest._REQUIRED_FILES:
        path = dist / Path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"file")
    (dist / "_internal" / "build-info.json").write_text(
        json.dumps(_valid_build_info()), encoding="utf-8")
    return dist


def test_dist_manifest_binds_artifact_to_expected_source(tmp_path):
    dist = _fake_dist(tmp_path)
    info = _valid_build_info()
    manifest, inventory, failures = dist_manifest.validate_distribution(
        dist, expected_commit=info["commit"],
        expected_build_id=info["build_id"],
        source_commit=info["commit"], source_clean=True,
        runtime_identity=info,
    )
    assert failures == []
    assert manifest["result"] == "PASS"
    assert manifest["artifact"]["commit"] == info["commit"]
    assert manifest["dist_tree_sha256"]
    assert len(inventory) == len(dist_manifest._REQUIRED_FILES)


def test_dist_manifest_fails_closed_on_stale_artifact_or_dirty_source(tmp_path):
    dist = _fake_dist(tmp_path)
    manifest, _inventory, failures = dist_manifest.validate_distribution(
        dist, expected_commit="d" * 40, expected_build_id="e" * 64,
        source_commit="f" * 40, source_clean=False,
        runtime_identity=_valid_build_info(),
    )
    assert manifest["result"] == "FAIL"
    assert any("expected commit" in failure for failure in failures)
    assert any("expected build id" in failure for failure in failures)
    assert any("current source HEAD" in failure for failure in failures)
    assert any("not clean" in failure for failure in failures)


def test_dist_manifest_enforces_size_and_file_caps(tmp_path, monkeypatch):
    dist = _fake_dist(tmp_path)
    info = _valid_build_info()
    monkeypatch.setattr(dist_manifest, "HARD_CAP_BYTES", 1)
    monkeypatch.setattr(dist_manifest, "HARD_FILE_CAP", 1)
    _manifest, _inventory, failures = dist_manifest.validate_distribution(
        dist, expected_commit=info["commit"],
        expected_build_id=info["build_id"],
        source_commit=info["commit"], source_clean=True,
        runtime_identity=info,
    )
    assert any("size" in failure for failure in failures)
    assert any("file count" in failure for failure in failures)


def test_dist_manifest_rejects_external_metadata_not_bound_to_runtime(tmp_path):
    dist = _fake_dist(tmp_path)
    info = _valid_build_info()
    stale = dict(info, commit="d" * 40)
    stale["build_id"] = compute_build_id(stale)
    manifest, _inventory, failures = dist_manifest.validate_distribution(
        dist, expected_commit=info["commit"],
        expected_build_id=info["build_id"],
        source_commit=info["commit"], source_clean=True,
        runtime_identity=stale,
    )
    assert manifest["runtime_identity_verified"] is False
    assert any("runtime identity differs" in failure for failure in failures)


def test_dist_manifest_static_receipt_binding_detects_changed_bytes(tmp_path):
    dist = _fake_dist(tmp_path)
    info = _valid_build_info()
    manifest, _inventory, failures = dist_manifest.validate_distribution(
        dist, expected_commit=info["commit"],
        expected_build_id=info["build_id"],
        source_commit=info["commit"], source_tree=info["git_tree"],
        source_clean=True, require_runtime=False,
    )
    assert failures == []

    (dist / "_internal" / "config" / "defaults.json").write_bytes(
        b"changed")
    _changed, _inventory, failures = dist_manifest.validate_distribution(
        dist, expected_commit=info["commit"],
        expected_build_id=info["build_id"],
        expected_artifact_id=manifest["artifact_id"],
        expected_exe_sha256=manifest["exe_sha256"],
        expected_file_count=manifest["file_count"],
        expected_total_bytes=manifest["total_bytes"],
        source_commit=info["commit"], source_tree=info["git_tree"],
        source_clean=True, require_runtime=False,
    )
    assert any("artifact inventory" in failure for failure in failures)
    assert any("byte count" in failure for failure in failures)


def test_dist_manifest_main_never_probes_a_static_mismatch(
        tmp_path, monkeypatch):
    dist = _fake_dist(tmp_path)
    info = _valid_build_info()
    evidence = tmp_path / "evidence"
    probed = False

    def forbidden_probe(_dist):
        nonlocal probed
        probed = True
        raise AssertionError("unbound artifact must not execute")

    monkeypatch.setattr(dist_manifest, "probe_runtime_identity",
                        forbidden_probe)
    monkeypatch.setattr(
        dist_manifest, "current_source",
        lambda _root: (info["commit"], info["git_tree"], True))
    monkeypatch.setattr("sys.argv", [
        "dist_manifest.py", "--dist", str(dist),
        "--expected-commit", info["commit"],
        "--expected-build-id", info["build_id"],
        "--expected-artifact-id", "0" * 64,
        "--expected-exe-sha256", "0" * 64,
        "--expected-file-count", "1",
        "--expected-total-bytes", "1",
        "--evidence-root", str(evidence),
        "--git-root", str(tmp_path),
    ])
    assert dist_manifest.main() == 2
    assert probed is False
    reports = list(evidence.rglob("manifest.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["result"] == "FAIL"
    assert report["runtime_identity_verified"] is False


def test_runtime_probe_rejects_legacy_or_non_executable_artifact(tmp_path):
    dist = _fake_dist(tmp_path)
    identity, error = dist_manifest.probe_runtime_identity(dist)
    assert identity is None
    assert error == "artifact executable is not a valid PE image"
