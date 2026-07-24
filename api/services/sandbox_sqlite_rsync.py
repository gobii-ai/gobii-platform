import json
import os
import signal
import subprocess
import tempfile
import time
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings


@lru_cache(maxsize=4)
def _sqlite_version(rsync_binary: str) -> str:
    sqlite_binary = os.path.join(os.path.dirname(rsync_binary), "sqlite3")
    try:
        result = subprocess.run(
            [sqlite_binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    output = (result.stdout or result.stderr or "").strip()
    return output.splitlines()[0][:256] if output else "unknown"


def sqlite_rsync_websocket_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme == "https":
        scheme = "wss"
    elif parsed.scheme == "http":
        scheme = "ws"
    else:
        raise ValueError("Sandbox compute URL must use http or https.")
    base_path = parsed.path.rstrip("/")
    path = f"{base_path}/sandbox/compute/sqlite_rsync"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def run_sqlite_rsync(
    *,
    websocket_url: str,
    token: str,
    agent_id: str,
    local_db_path: str,
    direction: str,
) -> dict[str, Any]:
    if direction not in {"worker_to_sandbox", "sandbox_to_worker"}:
        return {"status": "error", "message": "Invalid SQLite rsync direction."}

    binary = settings.SANDBOX_SQLITE_RSYNC_BINARY
    sqlite_version = _sqlite_version(binary)
    transport = settings.SANDBOX_SQLITE_RSYNC_TRANSPORT
    timeout = max(1, int(settings.SANDBOX_SQLITE_RSYNC_TIMEOUT_SECONDS))
    remote = "gobii-sandbox:agent-state.sqlite3"
    remote_mode = "replica" if direction == "worker_to_sandbox" else "origin"

    if direction == "worker_to_sandbox":
        origin = local_db_path
        replica = remote
    else:
        origin = remote
        replica = local_db_path

    metrics_descriptor, metrics_path = tempfile.mkstemp(prefix="gobii-sqlite-rsync-", suffix=".json")
    os.close(metrics_descriptor)
    env = os.environ.copy()
    env.update(
        {
            "GOBII_SQLITE_RSYNC_WEBSOCKET_URL": websocket_url,
            "GOBII_SQLITE_RSYNC_API_TOKEN": token,
            "GOBII_SQLITE_RSYNC_AGENT_ID": str(agent_id),
            "GOBII_SQLITE_RSYNC_REMOTE_MODE": remote_mode,
            "GOBII_SQLITE_RSYNC_METRICS_PATH": metrics_path,
        }
    )
    command = [
        binary,
        origin,
        replica,
        "--ssh",
        transport,
        "--exe",
        "sqlite3_rsync",
    ]
    started_at = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            return {
                "status": "error",
                "message": "SQLite rsync timed out.",
                "duration_ms": int((time.monotonic() - started_at) * 1000),
                "sqlite_version": sqlite_version,
            }
    except OSError as exc:
        return {
            "status": "error",
            "message": f"SQLite rsync failed to start: {exc}",
            "duration_ms": int((time.monotonic() - started_at) * 1000),
            "sqlite_version": sqlite_version,
        }
    finally:
        metrics: dict[str, Any] = {}
        try:
            with open(metrics_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
                if isinstance(loaded, dict):
                    metrics = loaded
        except (OSError, ValueError):
            pass
        try:
            os.remove(metrics_path)
        except FileNotFoundError:
            pass

    duration_ms = int((time.monotonic() - started_at) * 1000)
    if process.returncode != 0:
        message = (stderr or stdout or "SQLite rsync failed.").strip()
        return {
            "status": "error",
            "message": message[-4096:],
            "duration_ms": duration_ms,
            "sqlite_version": sqlite_version,
            **metrics,
        }
    return {
        "status": "ok",
        "duration_ms": duration_ms,
        "sqlite_version": sqlite_version,
        **metrics,
    }
