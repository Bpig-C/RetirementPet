"""Hostile-input and real-spawn gates for local PetPack preflight IPC."""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import time
from dataclasses import replace
from pathlib import Path

import pytest

from retirement_pet.petpack.local_import import (
    ISOLATED_IMPORT_PROTOCOL,
    ISOLATED_IMPORT_PROTOCOL_VERSION,
    LocalImportError,
    MAX_ISOLATED_IMPORT_METADATA_BYTES,
    parse_isolated_import_metadata,
    preflight_local_pack,
)


ROOT = Path(__file__).resolve().parent.parent
PACK = (ROOT / "assets" / "petpack" / "examples" /
        "realistic-retirement-cat-0.1.1.petpack")


def _crash_without_message(_pack_path, connection) -> None:
    import ctypes

    connection.close()
    ctypes.string_at(0, 1)
    os._exit(73)  # pragma: no cover - access violation is expected above


def _hang_without_message(_pack_path, connection) -> None:
    try:
        while True:
            time.sleep(1.0)
    finally:
        connection.close()


def _pickle_attack(_pack_path, connection) -> None:
    connection.send_bytes(pickle.dumps(_PickleCanary()))
    connection.close()


def _success_then_crash(pack_path, connection) -> None:
    from retirement_pet.petpack.local_import import isolated_import_process_entry

    isolated_import_process_entry(pack_path, connection)
    os._exit(74)


class _PickleCanary:
    def __reduce__(self):
        return (_write_pickle_marker, ())


def _write_pickle_marker():
    Path(os.environ["RETIREMENT_PET_PICKLE_MARKER"]).write_text(
        "executed", encoding="utf-8")


def test_real_spawn_preflight_returns_owned_bound_dto_without_parent_pixmap(
        monkeypatch):
    import retirement_pet.petpack.local_import as local_import

    class ForbiddenParentPixmap:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("parent process decoded untrusted PNG")

    # Windows spawn imports a clean child module, while the parent-side symbol
    # remains poisoned.  Success therefore proves QPixmap lived in the child.
    monkeypatch.setattr(local_import, "QPixmap", ForbiddenParentPixmap)
    preview = preflight_local_pack(PACK)

    assert preview.first_frames_verified is True
    assert preview.character_ids == ("realistic-cat",)
    assert preview.archive_sha256 == hashlib.sha256(PACK.read_bytes()).hexdigest()
    assert preview.payload == PACK.read_bytes()
    assert preview.validation_report.archive_sha256 == preview.archive_sha256
    assert preview.validation_report.trust_channel == "LOCAL_IMPORTED"
    assert preview.idle_first_assets == ()
    assert preview.idle_asset_payloads == ()


def test_child_to_parent_protocol_rejects_pickle_without_executing_it(tmp_path):
    marker = tmp_path / "pickle-must-never-run"
    os.environ["RETIREMENT_PET_PICKLE_MARKER"] = str(marker)

    try:
        with pytest.raises(LocalImportError, match="无效数据") as caught:
            preflight_local_pack(
                tmp_path / "attack.petpack", _target=_pickle_attack,
                timeout_seconds=5.0)
    finally:
        os.environ.pop("RETIREMENT_PET_PICKLE_MARKER", None)

    assert caught.value.child_reaped is True
    assert not marker.exists()


@pytest.mark.parametrize("frame", [
    b"not-json",
    b'{"protocol":"retirementpet.local-import","protocol":"duplicate"}',
    json.dumps({
        "protocol": ISOLATED_IMPORT_PROTOCOL,
        "version": ISOLATED_IMPORT_PROTOCOL_VERSION + 1,
        "status": "error",
        "error": "x",
    }).encode(),
    json.dumps({
        "protocol": ISOLATED_IMPORT_PROTOCOL,
        "version": ISOLATED_IMPORT_PROTOCOL_VERSION,
        "status": "ok",
        "payload": {"size": True, "sha256": "0" * 64},
        "preview": {},
    }).encode(),
])
def test_metadata_parser_fails_closed_on_malformed_schema(frame):
    with pytest.raises(ValueError):
        parse_isolated_import_metadata(frame)


def test_metadata_parser_applies_hard_frame_budget():
    with pytest.raises(ValueError, match="length"):
        parse_isolated_import_metadata(
            b"x" * (MAX_ISOLATED_IMPORT_METADATA_BYTES + 1))


def test_parent_reconstruction_rejects_tampered_child_metadata():
    import retirement_pet.petpack.local_import as local_import

    preview = preflight_local_pack(PACK)
    envelope = parse_isolated_import_metadata(
        local_import._success_frame(preview))
    assert not isinstance(envelope, str)
    metadata = json.loads(json.dumps(envelope.preview_metadata))
    metadata["first_frames_verified"] = False

    with pytest.raises(ValueError, match="verification"):
        local_import.preview_from_isolated_transfer(
            replace(envelope, preview_metadata=metadata), preview.payload)


def test_real_spawn_access_violation_is_rejected_and_reaped(tmp_path):
    with pytest.raises(LocalImportError, match="异常退出") as caught:
        preflight_local_pack(
            tmp_path / "crash.petpack", _target=_crash_without_message,
            timeout_seconds=5.0)

    assert caught.value.child_reaped is True
    assert caught.value.child_exit_code not in (None, 0)


def test_real_spawn_success_message_followed_by_crash_is_not_accepted():
    with pytest.raises(LocalImportError, match="异常退出") as caught:
        preflight_local_pack(
            PACK, _target=_success_then_crash, timeout_seconds=15.0)

    assert caught.value.child_reaped is True
    assert caught.value.child_exit_code == 74


def test_real_spawn_timeout_is_bounded_and_reaped(tmp_path):
    started = time.monotonic()
    with pytest.raises(LocalImportError, match="超时") as caught:
        preflight_local_pack(
            tmp_path / "hang.petpack", _target=_hang_without_message,
            timeout_seconds=0.05)

    assert time.monotonic() - started < 2.0
    assert caught.value.child_reaped is True
    assert caught.value.child_exit_code is not None


def test_source_canary_pass_and_timeout_do_not_create_a_pet_data_store(
        tmp_path, monkeypatch):
    from retirement_pet.main import _write_local_import_canary

    data_dir = tmp_path / "must-not-exist"
    monkeypatch.setenv("RETIREMENT_PET_DATA_DIR", str(data_dir))
    passed = tmp_path / "pass.json"
    assert _write_local_import_canary([
        "--local-import-preflight-out", str(passed),
        "--local-import-pack", str(PACK),
    ]) == 0
    pass_report = json.loads(passed.read_text(encoding="utf-8"))
    assert pass_report == {
        "schema": 1,
        "operation": "local_import_preflight",
        "ipc_protocol": 1,
        "result": "PASS",
        "archive_sha256": hashlib.sha256(PACK.read_bytes()).hexdigest(),
        "content_digest": (
            "6c4b368ad79124bf5bc48e6d8190917c7031f923b2cf00f728c4c17b9c21260c"
        ),
        "character_ids": ["realistic-cat"],
        "first_frames_verified": True,
        "child_exit_code": 0,
        "child_reaped": True,
    }

    timed_out = tmp_path / "timeout.json"
    assert _write_local_import_canary([
        "--local-import-preflight-out", str(timed_out),
        "--local-import-pack", str(PACK),
        "--local-import-preflight-timeout-ms", "1",
    ]) == 1
    timeout_report = json.loads(timed_out.read_text(encoding="utf-8"))
    assert timeout_report["result"] == "TIMEOUT"
    assert timeout_report["child_reaped"] is True
    assert timeout_report["child_exit_code"] != 0
    assert not data_dir.exists()


def test_canary_rejects_out_of_range_timeout_without_starting(tmp_path):
    output = tmp_path / "must-not-exist.json"
    from retirement_pet.main import _write_local_import_canary

    assert _write_local_import_canary([
        "--local-import-preflight-out", str(output),
        "--local-import-pack", str(PACK),
        "--local-import-preflight-timeout-ms", "0",
    ]) == 2
    assert not output.exists()


def test_child_to_parent_implementation_contains_no_pickle_or_object_pipe_api():
    local_source = (ROOT / "src" / "retirement_pet" / "petpack" /
                    "local_import.py").read_text(encoding="utf-8")
    page_source = (ROOT / "src" / "retirement_pet" / "ui" / "panel" /
                   "pages.py").read_text(encoding="utf-8")
    combined = local_source + page_source

    assert "import pickle" not in combined
    assert ".send(" not in combined
    assert ".recv(" not in combined
    assert "pickle.loads" not in combined


def test_module_entrypoint_has_spawn_safe_main_guard():
    source = (ROOT / "src" / "retirement_pet" / "__main__.py").read_text(
        encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert "sys.exit(main())" in source
