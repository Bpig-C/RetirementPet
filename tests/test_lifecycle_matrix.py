"""M5/P0 crash matrix: uninstall safe-switch + REAL external TerminateProcess.

CONFORMANCE 6 kill points covered here use PARENT-DRIVEN external
TerminateProcess: the child signals a named boundary via a marker file and
parks; the parent observes the marker and hard-kills the process with
TerminateProcess (no child cooperation at kill time).  Child-side
os._exit() exists in other suites as a complementary technique but can
never replace this.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from retirement_pet.lifecycle import LifecycleError, PackLibrary
from retirement_pet.lru_cache import LruByteCache
from retirement_pet.switcher import (
    ActiveSelectionStore,
    FakeSurface,
    RuntimeSwitcher,
    uninstall_with_safe_switch,
)

ROOT = Path(__file__).resolve().parent.parent
REF_PACK = ROOT / "tests" / "fixtures" / "petpack" / "minimal-static.petpack"
OFFICIAL_CAT = ROOT / "assets" / "petpack" / "retirement-cat-official.petpack"
KILL_CHILD = ROOT / "tests" / "helpers" / "_kill_child.py"

SWITCH_BEFORE_SELECTION_COMMIT = "switch.before_selection_commit"
SWITCH_AFTER_SELECTION_COMMIT = \
    "switch.after_selection_commit_before_return"
INSTALL_AFTER_PUBLISH = "install.after_publish_before_catalog_commit"

WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt",
    reason="parent-driven crash matrix requires Windows TerminateProcess",
)


@pytest.fixture()
def library(tmp_path):
    library = PackLibrary(tmp_path / "library")
    library.install(REF_PACK)
    library.register_builtin_release(OFFICIAL_CAT)
    yield library
    library.close()


@pytest.fixture()
def switcher(library):
    return RuntimeSwitcher(library, ActiveSelectionStore(library),
                           asset_cache=LruByteCache(48 * 1024 * 1024))


def _revision(library, package_id):
    return next(r.revision_key for r in library.list_revisions()
                if r.revision_key.pack.package_id == package_id)


def _commit(library, switcher, surface, package_id, character):
    request = switcher.request(_revision(library, package_id), character)
    candidate = switcher.prepare(request)
    assert candidate is not None
    return switcher.swap_and_commit(request, surface)


# -- uninstall safe-switch -------------------------------------------------------


def test_uninstall_active_revision_switches_to_alternative_first(
        library, switcher):
    surface = FakeSurface()
    demo_rk = _revision(library, "minimal-static")
    _commit(library, switcher, surface, "minimal-static", "demo")

    assert uninstall_with_safe_switch(library, switcher, demo_rk,
                                      surface=surface)
    active = switcher._store.get("active")
    assert active.character_fqid.endswith("cat")
    assert surface.renderer is switcher.current_runtime
    assert switcher.current_runtime.character_fqid().endswith("cat")
    assert library.get_revision(demo_rk) is None
    assert any(library.trash_dir.iterdir())


def test_uninstall_sole_active_user_pack_targets_builtin(library, switcher):
    """P0-A: with no other USER pack installed, the safe target is the
    REAL builtin official revision (never a digest-zero fake selection)."""
    surface = FakeSurface()
    demo_rk = _revision(library, "minimal-static")
    _commit(library, switcher, surface, "minimal-static", "demo")

    assert uninstall_with_safe_switch(library, switcher, demo_rk,
                                      surface=surface)
    active = switcher._store.get("active")
    record = library.get_revision(active.revision_key())
    assert record is not None and record.builtin
    assert active.content_digest != "0" * 64


def test_uninstall_builtin_is_refused(library, switcher):
    cat_rk = _revision(library, "retirement-cat-official")
    with pytest.raises(LifecycleError):
        library.uninstall_revision(cat_rk)
    assert library.get_revision(cat_rk) is not None


# -- REAL external TerminateProcess crash matrix -----------------------------------


_ACK_FIELDS = {"creation_time_100ns", "point", "pid", "token"}
_WAIT_OBJECT_0 = 0x00000000


def _plain_positive_int(value, maximum: int) -> bool:
    return type(value) is int and 0 < value <= maximum


def _configured_kernel32():
    import ctypes.wintypes as wt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    kernel32.TerminateProcess.restype = wt.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wt.HANDLE,
        ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wt.BOOL
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.WaitForSingleObject.restype = wt.DWORD
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL
    return kernel32


def _process_creation_time(kernel32, handle) -> int | None:
    import ctypes.wintypes as wt

    creation = wt.FILETIME()
    exit_time = wt.FILETIME()
    kernel_time = wt.FILETIME()
    user_time = wt.FILETIME()
    if not kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel_time), ctypes.byref(user_time)):
        return None
    return (creation.dwHighDateTime << 32) | creation.dwLowDateTime


def _open_authenticated_target(
        ack: object, *, expected_point: str, token: str):
    """Open a target only when the full ACK and live process identity match."""
    if not isinstance(ack, dict) or set(ack) != _ACK_FIELDS:
        return None
    if ack.get("point") != expected_point or ack.get("token") != token:
        return None
    if not _plain_positive_int(ack.get("pid"), 0xFFFFFFFF) \
            or not _plain_positive_int(
                ack.get("creation_time_100ns"), 0xFFFFFFFFFFFFFFFF):
        return None
    PROCESS_TERMINATE = 0x0001
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SYNCHRONIZE = 0x00100000
    kernel32 = _configured_kernel32()
    handle = kernel32.OpenProcess(
        PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
        False, ack["pid"],
    )
    if not handle:
        return None
    try:
        identity_matches = (
            _process_creation_time(kernel32, handle)
            == ack["creation_time_100ns"])
    except Exception:  # pragma: no cover - defensive ctypes boundary
        kernel32.CloseHandle(handle)
        return None
    if not identity_matches:
        kernel32.CloseHandle(handle)
        return None
    return kernel32, handle


def _terminate_authenticated_handle(kernel32, handle) -> bool:
    """Terminate and wait using the same already-authenticated handle."""
    if not kernel32.TerminateProcess(handle, 1):
        return False
    return kernel32.WaitForSingleObject(handle, 30_000) == _WAIT_OBJECT_0


def _child_environment() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    return env


def _collect_child_output(proc: subprocess.Popen) -> str:
    try:
        output, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        output, _ = proc.communicate(timeout=10)
    return output or ""


def _wait_for_durable_ack(
        proc: subprocess.Popen, marker: Path, *, point: str,
        token: str, timeout_s: float = 120.0) -> dict:
    """Wait for the exact durable boundary ACK, never for guessed timing."""
    deadline = time.monotonic() + timeout_s
    last_marker_error = "marker absent"
    while time.monotonic() < deadline:
        if marker.is_file():
            try:
                ack = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                last_marker_error = f"marker unreadable: {type(exc).__name__}"
            else:
                return ack
        if proc.poll() is not None:
            output = _collect_child_output(proc)
            pytest.fail(
                f"child died before {point} (rc={proc.returncode}):\n"
                f"{output[-4000:]}"
            )
        time.sleep(0.02)  # polling only; the marker is the synchronization

    if proc.poll() is None:
        proc.kill()
    output = _collect_child_output(proc)
    pytest.fail(
        f"timeout waiting for durable ACK {point}; {last_marker_error};\n"
        f"child output:\n{output[-4000:]}"
    )


def _kill_child_at_boundary(
        library_root: Path, marker: Path, point: str) -> dict:
    token = uuid.uuid4().hex
    target = None
    termination_requested = False
    proc = subprocess.Popen(
        [sys.executable, "-B", str(KILL_CHILD), "run",
         str(library_root), str(marker), point, token,
         str(REF_PACK), str(OFFICIAL_CAT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=str(ROOT), env=_child_environment(),
    )
    try:
        ack = _wait_for_durable_ack(
            proc, marker, point=point, token=token)
        target = _open_authenticated_target(
            ack, expected_point=point, token=token)
        # QApplication may be hosted by a GUI broker on this machine; the
        # durable child ACK identity, not Popen.pid, owns the fault point.
        assert target is not None, "boundary ACK/process identity mismatch"
        # No sleep here: the durable ACK is the exact kill barrier.
        kernel32, target_handle = target
        termination_requested = bool(
            kernel32.TerminateProcess(target_handle, 1))
        assert termination_requested, \
            f"TerminateProcess failed at {point}"
        assert kernel32.WaitForSingleObject(
            target_handle, 30_000) == _WAIT_OBJECT_0, \
            f"terminated target did not exit at {point}"
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            # Some GUI brokers outlive the process they host. The fault-point
            # owner is already proven terminated; reap the launcher only.
            proc.kill()
            proc.wait(timeout=10)
        return ack
    finally:
        cleanup_failed = False
        if target is None:
            started_marker = marker.with_name(marker.name + ".started")
            try:
                started = json.loads(
                    started_marker.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                started = None
            target = _open_authenticated_target(
                started, expected_point="harness.started", token=token)
        if target is not None:
            kernel32, target_handle = target
            try:
                if not termination_requested:
                    cleanup_failed = not _terminate_authenticated_handle(
                        kernel32, target_handle)
            finally:
                kernel32.CloseHandle(target_handle)
        if proc.poll() is None:
            proc.kill()
        _collect_child_output(proc)
        if cleanup_failed:
            raise AssertionError(
                "authenticated parked child could not be terminated")


def _fresh_process_snapshot(library_root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-B", str(KILL_CHILD), "verify",
         str(library_root)],
        cwd=str(ROOT), env=_child_environment(), capture_output=True,
        text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"fresh verifier failed (rc={result.returncode}):\n"
        f"stdout={result.stdout[-4000:]}\n"
        f"stderr={result.stderr[-4000:]}"
    )
    prefix = "VERIFY_JSON="
    payloads = [line[len(prefix):] for line in result.stdout.splitlines()
                if line.startswith(prefix)]
    assert len(payloads) == 1, f"missing verifier payload: {result.stdout[-4000:]}"
    return json.loads(payloads[0])


@WINDOWS_ONLY
def test_terminate_process_rejects_reused_pid_identity():
    """A stale marker cannot turn a reused PID into a kill target."""
    stale_ack = {
        "creation_time_100ns": 1,
        "point": "test.point",
        "pid": os.getpid(),
        "token": "owned",
    }
    assert _open_authenticated_target(
        stale_ack, expected_point="test.point", token="owned") is None


@WINDOWS_ONLY
@pytest.mark.parametrize("bad_ack", (
    {"creation_time_100ns": 1, "point": "test.point",
     "pid": True, "token": "owned"},
    {"creation_time_100ns": 1, "point": "test.point",
     "pid": 0x1_0000_0000, "token": "owned"},
    {"creation_time_100ns": True, "point": "test.point",
     "pid": 1, "token": "owned"},
    {"creation_time_100ns": 0x1_0000_0000_0000_0000,
     "point": "test.point", "pid": 1, "token": "owned"},
    {"creation_time_100ns": 1, "point": "wrong.point",
     "pid": 1, "token": "owned"},
    {"creation_time_100ns": 1, "point": "test.point",
     "pid": 1, "token": "wrong"},
    {"creation_time_100ns": 1, "point": "test.point",
     "pid": 1, "token": "owned", "extra": "field"},
    {"point": "test.point", "pid": 1, "token": "owned"},
))
def test_invalid_ack_is_rejected_before_open_process(monkeypatch, bad_ack):
    def must_not_configure_kernel32():
        raise AssertionError("invalid ACK reached OpenProcess setup")

    monkeypatch.setattr(
        sys.modules[__name__], "_configured_kernel32",
        must_not_configure_kernel32,
    )
    assert _open_authenticated_target(
        bad_ack, expected_point="test.point", token="owned") is None


def _assert_common_snapshot(snapshot: dict, *, catalog_count: int) -> None:
    assert len(snapshot["catalog"]) == catalog_count
    assert all(item["file_exists"] for item in snapshot["catalog"])
    assert snapshot["intent_files"] == []
    assert snapshot["staging_entries"] == []


@WINDOWS_ONLY
@pytest.mark.parametrize(
    ("point", "expected_package", "expected_character", "expected_counter"),
    (
        (SWITCH_BEFORE_SELECTION_COMMIT,
         "retirement-cat-official", "cat", 1),
        (SWITCH_AFTER_SELECTION_COMMIT,
         "minimal-static", "demo", 2),
    ),
)
def test_parent_terminate_at_selection_commit_boundaries(
        tmp_path, point, expected_package, expected_character,
        expected_counter):
    """A fresh process follows persisted truth on both sides of COMMIT."""
    library_root = tmp_path / "library"
    marker = tmp_path / "boundary.marker"

    setup = PackLibrary(library_root)
    setup.install(REF_PACK)
    setup.register_builtin_release(OFFICIAL_CAT)
    setup.close()

    ack = _kill_child_at_boundary(library_root, marker, point)
    assert ack["point"] == point

    first = _fresh_process_snapshot(library_root)
    _assert_common_snapshot(first, catalog_count=2)
    active = first["active"]
    assert active is not None
    assert active["package_id"] == expected_package
    assert active["character_fqid"].endswith(f".{expected_character}")
    assert active["generation"] == expected_counter
    assert active["commit_sequence"] == expected_counter
    assert first["active_record_file_exists"] is True
    assert first["active_renderable"] is True
    assert first["install_event_names"] == ["INSTALL_COMMITTED"]

    # A second independent opener must be a no-op: recovery is idempotent.
    assert _fresh_process_snapshot(library_root) == first


@WINDOWS_ONLY
def test_parent_terminate_after_install_publish_recovers_idempotently(
        tmp_path):
    """Published media + durable intent recover to one READY revision/event."""
    library_root = tmp_path / "library"
    marker = tmp_path / "boundary.marker"

    setup = PackLibrary(library_root)
    setup.close()

    _kill_child_at_boundary(library_root, marker, INSTALL_AFTER_PUBLISH)

    # Prove the intended crash residue before any recovery-capable opener.
    assert len(list((library_root / "journal").glob("intent-*.json"))) == 1
    published = [path for path in
                 (library_root / "packs" / "revisions").rglob("*")
                 if path.is_file()]
    assert len(published) == 1

    first = _fresh_process_snapshot(library_root)
    _assert_common_snapshot(first, catalog_count=1)
    assert first["active"] is None
    assert first["active_record_file_exists"] is None
    assert first["active_renderable"] is None
    assert [item["package_id"] for item in first["catalog"]] == \
        ["minimal-static"]
    assert first["install_event_names"] == ["INSTALL_COMMITTED"]
    assert len(first["install_event_transaction_ids"]) == 1
    assert first["install_event_transaction_ids"][0]

    second = _fresh_process_snapshot(library_root)
    assert second == first
