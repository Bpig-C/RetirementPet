"""V13-02: Agent CLI end-to-end over the real local protocol.

隔离约定：headless 应用使用独立临时数据目录与唯一实例名；CLI 以真实
子进程运行（python -m retirement_pet agent ...），通过实例名环境变量
指向测试应用。不读写日用 %APPDATA% 数据。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


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
        instance_name=f"pytest-cli-{tmp_path.name}",
    )
    yield pet
    pet.shutdown()


def _cli(qt_application, app, *argv, stdin: str | None = None,
         timeout_s: float = 40):
    env = os.environ.copy()
    env["RETIREMENT_PET_INSTANCE_NAME"] = app._guard._name
    env["PYTHONPATH"] = str(_REPO_ROOT / "src") + os.pathsep \
        + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    # file-backed stdin: the CLI reads it to EOF while we pump the app
    # loop; a pipe would deadlock (we only collect output after exit)
    stdin_handle = subprocess.DEVNULL
    stdin_file = None
    if stdin is not None:
        stdin_file = app._data_dir / f"cli-stdin-{time.monotonic_ns()}.md"
        stdin_file.write_text(stdin, encoding="utf-8")
        stdin_handle = open(stdin_file, "rb")
    process = subprocess.Popen(
        [sys.executable, "-m", "retirement_pet", "agent", *argv],
        stdin=stdin_handle,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(_REPO_ROOT), env=env)
    if stdin_file is not None:
        stdin_handle.close()
    deadline = time.monotonic() + timeout_s
    while process.poll() is None and time.monotonic() < deadline:
        qt_application.processEvents()
        time.sleep(0.01)
    qt_application.processEvents()
    try:
        out, err = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        raise
    finally:
        if stdin_file is not None:
            stdin_file.unlink(missing_ok=True)
    return process.returncode, out.decode("utf-8"), err.decode("utf-8")


def _cli_json(qt_application, app, *argv, stdin=None, expect_code=0):
    code, out, err = _cli(qt_application, app, *argv, "--json",
                          stdin=stdin)
    assert code == expect_code, f"exit={code} stdout={out} stderr={err}"
    payload = json.loads(out)
    assert payload["ok"] is (expect_code == 0)
    return payload


# -- the work order's end-to-end happy path -----------------------------------


def test_cli_full_daily_flow(qt_application, app, tmp_path):
    # 1. 查状态
    status = _cli_json(qt_application, app, "status", "get")
    assert status["data"]["app"] == "RetirementPet"

    # 2. 建父子任务
    parent = _cli_json(qt_application, app, "todo", "add", "--title",
                       "版本发布", "--horizon", "medium",
                       "--idempotency-key", "flow-add-1")
    parent_id = parent["data"]["task"]["id"]
    child = _cli_json(qt_application, app, "todo", "add", "--title",
                      "写发布说明", "--parent", parent_id)
    child_id = child["data"]["task"]["id"]
    assert child["data"]["task"]["parent_id"] == parent_id

    # 3. 写 Markdown 备注（stdin 管道，不进进程列表）
    markdown = "# 发布说明\n\n- [ ] 摘要\n- [x] 兼容性\n\n**加粗** `code`\n"
    note = _cli_json(qt_application, app, "todo", "note-set", "--task",
                     child_id, stdin=markdown)
    assert note["data"]["note_chars"] == len(markdown)
    assert markdown not in json.dumps(note)  # 响应不回显全文

    # 4. 聚焦
    focus = _cli_json(qt_application, app, "todo", "focus-start", "--task",
                      child_id)
    assert focus["data"]["focused"] is True
    assert app.todo.focus_task_id() == child_id

    # 5. 完成子树
    done = _cli_json(qt_application, app, "todo", "complete", "--task",
                     parent_id, "--scope", "subtree")
    assert done["data"]["completed"] == 2
    assert app.todo.get(parent_id).status.value == "done"
    assert app.todo.get(child_id).status.value == "done"
    assert app.todo.focus_task_id() is None  # focus inside subtree cleared

    # 6. 恢复（仅父项）
    restored = _cli_json(qt_application, app, "todo", "restore", "--task",
                         parent_id, "--scope", "self")
    assert restored["data"]["restored"] == 1
    assert app.todo.get(child_id).status.value == "done"

    # 7. 归档 → 恢复归档
    archived = _cli_json(qt_application, app, "todo", "archive", "--task",
                         parent_id)
    assert archived["data"]["archived"] == 2
    unarchived = _cli_json(qt_application, app, "todo", "restore-archived",
                           "--task", parent_id)
    assert unarchived["data"]["restored"] == 2

    # 8. 删除（数量确认）
    deleted = _cli_json(qt_application, app, "todo", "delete", "--task",
                        parent_id, "--confirm-subtree-count", "2")
    assert deleted["data"]["deleted"] == 2
    assert app.todo.get(parent_id) is None
    assert app.todo.get(child_id) is None

    # 备注 get 仍可读回（重建一个快速验证 include_note 与 --note-file -）
    probe = _cli_json(qt_application, app, "todo", "add", "--title", "备注读回")
    _cli(qt_application, app, "todo", "note-set", "--task",
         probe["data"]["task"]["id"], "--note", "正文 ABC")
    got = _cli_json(qt_application, app, "todo", "get", "--task",
                    probe["data"]["task"]["id"], "--with-note")
    assert got["data"]["note"] == "正文 ABC"
    again = _cli_json(qt_application, app, "todo", "note-set", "--task",
                      probe["data"]["task"]["id"], "--note-file", "-",
                      stdin="管道版本")
    assert again["data"]["note_chars"] == len("管道版本")


def test_cli_metadata_setters_and_move(qt_application, app):
    """V131-A: quadrant axes, horizon, due date and sibling order are all
    writable from the CLI against the RUNNING app."""
    parent = _cli_json(qt_application, app, "todo", "add", "--title",
                       "元数据任务")
    task_id = parent["data"]["task"]["id"]

    imp = _cli_json(qt_application, app, "todo", "importance-set",
                    "--task", task_id, "--importance", "high")
    assert imp["data"]["task"]["importance"] == "high"
    urg = _cli_json(qt_application, app, "todo", "urgency-set",
                    "--task", task_id, "--urgency", "low")
    assert urg["data"]["task"]["urgency"] == "low"
    assert app.todo.get(task_id).urgency.value == "low"  # UI store agrees

    cleared = _cli_json(qt_application, app, "todo", "importance-set",
                        "--task", task_id, "--importance", "none")
    assert cleared["data"]["task"]["importance"] is None

    horizon = _cli_json(qt_application, app, "todo", "horizon-set",
                        "--task", task_id, "--horizon", "medium")
    assert horizon["data"]["task"]["horizon"] == "medium"

    due = _cli_json(qt_application, app, "todo", "due-set",
                    "--task", task_id, "--due", "2026-10-01")
    assert due["data"]["task"]["due_date"] == "2026-10-01"
    due_cleared = _cli_json(qt_application, app, "todo", "due-set",
                            "--task", task_id, "--due", "none")
    assert due_cleared["data"]["task"]["due_date"] is None

    first = _cli_json(qt_application, app, "todo", "add", "--title", "一",
                      "--parent", task_id)
    _cli_json(qt_application, app, "todo", "add", "--title", "二",
              "--parent", task_id)
    moved = _cli_json(qt_application, app, "todo", "move",
                      "--task", first["data"]["task"]["id"], "--delta", "1")
    assert moved["ok"] is True
    assert [t.title for t in app.todo.children_of(task_id)] == ["二", "一"]

    # invalid choice is a usage error (exit 2); protocol-level bad date
    # surfaces as operation failure (exit 1)
    code, _out, _err = _cli(qt_application, app, "todo", "importance-set",
                            "--task", task_id, "--importance", "medium",
                            "--json")
    assert code == 2
    bad_date = _cli_json(qt_application, app, "todo", "due-set",
                         "--task", task_id, "--due", "2026-13-45",
                         expect_code=1)
    assert bad_date["code"] == "INVALID_ARGS"

    # CR-003: the contract is exactly YYYY-MM-DD; Python-accepted loose
    # ISO forms must fail through the CLI too
    for loose_iso in ("20260920", "2026-W38-7"):
        rejected = _cli_json(qt_application, app, "todo", "due-set",
                             "--task", task_id, "--due", loose_iso,
                             expect_code=1)
        assert rejected["code"] == "INVALID_ARGS", loose_iso
    rejected_add = _cli_json(qt_application, app, "todo", "add", "--title",
                             "坏日期", "--due", "20260920", expect_code=1)
    assert rejected_add["code"] == "INVALID_ARGS"


def test_cli_list_filters_and_pagination(qt_application, app):
    for index in range(3):
        _cli_json(qt_application, app, "todo", "add", "--title",
                  f"任务{index}")
    listed = _cli_json(qt_application, app, "todo", "list", "--status",
                       "open", "--limit", "2")
    assert len(listed["data"]["tasks"]) == 2
    assert listed["data"]["total_matched"] == 3
    assert listed["data"]["next_cursor"] == 2
    page2 = _cli_json(qt_application, app, "todo", "list", "--cursor", "2")
    assert len(page2["data"]["tasks"]) == 1
    assert page2["data"]["next_cursor"] is None


def test_cli_list_quadrant_filter(qt_application, app):
    from retirement_pet.todo import Level

    created = _cli_json(qt_application, app, "todo", "add", "--title",
                        "象限任务")
    task_id = created["data"]["task"]["id"]
    app.todo.set_importance(task_id, Level.HIGH)
    app.todo.set_urgency(task_id, Level.HIGH)
    _cli_json(qt_application, app, "todo", "add", "--title", "未分类任务")

    urgent = _cli_json(qt_application, app, "todo", "list", "--quadrant",
                       "important_urgent")
    assert [t["id"] for t in urgent["data"]["tasks"]] == [task_id]
    uncategorized = _cli_json(qt_application, app, "todo", "list",
                              "--quadrant", "uncategorized")
    assert len(uncategorized["data"]["tasks"]) == 1
    # invalid values are caught client-side as usage errors
    code, out, err = _cli(qt_application, app, "todo", "list",
                          "--quadrant", "nope")
    assert code == 2


# -- negative paths -------------------------------------------------------------


def test_cli_without_running_instance_exits_3(qt_application, tmp_path):
    env = os.environ.copy()
    env["RETIREMENT_PET_INSTANCE_NAME"] = f"nobody-home-{tmp_path.name}"
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.run(
        [sys.executable, "-m", "retirement_pet", "agent", "status", "get",
         "--json"],
        capture_output=True, cwd=str(_REPO_ROOT), env=env, timeout=30)
    assert process.returncode == 3
    payload = json.loads(process.stdout.decode("utf-8"))
    assert payload["ok"] is False
    assert payload["code"] == "UNAVAILABLE"


def test_cli_usage_error_exits_2(qt_application, app):
    code, out, err = _cli(qt_application, app, "todo", "complete", "--task",
                          "x")  # missing required --scope
    assert code == 2
    assert "--scope" in err


def test_cli_delete_with_wrong_count_exits_1_conflict(qt_application, app):
    created = _cli_json(qt_application, app, "todo", "add", "--title", "目标")
    task_id = created["data"]["task"]["id"]
    _cli_json(qt_application, app, "todo", "add", "--title", "子",
              "--parent", task_id)
    payload = _cli_json(qt_application, app, "todo", "delete", "--task",
                        task_id, "--confirm-subtree-count", "1",
                        expect_code=1)
    assert payload["code"] == "CONFLICT"
    assert app.todo.get(task_id) is not None  # nothing deleted


def test_cli_stale_generation_conflict(qt_application, app):
    status = _cli_json(qt_application, app, "status", "get")
    stale = status["data"]["todo"]["generation"]
    from retirement_pet.todo import Horizon

    app.todo.add_task("UI 同时编辑", Horizon.SHORT)  # generation moves on
    payload = _cli_json(qt_application, app, "todo", "add", "--title",
                        "陈旧写入", "--expected-generation", str(stale),
                        expect_code=1)
    assert payload["code"] == "CONFLICT"


def test_cli_idempotency_key_prevents_duplicate_creation(qt_application,
                                                         app):
    first = _cli_json(qt_application, app, "todo", "add", "--title", "一次",
                      "--idempotency-key", "cli-once")
    second = _cli_json(qt_application, app, "todo", "add", "--title", "一次",
                       "--idempotency-key", "cli-once")
    assert first == second
    assert len(app.todo.all_tasks()) == 1


def test_cli_invalid_task_exits_1_not_found(qt_application, app):
    payload = _cli_json(qt_application, app, "todo", "get", "--task",
                        "missing-id", expect_code=1)
    assert payload["code"] == "NOT_FOUND"


def test_cli_json_stdout_is_exactly_one_object(qt_application, app):
    code, out, err = _cli(qt_application, app, "status", "get", "--json")
    assert code == 0
    assert err == ""
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert set(payload) == {"protocol", "request_id", "ok", "code",
                            "message", "data", "generation"}


def test_cli_human_mode_prints_summary_and_help_works(qt_application, app):
    code, out, err = _cli(qt_application, app, "status", "get")
    assert code == 0
    assert out.startswith("code=OK generation=")
    assert "RetirementPet" in out

    code, out, err = _cli(qt_application, app, "--version")
    assert code == 0
    assert "agent CLI" in out

    code, out, err = _cli(qt_application, app, "todo")
    assert code == 0  # domain-only prints its help (a help request)
    assert "list" in out

    code, out, err = _cli(qt_application, app)
    assert code == 0  # bare agent prints the main help
    assert "<domain>" in out


# -- action / character domains ---------------------------------------------------


def test_cli_action_list_status_trigger_stop(qt_application, app):
    listed = _cli_json(qt_application, app, "action", "list")
    actions = {item["action"] for item in listed["data"]["actions"]}
    assert {"work", "rest", "eat"} <= actions
    modes = {item["mode"] for item in listed["data"]["actions"]}
    assert modes <= {"auto", "manual", "disabled"}

    status = _cli_json(qt_application, app, "action", "status")
    assert "current" in status["data"]

    triggered = _cli_json(qt_application, app, "action", "trigger",
                          "--action", "stretch")
    assert triggered["data"]["accepted"] in (True, False)
    assert "reason" in triggered["data"]

    unknown = _cli_json(qt_application, app, "action", "trigger", "--action",
                        "fly-to-moon", expect_code=1)
    assert unknown["code"] == "INVALID_ARGS"

    stopped = _cli_json(qt_application, app, "action", "stop")
    assert stopped["data"]["ended_user_performance"] is True


def test_cli_character_list_current_switch(qt_application, app):
    listed = _cli_json(qt_application, app, "character", "list")
    characters = listed["data"]["characters"]
    assert characters, "at least the builtin official cat must be listed"
    builtin = next(entry for entry in characters if entry["builtin"])

    current = _cli_json(qt_application, app, "character", "current")
    assert current["data"]["active"]["character_id"]

    switched = _cli_json(qt_application, app, "character", "switch",
                         "--character", builtin["character_id"],
                         "--package", builtin["package_id"],
                         "--idempotency-key", "switch-1")
    assert switched["data"]["switched"] is True

    missing = _cli_json(qt_application, app, "character", "switch",
                        "--character", "no-such-cat", expect_code=1)
    assert missing["code"] == "NOT_FOUND"


def test_cli_media_reports_bridge_availability(qt_application, app):
    """V13-05 wires the bridge; before/without it the honest answer is
    an explicit unavailable status, never a fake success."""
    code, out, err = _cli(qt_application, app, "media", "status", "--json")
    payload = json.loads(out)
    if payload["ok"]:
        assert "bridge_available" in payload["data"]
    else:
        assert payload["code"] == "UNKNOWN_OPERATION"
