"""Shared Slack conversation and debounce helpers."""

import logging
import time
from datetime import timedelta
from typing import Mapping

import redis
import requests
from django.conf import settings
from django.utils import timezone

from config.redis_client import get_redis_client
from api.models import (
    CommsChannel,
    DeliveryStatus,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
)

logger = logging.getLogger(__name__)

SLACK_INBOUND_DEBOUNCE_DEADLINE_KEY = "agent:slack-inbound-debounce:{agent_id}:deadline"
SLACK_INBOUND_DEBOUNCE_SCHEDULED_KEY = "agent:slack-inbound-debounce:{agent_id}:scheduled"


def slack_channel_address(team_id: str, channel_id: str) -> str:
    team_part = team_id or "unknown"
    return f"slack://team/{team_part}/channel/{channel_id}"


def slack_conversation_address(agent_id: object, team_id: str, channel_id: str) -> str:
    team_part = team_id or "unknown"
    return f"slack://agent/{agent_id}/team/{team_part}/channel/{channel_id}"


def slack_agent_address(agent_id: object) -> str:
    return f"slack://agent/{agent_id}"


def ensure_slack_agent_endpoint(agent: PersistentAgent) -> PersistentAgentCommsEndpoint:
    endpoint, _created = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.SLACK,
        address=slack_agent_address(agent.id),
        defaults={"owner_agent": agent, "is_primary": True},
    )
    updates = []
    if endpoint.owner_agent_id != agent.id:
        endpoint.owner_agent = agent
        updates.append("owner_agent")
    if not endpoint.is_primary:
        endpoint.is_primary = True
        updates.append("is_primary")
    if updates:
        endpoint.save(update_fields=updates)
    return endpoint


def ensure_conversation_participant(
    conversation: PersistentAgentConversation,
    endpoint: PersistentAgentCommsEndpoint,
    role: str,
) -> None:
    PersistentAgentConversationParticipant.objects.get_or_create(
        conversation=conversation,
        endpoint=endpoint,
        defaults={"role": role},
    )


def display_name_for_channel(channel_id: str, channel_name: str = "") -> str:
    return f"#{channel_name.lstrip('#')}" if channel_name else f"Slack {channel_id}"


def slack_channel_source_label(channel_id: str, channel_name: str = "") -> str:
    return display_name_for_channel(channel_id, channel_name)


def get_or_create_slack_conversation(
    agent: PersistentAgent,
    *,
    address: str,
    channel_id: str,
    channel_name: str = "",
) -> PersistentAgentConversation:
    display_name = display_name_for_channel(channel_id, channel_name)
    conversation, created = PersistentAgentConversation.objects.get_or_create(
        channel=CommsChannel.SLACK,
        address=address,
        defaults={"owner_agent": agent, "display_name": display_name},
    )
    updates = []
    if conversation.owner_agent_id is None:
        conversation.owner_agent = agent
        updates.append("owner_agent")
    if display_name and conversation.display_name != display_name:
        conversation.display_name = display_name
        updates.append("display_name")
    if updates and not created:
        conversation.save(update_fields=updates)
    return conversation


def slack_channel_endpoint(address: str) -> PersistentAgentCommsEndpoint:
    endpoint, _created = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.SLACK,
        address=address,
        defaults={"owner_agent": None},
    )
    return endpoint


def ensure_slack_conversation_participants(
    agent: PersistentAgent,
    conversation: PersistentAgentConversation,
    *,
    platform_channel_address: str,
) -> tuple[PersistentAgentCommsEndpoint, PersistentAgentCommsEndpoint]:
    from_endpoint = ensure_slack_agent_endpoint(agent)
    channel_endpoint = slack_channel_endpoint(platform_channel_address)
    ensure_conversation_participant(
        conversation,
        from_endpoint,
        PersistentAgentConversationParticipant.ParticipantRole.AGENT,
    )
    ensure_conversation_participant(
        conversation,
        channel_endpoint,
        PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
    )
    return from_endpoint, channel_endpoint


def create_slack_outbound_message(
    agent: PersistentAgent,
    *,
    channel_id: str,
    body: str,
    conversation_address: str,
    platform_channel_address: str = "",
    channel_name: str = "",
    raw_payload: Mapping[str, object] | None = None,
) -> PersistentAgentMessage:
    conversation = get_or_create_slack_conversation(
        agent,
        address=conversation_address,
        channel_id=channel_id,
        channel_name=channel_name,
    )
    from_endpoint, channel_endpoint = ensure_slack_conversation_participants(
        agent,
        conversation,
        platform_channel_address=platform_channel_address or slack_channel_address("", channel_id),
    )
    now = timezone.now()
    payload = dict(raw_payload or {})
    payload.setdefault("source_kind", "slack")
    payload.setdefault("slack_channel_id", channel_id)
    payload.setdefault("slack_channel_name", channel_name)
    payload.setdefault("slack_platform_channel_address", channel_endpoint.address)
    payload.setdefault("slack_conversation_address", conversation.address)
    payload.setdefault("source_label", slack_channel_source_label(channel_id, channel_name))
    return PersistentAgentMessage.objects.create(
        owner_agent=agent,
        from_endpoint=from_endpoint,
        conversation=conversation,
        is_outbound=True,
        body=body,
        raw_payload=payload,
        latest_status=DeliveryStatus.SENT,
        latest_sent_at=now,
    )


def slack_inbound_debounce_seconds() -> int:
    return max(0, int(settings.SLACK_INBOUND_DEBOUNCE_SECONDS))


def slack_inbound_debounce_keys(agent_id: str) -> tuple[str, str]:
    return (
        SLACK_INBOUND_DEBOUNCE_DEADLINE_KEY.format(agent_id=agent_id),
        SLACK_INBOUND_DEBOUNCE_SCHEDULED_KEY.format(agent_id=agent_id),
    )


def slack_inbound_debounce_ttl(delay_seconds: int) -> int:
    return max(60, delay_seconds * 6)


def process_agent_events_after_slack_debounce(agent_id: str, *, countdown: int = 0) -> None:
    from api.agent.tasks import process_agent_events_task

    if countdown > 0:
        process_agent_events_task.apply_async(args=[agent_id], countdown=countdown)
    else:
        process_agent_events_task.delay(agent_id)


def schedule_slack_inbound_processing(agent_id: str) -> dict[str, object]:
    debounce_seconds = slack_inbound_debounce_seconds()
    if debounce_seconds <= 0:
        process_agent_events_after_slack_debounce(str(agent_id))
        return {"debounced": False, "debounce_seconds": 0, "scheduled": True}

    normalized_agent_id = str(agent_id)
    deadline_key, scheduled_key = slack_inbound_debounce_keys(normalized_agent_id)
    deadline = time.time() + debounce_seconds
    ttl = slack_inbound_debounce_ttl(debounce_seconds)

    try:
        redis_client = get_redis_client()
        pipeline = redis_client.pipeline(transaction=True)
        pipeline.set(deadline_key, f"{deadline:.6f}", ex=ttl)
        pipeline.set(scheduled_key, "1", ex=ttl, nx=True)
        results = pipeline.execute()
        scheduled = bool(results[1])
    except redis.exceptions.RedisError:
        logger.exception(
            "Failed scheduling Slack inbound debounce for agent %s; falling back to delayed processing.",
            normalized_agent_id,
        )
        process_agent_events_after_slack_debounce(normalized_agent_id, countdown=debounce_seconds)
        return {"debounced": True, "debounce_seconds": debounce_seconds, "scheduled": True}

    if not scheduled:
        return {"debounced": True, "debounce_seconds": debounce_seconds, "scheduled": False}

    def _schedule() -> None:
        from api.agent.tasks.process_events import process_slack_inbound_debounce_task

        process_slack_inbound_debounce_task.apply_async(
            args=[normalized_agent_id],
            countdown=debounce_seconds,
        )

    _schedule()
    return {"debounced": True, "debounce_seconds": debounce_seconds, "scheduled": True}


def process_slack_inbound_debounce(agent_id: str) -> None:
    debounce_seconds = slack_inbound_debounce_seconds()
    normalized_agent_id = str(agent_id)
    if debounce_seconds <= 0:
        process_agent_events_after_slack_debounce(normalized_agent_id)
        return

    deadline_key, scheduled_key = slack_inbound_debounce_keys(normalized_agent_id)
    try:
        redis_client = get_redis_client()
        deadline_raw = redis_client.get(deadline_key)
        if isinstance(deadline_raw, bytes):
            deadline_raw = deadline_raw.decode("utf-8")
        deadline = float(deadline_raw or "0")
        countdown = max(0, int(round(deadline - time.time())))
        if countdown <= 0:
            redis_client.delete(deadline_key, scheduled_key)
            process_agent_events_after_slack_debounce(normalized_agent_id)
            return
        redis_client.expire(scheduled_key, slack_inbound_debounce_ttl(max(debounce_seconds, countdown)))
        from api.agent.tasks.process_events import process_slack_inbound_debounce_task

        process_slack_inbound_debounce_task.apply_async(args=[normalized_agent_id], countdown=countdown)
    except (redis.exceptions.RedisError, ValueError):
        logger.exception("Failed processing Slack inbound debounce for agent %s.", normalized_agent_id)
        process_agent_events_after_slack_debounce(normalized_agent_id)
