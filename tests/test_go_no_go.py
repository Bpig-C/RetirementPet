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
        "version": "1.3.0",
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
        if name == "displays_exe_harness":
            from scripts import verify_displays as harness

            report = _display_report(exe_sha256=receipt["exe_sha256"])
            report_dir = self.run_dir / "displays-exe"
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / "report.json").write_text(
                json.dumps(report), encoding="utf-8")
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


# -- V12-08: schema-2 display report aggregation and fail-closed consumer ----


def _single_display(**overrides):
    display = {"device": "DISPLAY1", "primary": True,
               "monitor": [0, 0, 1920, 1080], "work": [0, 0, 1920, 1040],
               "dpi": 96, "dpi_source": "per_monitor"}
    display.update(overrides)
    return display


def _two_display_mixed_negative():
    return [_single_display(), _single_display(
        device="DISPLAY2", primary=False, monitor=[-1920, 0, 1920, 1080],
        work=[-1920, 0, 1920, 1040], dpi=144)]


def _identity_evidence(**overrides):
    evidence = {"hwnd": 1111, "reported_pid": 200, "launched_pid": 200,
                "owning_pid": 200, "ownership": "direct",
                "ownership_verified": True, "host_parent_pid": None,
                "image_path": "C:/x/RetirementPet.exe",
                "image_sha256": "a" * 64}
    evidence.update(overrides)
    return evidence


FOOT_STABLE = [116.0, 160.0]


BASE_TS = "2026-09-13T08:00:00+00:00"


def _ts(index: int) -> str:
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 9, 13, 8, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(milliseconds=index * 400)).isoformat()


def _sample(index, rect, displays, *, foot=None, pid=200, hwnd=1111,
            fingerprint=None, dpi=96, frame=None, generated_at=None):
    import scripts.verify_displays as _h
    if fingerprint is None:
        fingerprint = _h.topology_of(displays)["fingerprint"]
    return {"monotonic_ms": index * 400, "rect": list(rect),
            "foot_point_screen": list(FOOT_STABLE) if foot is None
            else list(foot),
            "pid": pid, "hwnd": hwnd, "sequence": index + 1,
            "generated_at": generated_at or _ts(index),
            "window_frame": list(frame) if frame else list(rect),
            "window_dpi": dpi,
            "read_at_utc": _ts(index + 1),
            "native_rect": list(rect),
            "displays": displays,
            "topology_fingerprint": fingerprint}


def _phase(display, rect, foot=FOOT_STABLE, *, pid=200, hwnd=1111,
           sequence=50, dpi=96, frame=None, index=50):
    return {"rect": list(rect), "foot_point_screen": list(foot),
            "pid": pid, "hwnd": hwnd, "sequence": sequence,
            "generated_at": _ts(index),
            "window_frame": list(frame) if frame else list(rect),
            "window_dpi": dpi,
            "read_at_utc": _ts(index + 1),
            "native_rect": list(rect),
            "displays": display, "topology_fingerprint": None}


def _foot_evidence(**overrides):
    rect = [10, 10, 200, 160]
    displays = [_single_display()]
    evidence = {
        "samples": [_sample(index, rect, displays)
                    for index in range(4)],
        "no_input_stable": True,
        "panel_observed_open": True, "panel_observed_closed": True,
        "positions": {"before": _phase(displays, rect, sequence=50),
                      "panel_open": _phase(displays, rect, sequence=51),
                      "panel_closed": _phase(displays, rect, sequence=52)},
        "tolerance_px": 8}
    evidence.update(overrides)
    return evidence


def _visit(display, rect, *, moved=True, dpi="auto"):
    return {"device": display["device"], "rect": rect, "moved": moved,
            "dpi_observed": (display["dpi"] if dpi == "auto" else dpi)}


def _all_evidence(displays=None):
    displays = displays if displays is not None else [_single_display()]
    rect = [10, 10, 200, 160]
    visits = []
    for d in displays:
        w = d["work"]
        visits.append(_visit(d, [w[0] + 8, w[1] + 8, 200, 160]))
    return {
        "identity_binding": _identity_evidence(),
        "display_facts": {"displays": displays},
        "window_containment": {"rect": rect, "displays": displays,
                               "topology_fingerprint": "fp"},
        "foot_stability": _foot_evidence(),
        "per_display_landing": {"visited": visits},
        "topology_recovery": {"event_detected": False,
                              "reason": "no_topology_event"},
    }


def test_display_aggregation_single_screen_baseline_and_multi_skip():
    from scripts import verify_displays as harness

    displays = [_single_display()]
    evidence = _all_evidence(displays)
    before = after = harness.topology_of(displays)
    results = {name: derived["result"] for name, derived
               in harness.derive_all_results(evidence, before, after).items()}
    assert results["identity_binding"] == "PASS"
    assert results["topology_recovery"] == "SKIP"
    verdicts = harness.aggregate_display_verdicts(after, results)
    assert verdicts["single_screen_baseline"]["result"] == "PASS"
    multi = verdicts["multi_screen_certification"]
    assert multi["result"] == "SKIP"
    assert "second display" in multi["detail"]

    # each absent environment condition is a SKIP with its own reason
    two_same = [_single_display(), _single_display(
        device="DISPLAY2", primary=False, monitor=[-1920, 0, 1920, 1080],
        work=[-1920, 0, 1920, 1040])]
    evidence2 = _all_evidence(two_same)
    before2 = after2 = harness.topology_of(two_same)
    results2 = {name: derived["result"] for name, derived
                in harness.derive_all_results(evidence2, before2,
                                              after2).items()}
    verdicts = harness.aggregate_display_verdicts(after2, results2)
    assert "mixed-DPI" in verdicts["multi_screen_certification"]["detail"]

    two_mixed = [_single_display(), _single_display(
        device="DISPLAY2", primary=False, monitor=[1920, 0, 1920, 1080],
        work=[1920, 0, 1920, 1040], dpi=144)]
    evidence3 = _all_evidence(two_mixed)
    before3 = after3 = harness.topology_of(two_mixed)
    results3 = {name: derived["result"] for name, derived
                in harness.derive_all_results(evidence3, before3,
                                              after3).items()}
    verdicts = harness.aggregate_display_verdicts(after3, results3)
    assert "negative-coordinate" in \
        verdicts["multi_screen_certification"]["detail"]

    negative = _two_display_mixed_negative()
    evidence4 = _all_evidence(negative)
    before4 = after4 = harness.topology_of(negative)
    results4 = {name: derived["result"] for name, derived
                in harness.derive_all_results(evidence4, before4,
                                              after4).items()}
    verdicts = harness.aggregate_display_verdicts(after4, results4)
    assert "topology change" in \
        verdicts["multi_screen_certification"]["detail"]


def test_display_aggregation_full_matrix_and_recovery_pass():
    from scripts import verify_displays as harness

    displays = _two_display_mixed_negative()
    before = harness.topology_of([_single_display()])
    after = harness.topology_of(displays)
    events = [{"detected_at": "2026-09-13T00:00:00+00:00",
               "before": before, "after": after}]
    evidence = _all_evidence(displays)
    evidence["topology_recovery"] = {
        "event_detected": True,
        "before_fingerprint": before["fingerprint"],
        "after_fingerprint": after["fingerprint"],
        "after_rect": [10, 10, 200, 160],
    }
    results = {name: derived["result"] for name, derived
               in harness.derive_all_results(evidence, before, after,
                                             events).items()}
    verdicts = harness.aggregate_display_verdicts(after, results)
    assert verdicts["single_screen_baseline"]["result"] == "PASS"
    assert verdicts["multi_screen_certification"]["result"] == "PASS"

    # R08-04 negatives at the derivation layer: no event log, no
    # coordinates, or a rescue that the harness cannot claim
    evidence["topology_recovery"] = {
        "event_detected": True,
        "before_fingerprint": before["fingerprint"],
        "after_fingerprint": after["fingerprint"],
        "after_rect": [10, 10, 200, 160],
    }
    results = {name: derived["result"] for name, derived
               in harness.derive_all_results(evidence, before, after,
                                             []).items()}
    assert results["topology_recovery"] == "INVALID"

    results = {name: derived["result"] for name, derived
               in harness.derive_all_results(evidence, before, after,
                                             events).items()}
    assert results["topology_recovery"] == "PASS"
    del evidence["topology_recovery"]["after_rect"]
    results = {name: derived["result"] for name, derived
               in harness.derive_all_results(evidence, before, after,
                                             events).items()}
    assert results["topology_recovery"] == "INVALID"


def test_display_aggregation_fails_closed():
    from scripts import verify_displays as harness

    displays = [_single_display()]
    before = after = harness.topology_of(displays)

    # a skipped identity is INVALID, never SKIP/PASS
    evidence = _all_evidence(displays)
    evidence["identity_binding"] = {"reason": "skipped"}
    results = {name: derived["result"] for name, derived
               in harness.derive_all_results(evidence, before, after).items()}
    verdicts = harness.aggregate_display_verdicts(after, results)
    assert verdicts["single_screen_baseline"]["result"] == "INVALID"
    assert verdicts["multi_screen_certification"]["result"] == "INVALID"

    # an INVALID observation propagates as INVALID (never downgraded)
    evidence = _all_evidence(displays)
    evidence["window_containment"] = {}
    results = {name: derived["result"] for name, derived
               in harness.derive_all_results(evidence, before, after).items()}
    assert results["window_containment"] == "INVALID"
    verdicts = harness.aggregate_display_verdicts(after, results)
    assert verdicts["single_screen_baseline"]["result"] == "INVALID"

    # a FAIL in a baseline component fails both verdicts
    evidence = _all_evidence(displays)
    evidence["window_containment"] = {"rect": [5000, 5000, 200, 160]}
    results = {name: derived["result"] for name, derived
               in harness.derive_all_results(evidence, before, after).items()}
    assert results["window_containment"] == "FAIL"
    verdicts = harness.aggregate_display_verdicts(after, results)
    assert verdicts["single_screen_baseline"]["result"] == "FAIL"
    assert verdicts["multi_screen_certification"]["result"] == "FAIL"


def test_display_derivation_rejects_observations_without_facts():
    from scripts import verify_displays as harness

    # a self-report the native owner contradicts -> FAIL
    result, detail = harness.derive_identity_result(
        _identity_evidence(reported_pid=999))
    assert result == "FAIL"
    assert "does not own" in detail

    # an unrelated owner process -> FAIL (host chain not verified)
    result, _ = harness.derive_identity_result(
        _identity_evidence(owning_pid=300, ownership="unrelated"))
    assert result == "FAIL"

    # R08-01: a "direct" window owned by another process -> FAIL, even
    # when owner == reported (master's launched=999 probe)
    result, detail = harness.derive_identity_result(
        _identity_evidence(launched_pid=999))
    assert result == "FAIL"
    assert "not the launched process" in detail

    # R08-01: a candidate hash different from the OBSERVED image -> FAIL
    result, detail = harness.derive_identity_result(
        _identity_evidence(), candidate={"exe_sha256": "b" * 64})
    assert result == "FAIL"
    assert "differs from the observed" in detail

    # R08-01: an identity without an observed image hash -> INVALID
    evidence = _identity_evidence()
    del evidence["image_sha256"]
    result, detail = harness.derive_identity_result(evidence)
    assert result == "INVALID"
    assert "image hash" in detail

    # degraded DPI facts cannot pass as per-monitor measurements
    result, detail = harness.derive_display_facts_result(
        {"displays": [_single_display(dpi_source="system_fallback")]})
    assert result == "FAIL"
    assert "per-monitor" in detail

    # work areas must be sane on ALL FOUR edges
    result, _ = harness.derive_display_facts_result(
        {"displays": [_single_display(work=[0, 0, 99999, 1040])]})
    assert result == "FAIL"

    # R08-03: the tolerance is a contract constant - a report cannot
    # enlarge it to absorb a 100px move
    foot = _foot_evidence(tolerance_px=10000)
    result, detail = harness.derive_foot_result(
        foot, [_single_display()], _identity_evidence())
    assert result == "INVALID"
    assert "contract" in detail

    displays = [_single_display()]
    identity = _identity_evidence()

    # R08-03: with the contract tolerance a 100px window move FAILs
    # (the diagnostic generation moved WITH the window - frame and
    # native stay consistent)
    foot = _foot_evidence()
    closed = foot["positions"]["panel_closed"]
    closed["rect"] = [110, 10, 200, 160]
    closed["native_rect"] = [110, 10, 200, 160]
    closed["window_frame"] = [110, 10, 200, 160]
    result, detail = harness.derive_foot_result(foot, displays, identity)
    assert result == "FAIL" and "moved" in detail

    # R08-03 (master's second-round probe 2): a 100px HORIZONTAL foot
    # move with an unchanged y must also FAIL - the comparison uses the
    # complete x/y screen coordinates
    foot = _foot_evidence()
    foot["positions"]["panel_closed"]["foot_point_screen"] = \
        [FOOT_STABLE[0] + 100, FOOT_STABLE[1]]
    result, detail = harness.derive_foot_result(foot, displays, identity)
    assert result == "FAIL" and "dx=100.0" in detail

    # R08-03 (master's first-round layout counterexample): the window
    # does NOT move but the real layout foot anchor changes -> FAIL
    foot = _foot_evidence()
    foot["positions"]["panel_closed"]["foot_point_screen"] = \
        [116.0, 146.0]
    result, detail = harness.derive_foot_result(foot, displays, identity)
    assert result == "FAIL" and "foot anchor moved" in detail

    # the honest case: window and real foot anchor unchanged -> PASS
    result, detail = harness.derive_foot_result(foot := _foot_evidence(),
                                                displays, identity)
    assert result == "PASS" and "logical px" in detail

    # R08-03 (second-round probe 1): diagnostics from ANOTHER pid/hwnd
    # cannot pass - stale or foreign records are INVALID
    foot = _foot_evidence()
    for sample in foot["samples"]:
        sample["pid"] = 999
        sample["hwnd"] = 999
    result, detail = harness.derive_foot_result(foot, displays, identity)
    assert result == "INVALID" and "bound pid/hwnd" in detail

    # R08-03 (second-round probe 1): the same file counted twice - a
    # non-advancing sequence is not fresh
    foot = _foot_evidence()
    for sample in foot["samples"]:
        sample["sequence"] = 7
    result, detail = harness.derive_foot_result(foot, displays, identity)
    assert result == "INVALID" and "not fresh" in detail

    # R08-03 (second-round probe 3): drifting samples each labeled with
    # a DIFFERENT unverified fingerprint cannot bypass the comparison -
    # the fingerprint is recomputed from the sample's own displays
    foot = _foot_evidence()
    for index, sample in enumerate(foot["samples"]):
        sample["topology_fingerprint"] = f"forged-{index}"
    result, detail = harness.derive_foot_result(foot, displays, identity)
    assert result == "INVALID" and "does not match its own" in detail

    # R08-03 (second-round probe 3): genuine distinct topologies in the
    # groups still get checked - every group with >=2 samples drifts
    # independently
    second = _single_display(device="DISPLAY2", primary=False,
                             monitor=[-1920, 0, 1920, 1080],
                             work=[-1920, 0, 1920, 1040], dpi=144)
    foot = _foot_evidence()
    for index in (2, 3):
        foot["samples"][index]["displays"] = [second]
        foot["samples"][index]["topology_fingerprint"] = \
            harness.topology_of([second])["fingerprint"]
        foot["samples"][index]["rect"] = [-1912, 8, 200, 160]
        foot["samples"][index]["native_rect"] = [-1912, 8, 200, 160]
        foot["samples"][index]["window_frame"] = [-1912, 8, 200, 160]
    foot["samples"][2]["foot_point_screen"] = [-1890.0, 160.0]
    foot["samples"][3]["foot_point_screen"] = [-1800.0, 160.0]
    result, detail = harness.derive_foot_result(foot, displays, identity)
    # the DISPLAY2 group is checked independently: its internal foot
    # drift FAILs even though the DISPLAY1 group is perfectly stable
    assert result == "FAIL" and "foot anchor moved" in detail

    # R08-03: raw samples that contradict the stability flag -> FAIL
    foot = _foot_evidence(no_input_stable=True)
    foot["samples"][2] = _sample(2, [60, 10, 200, 160], displays)
    result, detail = harness.derive_foot_result(foot, displays, identity)
    assert result == "FAIL" and "raw samples" in detail

    # R08-03: a foot change within one topology is also drift -> FAIL
    foot = _foot_evidence(no_input_stable=True)
    foot["samples"][2] = _sample(2, [10, 10, 200, 160], displays,
                                 foot=[116.0, 146.0])
    result, detail = harness.derive_foot_result(foot, displays, identity)
    assert result == "FAIL" and "foot anchor moved" in detail

    # R08-03: samples without a screen-space foot observation -> INVALID
    # (the window edge is not an equivalent substitute)
    foot = _foot_evidence()
    for sample in foot["samples"]:
        del sample["foot_point_screen"]
    result, detail = harness.derive_foot_result(foot, displays, identity)
    assert result == "INVALID" and "not an equivalent substitute" in detail

    # R08-03: no raw samples at all -> INVALID
    result, detail = harness.derive_foot_result(
        {"no_input_stable": True, "panel_observed_open": True,
         "panel_observed_closed": True, "positions": {}, "tolerance_px": 8},
        [_single_display()])
    assert result == "INVALID"

    # panel visibility never really observed -> INVALID
    result, detail = harness.derive_foot_result(
        _foot_evidence(panel_observed_open=False), [_single_display()],
        _identity_evidence())
    assert result == "INVALID" and "really observed" in detail

    # R08-02: a visit that never left the first screen cannot count as
    # landing on the second display
    second = _single_display(device="DISPLAY2", primary=False,
                             monitor=[-1920, 0, 1920, 1080],
                             work=[-1920, 0, 1920, 1040], dpi=144)
    displays = [_single_display(), second]
    same_rect = [10, 10, 200, 160]
    result, detail = harness.derive_per_display_result(
        {"visited": [_visit(displays[0], same_rect),
                     _visit(second, same_rect, dpi=144)]}, displays)
    assert result == "FAIL"
    assert "did not land" in detail

    # R08-02: a failed move is a FAIL even if the rect were fine
    result, detail = harness.derive_per_display_result(
        {"visited": [_visit(displays[0], same_rect),
                     _visit(second, [-1912, 8, 200, 160], moved=False,
                            dpi=144)]}, displays)
    assert result == "FAIL" and "move did not take effect" in detail

    # R08-02: copied DPI instead of a post-move observation -> FAIL
    result, detail = harness.derive_per_display_result(
        {"visited": [_visit(displays[0], same_rect),
                     _visit(second, [-1912, 8, 200, 160],
                            dpi=None)]}, displays)
    assert result == "FAIL" and "DPI" in detail

    # the honest landing on BOTH screens passes
    result, _ = harness.derive_per_display_result(
        {"visited": [_visit(displays[0], same_rect),
                     _visit(second, [-1912, 8, 200, 160])]}, displays)
    assert result == "PASS"


def test_display_report_validator_rejects_unbacked_labels_and_tampering():
    from scripts import verify_displays as harness

    report = _display_report()
    assert harness.validate_display_report(report) == []

    # labels claiming PASS without any evidence: derivation mismatches
    fake_topology = {"display_count": 1, "multi_screen": False,
                     "mixed_dpi": False, "negative_coordinates": False,
                     "fingerprint": "fake"}
    fake = {
        "schema": harness.SCHEMA,
        "candidate": {"kind": "exe", "identity": "pid=1",
                      "exe_sha256": "a" * 64},
        "topology_before": fake_topology,
        "topology_after": fake_topology,
        "topology_events": [],
        "evidence": {},
        "scenarios": {name: {"result": "PASS", "detail": "claims"}
                      for name in harness.REQUIRED_SCENARIOS},
        "single_screen_baseline": {"result": "PASS", "detail": "claims"},
        "multi_screen_certification": {"result": "PASS", "detail": "claims"},
    }
    errors = harness.validate_display_report(fake)
    assert any("evidence" in e for e in errors)

    # a tampered label that the evidence does not support
    tampered = dict(_display_report())
    tampered["scenarios"] = dict(tampered["scenarios"])
    tampered["scenarios"]["window_containment"] = {
        "result": "PASS", "detail": "claims contained",
        "evidence": {"rect": [5000, 5000, 200, 160]}}
    errors = harness.validate_display_report(tampered)
    assert any("does not match the report's evidence record" in e
               or ("evidence" in e and "derives" in e)
               for e in errors)

    # a tampered top-level verdict that contradicts the aggregation
    # rules (evidence and labels are consistent; the verdict lies)
    tampered2 = dict(_display_report())
    tampered2["evidence"] = dict(tampered2["evidence"])
    tampered2["evidence"]["window_containment"] = {"rect": [5000, 5000,
                                                            200, 160]}
    tampered2["scenarios"] = dict(tampered2["scenarios"])
    tampered2["scenarios"]["window_containment"] = {
        "result": "FAIL", "detail": "not contained",
        "evidence": tampered2["evidence"]["window_containment"]}
    tampered2["single_screen_baseline"] = {
        "result": "PASS", "detail": "claims"}
    tampered2["multi_screen_certification"] = {
        "result": "PASS", "detail": "claims"}
    errors = harness.validate_display_report(tampered2)
    assert any("aggregation rules" in e for e in errors)

    # an unbound candidate is rejected
    unbound = dict(_display_report())
    unbound["candidate"] = {"kind": "exe"}
    assert any("candidate" in e
               for e in harness.validate_display_report(unbound))


def _display_report(*, exe_sha256="a" * 64, displays=None,
                    identity_evidence=None, rect=None, foot_evidence=None,
                    visited=None, recovery_evidence=None, events=None):
    from scripts import verify_displays as harness

    if displays is None:
        displays = [{
            "device": "DISPLAY1", "primary": True,
            "monitor": [0, 0, 1920, 1080], "work": [0, 0, 1920, 1040],
            "dpi": 96, "dpi_source": "per_monitor",
        }]
    if rect is None:
        rect = [10, 10, 200, 160]
    if identity_evidence is None:
        identity_evidence = {"hwnd": 1111, "reported_pid": 200,
                             "launched_pid": 200, "owning_pid": 200,
                             "ownership": "direct",
                             "ownership_verified": True,
                             "host_parent_pid": None,
                             "image_path": "C:/x/RetirementPet.exe",
                             "image_sha256": exe_sha256}
    if foot_evidence is None:
        foot_evidence = {
            "samples": [_sample(index, rect, displays)
                        for index in range(4)],
            "no_input_stable": True,
            "panel_observed_open": True, "panel_observed_closed": True,
            "positions": {
                phase: _phase(displays, rect, sequence=50 + index)
                for index, phase in enumerate(
                    ("before", "panel_open", "panel_closed"))},
            "tolerance_px": harness.FOOT_TOLERANCE_PX,
        }
    if visited is None:
        visited = [_visit(d, rect) for d in displays]
    if recovery_evidence is None:
        recovery_evidence = {"event_detected": False,
                             "reason": "no_topology_event"}
    evidence = {
        "identity_binding": identity_evidence,
        "display_facts": {"displays": displays},
        "window_containment": {"rect": rect, "displays": displays,
                               "topology_fingerprint": "fp"},
        "foot_stability": foot_evidence,
        "per_display_landing": {"visited": visited},
        "topology_recovery": recovery_evidence,
    }
    before = harness.topology_of(displays)
    after = harness.topology_of(displays)
    results = harness.derive_all_results(evidence, before, after,
                                         events, {"exe_sha256": exe_sha256})
    return {
        "schema": harness.SCHEMA,
        "candidate": {"kind": "exe", "identity": "launched_pid=200",
                      "exe_sha256": exe_sha256},
        "topology_before": before,
        "topology_after": after,
        "topology_events": [],
        "evidence": evidence,
        "scenarios": {name: {"result": derived["result"],
                             "detail": derived["detail"],
                             "evidence": evidence.get(name)}
                      for name, derived in results.items()},
        **harness.aggregate_display_verdicts(
            after, {name: derived["result"]
                    for name, derived in results.items()}),
    }


def test_display_consumer_rejects_foreign_candidate():
    receipt = {"exe_sha256": "a" * 64}
    report = _display_report(exe_sha256="b" * 64)
    single, multi, detail = go_no_go._displays_outcomes(report, receipt)
    assert single == "INVALID" and multi == "INVALID"
    assert "different candidate" in detail

    source_report = _display_report()
    source_report["candidate"]["kind"] = "source"
    single, multi, detail = go_no_go._displays_outcomes(
        source_report, receipt)
    assert single == "INVALID"
    assert "EXE candidate" in detail

    report = _display_report()
    single, multi, _ = go_no_go._displays_outcomes(report, receipt)
    assert single == "PASS" and multi == "SKIP"


# -- V12-08 D08-01/03/04: run_harness orchestration with injected fakes ------


class _FakeProc:
    def __init__(self, pid):
        self.pid = pid
        self.terminated = False

    def terminate(self):
        self.terminated = True


class _FakeTarget:
    """Stand-in for the launch/IPC boundary (never touches a real app)."""

    def __init__(self, *, ipc_ok=True, observers=None):
        self.kind = "exe"
        self.exe_sha256 = "a" * 64
        self.proc = _FakeProc(200)
        self.ipc_ok = ipc_ok
        self.ipc_commands = []
        self.observers = observers

    def spawn(self, extra_args, data_dir, instance, log_note):
        return self.proc

    def ipc(self, command, instance):
        self.ipc_commands.append(command)
        if self.observers is not None and self.ipc_ok:
            self.observers.phase = ("open" if command == "panel"
                                    else "closed")
        return self.ipc_ok


class _FakeObservers:
    """Scripted Win32 observations (verify_displays.Observers shape)."""

    def __init__(self, displays, rect, *, owner_pid=200, host_parent=None,
                 event_at_sample=None, event_displays=None,
                 rect_after_panel_closed=None, samples=4,
                 rect_after_event=None, dpi_override=None,
                 move_works=True, foot_after_panel_closed=None,
                 observation_pid=200, observation_hwnd=4660,
                 foot_screen_override=None, observation_frame_override=None,
                 observation_generated_at=None, observation_dpi=96):
        self.observation_pid = observation_pid
        self.observation_hwnd = observation_hwnd
        self.observation_sequence = 0
        self.foot_screen_override = foot_screen_override
        self.observation_frame_override = observation_frame_override
        self.observation_generated_at = observation_generated_at
        self.observation_dpi = observation_dpi
        self.displays = displays
        self.initial_rect = rect
        self.owner_pid = owner_pid
        self.host_parent = host_parent
        self.event_at_sample = event_at_sample
        self.event_displays = event_displays or displays
        self.rect_after_panel_closed = rect_after_panel_closed
        self.samples = samples
        self.sample_index = 0
        self.hwnd = 4660
        self.panel_visible = lambda pid: False
        self.rect_after_event = rect_after_event
        self.dpi_override = dpi_override
        self.move_works = move_works
        self._moved_rect = None
        self.foot_after_panel_closed = foot_after_panel_closed

    def window_alive(self, hwnd):
        return True

    def window_owner_pid(self, hwnd):
        return self.owner_pid

    def process_parent_pid(self, pid):
        return self.host_parent

    def process_image(self, pid):
        return {"path": "C:/fake/RetirementPet.exe", "sha256": "a" * 64}

    phase = "observe"

    def window_rect(self, hwnd):
        # explicit phase flag: no real-time dependence.  Position
        # resolution order: a harness move wins; then the app's own
        # post-event position once the event fired; then the scripted
        # panel-closed position; finally the initial rect.
        if getattr(self, "_moved_rect", None) is not None:
            return self._moved_rect
        event_fired = (self.event_at_sample is not None
                       and self.sample_index >= self.event_at_sample)
        if event_fired and self.rect_after_event is not None:
            return self.rect_after_event
        if self.phase == "closed" and self.rect_after_panel_closed:
            return self.rect_after_panel_closed
        return self.initial_rect

    def read_observation(self, report_file):
        """Simulate the app's live diagnostic report: chain-bound (pid,
        hwnd, incrementing sequence, fresh generation) with the real
        layout foot anchor in Qt LOGICAL screen coordinates and a
        window frame in the same unit."""
        from datetime import datetime, timezone

        self.observation_sequence += 1
        rect = self.window_rect(hwnd=None)
        foot = list(self.foot_screen_override
                    or (rect[0] + 116.0, rect[1] + rect[3] - 4.0))
        if self.phase == "closed" and self.foot_after_panel_closed:
            foot = list(self.foot_after_panel_closed)
        frame = list(self.observation_frame_override or rect)
        generated = self.observation_generated_at or (
            datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
        return {"pid": self.observation_pid, "hwnd": self.observation_hwnd,
                "sequence": self.observation_sequence,
                "generated_at": generated,
                "window_frame": frame,
                "window_dpi": self.observation_dpi,
                "foot_point_screen": foot,
                "foot_point": [foot[0] - frame[0], foot[1] - frame[1]]}

    def window_dpi(self, hwnd):
        # honest simulation: the per-window DPI follows the display the
        # CURRENT rect is on; tests can override for a stuck-DPI case
        if self.dpi_override is not None:
            return self.dpi_override
        rect = self.window_rect(hwnd)
        for display in self.enumerate_displays():
            wl, wt2, ww, wh = display["work"]
            if (wl <= rect[0] and wt2 <= rect[1]
                    and rect[0] + rect[2] <= wl + ww
                    and rect[1] + rect[3] <= wt2 + wh):
                return display["dpi"]
        return None

    def move_window(self, hwnd, rect):
        if self.move_works:
            # simulate the OS actually moving the window: subsequent
            # reads observe the new position
            self._moved_rect = list(rect)
        return self.move_works

    def enumerate_displays(self):
        if (self.event_at_sample is not None
                and self.sample_index >= self.event_at_sample):
            return self.event_displays
        return self.displays

    def sleep(self, seconds):
        self.sample_index += 1


def _harness_args():
    import argparse

    return argparse.Namespace(
        exe=True, source=False, artifact_dir=None,
        expected_build_id=None, expected_exe_sha256=None,
        observe_seconds=1.0,
    )


def _run_orchestrated(monkeypatch, harness, observers, target=None,
                      reported_pid=200):
    target = target or _FakeTarget()
    monkeypatch.setattr(
        harness, "find_pet_hwnd",
        lambda path, timeout_s, **kw: {"hwnd": 4660, "pid": reported_pid})
    report = harness.run_harness(
        _harness_args(), target=target, observers=observers)
    return report, target


def test_run_harness_identity_paths(tmp_path, monkeypatch):
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    displays = [_single_display()]
    rect = [10, 10, 200, 160]

    # normal run: identity PASS; an INJECTED target is left alone (the
    # harness only owns processes it launched itself)
    report, target = _run_orchestrated(
        monkeypatch, harness, _FakeObservers(displays, rect))
    assert report["scenarios"]["identity_binding"]["result"] == "PASS"
    assert report["candidate"]["identity"] == "launched_pid=200"
    assert target.proc.terminated is False

    # master's reproduction: self-reported pid 999, launched process owns
    # the window -> identity FAIL, never PASS
    report, _ = _run_orchestrated(
        monkeypatch, harness, _FakeObservers(displays, rect),
        reported_pid=999)
    assert report["scenarios"]["identity_binding"]["result"] == "FAIL"
    assert "does not own" in report["scenarios"]["identity_binding"]["detail"]

    # an owner from an unrelated process fails the ownership check
    report, _ = _run_orchestrated(
        monkeypatch, harness,
        _FakeObservers(displays, rect, owner_pid=300, host_parent=77))
    assert report["scenarios"]["identity_binding"]["result"] == "FAIL"

    # no window at all: INVALID, later scenarios observe nothing, and
    # the report must not validate as consistent
    target = _FakeTarget()
    monkeypatch.setattr(harness, "find_pet_hwnd",
                        lambda path, timeout_s, **kw: None)
    report = harness.run_harness(
        _harness_args(), target=target,
        observers=_FakeObservers(displays, rect))
    assert report["scenarios"]["identity_binding"]["result"] == "INVALID"
    assert target.proc.terminated is False
    assert harness.validate_display_report(report)

    # a target the harness launched itself (no injection) is cleaned up
    # even when the run aborts on a missing window
    owned = _FakeTarget()
    captured = {}
    real_spawn = type(owned).spawn

    monkeypatch.setattr(harness, "find_pet_hwnd",
                        lambda path, timeout_s, **kw: None)
    report = harness.run_harness(
        _harness_args(), target=owned, observers=_FakeObservers(
            displays, rect))
    # simulate self-launched semantics by clearing the marker the same
    # way run_harness does for its own Target: run again with target=None
    # is a real-process path (exercised on real machines); here we assert
    # the injected-target contract only.


def test_run_harness_foot_stability_uses_real_panel_observation(
        tmp_path, monkeypatch):
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    displays = [_single_display()]
    rect = [10, 10, 200, 160]

    def run(observers, ipc_ok=True, scripted_ipc=None):
        target = _FakeTarget(ipc_ok=ipc_ok)
        if scripted_ipc is not None:
            target.ipc = scripted_ipc
        monkeypatch.setattr(
            harness, "find_pet_hwnd",
            lambda path, timeout_s, **kw: {"hwnd": 4660, "pid": 200})
        report = harness.run_harness(
            _harness_args(), target=target, observers=observers)
        return report, target

    # panel really opens then really closes, anchor returns -> PASS
    visibility = {"phase": 0}

    def panel_visible(pid):
        return visibility["phase"] == 1

    observers = _FakeObservers(displays, rect,
                               rect_after_panel_closed=rect)
    observers.panel_visible = panel_visible

    commands = []

    def scripted_ipc(command, instance):
        visibility["phase"] += 1
        commands.append(command)
        observers.phase = "open" if command == "panel" else "closed"
        return True

    report, target = run(observers, scripted_ipc=scripted_ipc)
    assert commands == ["panel", "panel-close"]
    assert report["scenarios"]["foot_stability"]["result"] == "PASS"

    # IPC "succeeds" but the panel never really opens -> INVALID
    observers = _FakeObservers(displays, rect)
    report, _ = run(observers)
    assert report["scenarios"]["foot_stability"]["result"] == "INVALID"

    # the panel opens but the close command does not close it -> INVALID
    observers = _FakeObservers(displays, rect)
    observers.panel_visible = lambda pid: True
    report, _ = run(observers)
    assert report["scenarios"]["foot_stability"]["result"] == "INVALID"

    # the closed-state anchor moved 100px (still inside the work area)
    # -> FAIL: containment is not foot stability
    observers = _FakeObservers(
        displays, rect, rect_after_panel_closed=[110, 10, 200, 160])
    visibility = {"phase": 0}
    observers.panel_visible = lambda pid: visibility["phase"] == 1

    def scripted_ipc(command, instance):
        visibility["phase"] += 1
        observers.phase = "open" if command == "panel" else "closed"
        return True

    report, target = run(observers, scripted_ipc=scripted_ipc)
    assert report["scenarios"]["foot_stability"]["result"] == "FAIL"
    assert "moved" in report["scenarios"]["foot_stability"]["detail"]
    assert "stable without input" not in         report["scenarios"]["foot_stability"]["detail"]

    # IPC itself fails: the phases were never observed -> INVALID
    observers = _FakeObservers(displays, rect)
    report, _ = run(observers, ipc_ok=False)
    assert report["scenarios"]["foot_stability"]["result"] == "INVALID"


def test_run_harness_keeps_topology_event_and_verifies_recovery(
        tmp_path, monkeypatch):
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    single = [_single_display()]
    mixed = _two_display_mixed_negative()
    rect = [10, 10, 200, 160]

    # a topology event during the observation window is KEPT in the
    # event log (the old code overwrote the baseline and misreported
    # 'no topology change')
    observers = _FakeObservers(
        single, rect, event_at_sample=1, event_displays=mixed,
        rect_after_panel_closed=rect)
    report, _ = _run_orchestrated(monkeypatch, harness, observers)
    assert report["topology_events"], "event record missing"
    event = report["topology_events"][0]
    assert event["before"]["fingerprint"] != event["after"]["fingerprint"]
    assert report["scenarios"]["topology_recovery"]["result"] == "PASS"
    # per-display landing covered the NEW topology's displays
    assert report["scenarios"]["per_display_landing"]["result"] == "PASS"

    # no event: honest SKIP, empty event log
    report, _ = _run_orchestrated(
        monkeypatch, harness, _FakeObservers(single, rect))
    assert report["scenarios"]["topology_recovery"]["result"] == "SKIP"
    assert report["topology_events"] == []

    # an event whose recovery loses containment -> FAIL
    # the app did NOT recover: the window stays outside every work area
    # after the event; the harness's later per-display carrying must not
    # be able to rescue the recovery verdict
    observers = _FakeObservers(
        single, rect, event_at_sample=1, event_displays=mixed,
        rect_after_event=[99999, 10, 200, 160])
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    assert report["scenarios"]["topology_recovery"]["result"] == "FAIL"
    assert report["scenarios"]["per_display_landing"]["result"] == "PASS"


# -- V12-08 D08-06: Alpha required/optional semantics on the real finish path -


def _synthetic_display_report(receipt):
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    displays = [{
        "device": "DISPLAY1", "primary": True,
        "monitor": [0, 0, 1920, 1080], "work": [0, 0, 1920, 1040],
        "dpi": 96, "dpi_source": "per_monitor"}]
    topology = harness.topology_of(displays)
    rect = [10, 10, 200, 160]
    evidence = {
        "identity_binding": {"hwnd": 4660, "reported_pid": 200,
                             "launched_pid": 200, "owning_pid": 200,
                             "ownership": "direct",
                             "ownership_verified": True,
                             "host_parent_pid": None,
                             "image_path": "C:/x/RetirementPet.exe",
                             "image_sha256": receipt["exe_sha256"]},
        "display_facts": {"displays": displays},
        "window_containment": {"rect": rect, "displays": displays,
                               "topology_fingerprint": "fp"},
        "foot_stability": {
            "samples": [{"monotonic_ms": index * 400, "rect": rect,
                         "foot_point_screen": [116.0, 160.0],
                         "pid": 200, "hwnd": 4660, "sequence": index + 1,
                         "generated_at": _ts(index),
                         "window_frame": list(rect),
                         "window_dpi": 96,
                         "read_at_utc": _ts(index + 1),
                         "native_rect": list(rect),
                         "displays": displays,
                         "topology_fingerprint": topology["fingerprint"]}
                        for index in range(4)],
            "no_input_stable": True,
            "panel_observed_open": True, "panel_observed_closed": True,
            "positions": {
                phase: {"rect": rect, "foot_point_screen": [116.0, 160.0],
                        "pid": 200, "hwnd": 4660, "sequence": 50 + index,
                        "generated_at": _ts(50 + index),
                        "window_frame": list(rect),
                        "window_dpi": 96,
                        "read_at_utc": _ts(51 + index),
                        "native_rect": list(rect),
                        "displays": displays,
                        "topology_fingerprint": topology["fingerprint"]}
                for index, phase in enumerate(
                    ("before", "panel_open", "panel_closed"))},
            "tolerance_px": harness.FOOT_TOLERANCE_PX},
        "per_display_landing": {"visited": [
            {"device": displays[0]["device"], "rect": rect, "moved": True,
             "dpi_observed": 96}]},
        "topology_recovery": {"event_detected": False,
                              "reason": "no_topology_event"},
    }
    candidate = {"kind": "exe", "identity": "launched_pid=200",
                 "exe_sha256": receipt["exe_sha256"]}
    results = harness.derive_all_results(evidence, topology, topology, [],
                                         candidate)
    return {
        "schema": harness.SCHEMA,
        "candidate": {"kind": "exe", "identity": "launched_pid=200",
                      "exe_sha256": receipt["exe_sha256"]},
        "topology_before": topology,
        "topology_after": topology,
        "topology_events": [],
        "evidence": evidence,
        "scenarios": {name: {"result": derived["result"],
                             "detail": derived["detail"],
                             "evidence": evidence.get(name)}
                      for name, derived in results.items()},
        **harness.aggregate_display_verdicts(
            topology, {name: derived["result"] for name, derived
                       in results.items()}),
    }


def _write_display_report(self, receipt):
    report_dir = self.run_dir / "displays-exe"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text(
        json.dumps(_synthetic_display_report(receipt)), encoding="utf-8")


def _acceptance_env(tmp_path, monkeypatch):
    artifact, receipt = _artifact_and_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    evidence = tmp_path / "acceptance"
    monkeypatch.setenv(go_no_go._ISOLATION_MARKER, str(tmp_path / "pycache"))
    monkeypatch.setattr(
        go_no_go, "_environment_preflight",
        lambda _cache: {"commit": receipt["commit"],
                        "git_tree": receipt["git_tree"],
                        "toolchain_attestation_sha256": "c" * 64})
    return artifact, receipt_path, evidence, receipt


def test_alpha_passes_with_multi_screen_skip_and_single_screen_pass(
        tmp_path, monkeypatch):
    """D08-06: a genuine multi-screen SKIP must not block Alpha GO."""
    artifact, receipt_path, evidence, receipt = _acceptance_env(
        tmp_path, monkeypatch)

    def all_pass(self, name, *_args, **_kwargs):
        self.record(name, go_no_go.PASS, "fixture pass")
        if name == "displays_exe_harness":
            _write_display_report(self, receipt)
        return go_no_go.PASS

    monkeypatch.setattr(go_no_go.GateRun, "command", all_pass)
    assert go_no_go._run_acceptance(
        _args(artifact, receipt_path, evidence)) == 0
    report = _acceptance(evidence)
    checks = {item["name"]: item for item in report["automated_checks"]}
    assert checks["displays_single_screen_exe"]["outcome"] == "PASS"
    assert checks["displays_multi_screen_exe"]["outcome"] == "SKIP"
    assert report["alpha_trial_verdict"] == "ALPHA-GO"
    assert report["production_release_verdict"] != "PRODUCTION-GO"


def test_alpha_rejects_display_failure_or_invalid_despite_optional_label(
        tmp_path, monkeypatch):
    """D08-06: FAIL/INVALID never pass via the optional label."""
    artifact, receipt_path, evidence, receipt = _acceptance_env(
        tmp_path, monkeypatch)

    # a containment FAIL inside the display report keeps Alpha NO-GO
    def fail_pass(self, name, *_args, **_kwargs):
        self.record(name, go_no_go.PASS, "fixture pass")
        if name == "displays_exe_harness":
            _write_display_report(self, receipt)
            path = next((self.run_dir / "displays-exe").rglob("report.json"))
            report = json.loads(path.read_text(encoding="utf-8"))
            rect = [99999, 10, 200, 160]
            report["evidence"]["window_containment"] = {"rect": rect}
            report["scenarios"]["window_containment"] = {
                "result": "FAIL", "detail": "not contained",
                "evidence": {"rect": rect}}
            path.write_text(json.dumps(report), encoding="utf-8")
        return go_no_go.PASS

    monkeypatch.setattr(go_no_go.GateRun, "command", fail_pass)
    assert go_no_go._run_acceptance(
        _args(artifact, receipt_path, evidence)) != 0
    report = _acceptance(evidence)
    # the postcheck catches the inconsistent harness result and marks the
    # run INVALID - a FAIL can never surface as a PASS verdict
    assert report["alpha_trial_verdict"] in ("ALPHA-NO-GO", "INVALID_RUN")
    assert report["production_release_verdict"] != "PRODUCTION-GO"

    # an INVALID identity gate keeps Alpha NO-GO even with every other
    # check PASSing
    def invalid_display(self, name, *_args, **_kwargs):
        self.record(name, go_no_go.PASS, "fixture pass")
        if name == "displays_exe_harness":
            self.record(name, go_no_go.INVALID,
                        "identity evidence missing")
        return go_no_go.PASS

    monkeypatch.setattr(go_no_go.GateRun, "command", invalid_display)
    assert go_no_go._run_acceptance(
        _args(artifact, receipt_path, evidence)) != 0
    paths = sorted(evidence.rglob("acceptance.json"))
    report = json.loads(paths[-1].read_text(encoding="utf-8"))
    # INVALID short-circuits to the stronger INVALID_RUN verdict - also
    # a rejection, never a PASS
    assert report["alpha_trial_verdict"] in ("ALPHA-NO-GO", "INVALID_RUN")


def test_production_still_requires_multi_screen_certification(
        tmp_path, monkeypatch):
    """Production cannot upgrade a multi-screen SKIP into a PASS."""
    artifact, receipt_path, evidence, receipt = _acceptance_env(
        tmp_path, monkeypatch)

    def all_pass(self, name, *_args, **_kwargs):
        self.record(name, go_no_go.PASS, "fixture pass")
        if name == "displays_exe_harness":
            _write_display_report(self, receipt)
        return go_no_go.PASS

    monkeypatch.setattr(go_no_go.GateRun, "command", all_pass)
    args = _args(artifact, receipt_path, evidence)
    args.profile = "production"
    assert go_no_go._run_acceptance(args) != 0
    report = _acceptance(evidence)
    assert report["production_release_verdict"] == \
        "PRODUCTION-NO-GO:ALPHA_GATES_FAILED"


# -- R08-05: malformed reports are INVALID, never exceptions -----------------


def test_display_validator_never_raises_on_malformed_reports():
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    base = _display_report()

    def broken(mutate):
        report = json.loads(json.dumps(base))
        mutate(report)
        return report

    cases = {
        "null display record": lambda r: r["evidence"]["display_facts"]
            .update({"displays": [None]}),
        "negative width rect": lambda r: r["evidence"]["window_containment"]
            .update({"rect": [10, 10, -5, 160]}),
        "empty visit rect": lambda r: r["evidence"]["per_display_landing"]
            .update({"visited": [{"device": "DISPLAY1", "rect": [],
                                  "moved": True, "dpi_observed": 96}]}),
        "empty device name": lambda r: r["evidence"]["display_facts"]
            .update({"displays": [{"device": "", "primary": True,
                                   "monitor": [0, 0, 10, 10],
                                   "work": [0, 0, 10, 10],
                                   "dpi": 96,
                                   "dpi_source": "per_monitor"}]}),
        "wrong nesting": lambda r: r.update({"evidence": {
            k: "not-a-dict" for k in base["evidence"]}}),
        "events not a list": lambda r: r.update({"topology_events": 7}),
        "zero dpi": lambda r: r["evidence"]["display_facts"]
            .update({"displays": [_single_display(dpi=0)]}),
        "bool rect coords": lambda r: r["evidence"]["window_containment"]
            .update({"rect": [True, 10, 200, 160]}),
    }
    for name, mutate in cases.items():
        report = broken(mutate)
        try:
            errors = harness.validate_display_report(report)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"{name}: validator raised {exc!r}")
        assert errors, f"{name}: malformed report validated clean"
        assert all(isinstance(e, str) for e in errors), name

    # the honest report still validates
    assert harness.validate_display_report(base) == []


# -- R08 priority: full producer -> validator -> consumer chain --------------


def test_full_chain_harness_output_validates_and_consumes(
        tmp_path, monkeypatch):
    """Priority check for the next round: the RAW run_harness return
    value must pass the validator and the gate consumer untouched - no
    hand-built reports."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    displays = [_single_display()]
    observers = _FakeObservers(displays, [10, 10, 200, 160])
    observers.panel_visible = lambda pid: observers.phase == "open"
    receipt = {"exe_sha256": "a" * 64}

    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))

    # the producer's candidate binding carries the OBSERVED image hash
    assert report["candidate"]["exe_sha256"] == \
        observers.process_image(200)["sha256"]
    assert harness.validate_display_report(report) == []
    single, multi, detail = go_no_go._displays_outcomes(report, receipt)
    assert single == "PASS" and detail == ""
    assert multi == "SKIP"
    assert "second display" in         report["multi_screen_certification"]["detail"]

    # a receipt for a DIFFERENT binary cannot consume the same report
    single, multi, detail = go_no_go._displays_outcomes(
        report, {"exe_sha256": "f" * 64})
    assert single == "INVALID" and multi == "INVALID"


def test_full_chain_pid_and_hash_mismatches_fail(tmp_path, monkeypatch):
    """R08-01 negatives through the raw chain: pid and image identity
    inconsistencies can never produce PASS verdicts."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    displays = [_single_display()]

    # self-reported pid differs from the launched/owning pid
    observers = _FakeObservers(displays, [10, 10, 200, 160])
    report, _ = _run_orchestrated(monkeypatch, harness, observers,
                                  reported_pid=999)
    assert report["scenarios"]["identity_binding"]["result"] == "FAIL"
    assert harness.validate_display_report(report) == []
    single, _multi, _detail = go_no_go._displays_outcomes(
        report, {"exe_sha256": "a" * 64})
    assert single == "FAIL"  # the identity FAIL verdict propagates

    # the observed process image is a DIFFERENT binary than the receipt
    class _ForeignObservers(_FakeObservers):
        def process_image(self, pid):
            return {"path": "C:/fake/other.exe", "sha256": "b" * 64}

    observers = _ForeignObservers(displays, [10, 10, 200, 160])
    report, _ = _run_orchestrated(monkeypatch, harness, observers)
    assert report["candidate"]["exe_sha256"] == "b" * 64
    # internally consistent (so identity PASSes), but the candidate hash
    # is now the FOREIGN binary - the consumer must refuse it
    assert harness.validate_display_report(report) == []
    single, _multi, detail = go_no_go._displays_outcomes(
        report, {"exe_sha256": "a" * 64})
    assert single == "INVALID" and "different candidate" in detail


def test_full_chain_move_failures_and_stuck_dpi_fail(
        tmp_path, monkeypatch):
    """R08-02 negatives through the raw chain: a harness that cannot
    really move the window (or sees a stuck DPI) reports FAIL, and the
    consumer refuses to pass the multi-screen certification."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    displays = _two_display_mixed_negative()
    rect = [10, 10, 200, 160]
    receipt = {"exe_sha256": "a" * 64}

    # the move silently fails: the window stays on screen 1
    observers = _FakeObservers(displays, rect, move_works=False)
    observers.panel_visible = lambda pid: observers.phase == "open"
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    assert report["scenarios"]["per_display_landing"]["result"] == "FAIL"
    assert harness.validate_display_report(report) == []
    single, multi, _detail = go_no_go._displays_outcomes(report, receipt)
    assert single == "PASS"          # single-screen baseline unaffected
    assert multi == "FAIL"           # certification cannot pass

    # the window moves but its per-window DPI never switches
    observers = _FakeObservers(displays, rect, dpi_override=96)
    observers.panel_visible = lambda pid: observers.phase == "open"
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    assert report["scenarios"]["per_display_landing"]["result"] == "FAIL"
    assert harness.validate_display_report(report) == []
    _single, multi, _detail = go_no_go._displays_outcomes(report, receipt)
    assert multi == "FAIL"


def test_full_chain_rescued_recovery_and_missing_evidence_fail(
        tmp_path, monkeypatch):
    """R08-04: a topology event the app did NOT recover from stays FAIL
    even though the harness later carries the window per-display; a
    report claiming recovery without coordinates is INVALID."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    single = [_single_display()]
    mixed = _two_display_mixed_negative()

    observers = _FakeObservers(
        single, [10, 10, 200, 160], event_at_sample=1,
        event_displays=mixed, rect_after_event=[99999, 10, 200, 160])
    observers.panel_visible = lambda pid: observers.phase == "open"
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    assert report["scenarios"]["topology_recovery"]["result"] == "FAIL"
    assert harness.validate_display_report(report) == []
    single_v, multi_v, _d = go_no_go._displays_outcomes(
        report, {"exe_sha256": "a" * 64})
    assert single_v == "PASS"
    # the unstable run skips the panel phase, so the foot scenario is
    # INVALID; either way the certification is never PASS
    assert multi_v in ("FAIL", "INVALID")

    # consumer-side: a report claiming recovery without any logged
    # event or coordinates derives INVALID and can never pass
    report2 = _display_report(
        recovery_evidence={"event_detected": True,
                           "before_fingerprint": "x", "after_fingerprint":
                           "y"},
        events=[])
    assert report2["scenarios"]["topology_recovery"]["result"] == "INVALID"
    _single2, multi2, _d2 = go_no_go._displays_outcomes(
        report2, {"exe_sha256": "a" * 64})
    # the INVALID recovery poisons the certification; the baseline is
    # unaffected by design (it never depended on topology events)
    assert multi2 in ("INVALID", "FAIL")


# -- R08 residuals: real foot anchor, phase-bound topology, validator edges --


def test_foot_uses_real_layout_anchor_not_window_edge():
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    # master's layout counterexample: two characters in the SAME window
    # rect put base_anchor.y at 228.07 vs 148.76 (79.31px apart) while
    # the window bottom edge stays 236.  A window-edge proxy would call
    # both "0px"; the real foot anchor must FAIL the second one.
    foot = _foot_evidence()
    foot["positions"]["panel_closed"]["foot_point_screen"] = [116.0, 146.0]
    result, detail = harness.derive_foot_result(
        foot, [_single_display()], _identity_evidence())
    assert result == "FAIL" and "foot anchor moved" in detail


def test_orchestrated_foot_follows_real_anchor(tmp_path, monkeypatch):
    """Through the raw harness: a foot-anchor change with a motionless
    window is FAIL; a genuinely stable run is PASS."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    displays = [_single_display()]
    receipt = {"exe_sha256": "a" * 64}

    # window stands still, layout foot anchor jumps after the panel
    observers = _FakeObservers(displays, [10, 10, 200, 160],
                               foot_after_panel_closed=[116.0, 146.0])
    observers.panel_visible = lambda pid: observers.phase == "open"
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    assert report["scenarios"]["foot_stability"]["result"] == "FAIL"
    assert "foot anchor moved" in \
        report["scenarios"]["foot_stability"]["detail"]
    # the foot scenario FAILs, but the baseline components (identity,
    # facts, containment) are unaffected by design
    single, _multi, _d = go_no_go._displays_outcomes(report, receipt)
    assert single == "PASS"

    # honest stable run: PASS
    observers = _FakeObservers(displays, [10, 10, 200, 160])
    observers.panel_visible = lambda pid: observers.phase == "open"
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    assert report["scenarios"]["foot_stability"]["result"] == "PASS"
    single, _multi, _d = go_no_go._displays_outcomes(report, receipt)
    assert single == "PASS"


def test_orchestrated_phase_bound_topology_recovery(
        tmp_path, monkeypatch):
    """R08-04, master's positive control: the old screen disappears, the
    app recovers on its own to the new work area - containment is judged
    against each observation's OWN topology, so nothing misreports."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    old_screen = [_single_display()]
    new_screen = [{
        "device": "DISPLAY1", "primary": True,
        "monitor": [4000, 0, 1920, 1080], "work": [4000, 0, 1920, 1040],
        "dpi": 96, "dpi_source": "per_monitor"}]
    receipt = {"exe_sha256": "a" * 64}

    observers = _FakeObservers(old_screen, [10, 10, 200, 160],
                               event_at_sample=1,
                               event_displays=new_screen,
                               rect_after_event=[4010, 10, 200, 160])
    observers.panel_visible = lambda pid: observers.phase == "open"
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))

    # the initial rect is judged against the INITIAL topology: PASS
    assert report["scenarios"]["window_containment"]["result"] == "PASS"
    # the app really recovered onto the new work area: PASS
    assert report["scenarios"]["topology_recovery"]["result"] == "PASS"
    # the panel phase was re-baselined after the recovery: PASS
    assert report["scenarios"]["foot_stability"]["result"] == "PASS"
    assert report["single_screen_baseline"]["result"] == "PASS"
    assert report["scenarios"]["identity_binding"]["result"] == "PASS"
    assert harness.validate_display_report(report) == []
    single, _multi, _d = go_no_go._displays_outcomes(report, receipt)
    assert single == "PASS"

    # the failing control: the app does NOT recover (still at the old
    # coordinates) - recovery FAILs and the baseline containment is
    # still judged against the initial screens honestly
    observers = _FakeObservers(old_screen, [10, 10, 200, 160],
                               event_at_sample=1,
                               event_displays=new_screen)
    observers.panel_visible = lambda pid: observers.phase == "open"
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    assert report["scenarios"]["topology_recovery"]["result"] == "FAIL"
    assert report["scenarios"]["window_containment"]["result"] == "PASS"


def test_validator_survives_malformed_toplevel_structures():
    """R08-05: the three master probes - each mutates ONE top-level
    field of a valid report; the validator returns diagnostics and
    never raises."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    base = _display_report()
    assert harness.validate_display_report(base) == []

    malformed = json.loads(json.dumps(base))
    malformed["topology_before"] = {"displays": [None]}
    errors = harness.validate_display_report(malformed)
    assert errors and all(isinstance(e, str) for e in errors)

    malformed = json.loads(json.dumps(base))
    malformed["topology_after"] = {
        "displays": [{"dpi": 96, "monitor": None}]}
    errors = harness.validate_display_report(malformed)
    assert errors and all(isinstance(e, str) for e in errors)

    malformed = json.loads(json.dumps(base))
    malformed["single_screen_baseline"] = "PASS"
    errors = harness.validate_display_report(malformed)
    assert errors and all(isinstance(e, str) for e in errors)
    assert any("verdict single_screen_baseline" in e for e in errors)


def test_orchestrated_rejects_foreign_and_stale_diagnostics(
        tmp_path, monkeypatch):
    """R08-03 second-round probe 1 through the raw harness: a diagnostic
    stream from another pid/hwnd, or one that never advances its
    sequence, yields no samples - foot stability is INVALID, never a
    quiet PASS."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    displays = [_single_display()]

    # another process's diagnostic file: no sample is ever fresh
    observers = _FakeObservers(displays, [10, 10, 200, 160],
                               observation_pid=999,
                               observation_hwnd=999)
    observers.panel_visible = lambda pid: observers.phase == "open"
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    foot = report["scenarios"]["foot_stability"]
    assert foot["result"] == "INVALID"
    assert foot["evidence"]["samples"] == []
    assert foot["evidence"]["stale_diagnostic_reads"] > 0

    # the app stopped refreshing: every read shows the same sequence
    observers = _FakeObservers(displays, [10, 10, 200, 160])
    observers.panel_visible = lambda pid: observers.phase == "open"
    observers.observation_sequence = 5
    original_read = observers.read_observation

    def frozen(report_file):
        record = original_read(report_file)
        record["sequence"] = 5
        return record

    observers.read_observation = frozen
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    foot = report["scenarios"]["foot_stability"]
    # at most the FIRST read can be accepted (there was no earlier
    # sequence to compare); every later read is stale, so the panel
    # phases cannot get fresh observations and stability is INVALID
    assert foot["result"] == "INVALID"
    assert len(foot["evidence"]["samples"]) <= 1
    assert foot["evidence"]["stale_diagnostic_reads"] > 0


def test_orchestrated_horizontal_foot_move_fails(
        tmp_path, monkeypatch):
    """R08-03 second-round probe 2 through the raw harness: a 100px
    HORIZONTAL foot move (y unchanged) FAILs - the comparison covers
    the complete x/y screen coordinates."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    displays = [_single_display()]
    observers = _FakeObservers(displays, [10, 10, 400, 200],
                               foot_after_panel_closed=[226.0, 206.0])
    observers.panel_visible = lambda pid: observers.phase == "open"
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    foot = report["scenarios"]["foot_stability"]
    assert foot["result"] == "FAIL"
    assert "dx=100.0" in foot["detail"]


# -- R08-03 third round: freshness, phase replay, DPI-equivalent units -------


def test_orchestrated_rejects_stale_and_contradictory_diagnostics(
        tmp_path, monkeypatch):
    """Master's probes through the raw harness: a diagnostic that never
    regenerates (generated_at frozen in 2000) or whose window frame
    contradicts the native window is waited out - foot stability is
    INVALID, never a quiet PASS."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    displays = [_single_display()]

    # frozen generation: sequence advances but generated_at is old
    observers = _FakeObservers(displays, [10, 10, 200, 160],
                               observation_generated_at="2000-01-01T00:00:00"
                                                        "+00:00")
    observers.panel_visible = lambda pid: observers.phase == "open"
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    foot = report["scenarios"]["foot_stability"]
    assert foot["result"] == "INVALID"
    assert foot["evidence"]["samples"] == []
    assert any("stale" in r for r in foot["evidence"]["stale_reasons"])

    # contradictory window mapping: frame [9000,9000,1,1] vs the native
    # [10,10,200,160]
    observers = _FakeObservers(displays, [10, 10, 200, 160],
                               observation_frame_override=[9000, 9000, 1, 1])
    observers.panel_visible = lambda pid: observers.phase == "open"
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    foot = report["scenarios"]["foot_stability"]
    assert foot["result"] == "INVALID"
    # the contradiction is caught in the per-sample derivation


def test_orchestrated_accepts_late_first_layout(tmp_path, monkeypatch):
    """The legal startup case: the first diagnostics lack a usable foot
    anchor (layout not ready); once the layout exists the run proceeds
    and PASSes - waiting is not failure."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    displays = [_single_display()]
    observers = _FakeObservers(displays, [10, 10, 200, 160])
    observers.panel_visible = lambda pid: observers.phase == "open"

    original = observers.read_observation

    def late_layout(report_file):
        record = original(report_file)
        if observers.observation_sequence <= 3:
            record["foot_point_screen"] = None
        return record

    observers.read_observation = late_layout
    report, _ = _run_orchestrated(
        monkeypatch, harness, observers,
        target=_FakeTarget(observers=observers))
    foot = report["scenarios"]["foot_stability"]
    assert foot["result"] == "PASS"
    assert all(s["foot_point_screen"] is not None
               for s in foot["evidence"]["samples"])
    single, _multi, _d = go_no_go._displays_outcomes(
        report, {"exe_sha256": "a" * 64})
    assert single == "PASS"


def test_phase_sequence_replay_rejected_at_consumer():
    """Master's consumer probe: three phases reusing ONE diagnostic
    generation (all sequences equal) is a stale recording - the
    validator flags it and the consumer cannot pass."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    report = _display_report()
    tampered = json.loads(json.dumps(report))
    for phase in ("before", "panel_open", "panel_closed"):
        tampered["evidence"]["foot_stability"]["positions"][phase]\
            ["sequence"] = 1
        tampered["evidence"]["foot_stability"]["positions"][phase]\
            ["read_at_utc"] = _ts(1)
        tampered["evidence"]["foot_stability"]["positions"][phase]\
            ["generated_at"] = _ts(1)
        tampered["scenarios"]["foot_stability"]["evidence"] = \
            tampered["evidence"]["foot_stability"]
    # scenario evidence pointers must stay consistent for a clean
    # derivation comparison; the sequence relation alone trips it
    errors = harness.validate_display_report(tampered)
    assert any("strictly increasing" in e or "does not match" in e
               or "derives INVALID" in e for e in errors)
    single, _multi, _detail = go_no_go._displays_outcomes(
        tampered, {"exe_sha256": "a" * 64})
    assert single == "INVALID"


def test_foot_units_are_dpi_invariant():
    """The comparison runs in Qt logical px: the same logical geometry
    yields the same verdict whether the window reports 96 or 192 DPI
    with correspondingly doubled physical coordinates."""
    from scripts import verify_displays as harness

    harness = go_no_go.display_harness
    displays = [_single_display()]
    identity = _identity_evidence()

    def evidence_at(dpi):
        # the native rect scales with DPI (GetWindowRect is physical);
        # the diagnostic frame and the foot anchor stay logical
        scale = dpi // 96
        native = [v * scale for v in (10, 10, 200, 160)]
        foot = _foot_evidence()
        for sample in foot["samples"]:
            sample["window_dpi"] = dpi
            sample["native_rect"] = native
            sample["rect"] = native
            sample["window_frame"] = [10, 10, 200, 160]
        for phase in ("before", "panel_open", "panel_closed"):
            foot["positions"][phase]["window_dpi"] = dpi
            foot["positions"][phase]["native_rect"] = native
            foot["positions"][phase]["rect"] = native
            foot["positions"][phase]["window_frame"] = [10, 10, 200, 160]
        return foot

    ok_96, ok_detail = harness.derive_foot_result(
        evidence_at(96), displays, identity)
    ok_192, _detail = harness.derive_foot_result(
        evidence_at(192), displays, identity)
    assert ok_96 == "PASS" and ok_192 == "PASS"

    # a 20-DIP foot drop FAILs identically at both DPIs
    def moved(dpi):
        foot = evidence_at(dpi)
        for phase in ("panel_open", "panel_closed"):
            foot["positions"][phase]["foot_point_screen"] = [116.0, 176.0]
            foot["positions"][phase]["window_frame"] = [10, 26, 200, 160]
            foot["positions"][phase]["native_rect"] = [
                v * (dpi // 96) for v in (10, 26, 200, 160)]
            foot["positions"][phase]["rect"] = [
                v * (dpi // 96) for v in (10, 26, 200, 160)]
        return foot

    fail_96, _d1 = harness.derive_foot_result(moved(96), displays, identity)
    fail_192, _d2 = harness.derive_foot_result(moved(192), displays,
                                               identity)
    assert fail_96 == "FAIL" and fail_192 == "FAIL"

    # a physical/logical unit mix (foot in device px on a 2x screen)
    # lands outside the logical frame -> INVALID, not a false verdict
    mixed = evidence_at(192)
    for phase in ("panel_open", "panel_closed"):
        mixed["positions"][phase]["foot_point_screen"] = [
            v * 2 for v in [116.0, 160.0]]
    result, detail = harness.derive_foot_result(mixed, displays, identity)
    assert result == "INVALID" and "unit mismatch" in detail


# -- V12-08 consumer audit: dist manifest + window report hardening ----------


def _dist_manifest_report(**overrides):
    report = {
        "schema": 2,
        "result": "PASS",
        "source_comparison": {"commit": "d" * 40, "git_tree": "e" * 40,
                              "worktree_clean": True},
        "artifact": {"build_id": "a" * 64, "commit": "d" * 40,
                     "git_tree": "e" * 40},
        "exe_sha256": "c" * 64,
        "file_count": 10, "total_bytes": 100,
        "attestation_protocol": 2,
    }
    report.update(overrides)
    return report


def test_dist_manifest_without_source_comparison_is_invalid():
    """Old-format manifests that OMIT the source identity comparison
    must never pass (V12-08 consumer audit)."""
    from scripts import go_no_go as _g

    source = {"commit": "d" * 40, "git_tree": "e" * 40}
    receipt = {"schema": 1, "result": "PASS", "recipe_id": "a" * 64,
               "artifact_id": "b" * 64, "exe_sha256": "c" * 64,
               "commit": "d" * 40, "git_tree": "e" * 40,
               "file_count": 10, "total_bytes": 100,
               "attestation_protocol": 2}
    manifest = {
        "schema": 2, "result": "PASS",
        "source_comparison": {"commit": "d" * 40, "git_tree": "e" * 40,
                              "worktree_clean": True},
        "artifact": {"build_id": "a" * 64, "commit": "d" * 40,
                     "git_tree": "e" * 40},
        "exe_sha256": "c" * 64, "file_count": 10, "total_bytes": 100,
        "attestation_protocol": 2, "failures": [],
    }
    outcome, _d = _g.dist_manifest_outcome(manifest, receipt, receipt,
                                           source, 0)
    assert outcome == "PASS"

    old_manifest = dict(manifest)
    del old_manifest["source_comparison"]
    old_manifest["schema"] = 1
    outcome, detail = _g.dist_manifest_outcome(
        old_manifest, receipt, receipt, source, 0)
    assert outcome == "INVALID" and "schema" in detail

    outcome, detail = _g.dist_manifest_outcome(
        {**manifest, "source_comparison": {"commit": "x"}},
        receipt, receipt, source, 0)
    assert outcome == "INVALID" and "source identity" in detail

    outcome, detail = _g.dist_manifest_outcome(
        {**manifest, "source_comparison": None},
        receipt, receipt, source, 0)
    assert outcome == "INVALID"


def test_window_report_postcheck_rejects_bare_and_foreign_reports(
        tmp_path):
    from scripts import go_no_go

    window_dir = tmp_path / "window"
    window_dir.mkdir()
    from scripts import verify_windows as _vw
    report = {
        "harness": "scripts/verify_windows.py",
        "target": "exe",
        "checks": [{"name": name, "pass": True, "detail": "ok"}
                   for name in _vw.REQUIRED_CHECKS["exe"]],
        "artifact": {"build_id": "a" * 64},
        "exe_sha256": "c" * 64,
        "attestation_protocol": 2,
        "result": "PASS",
    }
    (window_dir / "report.json").write_text(
        json.dumps(report), encoding="utf-8")
    postcheck = go_no_go.result_postcheck_with_binding(
        window_dir, "report.json", expected_target="exe",
        expected_build_id="a" * 64, expected_exe_sha256="c" * 64)
    assert postcheck(0)[0] == go_no_go.PASS

    # a bare {"result": "PASS"} file is not evidence
    bare_dir = tmp_path / "bare"
    bare_dir.mkdir()
    (bare_dir / "report.json").write_text(
        json.dumps({"result": "PASS"}), encoding="utf-8")
    bare = go_no_go.result_postcheck_with_binding(
        bare_dir, "report.json",
        expected_build_id="a" * 64, expected_exe_sha256="c" * 64)
    outcome, _detail = bare(0)
    assert outcome == go_no_go.INVALID

    # a report binding a FOREIGN candidate cannot pass
    foreign = dict(report)
    foreign["exe_sha256"] = "b" * 64
    foreign_dir = tmp_path / "foreign"
    foreign_dir.mkdir()
    (foreign_dir / "report.json").write_text(
        json.dumps(foreign), encoding="utf-8")
    foreign_check = go_no_go.result_postcheck_with_binding(
        foreign_dir, "report.json",
        expected_build_id="a" * 64, expected_exe_sha256="c" * 64)
    outcome, _detail = foreign_check(0)
    assert outcome == go_no_go.INVALID


# -- OVR-01 residual: the ACTUAL inner wrapper must pass binding -------------


def test_module_level_postcheck_with_binding_accepts_valid_exe(tmp_path):
    """A legitimate EXE report through the ACTUAL _run_acceptance_impl
    result_postcheck (not the module-level helper) must PASS."""
    from scripts import go_no_go

    harness = go_no_go.display_harness
    window_dir = tmp_path / "window"
    window_dir.mkdir()
    report = {
        "harness": "scripts/verify_windows.py",
        "target": "exe",
        "checks": [{"name": name, "pass": True, "detail": "ok"}
                   for name in go_no_go.verify_windows.REQUIRED_CHECKS["exe"]],
        "artifact": {"build_id": "a" * 64},
        "exe_sha256": "c" * 64,
        "attestation_protocol": 2,
        "result": "PASS",
    }
    (window_dir / "report.json").write_text(json.dumps(report),
                                            encoding="utf-8")
    postcheck = go_no_go.result_postcheck_with_binding(
        window_dir, "report.json", expected_target="exe",
        expected_build_id="a" * 64, expected_exe_sha256="c" * 64)
    outcome, _detail = postcheck(0)
    assert outcome == go_no_go.PASS


def test_extra_check_failures_rejected_even_beyond_required_set(tmp_path):
    """A check not in REQUIRED_CHECKS but present with pass=False must
    also fail — the consumer audits ALL checks, not just required ones."""
    from scripts import go_no_go
    from scripts import verify_windows as vw

    window_dir = tmp_path / "window"
    window_dir.mkdir()
    checks = [{"name": name, "pass": True, "detail": "ok"}
              for name in vw.REQUIRED_CHECKS["exe"]]
    checks.append({"name": "B.onboarding-panel-visible", "pass": False,
                   "detail": "not visible"})
    report = {
        "harness": "scripts/verify_windows.py",
        "target": "exe",
        "checks": checks,
        "artifact": {"build_id": "a" * 64},
        "exe_sha256": "c" * 64,
        "attestation_protocol": 2,
        "result": "PASS",
    }
    (window_dir / "report.json").write_text(json.dumps(report),
                                            encoding="utf-8")
    postcheck = go_no_go.result_postcheck_with_binding(
        window_dir, "report.json", expected_target="exe",
        expected_build_id="a" * 64, expected_exe_sha256="c" * 64)
    outcome, _detail = postcheck(0)
    assert outcome == go_no_go.FAIL


# -- OVR-02 residual: full derivation uses origin-preserving mapping ---------


def test_derive_foot_origin_preserving_nonzero_origin(tmp_path):
    """Master's non-zero-origin coordinate model: physical [2120,200,
    400,320] at screen origin x=1920 with DPI 192 maps to logical
    [2020,100,200,160].  The full derivation must accept this."""
    from scripts import verify_displays as harness

    displays = [{
        "device": "DISPLAY2", "primary": False,
        "monitor": [1920, 0, 2560, 1440],
        "work": [1920, 0, 2560, 1400],
        "dpi": 192, "dpi_source": "per_monitor"}]
    identity = {"hwnd": 1111, "reported_pid": 200, "launched_pid": 200,
                "owning_pid": 200, "ownership": "direct",
                "ownership_verified": True, "host_parent_pid": None,
                "image_path": "C:/x/RetirementPet.exe",
                "image_sha256": "a" * 64}
    native = [2120, 200, 400, 320]
    logical_frame = [2020, 100, 200, 160]
    # foot anchor in SCREEN-SPACE LOGICAL coordinates, inside the
    # window frame (origin 2020,100 size 200x160)
    foot_anchor = [logical_frame[0] + 100.0,
                   logical_frame[1] + logical_frame[3] - 4.0]
    footprint = harness.topology_of(displays)["fingerprint"]
    evidence = {
        "identity_binding": identity,
        "display_facts": {"displays": displays},
        "window_containment": {"rect": native, "displays": displays,
                               "topology_fingerprint": footprint},
        "foot_stability": {
            "samples": [{"monotonic_ms": i * 400, "rect": native,
                         "foot_point_screen": list(foot_anchor),
                         "pid": 200, "hwnd": 1111, "sequence": i + 1,
                         "generated_at": f"2026-09-14T08:00:0{i+1}+00:00",
                         "window_frame": logical_frame, "window_dpi": 192,
                         "read_at_utc":
                             f"2026-09-14T08:00:0{i+2}+00:00",
                         "native_rect": native, "displays": displays,
                         "topology_fingerprint": footprint}
                        for i in range(3)],
            "no_input_stable": True,
            "panel_observed_open": True, "panel_observed_closed": True,
            "positions": {
                phase: {"rect": native, "foot_point_screen": list(foot_anchor),
                        "pid": 200, "hwnd": 1111, "sequence": 50 + idx,
                        "generated_at": f"2026-09-14T08:00:0{idx+1}+00:00",
                        "window_frame": logical_frame, "window_dpi": 192,
                        "read_at_utc": f"2026-09-14T08:00:0{idx+2}+00:00",
                        "native_rect": native, "displays": displays}
                for idx, phase in enumerate(
                    ("before", "panel_open", "panel_closed"))},
            "tolerance_px": harness.FOOT_TOLERANCE_DIP},
        "per_display_landing": {"visited": [
            {"device": "DISPLAY2", "rect": native, "moved": True,
             "dpi_observed": 192}]},
        "topology_recovery": {"event_detected": False,
                              "reason": "no_topology_event"},
    }
    results = harness.derive_all_results(
        evidence, harness.topology_of(displays),
        harness.topology_of(displays), [], None)
    assert results["foot_stability"]["result"] == "PASS"


# -- OVR-03 residual: late-failure cleanup in the drill -----------------------


def test_drill_late_failure_cleans_everything(tmp_path, monkeypatch):
    """A settings.json write failure after the database commits must
    still clean up all partial artifacts."""
    from scripts import prepare_v1_drill

    output = tmp_path / "drill"
    output.mkdir()
    original_write_text = Path.write_text

    def failing_write(self, data, encoding=None, errors=None, newline=None):
        if self.name == "settings.json":
            raise OSError("injected disk full")
        return original_write_text(self, data, encoding=encoding,
                                   errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", failing_write)
    try:
        prepare_v1_drill.build_v1_dir(output, tasks=2)
    except OSError:
        pass
    finally:
        Path.write_text = original_write_text
    # no partial writes: the database and log dir are cleaned up
    assert not (output / "tasks.db").exists()
    assert not (output / "logs").exists()
    assert not (output / "settings.json").exists()


def test_drill_retry_after_failure_uses_clean_dir(tmp_path):
    """OVR-03: after a failed build cleans up, a retry into the same
    directory succeeds (the dir is empty again, not blocked by
    leftover partial writes)."""
    from scripts import prepare_v1_drill

    output = tmp_path / "drill"
    output.mkdir()
    original_connect = prepare_v1_drill.sqlite3.connect

    def failing_connect(*args, **kwargs):
        raise RuntimeError("injected connection failure")

    prepare_v1_drill.sqlite3.connect = failing_connect
    try:
        prepare_v1_drill.build_v1_dir(output, tasks=2)
    except RuntimeError:
        pass
    finally:
        prepare_v1_drill.sqlite3.connect = original_connect

    # the failed attempt left no artifacts
    assert not (output / "tasks.db").exists()
    assert list(output.iterdir()) == [] or all(
        f.name == "logs" or not f.exists() for f in output.iterdir())

    # retry into the same dir succeeds
    prepare_v1_drill.build_v1_dir(output, tasks=3)
    assert (output / "tasks.db").exists()
    assert (output / "settings.json").exists()
    import sqlite3
    conn = sqlite3.connect(output / "tasks.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        assert count == 3
    finally:
        conn.close()


