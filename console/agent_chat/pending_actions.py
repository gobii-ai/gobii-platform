from django.urls import reverse
from django.utils import timezone

from api.agent.comms.human_input_requests import list_pending_human_input_requests
from api.models import (
    AgentSpawnRequest,
    CommsAllowlistRequest,
    PersistentAgent,
    PersistentAgentSecret,
)

from .access import user_can_manage_agent_settings


def _build_human_input_action(agent: PersistentAgent) -> dict | None:
    human_input_requests = list_pending_human_input_requests(agent)
    if not human_input_requests:
        return None
    return {
        "id": "human_input",
        "kind": "human_input",
        "requests": human_input_requests,
        "count": len(human_input_requests),
    }


def _serialize_requested_secret(secret: PersistentAgentSecret) -> dict:
    return {
        "id": str(secret.id),
        "name": secret.name,
        "key": secret.key,
        "secretType": secret.secret_type,
        "domainPattern": secret.domain_pattern,
        "description": secret.description,
        "createdAt": secret.created_at.isoformat() if secret.created_at else None,
        "updatedAt": secret.updated_at.isoformat() if secret.updated_at else None,
    }


def _serialize_contact_request(request_obj: CommsAllowlistRequest) -> dict:
    return {
        "id": str(request_obj.id),
        "channel": request_obj.channel,
        "address": request_obj.address,
        "name": request_obj.name,
        "reason": request_obj.reason,
        "purpose": request_obj.purpose,
        "allowInbound": bool(request_obj.request_inbound),
        "allowOutbound": bool(request_obj.request_outbound),
        "canConfigure": bool(request_obj.request_configure),
        "requestedAt": request_obj.requested_at.isoformat() if request_obj.requested_at else None,
        "expiresAt": request_obj.expires_at.isoformat() if request_obj.expires_at else None,
    }


def _serialize_spawn_request(agent: PersistentAgent, spawn_request: AgentSpawnRequest) -> dict:
    return {
        "id": f"spawn_request:{spawn_request.id}",
        "kind": "spawn_request",
        "requestId": str(spawn_request.id),
        "requestedCharter": spawn_request.requested_charter,
        "handoffMessage": spawn_request.handoff_message,
        "requestReason": spawn_request.request_reason,
        "requestedAt": spawn_request.requested_at.isoformat() if spawn_request.requested_at else None,
        "expiresAt": spawn_request.expires_at.isoformat() if spawn_request.expires_at else None,
        "decisionApiUrl": reverse(
            "console_agent_spawn_request_decision",
            kwargs={"agent_id": agent.id, "spawn_request_id": spawn_request.id},
        ),
    }


def _expire_pending_spawn_requests(agent: PersistentAgent) -> None:
    now = timezone.now()
    AgentSpawnRequest.objects.filter(
        agent=agent,
        status=AgentSpawnRequest.RequestStatus.PENDING,
        expires_at__lt=now,
    ).update(
        status=AgentSpawnRequest.RequestStatus.EXPIRED,
        responded_at=now,
    )


def _expire_pending_contact_requests(agent: PersistentAgent) -> None:
    now = timezone.now()
    CommsAllowlistRequest.objects.filter(
        agent=agent,
        status=CommsAllowlistRequest.RequestStatus.PENDING,
        expires_at__lt=now,
    ).update(
        status=CommsAllowlistRequest.RequestStatus.EXPIRED,
        responded_at=now,
    )


def list_pending_action_requests(agent: PersistentAgent, viewer_user) -> list[dict]:
    pending_actions: list[dict] = []

    human_input_action = _build_human_input_action(agent)
    if human_input_action:
        pending_actions.append(human_input_action)

    if viewer_user is None or not user_can_manage_agent_settings(
        viewer_user,
        agent,
        allow_delinquent_personal_chat=True,
    ):
        return pending_actions

    _expire_pending_spawn_requests(agent)
    _expire_pending_contact_requests(agent)

    for spawn_request in (
        AgentSpawnRequest.objects.filter(
            agent=agent,
            status=AgentSpawnRequest.RequestStatus.PENDING,
        )
        .order_by("-requested_at")
    ):
        pending_actions.append(_serialize_spawn_request(agent, spawn_request))

    requested_secrets = list(
        PersistentAgentSecret.objects.filter(
            agent=agent,
            requested=True,
        ).order_by("secret_type", "domain_pattern", "name")
    )
    if requested_secrets:
        pending_actions.append(
            {
                "id": "requested_secrets",
                "kind": "requested_secrets",
                "secrets": [_serialize_requested_secret(secret) for secret in requested_secrets],
                "count": len(requested_secrets),
                "fulfillApiUrl": reverse("console_agent_requested_secrets_fulfill", kwargs={"agent_id": agent.id}),
                "removeApiUrl": reverse("console_agent_requested_secrets_remove_api", kwargs={"agent_id": agent.id}),
            }
        )

    pending_contact_requests = list(
        CommsAllowlistRequest.objects.filter(
            agent=agent,
            status=CommsAllowlistRequest.RequestStatus.PENDING,
        ).order_by("-requested_at")
    )
    if pending_contact_requests:
        pending_actions.append(
            {
                "id": "contact_requests",
                "kind": "contact_requests",
                "requests": [_serialize_contact_request(request_obj) for request_obj in pending_contact_requests],
                "count": len(pending_contact_requests),
                "resolveApiUrl": reverse("console_agent_contact_requests_resolve", kwargs={"agent_id": agent.id}),
            }
        )

    return pending_actions


def get_legacy_pending_human_input_requests(pending_actions: list[dict]) -> list[dict]:
    for action in pending_actions:
        if action.get("kind") == "human_input":
            requests = action.get("requests")
            if isinstance(requests, list):
                return requests
            return []
    return []
