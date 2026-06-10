"""
Spawn-agent request tool for persistent agents.

This tool lets an agent request creation of a specialist peer agent that must be
approved by a human (Create/Decline). The spawned agent is peer-linked on approval.
"""

import logging
from typing import Any, Dict

from django.contrib.sites.models import Site
from django.urls import NoReverseMatch, reverse

from agents.services import AgentService
from api.services.spawn_requests import SpawnRequestService
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.urls import append_context_query, append_query_params

from .meta_gobii_names import META_GOBII_SYSTEM_SKILL_KEY, META_GOBII_SYSTEM_SKILL_KEYS

from ...models import AgentSpawnRequest, PersistentAgent, PersistentAgentSystemSkillState

logger = logging.getLogger(__name__)


def _should_continue_work(params: Dict[str, Any]) -> bool:
    raw = params.get("will_continue_work")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        return normalized in {"1", "true", "yes"}
    return bool(raw)


def _owner_for_agent(agent: PersistentAgent):
    return agent.organization if agent.organization_id else agent.user


def _meta_gobii_enabled_for_agent(agent: PersistentAgent) -> bool:
    return PersistentAgentSystemSkillState.objects.filter(
        agent=agent,
        skill_key__in=META_GOBII_SYSTEM_SKILL_KEYS,
        is_enabled=True,
    ).exists()


def _build_urls(agent: PersistentAgent, spawn_request: AgentSpawnRequest) -> tuple[str | None, str]:
    org_id = str(agent.organization_id) if agent.organization_id else None

    try:
        decision_path = reverse(
            "console_agent_spawn_request_decision",
            kwargs={"agent_id": agent.id, "spawn_request_id": spawn_request.id},
        )
    except NoReverseMatch:
        logger.warning("Failed to reverse spawn decision URL for agent %s", agent.id, exc_info=True)
        decision_path = f"/console/api/agents/{agent.id}/spawn-requests/{spawn_request.id}/decision/"
    decision_path = append_context_query(decision_path, org_id)

    chat_path = f"/app/agents/{agent.id}"
    chat_path = append_query_params(chat_path, {"spawn_request_id": str(spawn_request.id)})
    chat_path = append_context_query(chat_path, org_id)

    try:
        current_site = Site.objects.get_current()
        approval_url = f"https://{current_site.domain}{chat_path}"
    except Site.DoesNotExist:
        logger.warning("No current Site configured; returning relative approval URL for agent %s", agent.id)
        approval_url = chat_path

    return approval_url, decision_path


def execute_spawn_agent(
    agent: PersistentAgent,
    params: Dict[str, Any],
    *,
    invoked_via_meta_gobii: bool = False,
) -> Dict[str, Any]:
    if not invoked_via_meta_gobii and not _meta_gobii_enabled_for_agent(agent):
        return {
            "status": "error",
            "message": (
                "spawn_agent is available only through Meta Gobii. Enable the "
                f"{META_GOBII_SYSTEM_SKILL_KEY} system skill and use Meta Gobii's "
                "request Gobii creation capability."
            ),
        }

    charter = str(params.get("charter") or "").strip()
    handoff_message = str(params.get("handoff_message") or "").strip()
    request_reason = str(params.get("reason") or "").strip()
    will_continue = _should_continue_work(params)

    if not charter:
        return {"status": "error", "message": "Missing required parameter: charter"}
    if not handoff_message:
        return {"status": "error", "message": "Missing required parameter: handoff_message"}

    owner = _owner_for_agent(agent)
    if not AgentService.has_agents_available(owner):
        return {
            "status": "error",
            "message": "No additional agent capacity is available for this account.",
        }

    spawn_request, created = SpawnRequestService.create_or_reuse_pending_request(
        agent=agent,
        requested_charter=charter,
        handoff_message=handoff_message,
        request_reason=request_reason,
    )

    if created:
        props = Analytics.with_org_properties(
            {
                "agent_id": str(agent.id),
                "agent_name": agent.name,
                "spawn_request_id": str(spawn_request.id),
            },
            organization=agent.organization,
        )
        Analytics.track_event(
            user_id=agent.user_id,
            event=AnalyticsEvent.AGENT_SPAWN_REQUESTED,
            source=AnalyticsSource.AGENT,
            properties=props,
        )

    approval_url, decision_api_url = _build_urls(agent, spawn_request)
    request_label = "specialist agent"

    if created:
        message = (
            f"Created spawn request for {request_label}. "
            f"Ask the user to choose Create/Decline at {approval_url or 'the agent chat'}."
        )
    else:
        message = (
            f"A matching spawn request for {request_label} is already pending. "
            f"Ask the user to choose Create/Decline at {approval_url or 'the agent chat'}."
        )

    payload: Dict[str, Any] = {
        "status": "ok",
        "request_status": AgentSpawnRequest.RequestStatus.PENDING,
        "message": message,
        "created_count": 1 if created else 0,
        "already_pending_count": 0 if created else 1,
        "spawn_request_id": str(spawn_request.id),
        "approval_url": approval_url,
        "decision_api_url": decision_api_url,
        "auto_sleep_ok": not will_continue,
    }
    return payload
