"""Provenance-bound Alpha/Production acceptance controller.

The Alpha profile answers one narrow question: is this exact, receipt-bound
artifact safe enough for an initial local trial? It never upgrades pending
hardware or long-duration evidence into a Production PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
DEFAULT_EVIDENCE = ROOT / ".release" / "acceptance"
TOOLCHAIN_ATTESTATION = ROOT / ".release" / "toolchain-attestation.json"
_ISOLATION_MARKER = "RETIREMENT_PET_GATE_PYCACHE"
_HEX = frozenset("0123456789abcdef")


class GateError(RuntimeError):
    """The controller cannot produce a trustworthy verdict."""


PASS = "PASS"
FAIL = "FAIL"
INVALID = "INVALID"
SKIP = "SKIP"

_ACCEPTANCE_GATE_ORDER = (
    "receipt_schema",
    "source_toolchain_preflight",
    "static_artifact_binding",
    "receipt_bytes_before_runtime",
    "dist_manifest",
    "pytest_regular",
    "pytest_native",
    "window_harness_source",
    "static_before_window_exe",
    "window_harness_exe",
    "static_before_smoke_exe",
    "smoke_exe",
    "static_before_performance_alpha",
    "performance_alpha_health",
    "static_before_stability_alpha",
    "stability_alpha",
    "static_before_final_dist",
    "dist_manifest_final",
    "final_source_clean",
    "final_toolchain_lock",
    "final_receipt_bytes",
)


def _ensure_isolated_bytecode() -> int | None:
    """Re-exec once so ignored source pyc cannot hide changed source."""
    marker = os.environ.get(_ISOLATION_MARKER)
    if marker:
        observed = sys.pycache_prefix
        if observed is None or Path(observed).resolve() != Path(marker).resolve():
            print("gate preflight failed: isolated byte-code path is inactive",
                  file=sys.stderr)
            return 2
        return None
    with tempfile.TemporaryDirectory(prefix="retirement-pet-gate-pycache-") as raw:
        env = dict(os.environ)
        env[_ISOLATION_MARKER] = raw
        command = [
            sys.executable, "-X", f"pycache_prefix={raw}", "-I", "-B",
            str(Path(__file__).resolve()), *sys.argv[1:],
        ]
        return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def _file_observation(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        return {"path": str(path), "readable": False,
                "error": type(exc).__name__}
    return {"path": str(path), "readable": True,
            "size": size, "sha256": digest.hexdigest()}


def _is_hex(value: object, length: int) -> bool:
    return (isinstance(value, str) and len(value) == length
            and set(value).issubset(_HEX))


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise GateError(f"{label} must be a JSON object")
    return value


def _load_receipt(path: Path) -> dict[str, Any]:
    value = _load_json(path, "release receipt")
    exact_keys = {
        "schema", "result", "recipe_id", "artifact_id", "exe_sha256",
        "commit", "git_tree", "file_count", "total_bytes",
        "attestation_protocol",
    }
    if set(value) != exact_keys or value.get("schema") != 1 \
            or value.get("result") != "PASS":
        raise GateError("release receipt has an invalid schema or result")
    for field, length in (("recipe_id", 64), ("artifact_id", 64),
                          ("exe_sha256", 64), ("commit", 40),
                          ("git_tree", 40)):
        if not _is_hex(value.get(field), length):
            raise GateError(f"release receipt has invalid {field}")
    if (not isinstance(value.get("file_count"), int)
            or value["file_count"] <= 0
            or not isinstance(value.get("total_bytes"), int)
            or value["total_bytes"] <= 0
            or value.get("attestation_protocol") != 2):
        raise GateError("release receipt has invalid counts or protocol")
    return value


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode != 0:
        raise GateError("Git source identity is unavailable")
    return result.stdout.strip()


def _environment_preflight(
        pycache_root: Path) -> dict[str, str]:
    """Validate the source/tool environment, without touching an artifact."""
    if not VENV_PY.is_file():
        raise GateError("release virtual environment is missing")
    identity_script = ROOT / "scripts" / "release_identity.py"
    python = [
        str(VENV_PY), "-X", f"pycache_prefix={pycache_root}", "-I", "-B",
    ]
    for command, label in (
        (python + [str(identity_script), "assert-clean"], "source is not clean"),
        (python + [
            str(identity_script), "verify-toolchain", "--attestation",
            str(TOOLCHAIN_ATTESTATION),
        ],
         "toolchain does not match the release lock"),
    ):
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise GateError(f"{label}: {detail}")

    commit = _git(ROOT, "rev-parse", "HEAD")
    tree = _git(ROOT, "rev-parse", "HEAD^{tree}")
    attestation = _load_json(
        TOOLCHAIN_ATTESTATION, "private toolchain attestation")
    attestation_sha256 = hashlib.sha256(json.dumps(
        attestation, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        "commit": commit,
        "git_tree": tree,
        "toolchain_attestation_sha256": attestation_sha256,
    }


def _static_artifact_binding(
        artifact: Path, receipt: dict[str, Any], source: dict[str, str]) \
        -> tuple[dict[str, Any], list[str], list[str]]:
    """Hash the whole artifact without importing or executing from it."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts import dist_manifest  # noqa: PLC0415

    manifest, inventory, failures = dist_manifest.validate_distribution(
        artifact,
        expected_commit=receipt["commit"],
        expected_build_id=receipt["recipe_id"],
        expected_artifact_id=receipt["artifact_id"],
        expected_exe_sha256=receipt["exe_sha256"],
        expected_file_count=receipt["file_count"],
        expected_total_bytes=receipt["total_bytes"],
        source_commit=source["commit"],
        source_tree=source["git_tree"],
        source_clean=True,
        require_runtime=False,
    )
    if (receipt["commit"] != source["commit"]
            or receipt["git_tree"] != source["git_tree"]):
        failures.append("release receipt does not describe current HEAD")
        manifest["result"] = "FAIL"
        manifest["failures"] = failures
    artifact_info = manifest.get("artifact")
    artifact_toolchain = artifact_info.get("toolchain") \
        if isinstance(artifact_info, dict) else None
    expected_attestation = source.get("toolchain_attestation_sha256")
    if (not isinstance(artifact_toolchain, dict)
            or artifact_toolchain.get("attestation_sha256")
            != expected_attestation):
        failures.append(
            "artifact is not bound to the current private toolchain "
            "attestation")
        manifest["result"] = "FAIL"
        manifest["failures"] = failures
    return manifest, inventory, failures


def _receipt_binding(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in (
        "schema", "result", "recipe_id", "artifact_id", "exe_sha256",
        "commit", "git_tree", "file_count", "total_bytes",
        "attestation_protocol",
    )}


def _health_report_outcome(
        report: dict[str, Any], receipt: dict[str, Any], *, exit_code: int,
        evidence_class: str, formal_field: str,
        receipt_sha256: str) -> tuple[str, str]:
    """Classify a receipt-bound, explicitly non-Production observation."""
    artifact_report = report.get("artifact")
    expected_binding = {
        "build_id": receipt["recipe_id"],
        "artifact_id": receipt["artifact_id"],
        "exe_sha256": receipt["exe_sha256"],
        "commit": receipt["commit"],
        "git_tree": receipt["git_tree"],
        "receipt_sha256": receipt_sha256,
    }
    if not isinstance(artifact_report, dict) or any(
            artifact_report.get(key) != value
            for key, value in expected_binding.items()):
        return INVALID, "health report artifact binding is invalid"
    harness = report.get("harness")
    if (report.get("schema") != 1
            or report.get("target") != "exe"
            or report.get("evidence_class") != evidence_class
            or report.get("evidence_complete") is not True
            or report.get("production_gate") is not False
            or report.get(formal_field) is not False
            or not isinstance(harness, dict)
            or harness.get("postcheck_unchanged") is not True):
        return INVALID, "health report contract is incomplete"
    result = report.get("result")
    if exit_code == 0 and result == PASS:
        return PASS, (
            f"receipt-bound {evidence_class} observation passed; "
            "not a Production gate")
    if exit_code == 1 and result == FAIL:
        failures = report.get("failures")
        detail = "; ".join(failures) \
            if isinstance(failures, list) and failures \
            else f"{evidence_class} observation failed"
        return FAIL, detail
    return INVALID, (
        f"inconsistent health result (exit {exit_code}, "
        f"result {result!r})")


def _smoke_report_outcome(
        report: dict[str, Any], receipt: dict[str, Any], *,
        exit_code: int) -> tuple[str, str]:
    """Separate conclusive candidate failures from harness-invalid runs."""
    if (report.get("schema") != 4
            or report.get("expected_build_id") != receipt["recipe_id"]
            or report.get("expected_exe_sha256") != receipt["exe_sha256"]
            or report.get("attestation_protocol") != 2):
        return INVALID, "smoke report binding or schema is invalid"

    failures = report.get("failures")
    harness_errors = report.get("harness_errors")
    cleanup_actions = report.get("cleanup_actions")
    for value, label in (
            (failures, "failures"),
            (harness_errors, "harness_errors"),
            (cleanup_actions, "cleanup_actions")):
        if (not isinstance(value, list)
                or any(not isinstance(item, str) for item in value)):
            return INVALID, f"smoke report {label} are malformed"
    result = report.get("result")
    complete = report.get("evidence_complete")
    harness_completed = report.get("harness_completed")
    if (complete is not True or harness_completed is not True
            or harness_errors):
        detail = "; ".join(harness_errors) if harness_errors \
            else "smoke evidence is incomplete"
        return INVALID, detail
    if exit_code == 1 and result == FAIL and failures:
        return FAIL, "; ".join(failures)

    import_canary = report.get("local_import_canary")
    expected_import = {
        "schema": 1,
        "operation": "local_import_preflight",
        "ipc_protocol": 1,
        "result": PASS,
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
    }
    if (not isinstance(import_canary, dict)
            or set(import_canary) != set(expected_import)
            or any(import_canary.get(key) != value
                   for key, value in expected_import.items())):
        return INVALID, "frozen local-import success evidence is incomplete"

    timeout_canary = report.get("local_import_timeout_canary")
    expected_timeout_keys = {
        "schema", "operation", "ipc_protocol", "result",
        "archive_sha256", "content_digest", "character_ids",
        "first_frames_verified", "child_exit_code", "child_reaped",
    }
    if (not isinstance(timeout_canary, dict)
            or set(timeout_canary) != expected_timeout_keys
            or timeout_canary.get("schema") != 1
            or timeout_canary.get("operation") != "local_import_preflight"
            or timeout_canary.get("ipc_protocol") != 1
            or timeout_canary.get("result") != "TIMEOUT"
            or timeout_canary.get("archive_sha256") is not None
            or timeout_canary.get("content_digest") is not None
            or timeout_canary.get("character_ids") != []
            or timeout_canary.get("first_frames_verified") is not False
            or timeout_canary.get("child_reaped") is not True
            or isinstance(timeout_canary.get("child_exit_code"), bool)
            or not isinstance(timeout_canary.get("child_exit_code"), int)
            or timeout_canary.get("child_exit_code") == 0):
        return INVALID, "frozen local-import timeout evidence is incomplete"
    if (exit_code == 0 and result == PASS and not failures
            and not cleanup_actions
            and report.get("observed_build_id") == receipt["recipe_id"]
            and report.get("observed_exe_sha256") == receipt["exe_sha256"]
            and report.get("observed_runtime_build_id")
            == receipt["recipe_id"]):
        return PASS, "receipt-bound smoke evidence is complete"
    return INVALID, (
        f"inconsistent smoke result (exit {exit_code}, result {result!r})")


class GateRun:
    def __init__(self, run_dir: Path, base_env: dict[str, str]):
        self.run_dir = run_dir
        self.base_env = base_env
        self.checks: list[dict[str, Any]] = []
        self.invalid_reasons: list[str] = []

    def command(
            self, name: str, command: list[str], *, timeout: int = 1800,
            env_update: dict[str, str] | None = None,
            fail_exit_codes: frozenset[int] = frozenset({1}),
            postcheck: Callable[[int], tuple[str, str]] | None = None) -> str:
        log_dir = self.run_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        env = dict(self.base_env)
        if env_update:
            env.update(env_update)
        started = datetime.now(timezone.utc)
        timed_out = False
        try:
            result = subprocess.run(
                command, cwd=ROOT, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                check=False,
            )
            exit_code: int | None = result.returncode
            stdout, stderr = result.stdout, result.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = None
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", "replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
            self.invalid_reasons.append(f"{name}: timeout")
        except OSError as exc:
            exit_code = None
            stdout = ""
            stderr = str(exc)
            self.invalid_reasons.append(f"{name}: command error")
        stdout_path = log_dir / f"{name}.stdout.log"
        stderr_path = log_dir / f"{name}.stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        if timed_out or exit_code is None:
            outcome = INVALID
            detail = "timeout" if timed_out else "command error"
        elif exit_code == 0:
            outcome = PASS
            detail = "exit 0"
        elif exit_code in fail_exit_codes:
            outcome = FAIL
            detail = f"exit {exit_code}"
        else:
            outcome = INVALID
            detail = f"unexpected exit {exit_code}"
        if not timed_out and exit_code is not None and postcheck is not None:
            try:
                outcome, detail = postcheck(exit_code)
                if outcome not in {PASS, FAIL, INVALID}:
                    raise ValueError(f"invalid postcheck outcome: {outcome}")
            except Exception as exc:  # noqa: BLE001
                outcome, detail = INVALID, f"postcheck error: {exc}"
        if outcome == INVALID:
            reason = f"{name}: {detail}"
            if reason not in self.invalid_reasons:
                self.invalid_reasons.append(reason)
        self.checks.append({
            "name": name, "outcome": outcome,
            "pass": outcome == PASS, "detail": detail,
            "started_at_utc": started.isoformat(timespec="seconds"),
            "ended_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "exit_code": exit_code, "timed_out": timed_out,
            "stdout_log": stdout_path.relative_to(self.run_dir).as_posix(),
            "stderr_log": stderr_path.relative_to(self.run_dir).as_posix(),
            "command": command,
        })
        print(f"  [{outcome}] {name}: {detail}")
        return outcome

    def record(self, name: str, outcome: str, detail: str,
               **extra: Any) -> str:
        if outcome not in {PASS, FAIL, INVALID, SKIP}:
            raise ValueError(f"invalid gate outcome: {outcome}")
        entry = {
            "name": name, "outcome": outcome,
            "pass": outcome == PASS, "detail": detail,
            **extra,
        }
        self.checks.append(entry)
        if outcome == INVALID:
            self.invalid_reasons.append(f"{name}: {detail}")
        print(f"  [{outcome}] {name}: {detail}")
        return outcome


def _production_pending() -> list[dict[str, str]]:
    return [
        {"gate": "multi_screen_dpi",
         "need": "100/150/200% DPI、负坐标副屏、跨屏拖动与拔屏恢复"},
        {"gate": "real_logoff_login_autostart",
         "need": "真实注销/登录后的自启、不抢焦点与延迟"},
        {"gate": "lock_sleep_wake",
         "need": "真机锁屏、睡眠、唤醒后的挂起与恢复"},
        {"gate": "clean_windows_vm",
         "need": "无 Python/Anaconda 且存在竞争 Qt PATH 的干净 Windows 11"},
        {"gate": "soak_24h",
         "need": "绑定本 artifact 的连续 24 小时稳定性证据"},
        {"gate": "performance_budget_freeze",
         "need": "三台参考设备完成 10 分钟样本并冻结预算"},
    ]


def _verdicts(alpha_pass: bool, profile: str) -> tuple[str, str, bool]:
    alpha = "ALPHA-GO" if alpha_pass else "ALPHA-NO-GO"
    production = (
        "PRODUCTION-NO-GO:PENDING_ENVIRONMENT" if alpha_pass
        else "PRODUCTION-NO-GO:ALPHA_GATES_FAILED")
    return alpha, production, alpha_pass and profile == "alpha"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("alpha", "production"),
                        default="alpha")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path,
                        default=DEFAULT_EVIDENCE)
    return parser.parse_args()


def _run_acceptance_impl(
        args: argparse.Namespace,
        failure_context: dict[str, Any]) -> int:
    artifact = args.artifact_dir.resolve()
    receipt_path = args.receipt.resolve()
    evidence_root = args.evidence_root.resolve()
    release_root = (ROOT / ".release").resolve()
    if evidence_root.is_relative_to(ROOT.resolve()) \
            and not evidence_root.is_relative_to(release_root):
        raise GateError(
            "evidence inside the repository must be under ignored .release")
    pycache_root = Path(os.environ[_ISOLATION_MARKER]).resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = evidence_root / (
        f"{stamp}-{args.profile}-{uuid4().hex[:8]}")
    run_dir.mkdir(parents=True, exist_ok=False)
    base_env = dict(os.environ)
    base_env["PYTHONPYCACHEPREFIX"] = str(pycache_root)
    base_env["PYTHONDONTWRITEBYTECODE"] = "1"
    base_env.pop("QT_QPA_PLATFORM", None)
    gate = GateRun(run_dir, base_env)
    receipt: dict[str, Any] | None = None
    source: dict[str, str] | None = None
    static_manifest: dict[str, Any] | None = None
    receipt_observation = _file_observation(receipt_path)

    def finish() -> int:
        seen = {str(check.get("name")) for check in gate.checks}
        if "final_receipt_bytes" not in seen:
            if receipt is None:
                gate.record(
                    "final_receipt_bytes", SKIP,
                    "receipt schema was not trusted")
            else:
                final_observation = _file_observation(receipt_path)
                if final_observation == receipt_observation:
                    gate.record(
                        "final_receipt_bytes", PASS,
                        "supplied receipt bytes are unchanged",
                        observation=final_observation)
                else:
                    gate.record(
                        "final_receipt_bytes", INVALID,
                        "supplied receipt bytes changed or became unreadable",
                        initial=receipt_observation,
                        final=final_observation)
        seen = {str(check.get("name")) for check in gate.checks}
        for name in _ACCEPTANCE_GATE_ORDER:
            if name not in seen:
                gate.record(
                    name, SKIP,
                    "gate was not reached because acceptance stopped earlier")
        invalid_run = bool(gate.invalid_reasons)
        alpha_pass = (
            not invalid_run and bool(gate.checks)
            and all(check.get("outcome") == PASS for check in gate.checks))
        pending = _production_pending()
        alpha_verdict, production_verdict, requested_go = _verdicts(
            alpha_pass, args.profile)
        if invalid_run:
            alpha_verdict = "INVALID_RUN"
            production_verdict = "INVALID_RUN"
            requested_go = False
        requested_verdict = (
            "INVALID_RUN" if invalid_run
            else ("GO" if requested_go else "NO-GO"))
        report = {
            "schema": 2,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "requested_profile": args.profile,
            "requested_profile_verdict": requested_verdict,
            "alpha_trial_verdict": alpha_verdict,
            "production_release_verdict": production_verdict,
            "production_release_certified": False,
            "artifact": static_manifest.get("artifact")
            if isinstance(static_manifest, dict) else None,
            "artifact_static_manifest": static_manifest,
            "artifact_dir": str(artifact),
            "supplied_receipt": str(receipt_path),
            "supplied_receipt_observation": receipt_observation,
            "receipt": receipt,
            "source": ({**source, "clean": True}
                       if source is not None else None),
            "automated_checks": gate.checks,
            "automated_all_pass": alpha_pass,
            "run_valid": not invalid_run,
            "invalid_reasons": gate.invalid_reasons,
            "production_pending_gates": pending,
        }
        (run_dir / "acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(alpha_verdict)
        print(
            f"{production_verdict} ({len(pending)} environment gates pending)")
        print(f"Requested {args.profile}: {requested_verdict}")
        print(f"Evidence: {run_dir}")
        if invalid_run:
            return 2
        return 0 if requested_go else 1

    def skip_remaining(reason: str, names: tuple[str, ...]) -> None:
        for name in names:
            gate.record(name, SKIP, reason)

    failure_context.update({"gate": gate, "finish": finish})

    try:
        try:
            receipt = _load_receipt(receipt_path)
        except GateError as exc:
            gate.record("receipt_schema", INVALID, str(exc))
            skip_remaining(
                "receipt is invalid; no acceptance command was run",
                ("source_toolchain_preflight", "static_artifact_binding",
                 "dist_manifest", "pytest_regular", "pytest_native",
                 "window_harness_source", "static_before_window_exe",
                 "window_harness_exe", "static_before_smoke_exe", "smoke_exe",
                 "static_before_performance_alpha",
                 "performance_alpha_health", "static_before_stability_alpha",
                 "stability_alpha",
                 "dist_manifest_final", "final_source_clean",
                 "final_toolchain_lock"),
            )
            return finish()
        gate.record("receipt_schema", PASS, "trusted receipt schema is valid")

        try:
            source = _environment_preflight(pycache_root)
        except GateError as exc:
            gate.record("source_toolchain_preflight", INVALID, str(exc))
            skip_remaining(
                "acceptance environment is invalid; artifact was not read "
                "or executed",
                ("static_artifact_binding", "dist_manifest",
                 "pytest_regular", "pytest_native", "window_harness_source",
                 "static_before_window_exe", "window_harness_exe",
                 "static_before_smoke_exe", "smoke_exe",
                 "static_before_performance_alpha",
                 "performance_alpha_health", "static_before_stability_alpha",
                 "stability_alpha", "dist_manifest_final",
                 "final_source_clean", "final_toolchain_lock"),
            )
            return finish()
        gate.record(
            "source_toolchain_preflight", PASS,
            "clean source and locked toolchain are available")

        try:
            static_manifest, inventory, static_failures = \
                _static_artifact_binding(artifact, receipt, source)
            static_dir = run_dir / "static-artifact-binding"
            static_dir.mkdir(parents=True, exist_ok=False)
            (static_dir / "manifest.json").write_text(
                json.dumps(static_manifest, ensure_ascii=False, indent=2)
                + "\n", encoding="utf-8")
            (static_dir / "files.sha256").write_text(
                "\n".join(inventory) + "\n", encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            gate.record(
                "static_artifact_binding", INVALID,
                f"static inventory could not be completed: {exc}")
            skip_remaining(
                "static inventory is inconclusive; candidate was not "
                "executed",
                ("dist_manifest", "pytest_regular", "pytest_native",
                 "window_harness_source", "static_before_window_exe",
                 "window_harness_exe", "static_before_smoke_exe", "smoke_exe",
                 "static_before_performance_alpha",
                 "performance_alpha_health", "static_before_stability_alpha",
                 "stability_alpha",
                 "dist_manifest_final", "final_source_clean",
                 "final_toolchain_lock"),
            )
            return finish()
        if static_failures:
            gate.record(
                "static_artifact_binding", FAIL,
                "; ".join(static_failures),
                manifest="static-artifact-binding/manifest.json",
                inventory="static-artifact-binding/files.sha256")
            skip_remaining(
                "acceptance stopped at static artifact binding; candidate "
                "was not executed",
                ("dist_manifest", "pytest_regular", "pytest_native",
                 "window_harness_source", "static_before_window_exe",
                 "window_harness_exe", "static_before_smoke_exe", "smoke_exe",
                 "static_before_performance_alpha",
                 "performance_alpha_health", "static_before_stability_alpha",
                 "stability_alpha",
                 "dist_manifest_final", "final_source_clean",
                 "final_toolchain_lock"),
            )
            return finish()
        gate.record(
            "static_artifact_binding", PASS,
            "complete artifact inventory exactly matches supplied receipt",
            manifest="static-artifact-binding/manifest.json",
            inventory="static-artifact-binding/files.sha256")
    except Exception as exc:  # noqa: BLE001
        gate.record(
            "preflight_internal", INVALID,
            f"{type(exc).__name__}: {exc}")
        return finish()

    current_receipt_observation = _file_observation(receipt_path)
    if current_receipt_observation != receipt_observation:
        gate.record(
            "receipt_bytes_before_runtime", INVALID,
            "supplied receipt bytes changed before runtime validation",
            initial=receipt_observation,
            current=current_receipt_observation)
        skip_remaining(
            "receipt bytes changed; no candidate process was started",
            ("dist_manifest", "pytest_regular", "pytest_native",
             "window_harness_source", "static_before_window_exe",
             "window_harness_exe", "static_before_smoke_exe", "smoke_exe",
             "static_before_performance_alpha", "performance_alpha_health",
             "static_before_stability_alpha", "stability_alpha",
             "dist_manifest_final", "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()
    gate.record(
        "receipt_bytes_before_runtime", PASS,
        "supplied receipt bytes match the initial observation",
        observation=current_receipt_observation)

    assert receipt is not None
    assert source is not None
    python = [
        str(VENV_PY), "-X", f"pycache_prefix={pycache_root}", "-I", "-B",
    ]
    expected_id = receipt["recipe_id"]
    expected_exe = receipt["exe_sha256"]

    dist_evidence = run_dir / "dist-manifest"

    def receipt_postcheck(root: Path) \
            -> Callable[[int], tuple[str, str]]:
        def compare(exit_code: int) -> tuple[str, str]:
            manifest_paths = list(root.rglob("manifest.json"))
            receipt_paths = list(root.rglob("release-receipt.json"))
            if len(manifest_paths) != 1 or len(receipt_paths) != 1:
                return INVALID, (
                    "dist validation did not produce exactly one manifest "
                    "and receipt")
            observed_manifest = _load_json(
                manifest_paths[0], "dist validation manifest")
            observed_receipt = _load_json(
                receipt_paths[0], "dist validation receipt")
            result = observed_manifest.get("result")
            comparison = observed_manifest.get("source_comparison")
            if isinstance(comparison, dict) and comparison != {
                    "commit": source["commit"],
                    "git_tree": source["git_tree"],
                    "worktree_clean": True}:
                return INVALID, "source identity changed during acceptance"
            if exit_code == 0 and result == PASS:
                validated = _load_receipt(receipt_paths[0])
                if (_receipt_binding(validated)
                        != _receipt_binding(receipt)):
                    return FAIL, (
                        "dist receipt differs from supplied release receipt")
                return PASS, (
                    "artifact inventory exactly matches supplied receipt")
            if (exit_code == 2 and result == FAIL
                    and observed_receipt.get("result") == FAIL):
                observed_artifact = observed_manifest.get("artifact")
                expected_failure_receipt = {
                    "schema": 1,
                    "result": FAIL,
                    "recipe_id": observed_artifact.get("build_id")
                    if isinstance(observed_artifact, dict) else None,
                    "artifact_id": observed_manifest.get("artifact_id"),
                    "exe_sha256": observed_manifest.get("exe_sha256"),
                    "commit": observed_artifact.get("commit")
                    if isinstance(observed_artifact, dict) else None,
                    "git_tree": observed_artifact.get("git_tree")
                    if isinstance(observed_artifact, dict) else None,
                    "file_count": observed_manifest.get("file_count"),
                    "total_bytes": observed_manifest.get("total_bytes"),
                    "attestation_protocol": observed_manifest.get(
                        "attestation_protocol"),
                }
                if observed_receipt != expected_failure_receipt:
                    return INVALID, (
                        "failed dist receipt is inconsistent with manifest")
                failures = observed_manifest.get("failures")
                detail = "; ".join(failures) \
                    if isinstance(failures, list) and failures \
                    else "artifact validation failed"
                return FAIL, detail
            return INVALID, (
                f"inconsistent dist validator result (exit {exit_code}, "
                f"result {result!r})")
        return compare

    def result_postcheck(root: Path, filename: str) \
            -> Callable[[int], tuple[str, str]]:
        def compare(exit_code: int) -> tuple[str, str]:
            paths = list(root.rglob(filename))
            if len(paths) != 1:
                return INVALID, f"did not produce exactly one {filename}"
            result = _load_json(paths[0], filename).get("result")
            if exit_code == 0 and result == PASS:
                return PASS, "structured result is PASS"
            if exit_code == 1 and result == FAIL:
                return FAIL, "structured result is FAIL"
            return INVALID, (
                f"inconsistent structured result (exit {exit_code}, "
                f"result {result!r})")
        return compare

    def smoke_postcheck(root: Path) \
            -> Callable[[int], tuple[str, str]]:
        def compare(exit_code: int) -> tuple[str, str]:
            paths = list(root.rglob("result.json"))
            if len(paths) != 1:
                return INVALID, "did not produce exactly one result.json"
            report = _load_json(paths[0], "smoke result.json")
            return _smoke_report_outcome(
                report, receipt, exit_code=exit_code)
        return compare

    def health_report_postcheck(
            root: Path, filename: str, evidence_class: str,
            formal_field: str) -> Callable[[int], tuple[str, str]]:
        def compare(exit_code: int) -> tuple[str, str]:
            paths = list(root.rglob(filename))
            if len(paths) != 1:
                return INVALID, f"did not produce exactly one {filename}"
            report = _load_json(paths[0], filename)
            return _health_report_outcome(
                report, receipt, exit_code=exit_code,
                evidence_class=evidence_class, formal_field=formal_field,
                receipt_sha256=str(receipt_observation["sha256"]))
        return compare

    def dist_command(evidence: Path) -> list[str]:
        return python + [
            str(ROOT / "scripts" / "dist_manifest.py"),
            "--dist", str(artifact),
            "--expected-commit", receipt["commit"],
            "--expected-build-id", expected_id,
            "--expected-artifact-id", receipt["artifact_id"],
            "--expected-exe-sha256", expected_exe,
            "--expected-file-count", str(receipt["file_count"]),
            "--expected-total-bytes", str(receipt["total_bytes"]),
            "--evidence-root", str(evidence),
            "--git-root", str(ROOT),
        ]

    def recheck_static_binding(name: str) -> str:
        evidence = run_dir / name
        current_receipt = _file_observation(receipt_path)
        if current_receipt != receipt_observation:
            return gate.record(
                name, INVALID,
                "supplied receipt bytes changed or became unreadable",
                initial_receipt=receipt_observation,
                current_receipt=current_receipt)
        try:
            manifest, inventory, failures = _static_artifact_binding(
                artifact, receipt, source)
            evidence.mkdir(parents=True, exist_ok=False)
            (evidence / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            (evidence / "files.sha256").write_text(
                "\n".join(inventory) + "\n", encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            return gate.record(
                name, INVALID,
                f"static recheck could not be completed: {exc}")
        if failures:
            return gate.record(
                name, FAIL, "; ".join(failures),
                manifest=f"{name}/manifest.json",
                inventory=f"{name}/files.sha256")
        return gate.record(
            name, PASS, "artifact still exactly matches supplied receipt",
            manifest=f"{name}/manifest.json",
            inventory=f"{name}/files.sha256")

    dist_outcome = gate.command(
        "dist_manifest",
        dist_command(dist_evidence),
        postcheck=receipt_postcheck(dist_evidence),
    )
    if dist_outcome != PASS:
        skip_remaining(
            "runtime identity gate did not pass; no further candidate "
            "process was started",
            ("pytest_regular", "pytest_native", "window_harness_source",
             "static_before_window_exe", "window_harness_exe",
             "static_before_smoke_exe", "smoke_exe",
             "static_before_performance_alpha", "performance_alpha_health",
             "static_before_stability_alpha", "stability_alpha",
             "dist_manifest_final",
             "final_source_clean", "final_toolchain_lock"),
        )
        return finish()
    pytest_regular_outcome = gate.command(
        "pytest_regular",
        python + ["-m", "pytest", "-p", "no:cacheprovider", "tests",
                  "--basetemp", str(run_dir / "pytest-temp")],
    )
    if pytest_regular_outcome != PASS:
        skip_remaining(
            "regular test gate did not pass; no further candidate process "
            "was started",
            ("pytest_native", "window_harness_source",
             "static_before_window_exe", "window_harness_exe",
             "static_before_smoke_exe", "smoke_exe",
             "static_before_performance_alpha", "performance_alpha_health",
             "static_before_stability_alpha", "stability_alpha",
             "dist_manifest_final", "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()
    pytest_native_outcome = gate.command(
        "pytest_native",
        python + ["-m", "pytest", "-p", "no:cacheprovider",
                  "tests/native", "-q",
                  "--basetemp", str(run_dir / "native-temp")],
        env_update={"RP_RUN_NATIVE": "1"},
    )
    if pytest_native_outcome != PASS:
        skip_remaining(
            "native test gate did not pass; no further candidate process "
            "was started",
            ("window_harness_source", "static_before_window_exe",
             "window_harness_exe", "static_before_smoke_exe", "smoke_exe",
             "static_before_performance_alpha", "performance_alpha_health",
             "static_before_stability_alpha", "stability_alpha",
             "dist_manifest_final", "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()
    window_source_outcome = gate.command(
        "window_harness_source",
        python + [str(ROOT / "scripts" / "verify_windows.py"),
                  "--target", "source", "--evidence-root",
                  str(run_dir / "window-source")],
        postcheck=result_postcheck(run_dir / "window-source", "report.json"),
    )
    if window_source_outcome != PASS:
        skip_remaining(
            "source window harness did not pass; no further candidate "
            "process was started",
            ("static_before_window_exe", "window_harness_exe",
             "static_before_smoke_exe", "smoke_exe",
             "static_before_performance_alpha", "performance_alpha_health",
             "static_before_stability_alpha", "stability_alpha",
             "dist_manifest_final", "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()
    if recheck_static_binding("static_before_window_exe") != PASS:
        skip_remaining(
            "artifact changed or became unreadable before EXE harness; no "
            "further candidate process was started",
            ("window_harness_exe", "static_before_smoke_exe", "smoke_exe",
             "static_before_performance_alpha", "performance_alpha_health",
             "static_before_stability_alpha", "stability_alpha",
             "dist_manifest_final", "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()
    window_exe_outcome = gate.command(
        "window_harness_exe",
        python + [str(ROOT / "scripts" / "verify_windows.py"),
                  "--target", "exe", "--artifact-dir", str(artifact),
                  "--expected-build-id", expected_id,
                  "--expected-exe-sha256", expected_exe,
                  "--evidence-root", str(run_dir / "window-exe")],
        postcheck=result_postcheck(run_dir / "window-exe", "report.json"),
    )
    if window_exe_outcome != PASS:
        skip_remaining(
            "EXE window harness did not pass; no further candidate process "
            "was started",
            ("static_before_smoke_exe", "smoke_exe",
             "static_before_performance_alpha", "performance_alpha_health",
             "static_before_stability_alpha", "stability_alpha",
             "dist_manifest_final", "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()
    if recheck_static_binding("static_before_smoke_exe") != PASS:
        skip_remaining(
            "artifact changed or became unreadable before smoke; no further "
            "candidate process was started",
            ("smoke_exe", "static_before_performance_alpha",
             "performance_alpha_health", "static_before_stability_alpha",
             "stability_alpha", "dist_manifest_final", "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell:
        smoke_outcome = gate.command(
            "smoke_exe",
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(ROOT / "scripts" / "smoke_test.ps1"),
             "-ArtifactDir", str(artifact),
             "-ExpectedBuildId", expected_id,
             "-ExpectedExeSha256", expected_exe,
             "-EvidenceRoot", str(run_dir / "smoke")],
            postcheck=smoke_postcheck(run_dir / "smoke"),
        )
    else:
        smoke_outcome = gate.record(
            "smoke_exe", INVALID, "PowerShell executable is unavailable")

    if smoke_outcome != PASS:
        skip_remaining(
            "EXE smoke did not pass; no further candidate process was "
            "started",
            ("static_before_performance_alpha", "performance_alpha_health",
             "static_before_stability_alpha", "stability_alpha",
             "dist_manifest_final", "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()

    if recheck_static_binding("static_before_performance_alpha") != PASS:
        skip_remaining(
            "artifact changed or became unreadable before Alpha performance "
            "observation; no further candidate process was started",
            ("performance_alpha_health", "static_before_stability_alpha",
             "stability_alpha", "dist_manifest_final", "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()
    performance_root = run_dir / "performance-alpha"
    performance_outcome = gate.command(
        "performance_alpha_health",
        python + [str(ROOT / "scripts" / "perf_sample.py"),
                  "--target", "exe", "--profile", "alpha-health",
                  "--warmup-s", "5", "--scene-s", "30",
                  "--sample-interval-s", "0.5",
                  "--artifact-dir", str(artifact),
                  "--receipt", str(receipt_path),
                  "--evidence-root", str(performance_root)],
        timeout=300,
        postcheck=health_report_postcheck(
            performance_root, "performance.json", "ALPHA_HEALTH",
            "formal_performance_gate"),
    )
    if performance_outcome != PASS:
        skip_remaining(
            "Alpha performance health observation did not pass; no further "
            "candidate process was started",
            ("static_before_stability_alpha", "stability_alpha",
             "dist_manifest_final", "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()

    if recheck_static_binding("static_before_stability_alpha") != PASS:
        skip_remaining(
            "artifact changed or became unreadable before Alpha stability "
            "observation; no further candidate process was started",
            ("stability_alpha", "dist_manifest_final", "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()
    stability_root = run_dir / "stability-alpha"
    stability_outcome = gate.command(
        "stability_alpha",
        python + [str(ROOT / "scripts" / "soak_24h.py"),
                  "--target", "exe", "--profile", "alpha",
                  "--duration-s", "120", "--sample-interval-s", "5",
                  "--artifact-dir", str(artifact),
                  "--receipt", str(receipt_path),
                  "--evidence-root", str(stability_root)],
        timeout=300,
        postcheck=health_report_postcheck(
            stability_root, "soak.json", "ALPHA_STABILITY",
            "formal_24h_claim"),
    )
    if stability_outcome != PASS:
        skip_remaining(
            "Alpha stability observation did not pass; acceptance stopped",
            ("static_before_final_dist", "dist_manifest_final",
             "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()

    if recheck_static_binding("static_before_final_dist") != PASS:
        skip_remaining(
            "artifact or receipt changed before final validation; no further "
            "candidate process was started",
            ("dist_manifest_final", "final_source_clean",
             "final_toolchain_lock"),
        )
        return finish()

    final_dist_evidence = run_dir / "dist-manifest-final"
    gate.command(
        "dist_manifest_final",
        dist_command(final_dist_evidence),
        postcheck=receipt_postcheck(final_dist_evidence),
    )

    identity_script = ROOT / "scripts" / "release_identity.py"
    gate.command("final_source_clean",
                 python + [str(identity_script), "assert-clean"])
    gate.command("final_toolchain_lock",
                 python + [
                     str(identity_script), "verify-toolchain",
                     "--attestation", str(TOOLCHAIN_ATTESTATION),
                     "--expected-attestation-sha256",
                     source["toolchain_attestation_sha256"],
                 ])
    return finish()


def _run_acceptance(args: argparse.Namespace) -> int:
    failure_context: dict[str, Any] = {}
    try:
        return _run_acceptance_impl(args, failure_context)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        gate = failure_context.get("gate")
        finish = failure_context.get("finish")
        if isinstance(gate, GateRun) and callable(finish):
            gate.record(
                "acceptance_internal", INVALID,
                f"{type(exc).__name__}: {exc}")
            return finish()
        if isinstance(exc, GateError):
            raise
        raise GateError(
            f"acceptance controller failed before evidence initialization: "
            f"{type(exc).__name__}: {exc}") from exc


def main() -> int:
    isolated_result = _ensure_isolated_bytecode()
    if isolated_result is not None:
        return isolated_result
    try:
        return _run_acceptance(_parse_args())
    except (GateError, OSError, UnicodeError, ValueError) as exc:
        print(f"INVALID_RUN: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
