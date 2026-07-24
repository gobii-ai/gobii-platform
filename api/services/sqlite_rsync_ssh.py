#!/usr/bin/env python3
"""Restricted sqlite3_rsync SSH replacement backed by the sandbox WebSocket."""

import json
import os
import sys
import threading
from pathlib import Path

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

_CHUNK_BYTES = 64 * 1024


def _remote_mode(argv: list[str]) -> str:
    modes = [arg for arg in argv if arg in {"--origin", "--replica"}]
    if len(modes) != 1:
        raise ValueError("sqlite3_rsync transport requires exactly one remote mode")
    return modes[0][2:]


def _write_metrics(bytes_sent: int, bytes_received: int) -> None:
    path = os.environ.get("GOBII_SQLITE_RSYNC_METRICS_PATH", "").strip()
    if not path:
        return
    payload = {
        "bytes_sent": bytes_sent,
        "bytes_received": bytes_received,
        "wire_bytes": bytes_sent + bytes_received,
    }
    try:
        Path(path).write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    try:
        mode = _remote_mode(sys.argv[1:])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    expected_mode = os.environ.get("GOBII_SQLITE_RSYNC_REMOTE_MODE", "").strip()
    if expected_mode and mode != expected_mode:
        print("sqlite3_rsync transport mode mismatch", file=sys.stderr)
        return 2

    url = os.environ.get("GOBII_SQLITE_RSYNC_WEBSOCKET_URL", "").strip()
    token = os.environ.get("GOBII_SQLITE_RSYNC_API_TOKEN", "").strip()
    agent_id = os.environ.get("GOBII_SQLITE_RSYNC_AGENT_ID", "").strip()
    if not url or not token or not agent_id:
        print("sqlite3_rsync transport environment is incomplete", file=sys.stderr)
        return 2

    bytes_sent = 0
    bytes_received = 0
    sender_error: list[str] = []

    try:
        with connect(
            url,
            additional_headers={"X-Sandbox-Compute-Token": token},
            max_size=None,
            proxy=None,
        ) as websocket:
            websocket.send(json.dumps({"agent_id": agent_id, "mode": mode}))
            ready_raw = websocket.recv()
            if not isinstance(ready_raw, str):
                raise TypeError("sandbox sqlite3_rsync handshake was not text")
            ready = json.loads(ready_raw)
            if not isinstance(ready, dict) or ready.get("status") != "ready":
                message = ready.get("message") if isinstance(ready, dict) else None
                raise ValueError(str(message or "sandbox sqlite3_rsync handshake failed"))

            def _send_stdin() -> None:
                nonlocal bytes_sent
                try:
                    while True:
                        chunk = os.read(sys.stdin.fileno(), _CHUNK_BYTES)
                        if not chunk:
                            break
                        websocket.send(chunk)
                        bytes_sent += len(chunk)
                    websocket.send(json.dumps({"type": "eof"}))
                except (OSError, ConnectionClosed, WebSocketException) as exc:
                    sender_error.append(str(exc))

            sender = threading.Thread(target=_send_stdin, name="sqlite-rsync-stdin", daemon=True)
            sender.start()

            remote_exit_code = 1
            remote_message = ""
            while True:
                message = websocket.recv()
                if isinstance(message, bytes):
                    sys.stdout.buffer.write(message)
                    sys.stdout.buffer.flush()
                    bytes_received += len(message)
                    continue
                payload = json.loads(message)
                if isinstance(payload, dict) and payload.get("type") == "complete":
                    remote_exit_code = int(payload.get("exit_code") or 0)
                    remote_message = str(payload.get("message") or "")
                    break

            # sqlite3_rsync keeps the transport's stdin open until this helper
            # exits, so waiting for EOF here would add a circular delay.
            sender.join(timeout=0.05)
            if sender_error and remote_exit_code == 0:
                print(sender_error[-1], file=sys.stderr)
                remote_exit_code = 1
            if remote_message and remote_exit_code != 0:
                print(remote_message, file=sys.stderr)
            _write_metrics(bytes_sent, bytes_received)
            return remote_exit_code
    except (OSError, TypeError, ValueError, json.JSONDecodeError, ConnectionClosed, WebSocketException) as exc:
        print(f"sqlite3_rsync transport failed: {exc}", file=sys.stderr)
        _write_metrics(bytes_sent, bytes_received)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
