"""Single-instance guard via QLocalServer/QLocalSocket (design 12, v2 21.1).

First process owns a named local server.  Later processes connect and send
a structured newline-terminated command, then exit:

- ``show``    - ask the primary instance to show the pet (v1 behaviour);
- ``quiet``   - report a duplicate autostart without showing or activating it;
- ``quit``    - ask the primary instance to exit *normally*; only honoured
                when the primary was started with ``--test-ipc-quit``.
                This exists for the repeatable normal-exit harness
                (CONFORMANCE 7.4: a Stop-Process smoke is not exit evidence)
                and is not part of the user-facing command set.

Since V13-01 the same server also multiplexes the versioned local agent
JSON line protocol: any request line whose first byte is ``{`` is handed
to ``on_agent_line`` (one response line is written back) while legacy
ASCII commands keep their exact behaviour.  Per-connection reads are
newline-buffered with a byte cap and an idle timeout so one slow or
hostile client cannot stall or bloat the UI process.

Stale servers from crashed runs are removed safely.
"""

from __future__ import annotations

import getpass
import logging
import os
import re

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from retirement_pet import APP_ID
from retirement_pet.agent_protocol import (
    REQUEST_TOO_LARGE,
    static_error_response,
)

logger = logging.getLogger(__name__)

SHOW_COMMAND = "show"
QUIET_COMMAND = "quiet"
QUIT_COMMAND = "quit"
HIDE_COMMAND = "hide"
PANEL_COMMAND = "panel"  # panel | panel:<page-id>
PANEL_CLOSE_COMMAND = "panel-close"

#: environment override so verification harnesses can isolate the server
#: name from a really-running user instance.
INSTANCE_NAME_ENV = "RETIREMENT_PET_INSTANCE_NAME"

#: wire limits shared with the agent protocol (V13-01)
MAX_LINE_BYTES = 256 * 1024
AGENT_READ_TIMEOUT_MS = 10_000


def server_name(name: str | None = None) -> str:
    """Stable per-user server name (app id + sanitized username)."""
    if name:
        return name
    env = os.environ.get(INSTANCE_NAME_ENV)
    if env:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", env)
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "default"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", user)
    return f"{APP_ID}-single-{safe}"


def send_command(command: str, name: str | None = None,
                 timeout_ms: int = 300) -> bool:
    """Client side: deliver one command to the primary instance.

    Returns True when a live primary accepted the connection and the bytes
    were written.  Used by ``main --ipc-send`` (harness/diagnostic path).
    """
    probe = QLocalSocket()
    probe.connectToServer(server_name(name))
    if not probe.waitForConnected(timeout_ms):
        return False
    probe.write(f"{command}\n".encode("ascii"))
    probe.flush()
    probe.waitForBytesWritten(timeout_ms)
    probe.disconnectFromServer()
    if probe.state() != QLocalSocket.UnconnectedState:
        probe.waitForDisconnected(timeout_ms)
    return True


class SingleInstanceGuard:
    def __init__(self, name: str | None = None, *,
                 allow_quit_command: bool = False):
        self._name = server_name(name)
        self._allow_quit_command = bool(allow_quit_command)
        self._server: QLocalServer | None = None
        # Keeps the secondary's probe socket alive until its payload has
        # drained; destroying it early loses the buffered pipe data.
        self._probe: QLocalSocket | None = None
        # Per-connection newline framing state (V13-01): partial inbound
        # bytes and the idle timer guarding an incomplete line.
        self._rx_buffers: dict[QLocalSocket, bytes] = {}
        self._rx_timers: dict[QLocalSocket, QTimer] = {}
        # Optional agent protocol hook: bytes line -> response bytes|None.
        self.on_agent_line = None

    @property
    def is_primary(self) -> bool:
        return self._server is not None

    def acquire(self, *, command: str = SHOW_COMMAND) -> bool:
        """Try to become the primary instance.

        Returns True when this process owns the pet; False when another
        instance was found and notified (caller should exit).  Autostart uses
        ``quiet`` so an already-running primary never steals focus.
        """
        if command not in (SHOW_COMMAND, QUIET_COMMAND):
            raise ValueError("unsupported secondary-instance command")
        probe = QLocalSocket()
        probe.connectToServer(self._name)
        if probe.waitForConnected(300):
            probe.write(f"{command}\n".encode("ascii"))
            probe.flush()
            probe.waitForBytesWritten(300)
            self._probe = probe  # referenced until release()
            probe.disconnectFromServer()
            if probe.state() != QLocalSocket.UnconnectedState:
                probe.waitForDisconnected(300)
            logger.info("another instance is running; sent %s", command)
            return False

        # No live owner: clear any stale server socket left by a crash.
        QLocalServer.removeServer(self._name)
        server = QLocalServer()
        # CR13-02: the endpoint must be reachable only by the SAME Windows
        # user.  UserAccessOption sets the named-pipe DACL accordingly;
        # without it any local account could connect to the agent protocol.
        server.setSocketOptions(QLocalServer.UserAccessOption)
        if not server.listen(self._name):
            logger.error("cannot listen on %s: %s", self._name, server.errorString())
            # Pathological case (e.g. socket in a bad state): run anyway rather
            # than refusing to start the pet.
            server.deleteLater()
            self._server = None
            return True
        if not (server.socketOptions() & QLocalServer.UserAccessOption):
            # the platform refused the user-scoped DACL: serving an
            # unscoped endpoint is not acceptable (fail closed)
            logger.error("user-scoped socket options unavailable; "
                         "local server stays closed")
            server.close()
            server.deleteLater()
            self._server = None
            return True
        server.newConnection.connect(self._on_connection)
        self._server = server
        return True

    def _on_connection(self) -> None:
        connection = self._server.nextPendingConnection() if self._server else None
        if connection is None:
            return
        connection.readyRead.connect(lambda: self._read(connection))
        connection.disconnected.connect(
            lambda c=connection: self._forget_connection(c))
        # Data may already have arrived before the signal was connected.
        if connection.bytesAvailable() > 0:
            self._read(connection)

    def _forget_connection(self, connection: QLocalSocket) -> None:
        self._rx_buffers.pop(connection, None)
        timer = self._rx_timers.pop(connection, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        # the socket object has no parent; reclaim it once Qt is done
        connection.deleteLater()

    def _abort_connection(self, connection: QLocalSocket,
                          message: str, *,
                          notify: bytes | None = None) -> None:
        logger.debug("dropping instance connection: %s", message)
        if notify is not None and connection.state() \
                == QLocalSocket.ConnectedState:
            # best-effort notice for a well-behaved client; a hostile or
            # stalled peer must never cost the UI thread any wait
            connection.write(notify)
            connection.flush()
        self._forget_connection(connection)
        connection.disconnectFromServer()

    def _arm_rx_timer(self, connection: QLocalSocket) -> None:
        timer = self._rx_timers.get(connection)
        if timer is None:
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(
                lambda c=connection: self._abort_connection(
                    c, "incomplete line timed out"))
            self._rx_timers[connection] = timer
        timer.start(AGENT_READ_TIMEOUT_MS)

    def _disarm_rx_timer(self, connection: QLocalSocket) -> None:
        timer = self._rx_timers.get(connection)
        if timer is not None:
            timer.stop()

    def _read(self, connection: QLocalSocket) -> None:
        buffered = self._rx_buffers.get(connection, b"") \
            + bytes(connection.readAll())
        if b"\n" not in buffered:
            # incomplete line: cap, remember and wait for the rest
            if len(buffered) > MAX_LINE_BYTES:
                self._abort_connection(
                    connection, "line over byte cap",
                    notify=static_error_response(
                        REQUEST_TOO_LARGE,
                        "request line exceeds the byte limit"))
                return
            self._rx_buffers[connection] = buffered
            self._arm_rx_timer(connection)
            return
        self._disarm_rx_timer(connection)
        line, _, remainder = buffered.partition(b"\n")
        self._rx_buffers[connection] = remainder
        if len(line) > MAX_LINE_BYTES:
            self._abort_connection(
                connection, "line over byte cap",
                notify=static_error_response(
                    REQUEST_TOO_LARGE,
                    "request line exceeds the byte limit"))
            return
        self._dispatch_line(connection, line)
        # serve any further pipelined lines on the next event-loop pass
        if remainder and connection.state() == QLocalSocket.ConnectedState:
            QTimer.singleShot(0, lambda: self._read(connection)
                              if connection.state()
                              == QLocalSocket.ConnectedState else None)

    def _dispatch_line(self, connection: QLocalSocket,
                       raw_line: bytes) -> None:
        line = raw_line.strip()
        if line[:1] == b"{":
            self._dispatch_agent_line(connection, line)
            return
        data = line.decode("ascii", "replace")
        if data == SHOW_COMMAND:
            self.on_show_requested()
        elif data == QUIET_COMMAND:
            logger.info("quiet duplicate autostart ignored")
        elif data == QUIT_COMMAND and self._allow_quit_command:
            self.on_quit_requested()
        elif data == HIDE_COMMAND and self._allow_quit_command:
            self.on_hide_requested()
        elif data == PANEL_COMMAND or data.startswith(PANEL_COMMAND + ":"):
            page = data.split(":", 1)[1] if ":" in data else "overview"
            self.on_panel_requested(page)
        elif data == PANEL_CLOSE_COMMAND:
            self.on_panel_close_requested()
        else:
            logger.debug("ignoring unknown instance command: %r", data[:32])
        connection.disconnectFromServer()

    def _dispatch_agent_line(self, connection: QLocalSocket,
                             line: bytes) -> None:
        handler = self.on_agent_line
        if handler is None:
            logger.debug("agent protocol line ignored: no handler")
            connection.disconnectFromServer()
            return
        try:
            response = handler(line)
        except Exception:  # noqa: BLE001 - never kill the UI for one line
            logger.exception("agent protocol handler failed")
            response = None
        if response:
            connection.write(response)
            connection.flush()
        # stay connected: the agent protocol allows further requests

    # Subclass or monkeypatch these; the Qt layer also connects via signal.
    def on_panel_close_requested(self) -> None:  # pragma: no cover
        """Subclass or monkeypatch; the Qt layer connects via signal."""
        logger.info("panel close requested (no handler configured)")

    def on_show_requested(self) -> None:  # pragma: no cover - overridden
        logger.info("show requested by second instance")

    def on_quit_requested(self) -> None:  # pragma: no cover - overridden
        logger.info("quit requested by second instance")

    def on_hide_requested(self) -> None:  # pragma: no cover - overridden
        logger.info("hide requested by second instance")

    def on_panel_requested(self, page: str) -> None:  # pragma: no cover
        logger.info("panel requested by second instance: %s", page)

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        self._probe = None
        for timer in self._rx_timers.values():
            timer.stop()
            timer.deleteLater()
        self._rx_timers.clear()
        self._rx_buffers.clear()
