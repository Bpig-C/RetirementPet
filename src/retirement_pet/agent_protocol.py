"""Versioned local agent control protocol (V13-01).

A single newline-framed UTF-8 JSON line protocol multiplexed onto the
existing single-instance ``QLocalServer``.  Legacy ASCII one-shot commands
(``show``/``quiet``/``panel``…) keep their exact behaviour; any line whose
first byte is ``{`` enters this protocol instead.

Design boundaries frozen by the V1.3 work order:

- same-machine local endpoint only (the per-user single-instance server
  name); this is NOT a remote or authenticated API;
- one request line -> exactly one response line; responses always carry
  ``protocol``, the verbatim ``request_id``, ``ok``, a stable ``code``,
  a human-readable ``message``, ``data`` and the current todo data
  ``generation``;
- explicit operation allowlist only: no SQL, file access, shell, dynamic
  import or arbitrary attribute paths ever reach a handler;
- handlers run on the application thread (they are invoked from the Qt
  socket callback) and call the real services - never Qt widgets or the
  SQLite layer directly;
- logs record only operation, result code and a de-identified request id
  hash; task titles, notes, media titles and paths never enter logs;
- mutations support an ``idempotency_key`` so a client timeout+retry
  cannot create/complete/delete twice, and an optional
  ``expected_generation`` for optimistic concurrency against UI edits.
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: protocol identity string (frozen; a breaking change means ".v2")
PROTOCOL_NAME = "retirement-pet.agent.v1"

#: hard wire limits (bytes); one bad oversized client must never OOM the app
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
#: how long a connection may sit with an incomplete line before it is closed
READ_TIMEOUT_MS = 10_000
#: bounded idempotency replay cache (mutation responses only)
IDEMPOTENCY_CACHE_SIZE = 64
#: de-identified request id for logs: first 8 hex chars of a stable hash
_REQUEST_ID_LOG_CHARS = 8

#: response codes (stable API surface; never rename, only append)
OK = "OK"
BAD_UTF8 = "BAD_UTF8"
BAD_JSON = "BAD_JSON"
BAD_REQUEST = "BAD_REQUEST"
UNKNOWN_PROTOCOL = "UNKNOWN_PROTOCOL"
UNKNOWN_OPERATION = "UNKNOWN_OPERATION"
INVALID_ARGS = "INVALID_ARGS"
NOT_FOUND = "NOT_FOUND"
CONFLICT = "CONFLICT"
UNAVAILABLE = "UNAVAILABLE"
RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
INTERNAL = "INTERNAL"


class AgentError(Exception):
    """A structured protocol failure carrying a stable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _anonymize_request_id(request_id: str) -> str:
    import hashlib

    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return digest[:_REQUEST_ID_LOG_CHARS]


def _response(request_id: str, *, ok: bool, code: str, message: str,
              data: Any, generation: int) -> dict:
    return {
        "protocol": PROTOCOL_NAME,
        "request_id": request_id,
        "ok": bool(ok),
        "code": code,
        "message": message,
        "data": data if data is not None else {},
        "generation": int(generation),
    }


def _encode_response(response: dict) -> bytes:
    payload = json.dumps(response, ensure_ascii=False,
                         separators=(",", ":"))
    encoded = (payload + "\n").encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise AgentError(
            RESPONSE_TOO_LARGE,
            "response exceeds the protocol byte limit")
    return encoded


def static_error_response(code: str, message: str) -> bytes:
    """A protocol-shaped failure with no request context (transport-level
    rejections emitted by the socket layer itself)."""
    return _encode_response(_response(
        "", ok=False, code=code, message=message, data={}, generation=0))


def _require_str(args: dict, key: str, *, max_len: int = 10_000) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise AgentError(INVALID_ARGS, f"{key} must be a string")
    if not value:
        raise AgentError(INVALID_ARGS, f"{key} must not be empty")
    if len(value) > max_len:
        raise AgentError(INVALID_ARGS, f"{key} is too long")
    return value


def _optional_str(args: dict, key: str, *,
                  max_len: int = 10_000) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AgentError(INVALID_ARGS, f"{key} must be a string or null")
    if len(value) > max_len:
        raise AgentError(INVALID_ARGS, f"{key} is too long")
    return value


def _optional_int(args: dict, key: str, *,
                  minimum: int | None = None) -> int | None:
    value = args.get(key)
    if value is None:
        return None
    if type(value) is not int:  # bool is an int subclass; reject it
        raise AgentError(INVALID_ARGS, f"{key} must be an integer")
    if minimum is not None and value < minimum:
        raise AgentError(INVALID_ARGS, f"{key} must be >= {minimum}")
    return value


def _require_int(args: dict, key: str) -> int:
    value = args.get(key)
    if type(value) is not int:  # bool is an int subclass; reject it
        raise AgentError(INVALID_ARGS, f"{key} must be an integer")
    return value


def _level_arg(args: dict, key: str) -> str | None:
    """Required quadrant axis: 'high'|'low'|null (null clears the axis)."""
    if key not in args:
        raise AgentError(INVALID_ARGS, f"{key} is required (high|low|null)")
    value = args[key]
    if value is not None and value not in ("high", "low"):
        raise AgentError(INVALID_ARGS, f"{key} must be high|low|null")
    return value


_STRICT_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def _parse_iso_date(raw: str, key: str):
    """Strict YYYY-MM-DD contract.

    ``date.fromisoformat`` alone also accepts basic ISO (``20260920``) and
    week dates (``2026-W38-7``); the CLI metavar, help text and protocol
    doc all promise exactly ``YYYY-MM-DD``, so anything else is
    INVALID_ARGS (CR-003).
    """
    from datetime import date

    if not _STRICT_DATE.match(raw):
        raise AgentError(INVALID_ARGS,
                         f"{key} must be an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise AgentError(INVALID_ARGS,
                         f"{key} must be an ISO date (YYYY-MM-DD)") from exc


def _task_summary(task) -> dict:
    """Task fields an agent may see.  Notes are NOT included here."""
    return {
        "id": task.id,
        "parent_id": task.parent_id,
        "title": task.title,
        "horizon": task.horizon.value,
        "status": task.status.value,
        "importance": task.importance.value if task.importance else None,
        "urgency": task.urgency.value if task.urgency else None,
        "archived": bool(task.archived),
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "sort_key": int(task.sort_key),
        "created_at": task.created_at.isoformat(timespec="seconds"),
        "updated_at": task.updated_at.isoformat(timespec="seconds"),
        "completed_at": (task.completed_at.isoformat(timespec="seconds")
                         if task.completed_at else None),
    }


class AgentProtocolServer:
    """Dispatches validated JSON requests to real application services.

    ``handle_line`` is the single entry point used by the single-instance
    socket layer.  It never raises: every failure becomes a structured
    response, so one hostile request cannot take down the UI.
    """

    def __init__(self, app):
        self._app = app
        self._operations: dict[str, Callable[[dict], Any]] = {}
        self._replay_cache: dict[str, dict] = {}
        self._replay_order: deque[str] = deque()
        self._register_todo_operations()
        self._register_status_operations()
        self._register_action_operations()
        self._register_character_operations()
        self._register_media_operations()

    # -- entry point ---------------------------------------------------------

    def handle_line(self, line: bytes) -> bytes | None:
        """Handle one request line; return the response line to write back.

        ``None`` means "ignore silently" (blank keep-alive line).  All
        handler work happens here on the calling (application) thread.
        """
        if not line.strip():
            return None
        try:
            return self._handle_validated(line)
        except AgentError as exc:
            return self._failure_response(exc, self._current_request_id)
        except Exception:  # noqa: BLE001 - one request must never kill the UI
            logger.exception("agent operation crashed")
            return self._failure_response(
                AgentError(INTERNAL, "internal error"),
                self._current_request_id)
        finally:
            self._current_request_id = ""

    #: the most recent parsed request id, for error attribution only
    _current_request_id = ""

    def _failure_response(self, exc: AgentError, request_id: str) -> bytes:
        response = _response(
            request_id, ok=False, code=exc.code, message=exc.message,
            data={}, generation=self._generation())
        try:
            return _encode_response(response)
        except AgentError:
            return (b'{"protocol":"' + PROTOCOL_NAME.encode("ascii")
                    + b'","request_id":"","ok":false,"code":"INTERNAL",'
                      b'"message":"internal error","data":{},"generation":0}\n')

    # -- request pipeline ----------------------------------------------------

    def _handle_validated(self, line: bytes) -> bytes:
        request = self._parse_request(line)
        request_id = request["request_id"]
        self._current_request_id = request_id
        operation = request["operation"]
        cache_key = self._cache_key(request)
        if cache_key is not None and cache_key in self._replay_cache:
            cached = self._replay_cache[cache_key]
            fingerprint = self._request_fingerprint(request)
            if fingerprint != cached["fingerprint"]:
                # CR13-03: the same key must never stand in for a second,
                # different write operation
                raise AgentError(
                    CONFLICT,
                    "idempotency key was already used with a different "
                    "request")
            # a genuine retry reuses the FIRST business result, but the
            # response is bound to THIS request's id and original
            # generation - never the stale association
            response = _response(
                request_id, ok=True, code=OK, message="ok",
                data=cached["data"], generation=cached["generation"])
            logger.info("agent op=%s code=%s rid=%s replay=1", operation,
                        OK, _anonymize_request_id(request_id))
            return _encode_response(response)
        handler = self._operations.get(operation)
        if handler is None:
            raise AgentError(UNKNOWN_OPERATION,
                             f"unknown operation: {operation[:64]}")
        self._check_expected_generation(request["expected_generation"])
        data = handler(request["args"])
        generation = self._generation()
        response = _response(
            request_id, ok=True, code=OK, message="ok",
            data=data, generation=generation)
        encoded = _encode_response(response)
        if cache_key is not None:
            self._store_replay(cache_key, {
                "fingerprint": self._request_fingerprint(request),
                "data": data,
                "generation": generation,
            })
        logger.info("agent op=%s code=%s rid=%s", operation, OK,
                    _anonymize_request_id(request_id))
        return encoded

    @staticmethod
    def _request_fingerprint(request: dict) -> str:
        """Canonical identity of the OPERATION a key stands for (CR13-03).

        operation, args and the concurrency precondition all participate;
        the request_id deliberately does not (a retry legitimately uses a
        fresh id).
        """
        import hashlib

        canonical = json.dumps(
            {"operation": request["operation"],
             "args": request["args"],
             "expected_generation": request["expected_generation"]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _parse_request(self, line: bytes) -> dict:
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AgentError(BAD_UTF8, "request is not valid UTF-8") from exc
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise AgentError(BAD_JSON, "request is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise AgentError(BAD_REQUEST, "request must be a JSON object")
        allowed = {"protocol", "request_id", "operation", "args",
                   "idempotency_key", "expected_generation"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise AgentError(
                BAD_REQUEST, f"unknown request fields: {unknown[:4]}")
        if payload.get("protocol") != PROTOCOL_NAME:
            raise AgentError(
                UNKNOWN_PROTOCOL,
                f"unsupported protocol: {str(payload.get('protocol'))[:64]}")
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id \
                or len(request_id) > 128:
            raise AgentError(BAD_REQUEST,
                             "request_id must be a non-empty string (<=128)")
        operation = payload.get("operation")
        if not isinstance(operation, str) or not operation \
                or len(operation) > 64:
            raise AgentError(BAD_REQUEST,
                             "operation must be a non-empty string (<=64)")
        args = payload.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise AgentError(BAD_REQUEST, "args must be a JSON object")
        key = payload.get("idempotency_key")
        if key is not None and (not isinstance(key, str) or len(key) > 128):
            raise AgentError(
                BAD_REQUEST, "idempotency_key must be a string (<=128)")
        generation = payload.get("expected_generation")
        if generation is not None and (type(generation) is not int
                                       or generation < 0):
            raise AgentError(
                BAD_REQUEST,
                "expected_generation must be a non-negative integer")
        return {
            "request_id": request_id,
            "operation": operation,
            "args": args,
            "idempotency_key": key,
            "expected_generation": generation,
        }

    def _cache_key(self, request: dict) -> str | None:
        # the cache slot is the KEY ALONE (CR13-03): mixing the operation
        # into the slot would let one key stand for two different writes
        return request["idempotency_key"]

    def _store_replay(self, cache_key: str, response: dict) -> None:
        self._replay_cache[cache_key] = response
        self._replay_order.append(cache_key)
        while len(self._replay_order) > IDEMPOTENCY_CACHE_SIZE:
            oldest = self._replay_order.popleft()
            self._replay_cache.pop(oldest, None)

    def _generation(self) -> int:
        # direct field access: the public `.todo` property would force
        # lazy store initialization from a read-only path
        todo = getattr(self._app, "_todo_service", None)
        return int(getattr(todo, "data_generation", 0) or 0)

    def _check_expected_generation(self, expected: int | None) -> None:
        if expected is None:
            return
        current = self._generation()
        if expected != current:
            raise AgentError(
                CONFLICT,
                f"stale generation: expected {expected}, current {current}")

    # -- shared service access with structured failures ------------------------

    def _todo(self):
        todo = getattr(self._app, "todo", None)
        if todo is None:
            raise AgentError(UNAVAILABLE, "todo module is not available")
        if getattr(todo, "degraded", False):
            raise AgentError(UNAVAILABLE,
                             "todo store is degraded (write-forbidden)")
        return todo

    def _call(self, operation: Callable[[], Any]):
        """Run one service call mapping domain errors to stable codes."""
        from retirement_pet.todo import TodoError
        from retirement_pet.todo.errors import TaskStoreUnavailable

        try:
            return operation()
        except AgentError:
            raise
        except TaskStoreUnavailable as exc:
            raise AgentError(UNAVAILABLE,
                             f"todo store unavailable: {exc.reason}") from exc
        except TodoError as exc:
            message = str(exc)
            if "not found" in message:
                raise AgentError(NOT_FOUND, "task not found") from exc
            raise AgentError(INVALID_ARGS, message) from exc

    # -- operation registry ----------------------------------------------------

    def _register_status_operations(self) -> None:
        def status_get(_args: dict) -> dict:
            from retirement_pet import __version__

            # Read-only and side-effect free: never force-lazy-initialize
            # the todo store (an absent family must stay absent until a
            # real todo operation or UI use opens it).
            service = getattr(self._app, "_todo_service", None)
            initialized = service is not None
            degraded = bool(getattr(service, "degraded", True))
            return {
                "app": "RetirementPet",
                "version": __version__,
                "todo": {
                    "initialized": initialized,
                    "available": initialized and not degraded,
                    "generation": self._generation(),
                    "focusing": bool(self._app.todo_bridge.projection
                                     .focusing),
                },
                "activity": self._app.activity_status(),
            }

        self._operations["status.get"] = status_get

    def _register_todo_operations(self) -> None:
        from retirement_pet.todo import Horizon, Level

        def todo_add(args: dict) -> dict:
            todo = self._todo()
            title = _require_str(args, "title", max_len=200)
            horizon_raw = _optional_str(args, "horizon")
            if horizon_raw is None:
                horizon = Horizon.SHORT
            else:
                try:
                    horizon = Horizon(horizon_raw)
                except ValueError as exc:
                    raise AgentError(
                        INVALID_ARGS,
                        "horizon must be short|medium|long") from exc
            parent_id = _optional_str(args, "parent_id", max_len=64)
            due_date = _optional_str(args, "due_date", max_len=10)
            parsed_due = None if due_date is None \
                else _parse_iso_date(due_date, "due_date")
            task = self._call(lambda: todo.add_task(
                title, horizon, parent_id=parent_id, due_date=parsed_due))
            return {"task": _task_summary(task)}

        def todo_get(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            include_note = args.get("include_note", False)
            if not isinstance(include_note, bool):
                raise AgentError(INVALID_ARGS, "include_note must be a bool")
            task = self._call(lambda: todo.get(task_id))
            if task is None:
                raise AgentError(NOT_FOUND, "task not found")
            data = {"task": _task_summary(task)}
            if include_note:
                data["note"] = self._call(lambda: todo.note_for(task_id))
            return data

        def todo_list(args: dict) -> dict:
            todo = self._todo()
            parent_id = _optional_str(args, "parent_id", max_len=64)
            status_filter = _optional_str(args, "status", max_len=10)
            if status_filter is not None \
                    and status_filter not in ("open", "done"):
                raise AgentError(INVALID_ARGS,
                                 "status must be open|done when given")
            horizon_raw = _optional_str(args, "horizon")
            horizon = None
            if horizon_raw is not None:
                try:
                    horizon = Horizon(horizon_raw)
                except ValueError as exc:
                    raise AgentError(
                        INVALID_ARGS,
                        "horizon must be short|medium|long") from exc
            archived = args.get("archived", False)
            if not isinstance(archived, bool):
                raise AgentError(INVALID_ARGS, "archived must be a bool")
            quadrant = _optional_str(args, "quadrant", max_len=32)
            allowed_quadrants = ("important_urgent",
                                 "important_not_urgent",
                                 "not_important_urgent",
                                 "not_important_not_urgent",
                                 "uncategorized")
            if quadrant is not None and quadrant not in allowed_quadrants:
                raise AgentError(
                    INVALID_ARGS,
                    "quadrant must be important_urgent|important_not_urgent|"
                    "not_important_urgent|not_important_not_urgent|"
                    "uncategorized")
            limit = _optional_int(args, "limit", minimum=1)
            if limit is None:
                limit = 200
            if limit > 1000:
                raise AgentError(INVALID_ARGS, "limit must be <= 1000")
            cursor = _optional_int(args, "cursor", minimum=0)
            if cursor is None:
                cursor = 0

            def load() -> list:
                tasks = todo.all_tasks()
                matched = []
                for task in tasks:
                    if parent_id is not None \
                            and task.parent_id != parent_id:
                        continue
                    if status_filter is not None \
                            and task.status.value != status_filter:
                        continue
                    if horizon is not None and task.horizon is not horizon:
                        continue
                    if task.archived is not archived:
                        continue
                    if quadrant is not None:
                        if quadrant == "uncategorized":
                            if task.importance is not None \
                                    or task.urgency is not None:
                                continue
                        else:
                            want_i = quadrant.startswith("important")
                            want_u = quadrant.endswith("urgent") \
                                and "not_urgent" not in quadrant
                            actual_i = task.importance is not None \
                                and task.importance.value == "high"
                            actual_u = task.urgency is not None \
                                and task.urgency.value == "high"
                            if (actual_i, actual_u) != (want_i, want_u):
                                continue
                    matched.append(task)
                return matched

            matched = self._call(load)
            window = matched[cursor:cursor + limit]
            next_cursor = (cursor + limit
                           if cursor + limit < len(matched) else None)
            return {
                "tasks": [_task_summary(task) for task in window],
                "total_matched": len(matched),
                "next_cursor": next_cursor,
            }

        def todo_rename(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            title = _require_str(args, "title", max_len=200)
            self._call(lambda: todo.rename(task_id, title))
            return {"task": _task_summary(todo.get(task_id))}

        def todo_set_importance(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            raw = _level_arg(args, "importance")
            level = None if raw is None else Level(raw)
            self._call(lambda: todo.set_importance(task_id, level))
            return {"task": _task_summary(todo.get(task_id))}

        def todo_set_urgency(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            raw = _level_arg(args, "urgency")
            level = None if raw is None else Level(raw)
            self._call(lambda: todo.set_urgency(task_id, level))
            return {"task": _task_summary(todo.get(task_id))}

        def todo_set_horizon(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            horizon_raw = _require_str(args, "horizon", max_len=10)
            try:
                horizon = Horizon(horizon_raw)
            except ValueError as exc:
                raise AgentError(
                    INVALID_ARGS,
                    "horizon must be short|medium|long") from exc
            self._call(lambda: todo.set_horizon(task_id, horizon))
            return {"task": _task_summary(todo.get(task_id))}

        def todo_set_due_date(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            if "due_date" not in args:
                raise AgentError(
                    INVALID_ARGS, "due_date is required (ISO date or null)")
            raw = args.get("due_date")
            if raw is not None and not isinstance(raw, str):
                raise AgentError(
                    INVALID_ARGS,
                    "due_date must be an ISO date string or null")
            parsed = None if raw is None \
                else _parse_iso_date(raw, "due_date")
            self._call(lambda: todo.set_due_date(task_id, parsed))
            return {"task": _task_summary(todo.get(task_id))}

        def todo_move_within_siblings(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            delta = _require_int(args, "delta")
            # out-of-range deltas are a documented safe no-op in the service
            self._call(lambda: todo.move_within_siblings(task_id, delta))
            return {"task": _task_summary(todo.get(task_id))}

        def todo_note_set(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            note = args.get("note")
            if not isinstance(note, str):
                raise AgentError(INVALID_ARGS, "note must be a string")
            # bounded by the domain rule; over-limit is a visible error
            self._call(lambda: todo.set_note(task_id, note))
            # the response deliberately does NOT echo the full note
            return {"note_saved": True, "note_chars": len(note)}

        def todo_complete(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            scope = _require_str(args, "scope", max_len=16)
            if scope not in ("self", "subtree"):
                raise AgentError(INVALID_ARGS,
                                 "scope must be explicitly self|subtree")
            count = self._call(lambda: todo.complete(task_id, scope=scope))
            return {"completed": count, "scope": scope}

        def todo_restore(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            scope = _require_str(args, "scope", max_len=16)
            if scope not in ("self", "subtree"):
                raise AgentError(INVALID_ARGS,
                                 "scope must be explicitly self|subtree")
            count = self._call(lambda: todo.restore(task_id, scope=scope))
            return {"restored": count, "scope": scope}

        def todo_archive(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            count = self._call(lambda: todo.archive_subtree(task_id))
            return {"archived": count}

        def todo_restore_archived(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            count = self._call(lambda: todo.restore_archived(task_id))
            return {"restored": count}

        def todo_delete(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            confirmed = _optional_int(args, "confirm_subtree_count")
            if confirmed is None:
                raise AgentError(
                    INVALID_ARGS,
                    "confirm_subtree_count is required for deletion")
            summary = self._call(lambda: todo.subtree_summary(task_id))
            if confirmed != summary.total:
                raise AgentError(
                    CONFLICT,
                    f"confirm_subtree_count mismatch: store has "
                    f"{summary.total}, request confirmed {confirmed}")
            deleted = self._call(lambda: todo.delete_subtree(task_id))
            return {"deleted": deleted}

        def todo_focus_start(args: dict) -> dict:
            todo = self._todo()
            task_id = _require_str(args, "task_id", max_len=64)
            started = self._call(lambda: todo.start_focus(task_id))
            return {"focused": True, "changed": bool(started)}

        def todo_focus_stop(_args: dict) -> dict:
            todo = self._todo()
            self._call(lambda: todo.stop_focus())
            return {"focused": False}

        for name, handler in {
            "todo.add": todo_add,
            "todo.get": todo_get,
            "todo.list": todo_list,
            "todo.rename": todo_rename,
            "todo.set_importance": todo_set_importance,
            "todo.set_urgency": todo_set_urgency,
            "todo.set_horizon": todo_set_horizon,
            "todo.set_due_date": todo_set_due_date,
            "todo.move_within_siblings": todo_move_within_siblings,
            "todo.note_set": todo_note_set,
            "todo.complete": todo_complete,
            "todo.restore": todo_restore,
            "todo.archive": todo_archive,
            "todo.restore_archived": todo_restore_archived,
            "todo.delete": todo_delete,
            "todo.focus_start": todo_focus_start,
            "todo.focus_stop": todo_focus_stop,
        }.items():
            self._operations[name] = handler

    # -- action operations (V13-02) ---------------------------------------------

    def _register_action_operations(self) -> None:
        from retirement_pet.action_registry import ActionRegistry
        from retirement_pet.models import ActionId

        registry = ActionRegistry()

        def _current_action_payload() -> dict:
            controller = self._app.controller
            runtime = controller.current
            active_contexts = set(self._app.contexts.active())
            current = None
            if runtime is not None:
                now_ms = self._app.clock.monotonic_ms()
                current = {
                    "action": runtime.spec.action_id.value,
                    "priority": int(runtime.spec.priority),
                    "elapsed_ms": int(runtime.elapsed_ms(now_ms)),
                    "duration_ms": (int(runtime.duration_ms)
                                    if runtime.duration_ms is not None
                                    else None),
                }
            return {
                "current": current,
                "meeting": "MEETING" in active_contexts,
                "do_not_disturb": "DO_NOT_DISTURB" in active_contexts,
            }

        def action_list(_args: dict) -> dict:
            actions = []
            for action_id in ActionId:
                if action_id in (ActionId.BLINK,):
                    continue  # overlay-only; never a main action
                try:
                    spec = registry.get(action_id)
                except Exception:  # noqa: BLE001 - registry is static
                    continue
                semantic = action_id.value
                actions.append({
                    "action": semantic,
                    "priority": int(spec.priority),
                    "min_duration_ms": int(spec.min_duration_ms),
                    "max_duration_ms": (int(spec.max_duration_ms)
                                        if spec.max_duration_ms is not None
                                        else None),
                    "mode": self._app.action_mode(semantic),
                })
            actions.sort(key=lambda item: (-item["priority"],
                                           item["action"]))
            return {"actions": actions}

        def action_status(_args: dict) -> dict:
            return _current_action_payload()

        def action_trigger(args: dict) -> dict:
            semantic = _require_str(args, "action", max_len=32)
            from retirement_pet.models import ActionId as _ActionId

            try:
                _ActionId(semantic)
            except ValueError as exc:
                raise AgentError(INVALID_ARGS,
                                 f"unknown action: {semantic}") from exc
            accepted, message = self._app.request_manual_action(semantic)
            payload = _current_action_payload()
            payload.update({"requested": semantic, "accepted": bool(accepted),
                            "reason": message})
            if not accepted:
                payload["note"] = ("request rejected; no action changed")
            return payload

        def action_stop(_args: dict) -> dict:
            self._app.standby_now()
            payload = _current_action_payload()
            # the standby path always ends the user performance; services
            # may legitimately re-derive a new performance right away, so
            # the payload reports the live post-stop state
            payload["ended_user_performance"] = True
            return payload

        self._operations["action.list"] = action_list
        self._operations["action.status"] = action_status
        self._operations["action.trigger"] = action_trigger
        self._operations["action.stop"] = action_stop

    # -- character operations (V13-02) -------------------------------------------

    def _register_character_operations(self) -> None:
        def _entry_payload(entry) -> dict:
            return {
                "character_id": entry.character_id,
                "display_name": entry.display_name,
                "publisher_id": entry.publisher_id,
                "package_id": entry.package_id,
                "package_version": entry.package_version,
                "trust_channel": entry.trust_channel,
                "builtin": bool(entry.builtin),
                "revision": str(entry.revision_key),
            }

        def character_list(_args: dict) -> dict:
            entries = self._app.catalog.entries()
            return {"characters": [_entry_payload(entry)
                                   for entry in entries]}

        def character_current(_args: dict) -> dict:
            app = self._app
            data = {"in_safe_mode": bool(app.switcher.in_safe_mode)}
            selection = None
            try:
                selection = app._selection_store.get("active")
            except Exception:  # noqa: BLE001 - report, never crash
                data["selection_readable"] = False
            if selection is not None:
                entry = next(
                    (item for item in app.catalog.entries()
                     if item.revision_key == selection.revision_key()),
                    None)
                data["selection_readable"] = True
                data["active"] = {
                    "character_fqid": selection.character_fqid,
                    "character_id": (entry.character_id if entry
                                     else selection.character_fqid),
                    "display_name": (entry.display_name if entry
                                     else selection.character_fqid),
                    "builtin": bool(entry.builtin) if entry else None,
                    "revision": str(selection.revision_key()),
                }
            return data

        def character_switch(args: dict) -> dict:
            character_id = _require_str(args, "character_id", max_len=64)
            package_id = _optional_str(args, "package_id", max_len=64)
            entries = self._app.catalog.entries()
            matches = [entry for entry in entries
                       if entry.character_id == character_id
                       and (package_id is None
                            or entry.package_id == package_id)]
            if not matches:
                raise AgentError(
                    NOT_FOUND,
                    "no installed character matches the request")
            if len(matches) > 1:
                raise AgentError(
                    INVALID_ARGS,
                    "multiple revisions match; pass package_id to "
                    "disambiguate")
            entry = matches[0]
            switched = self._app.switch_character_entry(entry)
            data = character_current({})
            data["switched"] = bool(switched)
            if not switched:
                # an honest failure: the previous character stays active
                data["note"] = ("switch did not commit; the previous "
                                "character remains active")
            return data

        self._operations["character.list"] = character_list
        self._operations["character.current"] = character_current
        self._operations["character.switch"] = character_switch

    # -- media operations (V13-05) ----------------------------------------------

    def _register_media_operations(self) -> None:
        def media_status(_args: dict) -> dict:
            bridge = getattr(self._app, "media_bridge", None)
            if bridge is None:
                raise AgentError(UNAVAILABLE, "media bridge is not built")
            snapshot = bridge.snapshot()
            payload = snapshot.to_payload()
            payload["provider"] = bridge.provider_name
            return payload

        def _media_command(operation: str):
            def handler(args: dict) -> dict:
                bridge = getattr(self._app, "media_bridge", None)
                if bridge is None:
                    raise AgentError(UNAVAILABLE,
                                     "media bridge is not built")
                session_id = _optional_str(args, "session_id", max_len=64)
                accepted, message = {
                    "media.play_pause": bridge.play_pause,
                    "media.next": bridge.skip_next,
                    "media.previous": bridge.skip_previous,
                }[operation](session_id)
                return {
                    "requested": operation.split(".", 1)[1],
                    "accepted": bool(accepted),
                    "reason": message,
                    "status": bridge.snapshot().to_payload(),
                }

            return handler

        self._operations["media.status"] = media_status
        self._operations["media.play_pause"] = _media_command(
            "media.play_pause")
        self._operations["media.next"] = _media_command("media.next")
        self._operations["media.previous"] = _media_command(
            "media.previous")

    # -- introspection for --help / schema docs ---------------------------------

    def operations(self) -> tuple[str, ...]:
        return tuple(sorted(self._operations))


class AgentClient:
    """Minimal blocking client for tests and the agent CLI.

    Sends one JSON request line over a real ``QLocalSocket`` and reads
    exactly one response line.  Uses ``waitFor*`` only, so it works in a
    ``QCoreApplication``-less helper process.
    """

    def __init__(self, server: str, *, timeout_ms: int = 5000):
        self._server = server
        self._timeout_ms = int(timeout_ms)

    def request(self, operation: str, args: dict | None = None, *,
                request_id: str = "cli",
                idempotency_key: str | None = None,
                expected_generation: int | None = None) -> tuple[int, dict]:
        """Return ``(exit_code, response_dict)``; never raises for protocol
        failures - failures use exit code 1 and ok=false responses."""
        from PySide6.QtNetwork import QLocalSocket

        payload = {
            "protocol": PROTOCOL_NAME,
            "request_id": request_id,
            "operation": operation,
            "args": args or {},
        }
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        if expected_generation is not None:
            payload["expected_generation"] = expected_generation
        line = json.dumps(payload, ensure_ascii=False,
                          separators=(",", ":")) + "\n"

        socket = QLocalSocket()
        socket.connectToServer(self._server)
        if not socket.waitForConnected(self._timeout_ms):
            return 3, _response(request_id, ok=False, code=UNAVAILABLE,
                                message="cannot connect to a running "
                                        "RetirementPet instance",
                                data={}, generation=0)
        socket.write(line.encode("utf-8"))
        socket.flush()
        # waitForBytesWritten returns false IMMEDIATELY (not on timeout)
        # when flush() already wrote everything synchronously and nothing
        # is pending - only treat it as failure if bytes remain buffered.
        if socket.bytesToWrite() \
                and not socket.waitForBytesWritten(self._timeout_ms):
            state = str(socket.state()).split(".")[-1]
            error = socket.errorString()
            socket.disconnectFromServer()
            return 3, _response(request_id, ok=False, code=UNAVAILABLE,
                                message=f"timed out sending the request "
                                        f"(state={state} error={error})",
                                data={}, generation=0)
        buffer = bytearray()
        while b"\n" not in buffer:
            if not socket.waitForReadyRead(self._timeout_ms):
                socket.disconnectFromServer()
                return 3, _response(request_id, ok=False, code=UNAVAILABLE,
                                    message="timed out waiting for the "
                                            "response",
                                    data={}, generation=0)
            buffer.extend(bytes(socket.readAll()))
            if len(buffer) > MAX_RESPONSE_BYTES:
                socket.disconnectFromServer()
                return 3, _response(request_id, ok=False,
                                    code=RESPONSE_TOO_LARGE,
                                    message="response exceeded the byte "
                                            "limit",
                                    data={}, generation=0)
        socket.disconnectFromServer()
        response_line = bytes(buffer).split(b"\n", 1)[0]
        try:
            response = json.loads(response_line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return 4, _response(request_id, ok=False, code=BAD_JSON,
                                message="server sent an unparseable "
                                        "response",
                                data={}, generation=0)
        if not isinstance(response, dict):
            return 4, _response(request_id, ok=False, code=BAD_JSON,
                                message="server sent a non-object response",
                                data={}, generation=0)
        return (0 if response.get("ok") else 1), response
