import json
import logging
import time
from typing import Any, Dict

from sandbox_server.config import _agent_workspace
from sandbox_server.files import _handle_create_file, _handle_create_pdf
from sandbox_server.manifest import _store_proxy_env
from sandbox_server.run import _handle_python_exec
from sandbox_server.workspace import _require_agent_id, _trace_context

logger = logging.getLogger(__name__)


def _handle_tool_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    start = time.time()
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
        response = _handle_python_exec(params)
        duration_ms = int((time.time() - start) * 1000)
        trace_id, _traceparent = _trace_context(payload)
        logger.info(
            "Sandbox tool_request agent=%s tool=%s status=%s duration_ms=%s trace_id=%s result=%s",
            agent_id,
            tool_name,
            response.get("status"),
            duration_ms,
            trace_id,
            json.dumps(response, sort_keys=True, ensure_ascii=True),
        )
        return response
    if tool_name == "create_file":
        response = _handle_create_file(agent_root, params)
        duration_ms = int((time.time() - start) * 1000)
        trace_id, _traceparent = _trace_context(payload)
        logger.info(
            "Sandbox tool_request agent=%s tool=%s status=%s duration_ms=%s trace_id=%s result=%s",
            agent_id,
            tool_name,
            response.get("status"),
            duration_ms,
            trace_id,
            json.dumps(response, sort_keys=True, ensure_ascii=True),
        )
        return response
    if tool_name == "create_pdf":
        response = _handle_create_pdf(agent_root, params)
        duration_ms = int((time.time() - start) * 1000)
        trace_id, _traceparent = _trace_context(payload)
        logger.info(
            "Sandbox tool_request agent=%s tool=%s status=%s duration_ms=%s trace_id=%s result=%s",
            agent_id,
            tool_name,
            response.get("status"),
            duration_ms,
            trace_id,
            json.dumps(response, sort_keys=True, ensure_ascii=True),
        )
        return response

    response = {
        "status": "error",
        "error_code": "sandbox_unsupported_tool",
        "message": f"Sandbox tool '{tool_name}' is not supported yet.",
    }
    duration_ms = int((time.time() - start) * 1000)
    trace_id, _traceparent = _trace_context(payload)
    logger.info(
        "Sandbox tool_request agent=%s tool=%s status=%s duration_ms=%s trace_id=%s result=%s",
        agent_id,
        tool_name,
        response.get("status"),
        duration_ms,
        trace_id,
        json.dumps(response, sort_keys=True, ensure_ascii=True),
    )
    return response
