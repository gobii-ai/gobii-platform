"""Focused client for the stateless MCP Tasks extension over Streamable HTTP."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Callable, Literal, Optional

import httpx
from mcp.types import CallToolResult, Tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


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


TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class _MCPTask(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    task_id: str = Field(alias="taskId", min_length=1)
    status: TaskStatus
    status_message: str = Field(default="", alias="statusMessage")
    created_at: datetime = Field(alias="createdAt")
    last_updated_at: datetime = Field(alias="lastUpdatedAt")
    ttl_ms: Optional[NonNegativeInt] = Field(default=None, alias="ttlMs")
    poll_interval_ms: Optional[NonNegativeInt] = Field(default=None, alias="pollIntervalMs")

    @field_validator("created_at", "last_updated_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class MCPCreateTaskResult(_MCPTask):
    result_type: Literal["task"] = Field(default="task", alias="resultType")


class MCPDetailedTaskResult(_MCPTask):
    result_type: Literal["complete"] = Field(default="complete", alias="resultType")
    input_requests: Optional[dict[str, Any]] = Field(default=None, alias="inputRequests")
    result: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_status_payload(self):
        required_payloads = {
            "input_required": ("inputRequests", self.input_requests),
            "completed": ("result", self.result),
            "failed": ("error", self.error),
        }
        field_name, value = required_payloads.get(self.status, ("", {}))
        if not isinstance(value, dict):
            raise ValueError(f"An MCP task with status '{self.status}' must include {field_name}.")
        return self


def _parse_task_result(model, payload: dict[str, Any], result_type: str):
    if payload.get("resultType") != result_type:
        raise MCPTaskMalformedResponse(f"Expected resultType '{result_type}'.")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise MCPTaskMalformedResponse(f"Malformed MCP task response: {exc}") from exc


def parse_create_task_result(payload: dict[str, Any]) -> MCPCreateTaskResult:
    return _parse_task_result(MCPCreateTaskResult, payload, "task")


def parse_detailed_task_result(payload: dict[str, Any]) -> MCPDetailedTaskResult:
    return _parse_task_result(MCPDetailedTaskResult, payload, "complete")


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
