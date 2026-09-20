"""V13-01: versioned local agent JSON protocol over the real local server.

隔离约定：全部使用独立临时数据目录、随机实例名与合成任务；协议传输测试
通过真实子进程客户端（tests/helpers/_agent_socket_client.py）走真正的
QLocalSocket 进程间往返。不读写日用 %APPDATA% 数据库。
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from retirement_pet.agent_protocol import (
    BAD_JSON,
    BAD_REQUEST,
    BAD_UTF8,
    CONFLICT,
    IDEMPOTENCY_CACHE_SIZE,
    INVALID_ARGS,
    MAX_REQUEST_BYTES,
    NOT_FOUND,
    OK,
    PROTOCOL_NAME,
    UNKNOWN_OPERATION,
    UNKNOWN_PROTOCOL,
)

_HELPER = Path(__file__).parent / "helpers" / "_agent_socket_client.py"
_COUNTER = iter(range(10000))


@pytest.fixture()
def app(qt_application, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    pet = PetApplication(
        argv=["retirement-pet"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-agent-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


def _server(app) -> str:
    return app._guard._name


def _run_scenario(qt_application, app, steps, tmp_path, *,
                  timeout_s: float = 30) -> list:
    """Run one scripted client in a REAL child process; pump the server
    event loop while it talks."""
    scenario_path = tmp_path / f"scenario-{next(_COUNTER)}.json"
    out_path = tmp_path / f"client-{next(_COUNTER)}.json"
    scenario_path.write_text(
        json.dumps({"steps": steps}), encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(_HELPER), _server(app), str(out_path),
         str(scenario_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + timeout_s
    while process.poll() is None and not out_path.exists() \
            and time.monotonic() < deadline:
        qt_application.processEvents()
        time.sleep(0.01)
    qt_application.processEvents()
    return_code = process.wait(timeout=15)
    stderr = process.stderr.read().decode("utf-8", "replace") \
        if process.stderr is not None else ""
    assert return_code == 0, stderr
    return json.loads(out_path.read_text(encoding="utf-8"))["results"]


def _line(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _request(operation: str, args: dict | None = None, *,
             request_id: str = "t1", **extra) -> bytes:
    payload = {
        "protocol": PROTOCOL_NAME,
        "request_id": request_id,
        "operation": operation,
        "args": args or {},
    }
    payload.update(extra)
    return _line(payload)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _request_steps(operation: str, args: dict | None = None, *,
                   request_id: str = "t1", chunks: int = 1,
                   chunk_delay_s: float = 0.0, **extra) -> list:
    return [
        {"write": _b64(_request(operation, args, request_id=request_id,
                                **extra)),
         "chunks": chunks, "chunk_delay_s": chunk_delay_s},
        {"read": True, "timeout_ms": 6000},
    ]


def _single_response(results: list) -> dict:
    read_result = results[-1]
    assert read_result["type"] == "read", results
    assert not read_result["timeout"], results
    return json.loads(read_result["line"])


def _exchange(qt_application, app, tmp_path, operation, args=None, *,
              request_id: str = "t1", **extra) -> dict:
    results = _run_scenario(
        qt_application, app,
        _request_steps(operation, args, request_id=request_id, **extra),
        tmp_path)
    return _single_response(results)


# -- pure handler validation (no socket) -------------------------------------


def test_handle_line_shapes_and_codes(app):
    response = json.loads(
        app.agent_server.handle_line(_request("status.get"))[:-1]
        .decode("utf-8"))
    assert response["protocol"] == PROTOCOL_NAME
    assert response["request_id"] == "t1"
    assert response["ok"] is True and response["code"] == OK
    assert response["generation"] == 0
    assert response["data"]["app"] == "RetirementPet"
    assert "title" not in json.dumps(response["data"])  # no task text here


def test_bad_utf8_gets_structured_error(app):
    raw = b'{"protocol":"' + b"\xff\xfe" + b'"}\n'
    response = json.loads(
        app.agent_server.handle_line(raw)[:-1].decode("utf-8"))
    assert response["ok"] is False and response["code"] == BAD_UTF8


def test_bad_json_gets_structured_error(app):
    response = json.loads(app.agent_server.handle_line(b"{nope\n")
                          [:-1].decode("utf-8"))
    assert response["ok"] is False and response["code"] == BAD_JSON


@pytest.mark.parametrize("payload", [
    {"protocol": "retirement-pet.agent.v2", "request_id": "t",
     "operation": "status.get", "args": {}},          # wrong version
    {"protocol": PROTOCOL_NAME, "request_id": "",
     "operation": "status.get", "args": {}},          # empty request id
    {"protocol": PROTOCOL_NAME, "request_id": "t",
     "operation": "status.get", "args": []},          # args not object
    {"protocol": PROTOCOL_NAME, "request_id": "t",
     "operation": "status.get", "args": {}, "extra": 1},  # unknown field
    {"protocol": PROTOCOL_NAME, "request_id": "t",
     "operation": "status.get", "args": {},
     "expected_generation": "7"},                     # wrong type
])
def test_malformed_requests_rejected(app, payload):
    response = json.loads(app.agent_server.handle_line(_line(payload))
                          [:-1].decode("utf-8"))
    assert response["ok"] is False
    assert response["code"] in (BAD_REQUEST, UNKNOWN_PROTOCOL)


def test_non_object_json_rejected(app):
    response = json.loads(app.agent_server.handle_line(b"[1,2]\n")
                          [:-1].decode("utf-8"))
    assert response["code"] == BAD_REQUEST


def test_unknown_operation_rejected(app):
    response = json.loads(
        app.agent_server.handle_line(_request("todo.drop_table"))
        [:-1].decode("utf-8"))
    assert response["code"] == UNKNOWN_OPERATION


def test_blank_line_is_ignored(app):
    assert app.agent_server.handle_line(b"   \n") is None


def test_operations_allowlist_has_no_dangerous_operations(app):
    """Positive snapshot + safety scan: extending the allowlist must update
    this expected set together with docs/AGENT_PROTOCOL.md, so the exact
    capability surface is always reviewable. The "import" guard matches a
    whole verb only (dynamic-import style ops); domain words such as
    set_importance are legitimate."""
    expected = {
        "status.get",
        "todo.add", "todo.get", "todo.list", "todo.rename",
        "todo.set_importance", "todo.set_urgency", "todo.set_horizon",
        "todo.set_due_date", "todo.move_within_siblings", "todo.note_set",
        "todo.complete", "todo.restore", "todo.archive",
        "todo.restore_archived", "todo.delete",
        "todo.focus_start", "todo.focus_stop",
        "action.list", "action.status", "action.trigger", "action.stop",
        "character.list", "character.current", "character.switch",
        "media.status", "media.play_pause", "media.next", "media.previous",
    }
    operations = app.agent_server.operations()
    assert set(operations) == expected
    assert "status.get" in operations and "todo.add" in operations
    for forbidden in ("sql", "exec", "eval", "file", "shell", "python",
                      "read_file", "settings.write"):
        assert not any(forbidden in op for op in operations), forbidden
    for op in operations:  # "import" banned as a whole verb, not substring
        assert op.rsplit(".", 1)[-1] != "import", op


def test_handler_crash_becomes_internal_error(app, monkeypatch):
    def crashing(_args):
        raise RuntimeError("boom")

    monkeypatch.setitem(app.agent_server._operations, "todo.crash", crashing)
    response = json.loads(
        app.agent_server.handle_line(_request("todo.crash"))
        [:-1].decode("utf-8"))
    assert response["ok"] is False and response["code"] == "INTERNAL"


def test_status_get_does_not_initialize_the_todo_store(
        app, qt_application, tmp_path):
    before_family = app._todo_file_family_present()
    assert before_family is False
    _exchange(qt_application, app, tmp_path, "status.get")
    assert app._todo_file_family_present() is False  # still lazy


# -- protocol behaviour over real inter-process sockets ------------------------


def test_interprocess_status_get_roundtrip(app, qt_application, tmp_path):
    # fresh data dir: the store is still lazy, and status.get must NOT
    # force it into existence - "available" is honestly false until the
    # first real todo use opens the store
    fresh = _exchange(qt_application, app, tmp_path, "status.get",
                      request_id="ipc-1")
    assert fresh["protocol"] == PROTOCOL_NAME
    assert fresh["request_id"] == "ipc-1"
    assert fresh["ok"] is True and fresh["code"] == OK
    assert fresh["data"]["todo"]["initialized"] is False
    assert fresh["data"]["todo"]["available"] is False

    _add_task(qt_application, app, tmp_path, "初始化存储")  # opens the store
    initialized = _exchange(qt_application, app, tmp_path, "status.get",
                            request_id="ipc-2")
    assert initialized["data"]["todo"]["initialized"] is True
    assert initialized["data"]["todo"]["available"] is True


def test_interprocess_todo_add_then_list(app, qt_application, tmp_path):
    from retirement_pet.todo import Horizon

    added = _exchange(
        qt_application, app, tmp_path, "todo.add",
        {"title": "合成任务A", "horizon": "short"}, request_id="a1")
    assert added["ok"] is True
    task_id = added["data"]["task"]["id"]
    assert added["generation"] == 1

    # second child process, fresh socket
    listing = _exchange(qt_application, app, tmp_path, "todo.list",
                        {"archived": False}, request_id="l1")
    assert listing["ok"] is True
    assert any(task["id"] == task_id
               for task in listing["data"]["tasks"])
    assert Horizon(listing["data"]["tasks"][0]["horizon"]) is Horizon.SHORT


def test_interprocess_pipelined_two_requests_one_connection(
        app, qt_application, tmp_path):
    payload = (_request("status.get", request_id="p1")
               + _request("status.get", request_id="p2"))
    results = _run_scenario(qt_application, app, [
        {"write": _b64(payload)},
        {"read": True, "timeout_ms": 6000},
        {"read": True, "timeout_ms": 6000},
    ], tmp_path)
    first = json.loads(results[1]["line"])
    second = json.loads(results[2]["line"])
    assert first["request_id"] == "p1" and second["request_id"] == "p2"
    assert first["ok"] and second["ok"]


def test_half_packet_request_is_reassembled(app, qt_application, tmp_path):
    response = _exchange(
        qt_application, app, tmp_path, "status.get",
        request_id="half", chunks=3, chunk_delay_s=0.1)
    assert response["ok"] is True and response["request_id"] == "half"


def test_oversized_request_rejected_and_connection_closed(
        app, qt_application, tmp_path):
    chunk = b"x" * 64_000
    steps = [{"write": _b64(chunk)} for _ in
             range((MAX_REQUEST_BYTES // 64_000) + 2)]
    steps.append({"read": True, "timeout_ms": 6000})
    steps.append({"wait_disconnected": True, "timeout_ms": 3000})
    results = _run_scenario(qt_application, app, steps, tmp_path)
    response = json.loads(results[-2]["line"])
    assert response["ok"] is False
    assert response["code"] == "REQUEST_TOO_LARGE"
    assert results[-1]["disconnected"] is True
    # the app survived and still serves the next client
    followup = _exchange(qt_application, app, tmp_path, "status.get")
    assert followup["ok"] is True


def test_incomplete_line_times_out_and_closes(app, qt_application, tmp_path,
                                              monkeypatch):
    from retirement_pet import single_instance

    monkeypatch.setattr(single_instance, "AGENT_READ_TIMEOUT_MS", 200)
    partial = _request("status.get", request_id="stalled")[:12]
    results = _run_scenario(qt_application, app, [
        {"write": _b64(partial)},
        {"wait_disconnected": True, "timeout_ms": 3000},
    ], tmp_path)
    assert results[-1]["disconnected"] is True
    followup = _exchange(qt_application, app, tmp_path, "status.get")
    assert followup["ok"] is True  # app healthy after the drop


def test_reconnect_with_new_process_works(app, qt_application, tmp_path):
    first = _exchange(qt_application, app, tmp_path, "status.get")
    assert first["ok"]
    second = _exchange(qt_application, app, tmp_path, "status.get")
    assert second["ok"] and second["generation"] == first["generation"]


def test_legacy_ascii_commands_still_work_alongside(app, qt_application,
                                                    tmp_path):
    shown = []
    app._guard.on_show_requested = lambda: shown.append(True)
    results = _run_scenario(qt_application, app, [
        {"write": _b64(b"show\n")},
        {"wait_disconnected": True, "timeout_ms": 3000},
    ], tmp_path)
    assert results[-1]["disconnected"] is True
    deadline = time.monotonic() + 5
    while not shown and time.monotonic() < deadline:
        qt_application.processEvents()
        time.sleep(0.01)
    assert shown  # the primary reacted to the legacy command


def test_after_app_shutdown_client_cannot_connect(app, qt_application,
                                                  tmp_path):
    _exchange(qt_application, app, tmp_path, "status.get")
    app.shutdown()
    qt_application.processEvents()
    results = _run_scenario(qt_application, app, [
        {"connect": True, "timeout_ms": 2000},
    ], tmp_path, timeout_s=15)
    assert results[-1]["type"] == "connect"
    assert results[-1]["connected"] is False


def test_child_cannot_reach_foreign_server(qt_application, tmp_path):
    """Negative control: connecting to an unknown server name fails."""
    scenario_path = tmp_path / "foreign-scenario.json"
    out_path = tmp_path / "foreign-client.json"
    scenario_path.write_text(
        json.dumps({"steps": [{"connect": True, "timeout_ms": 1000}]}),
        encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(_HELPER),
         f"definitely-not-a-server-{tmp_path.name}", str(out_path),
         str(scenario_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process.wait(timeout=30)
    results = json.loads(out_path.read_text(encoding="utf-8"))["results"]
    assert results[0]["type"] == "connect"
    assert results[0]["connected"] is False


def test_unavailable_todo_store_reports_unavailable(qt_application,
                                                    tmp_path, monkeypatch):
    """A degraded store (corrupt db) fails closed through the protocol."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from retirement_pet.app import PetApplication
    from retirement_pet.clock import FakeClock

    (tmp_path / "tasks.db").write_bytes(b"not a sqlite file at all")
    pet = PetApplication(
        argv=["retirement-pet"],
        data_dir=tmp_path,
        clock=FakeClock(),
        headless=True,
        instance_name=f"pytest-agent-degraded-{tmp_path.name}",
    )
    try:
        response = _exchange(qt_application, pet, tmp_path, "todo.add",
                             {"title": "x", "horizon": "short"})
        assert response["ok"] is False
        assert response["code"] == "UNAVAILABLE"
        status = _exchange(qt_application, pet, tmp_path, "status.get")
        assert status["ok"] is True
        assert status["data"]["todo"]["available"] is False
    finally:
        pet.shutdown()


# -- todo operations over the wire ----------------------------------------------


def _add_task(qt_application, app, tmp_path, title, **extra) -> dict:
    response = _exchange(
        qt_application, app, tmp_path, "todo.add",
        {"title": title, "horizon": "short"}, **extra)
    assert response["ok"] is True, response
    return response


def test_todo_complete_requires_explicit_scope(app, qt_application,
                                               tmp_path):
    added = _add_task(qt_application, app, tmp_path, "范围任务")
    task_id = added["data"]["task"]["id"]

    response = _exchange(qt_application, app, tmp_path, "todo.complete",
                         {"task_id": task_id})
    assert response["ok"] is False
    assert response["code"] == INVALID_ARGS  # no hidden default scope

    response = _exchange(
        qt_application, app, tmp_path, "todo.complete",
        {"task_id": task_id, "scope": "subtree"})
    assert response["ok"] is True
    assert response["data"]["completed"] == 1


def test_todo_delete_requires_matching_confirm_count(app, qt_application,
                                                      tmp_path):
    added = _add_task(qt_application, app, tmp_path, "删除目标")
    task_id = added["data"]["task"]["id"]
    app.todo.add_subtask(task_id, "子")

    response = _exchange(
        qt_application, app, tmp_path, "todo.delete",
        {"task_id": task_id, "confirm_subtree_count": 1})
    assert response["ok"] is False
    assert response["code"] == CONFLICT  # store has 2, request said 1
    assert app.todo.get(task_id) is not None  # nothing deleted

    response = _exchange(
        qt_application, app, tmp_path, "todo.delete",
        {"task_id": task_id, "confirm_subtree_count": 2})
    assert response["ok"] is True
    assert response["data"]["deleted"] == 2
    assert app.todo.get(task_id) is None


def test_todo_delete_without_confirm_rejected(app, qt_application,
                                              tmp_path):
    added = _add_task(qt_application, app, tmp_path, "需确认")
    response = _exchange(qt_application, app, tmp_path, "todo.delete",
                         {"task_id": added["data"]["task"]["id"]})
    assert response["ok"] is False
    assert response["code"] == INVALID_ARGS


def test_todo_archive_and_restore_archived_roundtrip(app, qt_application,
                                                     tmp_path):
    """Archive is the everyday reversible action; the protocol exposes
    both directions (node-A acceptance gap found by reviewer C)."""
    added = _add_task(qt_application, app, tmp_path, "归档目标")
    task_id = added["data"]["task"]["id"]
    app.todo.add_subtask(task_id, "子")

    archived = _exchange(qt_application, app, tmp_path, "todo.archive",
                         {"task_id": task_id})
    assert archived["ok"] is True
    assert archived["data"]["archived"] == 2

    restored = _exchange(
        qt_application, app, tmp_path, "todo.restore_archived",
        {"task_id": task_id})
    assert restored["ok"] is True
    assert restored["data"]["restored"] == 2
    assert app.todo.get(task_id).archived is False

    missing = _exchange(
        qt_application, app, tmp_path, "todo.restore_archived",
        {"task_id": "nope"})
    assert missing["ok"] is False and missing["code"] == NOT_FOUND


def test_todo_set_importance_and_urgency(app, qt_application, tmp_path):
    """Quadrant axes are writable through the protocol (V131-A): explicit
    high/low, null clears, and the quadrant filter closes the loop."""
    added = _add_task(qt_application, app, tmp_path, "象限任务")
    task_id = added["data"]["task"]["id"]
    generation_before = added["generation"]

    imp = _exchange(qt_application, app, tmp_path, "todo.set_importance",
                    {"task_id": task_id, "importance": "high"})
    assert imp["ok"] is True, imp
    assert imp["data"]["task"]["importance"] == "high"
    urg = _exchange(qt_application, app, tmp_path, "todo.set_urgency",
                    {"task_id": task_id, "urgency": "low"})
    assert urg["ok"] is True, urg
    assert urg["data"]["task"]["urgency"] == "low"
    assert urg["generation"] > generation_before  # writes bump generation

    listing = _exchange(qt_application, app, tmp_path, "todo.list",
                        {"quadrant": "important_not_urgent"})
    assert [t["id"] for t in listing["data"]["tasks"]] == [task_id]

    cleared = _exchange(qt_application, app, tmp_path, "todo.set_importance",
                        {"task_id": task_id, "importance": None})
    assert cleared["ok"] is True
    assert cleared["data"]["task"]["importance"] is None
    # (importance cleared, urgency low) matches the not_* quadrant filter
    listing = _exchange(qt_application, app, tmp_path, "todo.list",
                        {"quadrant": "not_important_not_urgent"})
    assert [t["id"] for t in listing["data"]["tasks"]] == [task_id]

    bad_value = _exchange(qt_application, app, tmp_path, "todo.set_importance",
                          {"task_id": task_id, "importance": "medium"})
    assert bad_value["ok"] is False and bad_value["code"] == INVALID_ARGS
    missing_key = _exchange(qt_application, app, tmp_path,
                            "todo.set_importance", {"task_id": task_id})
    assert missing_key["ok"] is False \
        and missing_key["code"] == INVALID_ARGS
    not_found = _exchange(qt_application, app, tmp_path,
                          "todo.set_urgency",
                          {"task_id": "nope", "urgency": "high"})
    assert not_found["ok"] is False and not_found["code"] == NOT_FOUND


def test_todo_set_horizon_and_due_date(app, qt_application, tmp_path):
    added = _add_task(qt_application, app, tmp_path, "期限任务")
    task_id = added["data"]["task"]["id"]

    horizon = _exchange(qt_application, app, tmp_path, "todo.set_horizon",
                        {"task_id": task_id, "horizon": "medium"})
    assert horizon["ok"] is True
    assert horizon["data"]["task"]["horizon"] == "medium"

    due = _exchange(qt_application, app, tmp_path, "todo.set_due_date",
                    {"task_id": task_id, "due_date": "2026-10-01"})
    assert due["ok"] is True
    assert due["data"]["task"]["due_date"] == "2026-10-01"

    cleared = _exchange(qt_application, app, tmp_path, "todo.set_due_date",
                        {"task_id": task_id, "due_date": None})
    assert cleared["ok"] is True
    assert cleared["data"]["task"]["due_date"] is None

    bad_horizon = _exchange(qt_application, app, tmp_path, "todo.set_horizon",
                            {"task_id": task_id, "horizon": "daily"})
    assert bad_horizon["ok"] is False \
        and bad_horizon["code"] == INVALID_ARGS
    bad_date = _exchange(qt_application, app, tmp_path, "todo.set_due_date",
                         {"task_id": task_id, "due_date": "2026/10/01"})
    assert bad_date["ok"] is False and bad_date["code"] == INVALID_ARGS
    missing = _exchange(qt_application, app, tmp_path, "todo.set_due_date",
                        {"task_id": task_id})
    assert missing["ok"] is False and missing["code"] == INVALID_ARGS


@pytest.mark.parametrize("loose_iso", ["20260920", "2026-W38-7"])
def test_due_date_contract_is_strictly_yyyy_mm_dd(app, qt_application,
                                                  tmp_path, loose_iso):
    """CR-003: fromisoformat alone accepts basic ISO and week dates; the
    documented contract is exactly YYYY-MM-DD on every write path."""
    added = _add_task(qt_application, app, tmp_path, "日期契约")

    via_set = _exchange(qt_application, app, tmp_path, "todo.set_due_date",
                        {"task_id": added["data"]["task"]["id"],
                         "due_date": loose_iso})
    assert via_set["ok"] is False, loose_iso
    assert via_set["code"] == INVALID_ARGS

    via_add = _exchange(qt_application, app, tmp_path, "todo.add",
                        {"title": "坏日期", "horizon": "short",
                         "due_date": loose_iso})
    assert via_add["ok"] is False, loose_iso
    assert via_add["code"] == INVALID_ARGS

    strict = _exchange(qt_application, app, tmp_path, "todo.set_due_date",
                       {"task_id": added["data"]["task"]["id"],
                        "due_date": "2026-09-20"})
    assert strict["ok"] is True
    assert strict["data"]["task"]["due_date"] == "2026-09-20"


def test_todo_move_within_siblings(app, qt_application, tmp_path):
    root = _add_task(qt_application, app, tmp_path, "排序根")
    root_id = root["data"]["task"]["id"]
    child_ids = []
    for name in ("甲", "乙", "丙"):
        child = _exchange(
            qt_application, app, tmp_path, "todo.add",
            {"title": name, "horizon": "short", "parent_id": root_id})
        assert child["ok"] is True, child
        child_ids.append(child["data"]["task"]["id"])

    def sibling_titles():
        return [t.title for t in app.todo.children_of(root_id)]

    assert sibling_titles() == ["甲", "乙", "丙"]

    moved = _exchange(
        qt_application, app, tmp_path, "todo.move_within_siblings",
        {"task_id": child_ids[2], "delta": -2})
    assert moved["ok"] is True, moved
    assert sibling_titles() == ["丙", "甲", "乙"]

    # out-of-range delta is a safe no-op, not an error
    beyond = _exchange(
        qt_application, app, tmp_path, "todo.move_within_siblings",
        {"task_id": child_ids[2], "delta": 99})
    assert beyond["ok"] is True
    assert sibling_titles() == ["丙", "甲", "乙"]

    bad_delta = _exchange(
        qt_application, app, tmp_path, "todo.move_within_siblings",
        {"task_id": child_ids[2], "delta": "up"})
    assert bad_delta["ok"] is False and bad_delta["code"] == INVALID_ARGS
    bool_delta = _exchange(
        qt_application, app, tmp_path, "todo.move_within_siblings",
        {"task_id": child_ids[2], "delta": True})
    assert bool_delta["ok"] is False \
        and bool_delta["code"] == INVALID_ARGS


def test_missing_task_is_not_found(app, qt_application, tmp_path):
    response = _exchange(qt_application, app, tmp_path, "todo.get",
                         {"task_id": "nope"})
    assert response["ok"] is False and response["code"] == NOT_FOUND


def test_note_set_does_not_echo_the_note(app, qt_application, tmp_path):
    added = _add_task(qt_application, app, tmp_path, "备注任务")
    task_id = added["data"]["task"]["id"]
    response = _exchange(
        qt_application, app, tmp_path, "todo.note_set",
        {"task_id": task_id, "note": "# 秘密草稿\n- [ ] 事项"})
    assert response["ok"] is True
    assert "note" not in response["data"]
    assert response["data"]["note_chars"] == len("# 秘密草稿\n- [ ] 事项")
    fetched = _exchange(qt_application, app, tmp_path, "todo.get",
                        {"task_id": task_id, "include_note": True})
    assert fetched["data"]["note"] == "# 秘密草稿\n- [ ] 事项"


def test_expected_generation_conflict_after_ui_edit(app, qt_application,
                                                    tmp_path):
    before = _exchange(qt_application, app, tmp_path, "status.get")
    stale = before["data"]["todo"]["generation"]

    from retirement_pet.todo import Horizon

    app.todo.add_task("用户在 UI 里新建", Horizon.SHORT)  # bumps generation

    response = _exchange(
        qt_application, app, tmp_path, "todo.add",
        {"title": "陈旧代理写入", "horizon": "short"},
        expected_generation=stale)
    assert response["ok"] is False and response["code"] == CONFLICT
    titles = [t.title for t in app.todo.all_tasks()]
    assert "陈旧代理写入" not in titles


def test_expected_generation_match_succeeds(app, qt_application, tmp_path):
    response = _exchange(
        qt_application, app, tmp_path, "todo.add",
        {"title": "新鲜代理写入", "horizon": "short"},
        expected_generation=0)
    assert response["ok"] is True


def test_idempotency_key_replay_does_not_duplicate(app, qt_application,
                                                   tmp_path):
    """A retry reuses the business result but binds THIS request_id
    (CR13-03): the response is never the stale association."""
    first = _add_task(qt_application, app, tmp_path, "幂等创建",
                      idempotency_key="op-42", request_id="first-rid")
    second = _add_task(qt_application, app, tmp_path, "幂等创建",
                       idempotency_key="op-42", request_id="second-rid")
    assert second["request_id"] == "second-rid"  # fresh association
    assert first["data"] == second["data"]       # same business result
    assert first["data"]["task"]["id"] == second["data"]["task"]["id"]
    titles = [t.title for t in app.todo.all_tasks()]
    assert titles.count("幂等创建") == 1


def test_idempotency_key_reused_with_different_args_conflicts(
        app, qt_application, tmp_path):
    """CR13-03: the same key with a DIFFERENT request must be rejected
    without executing a second write."""
    _add_task(qt_application, app, tmp_path, "原始标题",
              idempotency_key="dup-key")
    response = _exchange(
        qt_application, app, tmp_path, "todo.add",
        {"title": "不同的写入", "horizon": "short"},
        idempotency_key="dup-key", request_id="attacker-1")
    assert response["ok"] is False
    assert response["code"] == CONFLICT
    titles = [t.title for t in app.todo.all_tasks()]
    assert "不同的写入" not in titles
    assert titles.count("原始标题") == 1


def test_idempotency_key_reused_across_operations_conflicts(
        app, qt_application, tmp_path):
    """The key namespace is per-write-operation: reusing one key for a
    different operation is a conflict, not a replay."""
    added = _add_task(qt_application, app, tmp_path, "跨操作目标",
                      idempotency_key="shared-key")
    task_id = added["data"]["task"]["id"]
    response = _exchange(
        qt_application, app, tmp_path, "todo.complete",
        {"task_id": task_id, "scope": "self"},
        idempotency_key="shared-key", request_id="cross-op")
    assert response["ok"] is False
    assert response["code"] == CONFLICT
    assert app.todo.get(task_id).status.value == "open"  # not executed


def test_idempotency_key_fingerprint_includes_concurrency_precondition(
        app, qt_application, tmp_path):
    """Changing expected_generation changes the fingerprint: a stale
    precondition under the same key is a conflict, not a silent replay."""
    _add_task(qt_application, app, tmp_path, "并发前置", idempotency_key="gen-key")
    response = _exchange(
        qt_application, app, tmp_path, "todo.add",
        {"title": "并发前置", "horizon": "short"},
        idempotency_key="gen-key", expected_generation=999999,
        request_id="stale-precond")
    assert response["ok"] is False
    assert response["code"] == CONFLICT


def test_idempotency_replay_after_client_timeout_retry(app, qt_application,
                                                       tmp_path):
    """A client that timed out and retries gets the SAME task, not a second;
    the retried response carries the retry's own request id."""
    first = _add_task(qt_application, app, tmp_path, "重试创建",
                      idempotency_key="retry-1", request_id="attempt-1")
    retried = _add_task(qt_application, app, tmp_path, "重试创建",
                        idempotency_key="retry-1", request_id="attempt-2")
    assert retried["data"]["task"]["id"] == first["data"]["task"]["id"]
    assert retried["request_id"] == "attempt-2"
    assert len(app.todo.all_tasks()) == 1


def test_idempotency_cache_is_bounded(app, qt_application, tmp_path):
    for index in range(IDEMPOTENCY_CACHE_SIZE + 4):
        _add_task(qt_application, app, tmp_path, f"批量{index}",
                  idempotency_key=f"k-{index}")
    assert len(app.agent_server._replay_cache) <= IDEMPOTENCY_CACHE_SIZE


def test_different_keys_same_operation_both_execute(app, qt_application,
                                                    tmp_path):
    _add_task(qt_application, app, tmp_path, "第一个", idempotency_key="a")
    _add_task(qt_application, app, tmp_path, "第二个", idempotency_key="b")
    assert len(app.todo.all_tasks()) == 2


def test_ui_immediately_refreshes_after_agent_mutation(app, qt_application,
                                                       tmp_path):
    """The same committed event the UI listens to also fires for agent
    writes, so an open Todo page updates without polling."""
    app._open_control_panel("todo")
    page = app._panel._built["todo"]
    qt_application.processEvents()
    assert page._items_by_id == {}

    events = []
    app.subscribe_todo_events(events.append)
    _add_task(qt_application, app, tmp_path, "界面联动任务")
    qt_application.processEvents()

    assert events  # the mutation announced itself
    page.refresh()
    assert any(item.text(0) == "界面联动任务"
               for item in page._items_by_id.values())


def test_protocol_log_lines_carry_no_task_titles(app, qt_application,
                                                 tmp_path, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="retirement_pet.agent_protocol"):
        _add_task(qt_application, app, tmp_path, "绝密标题内容")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "绝密标题内容" not in logged
    assert "agent op=todo.add" in logged
