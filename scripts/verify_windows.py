"""Repeatable native-window verification harness (M0 gate evidence).

Runs against a REAL launched process (source run or frozen EXE) with an
isolated data directory and instance name, and checks the P0 contract of
docs/CONFORMANCE.md 7.1/7.2/7.4:

  A. HWND style/exstyle - no caption/thickframe/min-max boxes, tool window
     (no taskbar/Alt+Tab), layered (transparency);
  B. normal startup does not steal the foreground; the one-time character
     panel is reported and must not steal it either;
  C. persisted ``click_through=true`` shows up as native WS_EX_TRANSPARENT
     (the state from which only the tray can recover);
  D. normal exit through the same quit() the tray 退出 action calls
     (``--test-ipc-quit`` + ``--ipc-send quit``): the process exits BY
     ITSELF (no Stop-Process), state is persisted, the log shows the
     shutdown sequence.
  E. frozen ``--startup`` uses the exact quoted executable and stays quiet;
  F. a duplicate frozen ``--startup`` neither shows nor activates an already
     running hidden primary instance.

Usage:
    .venv\\Scripts\\python scripts\\verify_windows.py --target source
    .venv\\Scripts\\python scripts\\verify_windows.py --target exe

Evidence: evidence/<timestamp>-<target>/report.json (all raw values).

Exit code 0 = all checks passed, 1 = any failure (INVALID RUN = crash).
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from retirement_pet.build_info import (  # noqa: E402
    BUILD_ATTESTATION_PROTOCOL,
    BuildInfoError,
    validate_build_attestation,
    validate_build_info,
)

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
CHARACTER_ONBOARDING_CAMPAIGN = "character-choice-1"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetForegroundWindow.restype = wt.HWND
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindow.argtypes = [wt.HWND]


def get_window_long(hwnd: int, index: int) -> int:
    return int(user32.GetWindowLongPtrW(hwnd, index))


def find_pet_hwnd(
        report_file: Path, timeout_s: float = 30.0, *,
        require_onboarding: bool = False) -> dict | None:
    """Poll for the window report the pet writes after show().

    The owning pid of a window is NOT reliably the process pid in every
    environment (GUI broker/hosting setups), so the launched app reports
    ``{"pid", "hwnd", "onboarding"}`` itself via ``--report-window``;
    the harness then verifies native windows by HANDLE.  When requested, wait
    for the deferred first-run decision point rather than accepting the
    earlier pet-only report.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if report_file.is_file():
            try:
                data = json.loads(report_file.read_text(encoding="utf-8"))
                onboarding = data.get("onboarding")
                onboarding_ready = (
                    isinstance(onboarding, dict)
                    and onboarding.get("ready") is True
                )
                if data.get("hwnd") and (
                        not require_onboarding or onboarding_ready):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(0.2)
    return None


def hwnd_alive(hwnd: int) -> bool:
    return bool(user32.IsWindow(wt.HWND(hwnd)))


def hwnd_visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(wt.HWND(hwnd)))


def wait_hwnd_visibility(
        hwnd: int, expected_visible: bool, timeout_s: float = 5.0) -> bool:
    """Wait for an IPC-driven native visibility change to settle."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if hwnd_alive(hwnd) and hwnd_visible(hwnd) is expected_visible:
            return True
        time.sleep(0.1)
    return hwnd_alive(hwnd) and hwnd_visible(hwnd) is expected_visible


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Harness contract: the checks every completed run MUST contain, per
# target.  The go/no-go consumer re-derives the verdict from these
# instead of trusting the report's own result label (V12-08 OVR-01).
REQUIRED_CHECKS = {
    "source": (
        "A.window-found", "A.window-visible", "A.no-caption",
        "A.no-thickframe", "A.no-minmaxbox", "A.toolwindow",
        "A.layered", "B.onboarding-panel-reported",
        "B.startup-no-focus-steal", "C.window-visible",
        "C.click-through-exstyle", "C.toolwindow-under-click-through",
        "C.hwnd-destroyed", "C.exit-code-zero",
        "C.process-exited-by-itself", "C.ipc-quit-delivered",
    ),
    "exe": (
        "A.window-found", "A.window-visible", "A.no-caption",
        "A.no-thickframe", "A.no-minmaxbox", "A.toolwindow",
        "A.layered", "B.onboarding-panel-reported",
        "B.startup-no-focus-steal", "C.window-visible",
        "C.click-through-exstyle", "C.toolwindow-under-click-through",
        "C.hwnd-destroyed", "C.exit-code-zero",
        "C.process-exited-by-itself", "C.ipc-quit-delivered",
        "D.process-exited-by-itself", "D.no-error-storm",
        "D.shutdown-sequence-logged", "D.state-persisted",
        "D.onboarding-hwnd-destroyed", "D.ipc-quit-delivered",
        "D.hwnd-destroyed", "D.exit-code-zero",
        "E.startup-window-found", "E.startup-window-visible",
        "E.startup-no-focus-steal", "E.startup-no-onboarding-panel",
        "E.frozen-startup-command", "E.process-exited-by-itself",
        "E.hwnd-destroyed", "E.exit-code-zero", "E.ipc-quit-delivered",
        "F.primary-window-found", "F.primary-still-running",
        "F.primary-hidden-before-duplicate", "F.hide-delivered",
        "F.duplicate-exited-by-itself",
        "F.duplicate-did-not-change-focus",
        "F.duplicate-did-not-show-primary",
        "F.completed-campaign-has-no-panel",
        "F.primary-exited-by-itself", "F.primary-exit-code-zero",
        "F.ipc-quit-delivered", "F.duplicate-exit-code-zero",
    ),
}


class Report:
    def __init__(self, target: str, run_dir: Path):
        self.target = target
        self.run_dir = run_dir
        self.checks: list[dict] = []

    def check(self, name: str, passed: bool, detail: str, raw=None) -> bool:
        entry = {"name": name, "pass": bool(passed), "detail": detail}
        if raw is not None:
            entry["raw"] = raw
        self.checks.append(entry)
        print(f"  [{'ok' if passed else 'FAIL'}] {name}: {detail}")
        return bool(passed)

    def write(self, extra: dict) -> None:
        payload = {
            "harness": "scripts/verify_windows.py",
            "tier": "WINDOWS_INTEGRATION (external process)",
            "date": datetime.now().isoformat(timespec="seconds"),
            "commit": git_commit(),
            "target": self.target,
            "run_dir": str(self.run_dir),
            "checks": self.checks,
            **extra,
        }
        path = self.run_dir / "report.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"evidence written: {path}")


class Target:
    """Launch command abstraction for source runs and the frozen EXE."""

    def __init__(
            self, kind: str, *, artifact_dir: Path | None = None,
            expected_build_id: str | None = None,
            expected_exe_sha256: str | None = None):
        self.kind = kind
        self.build_info: dict | None = None
        self.exe_sha256: str | None = None
        self.attestation_protocol: int | None = None
        if kind == "source":
            python = ROOT / ".venv" / "Scripts" / "python.exe"
            if not python.is_file():
                raise SystemExit(f"venv python missing: {python}")
            self.cmd = [str(python), "-m", "retirement_pet.main"]
        else:
            if not expected_build_id or not expected_exe_sha256:
                raise SystemExit(
                    "exe target requires --expected-build-id and "
                    "--expected-exe-sha256")
            artifact_dir = (artifact_dir or (
                ROOT / "dist" / "RetirementPet")).resolve()
            exe = artifact_dir / "RetirementPet.exe"
            if not exe.is_file():
                raise SystemExit(f"exe missing: {exe} (run scripts/build.ps1 first)")
            try:
                raw = json.loads((
                    artifact_dir / "_internal" / "build-info.json"
                ).read_text(encoding="utf-8"))
                self.build_info = validate_build_info(raw)
            except (OSError, UnicodeError, json.JSONDecodeError,
                    BuildInfoError) as exc:
                raise SystemExit("artifact build identity is invalid") from exc
            self.exe_sha256 = sha256(exe)
            if self.build_info["build_id"] != expected_build_id:
                raise SystemExit("artifact build id differs from expected value")
            if self.exe_sha256.lower() != expected_exe_sha256.lower():
                raise SystemExit("artifact EXE hash differs from expected value")
            with tempfile.TemporaryDirectory(
                    prefix="retirement-pet-window-attestation-") as raw:
                probe_dir = Path(raw)
                output = probe_dir / "attestation.json"
                env = dict(os.environ)
                env["RETIREMENT_PET_DATA_DIR"] = str(probe_dir / "data")
                env["RETIREMENT_PET_INSTANCE_NAME"] = \
                    f"window-attestation-{probe_dir.name}"
                env.pop("QT_QPA_PLATFORM", None)
                try:
                    result = subprocess.run(
                        [str(exe), "--build-attestation-v2-out", str(output)],
                        cwd=probe_dir, env=env, capture_output=True,
                        timeout=30, check=False,
                    )
                    attestation = validate_build_attestation(json.loads(
                        output.read_text(encoding="utf-8")))
                except (OSError, subprocess.TimeoutExpired, UnicodeError,
                        json.JSONDecodeError, BuildInfoError) as exc:
                    raise SystemExit(
                        "frozen runtime attestation failed") from exc
                if (result.returncode != 0
                        or attestation["compiled_identity"] != self.build_info):
                    raise SystemExit(
                        "frozen runtime identity differs from artifact metadata")
                self.attestation_protocol = BUILD_ATTESTATION_PROTOCOL
            self.cmd = [str(exe)]

    def spawn(self, extra_args: list[str], data_dir: Path, instance: str,
              log_note: str) -> subprocess.Popen:
        env = dict(os.environ)
        env["RETIREMENT_PET_DATA_DIR"] = str(data_dir)
        env["RETIREMENT_PET_INSTANCE_NAME"] = instance
        env.pop("QT_QPA_PLATFORM", None)  # never verify against offscreen
        if self.kind == "source":
            # src-layout without an installed package (tests use the same
            # path injection via conftest).
            env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        data_dir.mkdir(parents=True, exist_ok=True)
        print(f"==> launch [{self.kind}] {log_note}: {' '.join(self.cmd + extra_args)}")
        launch_cwd = ROOT if self.kind == "source" else data_dir
        return subprocess.Popen(
            self.cmd + extra_args, cwd=str(launch_cwd), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def ipc(self, command: str, instance: str) -> bool:
        env = dict(os.environ)
        env["RETIREMENT_PET_INSTANCE_NAME"] = instance
        if self.kind == "source":
            env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        ipc_cwd = ROOT if self.kind == "source" else Path(os.environ["TEMP"])
        result = subprocess.run(
            self.cmd + ["--ipc-send", command], cwd=str(ipc_cwd), env=env,
            capture_output=True, timeout=30,
        )
        return result.returncode == 0

    def diagnostic(
            self, arguments: list[str], data_dir: Path, instance: str) \
            -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["RETIREMENT_PET_DATA_DIR"] = str(data_dir)
        env["RETIREMENT_PET_INSTANCE_NAME"] = instance
        env.pop("QT_QPA_PLATFORM", None)
        if self.kind == "source":
            env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + \
                env.get("PYTHONPATH", "")
        cwd = ROOT if self.kind == "source" else data_dir
        return subprocess.run(
            self.cmd + arguments, cwd=cwd, env=env,
            capture_output=True, timeout=30, check=False,
        )


def wait_exited(proc: subprocess.Popen, timeout_s: float = 20.0) -> bool:
    try:
        proc.wait(timeout=timeout_s)
        return True
    except subprocess.TimeoutExpired:
        return False


def wait_hwnd_destroyed(hwnd: int, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not hwnd_alive(hwnd):
            return True
        time.sleep(0.1)
    return not hwnd_alive(hwnd)


def verify_log_health(report: Report, prefix: str, data_dir: Path) -> bool:
    logs = list((data_dir / "logs").glob("*.log")) \
        if (data_dir / "logs").is_dir() else []
    errors: list[str] = []
    for path in logs:
        for line in path.read_text(
                encoding="utf-8", errors="replace").splitlines():
            if ("ERROR" in line or "Traceback" in line
                    or "Unhandled exception" in line):
                errors.append(line[:160])
    return report.check(
        f"{prefix}.no-error-storm", not errors,
        "run log must contain no ERROR/Traceback lines",
        {"error_count": len(errors), "sample": errors[:5],
         "log_files": [path.name for path in logs]},
    )


def verify_phase_normal(target: Target, report: Report, run_dir: Path) -> bool:
    """Phase A: window contract, focus discipline, normal exit."""
    ok = True
    data_dir = run_dir / "data-a"
    instance = f"verify-a-{int(time.time())}"
    report_file = run_dir / "window-a.json"
    foreground_before = int(user32.GetForegroundWindow() or 0)

    proc = target.spawn(
        ["--test-ipc-quit", "--report-window", str(report_file)],
        data_dir, instance, "normal startup",
    )
    try:
        # A fresh data directory must exercise the real first-run choice.  Do
        # not inspect focus until the deferred panel offer has been reported.
        info = find_pet_hwnd(report_file, require_onboarding=True)
        if info is None or not hwnd_alive(info["hwnd"]):
            report.check("A.window-found", False,
                         f"pet window never appeared (pid={proc.pid}, alive={proc.poll() is None})")
            return False
        hwnd = int(info["hwnd"])
        report.check("A.window-found", True,
                     f"pid={info['pid']} hwnd={hwnd:#x} (hosting_pid_differs={info['pid'] != proc.pid})")
        ok &= report.check(
            "A.window-visible", hwnd_visible(hwnd),
            "normal-startup pet HWND must be visible")

        onboarding = info.get("onboarding", {})
        panel_hwnd = int(onboarding.get("panel_hwnd") or 0)
        ok &= report.check(
            "B.onboarding-panel-reported",
            onboarding.get("campaign") == CHARACTER_ONBOARDING_CAMPAIGN
            and onboarding.get("state") == "offered"
            and onboarding.get("offered") is True
            and onboarding.get("panel_visible") is True
            and panel_hwnd != 0,
            "fresh manual startup must report the one-time character panel",
            onboarding,
        )
        ok &= report.check(
            "B.onboarding-panel-visible",
            panel_hwnd != 0 and hwnd_alive(panel_hwnd)
            and hwnd_visible(panel_hwnd),
            "reported character panel HWND must be alive and visible",
            {"panel": hex(panel_hwnd)},
        )

        style = get_window_long(hwnd, GWL_STYLE)
        exstyle = get_window_long(hwnd, GWL_EXSTYLE)
        ok &= report.check(
            "A.no-caption", not (style & WS_CAPTION),
            "WS_CAPTION must be clear", {"style": hex(style)},
        )
        ok &= report.check(
            "A.no-thickframe", not (style & WS_THICKFRAME),
            "WS_THICKFRAME must be clear", {"style": hex(style)},
        )
        ok &= report.check(
            "A.no-minmaxbox",
            not (style & (WS_MINIMIZEBOX | WS_MAXIMIZEBOX)),
            "minimize/maximize boxes must be absent", {"style": hex(style)},
        )
        ok &= report.check(
            "A.toolwindow", bool(exstyle & WS_EX_TOOLWINDOW),
            "WS_EX_TOOLWINDOW must be set (no taskbar/Alt+Tab entry)",
            {"exstyle": hex(exstyle)},
        )
        ok &= report.check(
            "A.layered", bool(exstyle & WS_EX_LAYERED),
            "WS_EX_LAYERED must be set (transparency active)",
            {"exstyle": hex(exstyle)},
        )

        # Allow ShowWithoutActivating and the native show event to settle.
        time.sleep(0.5)
        foreground_after = int(user32.GetForegroundWindow() or 0)
        # Neither application top-level window may become foreground.  The
        # foreground may legitimately change for unrelated reasons in a live
        # session, so before/after are recorded as raw evidence.
        ok &= report.check(
            "B.startup-no-focus-steal",
            foreground_after not in {hwnd, panel_hwnd},
            "normal startup must not foreground the pet or first-run panel",
            {"before": hex(foreground_before), "after": hex(foreground_after),
             "pet": hex(hwnd), "panel": hex(panel_hwnd)},
        )

        # Normal exit: same quit() the tray 退出 action calls.
        sent = target.ipc("quit", instance)
        ok &= report.check("D.ipc-quit-delivered", sent, "--ipc-send quit accepted")
        exited = wait_exited(proc)
        ok &= report.check(
            "D.process-exited-by-itself", exited,
            "process must exit normally within 20s (no force kill)",
            {"returncode": proc.returncode if exited else "timeout"},
        )
        if exited:
            ok &= report.check(
                "D.exit-code-zero", proc.returncode == 0,
                f"returncode={proc.returncode}",
            )
            ok &= report.check(
                "D.hwnd-destroyed", wait_hwnd_destroyed(hwnd),
                "native pet HWND must be destroyed after normal exit")
            ok &= report.check(
                "D.onboarding-hwnd-destroyed",
                panel_hwnd == 0 or wait_hwnd_destroyed(panel_hwnd),
                "native first-run panel HWND must be destroyed after exit")
        ok &= report.check(
            "D.state-persisted", (data_dir / "state.json").is_file(),
            "shutdown path must persist state.json",
        )
        logs = list((data_dir / "logs").glob("*.log")) if (data_dir / "logs").is_dir() else []
        log_texts = [p.read_text(encoding="utf-8", errors="replace")
                     for p in logs]
        shutdown_logged = any("shutting down" in text for text in log_texts)
        ok &= report.check(
            "D.shutdown-sequence-logged", shutdown_logged,
            "log must contain the shutdown sequence",
            {"log_files": [p.name for p in logs]},
        )
        # P0-E: any ERROR / Traceback / unhandled exception in the run log
        # fails the harness - an error storm is a failure, not noise.
        error_lines = []
        for text in log_texts:
            for line in text.splitlines():
                if ("ERROR" in line or "Traceback" in line
                        or "Unhandled exception" in line):
                    error_lines.append(line[:160])
        ok &= report.check(
            "D.no-error-storm", not error_lines,
            "run log must contain no ERROR/Traceback lines",
            {"error_count": len(error_lines), "sample": error_lines[:5]},
        )
        return ok
    finally:
        if proc.poll() is None:  # failure path cleanup only
            proc.kill()
            proc.wait(timeout=10)


def verify_phase_click_through(target: Target, report: Report, run_dir: Path) -> bool:
    """Phase C: persisted click-through is visible natively; still exitable."""
    ok = True
    data_dir = run_dir / "data-b"
    instance = f"verify-b-{int(time.time())}"
    report_file = run_dir / "window-b.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "settings.json").write_text(
        json.dumps({
            "click_through": True,
            "character_onboarding_campaign": CHARACTER_ONBOARDING_CAMPAIGN,
        }, ensure_ascii=False), encoding="utf-8"
    )

    proc = target.spawn(
        ["--test-ipc-quit", "--report-window", str(report_file)],
        data_dir, instance, "click-through startup",
    )
    try:
        info = find_pet_hwnd(report_file)
        if info is None or not hwnd_alive(info["hwnd"]):
            report.check("C.window-found", False,
                         f"pet window never appeared (pid={proc.pid}, alive={proc.poll() is None})")
            return False
        hwnd = int(info["hwnd"])
        time.sleep(1.0)  # let flags settle
        ok &= report.check(
            "C.window-visible", hwnd_visible(hwnd),
            "click-through pet HWND must remain visible")
        exstyle = get_window_long(hwnd, GWL_EXSTYLE)
        ok &= report.check(
            "C.click-through-exstyle", bool(exstyle & WS_EX_TRANSPARENT),
            "persisted click_through=true must yield WS_EX_TRANSPARENT",
            {"exstyle": hex(exstyle)},
        )
        ok &= report.check(
            "C.toolwindow-under-click-through", bool(exstyle & WS_EX_TOOLWINDOW),
            "window contract must hold while click-through",
            {"exstyle": hex(exstyle)},
        )
        sent = target.ipc("quit", instance)
        ok &= report.check("C.ipc-quit-delivered", sent, "--ipc-send quit accepted")
        exited = wait_exited(proc)
        ok &= report.check(
            "C.process-exited-by-itself", exited,
            "must exit normally even after click-through startup",
        )
        if exited:
            ok &= report.check(
                "C.exit-code-zero", proc.returncode == 0,
                f"returncode={proc.returncode}")
            ok &= report.check(
                "C.hwnd-destroyed", wait_hwnd_destroyed(hwnd),
                "native pet HWND must be destroyed after normal exit")
        ok &= verify_log_health(report, "C", data_dir)
        return ok
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def verify_phase_frozen_startup(
        target: Target, report: Report, run_dir: Path) -> bool:
    """Frozen ``--startup`` uses its own quoted path and stays focus-quiet."""
    if target.kind != "exe":
        return True
    ok = True
    data_dir = run_dir / "data-startup"
    data_dir.mkdir(parents=True, exist_ok=True)
    instance = f"verify-startup-{int(time.time())}"
    report_file = run_dir / "window-startup.json"
    command_file = run_dir / "startup-command.txt"
    (data_dir / "settings.json").write_text(json.dumps({
        "startup_delay_s": 0,
        "always_on_top": True,
    }), encoding="utf-8")

    command_probe = target.diagnostic(
        ["--startup-command-out", str(command_file)], data_dir, instance)
    expected_command = f'"{Path(target.cmd[0]).resolve()}" --startup'
    observed_command = command_file.read_text(encoding="utf-8") \
        if command_file.is_file() else ""
    ok &= report.check(
        "E.frozen-startup-command",
        command_probe.returncode == 0 and observed_command == expected_command,
        "HKCU Run command must quote this exact frozen EXE with --startup",
        {"reported": observed_command, "expected": expected_command},
    )

    foreground_before = int(user32.GetForegroundWindow() or 0)
    proc = target.spawn(
        ["--startup", "--test-ipc-quit", "--report-window", str(report_file)],
        data_dir, instance, "frozen startup mode",
    )
    try:
        # Autostart intentionally never runs the deferred onboarding offer;
        # the pet-only report is the expected readiness boundary here.
        info = find_pet_hwnd(report_file, require_onboarding=True)
        if info is None or not hwnd_alive(info["hwnd"]):
            report.check("E.startup-window-found", False,
                         "startup-mode pet window never appeared")
            return False
        # Observe a short post-ready interval as well; a regression that
        # schedules a delayed onboarding panel must replace the report and be
        # caught before this phase quits the application.
        time.sleep(0.5)
        settled = find_pet_hwnd(
            report_file, timeout_s=1.0, require_onboarding=True)
        if settled is not None:
            info = settled
        hwnd = int(info["hwnd"])
        onboarding = info.get("onboarding", {})
        foreground_after = int(user32.GetForegroundWindow() or 0)
        ok &= report.check(
            "E.startup-window-found", True,
            f"startup mode reported hwnd={hwnd:#x}")
        ok &= report.check(
            "E.startup-window-visible", hwnd_visible(hwnd),
            "startup-mode pet HWND must be visible")
        ok &= report.check(
            "E.startup-no-onboarding-panel",
            onboarding.get("state") == "suppressed_autostart"
            and onboarding.get("offered") is False
            and not onboarding.get("panel_visible")
            and not onboarding.get("panel_hwnd"),
            "--startup must not create or show the first-run panel",
            onboarding,
        )
        ok &= report.check(
            "E.startup-no-focus-steal", foreground_after != hwnd,
            "startup mode must not make the pet foreground",
            {"before": hex(foreground_before), "after": hex(foreground_after),
             "pet": hex(hwnd)},
        )
        sent = target.ipc("quit", instance)
        ok &= report.check("E.ipc-quit-delivered", sent,
                           "startup-mode IPC quit accepted")
        exited = wait_exited(proc)
        ok &= report.check("E.process-exited-by-itself", exited,
                           "startup-mode process exits normally")
        if exited:
            ok &= report.check("E.exit-code-zero", proc.returncode == 0,
                               f"returncode={proc.returncode}")
            ok &= report.check(
                "E.hwnd-destroyed", wait_hwnd_destroyed(hwnd),
                "startup-mode HWND must be destroyed after normal exit")
        ok &= verify_log_health(report, "E", data_dir)
        return ok
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def verify_phase_duplicate_frozen_startup(
        target: Target, report: Report, run_dir: Path) -> bool:
    """An already-running frozen primary treats ``--startup`` as quiet."""
    if target.kind != "exe":
        return True
    ok = True
    data_dir = run_dir / "data-duplicate-startup"
    data_dir.mkdir(parents=True, exist_ok=True)
    instance = f"verify-duplicate-startup-{int(time.time())}"
    report_file = run_dir / "window-duplicate-startup.json"
    (data_dir / "settings.json").write_text(json.dumps({
        "startup_delay_s": 0,
        "character_onboarding_campaign": CHARACTER_ONBOARDING_CAMPAIGN,
    }), encoding="utf-8")

    primary = target.spawn(
        ["--test-ipc-quit", "--report-window", str(report_file)],
        data_dir, instance, "primary for duplicate frozen startup",
    )
    secondary: subprocess.Popen | None = None
    try:
        info = find_pet_hwnd(report_file, require_onboarding=True)
        if info is None or not hwnd_alive(info["hwnd"]):
            report.check("F.primary-window-found", False,
                         "primary pet window never appeared")
            return False
        hwnd = int(info["hwnd"])
        onboarding = info.get("onboarding", {})
        ok &= report.check(
            "F.primary-window-found", True,
            f"primary reported hwnd={hwnd:#x}")
        ok &= report.check(
            "F.completed-campaign-has-no-panel",
            onboarding.get("state") == "completed"
            and onboarding.get("offered") is False
            and not onboarding.get("panel_visible")
            and not onboarding.get("panel_hwnd"),
            "preseeded campaign must not open a character panel",
            onboarding,
        )

        hidden_sent = target.ipc("hide", instance)
        ok &= report.check(
            "F.hide-delivered", hidden_sent,
            "test IPC hide accepted before duplicate startup")
        hidden_before = hidden_sent and wait_hwnd_visibility(hwnd, False)
        ok &= report.check(
            "F.primary-hidden-before-duplicate", hidden_before,
            "primary pet must be natively hidden before duplicate startup")
        foreground_before = int(user32.GetForegroundWindow() or 0)

        secondary = target.spawn(
            ["--startup"], data_dir, instance,
            "duplicate frozen startup while primary is hidden",
        )
        duplicate_exited = wait_exited(secondary, timeout_s=10.0)
        ok &= report.check(
            "F.duplicate-exited-by-itself", duplicate_exited,
            "duplicate --startup must notify quietly and exit",
            {"returncode": secondary.returncode
             if duplicate_exited else "timeout"},
        )
        if duplicate_exited:
            ok &= report.check(
                "F.duplicate-exit-code-zero", secondary.returncode == 0,
                f"returncode={secondary.returncode}")

        # A SHOW command would make the native visibility check fail.  Exact
        # foreground equality is intentionally frozen for this short gate:
        # the operator must not interact with other windows during capture.
        time.sleep(0.5)
        foreground_after = int(user32.GetForegroundWindow() or 0)
        ok &= report.check(
            "F.duplicate-did-not-show-primary",
            hwnd_alive(hwnd) and not hwnd_visible(hwnd),
            "duplicate --startup must leave the hidden primary hidden")
        ok &= report.check(
            "F.duplicate-did-not-change-focus",
            foreground_after == foreground_before
            and foreground_after != hwnd,
            "duplicate --startup must not activate or change foreground",
            {"before": hex(foreground_before),
             "after": hex(foreground_after), "pet": hex(hwnd)},
        )
        ok &= report.check(
            "F.primary-still-running", primary.poll() is None,
            "quiet duplicate must not terminate the primary")

        sent = target.ipc("quit", instance)
        ok &= report.check("F.ipc-quit-delivered", sent,
                           "primary IPC quit accepted")
        exited = wait_exited(primary)
        ok &= report.check(
            "F.primary-exited-by-itself", exited,
            "primary exits normally after duplicate-startup gate")
        if exited:
            ok &= report.check(
                "F.primary-exit-code-zero", primary.returncode == 0,
                f"returncode={primary.returncode}")
            ok &= report.check(
                "F.primary-hwnd-destroyed", wait_hwnd_destroyed(hwnd),
                "primary HWND must be destroyed after normal exit")
        ok &= verify_log_health(report, "F", data_dir)
        return ok
    finally:
        if secondary is not None and secondary.poll() is None:
            secondary.kill()
            secondary.wait(timeout=10)
        if primary.poll() is None:
            primary.kill()
            primary.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["source", "exe"], default="source")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--expected-build-id")
    parser.add_argument("--expected-exe-sha256")
    parser.add_argument("--evidence-root", type=Path,
                        default=ROOT / "evidence")
    args = parser.parse_args()

    target = Target(
        args.target, artifact_dir=args.artifact_dir,
        expected_build_id=args.expected_build_id,
        expected_exe_sha256=args.expected_exe_sha256,
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    identity_prefix = (
        target.build_info["build_id"][:12]
        if target.build_info is not None else "source")
    # The report path is handed to the launched app, which resolves it
    # against ITS OWN cwd (the per-scenario data dir); a relative
    # evidence root would land the report where neither side looks.
    # The harness therefore always works with absolute paths.
    args.evidence_root = args.evidence_root.resolve()
    run_dir = args.evidence_root / (
        f"{stamp}-{identity_prefix}-{args.target}-verify-windows-"
        f"{uuid4().hex[:8]}")
    run_dir.mkdir(parents=True, exist_ok=False)

    report = Report(args.target, run_dir)
    print(f"==> verify_windows [{args.target}] evidence dir: {run_dir}")

    ok = verify_phase_normal(target, report, run_dir)
    ok &= verify_phase_click_through(target, report, run_dir)
    ok &= verify_phase_frozen_startup(target, report, run_dir)
    ok &= verify_phase_duplicate_frozen_startup(target, report, run_dir)

    report.write({
        "result": "PASS" if ok else "FAIL",
        "artifact": target.build_info,
        "exe_sha256": target.exe_sha256,
        "attestation_protocol": target.attestation_protocol,
        "python": sys.version.split()[0],
        "windows": {
            "build": getattr(sys.getwindowsversion(), "build", None),
            "major": getattr(sys.getwindowsversion(), "major", None),
            "minor": getattr(sys.getwindowsversion(), "minor", None),
        },
    })
    print(f"==> {('PASS' if ok else 'FAIL')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
