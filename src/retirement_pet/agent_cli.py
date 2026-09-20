"""Agent CLI (V13-02): thin command-line wrapper over the local protocol.

``RetirementPet.exe agent <domain> <verb> ...`` (source runs:
``python -m retirement_pet agent ...``).  The CLI holds no business logic:
every command maps to one ``retirement-pet.agent.v1`` operation executed by
the running application through the real local socket.

Output contract:

- ``--json``: stdout carries EXACTLY one JSON object (the protocol
  response); diagnostics go to stderr.
- default: a short human-readable summary on stdout.
- exit codes: 0 ok · 1 operation failed (ok=false) · 2 usage error ·
  3 no running instance / transport failure · 4 unparseable response.

The CLI never touches ``tasks.db`` or settings directly; when the
application is not running, commands fail with exit 3 instead of starting
a second store.
"""

from __future__ import annotations

import argparse
import json
import sys


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true",
                        help="机器输出：stdout 只写一个响应 JSON 对象")
    parser.add_argument("--timeout-ms", type=int, default=5000,
                        help="本地往返超时（毫秒，默认 5000）")


def _add_mutation(parser: argparse.ArgumentParser) -> None:
    _add_common(parser)
    parser.add_argument("--idempotency-key", default=None,
                        help="幂等键：超时重试不会重复执行")
    parser.add_argument("--expected-generation", type=int, default=None,
                        help="乐观并发：与应用当前 generation 不一致则拒绝")


def _add_task(parser: argparse.ArgumentParser, *, required: bool = True):
    parser.add_argument("--task", required=required,
                        help="目标任务的稳定 task id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="RetirementPet agent",
        description="查询与操作正在运行的 RetirementPet（本地 Agent 协议）",
    )
    parser.add_argument("--version", action="store_true",
                        help="显示 CLI 与协议版本")
    domains = parser.add_subparsers(dest="domain", metavar="<domain>")

    status = domains.add_parser("status", help="应用状态")
    status_verbs = status.add_subparsers(dest="verb", required=True)
    _add_common(status_verbs.add_parser("get", help="应用与 Todo 概要"))

    todo = domains.add_parser("todo", help="任务管理")
    todo_verbs = todo.add_subparsers(dest="verb", required=True)

    listing = todo_verbs.add_parser("list", help="列出任务")
    _add_common(listing)
    listing.add_argument("--parent", default=None, help="按父任务过滤")
    listing.add_argument("--status", choices=["open", "done"], default=None)
    listing.add_argument("--horizon",
                         choices=["short", "medium", "long"], default=None)
    listing.add_argument("--archived", action="store_true",
                         help="列出已归档任务（默认列出未归档）")
    listing.add_argument("--quadrant", default=None, dest="quadrant",
                         choices=["important_urgent", "important_not_urgent",
                                  "not_important_urgent",
                                  "not_important_not_urgent",
                                  "uncategorized"],
                         help="按重要×紧急象限筛选（或未分类）")
    listing.add_argument("--limit", type=int, default=None,
                         help="每页数量（协议默认 200，最大 1000）")
    listing.add_argument("--cursor", type=int, default=None,
                         help="分页游标（上一页 next_cursor）")

    getting = todo_verbs.add_parser("get", help="读取单个任务")
    _add_common(getting)
    _add_task(getting)
    getting.add_argument("--with-note", action="store_true",
                         help="包含完整备注")

    adding = todo_verbs.add_parser("add", help="新建任务")
    _add_mutation(adding)
    adding.add_argument("--title", required=True, help="任务标题")
    adding.add_argument("--horizon", choices=["short", "medium", "long"],
                        default="short")
    adding.add_argument("--parent", default=None, help="父任务 id")
    adding.add_argument("--due", default=None, metavar="YYYY-MM-DD")

    renaming = todo_verbs.add_parser("rename", help="重命名")
    _add_mutation(renaming)
    _add_task(renaming)
    renaming.add_argument("--title", required=True)

    importance_set = todo_verbs.add_parser("importance-set",
                                           help="设置重要轴")
    _add_mutation(importance_set)
    _add_task(importance_set)
    importance_set.add_argument("--importance", required=True,
                                choices=["high", "low", "none"],
                                help="high|low|none（none 清除）")

    urgency_set = todo_verbs.add_parser("urgency-set", help="设置紧急轴")
    _add_mutation(urgency_set)
    _add_task(urgency_set)
    urgency_set.add_argument("--urgency", required=True,
                             choices=["high", "low", "none"],
                             help="high|low|none（none 清除）")

    horizon_set = todo_verbs.add_parser("horizon-set", help="修改期限")
    _add_mutation(horizon_set)
    _add_task(horizon_set)
    horizon_set.add_argument("--horizon", required=True,
                             choices=["short", "medium", "long"])

    due_set = todo_verbs.add_parser("due-set", help="修改截止日期")
    _add_mutation(due_set)
    _add_task(due_set)
    due_set.add_argument("--due", required=True,
                         metavar="YYYY-MM-DD|none",
                         help="ISO 日期；none 清除")

    move = todo_verbs.add_parser("move", help="同级上移/下移")
    _add_mutation(move)
    _add_task(move)
    move.add_argument("--delta", type=int, required=True, metavar="N",
                      help="负数上移，正数下移；越界为安全 no-op")

    note_set = todo_verbs.add_parser("note-set", help="保存备注原文")
    _add_mutation(note_set)
    _add_task(note_set)
    note_source = note_set.add_mutually_exclusive_group()
    note_source.add_argument("--note-file", default=None,
                             help="从文件读取备注（'-' 表示 stdin）")
    note_source.add_argument("--note", default=None,
                             help="直接给出备注内容（短备注才建议使用）")

    completing = todo_verbs.add_parser("complete", help="完成任务")
    _add_mutation(completing)
    _add_task(completing)
    completing.add_argument("--scope", choices=["self", "subtree"],
                            required=True,
                            help="显式范围：self 仅此项，subtree 整个子树")

    restoring = todo_verbs.add_parser("restore", help="重新打开已完成任务")
    _add_mutation(restoring)
    _add_task(restoring)
    restoring.add_argument("--scope", choices=["self", "subtree"],
                           required=True,
                           help="显式范围：self 仅此项，subtree 子树内全部已完成")

    archiving = todo_verbs.add_parser("archive", help="归档子树（可逆）")
    _add_mutation(archiving)
    _add_task(archiving)

    unarchiving = todo_verbs.add_parser("restore-archived",
                                        help="恢复已归档子树")
    _add_mutation(unarchiving)
    _add_task(unarchiving)

    deleting = todo_verbs.add_parser("delete", help="物理删除子树")
    _add_mutation(deleting)
    _add_task(deleting)
    deleting.add_argument("--confirm-subtree-count", type=int, required=True,
                          metavar="N",
                          help="确认子树任务总数；与存储不一致则拒绝")

    focus_start = todo_verbs.add_parser("focus-start", help="开始专注")
    _add_mutation(focus_start)
    _add_task(focus_start)

    focus_stop = todo_verbs.add_parser("focus-stop", help="停止专注")
    _add_mutation(focus_stop)

    action = domains.add_parser("action", help="桌宠动作")
    action_verbs = action.add_subparsers(dest="verb", required=True)
    _add_common(action_verbs.add_parser("list", help="全部动作与模式"))
    _add_common(action_verbs.add_parser("status", help="当前动作"))
    trigger = action_verbs.add_parser("trigger", help="触发动作")
    _add_common(trigger)
    trigger.add_argument("--action", required=True, metavar="NAME",
                         help="动作名（见 action list）")
    _add_common(action_verbs.add_parser("stop", help="结束当前表演（待机）"))

    character = domains.add_parser("character", help="角色")
    character_verbs = character.add_subparsers(dest="verb", required=True)
    _add_common(character_verbs.add_parser("list", help="已安装角色"))
    _add_common(character_verbs.add_parser("current", help="当前角色"))
    switch = character_verbs.add_parser("switch", help="切换角色")
    _add_mutation(switch)
    switch.add_argument("--character", required=True, metavar="ID")
    switch.add_argument("--package", default=None, metavar="ID",
                        help="同名角色多版本时用于消歧")

    media = domains.add_parser("media", help="系统媒体会话（V13-05）")
    media_verbs = media.add_subparsers(dest="verb", required=True)
    _add_common(media_verbs.add_parser("status", help="媒体会话状态"))
    _add_common(media_verbs.add_parser("play-pause", help="播放/暂停"))
    _add_common(media_verbs.add_parser("next", help="下一曲"))
    _add_common(media_verbs.add_parser("previous", help="上一曲"))

    return parser


def _build_operation(args) -> tuple[str, dict, dict]:
    """Map parsed CLI args to (operation, op_args, request_extras)."""
    extras = {}
    if getattr(args, "idempotency_key", None):
        extras["idempotency_key"] = args.idempotency_key
    if getattr(args, "expected_generation", None) is not None:
        extras["expected_generation"] = args.expected_generation

    domain, verb = args.domain, args.verb
    if domain == "status":
        return "status.get", {}, extras
    if domain == "todo":
        if verb == "list":
            op_args = {"archived": bool(args.archived)}
            for key, flag in (("parent_id", "parent"),
                              ("status", "status"),
                              ("horizon", "horizon"),
                              ("quadrant", "quadrant"),
                              ("limit", "limit"),
                              ("cursor", "cursor")):
                value = getattr(args, flag)
                if value is not None:
                    op_args[key] = value
            return "todo.list", op_args, extras
        if verb == "get":
            return ("todo.get", {"task_id": args.task,
                                 "include_note": bool(args.with_note)},
                    extras)
        if verb == "add":
            op_args = {"title": args.title, "horizon": args.horizon}
            if args.parent:
                op_args["parent_id"] = args.parent
            if args.due:
                op_args["due_date"] = args.due
            return "todo.add", op_args, extras
        if verb == "rename":
            return ("todo.rename",
                    {"task_id": args.task, "title": args.title}, extras)
        if verb == "importance-set":
            return ("todo.set_importance",
                    {"task_id": args.task,
                     "importance": (None if args.importance == "none"
                                    else args.importance)},
                    extras)
        if verb == "urgency-set":
            return ("todo.set_urgency",
                    {"task_id": args.task,
                     "urgency": (None if args.urgency == "none"
                                 else args.urgency)},
                    extras)
        if verb == "horizon-set":
            return ("todo.set_horizon",
                    {"task_id": args.task, "horizon": args.horizon}, extras)
        if verb == "due-set":
            return ("todo.set_due_date",
                    {"task_id": args.task,
                     "due_date": (None if args.due == "none" else args.due)},
                    extras)
        if verb == "move":
            return ("todo.move_within_siblings",
                    {"task_id": args.task, "delta": args.delta}, extras)
        if verb == "note-set":
            note = _read_note(args)
            return "todo.note_set", {"task_id": args.task, "note": note}, \
                extras
        if verb == "complete":
            return ("todo.complete",
                    {"task_id": args.task, "scope": args.scope}, extras)
        if verb == "restore":
            return ("todo.restore",
                    {"task_id": args.task, "scope": args.scope}, extras)
        if verb == "archive":
            return "todo.archive", {"task_id": args.task}, extras
        if verb == "restore-archived":
            return "todo.restore_archived", {"task_id": args.task}, extras
        if verb == "delete":
            return ("todo.delete",
                    {"task_id": args.task,
                     "confirm_subtree_count": args.confirm_subtree_count},
                    extras)
        if verb == "focus-start":
            return "todo.focus_start", {"task_id": args.task}, extras
        if verb == "focus-stop":
            return "todo.focus_stop", {}, extras
    if domain == "action":
        if verb == "list":
            return "action.list", {}, extras
        if verb == "status":
            return "action.status", {}, extras
        if verb == "trigger":
            return "action.trigger", {"action": args.action}, extras
        if verb == "stop":
            return "action.stop", {}, extras
    if domain == "character":
        if verb == "list":
            return "character.list", {}, extras
        if verb == "current":
            return "character.current", {}, extras
        if verb == "switch":
            op_args = {"character_id": args.character}
            if args.package:
                op_args["package_id"] = args.package
            return "character.switch", op_args, extras
    if domain == "media":
        operation = {"status": "media.status",
                     "play-pause": "media.play_pause",
                     "next": "media.next",
                     "previous": "media.previous"}[verb]
        return operation, {}, extras
    raise AssertionError(f"unmapped CLI command: {domain} {verb}")


def _read_note(args) -> str:
    if args.note is not None:
        return args.note
    if args.note_file == "-":
        return sys.stdin.read()
    if args.note_file is not None:
        from pathlib import Path

        try:
            return Path(args.note_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"agent: cannot read note file: {exc.strerror}",
                  file=sys.stderr)
            raise SystemExit(2) from exc
    if sys.stdin.isatty():
        print("agent: note-set 需要 --note、--note-file 或管道 stdin",
              file=sys.stderr)
        raise SystemExit(2)
    return sys.stdin.read()


def _human_summary(operation: str, response: dict) -> str:
    code = response.get("code", "?")
    generation = response.get("generation")
    head = f"code={code} generation={generation}"
    if not response.get("ok"):
        message = response.get("message", "")
        return f"{head} {message}".strip()
    data = response.get("data") or {}
    return f"{head}\n{json.dumps(data, ensure_ascii=False, indent=2)}"


def _import_runtime():
    """Import the Qt runtime with a bounded retry.

    On busy Windows machines a fresh process occasionally fails the first
    PySide6 DLL load ("找不到指定的程序"/procedure-not-found) while the
    identical import succeeds moments later (loader/AV contention under
    rapid process churn).  The CLI has done nothing yet at this point, so
    retrying the import is safe and strictly better than crashing.
    """
    import time

    last: ImportError | None = None
    for attempt in range(4):
        try:
            from PySide6.QtCore import QCoreApplication

            from retirement_pet.agent_protocol import AgentClient
            from retirement_pet.single_instance import server_name

            return QCoreApplication, AgentClient, server_name
        except ImportError as exc:
            last = exc
            time.sleep(0.25 * (attempt + 1))
    raise last  # type: ignore[misc]


def run_cli(argv: list[str]) -> int:
    parser = build_parser()
    # domain-only (or bare) invocations are help requests, not usage errors
    if not argv:
        argv = ["--help"]
    elif (len(argv) == 1
          and argv[0] in ("status", "todo", "action", "character", "media")):
        argv = argv + ["--help"]
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse already printed help (0) or a usage error (2)
        return int(exc.code or 0)
    if getattr(args, "version", False):
        from retirement_pet import __version__

        from retirement_pet.agent_protocol import PROTOCOL_NAME

        print(f"RetirementPet {__version__} agent CLI ({PROTOCOL_NAME})")
        return 0

    operation, op_args, extras = _build_operation(args)

    # QtNetwork's blocking waits need a QCoreApplication-owned dispatcher
    # even in a console process.
    QCoreApplication, AgentClient, server_name = _import_runtime()
    QCoreApplication(sys.argv[:1])

    timeout_ms = max(500, int(getattr(args, "timeout_ms", 5000)))
    client = AgentClient(server_name(), timeout_ms=timeout_ms)
    exit_code, response = client.request(
        operation, op_args, request_id=f"cli-{operation}", **extras)

    if args.json:
        # machine mode: stdout carries exactly ONE JSON object
        print(json.dumps(response, ensure_ascii=False, sort_keys=True))
        if exit_code != 0:
            print(f"agent: {response.get('code')}: "
                  f"{response.get('message')}", file=sys.stderr)
        return exit_code
    if exit_code == 0:
        print(_human_summary(operation, response))
    else:
        print(f"agent: {response.get('code')}: "
              f"{response.get('message')}", file=sys.stderr)
    return exit_code
