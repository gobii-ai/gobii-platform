import logging

from celery import shared_task
from django.db import DatabaseError
from redis.exceptions import RedisError

from api.models import AgentComputeSession, PersistentAgent
from api.services.sandbox_compute import (
    SandboxComputeService,
    SandboxComputeUnavailable,
    _post_sync_queue_key,
    sandbox_compute_enabled,
    sandbox_compute_enabled_for_agent,
)
from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.sandbox_compute.warm_sandbox_for_agent", max_retries=0)
def warm_sandbox_for_agent(agent_id: str, reason: str = "recent_sandbox_tool_history") -> dict:
    if not sandbox_compute_enabled():
        return {"status": "skipped", "message": "Sandbox compute disabled"}
    if not agent_id:
        return {"status": "error", "message": "Missing agent id"}

    try:
        agent = (
            PersistentAgent.objects.select_related("user", "organization")
            .filter(id=agent_id)
            .first()
        )
    except DatabaseError as exc:
        logger.warning("Sandbox warm-up failed to load agent=%s: %s", agent_id, exc)
        return {"status": "error", "message": "Failed to load agent"}

    if not agent:
        return {"status": "skipped", "message": "Agent not found"}
    if not sandbox_compute_enabled_for_agent(agent):
        return {"status": "skipped", "message": "Agent not eligible for sandbox compute"}

    try:
        service = SandboxComputeService()
        session = service.deploy_or_resume(agent, reason=reason)
    except SandboxComputeUnavailable as exc:
        logger.info("Sandbox warm-up unavailable agent=%s reason=%s: %s", agent_id, reason, exc)
        return {"status": "error", "message": str(exc)}
    except DatabaseError as exc:
        logger.warning("Sandbox warm-up database error agent=%s reason=%s: %s", agent_id, reason, exc)
        return {"status": "error", "message": "Sandbox warm-up database error"}

    logger.info(
        "Sandbox warm-up completed agent=%s reason=%s session=%s state=%s",
        agent_id,
        reason,
        session.pk,
        session.state,
    )
    return {
        "status": "ok",
        "session_id": str(session.pk),
        "state": session.state,
    }


@shared_task(name="api.tasks.sandbox_compute.discover_mcp_tools")
def discover_mcp_tools(config_id: str, reason: str = "", agent_id: str = "") -> dict:
    if not sandbox_compute_enabled():
        return {"status": "skipped", "message": "Sandbox compute disabled"}

    if not config_id:
        return {"status": "error", "message": "Missing MCP server config id"}

    try:
        service = SandboxComputeService()
    except SandboxComputeUnavailable as exc:
        return {"status": "error", "message": str(exc)}

    agent = None
    if agent_id:
        agent = PersistentAgent.objects.filter(id=agent_id).first()
        if not agent:
            return {"status": "skipped", "message": "Agent not found"}

    return service.discover_mcp_tools(config_id, reason=reason, agent=agent)


@shared_task(name="api.tasks.sandbox_compute.sync_filespace_after_call", max_retries=0)
def sync_filespace_after_call(agent_id: str, source: str = "") -> dict:
    key = _post_sync_queue_key(str(agent_id or ""))
    redis_client = None
    try:
        redis_client = get_redis_client()
    except RedisError:
        logger.warning("Post-sync task could not connect to Redis agent=%s source=%s", agent_id, source)

    try:
        if not sandbox_compute_enabled():
            return {"status": "skipped", "message": "Sandbox compute disabled"}
        if not agent_id:
            return {"status": "error", "message": "Missing agent id"}

        agent = PersistentAgent.objects.filter(id=agent_id).first()
        if not agent:
            return {"status": "skipped", "message": "Agent not found"}

        session = AgentComputeSession.objects.filter(agent=agent).first()
        if not session:
            return {"status": "skipped", "message": "Sandbox session not found"}

        try:
            service = SandboxComputeService()
        except SandboxComputeUnavailable as exc:
            return {"status": "error", "message": str(exc)}

        sync_result = service._sync_workspace_push(agent, session)
        if not sync_result:
            result = {"status": "skipped", "message": "No sandbox push required"}
        else:
            result = sync_result

        if result.get("status") != "ok":
            logger.warning(
                "Sandbox async post-sync failed agent=%s source=%s status=%s result=%s",
                agent_id,
                source,
                result.get("status"),
                result,
            )
        else:
            logger.info(
                "Sandbox async post-sync completed agent=%s source=%s status=ok",
                agent_id,
                source,
            )
        return result
    finally:
        if redis_client is not None:
            try:
                redis_client.delete(key)
            except RedisError:
                logger.warning(
                    "Post-sync task failed to clear coalesce key agent=%s source=%s key=%s",
                    agent_id,
                    source,
                    key,
                    exc_info=True,
                )
