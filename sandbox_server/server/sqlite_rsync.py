import asyncio
import hmac
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from sandbox_server.config import _agent_workspace
from sandbox_server.server.internal_paths import CUSTOM_TOOL_SQLITE_FILESPACE_PATH

SQLITE_RSYNC_WEBSOCKET_PATH = "/sandbox/compute/sqlite_rsync"
_CHUNK_BYTES = 64 * 1024
_SQLITE_RSYNC_LOCKS: dict[str, asyncio.Lock] = {}


def _header(scope: dict[str, Any], name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("utf-8", errors="replace").strip()
    return ""


def _authorized(scope: dict[str, Any]) -> bool:
    expected = os.environ.get("SANDBOX_COMPUTE_API_TOKEN", "").strip()
    provided = _header(scope, b"x-sandbox-compute-token")
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def _configured_agent_id() -> str:
    return os.environ.get("SANDBOX_AGENT_ID", "").strip()


def _sqlite_path(agent_id: str) -> Path:
    return _agent_workspace(_configured_agent_id() or agent_id) / CUSTOM_TOOL_SQLITE_FILESPACE_PATH.lstrip("/")


def _sqlite_rsync_lock(agent_id: str) -> asyncio.Lock:
    lock_key = str(_sqlite_path(agent_id))
    return _SQLITE_RSYNC_LOCKS.setdefault(lock_key, asyncio.Lock())


def _quick_check(path: Path) -> tuple[bool, str]:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            row = connection.execute("PRAGMA quick_check(1);").fetchone()
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as exc:
        return False, str(exc)
    message = str(row[0]) if row else "quick_check returned no result"
    return message.lower() == "ok", message


def _remove_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            Path(f"{path}{suffix}").unlink()
        except FileNotFoundError:
            pass


def _invalidate_replica(path: Path) -> None:
    _remove_sidecars(path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _ensure_replica(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        connection = sqlite3.connect(target)
        connection.close()


async def _send_json(send, payload: dict[str, Any]) -> None:
    await send({"type": "websocket.send", "text": json.dumps(payload)})


async def _receive_handshake(receive) -> tuple[str | None, str | None]:
    event = await receive()
    if event.get("type") != "websocket.receive" or not isinstance(event.get("text"), str):
        return None, None
    try:
        payload = json.loads(event["text"])
    except json.JSONDecodeError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    agent_id = payload.get("agent_id")
    mode = payload.get("mode")
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None, None
    if mode not in {"origin", "replica"}:
        return None, None
    agent_id = agent_id.strip()
    configured_agent_id = _configured_agent_id()
    if configured_agent_id and agent_id != configured_agent_id:
        return None, None
    return agent_id, mode


async def _relay_websocket_to_stdin(receive, process: asyncio.subprocess.Process) -> None:
    assert process.stdin is not None
    try:
        while True:
            event = await receive()
            event_type = event.get("type")
            if event_type == "websocket.disconnect":
                break
            if event_type != "websocket.receive":
                continue
            content = event.get("bytes")
            if isinstance(content, bytes):
                process.stdin.write(content)
                await process.stdin.drain()
                continue
            text = event.get("text")
            if isinstance(text, str):
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and payload.get("type") == "eof":
                    break
    finally:
        process.stdin.close()


async def _relay_stdout_to_websocket(send, process: asyncio.subprocess.Process) -> None:
    assert process.stdout is not None
    while True:
        chunk = await process.stdout.read(_CHUNK_BYTES)
        if not chunk:
            return
        await send({"type": "websocket.send", "bytes": chunk})


async def _read_stderr(process: asyncio.subprocess.Process) -> bytes:
    assert process.stderr is not None
    return await process.stderr.read()


async def _run_remote_half(agent_id: str, mode: str, receive, send) -> tuple[int, str]:
    target = _sqlite_path(agent_id)

    if mode == "replica":
        await asyncio.to_thread(_ensure_replica, target)
        arguments = ["--replica", "worker.db", str(target)]
    else:
        if not target.exists():
            return 1, "Sandbox agent SQLite database is missing."
        valid, message = await asyncio.to_thread(_quick_check, target)
        if not valid:
            await asyncio.to_thread(_invalidate_replica, target)
            return 1, f"Sandbox agent SQLite database is invalid: {message}"
        arguments = ["--origin", str(target), "worker.db"]

    binary = os.environ.get("SANDBOX_SQLITE_RSYNC_BINARY", "/usr/local/bin/sqlite3_rsync")
    timeout = max(1, int(os.environ.get("SANDBOX_SQLITE_RSYNC_TIMEOUT_SECONDS", "180")))
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        inbound = asyncio.create_task(_relay_websocket_to_stdin(receive, process))
        outbound = asyncio.create_task(_relay_stdout_to_websocket(send, process))
        stderr_task = asyncio.create_task(_read_stderr(process))
        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            exit_code = 124
        await outbound
        if not inbound.done():
            inbound.cancel()
        await asyncio.gather(inbound, return_exceptions=True)
        stderr = (await stderr_task).decode("utf-8", errors="replace").strip()

        if exit_code != 0 and mode == "replica":
            # The worker is authoritative. Invalidate a failed replica so the
            # next pull starts from an empty, known-good seed.
            await asyncio.to_thread(_invalidate_replica, target)
        return exit_code, stderr
    except OSError as exc:
        if mode == "replica":
            await asyncio.to_thread(_invalidate_replica, target)
        return 1, f"Failed to start sqlite3_rsync: {exc}"


async def websocket_application(scope, receive, send) -> None:
    connect_event = await receive()
    if connect_event.get("type") != "websocket.connect":
        await send({"type": "websocket.close", "code": 4400})
        return
    if not _authorized(scope):
        await send({"type": "websocket.close", "code": 4401})
        return

    await send({"type": "websocket.accept"})
    agent_id, mode = await _receive_handshake(receive)
    if agent_id is None or mode is None:
        await _send_json(send, {"status": "error", "message": "Invalid sqlite3_rsync handshake."})
        await send({"type": "websocket.close", "code": 4400})
        return

    async with _sqlite_rsync_lock(agent_id):
        await _send_json(send, {"status": "ready"})
        exit_code, message = await _run_remote_half(agent_id, mode, receive, send)
        try:
            await _send_json(
                send,
                {
                    "type": "complete",
                    "exit_code": exit_code,
                    "message": message[-4096:],
                },
            )
        except RuntimeError:
            return
    try:
        await send({"type": "websocket.close", "code": 1000})
    except RuntimeError:
        pass
