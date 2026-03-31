import logging
import time
from typing import Any, Callable, Dict

from sandbox_server.logging_config import configure_logging
from sandbox_server.mcp import _handle_discover_mcp_tools, _handle_mcp_request
from sandbox_server.run import _handle_deploy_or_resume, _handle_run_command, _handle_terminate
from sandbox_server.sync import _handle_sync_filespace
from sandbox_server.tools import _handle_tool_request
from sandbox_server.workspace import _elapsed_ms, _extract_traceparent, _json_response, _parse_json, _require_auth

logger = logging.getLogger(__name__)
configure_logging()

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
    started_at = time.monotonic()
    path = environ.get("PATH_INFO", "") or ""
    method = environ.get("REQUEST_METHOD", "GET").upper()

    if path.rstrip("/") == "/healthz":
        return _json_response(start_response, "200 OK", {"status": "ok"})

    if method != "POST":
        logger.warning(
            "Sandbox request rejected path=%s method=%s http_status=405 duration_ms=%s",
            path,
            method,
            _elapsed_ms(started_at),
        )
        return _json_response(start_response, "405 Method Not Allowed", {"status": "error", "message": "POST only."})

    auth_error = _require_auth(environ)
    if auth_error:
        logger.warning(
            "Sandbox request rejected path=%s method=%s http_status=401 duration_ms=%s",
            path,
            method,
            _elapsed_ms(started_at),
        )
        return _json_response(start_response, "401 Unauthorized", auth_error)

    payload, parse_error = _parse_json(environ)
    if parse_error:
        logger.warning(
            "Sandbox request rejected path=%s method=%s http_status=400 reason=parse_error duration_ms=%s",
            path,
            method,
            _elapsed_ms(started_at),
        )
        return _json_response(start_response, "400 Bad Request", parse_error)
    if payload is None:
        logger.warning(
            "Sandbox request rejected path=%s method=%s http_status=400 reason=invalid_payload duration_ms=%s",
            path,
            method,
            _elapsed_ms(started_at),
        )
        return _json_response(start_response, "400 Bad Request", {"status": "error", "message": "Invalid request."})
    traceparent, trace_id = _extract_traceparent(environ)
    if traceparent:
        payload["_traceparent"] = traceparent
    if trace_id:
        payload["_trace_id"] = trace_id

    handler = _ROUTES.get(path.rstrip("/"))
    if not handler:
        logger.warning(
            "Sandbox request rejected path=%s method=%s http_status=404 trace_id=%s duration_ms=%s",
            path,
            method,
            trace_id,
            _elapsed_ms(started_at),
        )
        return _json_response(start_response, "404 Not Found", {"status": "error", "message": "Unknown endpoint."})

    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str):
        agent_id = None
    elif not agent_id.strip():
        agent_id = None
    else:
        agent_id = agent_id.strip()

    try:
        result = handler(payload)
    except Exception:
        logger.exception(
            "Sandbox compute request failed path=%s method=%s agent=%s trace_id=%s duration_ms=%s",
            path,
            method,
            agent_id,
            trace_id,
            _elapsed_ms(started_at),
        )
        return _json_response(
            start_response,
            "500 Internal Server Error",
            {"status": "error", "message": "Sandbox compute request failed."},
        )

    status = "unknown"
    if isinstance(result, dict):
        maybe_status = result.get("status")
        if isinstance(maybe_status, str) and maybe_status:
            status = maybe_status

    logger.info(
        (
            "Sandbox request completed path=%s method=%s agent=%s status=%s "
            "trace_id=%s duration_ms=%s payload_bytes=%s"
        ),
        path,
        method,
        agent_id,
        status,
        trace_id,
        _elapsed_ms(started_at),
        environ.get("CONTENT_LENGTH", "0"),
    )

    return _json_response(start_response, "200 OK", result)
