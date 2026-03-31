import base64
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Tuple
from urllib.parse import urlsplit

from sandbox_server.config import _TRACEPARENT_HEADER, _TRACEPARENT_PARTS, _TRACE_ID_HEX_LEN, _workspace_max_bytes


def _parse_since(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _parse_entry_updated_at(entry: Dict[str, Any]) -> Optional[float]:
    raw = entry.get("updated_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def _normalize_checksum(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    checksum = value.strip().lower()
    if len(checksum) != 64:
        return None
    if any(ch not in "0123456789abcdef" for ch in checksum):
        return None
    return checksum


def _checksum_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_file_checksum(path: Path) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            digest = hashlib.sha256()
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            return digest.hexdigest()
    except OSError:
        return None


def _resolve_local_checksum(path: Path, cache_entry: Any) -> Optional[str]:
    try:
        stat = path.stat()
    except OSError:
        return None

    if isinstance(cache_entry, dict):
        cached_checksum = _normalize_checksum(cache_entry.get("checksum_sha256"))
        cached_mtime = cache_entry.get("mtime")
        cached_size = cache_entry.get("size")
        if (
            cached_checksum
            and isinstance(cached_mtime, (int, float))
            and isinstance(cached_size, int)
            and stat.st_size == cached_size
            and abs(stat.st_mtime - float(cached_mtime)) <= 1e-6
        ):
            return cached_checksum

    return _read_file_checksum(path)


def _normalize_workspace_path(agent_root: Path, path: str) -> Tuple[Optional[Path], Optional[str]]:
    if not isinstance(path, str):
        return None, None
    cleaned = path.strip()
    if not cleaned:
        return None, None
    rel = cleaned.lstrip("/")
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        return None, None
    full_path = (agent_root / rel_path).resolve()
    if agent_root not in full_path.parents and full_path != agent_root:
        return None, None
    return full_path, "/" + rel_path.as_posix()


def _workspace_size_bytes(agent_root: Path) -> int:
    total = 0
    for path in _iter_workspace_files(agent_root):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _ensure_capacity(
    agent_root: Path,
    new_bytes: int,
    *,
    existing_bytes: int = 0,
    current_bytes: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    max_bytes = _workspace_max_bytes()
    if max_bytes <= 0:
        return None
    current = _workspace_size_bytes(agent_root) if current_bytes is None else max(current_bytes, 0)
    adjusted = max(current - max(existing_bytes, 0), 0)
    if adjusted + new_bytes > max_bytes:
        return {
            "status": "error",
            "message": "Workspace size limit exceeded.",
            "limit_bytes": max_bytes,
            "current_bytes": current,
            "attempted_bytes": new_bytes,
            "existing_bytes": existing_bytes,
        }
    return None


def _iter_workspace_files(agent_root: Path) -> Iterable[Path]:
    for root, dirs, files in os.walk(agent_root):
        root_path = Path(root)
        if ".gobii" in root_path.parts:
            continue
        for name in files:
            path = root_path / name
            if ".gobii" in path.parts:
                continue
            yield path


def _decode_content(change: Dict[str, Any]) -> Optional[bytes]:
    if "content_b64" in change:
        raw = change.get("content_b64")
        if isinstance(raw, str):
            try:
                return base64.b64decode(raw.encode("utf-8"))
            except (ValueError, OSError):
                return None
    content = change.get("content")
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    return None


def _json_response(start_response: Callable, status: str, payload: Dict[str, Any]) -> list[bytes]:
    body = json.dumps(payload).encode("utf-8")
    headers = [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ]
    start_response(status, headers)
    return [body]


def _extract_traceparent(environ: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    raw = environ.get(_TRACEPARENT_HEADER)
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    traceparent = raw.strip()
    parts = traceparent.split("-")
    if len(parts) < _TRACEPARENT_PARTS:
        return traceparent, None
    trace_id = parts[1].lower()
    if len(trace_id) != _TRACE_ID_HEX_LEN or any(ch not in "0123456789abcdef" for ch in trace_id):
        return traceparent, None
    if trace_id == "0" * _TRACE_ID_HEX_LEN:
        return traceparent, None
    return traceparent, trace_id


def _trace_context(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    trace_id = payload.get("_trace_id")
    traceparent = payload.get("_traceparent")
    if not isinstance(trace_id, str):
        trace_id = None
    if not isinstance(traceparent, str):
        traceparent = None
    return trace_id, traceparent


def _elapsed_ms(started_at: float) -> int:
    return int(round((time.monotonic() - started_at) * 1000))


def _safe_url_for_log(url: str) -> str:
    if not isinstance(url, str):
        return "<invalid-url>"
    trimmed = url.strip()
    if not trimmed:
        return "<empty-url>"
    parsed = urlsplit(trimmed)
    if parsed.scheme and parsed.netloc:
        path = parsed.path or "/"
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    return trimmed.split("?", 1)[0].split("#", 1)[0]


def _get_bearer_token(environ: Dict[str, Any]) -> Optional[str]:
    auth = environ.get("HTTP_AUTHORIZATION", "")
    if not auth:
        return None
    parts = auth.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def _get_supervisor_token(environ: Dict[str, Any]) -> Optional[str]:
    bearer = _get_bearer_token(environ)
    if bearer:
        return bearer
    header_token = environ.get("HTTP_X_SANDBOX_COMPUTE_TOKEN", "")
    if not isinstance(header_token, str):
        return None
    trimmed = header_token.strip()
    return trimmed or None


def _require_auth(environ: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    token = os.environ.get("SANDBOX_COMPUTE_API_TOKEN", "").strip()
    if not token:
        return {"status": "error", "message": "Sandbox compute API token is not configured."}
    provided = _get_supervisor_token(environ)
    if provided != token:
        return {"status": "error", "message": "Unauthorized."}
    return None


def _parse_json(environ: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    length_raw = environ.get("CONTENT_LENGTH") or "0"
    try:
        length = int(length_raw)
    except ValueError:
        length = 0
    body = environ["wsgi.input"].read(length) if length > 0 else b""
    if not body:
        return {}, None
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, {"status": "error", "message": "Invalid JSON body."}
    if not isinstance(data, dict):
        return None, {"status": "error", "message": "JSON body must be an object."}
    return data, None


def _require_agent_id(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None, {"status": "error", "message": "Missing required parameter: agent_id"}
    return agent_id.strip(), None


def _session_update(state: str) -> Dict[str, Any]:
    return {"status": "ok", "state": state}
