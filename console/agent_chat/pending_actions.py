from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from api.agent.comms.human_input_requests import expire_pending_human_input_requests, list_pending_human_input_requests
from api.models import AgentSpawnRequest, CommsAllowlistRequest, OutboundEmailReview, PersistentAgent, PersistentAgentHumanInputRequest, PersistentAgentSecret

from .access import user_can_manage_agent_settings

CONTACT_REQUEST_PENDING_ACTION_PREVIEW_LIMIT = 10


def _build_human_input_actions(agent: PersistentAgent) -> list[dict]:
    actions_by_batch: dict[str, dict] = {}
    for request in list_pending_human_input_requests(agent):
        batch_id = str(request.get("batchId") or request["id"])
        action = actions_by_batch.setdefault(
            batch_id,
            {
                "id": f"human_input:{batch_id}",
                "kind": "human_input",
                "requests": [],
                "count": 0,
            },
        )
        action["requests"].append(request)

    for action in actions_by_batch.values():
        action["requests"].sort(key=lambda request: request.get("batchPosition") or 1)
        action["count"] = len(action["requests"])

    return list(actions_by_batch.values())


def _serialize_requested_secret(secret: PersistentAgentSecret) -> dict:
    return {
        "id": str(secret.id),
        "name": secret.name,
        "key": secret.key,
        "secretType": secret.secret_type,
        "domainPattern": secret.domain_pattern,
        "description": secret.description,
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
        "smsContactPurpose": request_obj.sms_contact_purpose,
        "smsContactPurposeDetails": request_obj.sms_contact_purpose_details,
        "smsContactPermissionAttested": request_obj.sms_contact_permission_attested,
        "requestedAt": request_obj.requested_at.isoformat() if request_obj.requested_at else None,
        "expiresAt": request_obj.expires_at.isoformat() if request_obj.expires_at else None,
    }


def serialize_contact_request(request_obj: CommsAllowlistRequest) -> dict:
    return _serialize_contact_request(request_obj)


def _add_pending_counts(counts: dict[str, int], queryset) -> None:
    for row in queryset.values("agent_id").annotate(total=Count("id")):
        counts[str(row["agent_id"])] = counts.get(str(row["agent_id"]), 0) + int(row["total"] or 0)


def count_pending_action_requests_for_agents(agents: list[PersistentAgent], viewer_user) -> dict[str, int]:
    agent_ids = [agent.id for agent in agents]
    counts = {str(agent_id): 0 for agent_id in agent_ids}
    if not agent_ids:
        return counts

    now = timezone.now()
    active_expiry_filter = Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    _add_pending_counts(
        counts,
        PersistentAgentHumanInputRequest.objects.filter(
            agent_id__in=agent_ids,
            status=PersistentAgentHumanInputRequest.Status.PENDING,
        ).filter(active_expiry_filter),
    )

    manageable_agent_ids = [
        agent.id
        for agent in agents
        if viewer_user is not None and user_can_manage_agent_settings(
            viewer_user,
            agent,
            allow_delinquent_personal_chat=True,
        )
    ]
    if not manageable_agent_ids:
        return counts

    _add_pending_counts(
        counts,
        AgentSpawnRequest.objects.filter(
            agent_id__in=manageable_agent_ids,
            status=AgentSpawnRequest.RequestStatus.PENDING,
        ).filter(active_expiry_filter),
    )
    _add_pending_counts(
        counts,
        PersistentAgentSecret.objects.filter(
            agent_id__in=manageable_agent_ids,
            requested=True,
        ),
    )
    _add_pending_counts(
        counts,
        CommsAllowlistRequest.objects.filter(
            agent_id__in=manageable_agent_ids,
            status=CommsAllowlistRequest.RequestStatus.PENDING,
        ).filter(active_expiry_filter),
    )
    _add_pending_counts(
        counts,
        OutboundEmailReview.objects.filter(
            agent_id__in=manageable_agent_ids,
            status=OutboundEmailReview.Status.PENDING,
            expires_at__gt=now,
        ),
    )

    return counts


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


def expire_pending_action_requests(agent: PersistentAgent) -> None:
    expire_pending_human_input_requests(agent)
    _expire_pending_spawn_requests(agent)
    _expire_pending_contact_requests(agent)


def list_pending_action_requests(agent: PersistentAgent, viewer_user) -> list[dict]:
    pending_actions: list[dict] = []

    pending_actions.extend(_build_human_input_actions(agent))

    if viewer_user is None or not user_can_manage_agent_settings(
        viewer_user,
        agent,
        allow_delinquent_personal_chat=True,
    ):
        return pending_actions

    for spawn_request in (
        AgentSpawnRequest.objects.filter(
            agent=agent,
            status=AgentSpawnRequest.RequestStatus.PENDING,
        )
        .order_by("-requested_at")
    ):
        pending_actions.append(_serialize_spawn_request(agent, spawn_request))

    for secret in (
        PersistentAgentSecret.objects.filter(
            agent=agent,
            requested=True,
        ).order_by("secret_type", "domain_pattern", "name")
    ):
        pending_actions.append(
            {
                "id": f"requested_secret:{secret.id}",
                "kind": "requested_secrets",
                "secrets": [_serialize_requested_secret(secret)],
                "count": 1,
                "fulfillApiUrl": reverse("console_agent_requested_secrets_fulfill", kwargs={"agent_id": agent.id}),
                "removeApiUrl": reverse("console_agent_requested_secrets_remove_api", kwargs={"agent_id": agent.id}),
            }
        )

    contact_request_qs = (
        CommsAllowlistRequest.objects.filter(
            agent=agent,
            status=CommsAllowlistRequest.RequestStatus.PENDING,
        ).order_by("-requested_at")
    )
    contact_request_count = contact_request_qs.count()
    if contact_request_count:
        pending_actions.append(
            {
                "id": "contact_requests",
                "kind": "contact_requests",
                "requests": [
                    _serialize_contact_request(request_obj)
                    for request_obj in contact_request_qs[:CONTACT_REQUEST_PENDING_ACTION_PREVIEW_LIMIT]
                ],
                "count": contact_request_count,
                "resolveApiUrl": reverse("console_agent_contact_requests_resolve", kwargs={"agent_id": agent.id}),
            }
        )

    outbox_qs = OutboundEmailReview.objects.filter(
        agent=agent,
        status=OutboundEmailReview.Status.PENDING,
        expires_at__gt=timezone.now(),
    ).select_related("message__to_endpoint", "message__conversation").order_by("-queued_at")
    outbox_count = outbox_qs.count()
    if outbox_count:
        pending_actions.append(
            {
                "id": "outbox_reviews",
                "kind": "outbox_reviews",
                "count": outbox_count,
                "items": [
                    {
                        "id": str(review.id),
                        "subject": str((review.message.raw_payload or {}).get("subject") or ""),
                        "recipient": (
                            review.message.conversation.address
                            if review.message.conversation_id
                            else review.message.to_endpoint.address
                        ),
                        "queuedAt": review.queued_at.isoformat(),
                        "detailApiUrl": reverse("console_outbox_detail", kwargs={"outbox_id": review.id}),
                    }
                    for review in outbox_qs[:10]
                ],
                "outboxUrl": "/app/outbox",
            }
        )

    return pending_actions


def get_legacy_pending_human_input_requests(pending_actions: list[dict]) -> list[dict]:
    requests: list[dict] = []
    for action in pending_actions:
        if action.get("kind") == "human_input":
            action_requests = action.get("requests")
            if isinstance(action_requests, list):
                requests.extend(action_requests)
    return requests
