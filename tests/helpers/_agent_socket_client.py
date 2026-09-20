"""Scripted subprocess client for the V13-01 agent protocol tests.

Runs in a REAL separate OS process and speaks the actual protocol over a
real ``QLocalSocket`` (the same transport an agent CLI uses).  The parent
test drives one scripted scenario per invocation and reads the recorded
results from a JSON file.

Usage::

    python _agent_socket_client.py <server_name> <out_path> <scenario_file>

``<scenario_file>`` is a JSON file (avoids Windows command-length limits for
large scripted payloads).

Scenario shape: ``{"steps": [...]}``; steps execute in order and the
socket connects lazily on the first I/O step:

- ``{"connect": true}`` -> result ``{"type": "connect", "connected": bool}``
- ``{"write": "<base64>", "chunks": 1, "chunk_delay_s": 0.0}``
  -> result ``{"type": "write", "bytes": int}``
- ``{"read": true, "timeout_ms": 5000}`` -> one response line or a timeout:
  ``{"type": "read", "line": str|null, "timeout": bool}``
- ``{"read_available": true, "timeout_ms": 500}``
  -> whatever bytes arrived: ``{"type": "read_available", "data": str}``
- ``{"wait_disconnected": true, "timeout_ms": 5000}``
  -> ``{"type": "wait_disconnected", "disconnected": bool}``
- ``{"disconnect": true}`` -> ``{"type": "disconnect"}``
"""

from __future__ import annotations

import base64
import json
import sys
import time

from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalSocket


def _write_chunks(socket: QLocalSocket, payload: bytes, chunks: int,
                  delay_s: float) -> None:
    chunks = max(1, chunks)
    size = max(1, len(payload) // chunks)
    offset = 0
    while offset < len(payload):
        part = payload[offset:offset + size]
        socket.write(part)
        socket.flush()
        socket.waitForBytesWritten(5000)
        offset += len(part)
        if offset < len(payload) and delay_s > 0:
            time.sleep(delay_s)


def main() -> int:
    server_name, out_path, scenario_file = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(scenario_file, encoding="utf-8") as handle:
        scenario = json.load(handle)
    socket = QLocalSocket()
    results = []

    def ensure_connected(timeout_ms: int = 5000) -> None:
        if socket.state() == QLocalSocket.UnconnectedState:
            socket.connectToServer(server_name)
            socket.waitForConnected(timeout_ms)

    for step in scenario["steps"]:
        if "connect" in step:
            socket.connectToServer(server_name)
            connected = socket.waitForConnected(
                int(step.get("timeout_ms", 5000)))
            results.append({"type": "connect", "connected": bool(connected)})
            continue
        if "write" in step:
            ensure_connected()
            payload = base64.b64decode(step["write"])
            _write_chunks(socket, payload, int(step.get("chunks", 1)),
                          float(step.get("chunk_delay_s", 0.0)))
            results.append({"type": "write", "bytes": len(payload)})
            continue
        if step.get("read"):
            ensure_connected()
            buffer = bytearray()
            timed_out = False
            deadline = time.monotonic() + step.get("timeout_ms", 5000) / 1000
            while b"\n" not in buffer:
                if not socket.waitForReadyRead(
                        max(10, int((deadline - time.monotonic()) * 1000))):
                    if b"\n" in buffer:
                        break
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    continue
                buffer.extend(bytes(socket.readAll()))
                if len(buffer) > 4 * 1024 * 1024:
                    timed_out = True
                    break
            line = None
            if b"\n" in buffer:
                line = buffer.split(b"\n", 1)[0].decode("utf-8", "replace")
            results.append({"type": "read", "line": line,
                            "timeout": bool(timed_out)})
            continue
        if step.get("read_available"):
            buffer = bytearray()
            deadline = time.monotonic() + step.get("timeout_ms", 500) / 1000
            while time.monotonic() < deadline:
                if socket.waitForReadyRead(50):
                    buffer.extend(bytes(socket.readAll()))
                    if b"\n" in buffer:
                        break
            results.append({"type": "read_available",
                            "data": bytes(buffer).decode("utf-8", "replace")})
            continue
        if step.get("wait_disconnected"):
            deadline = time.monotonic() + step.get("timeout_ms", 5000) / 1000
            while time.monotonic() < deadline:
                if socket.state() == QLocalSocket.UnconnectedState:
                    break
                socket.waitForReadyRead(50)
            results.append({"type": "wait_disconnected",
                            "disconnected": socket.state()
                            == QLocalSocket.UnconnectedState})
            continue
        if step.get("disconnect"):
            socket.disconnectFromServer()
            results.append({"type": "disconnect"})
            continue
        results.append({"type": "unknown_step", "step": step})

    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump({"results": results}, handle, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
