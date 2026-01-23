import logging

from celery import shared_task

from api.services.sandbox_compute import (
    SandboxComputeService,
    SandboxComputeUnavailable,
    sandbox_compute_enabled,
)

logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.sandbox_compute.discover_mcp_tools")
def discover_mcp_tools(config_id: str, reason: str = "") -> dict:
    if not sandbox_compute_enabled():
        return {"status": "skipped", "message": "Sandbox compute disabled"}

    if not config_id:
        return {"status": "error", "message": "Missing MCP server config id"}

    try:
        service = SandboxComputeService()
    except SandboxComputeUnavailable as exc:
        return {"status": "error", "message": str(exc)}

    return service.discover_mcp_tools(config_id, reason=reason)
