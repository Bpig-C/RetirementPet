"""Fail-closed out-of-process performance and Alpha-health sampler.

This harness records either ``CALIBRATION`` or ``ALPHA_HEALTH`` evidence.
Neither profile is a frozen performance gate, including when custom short
durations are supplied.  A future formal gate must be a separate, frozen
profile with reference-machine metadata and cannot be selected here.

EXE runs require ``--artifact-dir`` plus a matching release ``--receipt``;
all runs require an explicit ``--evidence-root``.  The sampled PID is always
the PID self-reported by the application, never an assumed launcher PID.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import math
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

if __package__:
    from .soak_24h import (
        ArtifactBinding,
        HarnessError,
        Target,
        VerifiedProcess,
        atomic_write,
        bind_target,
        required_sample_count,
        scan_run_logs,
        shutdown_target,
        supporting_evidence,
        verify_artifact_unchanged,
        verify_harness_inputs,
        verify_harness_unchanged,
        wait_for_app_report,
        wait_for_window_visibility,
        write_jsonl_evidence,
    )
else:  # isolated direct execution needs an explicit sibling-module path
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    from soak_24h import (  # type: ignore[no-redef]
        ArtifactBinding,
        HarnessError,
        Target,
        VerifiedProcess,
        atomic_write,
        bind_target,
        required_sample_count,
        scan_run_logs,
        shutdown_target,
        supporting_evidence,
        verify_artifact_unchanged,
        verify_harness_inputs,
        verify_harness_unchanged,
        wait_for_app_report,
        wait_for_window_visibility,
        write_jsonl_evidence,
    )

ROOT = Path(__file__).resolve().parent.parent
ROLLING_WINDOW_S = 5.0
_REQUIRED_MARKERS = (
    "process_start", "qt_ready", "tray_ready", "first_pet_paint",
)


class ProcessMetrics:
    """CPU time, Private Bytes, and working set for a specific Windows PID."""

    def __init__(self, process: VerifiedProcess):
        self.process = process
        self.pid = process.pid
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._psapi = ctypes.WinDLL("psapi", use_last_error=True)

        class PMCX(ctypes.Structure):
            _fields_ = [
                ("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]
        self._PMCX = PMCX

    def sample(self) -> dict[str, int] | None:
        handle = self.process.handle
        if not handle or not self.process.is_alive():
            return None
        creation, exit_t, kernel, user = (
            wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME())
        if not self._kernel32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_t),
                ctypes.byref(kernel), ctypes.byref(user)):
            return None
        memory = self._PMCX()
        memory.cb = ctypes.sizeof(self._PMCX)
        if not self._psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(memory), memory.cb):
            return None

        def to_100ns(value: wt.FILETIME) -> int:
            return (value.dwHighDateTime << 32) | value.dwLowDateTime

        return {
            "cpu_100ns": to_100ns(kernel) + to_100ns(user),
            "private_bytes": int(memory.PrivateUsage),
            "working_set_bytes": int(memory.WorkingSetSize),
        }


def percentile(values: list[float], pct: float) -> float:
    if not values:
        raise HarnessError("cannot summarize an empty sample set")
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        int(round(pct / 100 * (len(ordered) - 1))),
    )
    return ordered[index]


def summarize(samples: list[dict[str, Any]], interval_s: float,
              bound_pid: int | None = None) -> dict[str, Any]:
    if not samples:
        raise HarnessError("cannot summarize an empty sample set")
    # fail closed on hostile or corrupted samples (V12-08): a negative
    # CPU delta, a non-finite value or a sample from another process
    # must abort the aggregation, never silently blend in
    for sample in samples:
        cpu_value = sample.get("cpu_percent")
        if (not isinstance(cpu_value, (int, float))
                or isinstance(cpu_value, bool)
                or not math.isfinite(cpu_value)
                or cpu_value < 0):
            raise HarnessError(
                f"invalid cpu_percent in sample: {cpu_value!r}")
        private = sample.get("private_bytes")
        if (not isinstance(private, int) or isinstance(private, bool)
                or private < 0):
            raise HarnessError(
                f"invalid private_bytes in sample: {private!r}")
        if bound_pid is not None and sample.get("app_pid") != bound_pid:
            raise HarnessError(
                f"sample from foreign app_pid {sample.get('app_pid')!r}")
    cpu = [float(sample["cpu_percent"]) for sample in samples]
    memory = [int(sample["private_bytes"]) for sample in samples]
    window_size = max(1, int(round(ROLLING_WINDOW_S / interval_s)))
    rolling = [
        statistics.fmean(cpu[max(0, index - window_size + 1):index + 1])
        for index in range(len(cpu))
    ]
    return {
        "samples": len(samples),
        "cpu_avg_percent": round(statistics.fmean(cpu), 3),
        "cpu_p95_rolling5s_percent": round(percentile(rolling, 95), 3),
        "cpu_peak_percent": round(max(cpu), 3),
        "private_bytes_avg": int(statistics.fmean(memory)),
        "private_bytes_peak": max(memory),
    }


def measure_scene(
        metrics: ProcessMetrics, proc: subprocess.Popen, *, scene: str,
        phase: str, duration_s: float, interval_s: float,
        samples: list[dict[str, Any]]) -> int:
    """Measure one phase, raising on any process or sampling discontinuity."""
    if duration_s <= 0:
        return 0
    baseline = metrics.sample()
    if baseline is None:
        raise HarnessError(
            f"{scene}/{phase}: initial sample of self-reported PID failed")
    last_cpu = baseline["cpu_100ns"]
    last_wall = time.monotonic()
    deadline = last_wall + duration_s
    next_sample = last_wall + interval_s
    collected = 0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise HarnessError(
                f"{scene}/{phase}: primary process exited ({proc.returncode})")
        now = time.monotonic()
        sleep_s = min(max(0.0, next_sample - now),
                      max(0.0, deadline - now), 0.1)
        if sleep_s:
            time.sleep(sleep_s)
            continue
        if now < next_sample:
            continue
        data = metrics.sample()
        if data is None:
            raise HarnessError(
                f"{scene}/{phase}: sample of self-reported PID failed")
        wall_delta = now - last_wall
        cpu_delta = data["cpu_100ns"] - last_cpu
        if wall_delta <= 0 or cpu_delta < 0:
            raise HarnessError(f"{scene}/{phase}: invalid process counters")
        if wall_delta > interval_s * 2.5:
            raise HarnessError(
                f"{scene}/{phase}: sampling gap {wall_delta:.3f}s "
                "exceeds continuity limit")
        samples.append({
            "scene": scene,
            "phase": phase,
            "t_monotonic_s": round(now, 6),
            "cpu_percent": round(
                cpu_delta / 10_000_000.0 / wall_delta * 100, 3),
            "private_bytes": data["private_bytes"],
            "working_set_bytes": data["working_set_bytes"],
            "app_pid": metrics.pid,
        })
        collected += 1
        last_cpu = data["cpu_100ns"]
        last_wall = now
        # Never backfill missed slots with a burst of adjacent samples.
        next_sample = now + interval_s
    return collected


def load_perf_markers(data_dir: Path) -> list[dict[str, Any]]:
    path = Path(data_dir) / "logs" / "perf_markers.jsonl"
    if not path.is_file():
        raise HarnessError("performance marker log is missing")
    markers: list[dict[str, Any]] = []
    try:
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            timestamp = value.get("t_ms") if isinstance(value, dict) else None
            if (not isinstance(value, dict)
                    or not isinstance(value.get("marker"), str)
                    or not isinstance(timestamp, (int, float))
                    or isinstance(timestamp, bool)
                    or not math.isfinite(timestamp)):
                raise HarnessError(
                    f"performance marker line {number} is invalid")
            markers.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessError("performance marker log is unreadable") from exc
    indexes: list[int] = []
    for name in _REQUIRED_MARKERS:
        matches = [
            index for index, marker in enumerate(markers)
            if marker["marker"] == name
        ]
        if len(matches) != 1:
            raise HarnessError(f"marker {name!r} must appear exactly once")
        indexes.append(matches[0])
    if indexes != sorted(indexes):
        raise HarnessError("required performance markers are out of order")
    times = [float(markers[index]["t_ms"]) for index in indexes]
    if any(later < earlier for earlier, later in zip(times, times[1:])):
        raise HarnessError("required performance marker times moved backwards")
    return markers


def validate_marker_window(
        markers: list[dict[str, Any]], start_ms: int,
        end_ms: int) -> None:
    required = {
        marker["marker"]: float(marker["t_ms"])
        for marker in markers if marker["marker"] in _REQUIRED_MARKERS
    }
    if any(not start_ms <= required[name] <= end_ms
           for name in _REQUIRED_MARKERS):
        raise HarnessError("performance markers fall outside the measured run")


def resolve_profile(
        profile: str, warmup_s: float | None, scene_s: float | None,
        interval_s: float | None) -> tuple[str, float, float, float, bool]:
    if profile == "alpha-health":
        defaults = (10.0, 60.0, 0.5)
        evidence_class = "ALPHA_HEALTH"
    else:
        defaults = (20.0, 60.0, 0.25)
        evidence_class = "CALIBRATION"
    warmup = defaults[0] if warmup_s is None else warmup_s
    scene = defaults[1] if scene_s is None else scene_s
    interval = defaults[2] if interval_s is None else interval_s
    if warmup < 0 or scene <= 0:
        raise HarnessError("warmup must be non-negative and scene duration positive")
    if interval <= 0 or interval > scene / 4:
        raise HarnessError(
            "sample interval must be positive and allow at least four samples")
    custom_timing = any(value is not None for value in (
        warmup_s, scene_s, interval_s))
    return evidence_class, float(warmup), float(scene), float(interval), custom_timing


def evaluate_perf(
        *, samples: list[dict[str, Any]], scenes: tuple[str, ...],
        scene_s: float, interval_s: float, markers_ok: bool,
        error_count: int, run_failures: list[str], normal_exit: bool,
        forced_termination: bool) -> list[str]:
    failures = list(run_failures)
    minimum = required_sample_count(scene_s, interval_s)
    for scene in scenes:
        count = sum(
            sample["scene"] == scene and sample["phase"] == "measure"
            for sample in samples)
        if count < minimum:
            failures.append(
                f"{scene} sampling coverage {count}/{minimum} is insufficient")
    if not markers_ok:
        failures.append("required performance markers were not validated")
    if error_count:
        failures.append(f"{error_count} ERROR/Traceback lines in run logs")
    if forced_termination:
        failures.append("forced termination was required")
    if not normal_exit:
        failures.append("application did not exit normally with code 0")
    return failures


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("source", "exe"), default="source")
    parser.add_argument("--profile", choices=("calibration", "alpha-health"),
                        default="calibration")
    parser.add_argument("--warmup-s", type=float)
    parser.add_argument("--scene-s", type=float)
    parser.add_argument("--sample-interval-s", type=float)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path,
                        help="reuse a prepared data dir (e.g. with a "
                             "pre-activated static pack) instead of a "
                             "fresh temp dir; its contents are kept")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        (evidence_class, warmup_s, scene_s, interval_s,
         custom_timing) = resolve_profile(
             args.profile, args.warmup_s, args.scene_s,
             args.sample_interval_s)
    except HarnessError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.evidence_root.resolve() / (
        f"{stamp}-{evidence_class.lower()}-{uuid4().hex[:8]}")
    run_dir.mkdir(parents=True, exist_ok=False)
    if args.data_dir is not None:
        data_dir = args.data_dir.resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        # a reused data dir still holds markers from earlier runs; the
        # exactly-once marker validation is per-run, so archive them
        prior = data_dir / "logs" / "perf_markers.jsonl"
        if prior.is_file():
            prior.rename(data_dir / "logs" / f"perf_markers.{stamp}-"
                         f"{uuid4().hex[:6]}.jsonl")
    else:
        data_dir = run_dir / "data"
    window_report = run_dir / "window.json"
    instance = f"perf-{uuid4().hex}"

    binding: ArtifactBinding | None = None
    target: Target | None = None
    proc: subprocess.Popen | None = None
    app_pid: int | None = None
    app_hwnd: int | None = None
    verified_process: VerifiedProcess | None = None
    process_binding: dict[str, Any] | None = None
    harness_hashes: dict[str, str] = {}
    harness_postcheck = False
    samples: list[dict[str, Any]] = []
    markers: list[dict[str, Any]] = []
    markers_ok = False
    run_failures: list[str] = []
    forced_termination = False
    normal_exit = False
    t0_wall: float | None = None
    t0_ms: int | None = None
    shutdown = {
        "quit_ipc_ok": False, "popen_exit_code": None,
        "app_pid_exited": False, "forced_termination": False,
        "normal_exit": False, "residual_app_process": False,
    }

    try:
        binding = bind_target(
            args.target, args.artifact_dir, args.receipt, run_dir)
        harness_hashes = verify_harness_inputs(binding, (
            "scripts/soak_24h.py", "scripts/perf_sample.py"))
        target = Target(binding, run_dir)
        t0_wall = time.time()
        t0_ms = time.monotonic_ns() // 1_000_000
        proc = target.spawn(data_dir, instance, window_report)
        app_report = wait_for_app_report(proc, window_report)
        app_pid, app_hwnd = app_report["pid"], app_report["hwnd"]
        verified_process = VerifiedProcess(
            app_pid, app_hwnd, Path(binding.command[0]), t0_wall)
        process_binding = verified_process.report()
        metrics = ProcessMetrics(verified_process)

        target.ipc("show", instance)
        wait_for_window_visibility(proc, app_hwnd, True)
        measure_scene(
            metrics, proc, scene="visible_idle", phase="warmup",
            duration_s=warmup_s, interval_s=interval_s, samples=samples)
        measure_scene(
            metrics, proc, scene="visible_idle", phase="measure",
            duration_s=scene_s, interval_s=interval_s, samples=samples)

        # V12-08: panel-open state (countdown panel visible and updating
        # while the pet body stays composed)
        target.ipc("panel", instance)
        time.sleep(2.0)
        measure_scene(
            metrics, proc, scene="panel_open", phase="warmup",
            duration_s=warmup_s, interval_s=interval_s, samples=samples)
        measure_scene(
            metrics, proc, scene="panel_open", phase="measure",
            duration_s=scene_s, interval_s=interval_s, samples=samples)
        target.ipc("panel-close", instance)
        time.sleep(2.0)

        target.ipc("hide", instance)
        wait_for_window_visibility(proc, app_hwnd, False)
        measure_scene(
            metrics, proc, scene="hidden", phase="warmup",
            duration_s=warmup_s, interval_s=interval_s, samples=samples)
        measure_scene(
            metrics, proc, scene="hidden", phase="measure",
            duration_s=scene_s, interval_s=interval_s, samples=samples)
    except Exception as exc:  # noqa: BLE001 - evidence must fail closed
        run_failures.append(str(exc) or type(exc).__name__)
    finally:
        if proc is not None and target is not None:
            shutdown = shutdown_target(
                proc, target, instance, app_pid, run_failures,
                verified_process=verified_process)
            forced_termination = shutdown["forced_termination"]
            normal_exit = shutdown["normal_exit"]
        if verified_process is not None:
            verified_process.close()

    try:
        markers = load_perf_markers(data_dir)
        if t0_ms is None:
            raise HarnessError("CreateProcess T0 was not recorded")
        validate_marker_window(
            markers, t0_ms, time.monotonic_ns() // 1_000_000)
        markers_ok = True
    except HarnessError as exc:
        run_failures.append(str(exc))
    try:
        error_count, log_errors = scan_run_logs(data_dir)
    except HarnessError as exc:
        error_count, log_errors = 0, []
        run_failures.append(str(exc))
    if binding is not None and binding.target == "exe":
        try:
            verify_artifact_unchanged(binding)
            assert binding.receipt_path is not None
            receipt_bytes = binding.receipt_path.read_bytes()
            if hashlib.sha256(receipt_bytes).hexdigest() \
                    != binding.identity["receipt_sha256"]:
                raise HarnessError("release receipt changed before capture")
            atomic_write(run_dir / "bound-release-receipt.json",
                         receipt_bytes)
        except HarnessError as exc:
            run_failures.append(str(exc))
        except OSError as exc:
            run_failures.append(f"release receipt capture failed: {exc}")
    if binding is not None:
        try:
            verify_harness_unchanged(binding, harness_hashes, (
                "scripts/soak_24h.py", "scripts/perf_sample.py"))
            harness_postcheck = True
        except HarnessError as exc:
            run_failures.append(str(exc))

    try:
        sample_evidence = write_jsonl_evidence(
            run_dir / "samples.jsonl", samples)
    except OSError as exc:
        sample_evidence = None
        run_failures.append(f"sample evidence write failed: {exc}")
    try:
        support = supporting_evidence(run_dir, data_dir)
    except OSError as exc:
        support = []
        run_failures.append(f"supporting evidence inventory failed: {exc}")

    scenes = ("visible_idle", "panel_open", "hidden")
    failures = evaluate_perf(
        samples=samples, scenes=scenes, scene_s=scene_s,
        interval_s=interval_s, markers_ok=markers_ok,
        error_count=error_count, run_failures=run_failures,
        normal_exit=normal_exit, forced_termination=forced_termination,
    )
    summaries: dict[str, Any] = {}
    for scene in scenes:
        measured = [
            sample for sample in samples
            if sample["scene"] == scene and sample["phase"] == "measure"
        ]
        summaries[scene] = summarize(
            measured, interval_s,
            bound_pid=app_pid) if measured else None
    first_paint = next(
        (marker for marker in markers
         if marker["marker"] == "first_pet_paint"), None)
    report = {
        "schema": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "evidence_class": evidence_class,
        "formal_performance_gate": False,
        "production_gate": False,
        "environment_policy": "python-qt-injection-cleared-v1",
        "harness": {
            "inputs": harness_hashes,
            "postcheck_unchanged": harness_postcheck,
        },
        "custom_timing": custom_timing,
        "target": args.target,
        "artifact": binding.report() if binding else None,
        "sample_interval_s": interval_s,
        "warmup_s": warmup_s,
        "scene_s": scene_s,
        "scenes": summaries,
        "launch": {
            "create_process_t0_epoch": t0_wall,
            "t0_monotonic_ms": t0_ms,
            "popen_pid": proc.pid if proc else None,
            "app_self_reported_pid": app_pid,
            "app_self_reported_hwnd": app_hwnd,
            "verified_process": process_binding,
            "pid_mismatch_broker": (
                proc is not None and app_pid is not None and proc.pid != app_pid),
            "first_pet_paint_latency_ms": (
                first_paint["t_ms"] - t0_ms)
            if first_paint and t0_ms is not None else None,
            "normal_exit": normal_exit,
            "forced_termination": forced_termination,
            "shutdown": shutdown,
        },
        "markers": markers,
        "log_error_count": error_count,
        "log_errors": log_errors,
        "sample_evidence": sample_evidence,
        "supporting_evidence": support,
        "evidence_complete": True,
        "provisional": True,
        "failures": failures,
        "result": "PASS" if not failures else "FAIL",
    }
    atomic_write(
        run_dir / "performance.json",
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"))
    print(json.dumps({
        "result": report["result"],
        "evidence_class": evidence_class,
        "formal_performance_gate": False,
        "artifact": report["artifact"],
        "scenes": summaries,
        "failures": failures,
        "evidence": str(run_dir),
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
