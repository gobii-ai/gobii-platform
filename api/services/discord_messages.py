"""Shared Discord conversation and debounce helpers."""

import logging
import math
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

DISCORD_INBOUND_DEBOUNCE_DEADLINE_KEY = "agent:discord-inbound-debounce:{agent_id}:deadline"
DISCORD_INBOUND_DEBOUNCE_SCHEDULED_KEY = "agent:discord-inbound-debounce:{agent_id}:scheduled"
DISCORD_INBOUND_TYPING_CHANNEL_KEY = "agent:discord-inbound-debounce:{agent_id}:typing-channel"
DISCORD_API_BASE = "https://discord.com/api/v10"


def discord_channel_address(guild_id: str, channel_id: str) -> str:
    guild_part = guild_id or "unknown"
    return f"discord://guild/{guild_part}/channel/{channel_id}"


def discord_conversation_address(agent_id: object, guild_id: str, channel_id: str) -> str:
    guild_part = guild_id or "unknown"
    return f"discord://agent/{agent_id}/guild/{guild_part}/channel/{channel_id}"


def discord_agent_address(agent_id: object) -> str:
    return f"discord://agent/{agent_id}"


def ensure_discord_agent_endpoint(agent: PersistentAgent) -> PersistentAgentCommsEndpoint:
    endpoint, _created = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.DISCORD,
        address=discord_agent_address(agent.id),
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
    return f"#{channel_name.lstrip('#')}" if channel_name else f"Discord {channel_id}"


def discord_channel_source_label(channel_id: str, channel_name: str = "") -> str:
    return display_name_for_channel(channel_id, channel_name)


def get_or_create_discord_conversation(
    agent: PersistentAgent,
    *,
    address: str,
    channel_id: str,
    channel_name: str = "",
) -> PersistentAgentConversation:
    display_name = display_name_for_channel(channel_id, channel_name)
    conversation, created = PersistentAgentConversation.objects.get_or_create(
        channel=CommsChannel.DISCORD,
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


def discord_channel_endpoint(address: str) -> PersistentAgentCommsEndpoint:
    endpoint, _created = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.DISCORD,
        address=address,
        defaults={"owner_agent": None},
    )
    return endpoint


def ensure_discord_conversation_participants(
    agent: PersistentAgent,
    conversation: PersistentAgentConversation,
    *,
    platform_channel_address: str,
) -> tuple[PersistentAgentCommsEndpoint, PersistentAgentCommsEndpoint]:
    from_endpoint = ensure_discord_agent_endpoint(agent)
    channel_endpoint = discord_channel_endpoint(platform_channel_address)
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


def create_discord_outbound_message(
    agent: PersistentAgent,
    *,
    channel_id: str,
    body: str,
    conversation_address: str,
    platform_channel_address: str = "",
    channel_name: str = "",
    raw_payload: Mapping[str, object] | None = None,
) -> PersistentAgentMessage:
    conversation = get_or_create_discord_conversation(
        agent,
        address=conversation_address,
        channel_id=channel_id,
        channel_name=channel_name,
    )
    from_endpoint, channel_endpoint = ensure_discord_conversation_participants(
        agent,
        conversation,
        platform_channel_address=platform_channel_address or discord_channel_address("", channel_id),
    )
    now = timezone.now()
    payload = dict(raw_payload or {})
    payload.setdefault("source_kind", "discord")
    payload.setdefault("discord_channel_id", channel_id)
    payload.setdefault("discord_channel_name", channel_name)
    payload.setdefault("discord_platform_channel_address", channel_endpoint.address)
    payload.setdefault("discord_conversation_address", conversation.address)
    payload.setdefault("source_label", discord_channel_source_label(channel_id, channel_name))
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


def find_recent_discord_outbound(
    agent: PersistentAgent,
    *,
    channel_id: str,
    body: str,
    source: str = "",
) -> PersistentAgentMessage | None:
    filters = {
        "owner_agent": agent,
        "is_outbound": True,
        "conversation__channel": CommsChannel.DISCORD,
        "body": body,
        "timestamp__gte": timezone.now() - timedelta(minutes=10),
        "raw_payload__discord_channel_id": channel_id,
    }
    if source:
        filters["raw_payload__source"] = source
    return (
        PersistentAgentMessage.objects
        .select_related("conversation")
        .filter(**filters)
        .order_by("-timestamp")
        .first()
    )


def discord_inbound_debounce_seconds() -> int:
    return max(0, int(settings.DISCORD_INBOUND_DEBOUNCE_SECONDS))


def discord_inbound_debounce_keys(agent_id: str) -> tuple[str, str]:
    return (
        DISCORD_INBOUND_DEBOUNCE_DEADLINE_KEY.format(agent_id=agent_id),
        DISCORD_INBOUND_DEBOUNCE_SCHEDULED_KEY.format(agent_id=agent_id),
    )


def discord_inbound_typing_channel_key(agent_id: str) -> str:
    return DISCORD_INBOUND_TYPING_CHANNEL_KEY.format(agent_id=agent_id)


def discord_inbound_debounce_ttl(delay_seconds: int) -> int:
    return max(60, delay_seconds * 6)


def send_discord_typing_indicator(channel_id: str) -> bool:
    normalized_channel_id = str(channel_id or "").strip()
    if not normalized_channel_id or not settings.DISCORD_BOT_TOKEN:
        return False
    try:
        response = requests.post(
            f"{DISCORD_API_BASE}/channels/{normalized_channel_id}/typing",
            headers={"Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}"},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to send Discord typing indicator for channel %s: %s", normalized_channel_id, exc)
        return False
    return True


def process_agent_events_after_discord_debounce(agent_id: str, *, countdown: int = 0) -> None:
    from api.agent.tasks import process_agent_events_task

    if countdown > 0:
        process_agent_events_task.apply_async(args=[agent_id], countdown=countdown)
    else:
        process_agent_events_task.delay(agent_id)


def schedule_discord_inbound_processing(agent_id: str, *, typing_channel_id: str = "") -> dict[str, object]:
    debounce_seconds = discord_inbound_debounce_seconds()
    normalized_typing_channel_id = str(typing_channel_id or "").strip()
    if normalized_typing_channel_id:
        send_discord_typing_indicator(normalized_typing_channel_id)
    if debounce_seconds <= 0:
        process_agent_events_after_discord_debounce(str(agent_id))
        return {"debounced": False, "debounce_seconds": 0, "scheduled": True}

    normalized_agent_id = str(agent_id)
    deadline_key, scheduled_key = discord_inbound_debounce_keys(normalized_agent_id)
    typing_channel_key = discord_inbound_typing_channel_key(normalized_agent_id)
    deadline = time.time() + debounce_seconds
    ttl = discord_inbound_debounce_ttl(debounce_seconds)

    try:
        redis_client = get_redis_client()
        pipeline = redis_client.pipeline(transaction=True)
        pipeline.set(deadline_key, f"{deadline:.6f}", ex=ttl)
        pipeline.set(scheduled_key, "1", ex=ttl, nx=True)
        if normalized_typing_channel_id:
            pipeline.set(typing_channel_key, normalized_typing_channel_id, ex=ttl)
        results = pipeline.execute()
        scheduled_result = results[1]
        scheduled = bool(scheduled_result)
    except redis.exceptions.RedisError:
        logger.exception(
            "Failed scheduling Discord inbound debounce for agent %s; falling back to delayed processing.",
            normalized_agent_id,
        )
        process_agent_events_after_discord_debounce(normalized_agent_id, countdown=debounce_seconds)
        return {
            "debounced": False,
            "debounce_seconds": debounce_seconds,
            "scheduled": True,
            "fallback": True,
        }

    if scheduled:
        if settings.CELERY_TASK_ALWAYS_EAGER:
            redis_client.delete(deadline_key, scheduled_key, typing_channel_key)
            process_agent_events_after_discord_debounce(normalized_agent_id)
            return {
                "debounced": False,
                "debounce_seconds": debounce_seconds,
                "scheduled": True,
                "eager": True,
            }

        from api.agent.tasks.process_events import process_discord_inbound_debounce_task
        process_discord_inbound_debounce_task.apply_async(
            args=[normalized_agent_id],
            countdown=debounce_seconds,
        )

    return {
        "debounced": True,
        "debounce_seconds": debounce_seconds,
        "scheduled": scheduled,
    }


def coerce_redis_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def process_discord_inbound_debounce(agent_id: str) -> None:
    debounce_seconds = discord_inbound_debounce_seconds()
    normalized_agent_id = str(agent_id)
    if debounce_seconds <= 0:
        process_agent_events_after_discord_debounce(normalized_agent_id)
        return

    deadline_key, scheduled_key = discord_inbound_debounce_keys(normalized_agent_id)
    typing_channel_key = discord_inbound_typing_channel_key(normalized_agent_id)
    now = time.time()

    try:
        redis_client = get_redis_client()
        typing_channel_id = redis_client.get(typing_channel_key)
        if isinstance(typing_channel_id, (bytes, bytearray)):
            typing_channel_id = typing_channel_id.decode("utf-8", "ignore")
        typing_channel_id = str(typing_channel_id or "").strip()
        if typing_channel_id:
            send_discord_typing_indicator(typing_channel_id)
        deadline = coerce_redis_float(redis_client.get(deadline_key))
        if deadline is not None and deadline > now:
            if settings.CELERY_TASK_ALWAYS_EAGER:
                redis_client.delete(deadline_key, scheduled_key, typing_channel_key)
                process_agent_events_after_discord_debounce(normalized_agent_id)
                return

            countdown = max(1, math.ceil(deadline - now))
            ttl = discord_inbound_debounce_ttl(max(debounce_seconds, countdown))
            redis_client.expire(deadline_key, ttl)
            redis_client.expire(scheduled_key, ttl)
            redis_client.expire(typing_channel_key, ttl)
            from api.agent.tasks.process_events import process_discord_inbound_debounce_task

            process_discord_inbound_debounce_task.apply_async(
                args=[normalized_agent_id],
                countdown=countdown,
            )
            return

        redis_client.delete(deadline_key, scheduled_key, typing_channel_key)
    except redis.exceptions.RedisError:
        logger.exception(
            "Failed processing Discord inbound debounce for agent %s; falling back to immediate processing.",
            normalized_agent_id,
        )

    process_agent_events_after_discord_debounce(normalized_agent_id)
