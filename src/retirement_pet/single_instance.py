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

Stale servers from crashed runs are removed safely.
"""

from __future__ import annotations

import getpass
import logging
import os
import re

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from retirement_pet import APP_ID

logger = logging.getLogger(__name__)

SHOW_COMMAND = "show"
QUIET_COMMAND = "quiet"
QUIT_COMMAND = "quit"
HIDE_COMMAND = "hide"
PANEL_COMMAND = "panel"  # panel | panel:<page-id>

#: environment override so verification harnesses can isolate the server
#: name from a really-running user instance.
INSTANCE_NAME_ENV = "RETIREMENT_PET_INSTANCE_NAME"


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
        if not server.listen(self._name):
            logger.error("cannot listen on %s: %s", self._name, server.errorString())
            # Pathological case (e.g. socket in a bad state): run anyway rather
            # than refusing to start the pet.
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
        # Data may already have arrived before the signal was connected.
        if connection.bytesAvailable() > 0:
            self._read(connection)

    def _read(self, connection: QLocalSocket) -> None:
        data = bytes(connection.readAll()).decode("ascii", "replace").strip()
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
        else:
            logger.debug("ignoring unknown instance command: %r", data[:32])
        connection.disconnectFromServer()

    # Subclass or monkeypatch these; the Qt layer also connects via signal.
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
