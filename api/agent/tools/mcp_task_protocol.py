"""Focused client for the stateless MCP Tasks extension over Streamable HTTP."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

import httpx
from mcp.types import CallToolResult, Tool


MCP_TASKS_EXTENSION = "io.modelcontextprotocol/tasks"
MCP_STATELESS_PROTOCOL_VERSION = "2026-07-28"
MCP_CLIENT_INFO = {"name": "gobii", "version": "1.0"}


class MCPTaskProtocolError(RuntimeError):
    pass


class MCPTaskHTTPError(MCPTaskProtocolError):
    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        return self.status_code == 429 or 500 <= self.status_code < 600


class MCPTaskMalformedResponse(MCPTaskProtocolError):
    pass


@dataclass(frozen=True)
class MCPTaskDiscovery:
    protocol_version: str
    supports_tasks: bool


@dataclass(frozen=True)
class MCPCreateTaskResult:
    task_id: str
    status: str
    status_message: str
    created_at: str
    last_updated_at: str
    ttl_ms: Optional[int]
    poll_interval_ms: Optional[int]


@dataclass(frozen=True)
class MCPDetailedTaskResult:
    task_id: str
    status: str
    status_message: str
    created_at: str
    last_updated_at: str
    ttl_ms: Optional[int]
    poll_interval_ms: Optional[int]
    input_requests: Optional[dict[str, Any]]
    result: Optional[dict[str, Any]]
    error: Optional[dict[str, Any]]


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise MCPTaskMalformedResponse(f"MCP response field '{key}' must be a non-empty string.")
    return value


def _optional_integer(payload: dict[str, Any], key: str) -> Optional[int]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MCPTaskMalformedResponse(f"MCP response field '{key}' must be a non-negative integer or null.")
    return value


def _required_timestamp(payload: dict[str, Any], key: str) -> str:
    value = _required_string(payload, key)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MCPTaskMalformedResponse(f"MCP response field '{key}' must be an ISO 8601 timestamp.") from exc
    return value


def parse_create_task_result(payload: dict[str, Any]) -> MCPCreateTaskResult:
    if payload.get("resultType") != "task":
        raise MCPTaskMalformedResponse("Expected a CreateTaskResult with resultType 'task'.")
    status = _required_string(payload, "status")
    if status not in {"working", "input_required", "completed", "failed", "cancelled"}:
        raise MCPTaskMalformedResponse(f"Unsupported MCP task status '{status}'.")
    return MCPCreateTaskResult(
        task_id=_required_string(payload, "taskId"),
        status=status,
        status_message=str(payload.get("statusMessage") or ""),
        created_at=_required_timestamp(payload, "createdAt"),
        last_updated_at=_required_timestamp(payload, "lastUpdatedAt"),
        ttl_ms=_optional_integer(payload, "ttlMs"),
        poll_interval_ms=_optional_integer(payload, "pollIntervalMs"),
    )


def parse_detailed_task_result(payload: dict[str, Any]) -> MCPDetailedTaskResult:
    if payload.get("resultType") != "complete":
        raise MCPTaskMalformedResponse("tasks/get must return resultType 'complete'.")
    status = _required_string(payload, "status")
    if status not in {"working", "input_required", "completed", "failed", "cancelled"}:
        raise MCPTaskMalformedResponse(f"Unsupported MCP task status '{status}'.")
    input_requests = payload.get("inputRequests")
    result = payload.get("result")
    error = payload.get("error")
    if status == "input_required" and not isinstance(input_requests, dict):
        raise MCPTaskMalformedResponse("An input_required task must include inputRequests.")
    if status == "completed" and not isinstance(result, dict):
        raise MCPTaskMalformedResponse("A completed task must include an inlined result.")
    if status == "failed" and not isinstance(error, dict):
        raise MCPTaskMalformedResponse("A failed task must include a JSON-RPC error.")
    return MCPDetailedTaskResult(
        task_id=_required_string(payload, "taskId"),
        status=status,
        status_message=str(payload.get("statusMessage") or ""),
        created_at=_required_timestamp(payload, "createdAt"),
        last_updated_at=_required_timestamp(payload, "lastUpdatedAt"),
        ttl_ms=_optional_integer(payload, "ttlMs"),
        poll_interval_ms=_optional_integer(payload, "pollIntervalMs"),
        input_requests=input_requests,
        result=result,
        error=error,
    )


class MCPTaskHTTPClient:
    """Stateless 2026 MCP client using the project's configured httpx factory."""

    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str],
        httpx_client_factory: Callable[..., httpx.AsyncClient],
        timeout_seconds: float,
        protocol_version: str = MCP_STATELESS_PROTOCOL_VERSION,
    ):
        self.url = url
        self.headers = dict(headers)
        self.httpx_client_factory = httpx_client_factory
        self.timeout_seconds = timeout_seconds
        self.protocol_version = protocol_version

    def _request_meta(self, *, advertise_tasks: bool) -> dict[str, Any]:
        capabilities: dict[str, Any] = {}
        if advertise_tasks:
            capabilities["extensions"] = {MCP_TASKS_EXTENSION: {}}
        return {
            "io.modelcontextprotocol/protocolVersion": self.protocol_version,
            "io.modelcontextprotocol/clientInfo": MCP_CLIENT_INFO,
            "io.modelcontextprotocol/clientCapabilities": capabilities,
        }

    @staticmethod
    def _decode_response_body(response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "").lower()
        if "text/event-stream" in content_type:
            event_data: list[str] = []
            events: list[str] = []
            for line in [*response.text.splitlines(), ""]:
                if not line:
                    if event_data:
                        events.append("\n".join(event_data))
                        event_data = []
                elif line.startswith("data:"):
                    event_data.append(line[5:].lstrip())
            for event in events:
                try:
                    decoded = json.loads(event)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict) and ("result" in decoded or "error" in decoded):
                    return decoded
            raise MCPTaskMalformedResponse("MCP SSE response did not contain a JSON-RPC result.")
        try:
            decoded = response.json()
        except json.JSONDecodeError as exc:
            raise MCPTaskMalformedResponse("MCP server returned invalid JSON.") from exc
        if not isinstance(decoded, dict):
            raise MCPTaskMalformedResponse("MCP server returned a non-object JSON-RPC response.")
        return decoded

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        name: Optional[str] = None,
        advertise_tasks: bool,
    ) -> dict[str, Any]:
        request_params = dict(params)
        existing_meta = request_params.get("_meta")
        meta = dict(existing_meta) if isinstance(existing_meta, dict) else {}
        meta.update(self._request_meta(advertise_tasks=advertise_tasks))
        request_params["_meta"] = meta
        headers = {
            **self.headers,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self.protocol_version,
            "Mcp-Method": method,
        }
        if name:
            headers["Mcp-Name"] = name
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": request_params}
        async with self.httpx_client_factory(
            headers=headers,
            timeout=httpx.Timeout(self.timeout_seconds),
        ) as client:
            response = await client.post(self.url, json=body)
        if response.status_code >= 400:
            raise MCPTaskHTTPError(
                f"MCP {method} failed with HTTP {response.status_code}.",
                status_code=response.status_code,
            )
        payload = self._decode_response_body(response)
        if payload.get("jsonrpc") != "2.0" or payload.get("id") != 1:
            raise MCPTaskMalformedResponse(f"MCP {method} returned an invalid JSON-RPC envelope.")
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "MCP JSON-RPC request failed.")
            raise MCPTaskProtocolError(f"{message} (code={error.get('code')})")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise MCPTaskMalformedResponse(f"MCP {method} response did not contain an object result.")
        return result

    async def discover(self) -> MCPTaskDiscovery:
        result = await self._request("server/discover", {}, advertise_tasks=True)
        versions = result.get("supportedVersions")
        if not isinstance(versions, list) or not all(isinstance(value, str) for value in versions):
            raise MCPTaskMalformedResponse("server/discover did not return supportedVersions.")
        if self.protocol_version not in versions:
            raise MCPTaskProtocolError(f"MCP server does not advertise protocol {self.protocol_version}.")
        capabilities = result.get("capabilities")
        extensions = capabilities.get("extensions") if isinstance(capabilities, dict) else None
        return MCPTaskDiscovery(
            protocol_version=self.protocol_version,
            supports_tasks=isinstance(extensions, dict) and MCP_TASKS_EXTENSION in extensions,
        )

    async def list_tools(self) -> list[Tool]:
        collected: list[Tool] = []
        cursor: Optional[str] = None
        seen_cursors: set[str] = set()
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = await self._request("tools/list", params, advertise_tasks=False)
            tools = result.get("tools")
            if not isinstance(tools, list):
                raise MCPTaskMalformedResponse("tools/list did not return a tools array.")
            collected.extend(Tool.model_validate(tool) for tool in tools)
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return collected
            if not isinstance(next_cursor, str) or not next_cursor:
                raise MCPTaskMalformedResponse("tools/list returned an invalid nextCursor.")
            if next_cursor in seen_cursors:
                raise MCPTaskMalformedResponse("tools/list repeated a pagination cursor.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        advertise_tasks: bool,
    ) -> CallToolResult | MCPCreateTaskResult:
        result = await self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            name=tool_name,
            advertise_tasks=advertise_tasks,
        )
        if result.get("resultType") == "task":
            return parse_create_task_result(result)
        return CallToolResult.model_validate(result)

    async def get_task(self, task_id: str) -> MCPDetailedTaskResult:
        result = await self._request(
            "tasks/get",
            {"taskId": task_id},
            name=task_id,
            advertise_tasks=True,
        )
        parsed = parse_detailed_task_result(result)
        if parsed.task_id != task_id:
            raise MCPTaskMalformedResponse("tasks/get returned a different taskId.")
        return parsed

    async def cancel_task(self, task_id: str) -> None:
        result = await self._request(
            "tasks/cancel",
            {"taskId": task_id},
            name=task_id,
            advertise_tasks=True,
        )
        if result.get("resultType") != "complete":
            raise MCPTaskMalformedResponse("tasks/cancel must return resultType 'complete'.")
