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

from ...models import AgentSpawnRequest, PersistentAgent

logger = logging.getLogger(__name__)


def _should_continue_work(params: Dict[str, Any]) -> bool:
    raw = params.get("will_continue_work")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        return normalized in {"1", "true", "yes"}
    return bool(raw)


def _owner_for_agent(agent: PersistentAgent):
    return agent.organization if agent.organization_id else agent.user


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

    try:
        chat_path = reverse("agent_chat_shell", kwargs={"pk": agent.id})
    except NoReverseMatch:
        logger.warning("Failed to reverse chat URL for agent %s", agent.id, exc_info=True)
        chat_path = f"/console/agents/{agent.id}/chat/"
    chat_path = append_query_params(chat_path, {"spawn_request_id": str(spawn_request.id)})
    chat_path = append_context_query(chat_path, org_id)

    try:
        current_site = Site.objects.get_current()
        approval_url = f"https://{current_site.domain}{chat_path}"
    except Site.DoesNotExist:
        logger.warning("No current Site configured; returning relative approval URL for agent %s", agent.id)
        approval_url = chat_path

    return approval_url, decision_path


def get_spawn_agent_tool(
    agent: PersistentAgent | None = None,
    *,
    available_capacity: int | None = None,
) -> Dict[str, Any]:
    availability_note = ""
    if agent:
        available = available_capacity
        if available is None:
            owner = _owner_for_agent(agent)
            available = AgentService.get_agents_available(owner)
        availability_note = f" Current owner capacity: {max(int(available), 0)} additional agent(s)."

    return {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": (
                "Request creation of a specialist partner agent when work is genuinely outside your charter/scope. "
                "This does not create the agent immediately: it creates a human approval request (Create/Decline). "
                "On approval, the new agent is automatically peer-linked and receives your handoff message. "
                "Use sparingly for clear scope boundaries, not for convenience."
                f"{availability_note}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "charter": {
                        "type": "string",
                        "description": "Full charter/instructions for the specialist agent to run with.",
                    },
                    "handoff_message": {
                        "type": "string",
                        "description": "Initial task handoff sent from you to the spawned agent after approval.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this work is out-of-scope and requires a specialist agent.",
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = you will continue with more work now, false = done after this action.",
                    },
                },
                "required": ["charter", "handoff_message", "will_continue_work"],
            },
        },
    }


def execute_spawn_agent(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
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
