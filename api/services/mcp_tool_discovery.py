import logging

from django.conf import settings

from api.services.sandbox_compute import sandbox_compute_enabled

logger = logging.getLogger(__name__)


def schedule_mcp_tool_discovery(config_id: str, *, reason: str) -> None:
    if not config_id or not sandbox_compute_enabled():
        return

    if not getattr(settings, "CELERY_BROKER_URL", ""):
        _run_mcp_tool_discovery(config_id, reason)
        return

    from api.tasks.sandbox_compute import discover_mcp_tools

    try:
        discover_mcp_tools.delay(config_id, reason=reason)
    except (AttributeError, TypeError) as exc:
        logger.warning("Failed to enqueue MCP tool discovery for %s: %s", config_id, exc)
        _run_mcp_tool_discovery(config_id, reason)


def _run_mcp_tool_discovery(config_id: str, reason: str) -> None:
    from api.tasks.sandbox_compute import discover_mcp_tools

    try:
        discover_mcp_tools(config_id, reason=reason)
    except (ValueError, RuntimeError) as exc:
        logger.warning("Inline MCP tool discovery failed for %s: %s", config_id, exc)
