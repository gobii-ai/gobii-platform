import logging
from typing import Optional

from api.models import PersistentAgent
from api.services.sandbox_compute import (
    SandboxComputeService,
    SandboxComputeUnavailable,
    sandbox_compute_enabled,
)

logger = logging.getLogger(__name__)


def schedule_mcp_tool_discovery(
    config_id: str,
    *,
    reason: str,
    agent: Optional[PersistentAgent] = None,
) -> None:
    if not config_id or not sandbox_compute_enabled():
        return

    try:
        service = SandboxComputeService()
        service.discover_mcp_tools(config_id, reason=reason, agent=agent)
    except (SandboxComputeUnavailable, ValueError, RuntimeError) as exc:
        logger.warning("Inline MCP tool discovery failed for %s: %s", config_id, exc)
