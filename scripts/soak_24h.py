"""Fail-closed stability harness for source and receipt-bound EXE runs.

The three profiles make deliberately different claims:

* ``machinery`` only checks that the harness machinery works;
* ``alpha`` is a 60--120 second initial-trial stability observation;
* ``24h`` is fixed at exactly 86,400 seconds and emits only a
  ``SOAK_24H_OBSERVATION``; it is not a formal 24-hour or Production gate.

EXE runs require an explicit artifact directory and release receipt.  The
receipt is checked against the complete artifact inventory, the executable
hash, external build metadata, and the v2 identity compiled into the EXE.
Evidence is always written below an explicit ``--evidence-root``.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
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

VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
_HEX = frozenset("0123456789abcdef")
_RECEIPT_KEYS = {
    "schema", "result", "recipe_id", "artifact_id", "exe_sha256",
    "commit", "git_tree", "file_count", "total_bytes",
    "attestation_protocol",
}
_ERROR_MARKERS = ("ERROR", "CRITICAL", "Traceback")
_STILL_ACTIVE = 259
_INJECTION_ENV = {
    "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT",
    "PYTHONUSERBASE", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM",
    "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QML_IMPORT_PATH",
    "PYSIDE_DESIGNER_PLUGINS",
}
_STEADY_STATE_ONBOARDING_CAMPAIGN = "character-choice-1"


class HarnessError(RuntimeError):
    """A lifecycle, provenance, sampling, or evidence condition failed."""


def sanitized_environment(
        target: str, data_dir: Path, instance: str,
        artifact_dir: Path | None = None) -> dict[str, str]:
    """Remove Python/Qt injection and isolate the frozen DLL search path."""
    env = dict(os.environ)
    for name in _INJECTION_ENV:
        env.pop(name, None)
    env["RETIREMENT_PET_DATA_DIR"] = str(data_dir)
    env["RETIREMENT_PET_INSTANCE_NAME"] = instance
    env["PYTHONNOUSERSITE"] = "1"
    if target == "source":
        env["PYTHONPATH"] = str(SRC)
        pyside = ROOT / ".venv" / "Lib" / "site-packages" / "PySide6"
        env["PATH"] = os.pathsep.join((
            str(ROOT / ".venv" / "Scripts"), str(pyside),
            env.get("PATH", ""),
        ))
    else:
        if artifact_dir is None:
            raise HarnessError("frozen environment requires an artifact path")
        system_drive = env.get("SystemDrive", "C:")
        windows = Path(env.get("SystemRoot") or env.get("WINDIR")
                       or str(Path(system_drive) / "Windows"))
        env["PATH"] = os.pathsep.join(str(path) for path in (
            artifact_dir, artifact_dir / "_internal",
            windows / "System32", windows,
        ))
    return env


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def prepare_steady_state_data_dir(data_dir: Path) -> Path:
    """Preseed settings so steady-state evidence excludes first-run UI."""
    data_dir = Path(data_dir)
    settings_path = data_dir / "settings.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    settings: dict[str, Any] = {}
    if settings_path.is_file():
        try:
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HarnessError(
                "steady-state settings are unreadable") from exc
        if not isinstance(raw, dict):
            raise HarnessError("steady-state settings root is not an object")
        settings = raw
    settings["character_onboarding_campaign"] = (
        _STEADY_STATE_ONBOARDING_CAMPAIGN)
    try:
        atomic_write(
            settings_path,
            (json.dumps(
                settings, ensure_ascii=False, indent=2, sort_keys=True
            ) + "\n").encode("utf-8"),
        )
        persisted = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessError(
            "steady-state settings could not be persisted") from exc
    if (not isinstance(persisted, dict)
            or persisted.get("character_onboarding_campaign")
            != _STEADY_STATE_ONBOARDING_CAMPAIGN):
        raise HarnessError("steady-state onboarding marker was not persisted")
    return settings_path


def write_jsonl_evidence(
        path: Path, values: list[dict[str, Any]]) -> dict[str, Any]:
    data = "".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
        for value in values
    ).encode("utf-8")
    atomic_write(path, data)
    return {
        "path": Path(path).name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "line_count": len(values),
    }


def supporting_evidence(run_dir: Path, data_dir: Path) -> list[dict[str, Any]]:
    paths = list(Path(run_dir).glob("primary.*.log"))
    paths.extend(Path(run_dir).glob("ipc-transcript.jsonl"))
    paths.extend(Path(data_dir).rglob("*"))
    paths.extend(Path(run_dir).glob("runtime-attestation-v2.json"))
    paths.extend(Path(run_dir).glob("bound-release-receipt.json"))
    records = []
    run_dir_resolved = Path(run_dir).resolve()
    archive_root = run_dir_resolved / "prepared-data"
    for path in sorted(set(path for path in paths if path.is_file())):
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(run_dir_resolved).as_posix()
        except ValueError:
            # a prepared data dir may live outside the run dir (V12-08
            # --data-dir): COPY it into the evidence so the recorded
            # hash is always backed by an archived file
            target = archive_root / resolved.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(resolved, target)
            relative = target.relative_to(run_dir_resolved).as_posix()
        records.append({
            "path": relative,
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        })
    return records


def _is_hex(value: object, length: int) -> bool:
    return (isinstance(value, str) and len(value) == length
            and set(value).issubset(_HEX))


def load_receipt(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessError("release receipt is unreadable") from exc
    if (not isinstance(value, dict) or set(value) != _RECEIPT_KEYS
            or value.get("schema") != 1 or value.get("result") != "PASS"):
        raise HarnessError("release receipt has an invalid schema or result")
    for field, length in (("recipe_id", 64), ("artifact_id", 64),
                          ("exe_sha256", 64), ("commit", 40),
                          ("git_tree", 40)):
        if not _is_hex(value.get(field), length):
            raise HarnessError(f"release receipt has invalid {field}")
    if (not isinstance(value.get("file_count"), int)
            or value["file_count"] <= 0
            or not isinstance(value.get("total_bytes"), int)
            or value["total_bytes"] <= 0
            or value.get("attestation_protocol")
            != BUILD_ATTESTATION_PROTOCOL):
        raise HarnessError("release receipt has invalid counts or protocol")
    return value


def artifact_inventory(artifact_dir: Path) -> tuple[str, int, int]:
    """Return the receipt-compatible tree hash, file count, and byte count."""
    artifact_dir = Path(artifact_dir)
    if not artifact_dir.is_dir():
        raise HarnessError("artifact directory is missing")
    lines: list[str] = []
    total_bytes = 0
    for path in sorted(artifact_dir.rglob("*")):
        try:
            stat = path.lstat()
        except OSError as exc:
            raise HarnessError("artifact inventory is unreadable") from exc
        attributes = getattr(stat, "st_file_attributes", 0)
        if path.is_symlink() or attributes & 0x400:
            raise HarnessError("artifact contains a forbidden reparse point")
        if not path.is_file():
            continue
        relative = path.relative_to(artifact_dir).as_posix()
        total_bytes += stat.st_size
        lines.append(f"{_sha256(path)}  {relative}")
    digest = hashlib.sha256(
        ("\n".join(lines) + "\n").encode("utf-8")).hexdigest()
    return digest, len(lines), total_bytes


def _load_external_build_info(artifact_dir: Path) -> dict[str, Any]:
    path = artifact_dir / "_internal" / "build-info.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return validate_build_info(value)
    except (OSError, UnicodeError, json.JSONDecodeError,
            BuildInfoError) as exc:
        raise HarnessError("artifact build info is missing or invalid") from exc


def _probe_compiled_identity(
        exe: Path, run_dir: Path, expected: dict[str, Any]) -> None:
    output = run_dir / "runtime-attestation-v2.json"
    data_dir = run_dir / "attestation-data"
    env = sanitized_environment(
        "exe", data_dir, f"attest-{uuid4().hex}", exe.parent)
    try:
        result = subprocess.run(
            [str(exe), "--build-attestation-v2-out", str(output)],
            cwd=run_dir, env=env, capture_output=True, timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarnessError("frozen runtime identity probe failed") from exc
    if result.returncode != 0 or not output.is_file():
        raise HarnessError("frozen runtime identity probe failed")
    try:
        raw = json.loads(output.read_text(encoding="utf-8"))
        attestation = validate_build_attestation(raw)
    except (OSError, UnicodeError, json.JSONDecodeError,
            BuildInfoError) as exc:
        raise HarnessError("frozen runtime identity probe was invalid") from exc
    if attestation["compiled_identity"] != expected:
        raise HarnessError("compiled identity differs from external build info")


@dataclass(frozen=True)
class ArtifactBinding:
    target: str
    command: tuple[str, ...]
    identity: dict[str, Any]
    artifact_dir: Path | None = None
    receipt_path: Path | None = None

    def report(self) -> dict[str, Any]:
        return dict(self.identity)


def verify_artifact_unchanged(binding: ArtifactBinding) -> None:
    """Revalidate the full EXE artifact against its original binding."""
    if binding.target == "source":
        return
    if binding.artifact_dir is None or binding.receipt_path is None:
        raise HarnessError("EXE binding is incomplete")
    receipt = load_receipt(binding.receipt_path)
    identity = binding.identity
    if _sha256(binding.receipt_path) != identity.get("receipt_sha256"):
        raise HarnessError("release receipt changed during the run")
    expected = {
        "recipe_id": identity.get("build_id"),
        "artifact_id": identity.get("artifact_id"),
        "exe_sha256": identity.get("exe_sha256"),
        "commit": identity.get("commit"),
        "git_tree": identity.get("git_tree"),
    }
    if any(receipt[key] != value for key, value in expected.items()):
        raise HarnessError("release receipt differs from the bound identity")
    external = _load_external_build_info(binding.artifact_dir)
    if (external["build_id"] != identity.get("build_id")
            or external["commit"] != identity.get("commit")
            or external["git_tree"] != identity.get("git_tree")):
        raise HarnessError("artifact build info changed during the run")
    exe = binding.artifact_dir / "RetirementPet.exe"
    if not exe.is_file() or _sha256(exe) != identity.get("exe_sha256"):
        raise HarnessError("artifact executable changed during the run")
    tree_hash, file_count, total_bytes = artifact_inventory(
        binding.artifact_dir)
    if (tree_hash != identity.get("artifact_id")
            or file_count != receipt["file_count"]
            or total_bytes != receipt["total_bytes"]):
        raise HarnessError("artifact inventory changed during the run")


def verify_harness_inputs(
        binding: ArtifactBinding,
        relative_paths: tuple[str, ...]) -> dict[str, str]:
    """Bind EXE observations to harness bytes included in its build recipe."""
    observed = {
        relative: _sha256(ROOT / relative) for relative in relative_paths
    }
    if binding.target == "source":
        return observed
    expected = binding.identity.get("build_input_harnesses")
    if not isinstance(expected, dict):
        raise HarnessError("artifact does not bind harness input hashes")
    for relative in relative_paths:
        if expected.get(relative) != observed[relative]:
            raise HarnessError(
                f"running harness differs from artifact input: {relative}")
    return observed


def verify_harness_unchanged(
        binding: ArtifactBinding, initial: dict[str, str],
        relative_paths: tuple[str, ...]) -> None:
    if verify_harness_inputs(binding, relative_paths) != initial:
        raise HarnessError("harness bytes changed during the run")


def bind_target(
        target: str, artifact_dir: Path | None, receipt_path: Path | None,
        run_dir: Path, *, probe_runtime: bool = True) -> ArtifactBinding:
    if target == "source":
        if artifact_dir is not None or receipt_path is not None:
            raise HarnessError(
                "source target must not be labelled with artifact evidence")
        if not VENV_PYTHON.is_file():
            raise HarnessError("source virtual-environment Python is missing")
        return ArtifactBinding(
            target="source",
            command=(str(VENV_PYTHON), "-m", "retirement_pet.main"),
            identity={"artifact_kind": "source", "release_identity": False},
        )

    if artifact_dir is None or receipt_path is None:
        raise HarnessError(
            "EXE target requires explicit --artifact-dir and --receipt")
    artifact_dir = Path(artifact_dir).resolve()
    receipt_path = Path(receipt_path).resolve()
    receipt = load_receipt(receipt_path)
    exe = artifact_dir / "RetirementPet.exe"
    if not exe.is_file():
        raise HarnessError("artifact executable is missing")
    external = _load_external_build_info(artifact_dir)
    if (external["build_id"] != receipt["recipe_id"]
            or external["commit"] != receipt["commit"]
            or external["git_tree"] != receipt["git_tree"]):
        raise HarnessError("artifact build info differs from release receipt")
    if _sha256(exe) != receipt["exe_sha256"]:
        raise HarnessError("artifact executable hash differs from receipt")
    tree_hash, file_count, total_bytes = artifact_inventory(artifact_dir)
    if (tree_hash != receipt["artifact_id"]
            or file_count != receipt["file_count"]
            or total_bytes != receipt["total_bytes"]):
        raise HarnessError("artifact inventory differs from release receipt")
    if probe_runtime:
        _probe_compiled_identity(exe, run_dir, external)
    return ArtifactBinding(
        target="exe", command=(str(exe),),
        identity={
            "artifact_kind": "onedir",
            "build_id": receipt["recipe_id"],
            "artifact_id": receipt["artifact_id"],
            "exe_sha256": receipt["exe_sha256"],
            "commit": receipt["commit"],
            "git_tree": receipt["git_tree"],
            "receipt": str(receipt_path),
            "receipt_sha256": _sha256(receipt_path),
            "build_input_harnesses": {
                relative: external["inputs"]["files"].get(relative)
                for relative in (
                    "scripts/soak_24h.py", "scripts/perf_sample.py")
            },
        },
        artifact_dir=artifact_dir,
        receipt_path=receipt_path,
    )


class Target:
    def __init__(self, binding: ArtifactBinding, run_dir: Path):
        self.binding = binding
        self.run_dir = Path(run_dir)

    def env(self, data_dir: Path, instance: str) -> dict[str, str]:
        return sanitized_environment(
            self.binding.target, data_dir, instance,
            self.binding.artifact_dir)

    def spawn(
            self, data_dir: Path, instance: str,
            window_report: Path) -> subprocess.Popen:
        prepare_steady_state_data_dir(data_dir)
        with (self.run_dir / "primary.stdout.log").open("wb") as stdout, \
                (self.run_dir / "primary.stderr.log").open("wb") as stderr:
            return subprocess.Popen(
                [*self.binding.command, "--test-ipc-quit", "--report-window",
                 str(window_report)],
                cwd=self.run_dir, env=self.env(data_dir, instance),
                stdout=stdout, stderr=stderr,
            )

    def ipc(self, command: str, instance: str, *, timeout: float = 30) -> None:
        try:
            result = subprocess.run(
                [*self.binding.command, "--ipc-send", command],
                cwd=self.run_dir,
                env=self.env(self.run_dir / "ipc-data", instance),
                capture_output=True, timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HarnessError(f"IPC {command!r} failed") from exc
        output = (result.stdout or b"") + (result.stderr or b"")
        decoded = output.decode("utf-8", "replace") \
            if isinstance(output, bytes) else str(output)
        transcript = {
            "at_utc": datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"),
            "command": command,
            "returncode": result.returncode,
            "output": decoded[:8_192],
        }
        try:
            with (self.run_dir / "ipc-transcript.jsonl").open("ab") as handle:
                handle.write((json.dumps(
                    transcript, ensure_ascii=False, sort_keys=True) + "\n"
                ).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise HarnessError("IPC transcript could not be persisted") from exc
        if result.returncode != 0:
            raise HarnessError(
                f"IPC {command!r} returned {result.returncode}")
        if any(marker in decoded for marker in _ERROR_MARKERS):
            raise HarnessError(f"IPC {command!r} emitted an error")


def wait_for_app_report(
        proc: subprocess.Popen, report: Path, *,
        timeout_s: float = 60) -> dict[str, int]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise HarnessError(
                f"primary process exited during startup ({proc.returncode})")
        if report.is_file():
            try:
                value = json.loads(report.read_text(encoding="utf-8"))
                pid = value["pid"]
                hwnd = value["hwnd"]
                onboarding = value["onboarding"]
                steady_state_ready = (
                    isinstance(onboarding, dict)
                    and onboarding.get("campaign")
                    == _STEADY_STATE_ONBOARDING_CAMPAIGN
                    and onboarding.get("ready") is True
                    and onboarding.get("state") == "completed"
                    and onboarding.get("offered") is False
                    and not onboarding.get("panel_visible")
                    and not onboarding.get("panel_hwnd")
                )
                if (isinstance(pid, int) and not isinstance(pid, bool)
                        and pid > 0 and isinstance(hwnd, int)
                        and not isinstance(hwnd, bool) and hwnd > 0
                        and steady_state_ready):
                    return {"pid": pid, "hwnd": hwnd}
            except (OSError, UnicodeError, json.JSONDecodeError, KeyError):
                pass
        time.sleep(0.1)
    raise HarnessError(
        "application did not self-report a steady-state-ready PID and HWND")


def wait_for_app_pid(
        proc: subprocess.Popen, report: Path, *, timeout_s: float = 60) -> int:
    """Compatibility wrapper used by focused unit tests and callers."""
    return wait_for_app_report(proc, report, timeout_s=timeout_s)["pid"]


def wait_for_window_visibility(
        proc: subprocess.Popen, hwnd: int, expected: bool, *,
        timeout_s: float = 5) -> None:
    """Confirm that an IPC visibility request affected the real HWND."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise HarnessError(
                f"primary process exited while checking window ({proc.returncode})")
        if not user32.IsWindow(hwnd):
            raise HarnessError("self-reported window handle became invalid")
        if bool(user32.IsWindowVisible(hwnd)) is expected:
            return
        time.sleep(0.05)
    state = "visible" if expected else "hidden"
    raise HarnessError(f"window did not become {state} after IPC")


def _allowed_runtime_images(expected_image: Path) -> set[str]:
    allowed = {str(expected_image).casefold()}
    base = getattr(sys, "_base_executable", None)
    if base:
        allowed.add(str(Path(base).resolve()).casefold())
    return allowed


class VerifiedProcess:
    """Persistent, image-verified handle for the self-reported app process."""

    def __init__(
            self, pid: int, hwnd: int, expected_image: Path,
            launched_at_epoch: float):
        self.pid = pid
        self.hwnd = hwnd
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.OpenProcess.restype = wt.HANDLE
        self._handle = self._kernel32.OpenProcess(
            0x1000 | 0x00100000, False, pid)
        if not self._handle:
            raise HarnessError("could not hold the self-reported app process")
        try:
            size = wt.DWORD(32_768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self._kernel32.QueryFullProcessImageNameW(
                    self._handle, 0, buffer, ctypes.byref(size)):
                raise HarnessError("could not query app process image")
            observed = Path(buffer.value).resolve()
            allowed = _allowed_runtime_images(Path(expected_image).resolve())
            if str(observed).casefold() not in allowed:
                raise HarnessError(
                    f"self-reported PID image mismatch: {observed}")
            creation, exit_t, kernel, user = (
                wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME())
            if not self._kernel32.GetProcessTimes(
                    self._handle, ctypes.byref(creation), ctypes.byref(exit_t),
                    ctypes.byref(kernel), ctypes.byref(user)):
                raise HarnessError("could not query app process creation time")
            filetime = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            created_epoch = filetime / 10_000_000.0 - 11_644_473_600
            if created_epoch < launched_at_epoch - 2 \
                    or created_epoch > time.time() + 2:
                raise HarnessError(
                    "self-reported PID predates the harness launch")
            owner = wt.DWORD()
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner)):
                raise HarnessError("could not resolve self-reported HWND owner")
            self.image = str(observed)
            self.created_epoch = created_epoch
            self.hwnd_owner_pid = int(owner.value)
        except Exception:
            self.close()
            raise

    @property
    def handle(self):
        return self._handle

    def is_alive(self) -> bool:
        if not self._handle:
            return False
        exit_code = wt.DWORD()
        return bool(self._kernel32.GetExitCodeProcess(
            self._handle, ctypes.byref(exit_code))) \
            and exit_code.value == _STILL_ACTIVE

    def wait_exit(self, timeout_s: float) -> bool:
        if not self._handle:
            return True
        return self._kernel32.WaitForSingleObject(
            self._handle, max(0, int(timeout_s * 1000))) == 0

    def report(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "image": self.image,
            "created_epoch": self.created_epoch,
            "hwnd": self.hwnd,
            "hwnd_owner_pid": self.hwnd_owner_pid,
            "broker_owner_mismatch": self.hwnd_owner_pid != self.pid,
            "persistent_handle": True,
        }

    def close(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            self._kernel32.CloseHandle(handle)
            self._handle = None


def shutdown_target(
        proc: subprocess.Popen, target: Target, instance: str,
        app_pid: int | None, failures: list[str], *,
        verified_process: VerifiedProcess | None = None) -> dict[str, Any]:
    """Request normal exit, verify both Popen and app PID, then clean up."""
    quit_ok = False
    forced = False
    app_was_alive = verified_process.is_alive() \
        if verified_process is not None else False
    if proc.poll() is None or app_was_alive:
        try:
            target.ipc("quit", instance)
            quit_ok = True
        except HarnessError as exc:
            failures.append(str(exc))
    try:
        if proc.poll() is None:
            proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        forced = True
        failures.append("launcher process did not exit after quit IPC")
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            failures.append("launcher remained alive after forced termination")
    app_exited = verified_process is not None \
        and verified_process.wait_exit(10)
    if app_pid is not None and not app_exited:
        forced = True
        failures.append("self-reported application PID did not exit after quit IPC")
        # Never TerminateProcess an untrusted bare PID: after process exit the
        # number can be reused by an unrelated application.  Only the Popen
        # handle created by this harness is eligible for forced cleanup.
        failures.append(
            "residual app PID was not force-killed because no persistent "
            "verified process handle is available")
    normal = (
        quit_ok and not forced and proc.poll() == 0 and app_exited
    )
    return {
        "quit_ipc_ok": quit_ok,
        "popen_exit_code": proc.poll(),
        "app_pid_exited": app_exited,
        "residual_app_process": not app_exited,
        "forced_termination": forced,
        "normal_exit": normal,
    }


class ProcessMetrics:
    """Windows process CPU time and Private Bytes for the self-reported PID."""

    def __init__(self, process: VerifiedProcess):
        self.process = process
        self.pid = process.pid
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._psapi = ctypes.WinDLL("psapi", use_last_error=True)

        class PMC(ctypes.Structure):
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
        self._PMC = PMC

    def sample(self) -> dict[str, int] | None:
        handle = self.process.handle
        if not handle or not self.process.is_alive():
            return None
        creation, exit_t, kernel, user = (
            wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME())
        if not self._k32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_t),
                ctypes.byref(kernel), ctypes.byref(user)):
            return None
        memory = self._PMC()
        memory.cb = ctypes.sizeof(self._PMC)
        if not self._psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(memory), memory.cb):
            return None

        def to_100ns(value: wt.FILETIME) -> int:
            return (value.dwHighDateTime << 32) | value.dwLowDateTime

        return {
            "cpu_100ns": to_100ns(kernel) + to_100ns(user),
            "private_bytes": int(memory.PrivateUsage),
        }


def resolve_profile(
        profile: str, duration_s: float | None,
        sample_interval_s: float | None) -> tuple[str, float, float]:
    if profile == "24h":
        if duration_s is not None and duration_s != 86_400:
            raise HarnessError("24h evidence requires exactly 86,400 seconds")
        duration = 86_400.0
        evidence_class = "SOAK_24H_OBSERVATION"
        default_interval = 60.0
    elif profile == "alpha":
        duration = 120.0 if duration_s is None else duration_s
        if not 60 <= duration <= 120:
            raise HarnessError("Alpha stability duration must be 60--120 seconds")
        evidence_class = "ALPHA_STABILITY"
        default_interval = 5.0
    else:
        duration = 15.0 if duration_s is None else duration_s
        if not 1 <= duration < 60:
            raise HarnessError(
                "machinery validation duration must be 1--59 seconds")
        evidence_class = "MACHINERY_VALIDATION"
        default_interval = 1.0
    interval = default_interval if sample_interval_s is None else sample_interval_s
    if profile == "24h" and interval != 60:
        raise HarnessError("24h evidence requires a fixed 60-second interval")
    if interval <= 0 or interval > duration / 4:
        raise HarnessError(
            "sample interval must be positive and allow at least four samples")
    return evidence_class, float(duration), float(interval)


def required_sample_count(duration_s: float, interval_s: float) -> int:
    """Require at least 80% of scheduled post-baseline samples."""
    scheduled = max(1, int(duration_s / interval_s))
    return max(1, int(scheduled * 0.8))


def scan_run_logs(data_dir: Path) -> tuple[int, list[str]]:
    errors: list[str] = []
    log_dir = Path(data_dir) / "logs"
    if not log_dir.is_dir():
        raise HarnessError("application log directory is missing")
    paths = list(log_dir.rglob("*.log*"))
    run_dir = Path(data_dir).parent
    paths.extend(run_dir.glob("primary.*.log"))
    for path in sorted(set(paths)):
        try:
            lines = path.read_text(
                encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            raise HarnessError(f"could not read run log {path.name}") from exc
        for number, line in enumerate(lines, start=1):
            if any(marker in line for marker in _ERROR_MARKERS):
                errors.append(f"{path.name}:{number}:{line[:240]}")
    return len(errors), errors


class IncrementalLogMonitor:
    """Read each log byte once across rotations and retain error evidence."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.run_dir = self.data_dir.parent
        self._offsets: dict[tuple[int, int], int] = {}
        self.errors: list[str] = []

    def poll(self) -> list[str]:
        log_dir = self.data_dir / "logs"
        if not log_dir.is_dir():
            raise HarnessError("application log directory is missing")
        paths = list(log_dir.rglob("*.log*"))
        paths.extend(self.run_dir.glob("primary.*.log"))
        discovered: list[str] = []
        for path in sorted(set(paths)):
            try:
                stat = path.stat()
                key = (int(stat.st_dev), int(stat.st_ino))
                offset = self._offsets.get(key, 0)
                if stat.st_size < offset:
                    offset = 0
                with path.open("rb") as handle:
                    handle.seek(offset)
                    data = handle.read()
                    self._offsets[key] = handle.tell()
            except FileNotFoundError:
                continue  # normal rotation race; renamed inode is seen next poll
            except OSError as exc:
                raise HarnessError(
                    f"could not incrementally read {path.name}") from exc
            for line in data.decode("utf-8", "replace").splitlines():
                if any(marker in line for marker in _ERROR_MARKERS):
                    detail = f"{path.name}:{line[:240]}"
                    self.errors.append(detail)
                    discovered.append(detail)
        return discovered


def evaluate_soak(
        *, completed: bool, samples: list[dict[str, Any]],
        duration_s: float, interval_s: float, error_count: int,
        ipc_failures: list[str], normal_exit: bool,
        forced_termination: bool) -> list[str]:
    failures: list[str] = []
    if not completed:
        failures.append("requested observation duration was not completed")
    minimum = required_sample_count(duration_s, interval_s)
    if len(samples) < minimum:
        failures.append(
            f"sampling coverage {len(samples)}/{minimum} is insufficient")
    if ipc_failures:
        failures.extend(ipc_failures)
    if error_count:
        failures.append(f"{error_count} ERROR/Traceback lines in run logs")
    if forced_termination:
        failures.append("forced termination was required")
    if not normal_exit:
        failures.append("application did not exit normally with code 0")
    if samples:
        first = samples[0]["private_bytes"]
        last = samples[-1]["private_bytes"]
        growth = last - first
        hard_cap = max(50 * 1024 * 1024, first * 0.30)
        if growth > hard_cap:
            failures.append(
                f"memory growth {growth} exceeds hard cap {int(hard_cap)}")
    return failures


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("source", "exe"), default="source")
    parser.add_argument("--profile", choices=("machinery", "alpha", "24h"),
                        default="alpha")
    parser.add_argument("--duration-s", type=float)
    parser.add_argument("--sample-interval-s", type=float)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        evidence_class, duration_s, interval_s = resolve_profile(
            args.profile, args.duration_s, args.sample_interval_s)
        if args.profile == "24h" and args.target != "exe":
            raise HarnessError("24h evidence requires a receipt-bound EXE")
    except HarnessError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.evidence_root.resolve() / (
        f"{stamp}-{evidence_class.lower()}-{uuid4().hex[:8]}")
    run_dir.mkdir(parents=True, exist_ok=False)
    data_dir = run_dir / "data"
    window_report = run_dir / "window.json"
    instance = f"soak-{uuid4().hex}"

    binding: ArtifactBinding | None = None
    target: Target | None = None
    proc: subprocess.Popen | None = None
    app_pid: int | None = None
    app_hwnd: int | None = None
    verified_process: VerifiedProcess | None = None
    process_binding: dict[str, Any] | None = None
    log_monitor: IncrementalLogMonitor | None = None
    harness_hashes: dict[str, str] = {}
    harness_postcheck = False
    samples: list[dict[str, Any]] = []
    run_failures: list[str] = []
    completed = False
    forced_termination = False
    normal_exit = False
    shutdown = {
        "quit_ipc_ok": False, "popen_exit_code": None,
        "app_pid_exited": False, "forced_termination": False,
        "normal_exit": False, "residual_app_process": False,
    }
    started = time.monotonic()

    try:
        binding = bind_target(
            args.target, args.artifact_dir, args.receipt, run_dir)
        harness_hashes = verify_harness_inputs(
            binding, ("scripts/soak_24h.py",))
        target = Target(binding, run_dir)
        launch_epoch = time.time()
        proc = target.spawn(data_dir, instance, window_report)
        app_report = wait_for_app_report(proc, window_report)
        app_pid, app_hwnd = app_report["pid"], app_report["hwnd"]
        verified_process = VerifiedProcess(
            app_pid, app_hwnd, Path(binding.command[0]), launch_epoch)
        process_binding = verified_process.report()
        wait_for_window_visibility(proc, app_hwnd, True)
        log_monitor = IncrementalLogMonitor(data_dir)
        initial_errors = log_monitor.poll()
        if initial_errors:
            raise HarnessError("run log error observed: " + initial_errors[0])
        metrics = ProcessMetrics(verified_process)
        baseline = metrics.sample()
        if baseline is None:
            raise HarnessError("initial sample of self-reported PID failed")
        last_cpu = baseline["cpu_100ns"]
        last_wall = time.monotonic()
        observation_started = last_wall
        deadline = last_wall + duration_s
        next_sample = last_wall + interval_s
        next_log_check = last_wall
        hide_sent = False
        show_sent = False

        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise HarnessError(
                    f"primary process exited during observation ({proc.returncode})")
            now = time.monotonic()
            elapsed = now - observation_started
            if now >= next_log_check:
                live_errors = log_monitor.poll()
                if live_errors:
                    raise HarnessError(
                        "run log error observed: " + live_errors[0])
                next_log_check = now + 1.0
            if not hide_sent and elapsed >= duration_s / 3:
                target.ipc("hide", instance)
                wait_for_window_visibility(proc, app_hwnd, False)
                hide_sent = True
            if not show_sent and elapsed >= duration_s * 2 / 3:
                target.ipc("show", instance)
                wait_for_window_visibility(proc, app_hwnd, True)
                show_sent = True
            sleep_s = min(max(0.0, next_sample - now),
                          max(0.0, deadline - now), 0.25)
            if sleep_s:
                time.sleep(sleep_s)
                continue
            if now < next_sample:
                continue
            data = metrics.sample()
            if data is None:
                raise HarnessError("sample of self-reported PID failed")
            wall_delta = now - last_wall
            if wall_delta <= 0:
                raise HarnessError("non-positive sampling interval observed")
            if wall_delta > interval_s * 2.5:
                raise HarnessError(
                    f"sampling gap {wall_delta:.3f}s exceeds continuity limit")
            cpu_delta = data["cpu_100ns"] - last_cpu
            if cpu_delta < 0:
                raise HarnessError("process CPU counter moved backwards")
            samples.append({
                "t_s": round(now - started, 3),
                "cpu_percent": round(
                    cpu_delta / 10_000_000.0 / wall_delta * 100, 3),
                "private_bytes": data["private_bytes"],
                "app_pid": app_pid,
            })
            last_cpu = data["cpu_100ns"]
            last_wall = now
            # Never backfill missed slots with a burst of adjacent samples.
            next_sample = now + interval_s
        completed = True
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

    if log_monitor is not None:
        try:
            final_live_errors = log_monitor.poll()
            if final_live_errors:
                run_failures.append(
                    "run log error observed: " + final_live_errors[0])
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
            verify_harness_unchanged(
                binding, harness_hashes, ("scripts/soak_24h.py",))
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
    failures = evaluate_soak(
        completed=completed, samples=samples, duration_s=duration_s,
        interval_s=interval_s, error_count=error_count,
        ipc_failures=run_failures, normal_exit=normal_exit,
        forced_termination=forced_termination,
    )
    first = samples[0]["private_bytes"] if samples else None
    last = samples[-1]["private_bytes"] if samples else None
    report = {
        "schema": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "evidence_class": evidence_class,
        "formal_24h_claim": False,
        "production_gate": False,
        "environment_policy": "python-qt-injection-cleared-v1",
        "harness": {
            "inputs": harness_hashes,
            "postcheck_unchanged": harness_postcheck,
        },
        "target": args.target,
        "artifact": binding.report() if binding else None,
        "duration_requested_s": duration_s,
        "duration_completed": completed,
        "sample_interval_s": interval_s,
        "samples": len(samples),
        "minimum_samples": required_sample_count(duration_s, interval_s),
        "popen_pid": proc.pid if proc else None,
        "app_self_reported_pid": app_pid,
        "app_self_reported_hwnd": app_hwnd,
        "verified_process": process_binding,
        "private_bytes_first": first,
        "private_bytes_last": last,
        "growth_bytes": (last - first)
        if first is not None and last is not None else None,
        "log_error_count": error_count,
        "log_errors": log_errors,
        "forced_termination": forced_termination,
        "normal_exit": normal_exit,
        "shutdown": shutdown,
        "sample_evidence": sample_evidence,
        "supporting_evidence": support,
        "evidence_complete": True,
        "failures": failures,
        "result": "PASS" if not failures else "FAIL",
    }
    atomic_write(
        run_dir / "soak.json",
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"))
    print(json.dumps({
        "result": report["result"],
        "evidence_class": evidence_class,
        "samples": len(samples),
        "artifact": report["artifact"],
        "failures": failures,
        "evidence": str(run_dir),
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
