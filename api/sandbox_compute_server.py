import base64
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

import requests

from api.sandbox_utils import normalize_timeout as _normalize_timeout

logger = logging.getLogger(__name__)

_DEFAULT_ALLOWED_ENV_KEYS = {
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TERM",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "PYTHONUNBUFFERED",
    "PYTHONIOENCODING",
}

_PROXY_ENV_KEYS = {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}


def _allowed_env_keys() -> set[str]:
    raw = os.environ.get("SANDBOX_COMPUTE_ALLOWED_ENV_KEYS", "")
    if not raw.strip():
        return set(_DEFAULT_ALLOWED_ENV_KEYS)
    parts = [part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()]
    return set(parts) or set(_DEFAULT_ALLOWED_ENV_KEYS)


def _sandbox_env(agent_root: Optional[Path] = None, extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    allowed = _allowed_env_keys()
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if extra_env:
        for key, value in extra_env.items():
            if key in allowed or key.startswith("SANDBOX_"):
                env[key] = str(value)
    if agent_root:
        env.update(_proxy_env_from_manifest(agent_root))
    return env


def _workspace_root() -> Path:
    root = os.environ.get("SANDBOX_WORKSPACE_ROOT", "/workspace").strip() or "/workspace"
    return Path(root)


def _workspace_max_bytes() -> int:
    raw = os.environ.get("SANDBOX_WORKSPACE_MAX_BYTES") or os.environ.get("SANDBOX_COMPUTE_WORKSPACE_LIMIT_BYTES")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 1024 * 1024 * 1024


def _stdio_max_bytes() -> int:
    raw = os.environ.get("SANDBOX_COMPUTE_STDIO_MAX_BYTES")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 1024 * 1024


def _run_command_timeout_seconds() -> int:
    raw = os.environ.get("SANDBOX_COMPUTE_RUN_COMMAND_TIMEOUT_SECONDS")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 120


def _python_default_timeout_seconds() -> int:
    raw = os.environ.get("SANDBOX_COMPUTE_PYTHON_DEFAULT_TIMEOUT_SECONDS")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 30


def _python_max_timeout_seconds() -> int:
    raw = os.environ.get("SANDBOX_COMPUTE_PYTHON_MAX_TIMEOUT_SECONDS")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 120


def _agent_workspace(agent_id: str) -> Path:
    root = _workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    agent_root = root / agent_id
    agent_root.mkdir(parents=True, exist_ok=True)
    return agent_root


def _manifest_path(agent_root: Path) -> Path:
    meta_dir = agent_root / ".gobii"
    meta_dir.mkdir(parents=True, exist_ok=True)
    return meta_dir / "manifest.json"


def _load_manifest(agent_root: Path) -> Dict[str, Any]:
    path = _manifest_path(agent_root)
    if not path.exists():
        return {"files": {}, "deleted": {}}
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        return {"files": {}, "deleted": {}}
    if not isinstance(data, dict):
        return {"files": {}, "deleted": {}}
    data.setdefault("files", {})
    data.setdefault("deleted", {})
    if not isinstance(data["files"], dict):
        data["files"] = {}
    if not isinstance(data["deleted"], dict):
        data["deleted"] = {}
    return data


def _save_manifest(agent_root: Path, manifest: Dict[str, Any]) -> None:
    path = _manifest_path(agent_root)
    path.write_text(json.dumps(manifest, sort_keys=True))


def _proxy_env_from_manifest(agent_root: Path) -> Dict[str, str]:
    manifest = _load_manifest(agent_root)
    env = manifest.get("env", {})
    if not isinstance(env, dict):
        return {}
    filtered: Dict[str, str] = {}
    for key in _PROXY_ENV_KEYS:
        value = env.get(key)
        if isinstance(value, str) and value.strip():
            filtered[key] = value.strip()
    return filtered


def _store_proxy_env(agent_root: Path, payload: Dict[str, Any]) -> None:
    proxy_env = payload.get("proxy_env")
    if not isinstance(proxy_env, dict):
        return
    filtered: Dict[str, str] = {}
    for key in _PROXY_ENV_KEYS:
        value = proxy_env.get(key)
        if isinstance(value, str) and value.strip():
            filtered[key] = value.strip()
    if not filtered:
        return
    manifest = _load_manifest(agent_root)
    manifest["env"] = {**manifest.get("env", {}), **filtered}
    _save_manifest(agent_root, manifest)


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


def _ensure_capacity(agent_root: Path, new_bytes: int, *, existing_bytes: int = 0) -> Optional[Dict[str, Any]]:
    max_bytes = _workspace_max_bytes()
    if max_bytes <= 0:
        return None
    current = _workspace_size_bytes(agent_root)
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


def _truncate_streams(stdout: str, stderr: str) -> Tuple[str, str]:
    max_bytes = _stdio_max_bytes()
    stdout_bytes = stdout.encode("utf-8")
    stderr_bytes = stderr.encode("utf-8")
    total = len(stdout_bytes) + len(stderr_bytes)
    if total <= max_bytes:
        return stdout, stderr
    remaining = max_bytes
    truncated_stdout = stdout_bytes[:remaining]
    remaining -= len(truncated_stdout)
    truncated_stderr = stderr_bytes[:remaining]
    return (
        truncated_stdout.decode("utf-8", errors="ignore"),
        truncated_stderr.decode("utf-8", errors="ignore"),
    )


def _get_bearer_token(environ: Dict[str, Any]) -> Optional[str]:
    auth = environ.get("HTTP_AUTHORIZATION", "")
    if not auth:
        return None
    parts = auth.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def _require_auth(environ: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    token = os.environ.get("SANDBOX_COMPUTE_API_TOKEN", "")
    if not token:
        return None
    provided = _get_bearer_token(environ)
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


def _handle_deploy_or_resume(payload: Dict[str, Any]) -> Dict[str, Any]:
    agent_id, error = _require_agent_id(payload)
    if error:
        return error
    agent_root = _agent_workspace(agent_id)
    _store_proxy_env(agent_root, payload)
    return _session_update("running")


def _handle_run_command(payload: Dict[str, Any]) -> Dict[str, Any]:
    agent_id, error = _require_agent_id(payload)
    if error:
        return error
    agent_root = _agent_workspace(agent_id)
    _store_proxy_env(agent_root, payload)
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        return {"status": "error", "message": "Missing required parameter: command"}
    if payload.get("interactive") is True:
        return {"status": "error", "message": "Interactive sessions are not supported yet."}
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        cwd_path, _ = _normalize_workspace_path(agent_root, cwd)
        if cwd_path is None:
            return {"status": "error", "message": "Invalid cwd path."}
        cwd = str(cwd_path)
    else:
        cwd = None
    env = payload.get("env") if isinstance(payload.get("env"), dict) else None
    timeout = _normalize_timeout(
        payload.get("timeout"),
        default=_run_command_timeout_seconds(),
    )

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd or None,
            env=_sandbox_env(agent_root, env),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Command timed out."}
    except OSError as exc:
        return {"status": "error", "message": f"Command failed to start: {exc}"}

    stdout, stderr = _truncate_streams(result.stdout or "", result.stderr or "")
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "exit_code": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _handle_python_exec(payload: Dict[str, Any]) -> Dict[str, Any]:
    agent_id, error = _require_agent_id(payload)
    if error:
        return error
    agent_root = _agent_workspace(agent_id)
    _store_proxy_env(agent_root, payload)
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"status": "error", "message": "Missing required parameter: code"}
    timeout = _normalize_timeout(
        payload.get("timeout_seconds"),
        default=_python_default_timeout_seconds(),
        maximum=_python_max_timeout_seconds(),
    )

    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=_sandbox_env(agent_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Python execution timed out."}
    except OSError as exc:
        return {"status": "error", "message": f"Python execution failed to start: {exc}"}

    stdout, stderr = _truncate_streams(result.stdout or "", result.stderr or "")
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "exit_code": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _write_file(agent_root: Path, rel_path: str, content: bytes, overwrite: bool) -> Optional[Dict[str, Any]]:
    full_path, normalized = _normalize_workspace_path(agent_root, rel_path)
    if full_path is None:
        return {"status": "error", "message": "Invalid file_path."}
    if full_path.exists() and not overwrite:
        return {"status": "error", "message": "File already exists. Use overwrite=true to replace it."}

    existing_bytes = 0
    if full_path.exists():
        try:
            existing_bytes = full_path.stat().st_size
        except OSError:
            existing_bytes = 0

    capacity_error = _ensure_capacity(agent_root, len(content), existing_bytes=existing_bytes)
    if capacity_error:
        return capacity_error

    full_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(full_path, "wb") as handle:
            handle.write(content)
    except OSError as exc:
        return {"status": "error", "message": f"Failed to write file: {exc}"}
    return {"status": "ok", "export_path": normalized}


def _handle_create_file(agent_root: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return {"status": "error", "message": "Missing required parameter: content"}
    mime_type = payload.get("mime_type")
    if not isinstance(mime_type, str) or not mime_type.strip():
        return {"status": "error", "message": "Missing required parameter: mime_type"}
    file_path = payload.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return {"status": "error", "message": "Missing required parameter: file_path"}
    file_path = file_path.strip()
    overwrite = payload.get("overwrite") is True
    if not Path(file_path).suffix:
        base_type = mime_type.split(";", 1)[0].strip().lower()
        guessed = mimetypes.guess_extension(base_type or "") or ""
        if guessed:
            file_path = f"{file_path}{guessed}"
    response = _write_file(agent_root, file_path, content.encode("utf-8"), overwrite)
    if response.get("status") != "ok":
        return response
    response["mime_type"] = mime_type
    return response


def _handle_create_csv(agent_root: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    csv_text = payload.get("csv_text")
    if not isinstance(csv_text, str) or not csv_text.strip():
        return {"status": "error", "message": "Missing required parameter: csv_text"}
    file_path = payload.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return {"status": "error", "message": "Missing required parameter: file_path"}
    file_path = file_path.strip()
    if not Path(file_path).suffix:
        file_path = f"{file_path}.csv"
    overwrite = payload.get("overwrite") is True
    response = _write_file(agent_root, file_path, csv_text.encode("utf-8"), overwrite)
    if response.get("status") != "ok":
        return response
    response["mime_type"] = "text/csv"
    return response


def _blocked_html_assets(html: str) -> bool:
    lowered = html.lower()
    return "http://" in lowered or "https://" in lowered or "file://" in lowered


_TOKEN_PATTERN = re.compile(r"\$\[([^\]]+)\]")


def _substitute_workspace_tokens(html: str, agent_root: Path) -> str:
    def _replace(match: re.Match) -> str:
        token = match.group(1)
        full_path, _normalized = _normalize_workspace_path(agent_root, token)
        if full_path is None or not full_path.exists():
            return match.group(0)
        try:
            content = full_path.read_bytes()
        except OSError:
            return match.group(0)
        mime_type = mimetypes.guess_type(full_path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(content).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    return _TOKEN_PATTERN.sub(_replace, html)


def _handle_create_pdf(agent_root: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    html = payload.get("html")
    if not isinstance(html, str) or not html.strip():
        return {"status": "error", "message": "Missing required parameter: html"}
    file_path = payload.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return {"status": "error", "message": "Missing required parameter: file_path"}
    file_path = file_path.strip()
    if not Path(file_path).suffix:
        file_path = f"{file_path}.pdf"
    overwrite = payload.get("overwrite") is True

    html = _substitute_workspace_tokens(html, agent_root)

    if _blocked_html_assets(html):
        return {
            "status": "error",
            "message": "HTML contains blocked asset references (http/https/file).",
        }

    try:
        from weasyprint import HTML
    except Exception as exc:
        return {"status": "error", "message": f"PDF renderer unavailable: {exc}"}

    try:
        pdf_bytes = HTML(string=html, base_url=str(agent_root)).write_pdf()
    except Exception as exc:
        return {"status": "error", "message": f"Failed to render PDF: {exc}"}

    response = _write_file(agent_root, file_path, pdf_bytes, overwrite)
    if response.get("status") != "ok":
        return response
    response["mime_type"] = "application/pdf"
    return response


def _handle_tool_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    agent_id, error = _require_agent_id(payload)
    if error:
        return error
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return {"status": "error", "message": "Missing required parameter: tool_name"}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    agent_root = _agent_workspace(agent_id)
    _store_proxy_env(agent_root, payload)

    if tool_name == "python_exec":
        params = dict(params)
        params["agent_id"] = agent_id
        return _handle_python_exec(params)
    if tool_name == "create_file":
        return _handle_create_file(agent_root, params)
    if tool_name == "create_csv":
        return _handle_create_csv(agent_root, params)
    if tool_name == "create_pdf":
        return _handle_create_pdf(agent_root, params)

    return {
        "status": "error",
        "error_code": "sandbox_unsupported_tool",
        "message": f"Sandbox tool '{tool_name}' is not supported yet.",
    }


def _handle_sync_filespace(payload: Dict[str, Any]) -> Dict[str, Any]:
    agent_id, error = _require_agent_id(payload)
    if error:
        return error
    direction = payload.get("direction")
    if not isinstance(direction, str) or not direction.strip():
        return {"status": "error", "message": "Missing required parameter: direction"}
    direction = direction.strip().lower()
    agent_root = _agent_workspace(agent_id)
    _store_proxy_env(agent_root, payload)
    manifest = _load_manifest(agent_root)

    if direction == "push":
        since = _parse_since(payload.get("since"))
        changes: list[Dict[str, Any]] = []
        seen_paths: set[str] = set()
        for path in _iter_workspace_files(agent_root):
            rel = "/" + path.relative_to(agent_root).as_posix()
            seen_paths.add(rel)
            try:
                stat = path.stat()
            except OSError:
                continue
            mtime = stat.st_mtime
            if since is not None and mtime <= since:
                continue
            try:
                content = path.read_bytes()
            except OSError:
                continue
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            changes.append(
                {
                    "path": rel,
                    "content_b64": base64.b64encode(content).decode("utf-8"),
                    "mime_type": mime_type,
                }
            )
            manifest["files"][rel] = {"mtime": mtime, "size": stat.st_size}
            manifest["deleted"].pop(rel, None)

        deleted = manifest.get("deleted", {})
        tracked = set(manifest.get("files", {}).keys())
        for rel in sorted(tracked - seen_paths):
            deleted_at = time.time()
            if since is not None and deleted_at <= since:
                continue
            changes.append({"path": rel, "is_deleted": True})
            deleted[rel] = {"deleted_at": deleted_at}
            manifest["files"].pop(rel, None)
        manifest["deleted"] = deleted
        _save_manifest(agent_root, manifest)
        return {
            "status": "ok",
            "changes": changes,
            "sync_timestamp": datetime.now(timezone.utc).isoformat(),
        }

    if direction == "pull":
        entries = payload.get("files") or payload.get("changes") or []
        if not isinstance(entries, list):
            return {"status": "error", "message": "files must be a list."}
        skipped = 0
        conflicts = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            full_path, normalized = _normalize_workspace_path(agent_root, path)
            if full_path is None or not normalized:
                continue
            entry_updated_at = _parse_entry_updated_at(entry)
            is_deleted = bool(entry.get("is_deleted"))
            if is_deleted:
                if entry_updated_at is not None and full_path.exists():
                    try:
                        local_mtime = full_path.stat().st_mtime
                    except OSError:
                        local_mtime = None
                    if local_mtime is not None and local_mtime > entry_updated_at:
                        conflicts += 1
                        skipped += 1
                        continue
                try:
                    if full_path.exists():
                        full_path.unlink()
                except OSError:
                    continue
                manifest["files"].pop(normalized, None)
                deleted_at = entry_updated_at if entry_updated_at is not None else time.time()
                manifest.setdefault("deleted", {})[normalized] = {"deleted_at": deleted_at}
                continue

            content = _decode_content(entry)
            if content is None and entry.get("download_url"):
                try:
                    content = _download_file(
                        entry["download_url"],
                        entry.get("size_bytes"),
                        _proxy_env_from_manifest(agent_root),
                    )
                except RuntimeError as exc:
                    return {"status": "error", "message": str(exc)}
            if content is None:
                return {"status": "error", "message": f"Missing content for {normalized}"}

            existing_bytes = 0
            if full_path.exists():
                try:
                    existing_bytes = full_path.stat().st_size
                except OSError:
                    existing_bytes = 0
            if entry_updated_at is not None and full_path.exists():
                try:
                    local_mtime = full_path.stat().st_mtime
                except OSError:
                    local_mtime = None
                if local_mtime is not None and local_mtime > entry_updated_at:
                    conflicts += 1
                    skipped += 1
                    continue

            capacity_error = _ensure_capacity(agent_root, len(content), existing_bytes=existing_bytes)
            if capacity_error:
                return capacity_error

            full_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(full_path, "wb") as handle:
                    handle.write(content)
            except OSError as exc:
                return {"status": "error", "message": f"Failed to write {normalized}: {exc}"}
            manifest["files"][normalized] = {
                "mtime": full_path.stat().st_mtime,
                "size": full_path.stat().st_size,
                "updated_at": entry.get("updated_at"),
            }
            manifest["deleted"].pop(normalized, None)

        _save_manifest(agent_root, manifest)
        return {
            "status": "ok",
            "applied": len(entries) - skipped,
            "skipped": skipped,
            "conflicts": conflicts,
        }

    return {"status": "error", "message": "Invalid sync direction."}


def _download_file(url: str, expected_size: Optional[int], proxy_env: Optional[Dict[str, str]]) -> bytes:
    max_bytes = _workspace_max_bytes()
    if expected_size and max_bytes > 0 and expected_size > max_bytes:
        raise RuntimeError("Download exceeds workspace size limit.")
    proxies = None
    if proxy_env:
        http_proxy = proxy_env.get("HTTP_PROXY")
        https_proxy = proxy_env.get("HTTPS_PROXY") or http_proxy
        if http_proxy or https_proxy:
            proxies = {
                "http": http_proxy or "",
                "https": https_proxy or "",
            }
    try:
        with requests.get(url, stream=True, timeout=30, proxies=proxies) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                data.extend(chunk)
                if max_bytes > 0 and len(data) > max_bytes:
                    raise RuntimeError("Downloaded file exceeds workspace size limit.")
            return bytes(data)
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to download file: {exc}") from exc


def _handle_terminate(payload: Dict[str, Any]) -> Dict[str, Any]:
    agent_id, error = _require_agent_id(payload)
    if error:
        return error
    agent_root = _agent_workspace(agent_id)
    _store_proxy_env(agent_root, payload)
    return _session_update("stopped")


def _handle_mcp_request(_payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "error",
        "error_code": "sandbox_unsupported_mcp",
        "message": "Sandbox MCP supervisor is not available yet.",
    }


def _handle_discover_mcp_tools(_payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "error",
        "error_code": "sandbox_unsupported_mcp",
        "message": "Sandbox MCP tool discovery is not available yet.",
    }


_ROUTES: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "/sandbox/compute/deploy_or_resume": _handle_deploy_or_resume,
    "/sandbox/compute/run_command": _handle_run_command,
    "/sandbox/compute/tool_request": _handle_tool_request,
    "/sandbox/compute/mcp_request": _handle_mcp_request,
    "/sandbox/compute/sync_filespace": _handle_sync_filespace,
    "/sandbox/compute/terminate": _handle_terminate,
    "/sandbox/compute/discover_mcp_tools": _handle_discover_mcp_tools,
}


def application(environ: Dict[str, Any], start_response: Callable) -> list[bytes]:
    path = environ.get("PATH_INFO", "") or ""
    method = environ.get("REQUEST_METHOD", "GET").upper()

    if path.rstrip("/") == "/healthz":
        return _json_response(start_response, "200 OK", {"status": "ok"})

    if method != "POST":
        return _json_response(start_response, "405 Method Not Allowed", {"status": "error", "message": "POST only."})

    auth_error = _require_auth(environ)
    if auth_error:
        return _json_response(start_response, "401 Unauthorized", auth_error)

    payload, parse_error = _parse_json(environ)
    if parse_error:
        return _json_response(start_response, "400 Bad Request", parse_error)
    if payload is None:
        return _json_response(start_response, "400 Bad Request", {"status": "error", "message": "Invalid request."})

    handler = _ROUTES.get(path.rstrip("/"))
    if not handler:
        return _json_response(start_response, "404 Not Found", {"status": "error", "message": "Unknown endpoint."})

    try:
        result = handler(payload)
    except Exception:
        logger.exception("Sandbox compute request failed for %s", path)
        return _json_response(
            start_response,
            "200 OK",
            {"status": "error", "message": "Sandbox compute request failed."},
        )

    return _json_response(start_response, "200 OK", result)
