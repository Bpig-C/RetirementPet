"""Acceptance-controller semantics must fail closed and stay profile-specific."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from retirement_pet.build_info import compute_build_id
from scripts import dist_manifest, go_no_go


def _receipt() -> dict:
    return {
        "schema": 1,
        "result": "PASS",
        "recipe_id": "a" * 64,
        "artifact_id": "b" * 64,
        "exe_sha256": "c" * 64,
        "commit": "d" * 40,
        "git_tree": "e" * 40,
        "file_count": 10,
        "total_bytes": 100,
        "attestation_protocol": 2,
    }


def test_receipt_parser_requires_exact_pass_schema(tmp_path):
    path = tmp_path / "receipt.json"
    value = _receipt()
    path.write_text(json.dumps(value), encoding="utf-8")
    assert go_no_go._load_receipt(path) == value

    value["unexpected"] = True
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(go_no_go.GateError):
        go_no_go._load_receipt(path)


@pytest.mark.parametrize("field,value", [
    ("result", "FAIL"),
    ("recipe_id", "not-a-hash"),
    ("file_count", 0),
    ("total_bytes", 0),
    ("attestation_protocol", 1),
])
def test_receipt_parser_rejects_untrusted_values(tmp_path, field, value):
    path = tmp_path / "receipt.json"
    receipt = _receipt()
    receipt[field] = value
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(go_no_go.GateError):
        go_no_go._load_receipt(path)


def test_alpha_and_production_verdicts_are_never_conflated():
    assert go_no_go._verdicts(True, "alpha") == (
        "ALPHA-GO", "PRODUCTION-NO-GO:PENDING_ENVIRONMENT", True)
    assert go_no_go._verdicts(True, "production") == (
        "ALPHA-GO", "PRODUCTION-NO-GO:PENDING_ENVIRONMENT", False)
    assert go_no_go._verdicts(False, "alpha") == (
        "ALPHA-NO-GO", "PRODUCTION-NO-GO:ALPHA_GATES_FAILED", False)


def test_dist_receipt_binding_covers_entire_artifact_identity():
    receipt = _receipt()
    binding = go_no_go._receipt_binding(receipt)
    assert binding == receipt
    changed = dict(receipt, artifact_id="f" * 64)
    assert go_no_go._receipt_binding(changed) != binding


def _alpha_health_report(receipt: dict, receipt_sha256: str) -> dict:
    return {
        "schema": 1,
        "result": "PASS",
        "target": "exe",
        "evidence_class": "ALPHA_HEALTH",
        "evidence_complete": True,
        "production_gate": False,
        "formal_performance_gate": False,
        "harness": {"postcheck_unchanged": True},
        "artifact": {
            "build_id": receipt["recipe_id"],
            "artifact_id": receipt["artifact_id"],
            "exe_sha256": receipt["exe_sha256"],
            "commit": receipt["commit"],
            "git_tree": receipt["git_tree"],
            "receipt_sha256": receipt_sha256,
        },
        "failures": [],
    }


def test_alpha_health_report_is_bound_and_never_claims_production():
    receipt = _receipt()
    receipt_sha256 = "0" * 64
    report = _alpha_health_report(receipt, receipt_sha256)
    outcome, detail = go_no_go._health_report_outcome(
        report, receipt, exit_code=0, evidence_class="ALPHA_HEALTH",
        formal_field="formal_performance_gate",
        receipt_sha256=receipt_sha256)
    assert outcome == go_no_go.PASS
    assert "not a Production gate" in detail


@pytest.mark.parametrize("mutation", [
    lambda report: report["artifact"].update(artifact_id="f" * 64),
    lambda report: report["artifact"].update(receipt_sha256="f" * 64),
    lambda report: report.update(production_gate=True),
    lambda report: report.update(formal_performance_gate=True),
    lambda report: report["harness"].update(postcheck_unchanged=False),
    lambda report: report.update(evidence_complete=False),
])
def test_alpha_health_report_rejects_unbound_or_overclaimed_evidence(mutation):
    receipt = _receipt()
    receipt_sha256 = "0" * 64
    report = _alpha_health_report(receipt, receipt_sha256)
    mutation(report)
    outcome, _detail = go_no_go._health_report_outcome(
        report, receipt, exit_code=0, evidence_class="ALPHA_HEALTH",
        formal_field="formal_performance_gate",
        receipt_sha256=receipt_sha256)
    assert outcome == go_no_go.INVALID


def test_alpha_health_report_preserves_structured_product_failure():
    receipt = _receipt()
    receipt_sha256 = "0" * 64
    report = _alpha_health_report(receipt, receipt_sha256)
    report.update(result="FAIL", failures=["process exited early"])
    outcome, detail = go_no_go._health_report_outcome(
        report, receipt, exit_code=1, evidence_class="ALPHA_HEALTH",
        formal_field="formal_performance_gate",
        receipt_sha256=receipt_sha256)
    assert outcome == go_no_go.FAIL
    assert detail == "process exited early"


def _smoke_report(receipt: dict) -> dict:
    return {
        "schema": 4,
        "result": "PASS",
        "evidence_complete": True,
        "harness_completed": True,
        "harness_errors": [],
        "cleanup_actions": [],
        "expected_build_id": receipt["recipe_id"],
        "observed_build_id": receipt["recipe_id"],
        "expected_exe_sha256": receipt["exe_sha256"],
        "observed_exe_sha256": receipt["exe_sha256"],
        "observed_runtime_build_id": receipt["recipe_id"],
        "attestation_protocol": 2,
        "local_import_canary": {
            "schema": 1,
            "operation": "local_import_preflight",
            "ipc_protocol": 1,
            "result": "PASS",
            "archive_sha256": (
                "ce80e7dd73726cdbb252f21d65538014fe10790dce7a44816ebb80bc26c2a964"
            ),
            "content_digest": (
                "6c4b368ad79124bf5bc48e6d8190917c7031f923b2cf00f728c4c17b9c21260c"
            ),
            "character_ids": ["realistic-cat"],
            "first_frames_verified": True,
            "child_exit_code": 0,
            "child_reaped": True,
        },
        "local_import_timeout_canary": {
            "schema": 1,
            "operation": "local_import_preflight",
            "ipc_protocol": 1,
            "result": "TIMEOUT",
            "archive_sha256": None,
            "content_digest": None,
            "character_ids": [],
            "first_frames_verified": False,
            "child_exit_code": 1,
            "child_reaped": True,
        },
        "failures": [],
    }


def test_smoke_report_accepts_complete_receipt_bound_pass():
    receipt = _receipt()
    outcome, detail = go_no_go._smoke_report_outcome(
        _smoke_report(receipt), receipt, exit_code=0)
    assert outcome == go_no_go.PASS
    assert "receipt-bound" in detail


def test_smoke_report_preserves_complete_candidate_failure():
    receipt = _receipt()
    report = _smoke_report(receipt)
    report.update(result="FAIL", failures=["primary exited early"])
    outcome, detail = go_no_go._smoke_report_outcome(
        report, receipt, exit_code=1)
    assert outcome == go_no_go.FAIL
    assert detail == "primary exited early"


def test_smoke_report_preserves_explicit_frozen_import_failure():
    receipt = _receipt()
    report = _smoke_report(receipt)
    report.update(
        result="FAIL",
        failures=["local-import preflight did not pass"],
        local_import_canary={
            **report["local_import_canary"],
            "result": "REJECT",
            "first_frames_verified": False,
        },
    )

    outcome, detail = go_no_go._smoke_report_outcome(
        report, receipt, exit_code=1)

    assert outcome == go_no_go.FAIL
    assert "local-import" in detail


def test_smoke_report_classifies_harness_exception_as_invalid():
    receipt = _receipt()
    report = _smoke_report(receipt)
    report.update(
        result="INVALID",
        evidence_complete=False,
        harness_completed=False,
        harness_errors=["required system command is unavailable"],
    )
    outcome, detail = go_no_go._smoke_report_outcome(
        report, receipt, exit_code=2)
    assert outcome == go_no_go.INVALID
    assert "required system command" in detail


def test_smoke_report_rejects_incomplete_failure_as_invalid():
    receipt = _receipt()
    report = _smoke_report(receipt)
    report.update(
        result="FAIL", evidence_complete=False, harness_completed=False,
        failures=["incomplete observation"],
    )
    outcome, _detail = go_no_go._smoke_report_outcome(
        report, receipt, exit_code=1)
    assert outcome == go_no_go.INVALID


@pytest.mark.parametrize(
    "mutation",
    [
        lambda report: report.pop("local_import_canary"),
        lambda report: report["local_import_canary"].update(
            first_frames_verified=False),
        lambda report: report["local_import_canary"].update(
            archive_sha256="0" * 64),
        lambda report: report["local_import_timeout_canary"].update(
            child_reaped=False),
        lambda report: report["local_import_timeout_canary"].update(
            child_exit_code=0),
        lambda report: report["local_import_timeout_canary"].update(
            result="PASS"),
    ],
)
def test_smoke_report_rejects_missing_or_unbound_frozen_import_evidence(
        mutation):
    receipt = _receipt()
    report = _smoke_report(receipt)
    mutation(report)

    outcome, detail = go_no_go._smoke_report_outcome(
        report, receipt, exit_code=0)

    assert outcome == go_no_go.INVALID
    assert "local-import" in detail


def test_smoke_report_rejects_legacy_schema_even_with_receipt_binding():
    receipt = _receipt()
    report = _smoke_report(receipt)
    report["schema"] = 3

    outcome, detail = go_no_go._smoke_report_outcome(
        report, receipt, exit_code=0)

    assert outcome == go_no_go.INVALID
    assert "schema" in detail


def test_release_powershell_does_not_use_ambient_get_file_hash():
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    for name in ("build.ps1", "smoke_test.ps1"):
        source = (scripts / name).read_text(encoding="utf-8")
        assert "Get-FileHash" not in source
        assert "System.Security.Cryptography.SHA256" in source


def test_frozen_local_import_canary_is_part_of_smoke_contract():
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    smoke = (scripts / "smoke_test.ps1").read_text(encoding="utf-8")

    assert "--local-import-preflight-out" in smoke
    assert "--local-import-pack" in smoke
    assert "--local-import-preflight-timeout-ms" in smoke
    assert "schema = 4" in smoke
    assert "local_import_canary = $ObservedImportCanary" in smoke
    assert "local_import_timeout_canary = $ObservedImportTimeoutCanary" in smoke


def _build_info() -> dict:
    value = {
        "schema": 1,
        "artifact": "RetirementPet",
        "artifact_kind": "onedir",
        "version": "1.1.1",
        "commit": "d" * 40,
        "git_tree": "e" * 40,
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
        "packaging": {
            "mode": "onedir", "upx": False,
            "spec": "RetirementPet.spec", "source": "git-archive",
        },
    }
    value["build_id"] = compute_build_id(value)
    return value


def _artifact_and_receipt(root: Path) -> tuple[Path, dict]:
    artifact = root / "RetirementPet"
    for relative in dist_manifest._REQUIRED_FILES:
        path = artifact / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    info = _build_info()
    (artifact / "_internal" / "build-info.json").write_text(
        json.dumps(info), encoding="utf-8")
    runtime = artifact / "_internal" / "python312.dll"
    runtime.write_bytes(b"trusted-runtime")
    manifest, _inventory, failures = dist_manifest.validate_distribution(
        artifact,
        expected_commit=info["commit"],
        expected_build_id=info["build_id"],
        source_commit=info["commit"], source_tree=info["git_tree"],
        source_clean=True, require_runtime=False,
    )
    assert failures == []
    receipt = {
        "schema": 1,
        "result": "PASS",
        "recipe_id": info["build_id"],
        "artifact_id": manifest["artifact_id"],
        "exe_sha256": manifest["exe_sha256"],
        "commit": info["commit"],
        "git_tree": info["git_tree"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "attestation_protocol": 2,
    }
    return artifact, receipt


def _args(artifact: Path, receipt_path: Path,
          evidence: Path) -> argparse.Namespace:
    return argparse.Namespace(
        profile="alpha", artifact_dir=artifact,
        receipt=receipt_path, evidence_root=evidence,
    )


def _acceptance(evidence: Path) -> dict:
    paths = list(evidence.rglob("acceptance.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


def test_gate_run_distinguishes_failure_from_invalid_execution(tmp_path):
    gate = go_no_go.GateRun(tmp_path, dict(os.environ))
    assert gate.command(
        "test_failure", [sys.executable, "-c", "raise SystemExit(1)"]
    ) == go_no_go.FAIL
    assert gate.command(
        "test_internal", [sys.executable, "-c", "raise SystemExit(2)"]
    ) == go_no_go.INVALID
    assert gate.command(
        "bad_postcheck", [sys.executable, "-c", "raise SystemExit(0)"],
        postcheck=lambda _code: (_ for _ in ()).throw(
            RuntimeError("broken evidence")),
    ) == go_no_go.INVALID
    assert gate.invalid_reasons == [
        "test_internal: unexpected exit 2",
        "bad_postcheck: postcheck error: broken evidence",
    ]


def test_tampered_internal_file_is_no_go_and_never_executes_artifact(
        tmp_path, monkeypatch):
    artifact, receipt = _artifact_and_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    (artifact / "_internal" / "python312.dll").write_bytes(
        b"tampered-runtime")
    evidence = tmp_path / "acceptance"
    monkeypatch.setenv(go_no_go._ISOLATION_MARKER, str(tmp_path / "pycache"))
    monkeypatch.setattr(
        go_no_go, "_environment_preflight",
        lambda _cache: {
            "commit": receipt["commit"], "git_tree": receipt["git_tree"],
            "toolchain_attestation_sha256": "c" * 64})

    def forbidden_command(*_args, **_kwargs):
        raise AssertionError("artifact command ran after static mismatch")

    monkeypatch.setattr(go_no_go.GateRun, "command", forbidden_command)
    assert go_no_go._run_acceptance(
        _args(artifact, receipt_path, evidence)) == 1
    report = _acceptance(evidence)
    assert report["alpha_trial_verdict"] == "ALPHA-NO-GO"
    assert report["run_valid"] is True
    checks = {item["name"]: item for item in report["automated_checks"]}
    assert checks["static_artifact_binding"]["outcome"] == go_no_go.FAIL
    for name in ("dist_manifest", "window_harness_exe", "smoke_exe",
                 "performance_alpha_health", "stability_alpha",
                 "dist_manifest_final"):
        assert checks[name]["outcome"] == go_no_go.SKIP


def test_valid_receipt_for_different_head_is_no_go_with_evidence(
        tmp_path, monkeypatch):
    artifact, receipt = _artifact_and_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    evidence = tmp_path / "acceptance"
    monkeypatch.setenv(go_no_go._ISOLATION_MARKER, str(tmp_path / "pycache"))
    monkeypatch.setattr(
        go_no_go, "_environment_preflight",
        lambda _cache: {
            "commit": "f" * 40, "git_tree": "a" * 40,
            "toolchain_attestation_sha256": "c" * 64})
    monkeypatch.setattr(
        go_no_go.GateRun, "command",
        lambda *_args, **_kwargs: pytest.fail("artifact command must not run"))
    assert go_no_go._run_acceptance(
        _args(artifact, receipt_path, evidence)) == 1
    report = _acceptance(evidence)
    assert report["alpha_trial_verdict"] == "ALPHA-NO-GO"
    assert report["run_valid"] is True
    assert "does not describe current HEAD" in \
        report["automated_checks"][2]["detail"]


def test_invalid_receipt_still_writes_invalid_run_evidence(
        tmp_path, monkeypatch):
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("{}", encoding="utf-8")
    evidence = tmp_path / "acceptance"
    monkeypatch.setenv(go_no_go._ISOLATION_MARKER, str(tmp_path / "pycache"))
    assert go_no_go._run_acceptance(_args(
        tmp_path / "missing-artifact", receipt_path, evidence)) == 2
    report = _acceptance(evidence)
    assert report["alpha_trial_verdict"] == "INVALID_RUN"
    assert report["run_valid"] is False
    assert report["receipt"] is None
    assert report["supplied_receipt_observation"] == {
        "path": str(receipt_path.resolve()),
        "readable": True,
        "size": 2,
        "sha256": hashlib.sha256(b"{}").hexdigest(),
    }
    assert report["automated_checks"][0]["outcome"] == go_no_go.INVALID


def test_environment_preflight_failure_is_invalid_with_evidence(
        tmp_path, monkeypatch):
    artifact, receipt = _artifact_and_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    evidence = tmp_path / "acceptance"
    monkeypatch.setenv(go_no_go._ISOLATION_MARKER, str(tmp_path / "pycache"))

    def invalid_environment(_cache):
        raise go_no_go.GateError("toolchain does not match release lock")

    monkeypatch.setattr(
        go_no_go, "_environment_preflight", invalid_environment)
    assert go_no_go._run_acceptance(
        _args(artifact, receipt_path, evidence)) == 2
    report = _acceptance(evidence)
    assert report["alpha_trial_verdict"] == "INVALID_RUN"
    assert report["run_valid"] is False
    assert report["automated_checks"][1]["outcome"] == go_no_go.INVALID


def test_first_runtime_manifest_failure_skips_later_artifact_commands(
        tmp_path, monkeypatch):
    artifact, receipt = _artifact_and_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    evidence = tmp_path / "acceptance"
    monkeypatch.setenv(go_no_go._ISOLATION_MARKER, str(tmp_path / "pycache"))
    monkeypatch.setattr(
        go_no_go, "_environment_preflight",
        lambda _cache: {
            "commit": receipt["commit"], "git_tree": receipt["git_tree"],
            "toolchain_attestation_sha256": "c" * 64})
    invoked: list[str] = []

    def fail_first(self, name, *_args, **_kwargs):
        invoked.append(name)
        self.record(name, go_no_go.FAIL, "runtime identity failed")
        return go_no_go.FAIL

    monkeypatch.setattr(go_no_go.GateRun, "command", fail_first)
    assert go_no_go._run_acceptance(
        _args(artifact, receipt_path, evidence)) == 1
    assert invoked == ["dist_manifest"]
    report = _acceptance(evidence)
    checks = {item["name"]: item for item in report["automated_checks"]}
    for name in ("window_harness_exe", "smoke_exe",
                 "performance_alpha_health", "stability_alpha",
                 "dist_manifest_final"):
        assert checks[name]["outcome"] == go_no_go.SKIP


@pytest.mark.parametrize(("failed_gate", "expected_invoked"), [
    ("pytest_regular", ["dist_manifest", "pytest_regular"]),
    ("pytest_native", ["dist_manifest", "pytest_regular", "pytest_native"]),
    ("window_harness_source", [
        "dist_manifest", "pytest_regular", "pytest_native",
        "window_harness_source",
    ]),
])
def test_source_gates_fail_fast_before_further_candidate_execution(
        tmp_path, monkeypatch, failed_gate, expected_invoked):
    artifact, receipt = _artifact_and_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    evidence = tmp_path / "acceptance"
    monkeypatch.setenv(go_no_go._ISOLATION_MARKER, str(tmp_path / "pycache"))
    monkeypatch.setattr(
        go_no_go, "_environment_preflight",
        lambda _cache: {
            "commit": receipt["commit"], "git_tree": receipt["git_tree"],
            "toolchain_attestation_sha256": "c" * 64})
    invoked: list[str] = []

    def fail_selected(self, name, *_args, **_kwargs):
        invoked.append(name)
        outcome = go_no_go.FAIL if name == failed_gate else go_no_go.PASS
        self.record(name, outcome, "fixture outcome")
        return outcome

    monkeypatch.setattr(go_no_go.GateRun, "command", fail_selected)
    assert go_no_go._run_acceptance(
        _args(artifact, receipt_path, evidence)) == 1
    assert invoked == expected_invoked
    report = _acceptance(evidence)
    checks = {item["name"]: item for item in report["automated_checks"]}
    assert checks["window_harness_exe"]["outcome"] == go_no_go.SKIP
    assert checks["performance_alpha_health"]["outcome"] == go_no_go.SKIP
    assert checks["stability_alpha"]["outcome"] == go_no_go.SKIP


def test_dynamic_internal_error_is_finalized_as_invalid_evidence(
        tmp_path, monkeypatch):
    artifact, receipt = _artifact_and_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    evidence = tmp_path / "acceptance"
    monkeypatch.setenv(go_no_go._ISOLATION_MARKER, str(tmp_path / "pycache"))
    monkeypatch.setattr(
        go_no_go, "_environment_preflight",
        lambda _cache: {
            "commit": receipt["commit"], "git_tree": receipt["git_tree"],
            "toolchain_attestation_sha256": "c" * 64})

    def broken_command(*_args, **_kwargs):
        raise OSError("log directory became unavailable")

    monkeypatch.setattr(go_no_go.GateRun, "command", broken_command)
    assert go_no_go._run_acceptance(
        _args(artifact, receipt_path, evidence)) == 2
    report = _acceptance(evidence)
    assert report["alpha_trial_verdict"] == "INVALID_RUN"
    assert report["run_valid"] is False
    internal = [item for item in report["automated_checks"]
                if item["name"] == "acceptance_internal"]
    assert len(internal) == 1
    assert internal[0]["outcome"] == go_no_go.INVALID
    names = {item["name"] for item in report["automated_checks"]}
    assert set(go_no_go._ACCEPTANCE_GATE_ORDER).issubset(names)


def test_receipt_byte_drift_stops_later_candidate_execution(
        tmp_path, monkeypatch):
    artifact, receipt = _artifact_and_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    evidence = tmp_path / "acceptance"
    monkeypatch.setenv(go_no_go._ISOLATION_MARKER, str(tmp_path / "pycache"))
    monkeypatch.setattr(
        go_no_go, "_environment_preflight",
        lambda _cache: {
            "commit": receipt["commit"], "git_tree": receipt["git_tree"],
            "toolchain_attestation_sha256": "c" * 64})
    invoked: list[str] = []

    def mutate_after_stability(self, name, *_args, **_kwargs):
        invoked.append(name)
        self.record(name, go_no_go.PASS, "fixture pass")
        if name == "stability_alpha":
            receipt_path.write_text(
                json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return go_no_go.PASS

    monkeypatch.setattr(
        go_no_go.GateRun, "command", mutate_after_stability)
    assert go_no_go._run_acceptance(
        _args(artifact, receipt_path, evidence)) == 2
    report = _acceptance(evidence)
    checks = {item["name"]: item for item in report["automated_checks"]}
    assert checks["static_before_final_dist"]["outcome"] == go_no_go.INVALID
    assert checks["dist_manifest_final"]["outcome"] == go_no_go.SKIP
    assert checks["final_receipt_bytes"]["outcome"] == go_no_go.INVALID
    assert "dist_manifest_final" not in invoked


def test_artifact_is_rechecked_after_long_source_gates_before_exe_launch(
        tmp_path, monkeypatch):
    artifact, receipt = _artifact_and_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    evidence = tmp_path / "acceptance"
    monkeypatch.setenv(go_no_go._ISOLATION_MARKER, str(tmp_path / "pycache"))
    monkeypatch.setattr(
        go_no_go, "_environment_preflight",
        lambda _cache: {
            "commit": receipt["commit"], "git_tree": receipt["git_tree"],
            "toolchain_attestation_sha256": "c" * 64})
    invoked: list[str] = []

    def mutate_during_tests(self, name, *_args, **_kwargs):
        invoked.append(name)
        self.record(name, go_no_go.PASS, "fixture pass")
        if name == "pytest_regular":
            (artifact / "_internal" / "python312.dll").write_bytes(
                b"changed-after-runtime-gate")
        return go_no_go.PASS

    monkeypatch.setattr(
        go_no_go.GateRun, "command", mutate_during_tests)
    assert go_no_go._run_acceptance(
        _args(artifact, receipt_path, evidence)) == 1
    assert invoked == [
        "dist_manifest", "pytest_regular", "pytest_native",
        "window_harness_source",
    ]
    report = _acceptance(evidence)
    checks = {item["name"]: item for item in report["automated_checks"]}
    assert checks["static_before_window_exe"]["outcome"] == go_no_go.FAIL
    assert checks["window_harness_exe"]["outcome"] == go_no_go.SKIP
    assert checks["smoke_exe"]["outcome"] == go_no_go.SKIP
    assert checks["performance_alpha_health"]["outcome"] == go_no_go.SKIP
    assert checks["stability_alpha"]["outcome"] == go_no_go.SKIP
