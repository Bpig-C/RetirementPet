"""Fail-closed semantics for the performance and stability harnesses."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import perf_sample, soak_24h


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt(**updates) -> dict:
    value = {
        "schema": 1,
        "result": "PASS",
        "recipe_id": "a" * 64,
        "artifact_id": "b" * 64,
        "exe_sha256": "c" * 64,
        "commit": "d" * 40,
        "git_tree": "e" * 40,
        "file_count": 2,
        "total_bytes": 100,
        "attestation_protocol": 2,
    }
    value.update(updates)
    return value


def test_receipt_requires_exact_pass_schema(tmp_path):
    path = tmp_path / "receipt.json"
    value = _receipt()
    path.write_text(json.dumps(value), encoding="utf-8")
    assert soak_24h.load_receipt(path) == value

    value["extra"] = "untrusted"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(soak_24h.HarnessError):
        soak_24h.load_receipt(path)


@pytest.mark.parametrize("field,value", [
    ("result", "FAIL"),
    ("recipe_id", "not-a-hash"),
    ("file_count", 0),
    ("total_bytes", 0),
    ("attestation_protocol", 1),
])
def test_receipt_rejects_untrusted_values(tmp_path, field, value):
    path = tmp_path / "receipt.json"
    receipt = _receipt(**{field: value})
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(soak_24h.HarnessError):
        soak_24h.load_receipt(path)


def test_exe_binding_covers_build_exe_and_complete_inventory(
        tmp_path, monkeypatch):
    artifact = tmp_path / "artifact"
    (artifact / "_internal").mkdir(parents=True)
    exe = artifact / "RetirementPet.exe"
    exe.write_bytes(b"trusted-executable")
    (artifact / "_internal" / "payload.bin").write_bytes(b"payload")
    tree, count, total = soak_24h.artifact_inventory(artifact)
    receipt = _receipt(
        artifact_id=tree, exe_sha256=_sha256(exe),
        file_count=count, total_bytes=total,
    )
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    external = {
        "build_id": receipt["recipe_id"],
        "commit": receipt["commit"],
        "git_tree": receipt["git_tree"],
        "inputs": {"files": {
            "scripts/soak_24h.py": soak_24h._sha256(
                Path(soak_24h.__file__)),
            "scripts/perf_sample.py": soak_24h._sha256(
                Path(perf_sample.__file__)),
        }},
    }
    monkeypatch.setattr(soak_24h, "_load_external_build_info",
                        lambda _artifact: external)
    probed = []
    monkeypatch.setattr(
        soak_24h, "_probe_compiled_identity",
        lambda observed_exe, _run, identity: probed.append(
            (observed_exe, identity)),
    )

    binding = soak_24h.bind_target(
        "exe", artifact, receipt_path, tmp_path / "run")

    assert binding.identity["build_id"] == receipt["recipe_id"]
    assert binding.identity["artifact_id"] == tree
    assert binding.identity["exe_sha256"] == receipt["exe_sha256"]
    assert probed == [(exe, external)]
    soak_24h.verify_artifact_unchanged(binding)
    soak_24h.verify_harness_inputs(
        binding, ("scripts/soak_24h.py", "scripts/perf_sample.py"))
    mismatched = soak_24h.ArtifactBinding(
        target="exe", command=binding.command,
        identity={**binding.identity, "build_input_harnesses": {
            "scripts/soak_24h.py": "0" * 64,
        }}, artifact_dir=artifact, receipt_path=receipt_path,
    )
    with pytest.raises(soak_24h.HarnessError, match="differs"):
        soak_24h.verify_harness_inputs(
            mismatched, ("scripts/soak_24h.py",))

    (artifact / "_internal" / "payload.bin").write_bytes(b"tampered")
    with pytest.raises(soak_24h.HarnessError, match="inventory"):
        soak_24h.verify_artifact_unchanged(binding)
    with pytest.raises(soak_24h.HarnessError, match="inventory"):
        soak_24h.bind_target(
            "exe", artifact, receipt_path, tmp_path / "second",
            probe_runtime=False)


def test_exe_binding_never_uses_an_implicit_dist(tmp_path):
    with pytest.raises(soak_24h.HarnessError, match="explicit"):
        soak_24h.bind_target("exe", None, None, tmp_path)


def test_source_binding_cannot_be_labelled_with_artifact_metadata(tmp_path):
    with pytest.raises(soak_24h.HarnessError, match="must not"):
        soak_24h.bind_target("source", tmp_path, tmp_path / "receipt", tmp_path)


def test_harness_bytes_are_frozen_and_rechecked(monkeypatch):
    binding = soak_24h.ArtifactBinding(
        target="source", command=("python",),
        identity={"artifact_kind": "source"})
    monkeypatch.setattr(soak_24h, "_sha256", lambda _path: "b" * 64)

    with pytest.raises(soak_24h.HarnessError, match="changed"):
        soak_24h.verify_harness_unchanged(
            binding, {"scripts/soak_24h.py": "a" * 64},
            ("scripts/soak_24h.py",))


def test_self_reported_pid_is_used_even_when_launcher_pid_differs(tmp_path):
    report = tmp_path / "window.json"
    report.write_text(json.dumps({
        "pid": 9090,
        "hwnd": 1,
        "onboarding": {
            "campaign": "character-choice-1",
            "ready": True,
            "state": "completed",
            "offered": False,
            "panel_visible": False,
            "panel_hwnd": 0,
        },
    }), encoding="utf-8")
    proc = SimpleNamespace(pid=1010, returncode=None, poll=lambda: None)

    assert soak_24h.wait_for_app_pid(proc, report, timeout_s=0.1) == 9090


def test_steady_state_report_rejects_pet_only_first_report(tmp_path):
    report = tmp_path / "window.json"
    report.write_text(json.dumps({
        "pid": 9090,
        "hwnd": 1,
        "onboarding": {
            "campaign": "character-choice-1",
            "ready": False,
            "state": "pending",
            "offered": False,
            "panel_visible": False,
            "panel_hwnd": 0,
        },
    }), encoding="utf-8")
    proc = SimpleNamespace(pid=1010, returncode=None, poll=lambda: None)

    with pytest.raises(soak_24h.HarnessError, match="steady-state-ready"):
        soak_24h.wait_for_app_report(proc, report, timeout_s=0.05)


def test_window_visibility_is_confirmed_after_ipc(monkeypatch):
    user32 = SimpleNamespace(
        IsWindow=lambda _hwnd: 1,
        IsWindowVisible=lambda _hwnd: 0,
    )
    monkeypatch.setattr(soak_24h.ctypes, "WinDLL",
                        lambda *args, **kwargs: user32)
    proc = SimpleNamespace(returncode=None, poll=lambda: None)

    soak_24h.wait_for_window_visibility(proc, 1234, False, timeout_s=0.1)


def test_verified_process_binds_image_creation_hwnd_and_persistent_handle(
        tmp_path, monkeypatch):
    expected = (tmp_path / "RetirementPet.exe").resolve()
    expected.write_bytes(b"MZ")
    created_epoch = 1_000.0
    filetime = int((created_epoch + 11_644_473_600) * 10_000_000)

    class Function:
        def __init__(self, callback):
            self.callback = callback
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    class Kernel:
        def __init__(self):
            self.closed = []
            self.OpenProcess = Function(lambda *_args: 77)
            self.QueryFullProcessImageNameW = Function(self.query_image)
            self.GetProcessTimes = Function(self.process_times)
            self.GetExitCodeProcess = Function(self.exit_code)
            self.WaitForSingleObject = Function(lambda *_args: 0)
            self.CloseHandle = Function(self.closed.append)

        @staticmethod
        def query_image(_handle, _flags, buffer, _size):
            buffer.value = str(expected)
            return 1

        @staticmethod
        def process_times(_handle, creation, _exit, _kernel, _user):
            creation._obj.dwLowDateTime = filetime & 0xFFFFFFFF
            creation._obj.dwHighDateTime = filetime >> 32
            return 1

        @staticmethod
        def exit_code(_handle, code):
            code._obj.value = 259
            return 1

    class User:
        @staticmethod
        def GetWindowThreadProcessId(_hwnd, owner):
            owner._obj.value = 4242
            return 1

    kernel = Kernel()
    monkeypatch.setattr(
        soak_24h.ctypes, "WinDLL",
        lambda name, **_kwargs: kernel if name == "kernel32" else User(),
    )
    monkeypatch.setattr(soak_24h.time, "time", lambda: 1_001.0)

    process = soak_24h.VerifiedProcess(
        4242, 1234, expected, launched_at_epoch=999.0)

    assert process.handle == 77
    assert process.is_alive() is True
    assert process.report()["hwnd_owner_pid"] == 4242
    process.close()
    assert kernel.closed == [77]


def test_ipc_nonzero_is_a_hard_failure(tmp_path, monkeypatch):
    binding = soak_24h.ArtifactBinding(
        "exe", ("RetirementPet.exe",), {"artifact_kind": "onedir"},
        artifact_dir=tmp_path)
    target = soak_24h.Target(binding, tmp_path)
    monkeypatch.setattr(
        soak_24h.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=7, stdout=b"", stderr=b""),
    )

    with pytest.raises(soak_24h.HarnessError, match="returned 7"):
        target.ipc("hide", "instance")


def test_steady_state_preseed_preserves_settings_and_is_idempotent(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings_path = data_dir / "settings.json"
    settings_path.write_text(json.dumps({
        "volume": 0.2,
        "character_onboarding_campaign": "older-campaign",
    }), encoding="utf-8")

    assert soak_24h.prepare_steady_state_data_dir(data_dir) == settings_path
    first_bytes = settings_path.read_bytes()
    assert soak_24h.prepare_steady_state_data_dir(data_dir) == settings_path

    assert settings_path.read_bytes() == first_bytes
    assert json.loads(first_bytes) == {
        "volume": 0.2,
        "character_onboarding_campaign": "character-choice-1",
    }


@pytest.mark.parametrize("contents", ["[]", "not-json"])
def test_steady_state_preseed_rejects_invalid_settings(tmp_path, contents):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "settings.json").write_text(contents, encoding="utf-8")

    with pytest.raises(soak_24h.HarnessError, match="steady-state settings"):
        soak_24h.prepare_steady_state_data_dir(data_dir)


def test_shared_target_preseeds_onboarding_before_process_launch(
        tmp_path, monkeypatch):
    binding = soak_24h.ArtifactBinding(
        "source", ("python", "-m", "retirement_pet.main"),
        {"artifact_kind": "source"})
    target = perf_sample.Target(binding, tmp_path)
    data_dir = tmp_path / "data"
    observed = {}

    def fake_popen(*_args, **_kwargs):
        observed.update(json.loads(
            (data_dir / "settings.json").read_text(encoding="utf-8")))
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr(soak_24h.subprocess, "Popen", fake_popen)

    proc = target.spawn(data_dir, "instance", tmp_path / "window.json")

    assert proc.pid == 1234
    assert observed["character_onboarding_campaign"] == "character-choice-1"


def test_short_profiles_cannot_claim_24_hours():
    alpha = soak_24h.resolve_profile("alpha", 60, 2)
    machinery = soak_24h.resolve_profile("machinery", 10, 1)
    formal = soak_24h.resolve_profile("24h", None, 60)

    assert alpha[0] == "ALPHA_STABILITY"
    assert machinery[0] == "MACHINERY_VALIDATION"
    assert formal[:2] == ("SOAK_24H_OBSERVATION", 86_400.0)
    with pytest.raises(soak_24h.HarnessError, match="86,400"):
        soak_24h.resolve_profile("24h", 120, 5)
    with pytest.raises(soak_24h.HarnessError, match="60-second"):
        soak_24h.resolve_profile("24h", None, 3_600)
    with pytest.raises(soak_24h.HarnessError, match="60--120"):
        soak_24h.resolve_profile("alpha", 59, 1)


def test_soak_evaluation_fails_closed_on_every_lifecycle_signal():
    failures = soak_24h.evaluate_soak(
        completed=False,
        samples=[{"private_bytes": 10}],
        duration_s=60,
        interval_s=5,
        error_count=2,
        ipc_failures=["IPC hide failed"],
        normal_exit=False,
        forced_termination=True,
    )

    joined = "\n".join(failures)
    assert "duration" in joined
    assert "coverage" in joined
    assert "IPC hide failed" in joined
    assert "ERROR/Traceback" in joined
    assert "forced termination" in joined
    assert "exit normally" in joined


def test_log_errors_are_evidence_failures(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "run.log").write_text(
        "INFO ready\nERROR failed\nTraceback follows\n", encoding="utf-8")
    (logs / "retirement-pet.log.1").write_text(
        "CRITICAL rotated failure\n", encoding="utf-8")

    count, details = soak_24h.scan_run_logs(tmp_path)

    assert count == 3
    assert any("retirement-pet.log.1" in detail for detail in details)


def test_missing_application_logs_fail_closed(tmp_path):
    with pytest.raises(soak_24h.HarnessError, match="log directory"):
        soak_24h.scan_run_logs(tmp_path)


def test_incremental_log_monitor_survives_rotation(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    current = logs / "retirement-pet.log"
    current.write_text("INFO ready\n", encoding="utf-8")
    monitor = soak_24h.IncrementalLogMonitor(tmp_path)
    assert monitor.poll() == []

    with current.open("a", encoding="utf-8") as handle:
        handle.write("ERROR before rotation\n")
    assert any("before rotation" in line for line in monitor.poll())

    rotated = logs / "retirement-pet.log.1"
    current.replace(rotated)
    current.write_text("CRITICAL after rotation\n", encoding="utf-8")
    assert any("after rotation" in line for line in monitor.poll())
    assert len(monitor.errors) == 2


def test_shutdown_detects_and_terminates_broker_owned_app(monkeypatch):
    class Proc:
        pid = 111
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.returncode = 0
            return 0

        def kill(self):
            self.returncode = 1

    class Target:
        commands = []

        def ipc(self, command, instance):
            self.commands.append((command, instance))

    verified = SimpleNamespace(
        is_alive=lambda: True,
        wait_exit=lambda _timeout: False,
    )
    failures = []

    result = soak_24h.shutdown_target(
        Proc(), Target(), "instance", 222, failures,
        verified_process=verified)

    assert result["forced_termination"] is True
    assert result["normal_exit"] is False
    assert result["residual_app_process"] is True
    assert any("application PID" in failure for failure in failures)


def test_source_target_can_never_run_the_formal_24h_profile(tmp_path):
    assert soak_24h.main([
        "--target", "source", "--profile", "24h",
        "--evidence-root", str(tmp_path),
    ]) == 2


def test_perf_profiles_are_never_formal_gate_labels():
    calibration = perf_sample.resolve_profile(
        "calibration", None, None, None)
    alpha = perf_sample.resolve_profile(
        "alpha-health", 1, 4, 1)

    assert calibration[0] == "CALIBRATION"
    assert calibration[4] is False
    assert alpha[0] == "ALPHA_HEALTH"
    assert alpha[4] is True
    assert "GATE" not in calibration[0]
    assert "GATE" not in alpha[0]


def test_perf_evaluation_requires_scene_coverage_markers_logs_and_exit():
    failures = perf_sample.evaluate_perf(
        samples=[{
            "scene": "visible_idle", "phase": "measure",
            "cpu_percent": 1.0, "private_bytes": 10,
        }],
        scenes=("visible_idle", "hidden"),
        scene_s=20,
        interval_s=1,
        markers_ok=False,
        error_count=1,
        run_failures=["IPC hide returned 2"],
        normal_exit=False,
        forced_termination=True,
    )

    joined = "\n".join(failures)
    assert "visible_idle sampling coverage" in joined
    assert "hidden sampling coverage" in joined
    assert "markers" in joined
    assert "ERROR/Traceback" in joined
    assert "IPC hide returned 2" in joined
    assert "forced termination" in joined
    assert "exit normally" in joined


def test_measure_scene_fails_if_primary_process_dies():
    class Metrics:
        pid = 5150

        @staticmethod
        def sample():
            return {
                "cpu_100ns": 100,
                "private_bytes": 200,
                "working_set_bytes": 300,
            }

    proc = SimpleNamespace(returncode=9, poll=lambda: 9)
    with pytest.raises(perf_sample.HarnessError, match="exited"):
        perf_sample.measure_scene(
            Metrics(), proc, scene="visible_idle", phase="measure",
            duration_s=0.05, interval_s=0.01, samples=[])


def test_perf_markers_must_be_valid_and_include_first_paint(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    path = log_dir / "perf_markers.jsonl"
    path.write_text(
        json.dumps({"marker": "process_start", "t_ms": 10}) + "\n",
        encoding="utf-8")
    with pytest.raises(perf_sample.HarnessError, match="must appear"):
        perf_sample.load_perf_markers(tmp_path)

    values = [
        {"marker": "process_start", "t_ms": 10},
        {"marker": "qt_ready", "t_ms": 12},
        {"marker": "tray_ready", "t_ms": 14},
        {"marker": "first_pet_paint", "t_ms": 20},
    ]
    path.write_text("".join(json.dumps(value) + "\n" for value in values),
                    encoding="utf-8")
    markers = perf_sample.load_perf_markers(tmp_path)
    assert markers[-1]["t_ms"] == 20
    perf_sample.validate_marker_window(markers, 5, 25)

    values[-1]["t_ms"] = float("nan")
    path.write_text("".join(json.dumps(value) + "\n" for value in values),
                    encoding="utf-8")
    with pytest.raises(perf_sample.HarnessError, match="invalid"):
        perf_sample.load_perf_markers(tmp_path)


def test_both_clis_require_explicit_evidence_root():
    with pytest.raises(SystemExit):
        soak_24h._parse_args([])
    with pytest.raises(SystemExit):
        perf_sample._parse_args([])
