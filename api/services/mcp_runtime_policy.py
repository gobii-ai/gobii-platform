"""Shared transport and sandbox policy for MCP server representations."""

from collections.abc import Mapping
from typing import Any

from api.models import MCPServerConfig


def _runtime_value(runtime: Any, field: str) -> Any:
    if isinstance(runtime, Mapping):
        return runtime.get(field)
    return getattr(runtime, field, None)


def mcp_server_is_stdio(runtime: Any) -> bool:
    """Return whether the runtime selects the command-based STDIO transport."""
    if runtime is None:
        return False
    return bool(_runtime_value(runtime, "command")) and not bool(_runtime_value(runtime, "url"))


def mcp_server_requires_agent_sandbox(runtime: Any) -> bool:
    """Return whether the runtime must execute inside an agent sandbox."""
    if runtime is None:
        return False
    return (
        _runtime_value(runtime, "scope") != MCPServerConfig.Scope.PLATFORM
        and mcp_server_is_stdio(runtime)
    )
