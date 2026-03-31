import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple

from fastmcp import Client
from fastmcp.client.transports import StdioTransport as FastMCPStdioTransport
from fastmcp.client.transports import StreamableHttpTransport
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from sandbox_server.config import (
    _PROXY_ENV_KEYS,
    _agent_workspace,
    _mcp_timeout_seconds,
    _runtime_cache_paths,
    _workspace_root,
)
from sandbox_server.manifest import _proxy_env_from_manifest, _store_proxy_env
from sandbox_server.workspace import _elapsed_ms, _require_agent_id, _trace_context

logger = logging.getLogger(__name__)


class GobiiStdioTransport(FastMCPStdioTransport):
    """Custom stdio transport that guarantees an errlog with a real fileno."""

    def __init__(
        self,
        command: str,
        args: list[str],
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        keep_alive: Optional[bool] = None,
    ):
        super().__init__(command=command, args=args, env=env, cwd=cwd, keep_alive=keep_alive)
        self._errlog_fallback = None

    def _resolve_errlog(self):
        for candidate in (getattr(sys, "__stderr__", None), sys.stderr):
            if candidate and hasattr(candidate, "fileno"):
                return candidate
        if self._errlog_fallback is None:
            self._errlog_fallback = open(os.devnull, "w")
        return self._errlog_fallback

    async def connect(self, **session_kwargs):
        if self._connect_task is not None:
            return

        errlog = self._resolve_errlog()

        async def _connect_task():
            try:
                async with contextlib.AsyncExitStack() as stack:
                    server_params = StdioServerParameters(
                        command=self.command,
                        args=self.args,
                        env=self.env,
                        cwd=self.cwd,
                    )
                    transport = await stack.enter_async_context(
                        stdio_client(server_params, errlog=errlog)
                    )
                    read_stream, write_stream = transport
                    self._session = await stack.enter_async_context(
                        ClientSession(read_stream, write_stream, **session_kwargs)
                    )

                    logger.debug("Stdio transport connected")
                    self._ready_event.set()

                    await self._stop_event.wait()
            finally:
                self._session = None
                logger.debug("Stdio transport disconnected")

        self._connect_task = asyncio.create_task(_connect_task())
        await self._ready_event.wait()

        if self._connect_task.done():
            exception = self._connect_task.exception()
            if exception is not None:
                raise exception

    async def disconnect(self):
        await super().disconnect()
        self._cleanup_errlog()

    async def close(self):
        await super().close()
        self._cleanup_errlog()

    def _cleanup_errlog(self):
        if self._errlog_fallback:
            try:
                self._errlog_fallback.close()
            except OSError:
                pass
            self._errlog_fallback = None


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return []


def _coerce_str_dict(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    cleaned: Dict[str, str] = {}
    for key, val in value.items():
        if key is None or val is None:
            continue
        cleaned[str(key)] = str(val)
    return cleaned


def _is_workspace_path(path_value: str) -> bool:
    if not path_value:
        return False
    workspace = str(_workspace_root()).rstrip("/")
    candidate = os.path.abspath(path_value)
    return candidate == workspace or candidate.startswith(f"{workspace}/")


def _normalize_stdio_env(env: Dict[str, str], *, runtime_identity: str) -> Dict[str, str]:
    normalized = dict(env)
    runtime_paths = _runtime_cache_paths(runtime_identity)
    home_path = str(runtime_paths["home"])
    xdg_path = str(runtime_paths["xdg"])
    npm_path = str(runtime_paths["npm"])

    current_home = normalized.get("HOME", "").strip()
    if not current_home or _is_workspace_path(current_home):
        normalized["HOME"] = home_path

    current_xdg = normalized.get("XDG_CACHE_HOME", "").strip()
    if not current_xdg or _is_workspace_path(current_xdg):
        normalized["XDG_CACHE_HOME"] = xdg_path

    current_npm = (normalized.get("NPM_CONFIG_CACHE") or normalized.get("npm_config_cache") or "").strip()
    if not current_npm or _is_workspace_path(current_npm):
        normalized["NPM_CONFIG_CACHE"] = npm_path
        normalized["npm_config_cache"] = npm_path
    else:
        normalized["NPM_CONFIG_CACHE"] = current_npm
        normalized["npm_config_cache"] = current_npm

    return normalized


def _proxy_env_from_process() -> Dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in _PROXY_ENV_KEYS and value}


def _parse_mcp_server_payload(payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    raw = payload.get("server")
    if not isinstance(raw, dict):
        return None, {"status": "error", "message": "Missing MCP server definition."}

    config_id = raw.get("config_id") or raw.get("id") or payload.get("server_id")
    if not isinstance(config_id, str) or not config_id.strip():
        return None, {"status": "error", "message": "Missing MCP server config id."}

    name = raw.get("name") or ""
    if not isinstance(name, str):
        name = str(name)

    command = raw.get("command") or ""
    if not isinstance(command, str):
        command = str(command)
    command = command.strip()

    url = raw.get("url") or ""
    if not isinstance(url, str):
        url = str(url)
    url = url.strip()

    args = _coerce_str_list(raw.get("command_args") or raw.get("args") or [])
    env = _coerce_str_dict(raw.get("env") or raw.get("environment") or {})
    headers = _coerce_str_dict(raw.get("headers") or {})
    if command:
        runtime_identity = str(payload.get("agent_id") or f"discovery-{config_id.strip()}")
        env = _normalize_stdio_env(env, runtime_identity=runtime_identity)

    if not command and not url:
        return None, {"status": "error", "message": "MCP server must include a command or URL."}

    return {
        "config_id": config_id.strip(),
        "name": name.strip(),
        "command": command,
        "args": args,
        "url": url,
        "env": env,
        "headers": headers,
    }, None


def _normalize_mcp_result(result: Any, _seen: Optional[set[int]] = None) -> Any:
    if result is None or isinstance(result, (str, int, float, bool)):
        return result
    if _seen is None:
        _seen = set()

    result_id = id(result)
    if result_id in _seen:
        return "<circular-reference>"

    _seen.add(result_id)
    try:
        if isinstance(result, dict):
            return {str(key): _normalize_mcp_result(value, _seen) for key, value in result.items()}
        if isinstance(result, (list, tuple, set)):
            return [_normalize_mcp_result(item, _seen) for item in result]
        if hasattr(result, "model_dump"):
            try:
                return _normalize_mcp_result(result.model_dump(mode="json"), _seen)
            except TypeError:
                try:
                    return _normalize_mcp_result(result.model_dump(), _seen)
                except Exception:
                    logger.debug("Failed to serialize MCP result via model_dump()", exc_info=True)
            except Exception:
                logger.debug("Failed to serialize MCP result via model_dump()", exc_info=True)
        if hasattr(result, "dict"):
            try:
                return _normalize_mcp_result(result.dict(), _seen)
            except Exception:
                logger.debug("Failed to serialize MCP result via dict()", exc_info=True)
        if hasattr(result, "__dict__"):
            return _normalize_mcp_result(vars(result), _seen)
    finally:
        _seen.discard(result_id)
    return str(result)


def _safe_json_log(payload: Dict[str, Any]) -> str:
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=True)
    except TypeError:
        return json.dumps(_normalize_mcp_result(payload), sort_keys=True, ensure_ascii=True)


def _prepare_runtime_proxy_env(
    payload: Dict[str, Any], runtime: Dict[str, Any], *, skip_when_url: bool = False
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    if skip_when_url and runtime.get("url"):
        return None, None

    agent_id, error = _require_agent_id(payload)
    if error:
        return None, error

    agent_root = _agent_workspace(agent_id)
    _store_proxy_env(agent_root, payload)
    if runtime.get("command"):
        process_proxy_env = _proxy_env_from_process()
        manifest_proxy_env = _proxy_env_from_manifest(agent_root)
        if process_proxy_env or manifest_proxy_env:
            runtime["env"] = {
                **process_proxy_env,
                **runtime.get("env", {}),
                **manifest_proxy_env,
            }
    return agent_id, None


async def _call_mcp_tool(runtime: Dict[str, Any], tool_name: str, params: Dict[str, Any]) -> Any:
    if runtime.get("url"):
        transport = StreamableHttpTransport(url=runtime["url"], headers=runtime["headers"])
    else:
        transport = GobiiStdioTransport(
            command=runtime["command"],
            args=runtime["args"],
            env=runtime["env"],
        )

    client = Client(transport)
    async with client:
        timeout = _mcp_timeout_seconds()
        return await asyncio.wait_for(client.call_tool(tool_name, params), timeout=timeout)


async def _discover_mcp_tools(runtime: Dict[str, Any]) -> list[Dict[str, Any]]:
    if runtime.get("url"):
        transport = StreamableHttpTransport(url=runtime["url"], headers=runtime["headers"])
    else:
        transport = GobiiStdioTransport(
            command=runtime["command"],
            args=runtime["args"],
            env=runtime["env"],
        )

    client = Client(transport)
    async with client:
        tools = await client.list_tools()

    serialized: list[Dict[str, Any]] = []
    for tool in tools or []:
        name = getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else None)
        if not name:
            continue
        description = getattr(tool, "description", None) or ""
        parameters = getattr(tool, "inputSchema", None)
        if parameters is None and isinstance(tool, dict):
            parameters = tool.get("inputSchema") or tool.get("input_schema")
        if runtime.get("name") == "pipedream":
            full_name = name
        else:
            full_name = f"mcp_{runtime.get('name')}_{name}" if runtime.get("name") else name
        serialized.append(
            {
                "full_name": full_name,
                "server_name": runtime.get("name") or "",
                "tool_name": name,
                "description": description or f"{name} from {runtime.get('name') or 'MCP server'}",
                "parameters": parameters or {"type": "object", "properties": {}},
            }
        )
    return serialized


def _handle_mcp_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    start = time.time()
    runtime, runtime_error = _parse_mcp_server_payload(payload)
    if runtime_error:
        return runtime_error
    agent_id, error = _prepare_runtime_proxy_env(payload, runtime)
    if error:
        return error

    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return {"status": "error", "message": "Missing required parameter: tool_name"}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

    try:
        result = asyncio.run(_call_mcp_tool(runtime, tool_name.strip(), params))
    except Exception as exc:
        duration_ms = int((time.time() - start) * 1000)
        trace_id, _traceparent = _trace_context(payload)
        logger.exception(
            "Sandbox mcp_request failed agent=%s tool=%s duration_ms=%s trace_id=%s",
            agent_id,
            tool_name,
            duration_ms,
            trace_id,
        )
        response = {"status": "error", "message": str(exc)}
        logger.info(
            "Sandbox mcp_request agent=%s tool=%s status=%s duration_ms=%s trace_id=%s result=%s",
            agent_id,
            tool_name,
            response.get("status"),
            duration_ms,
            trace_id,
            _safe_json_log(response),
        )
        return response

    response = {"status": "ok", "result": _normalize_mcp_result(result)}
    duration_ms = int((time.time() - start) * 1000)
    trace_id, _traceparent = _trace_context(payload)
    logger.info(
        "Sandbox mcp_request agent=%s tool=%s status=%s duration_ms=%s trace_id=%s result=%s",
        agent_id,
        tool_name,
        response.get("status"),
        duration_ms,
        trace_id,
        _safe_json_log(response),
    )
    return response


def _handle_discover_mcp_tools(payload: Dict[str, Any]) -> Dict[str, Any]:
    started_at = time.monotonic()
    runtime, runtime_error = _parse_mcp_server_payload(payload)
    if runtime_error:
        return runtime_error
    _agent_id, error = _prepare_runtime_proxy_env(payload, runtime, skip_when_url=True)
    if error:
        return error
    trace_id, _traceparent = _trace_context(payload)
    try:
        tools = asyncio.run(_discover_mcp_tools(runtime))
    except Exception as exc:
        logger.exception(
            "Sandbox discover_mcp_tools failed server_id=%s duration_ms=%s trace_id=%s",
            runtime.get("config_id"),
            _elapsed_ms(started_at),
            trace_id,
        )
        return {"status": "error", "message": str(exc)}

    response = {
        "status": "ok",
        "tools": tools,
        "server_id": runtime.get("config_id"),
    }
    logger.info(
        "Sandbox discover_mcp_tools server_id=%s status=ok tools=%s duration_ms=%s trace_id=%s",
        runtime.get("config_id"),
        len(tools),
        _elapsed_ms(started_at),
        trace_id,
    )
    return response
