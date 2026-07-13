import logging

from celery import shared_task

from api.agent.tools.mcp_manager import get_mcp_manager

logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.refresh_mcp_catalog", ignore_result=True)
def refresh_mcp_catalog(server_id: str, app_slugs: list[str]) -> None:
    manager = get_mcp_manager()
    if not manager.refresh_cached_catalog(server_id, app_slugs):
        logger.warning("MCP catalog refresh did not complete for server %s", server_id)
